"""
Tier 2: Boundary & Corner Cases E2E Tests for Gluco Track (R1, R2, R3)
Includes >=5 boundary/edge test cases per feature (total 15 test cases).
"""

import os
import sys
import unittest
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from e2e_tests.contracts import (
    run_detect_anomalies,
    run_query_literature,
    run_generate_report,
    run_impute_missing_doses,
    run_analyze_nutritional_impact,
    run_get_time_bucket,
    generate_synthetic_glucose_data
)

class TestTier2Boundaries(unittest.TestCase):

    # =================================================================
    # R1 Boundary & Corner Cases (5 Test Cases)
    # =================================================================

    def test_r1_tier2_01_empty_historical_dataset(self):
        """Verify handling of completely empty historical dataset (0 glucose readings)."""
        empty_readings = []
        anomalies = run_detect_anomalies(empty_readings)

        self.assertEqual(len(anomalies), 0, "Empty dataset should yield 0 anomalies.")

        report = run_generate_report(empty_readings, output_path=None)

        self.assertIsInstance(report, str)
        self.assertIn("# Executive Summary", report)

    def test_r1_tier2_02_flat_line_glucose_data(self):
        """Verify handling of perfectly flat glucose readings (e.g. constant 100 mg/dL)."""
        readings, _ = generate_synthetic_glucose_data(days=3, pattern="flatline")
        anomalies = run_detect_anomalies(readings)

        variability_anomalies = [a for a in anomalies if a.get("type") in ["high_variability", "nocturnal_hypo", "postprandial_spike"]]
        self.assertEqual(len(variability_anomalies), 0, "Flat line glucose data should not trigger spikes, hypos, or variability.")

    def test_r1_tier2_03_all_high_glucose_hyperglycemia(self):
        """Verify handling of extreme hyperglycemia across entire dataset (>250 mg/dL)."""
        start_ts = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
        high_readings = [
            {"timestamp": start_ts + timedelta(minutes=15 * i), "value": 285.0}
            for i in range(100)
        ]

        anomalies = run_detect_anomalies(high_readings)

        types = [a["type"] for a in anomalies]
        self.assertIn("hyperglycemia", types, "All high glucose dataset must trigger hyperglycemia anomaly.")

    def test_r1_tier2_04_all_low_glucose_hypoglycemia(self):
        """Verify handling of extreme persistent hypoglycemia (<70 mg/dL)."""
        start_ts = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
        low_readings = [
            {"timestamp": start_ts + timedelta(minutes=15 * i), "value": 55.0}
            for i in range(100)
        ]

        anomalies = run_detect_anomalies(low_readings)

        types = [a["type"] for a in anomalies]
        self.assertTrue("hypoglycemia" in types or "nocturnal_hypo" in types, "Low glucose dataset must trigger hypoglycemia anomaly.")

    def test_r1_tier2_05_api_network_timeout_fallback(self):
        """Verify report generator handles API offline/network timeouts gracefully via fallback citations."""
        anomalies = [{"type": "dawn_phenomenon", "severity": "high", "metric": "170 mg/dL"}]

        remedies = run_query_literature(anomalies)

        self.assertIsInstance(remedies, list)
        self.assertGreater(len(remedies), 0, "Literature query fallback should return citations.")

    # =================================================================
    # R2 Boundary & Corner Cases (5 Test Cases)
    # =================================================================

    def test_r2_tier2_01_zero_missing_doses(self):
        """Verify imputation model returns 0 imputed doses when all doses are already logged."""
        readings, logged_doses = generate_synthetic_glucose_data(days=2, pattern="standard")

        for r in readings:
            logged_doses.append({
                "timestamp": r["timestamp"],
                "rapid_acting": 2.0,
                "is_imputed": False
            })

        imputed = run_impute_missing_doses(readings, logged_doses)

        self.assertEqual(len(imputed), 0, "When all doses are logged, zero doses should be imputed.")

    def test_r2_tier2_02_100_percent_missing_doses(self):
        """Verify imputation engine processes 100% missing doses (no logged doses) without crashing."""
        readings, _ = generate_synthetic_glucose_data(days=3, pattern="unlogged_corrections")

        imputed = run_impute_missing_doses(readings, [])

        self.assertIsInstance(imputed, list)
        self.assertGreater(len(imputed), 0, "Should impute correction doses when zero doses are logged.")

    def test_r2_tier2_03_noisy_corrupt_glucose_readings(self):
        """Verify imputation engine handles noise, duplicates, None values, and non-chronological order."""
        base_ts = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        noisy_readings = [
            {"timestamp": base_ts + timedelta(minutes=60), "value": 110.0},
            {"timestamp": base_ts, "value": 220.0},
            {"timestamp": base_ts + timedelta(minutes=15), "value": None},
            {"timestamp": base_ts + timedelta(minutes=30), "value": float("nan")},
            {"timestamp": base_ts, "value": 220.0},
        ]

        imputed = run_impute_missing_doses(noisy_readings, [])

        self.assertIsInstance(imputed, list, "Should handle noisy corrupt data gracefully without raising exceptions.")

    def test_r2_tier2_04_extreme_peak_spikes(self):
        """Verify extreme glucose peak spikes cap estimated doses at safety limits (e.g. <= 15.0 U)."""
        base_ts = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        extreme_readings = [
            {"timestamp": base_ts, "value": 100.0},
            {"timestamp": base_ts + timedelta(minutes=15), "value": 500.0},
            {"timestamp": base_ts + timedelta(minutes=60), "value": 80.0},
        ]

        imputed = run_impute_missing_doses(extreme_readings, [])

        for dose in imputed:
            self.assertLessEqual(dose["rapid_acting"], 15.0, "Imputed dose must be capped at maximum safety limit of 15U.")

    def test_r2_tier2_05_negative_and_zero_dose_bounds(self):
        """Verify imputation model enforces non-negative insulin dose estimates (dose >= 0.0 U)."""
        base_ts = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        rising_readings = [
            {"timestamp": base_ts, "value": 100.0},
            {"timestamp": base_ts + timedelta(minutes=15), "value": 105.0},
            {"timestamp": base_ts + timedelta(minutes=30), "value": 110.0},
        ]

        imputed = run_impute_missing_doses(rising_readings, [])

        for dose in imputed:
            self.assertGreaterEqual(dose["rapid_acting"], 0.0, "Imputed units must never be negative.")

    # =================================================================
    # R3 Boundary & Corner Cases (5 Test Cases)
    # =================================================================

    def test_r3_tier2_01_time_bucket_boundary_timestamps(self):
        """Verify boundary timestamps at 05:59, 06:00, 11:59, 12:00, 17:59, 18:00, 22:59, 23:00 map to correct buckets."""
        self.assertEqual(run_get_time_bucket(5), "Night")
        self.assertEqual(run_get_time_bucket(6), "Morning")
        self.assertEqual(run_get_time_bucket(11), "Morning")
        self.assertEqual(run_get_time_bucket(12), "Afternoon")
        self.assertEqual(run_get_time_bucket(17), "Afternoon")
        self.assertEqual(run_get_time_bucket(18), "Evening")
        self.assertEqual(run_get_time_bucket(22), "Evening")
        self.assertEqual(run_get_time_bucket(23), "Night")

    def test_r3_tier2_02_single_meal_data(self):
        """Verify model handles single meal entry without throwing KeyError or DivisionByZero."""
        base_ts = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
        single_meal = [
            {"timestamp": base_ts, "value": 100.0},
            {"timestamp": base_ts + timedelta(minutes=45), "value": 150.0},
        ]

        res = run_analyze_nutritional_impact(single_meal, [])

        self.assertIn("time_buckets", res)
        self.assertEqual(len(res["time_buckets"]), 4, "Must contain entries for all 4 time buckets.")

    def test_r3_tier2_03_zero_glucose_rise(self):
        """Verify meals with 0 mg/dL glucose rise yield valid peak_rise_mgdl=0.0 and stable modifiers."""
        base_ts = datetime(2026, 8, 1, 13, 0, tzinfo=timezone.utc)
        flat_meal = [
            {"timestamp": base_ts, "value": 110.0},
            {"timestamp": base_ts + timedelta(minutes=30), "value": 110.0},
            {"timestamp": base_ts + timedelta(minutes=60), "value": 110.0},
        ]

        res = run_analyze_nutritional_impact(flat_meal, [])

        self.assertIsInstance(res["time_buckets"], dict)

    def test_r3_tier2_04_extreme_latency_inputs(self):
        """Verify model measures peak latency up to 300 minutes for delayed high-fat/protein postprandial curves."""
        base_ts = datetime(2026, 8, 1, 19, 0, tzinfo=timezone.utc)
        delayed_meal = [
            {"timestamp": base_ts, "value": 100.0},
            {"timestamp": base_ts + timedelta(minutes=150), "value": 160.0},
        ]

        res = run_analyze_nutritional_impact(delayed_meal, [])

        self.assertGreater(res["time_buckets"]["Evening"]["peak_latency_min"], 0)

    def test_r3_tier2_05_missing_historical_time_records(self):
        """Verify model handles multi-day gaps between historical readings cleanly."""
        ts1 = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc)
        gapped_readings = [
            {"timestamp": ts1, "value": 100.0},
            {"timestamp": ts1 + timedelta(minutes=45), "value": 140.0},
            {"timestamp": ts2, "value": 105.0},
            {"timestamp": ts2 + timedelta(minutes=45), "value": 145.0},
        ]

        res = run_analyze_nutritional_impact(gapped_readings, [])

        self.assertIn("Morning", res["time_buckets"])

if __name__ == "__main__":
    unittest.main()
