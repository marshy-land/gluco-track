"""
Challenger 2 Empirical Integration & Stress Test Suite
Testing /api/insulin/history query parameters, schema validation, DB migration idempotency, and high concurrency resilience.
"""

import unittest
import concurrent.futures
from datetime import datetime
from fastapi.testclient import TestClient
from app import app
from db import init_db, get_connection


class TestChallengerAPIIntegration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """Ensure DB migration is executed prior to test suite execution."""
        try:
            init_db()
        except Exception as e:
            print(f"init_db setup warning: {e}")

    def setUp(self):
        self.client = TestClient(app)

    def test_include_imputed_omitted(self):
        """Verify /api/insulin/history when include_imputed is omitted defaults to False."""
        res = self.client.get("/api/insulin/history")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIsInstance(data, list)
        for item in data:
            self.assertIn("is_imputed", item)
            self.assertIsInstance(item["is_imputed"], bool)
            self.assertFalse(item["is_imputed"])

    def test_include_imputed_false_variants(self):
        """Verify include_imputed=false, False, 0, no, off query parameters."""
        for val in ["false", "False", "0", "no", "off"]:
            res = self.client.get(f"/api/insulin/history?include_imputed={val}")
            self.assertEqual(res.status_code, 200, f"Failed for include_imputed={val}")
            data = res.json()
            self.assertIsInstance(data, list)
            for item in data:
                self.assertFalse(item.get("is_imputed", False))

    def test_include_imputed_true_variants(self):
        """Verify include_imputed=true, True, 1, yes, on query parameters."""
        for val in ["true", "True", "1", "yes", "on"]:
            res = self.client.get(f"/api/insulin/history?include_imputed={val}")
            self.assertEqual(res.status_code, 200, f"Failed for include_imputed={val}")
            data = res.json()
            self.assertIsInstance(data, list)

    def test_include_imputed_invalid_boolean(self):
        """Verify invalid boolean strings return 422 Unprocessable Entity cleanly."""
        invalid_vals = ["invalid", "123", "foo", "maybe", ""]
        for val in invalid_vals:
            res = self.client.get(f"/api/insulin/history?include_imputed={val}")
            self.assertEqual(res.status_code, 422, f"Expected 422 for include_imputed={val}, got {res.status_code}")
            data = res.json()
            self.assertIn("detail", data)

    def test_hours_validation(self):
        """Verify bounds and type validation on hours query parameter."""
        # Valid hours
        for h in [1, 24, 168, 720, 4320]:
            res = self.client.get(f"/api/insulin/history?hours={h}")
            self.assertEqual(res.status_code, 200)

        # Out of bounds or malformed hours
        for h in [0, -10, 5000, "abc", "1.5"]:
            res = self.client.get(f"/api/insulin/history?hours={h}")
            self.assertEqual(res.status_code, 422, f"Expected 422 for hours={h}, got {res.status_code}")

    def test_response_schema_structure(self):
        """Verify returned JSON structure matches expected schema with is_imputed and confidence_score."""
        res = self.client.get("/api/insulin/history?hours=168&include_imputed=true")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIsInstance(data, list)

        prev_ts = None
        for item in data:
            expected_keys = {
                "id", "timestamp", "rapid_acting", "long_acting", "meal",
                "correction", "user_change", "device", "serial_number",
                "is_imputed", "confidence_score"
            }
            for key in expected_keys:
                self.assertIn(key, item, f"Key '{key}' missing from item: {item}")

            self.assertIsInstance(item["is_imputed"], bool)
            self.assertIsInstance(item["timestamp"], str)

            ts_parsed = datetime.fromisoformat(item["timestamp"])
            self.assertIsNotNone(ts_parsed)

            if prev_ts is not None:
                self.assertGreaterEqual(ts_parsed, prev_ts, "Doses must be chronologically ordered")
            prev_ts = ts_parsed

            if item["is_imputed"]:
                self.assertIsNotNone(item["confidence_score"], "Imputed dose must have confidence_score")
                self.assertGreaterEqual(item["confidence_score"], 0.0)
                self.assertLessEqual(item["confidence_score"], 1.0)

    def test_init_db_idempotency_sequential(self):
        """Verify init_db can be called repeatedly without throwing errors or duplicating columns."""
        for i in range(5):
            try:
                init_db()
            except Exception as e:
                self.fail(f"init_db failed on sequential iteration {i}: {e}")

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'insulin_doses'
                """)
                cols = [row[0] for row in cur.fetchall()]
                self.assertEqual(cols.count("is_imputed"), 1, "is_imputed column duplicated")
                self.assertEqual(cols.count("confidence_score"), 1, "confidence_score column duplicated")
        finally:
            conn.close()

    def test_init_db_idempotency_concurrent(self):
        """Verify concurrent executions of init_db complete cleanly without race condition failures."""
        def run_init():
            init_db()

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(run_init) for _ in range(5)]
            for f in concurrent.futures.as_completed(futures):
                # Ensure no exceptions raised
                f.result()

    def test_concurrent_api_requests(self):
        """Stress test /api/insulin/history under concurrent load with varying params."""
        def make_request(req_id):
            params = [
                "/api/insulin/history?hours=24&include_imputed=true",
                "/api/insulin/history?hours=48&include_imputed=false",
                "/api/insulin/history?hours=12&include_imputed=invalid",
                "/api/insulin/history?hours=-5",
            ]
            url = params[req_id % len(params)]
            res = self.client.get(url)
            return res.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_request, i) for i in range(40)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(len(results), 40)
        for status in results:
            self.assertIn(status, [200, 422])


if __name__ == "__main__":
    unittest.main()
