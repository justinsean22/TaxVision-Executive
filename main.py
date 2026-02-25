import os
import time
import logging
import random
import re
import json
import functions_framework
from flask import jsonify
from google.cloud import discoveryengine_v1 as discoveryengine
import vertexai
from vertexai.generative_models import GenerativeModel

# ---------------------------------------
# CONFIG
# ---------------------------------------
logging.basicConfig(level=logging.INFO)

PROJECT_ID = os.environ.get("PROJECT_ID", "taxvision-intelligence")
ENGINE_ID = os.environ.get("ENGINE_ID", "taxvision-wiki-bot_1770634838800")

SERVING_CONFIG = (
    f"projects/{PROJECT_ID}/locations/global/"
    f"collections/default_collection/"
    f"engines/{ENGINE_ID}/servingConfigs/default_search"
)

FACT_CACHE_TTL = 300
_cached_fact = None
_cached_fact_timestamp = 0

vertexai.init(project=PROJECT_ID, location="us-central1")
gemini_model = GenerativeModel("gemini-1.5-flash")

# --- ADDED: Missing helper function ---
def normalize_utf8(text):
    if not text: return ""
    return str(text).encode('utf-8', errors='ignore').decode('utf-8')

# ---------------------------------------
# UTILITIES
# ---------------------------------------
def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }

def is_refusal(text: str) -> bool:
    refusal_keywords = ["not qualified", "tax advice", "cannot provide", "consult a professional"]
    return any(keyword in text.lower() for keyword in refusal_keywords)

def create_client():
    return discoveryengine.SearchServiceClient()

def safe_summary(response):
    if response.summary and getattr(response.summary, "summary_text", None):
        return response.summary.summary_text.strip()
    return ""

def safe_extract_snippets(response, max_docs=3):
    sections = []
    for result in response.results[:max_docs]:
        try:
            derived = getattr(result.document, "derived_struct_data", None)
            if derived and "snippets" in derived:
                snippet = derived["snippets"][0].get("snippet", "").strip()
                if snippet:
                    sections.append(snippet)
        except Exception:
            continue
    return sections

# ---------------------------------------
# OBBBA CALCULATION ENGINE
# ---------------------------------------
def calculate_obbba(reg_rate: float, ot_rate: float, ot_hours: float):
    if reg_rate <= 0 or ot_rate <= 0 or ot_hours <= 0:
        return 0.0
    deduction = (ot_rate - reg_rate) * ot_hours
    return max(deduction, 0.0)

def extract_stub_values_regex(text: str):
    reg = re.search(r"reg(?:ular)?\s*rate[:\s$]*([\d\.]+)", text, re.IGNORECASE)
    ot = re.search(r"ot\s*rate[:\s$]*([\d\.]+)", text, re.IGNORECASE)
    hours = re.search(r"ot\s*hours[:\s]*([\d\.]+)", text, re.IGNORECASE)

    return {
        "reg_rate": float(reg.group(1)) if reg else 0.0,
        "ot_rate": float(ot.group(1)) if ot else 0.0,
        "ot_hours": float(hours.group(1)) if hours else 0.0,
    }

def extract_stub_values_ai(text: str):
    try:
        prompt = f"Extract pay stub values. Return ONLY valid JSON. Schema: {{'reg_rate': number, 'ot_rate': number, 'ot_hours': number}} Text: {text}"
        response = gemini_model.generate_content(prompt)
        raw = (response.text or "").strip()
        clean = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean)
        return {
            "reg_rate": float(data.get("reg_rate", 0.0)),
            "ot_rate": float(data.get("ot_rate", 0.0)),
            "ot_hours": float(data.get("ot_hours", 0.0)),
        }
    except Exception as e:
        logging.error(f"AI extraction failed: {e}")
        return {"reg_rate": 0.0, "ot_rate": 0.0, "ot_hours": 0.0}

@functions_framework.http
def api_router(request):
    if request.method == "OPTIONS":
        return ("", 204, cors_headers())
    headers = cors_headers()
    path = request.path
    try:
        if path.endswith("/get_tax_fact"):
            return handle_tax_fact(request, headers)
        return handle_tax_bot(request, headers)
    except Exception:
        logging.exception("Critical failure in router.")
        return jsonify({"error": "System offline."}), 500, headers

def handle_tax_bot(request, headers):
    if request.method != "POST":
        return jsonify({"error": "POST required"}), 405, headers

    request_json = request.get_json(silent=True) or {}
    user_query = request_json.get("question", "").strip()
    mode = request_json.get("mode", "research")

    if not user_query:
        return jsonify({"answer": "System online."}), 200, headers

    if mode == "calculation":
        extracted = extract_stub_values_regex(user_query)
        if extracted["reg_rate"] <= 0 or extracted["ot_rate"] <= 0 or extracted["ot_hours"] <= 0:
            extracted = extract_stub_values_ai(user_query)
        
        if extracted["reg_rate"] <= 0 or extracted["ot_rate"] <= 0 or extracted["ot_hours"] <= 0:
            return jsonify({"error": "Data extraction failed."}), 200, headers

        deduction = calculate_obbba(extracted["reg_rate"], extracted["ot_rate"], extracted["ot_hours"])
        return jsonify({"mode": "calculation", "deduction": deduction}), 200, headers

    # --- RESEARCH MODE ---
    client = create_client()
    content_search_spec = discoveryengine.SearchRequest.ContentSearchSpec(
        summary_spec=discoveryengine.SearchRequest.ContentSearchSpec.SummarySpec(summary_result_count=5)
    )
    search_request = discoveryengine.SearchRequest(
        serving_config=SERVING_CONFIG,
        query=user_query,
        content_search_spec=content_search_spec,
    )
    response = client.search(search_request)
    summary = safe_summary(response)

    if summary and not is_refusal(summary):
        citations = []
        for result in response.results[:3]:
            try:
                doc = result.document
                raw_title = getattr(doc, "title", "IRS Source")
                # FIXED: Indentation and calling the now-defined helper
                safe_title = normalize_utf8(raw_title)
                citations.append({
                    "title": safe_title,
                    "url": getattr(doc, "uri", "")
                })
            except Exception:
                continue
        return jsonify({"answer": summary, "citations": citations}), 200, headers

    snippets = safe_extract_snippets(response)
    return jsonify({"answer": "\n".join(snippets) if snippets else "No guidance found.", "citations": []}), 200, headers

def handle_tax_fact(request, headers):
    global _cached_fact, _cached_fact_timestamp
    now = time.time()
    if _cached_fact and (now - _cached_fact_timestamp) < FACT_CACHE_TTL:
        return jsonify({"fact": _cached_fact}), 200, headers

    try:
        client = create_client()
        topics = ["Standard Deduction", "Earned Income Credit", "401k Limits"]
        chosen_topic = random.choice(topics)
        search_request = discoveryengine.SearchRequest(
            serving_config=SERVING_CONFIG,
            query=f"2026 tax fact for {chosen_topic}",
            content_search_spec=discoveryengine.SearchRequest.ContentSearchSpec(
                summary_spec=discoveryengine.SearchRequest.ContentSearchSpec.SummarySpec(summary_result_count=1)
            )
        )
        response = client.search(search_request)
        fact = safe_summary(response) or "IRS Library Online."
        _cached_fact, _cached_fact_timestamp = fact[:100], now
        return jsonify({"fact": _cached_fact}), 200, headers
    except:
        return jsonify({"fact": "IRS Source Library Online."}), 200, headers
