"""
Adversarial Verification Suite for Challenger M4 4
Focus: R3 (ml_heuristics.py) and R1/R2/R3 Cross-Feature Interaction & Edge-Case Safety
"""

import sys
import os
import unittest
import math
import pytz
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import app
from ml_heuristics import (
    parse_dt,
    get_time_of_day_bucket,
    calculate_nutritional_impact_modifiers,
    get_nutritional_impact,
    calculate_personalized_isf,
    predict_adaptive_glucose,
    train_predictive_model,
    FALLBACK_NUTRITIONAL_BUCKETS
)
from dietary_analysis import (
    analyze_glucose_dataset,
    calculate_glycemic_stats,
    generate_report
)
from imputation import detect_and_impute_missing_doses
from prediction import calculate_iob


class TestChallenger4Adversarial(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)
        self.tz_ny = pytz.timezone("America/New_York")

    # -------------------------------------------------------------------------
    # R3 Defensive Parsing & Null/String Telemetry Safety
    # -------------------------------------------------------------------------
    def test_r3_parse_dt_edge_cases(self):
        """Test parse_dt with all kinds of malformed input types."""
        self.assertIsNone(parse_dt(None))
        self.assertIsNone(parse_dt("invalid_date_string"))
        self.assertIsNone(parse_dt(123456789))
        self.assertIsNone(parse_dt([]))
        self.assertIsNone(parse_dt({}))
        self.assertIsNone(parse_dt(True))
        
        # Valid ISO strings
        dt_valid = parse_dt("2026-08-04T12:00:00Z")
        self.assertIsNotNone(dt_valid)
        self.assertEqual(dt_valid.tzinfo, timezone.utc)

        # Naive datetime
        naive_dt = datetime(2026, 8, 4, 12, 0, 0)
        dt_localized = parse_dt(naive_dt)
        self.assertIsNotNone(dt_localized)
        self.assertEqual(dt_localized.tzinfo, timezone.utc)

    def test_r3_nutritional_impact_malformed_readings_and_doses(self):
        """
        Pass dirty/malformed lists containing nulls, non-numeric strings, NaN, Inf,
        booleans, non-dict objects into calculate_nutritional_impact_modifiers.
        """
        dirty_readings = [
            None,
            "not_a_dict",
            123,
            {"timestamp": "2026-08-04T08:00:00Z", "value": None},
            {"timestamp": "2026-08-04T08:15:00Z", "value": "INVALID"},
            {"timestamp": "2026-08-04T08:30:00Z", "value": "150.5"},  # String float
            {"timestamp": "2026-08-04T08:45:00Z", "value": float("nan")},
            {"timestamp": "2026-08-04T09:00:00Z", "value": float("inf")},
            {"timestamp": "2026-08-04T09:15:00Z", "value": -float("inf")},
            {"timestamp": "2026-08-04T09:30:00Z", "value": 140.0},
            {"timestamp": "invalid_ts", "value": 130.0},
            {"value": 120.0}, # missing timestamp key
        ]

        dirty_doses = [
            None,
            "not_a_dict",
            456,
            {"timestamp": "2026-08-04T08:00:00Z", "meal": None, "rapid_acting": "3.5"},
            {"timestamp": "2026-08-04T08:05:00Z", "meal": "INVALID", "rapid_acting": "BAD"},
            {"timestamp": "2026-08-04T08:10:00Z", "meal": float("nan"), "rapid_acting": float("inf")},
            {"timestamp": "2026-08-04T12:00:00Z", "meal": "5.0", "rapid_acting": 4.0},
            {"timestamp": "unparseable", "meal": 5.0},
        ]

        res = calculate_nutritional_impact_modifiers(readings=dirty_readings, doses=dirty_doses)
        self.assertIn("time_buckets", res)
        self.assertIn("recommendations", res)
        for b in ["Morning", "Afternoon", "Evening", "Night"]:
            self.assertIn("peak_rise_mgdl", res["time_buckets"][b])
            self.assertIn("peak_latency_min", res["time_buckets"][b])
            self.assertIn("modifier", res["time_buckets"][b])
            self.assertGreaterEqual(res["time_buckets"][b]["modifier"], 0.50)
            self.assertLessEqual(res["time_buckets"][b]["modifier"], 2.50)

    def test_r3_personalized_isf_with_string_numbers_and_nulls(self):
        """
        Verify calculate_personalized_isf handles doses/readings with string numbers
        and nulls safely without crashing.
        """
        now = datetime.now(timezone.utc)
        mock_doses = [
            {"timestamp": now - timedelta(hours=10), "rapid_acting": "3.0", "meal": "0.0", "correction": "0.0"},
            {"timestamp": now - timedelta(hours=5), "rapid_acting": None, "meal": "4.0", "correction": None},
        ]
        mock_readings = [
            {"timestamp": now - timedelta(hours=10), "value": "180.0"},
            {"timestamp": now - timedelta(hours=6), "value": "100.0"},
            {"timestamp": now - timedelta(hours=5), "value": 200.0},
            {"timestamp": now - timedelta(hours=1), "value": 120.0},
        ]

        with patch("db.get_insulin_history", return_value=mock_doses), \
             patch("db.get_history", return_value=mock_readings):
            res = calculate_personalized_isf()
            self.assertIn("morning", res)
            self.assertIn("global", res)
            self.assertEqual(res["global"], 50.0 or res.get("global"))

    def test_r3_predict_adaptive_glucose_edge_cases(self):
        """Verify predict_adaptive_glucose with insufficient readings, invalid timestamps, string values."""
        # Case 1: Insufficient readings (< 5)
        self.assertIsNone(predict_adaptive_glucose([], 0.0))

        # Case 2: 5 readings with string numbers and invalid values
        now = datetime.now(timezone.utc)
        readings = [
            {"timestamp": now - timedelta(minutes=60), "value": "120.0"},
            {"timestamp": now - timedelta(minutes=45), "value": "125.0"},
            {"timestamp": now - timedelta(minutes=30), "value": "130.0"},
            {"timestamp": now - timedelta(minutes=15), "value": "135.0"},
            {"timestamp": now, "value": "140.0"},
        ]

        with patch("ml_heuristics.load_heuristics_params", return_value={
            "model_trained": True,
            "coefficients": [1.0, 0.5, 0.2, 0.1, 0.05, 0.0, 0.0, -2.0]
        }):
            preds = predict_adaptive_glucose(readings, iob_val="1.5")
            self.assertIsNotNone(preds)
            self.assertEqual(len(preds), 3)
            for p in preds:
                self.assertIn("minutes", p)
                self.assertIn("value", p)
                self.assertGreaterEqual(p["value"], 40.0)
                self.assertLessEqual(p["value"], 400.0)

    # -------------------------------------------------------------------------
    # Cross-Feature Interactions (R1 x R2 x R3)
    # -------------------------------------------------------------------------
    def test_cross_feature_r1_r2_r3_pipeline(self):
        """
        Simulate full end-to-end pipeline:
        1. Raw noisy telemetry with string values and missing doses.
        2. R2 Imputation detects missing doses.
        3. R3 Nutritional Impact computes modifiers on combined dataset.
        4. R1 Dietary Analysis calculates glycemic stats and generates report.
        """
        now = datetime.now(timezone.utc)
        raw_readings = []
        # Build 3 days of readings with dawn rise and postprandial spikes
        for i in range(288):
            t = now - timedelta(days=3) + timedelta(minutes=15 * i)
            val = 100.0
            if t.hour in [5, 6, 7]:
                val = 160.0
            elif t.hour in [12, 13, 18, 19]:
                val = 210.0
            raw_readings.append({"timestamp": t.isoformat(), "value": str(val)}) # String numbers!

        raw_doses = [
            {"timestamp": (now - timedelta(days=2)).isoformat(), "rapid_acting": "4.0", "meal": "4.0"},
            {"timestamp": (now - timedelta(days=1)).isoformat(), "rapid_acting": "5.0", "meal": "5.0"},
        ]

        # 1. R2 Imputation
        imputed = detect_and_impute_missing_doses(raw_readings, raw_doses)
        self.assertIsInstance(imputed, list)

        # 2. R3 Nutritional Impact
        all_doses = raw_doses + imputed
        r3_output = calculate_nutritional_impact_modifiers(readings=raw_readings, doses=all_doses)
        self.assertIn("time_buckets", r3_output)

        # 3. R1 Glycemic Stats & Report
        stats = calculate_glycemic_stats(raw_readings)
        self.assertIn("average", stats)
        self.assertIn("gmi", stats)

        report = generate_report(readings=raw_readings, output_path=None, use_network=False)
        self.assertIn("Executive Summary", report)

    # -------------------------------------------------------------------------
    # API HTTP Endpoint Adversarial Tests
    # -------------------------------------------------------------------------
    def test_api_nutritional_impact_edge_cases(self):
        """Test GET /api/nutritional-impact with extreme query parameters."""
        resp1 = self.client.get("/api/nutritional-impact?hours=1")
        self.assertEqual(resp1.status_code, 200)

        resp2 = self.client.get("/api/nutritional-impact?hours=4320")
        self.assertEqual(resp2.status_code, 200)

        resp_invalid = self.client.get("/api/nutritional-impact?hours=0")
        self.assertEqual(resp_invalid.status_code, 422) # FastAPI validation error

        resp_summary = self.client.get("/api/nutritional-impact/summary")
        self.assertEqual(resp_summary.status_code, 200)

    def test_api_predictions_and_heuristics_status(self):
        """Test GET /api/predictions and /api/heuristics/status under mocked empty / malformed DB."""
        with patch("db.get_latest_reading", return_value={"timestamp": datetime.now(timezone.utc), "value": "145.0"}), \
             patch("db.get_history", return_value=[]), \
             patch("db.get_insulin_history", return_value=[]):
            resp = self.client.get("/api/predictions")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["current_glucose"], 145.0)

        resp_status = self.client.get("/api/heuristics/status")
        self.assertEqual(resp_status.status_code, 200)
        self.assertIn("model_trained", resp_status.json())


if __name__ == "__main__":
    unittest.main()
