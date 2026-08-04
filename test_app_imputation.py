"""
API Endpoint Integration Tests for /api/insulin/history?include_imputed=true
"""

import unittest
from fastapi.testclient import TestClient
from app import app


class TestAppImputationEndpoint(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)

    def test_api_insulin_history_default(self):
        """Tests /api/insulin/history endpoint returns doses without error."""
        response = self.client.get("/api/insulin/history?hours=24")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsInstance(data, list)

    def test_api_insulin_history_include_imputed(self):
        """Tests /api/insulin/history?include_imputed=true endpoint runs imputation model cleanly."""
        response = self.client.get("/api/insulin/history?hours=24&include_imputed=true")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsInstance(data, list)


if __name__ == "__main__":
    unittest.main()
