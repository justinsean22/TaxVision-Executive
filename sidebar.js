const API_URL = "https://asktaxwiki-810362790827.us-south1.run.app";

// UI References
const analyzeBtn = document.getElementById("analyze");
const queryInput = document.getElementById("query");
const responseDiv = document.getElementById("response");
const factDiv = document.getElementById("fact");
const statusDiv = document.getElementById("status") || { innerText: '', style: {} };
const scrubStatus = document.getElementById("scrub-status");
const citationToggle = document.getElementById("toggle-citations");
const citationsContainer = document.getElementById("citations-container");

// ----------------------------------------
// LOCAL PII SCRUBBER (Client-Side Shield)
// ----------------------------------------
function scrubPII(text) {
  let scrubbed = text;
  scrubbed = scrubbed.replace(/\b\d{3}-\d{2}-\d{4}\b/g, "XXX-XX-XXXX"); // SSN
  scrubbed = scrubbed.replace(/\b\d{2}-\d{7}\b/g, "XX-XXXXXXX");      // EIN
  scrubbed = scrubbed.replace(/\b\d{9}\b/g, "XXXXXXXXX");             // Routing
  return scrubbed;
}

// --- Lightweight Intent Detection ---
function detectMode(text) {
  const numericPattern = /\d{2,}/g;
  const keywords = ["wages", "overtime", "gross", "net", "withholding", "federal tax", "hours", "rate", "deduction"];
  const hasNumbers = numericPattern.test(text);
  const hasKeyword = keywords.some(k => text.toLowerCase().includes(k));
  return (hasNumbers && hasKeyword) ? "calculation" : "research";
}

// --- Citation Rendering ---
function renderCitations(citations) {
  if (!citations || citations.length === 0) {
    if (citationToggle) citationToggle.style.display = "none";
    if (citationsContainer) citationsContainer.style.display = "none";
    return;
  }

  if (citationToggle) {
    citationToggle.style.display = "block";
    citationToggle.innerText = "View Citations";
  }
  
  if (citationsContainer) {
    citationsContainer.style.display = "none"; // Reset to closed on new search
    citationsContainer.innerHTML = citations.map(c => `
      <div class="citation-item">
        <a href="${c.url}" target="_blank">&#128279; ${c.title}</a>
      </div>
    `).join("");
  }
}

// --- Event Listeners ---

// 1. Citation Toggle Logic
if (citationToggle) {
  citationToggle.addEventListener("click", () => {
    const visible = citationsContainer.style.display === "block";
    citationsContainer.style.display = visible ? "none" : "block";
    citationToggle.innerText = visible ? "View Citations" : "Hide Citations";
  });
}

// 2. Drag and Drop Logic
queryInput.addEventListener("dragover", (e) => { e.preventDefault(); queryInput.style.border = "2px dashed #1A1A1B"; });
queryInput.addEventListener("dragleave", (e) => { e.preventDefault(); queryInput.style.border = "1px solid #E5E5E5"; });
queryInput.addEventListener("drop", async (e) => {
  e.preventDefault();
  queryInput.style.border = "1px solid #E5E5E5";
  const file = e.dataTransfer.files[0];
  if (file && (file.type === "text/plain" || file.name.endsWith('.csv'))) {
    queryInput.value = await file.text();
    analyzeBtn.click();
  }
});

// 3. Highlight Storage Listeners
chrome.storage.local.get(["selectedText"], (res) => { if (res.selectedText) queryInput.value = res.selectedText; });
chrome.storage.onChanged.addListener((changes, ns) => {
  if (ns === "local" && changes.selectedText) {
    queryInput.value = changes.selectedText.newValue;
    responseDiv.classList.remove("visible");
    analyzeBtn.click();
  }
});

let isAnalyzing = false;

// 4. THE EXECUTIVE ANALYSIS LOGIC
analyzeBtn.addEventListener("click", async () => {
  if (isAnalyzing) return;
  isAnalyzing = true;

  try {
    const query = queryInput.value.trim();
    if (!query) return;

    const sanitized = scrubPII(query);
    const mode = detectMode(query);
    const wasScrubbed = sanitized !== query;

    // UI Reset
    if (scrubStatus) scrubStatus.innerText = wasScrubbed ? "Sensitive identifiers masked locally." : "";
    statusDiv.innerText = "Analyzing IRS Intelligence...";
    statusDiv.style.color = "#1A1A1B";

    responseDiv.classList.remove("visible");

    if (citationToggle) citationToggle.style.display = "none";
    if (citationsContainer) citationsContainer.style.display = "none";

    const res = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: sanitized, mode: mode })
    });

    if (!res.ok) throw new Error("Network Error");
    const data = await res.json();

    // Render logic based on mode
    if (data.mode === "calculation" && data.deduction !== undefined) {
      responseDiv.innerHTML = DOMPurify.sanitize(`
        <div class="highlight">
          <strong>OBBBA Calculation:</strong><br>
          Estimated Deduction: $${data.deduction.toFixed(2)}
        </div>`);
    } else {
      const markdown = data.answer || "No guidance found.";
      const rawHTML = marked.parse(markdown);
      responseDiv.innerHTML = DOMPurify.sanitize(rawHTML);
    }

    responseDiv.classList.add("visible");

    // Handle Citations
    renderCitations(data.citations);

    statusDiv.innerText = "Research Complete";
    statusDiv.style.color = "#2ECC71";

  } catch (error) {
    statusDiv.innerText = "Connection Error";
    statusDiv.style.color = "#E74C3C";
    responseDiv.innerText = "Unable to reach TaxVision Research Engine.";
    responseDiv.classList.add("visible");
  } finally {
    isAnalyzing = false;
  }
});

// 5. Load Tax Fact
(async function loadFact() {
  try {
    const res = await fetch(API_URL + "/get_tax_fact");
    const data = await res.json();
    factDiv.innerText = "2026 UPDATE: " + (data.fact || "Library active.");
  } catch (e) { factDiv.innerText = "Research Library Active."; }
})();
