"""
Adversarial Edge-Case and Stress Test Suite for Milestone M4 Phase 2 (R3 & R1/R2/R3 Cross-Feature Interactions)
Author: Challenger 2
"""

import sys
import os
import unittest
import math
import concurrent.futures
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
    get_time_of_day_bucket,
    calculate_nutritional_impact_modifiers,
    get_nutritional_impact,
    FALLBACK_NUTRITIONAL_BUCKETS
)
from dietary_analysis import (
    analyze_glucose_dataset,
    detect_postprandial_spikes,
    detect_dawn_phenomenon,
    detect_nocturnal_hypos,
    calculate_glycemic_variability,
    generate_report
)
from imputation import detect_and_impute_missing_doses
from e2e_tests.contracts import generate_synthetic_glucose_data


class TestAdversarialR3AndInteractions(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)
        self.tz_ny = pytz.timezone("America/New_York")

    # =========================================================================
    # 1. Midnight & Time-Bucket Boundary Tests (R3)
    # =========================================================================
    def test_01_boundary_timestamps_get_time_of_day_bucket(self):
        """Test exact hour transitions for get_time_of_day_bucket."""
        # Morning: 04:00 - 11:00
        dt_0400 = self.tz_ny.localize(datetime(2026, 8, 4, 4, 0, 0))
        dt_1059 = self.tz_ny.localize(datetime(2026, 8, 4, 10, 59, 59))
        self.assertEqual(get_time_of_day_bucket(dt_0400, "America/New_York"), "morning")
        self.assertEqual(get_time_of_day_bucket(dt_1059, "America/New_York"), "morning")

        # Afternoon: 11:00 - 17:00
        dt_1100 = self.tz_ny.localize(datetime(2026, 8, 4, 11, 0, 0))
        dt_1659 = self.tz_ny.localize(datetime(2026, 8, 4, 16, 59, 59))
        self.assertEqual(get_time_of_day_bucket(dt_1100, "America/New_York"), "afternoon")
        self.assertEqual(get_time_of_day_bucket(dt_1659, "America/New_York"), "afternoon")

        # Evening: 17:00 - 22:00
        dt_1700 = self.tz_ny.localize(datetime(2026, 8, 4, 17, 0, 0))
        dt_2159 = self.tz_ny.localize(datetime(2026, 8, 4, 21, 59, 59))
        self.assertEqual(get_time_of_day_bucket(dt_1700, "America/New_York"), "evening")
        self.assertEqual(get_time_of_day_bucket(dt_2159, "America/New_York"), "evening")

        # Night: 22:00 - 04:00
        dt_2200 = self.tz_ny.localize(datetime(2026, 8, 4, 22, 0, 0))
        dt_2359 = self.tz_ny.localize(datetime(2026, 8, 4, 23, 59, 59))
        dt_0000 = self.tz_ny.localize(datetime(2026, 8, 4, 0, 0, 0))
        dt_0359 = self.tz_ny.localize(datetime(2026, 8, 4, 3, 59, 59))
        self.assertEqual(get_time_of_day_bucket(dt_2200, "America/New_York"), "night")
        self.assertEqual(get_time_of_day_bucket(dt_2359, "America/New_York"), "night")
        self.assertEqual(get_time_of_day_bucket(dt_0000, "America/New_York"), "night")
        self.assertEqual(get_time_of_day_bucket(dt_0359, "America/New_York"), "night")

    def test_02_invalid_or_extreme_timezone_strings(self):
        """Verify behavior with invalid timezone string, empty string, or None."""
        dt = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
        # Invalid timezone should fallback to UTC without error
        b_invalid = get_time_of_day_bucket(dt, "NonExistent/Timezone_123")
        self.assertIn(b_invalid, ["morning", "afternoon", "evening", "night"])

        b_none = get_time_of_day_bucket(dt, None)
        self.assertEqual(b_none, "afternoon")

        res = calculate_nutritional_impact_modifiers(readings=[], doses=[], timezone_str="Invalid/Timezone")
        self.assertIn("time_buckets", res)

    # =========================================================================
    # 2. Corrupted, Missing, and Non-Numeric Input Resilience (BUG FINDING)
    # =========================================================================
    def test_03_corrupted_reading_values_resilience(self):
        """
        Verify calculate_nutritional_impact_modifiers handles corrupted reading values
        (None, non-numeric strings) gracefully without raising TypeError or ValueError.
        """
        now = datetime.now(timezone.utc)
        bad_readings = [
            {"timestamp": now.isoformat(), "value": None},
            {"timestamp": (now + timedelta(minutes=15)).isoformat(), "value": "corrupted"},
        ]
        
        res1 = calculate_nutritional_impact_modifiers(readings=bad_readings, doses=[])
        self.assertIn("time_buckets", res1)

        bad_readings_str = [
            {"timestamp": now.isoformat(), "value": "corrupted_string"},
        ]
        res2 = calculate_nutritional_impact_modifiers(readings=bad_readings_str, doses=[])
        self.assertIn("time_buckets", res2)

    def test_04_out_of_order_and_naive_timestamps(self):
        """Test unsorted readings with naive datetimes and mixed string formats."""
        base = datetime(2026, 8, 1, 12, 0, 0) # naive
        readings = [
            {"timestamp": base + timedelta(minutes=60), "value": 160.0},
            {"timestamp": (base + timedelta(minutes=30)).isoformat(), "value": 140.0},
            {"timestamp": base, "value": 100.0},
        ]
        doses = [
            {"timestamp": (base + timedelta(minutes=5)).isoformat(), "meal": 4.0, "rapid_acting": 4.0}
        ]

        res = calculate_nutritional_impact_modifiers(readings=readings, doses=doses)
        self.assertIn("time_buckets", res)

    def test_05_missing_database_and_null_responses(self):
        """Verify behavior when database raises exceptions or returns empty lists."""
        with patch("db.get_history", side_effect=Exception("DB Connection Error")), \
             patch("db.get_insulin_history", side_effect=Exception("DB Connection Error")):
            res = calculate_nutritional_impact_modifiers(readings=None, doses=None)
            self.assertIn("time_buckets", res)
            self.assertIn("recommendations", res)
            # Should return default fallbacks
            self.assertEqual(res["time_buckets"]["Morning"]["modifier"], 1.25)

    # =========================================================================
    # 3. Extreme Excursions and Modifier Clamping Limits
    # =========================================================================
    def test_06_modifier_clamping_upper_and_lower_bounds(self):
        """Verify that modifiers are strictly clamped to [0.50, 2.50]."""
        base_dt = self.tz_ny.localize(datetime(2026, 8, 1, 0, 0))
        readings = []
        doses = []

        # Afternoon baseline (13:00): 3 events with +20.0 rise
        for day in range(3):
            t = base_dt + timedelta(days=day, hours=13)
            doses.append({"timestamp": t.isoformat(), "meal": 5.0, "rapid_acting": 5.0})
            readings.append({"timestamp": t.isoformat(), "value": 100.0})
            readings.append({"timestamp": (t + timedelta(minutes=45)).isoformat(), "value": 120.0})
            readings.append({"timestamp": (t + timedelta(minutes=120)).isoformat(), "value": 110.0})

        # Morning extreme high rise (+200.0 rise -> raw mod = 200/20 = 10.0 -> clamp to 2.50)
        for day in range(3):
            t = base_dt + timedelta(days=day, hours=8)
            doses.append({"timestamp": t.isoformat(), "meal": 5.0, "rapid_acting": 5.0})
            readings.append({"timestamp": t.isoformat(), "value": 100.0})
            readings.append({"timestamp": (t + timedelta(minutes=60)).isoformat(), "value": 300.0})
            readings.append({"timestamp": (t + timedelta(minutes=120)).isoformat(), "value": 200.0})

        # Evening drop / negative rise (rise = +2.0 mg/dL -> raw mod = 2/20 = 0.10 -> clamp to 0.50)
        for day in range(3):
            t = base_dt + timedelta(days=day, hours=18)
            doses.append({"timestamp": t.isoformat(), "meal": 5.0, "rapid_acting": 5.0})
            readings.append({"timestamp": t.isoformat(), "value": 100.0})
            readings.append({"timestamp": (t + timedelta(minutes=50)).isoformat(), "value": 102.0})
            readings.append({"timestamp": (t + timedelta(minutes=120)).isoformat(), "value": 90.0})

        res = calculate_nutritional_impact_modifiers(readings=readings, doses=doses)
        buckets = res["time_buckets"]

        self.assertEqual(buckets["Morning"]["modifier"], 2.50, "Upper bound clamp should be 2.50")
        self.assertEqual(buckets["Evening"]["modifier"], 0.50, "Lower bound clamp should be 0.50")

    # =========================================================================
    # 4. Cross-Feature Interactions (R1 x R2 x R3)
    # =========================================================================
    def test_07_r2_imputed_doses_injected_into_r3_nutritional_model(self):
        """
        Cross-Feature Test R2 x R3:
        Generate data with missing correction doses, run R2 imputation,
        pass combined logged+imputed doses into R3 model.
        Verify R3 handles imputed doses cleanly.
        """
        readings, logged_doses = generate_synthetic_glucose_data(days=5, pattern="unlogged_corrections")
        imputed_doses = detect_and_impute_missing_doses(readings, logged_doses)
        self.assertGreater(len(imputed_doses), 0, "R2 should detect missing doses.")

        combined_doses = logged_doses + imputed_doses
        res = calculate_nutritional_impact_modifiers(readings=readings, doses=combined_doses)

        self.assertIn("time_buckets", res)
        self.assertIn("recommendations", res)
        for b in ["Morning", "Afternoon", "Evening", "Night"]:
            self.assertIn("modifier", res["time_buckets"][b])
            self.assertGreaterEqual(res["time_buckets"][b]["modifier"], 0.50)
            self.assertLessEqual(res["time_buckets"][b]["modifier"], 2.50)

    def test_08_r1_dietary_report_with_r2_imputed_and_r3_modifiers(self):
        """
        Cross-Feature Test R1 x R2 x R3:
        Run R2 imputation -> update dataset -> calculate R3 nutritional modifiers -> generate R1 report.
        """
        readings, logged_doses = generate_synthetic_glucose_data(days=7, pattern="dawn_hypo")
        imputed_doses = detect_and_impute_missing_doses(readings, logged_doses)
        r3_res = calculate_nutritional_impact_modifiers(readings=readings, doses=logged_doses + imputed_doses)

        # Generate report without writing to disk
        report_md = generate_report(readings=readings, output_path=None, use_network=False)

        self.assertIn("# Executive Summary", report_md)
        self.assertIn("Observed Glycemic Trends & Anomalies", report_md)
        self.assertIn("Dawn Phenomenon", report_md)
        self.assertIn("Nocturnal Hypoglycemia", report_md)
        self.assertIsInstance(r3_res["recommendations"], list)

    def test_09_r3_spike_detection_fallback_when_doses_are_empty(self):
        """
        Test Strategy 2 (Continuous Spike Detection) in R3 when doses list is completely empty.
        """
        base_dt = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
        readings = []
        # Create 15-minute readings with periodic spikes >= 15 mg/dL
        for i in range(100):
            ts = base_dt + timedelta(minutes=15 * i)
            val = 100.0 + (30.0 if (i % 12 == 4) else 0.0)
            readings.append({"timestamp": ts.isoformat(), "value": val})

        res = calculate_nutritional_impact_modifiers(readings=readings, doses=[])
        self.assertIn("time_buckets", res)
        self.assertIn("recommendations", res)

    # =========================================================================
    # 5. Concurrent API Request Stress Test
    # =========================================================================
    def test_10_concurrent_api_requests(self):
        """Send 20 concurrent GET requests to /api/nutritional-impact and check stability."""
        def call_endpoint(endpoint):
            resp = self.client.get(endpoint)
            return resp.status_code, resp.json()

        endpoints = [
            "/api/nutritional-impact",
            "/api/nutritional-impact/summary",
            "/api/nutritional-impact?hours=168",
            "/api/nutritional-impact?hours=720"
        ] * 5

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(call_endpoint, ep) for ep in endpoints]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        for status_code, json_body in results:
            self.assertEqual(status_code, 200)
            self.assertIn("time_buckets", json_body)
            self.assertIn("recommendations", json_body)


if __name__ == "__main__":
    unittest.main()
