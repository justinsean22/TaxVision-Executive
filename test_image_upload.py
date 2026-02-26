import sys
from unittest.mock import MagicMock, patch

# Mock modules before importing main
sys.modules["google.cloud"] = MagicMock()
sys.modules["google.cloud.vision"] = MagicMock()
sys.modules["google.cloud.discoveryengine_v1"] = MagicMock()
sys.modules["vertexai"] = MagicMock()
sys.modules["vertexai.generative_models"] = MagicMock()
sys.modules["functions_framework"] = MagicMock()

# Setup http decorator mock
def http_mock(func):
    return func
sys.modules["functions_framework"].http = http_mock

import unittest
from flask import Flask, jsonify
import main

class TestTaxBot(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.context = self.app.test_request_context()
        self.context.push()

    def tearDown(self):
        self.context.pop()

    def test_image_upload_success(self):
        # Mock Vision API response
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_text_annotation = MagicMock()
        mock_text_annotation.description = "Regular Rate: $20.00\nOT Rate: $30.00\nOT Hours: 10"
        mock_response.text_annotations = [mock_text_annotation]
        mock_response.error.message = None
        mock_client.text_detection.return_value = mock_response

        with patch('main.vision.ImageAnnotatorClient', return_value=mock_client):
            mock_file = MagicMock()
            mock_file.mimetype = "image/png"
            mock_file.read.return_value = b"fake image content"

            with patch('flask.request') as mock_request:
                mock_request.method = "POST"
                mock_request.content_type = "multipart/form-data"
                mock_request.files = {"file": mock_file}
                mock_request.get_json = MagicMock(return_value={})

                response, status, headers = main.handle_tax_bot(mock_request, {})

                self.assertEqual(status, 200)
                data = response.get_json()
                self.assertEqual(data.get('mode'), 'calculation')
                self.assertEqual(data.get('deduction'), 100.0)

    def test_image_upload_too_large(self):
        with patch('flask.request') as mock_request:
            mock_request.method = "POST"
            mock_request.content_type = "multipart/form-data"

            mock_file = MagicMock()
            mock_file.mimetype = "image/png"
            mock_file.read.return_value = b"a" * (5 * 1024 * 1024 + 1)
            mock_request.files = {"file": mock_file}

            response, status, headers = main.handle_tax_bot(mock_request, {})
            self.assertEqual(status, 400)
            self.assertIn("exceeds 5MB", response.get_json()['error'])

    def test_not_image(self):
         with patch('flask.request') as mock_request:
            mock_request.method = "POST"
            mock_request.content_type = "multipart/form-data"

            mock_file = MagicMock()
            mock_file.mimetype = "application/pdf"
            mock_request.files = {"file": mock_file}

            response, status, headers = main.handle_tax_bot(mock_request, {})
            self.assertEqual(status, 400)
            self.assertIn("Only image files", response.get_json()['error'])

    def test_existing_json_flow(self):
        # Ensure normal JSON requests still work
        with patch('flask.request') as mock_request:
            mock_request.method = "POST"
            mock_request.content_type = "application/json"
            mock_request.files = None
            # Explicitly mock get_json to avoid it being an AsyncMock (which seems to happen by default in some envs?)
            mock_request.get_json = MagicMock(return_value={
                "question": "Regular Rate: $20.00 OT Rate: $30.00 OT Hours: 10",
                "mode": "calculation"
            })

            response, status, headers = main.handle_tax_bot(mock_request, {})
            self.assertEqual(status, 200)
            data = response.get_json()
            self.assertEqual(data.get('mode'), 'calculation')
            self.assertEqual(data.get('deduction'), 100.0)

if __name__ == '__main__':
    unittest.main()
