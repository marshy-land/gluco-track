"""
Adversarial Verification Test Suite - Challenger M4-6
Target: White-box verification of R3 (ml_heuristics.py) and R1/R2/R3 cross-feature interactions.
"""

import sys
import os
import unittest
import math
from datetime import datetime, timezone, timedelta
import pytz
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import app
from ml_heuristics import (
    calculate_personalized_isf,
    predict_adaptive_glucose,
    train_predictive_model,
    calculate_nutritional_impact_modifiers,
    get_time_of_day_bucket,
    _safe_float,
    parse_dt,
    transpose,
    matmul,
    invert_matrix,
    DEFAULT_ISFS,
    FALLBACK_NUTRITIONAL_BUCKETS
)
from imputation import detect_and_impute_missing_doses
from dietary_analysis import analyze_glucose_dataset, generate_report
from e2e_tests.contracts import generate_synthetic_glucose_data


class TestChallengerM46Adversarial(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)

    # =========================================================================
    # 1. White-Box Defensive Parsing Tests for ml_heuristics.py
    # =========================================================================
    def test_safe_float_edge_cases(self):
        """Test _safe_float against adversarial inputs."""
        self.assertEqual(_safe_float(None, 5.0), 5.0)
        self.assertEqual(_safe_float("123.45"), 123.45)
        self.assertEqual(_safe_float("invalid", 0.0), 0.0)
        self.assertEqual(_safe_float(float('nan'), 10.0), 10.0)
        self.assertEqual(_safe_float(float('inf'), 10.0), 10.0)
        self.assertEqual(_safe_float(float('-inf'), 10.0), 10.0)
        self.assertEqual(_safe_float({}, 0.0), 0.0)
        self.assertEqual(_safe_float([], 0.0), 0.0)

    def test_parse_dt_edge_cases(self):
        """Test parse_dt against string timestamps, naive datetimes, and invalid objects."""
        now_utc = datetime.now(timezone.utc)
        self.assertEqual(parse_dt(now_utc), now_utc)

        naive_dt = datetime(2026, 8, 4, 12, 0, 0)
        parsed_naive = parse_dt(naive_dt)
        self.assertIsNotNone(parsed_naive.tzinfo)

        iso_z = "2026-08-04T12:00:00Z"
        self.assertIsNotNone(parse_dt(iso_z))

        iso_offset = "2026-08-04T12:00:00-04:00"
        self.assertIsNotNone(parse_dt(iso_offset))

        self.assertIsNone(parse_dt("not-a-timestamp"))
        self.assertIsNone(parse_dt(None))
        self.assertIsNone(parse_dt(123456789))

    def test_calculate_personalized_isf_defensive_parsing(self):
        """
        Verify calculate_personalized_isf safely handles string timestamps,
        string numbers, nulls, and non-dict entries without uncaught exceptions.
        """
        now = datetime.now(timezone.utc)
        mock_doses = [
            {
                "timestamp": (now - timedelta(hours=5)).isoformat(),
                "rapid_acting": "3.5",
                "meal": "0.0",
                "correction": "0.0",
                "user_change": "0.0"
            },
            {
                "timestamp": (now - timedelta(hours=10)).isoformat(),
                "rapid_acting": None,
                "meal": "invalid",
                "correction": float('nan'),
                "user_change": float('inf')
            },
            "corrupted_dose_entry",
            None
        ]
        mock_readings = [
            {"timestamp": (now - timedelta(hours=5)).isoformat(), "value": "180.0"},
            {"timestamp": (now - timedelta(hours=1)).isoformat(), "value": "100.0"},
            {"timestamp": (now - timedelta(hours=2)).isoformat(), "value": None},
            {"timestamp": "invalid_ts", "value": "150.0"},
            "corrupted_reading_entry"
        ]

        with patch("db.get_insulin_history", return_value=mock_doses), \
             patch("db.get_history", return_value=mock_readings):
            res = calculate_personalized_isf()
            self.assertIsInstance(res, dict)
            for k in ["morning", "afternoon", "evening", "night", "global"]:
                self.assertIn(k, res)
                self.assertIsInstance(res[k], float)

    def test_predict_adaptive_glucose_defensive_parsing(self):
        """
        Verify predict_adaptive_glucose handles string reading values, string IOB,
        None, and invalid coefficients gracefully.
        """
        now = datetime.now(timezone.utc)
        mock_readings = [
            {"timestamp": (now - timedelta(minutes=60)).isoformat(), "value": "120.0"},
            {"timestamp": (now - timedelta(minutes=45)).isoformat(), "value": "125.0"},
            {"timestamp": (now - timedelta(minutes=30)).isoformat(), "value": "130.0"},
            {"timestamp": (now - timedelta(minutes=15)).isoformat(), "value": "135.0"},
            {"timestamp": (now).isoformat(), "value": "140.0"},
            "corrupted_entry"
        ]

        mock_params = {
            "model_trained": True,
            "coefficients": [1.0, 0.5, 0.2, 0.1, 0.05, 0.0, 0.0, -2.0]
        }

        with patch("ml_heuristics.load_heuristics_params", return_value=mock_params):
            res = predict_adaptive_glucose(mock_readings, iob_val="1.5")
            self.assertIsInstance(res, list)
            self.assertEqual(len(res), 3)
            for p in res:
                self.assertIn("minutes", p)
                self.assertIn("value", p)
                self.assertIn("trend_rate", p)
                self.assertGreaterEqual(p["value"], 40.0)
                self.assertLessEqual(p["value"], 400.0)

    def test_train_predictive_model_insufficient_or_corrupted(self):
        """Verify train_predictive_model with empty data or insufficient samples."""
        with patch("db.get_history", return_value=[]), \
             patch("db.get_insulin_history", return_value=[]):
            success, msg = train_predictive_model(history_days=30)
            self.assertFalse(success)
            self.assertIn("Insufficient", msg)

    def test_matrix_inversion_singular_resilience(self):
        """Verify invert_matrix correctly raises ValueError on singular matrix."""
        singular_matrix = [
            [1.0, 2.0, 3.0],
            [2.0, 4.0, 6.0],
            [1.0, 1.0, 1.0]
        ]
        with self.assertRaises(ValueError):
            invert_matrix(singular_matrix)

    def test_calculate_nutritional_impact_modifiers_defensive_parsing(self):
        """
        Verify calculate_nutritional_impact_modifiers handles string timestamps,
        string numbers, nulls, and matrix inputs safely.
        """
        now = datetime.now(timezone.utc)
        readings = [
            {"timestamp": (now - timedelta(minutes=i*15)).isoformat(), "value": str(100.0 + i*5)}
            for i in range(10)
        ]
        readings.append({"timestamp": (now + timedelta(minutes=200)).isoformat(), "value": None})
        readings.append("invalid_reading")

        doses = [
            {"timestamp": (now - timedelta(minutes=60)).isoformat(), "meal": "5.0", "rapid_acting": "5.0"},
            {"timestamp": (now - timedelta(minutes=30)).isoformat(), "meal": None, "rapid_acting": float('nan')},
            "invalid_dose"
        ]

        res = calculate_nutritional_impact_modifiers(readings=readings, doses=doses)
        self.assertIn("time_buckets", res)
        self.assertIn("recommendations", res)

    # =========================================================================
    # 2. Cross-Feature Integration Tests (R1 x R2 x R3)
    # =========================================================================
    def test_r1_r2_r3_end_to_end_pipeline_integration(self):
        """
        Full integration test:
        1. Generate synthetic data with missing doses (R2 trigger)
        2. Impute doses (R2)
        3. Train R3 model and calculate R3 nutritional impact modifiers
        4. Generate R1 dietary analysis report incorporating overall dataset
        """
        readings, logged_doses = generate_synthetic_glucose_data(days=5, pattern="unlogged_corrections")

        # Step 1: R2 Imputation
        imputed_doses = detect_and_impute_missing_doses(readings, logged_doses)
        self.assertIsInstance(imputed_doses, list)

        # Step 2: R3 Nutritional Modifiers with combined doses
        all_doses = logged_doses + imputed_doses
        r3_impact = calculate_nutritional_impact_modifiers(readings=readings, doses=all_doses)
        self.assertIn("time_buckets", r3_impact)
        self.assertIn("Morning", r3_impact["time_buckets"])

        # Step 3: R1 Dietary Report Generation
        report = generate_report(readings=readings, output_path=None, use_network=False)
        self.assertIsInstance(report, str)
        self.assertIn("# Executive Summary", report)

    # =========================================================================
    # 3. API Endpoint Verification
    # =========================================================================
    def test_api_nutritional_impact_endpoint(self):
        """Test GET /api/nutritional-impact."""
        resp = self.client.get("/api/nutritional-impact")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("time_buckets", data)
        self.assertIn("recommendations", data)

    def test_api_heuristics_status_endpoint(self):
        """Test GET /api/heuristics/status."""
        resp = self.client.get("/api/heuristics/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("model_trained", data)
        self.assertIn("isf", data)


if __name__ == "__main__":
    unittest.main()
