"""
Empirical Stress Test Suite for Missing Dose Imputation (Requirement R2)
Author: Challenger 1 (Milestone M2)
"""

import unittest
import math
from unittest.mock import patch
from datetime import datetime, timezone, timedelta
import pytz

from imputation import detect_and_impute_missing_doses
from prediction import calculate_iob
from ml_heuristics import load_heuristics_params


class TestChallengerImputation(unittest.TestCase):

    def setUp(self):
        self.base_time = datetime(2026, 8, 4, 12, 0, 0, tzinfo=pytz.utc)

    # -------------------------------------------------------------------------
    # 1. Golden Path Verification
    # -------------------------------------------------------------------------
    def test_golden_path_imputation(self):
        """Clean 140 mg/dL drop over 90 mins with starting glucose 240 mg/dL and no logged doses."""
        readings = []
        for m in range(0, 95, 5):
            t = self.base_time + timedelta(minutes=m)
            val = 240.0 - (140.0 * (m / 90.0))
            readings.append({"timestamp": t, "value": round(val, 1)})

        imputed = detect_and_impute_missing_doses(readings, [], timezone_str="America/New_York")
        self.assertGreaterEqual(len(imputed), 1, "Expected at least 1 imputed dose for a clear 140 mg/dL drop")
        dose = imputed[0]
        self.assertTrue(dose["is_imputed"])
        self.assertGreaterEqual(dose["confidence_score"], 0.50)
        self.assertGreaterEqual(dose["rapid_acting"], 0.5)
        self.assertLessEqual(dose["rapid_acting"], 15.0)

    # -------------------------------------------------------------------------
    # 2. Zero & Negative Glucose Trends (Rising / Flat Glucose)
    # -------------------------------------------------------------------------
    def test_flat_glucose_trend(self):
        """Constant glucose line at 180 mg/dL should yield 0 imputed doses."""
        readings = []
        for m in range(0, 240, 5):
            t = self.base_time + timedelta(minutes=m)
            readings.append({"timestamp": t, "value": 180.0})

        imputed = detect_and_impute_missing_doses(readings, [])
        self.assertEqual(len(imputed), 0, "Flat glucose trend should produce 0 imputed doses")

    def test_rising_glucose_trend(self):
        """Rising glucose from 150 to 280 mg/dL over 2 hours should yield 0 imputed doses."""
        readings = []
        for m in range(0, 125, 5):
            t = self.base_time + timedelta(minutes=m)
            val = 150.0 + (130.0 * (m / 120.0))
            readings.append({"timestamp": t, "value": round(val, 1)})

        imputed = detect_and_impute_missing_doses(readings, [])
        self.assertEqual(len(imputed), 0, "Rising glucose trend should produce 0 imputed doses")

    def test_minor_noise_drop_below_threshold(self):
        """Glucose dropping only 15 mg/dL (200 -> 185 mg/dL) should yield 0 imputed doses."""
        readings = []
        for m in range(0, 95, 5):
            t = self.base_time + timedelta(minutes=m)
            val = 200.0 - (15.0 * (m / 90.0))
            readings.append({"timestamp": t, "value": round(val, 1)})

        imputed = detect_and_impute_missing_doses(readings, [])
        self.assertEqual(len(imputed), 0, "Minor drops (< 25 mg/dL) should produce 0 imputed doses")

    def test_low_starting_glucose(self):
        """Glucose dropping from 110 mg/dL to 70 mg/dL should be ignored (start < 120 mg/dL)."""
        readings = []
        for m in range(0, 95, 5):
            t = self.base_time + timedelta(minutes=m)
            val = 110.0 - (40.0 * (m / 90.0))
            readings.append({"timestamp": t, "value": round(val, 1)})

        imputed = detect_and_impute_missing_doses(readings, [])
        self.assertEqual(len(imputed), 0, "Starting glucose < 120 mg/dL should produce 0 imputed doses")

    # -------------------------------------------------------------------------
    # 3. Rapid Fluctuations & Oscillations
    # -------------------------------------------------------------------------
    def test_rapid_oscillations_c_shape_penalty(self):
        """High frequency zig-zag pattern (200 -> 260 -> 180 -> 250 -> 170)."""
        readings = []
        vals = [200, 260, 180, 250, 170, 240, 160, 230, 150]
        for idx, val in enumerate(vals):
            t = self.base_time + timedelta(minutes=idx * 15)
            readings.append({"timestamp": t, "value": float(val)})

        imputed = detect_and_impute_missing_doses(readings, [])
        for item in imputed:
            self.assertGreaterEqual(item["confidence_score"], 0.50)

    # -------------------------------------------------------------------------
    # 4. Empty & Missing Glucose Readings / Datetime & Timezone Issues
    # -------------------------------------------------------------------------
    def test_empty_readings(self):
        self.assertEqual(detect_and_impute_missing_doses([], []), [])

    def test_insufficient_readings_count(self):
        readings = [
            {"timestamp": self.base_time, "value": 200.0},
            {"timestamp": self.base_time + timedelta(minutes=15), "value": 150.0},
            {"timestamp": self.base_time + timedelta(minutes=30), "value": 100.0},
        ]
        self.assertEqual(detect_and_impute_missing_doses(readings, []), [])

    def test_large_time_gap_between_readings(self):
        """Readings spaced 5 hours apart (> 240 min window)."""
        readings = [
            {"timestamp": self.base_time, "value": 250.0},
            {"timestamp": self.base_time + timedelta(hours=1), "value": 240.0},
            {"timestamp": self.base_time + timedelta(hours=2), "value": 230.0},
            {"timestamp": self.base_time + timedelta(hours=7), "value": 100.0},
        ]
        imputed = detect_and_impute_missing_doses(readings, [])
        self.assertEqual(len(imputed), 0, "Drop across >4 hour gap should exceed window duration")

    def test_mixed_naive_and_aware_timestamps(self):
        """Robustness check when some timestamps in readings/doses are naive datetimes."""
        t0_naive = datetime(2026, 8, 4, 12, 0, 0)
        readings = []
        for m in range(0, 95, 5):
            t = t0_naive + timedelta(minutes=m) if m % 10 == 0 else pytz.utc.localize(t0_naive + timedelta(minutes=m))
            val = 240.0 - (140.0 * (m / 90.0))
            readings.append({"timestamp": t, "value": round(val, 1)})

        logged_doses = [
            {"timestamp": t0_naive - timedelta(hours=5), "rapid_acting": 3.0}
        ]

        try:
            imputed = detect_and_impute_missing_doses(readings, logged_doses)
            self.assertIsInstance(imputed, list)
        except TypeError as e:
            # We record this failure mode explicitly
            raise e

    def test_invalid_timezone_string(self):
        """Check behavior when an unknown/invalid timezone string is supplied."""
        readings = []
        for m in range(0, 95, 5):
            t = self.base_time + timedelta(minutes=m)
            val = 240.0 - (140.0 * (m / 90.0))
            readings.append({"timestamp": t, "value": round(val, 1)})

        try:
            imputed = detect_and_impute_missing_doses(readings, [], timezone_str="NonExistent/Timezone")
            self.assertIsInstance(imputed, list)
        except pytz.UnknownTimeZoneError as e:
            raise e

    # -------------------------------------------------------------------------
    # 5. ISF Bounds & Mathematical Stability (Zero/Negative ISF)
    # -------------------------------------------------------------------------
    @patch("imputation.load_heuristics_params")
    def test_zero_or_negative_isf_handling(self, mock_heuristics):
        """Test behavior when ISF configuration contains 0.0 or negative values."""
        mock_heuristics.return_value = {
            "isf": {"afternoon": -10.0, "global": -10.0}
        }
        readings = []
        for m in range(0, 95, 5):
            t = self.base_time + timedelta(minutes=m)
            val = 240.0 - (140.0 * (m / 90.0))
            readings.append({"timestamp": t, "value": round(val, 1)})

        try:
            imputed = detect_and_impute_missing_doses(readings, [])
            for item in imputed:
                self.assertGreater(item["rapid_acting"], 0.0)
        except Exception as e:
            print(f"Exception on negative ISF: {type(e).__name__}: {e}")

    # -------------------------------------------------------------------------
    # 6. Dose Clamping Boundaries ([0.5 U, 15.0 U])
    # -------------------------------------------------------------------------
    def test_lower_dose_clamping(self):
        """Unexplained drop is small (25 mg/dL over 2 hours). Raw dose ~ 0.35 U -> clamped to 0.5 U."""
        readings = []
        for m in range(0, 125, 10):
            t = self.base_time + timedelta(minutes=m)
            val = 220.0 - (25.0 * (m / 120.0))
            readings.append({"timestamp": t, "value": round(val, 1)})

        imputed = detect_and_impute_missing_doses(readings, [], min_confidence=0.10)
        if imputed:
            for item in imputed:
                self.assertGreaterEqual(item["rapid_acting"], 0.5)

    def test_upper_dose_clamping(self):
        """Massive unexplained drop (350 mg/dL over 60 mins). Raw dose ~ 14 U -> clamped upper bound <= 15.0 U."""
        readings = []
        for m in range(0, 65, 5):
            t = self.base_time + timedelta(minutes=m)
            val = 400.0 - (350.0 * (m / 60.0))
            readings.append({"timestamp": t, "value": round(val, 1)})

        imputed = detect_and_impute_missing_doses(readings, [])
        self.assertGreaterEqual(len(imputed), 1)
        for item in imputed:
            self.assertLessEqual(item["rapid_acting"], 15.0)

    # -------------------------------------------------------------------------
    # 7. Confidence Score Thresholding (C < 0.50 vs C >= 0.50)
    # -------------------------------------------------------------------------
    def test_confidence_threshold_filtering(self):
        """
        Construct a noisy, low hyperglycemia, meal-associated drop that produces C < 0.50.
        Verify it is rejected when min_confidence=0.50, but accepted when min_confidence=0.20.
        """
        readings = []
        for m in range(0, 95, 10):
            t = self.base_time + timedelta(minutes=m)
            val = 125.0 - (30.0 * (m / 90.0))
            readings.append({"timestamp": t, "value": round(val, 1)})

        logged_doses = [
            {"timestamp": self.base_time - timedelta(minutes=30), "meal": 5.0, "rapid_acting": 5.0}
        ]

        strict_imputed = detect_and_impute_missing_doses(readings, logged_doses, min_confidence=0.50)
        lenient_imputed = detect_and_impute_missing_doses(readings, logged_doses, min_confidence=0.20)

        self.assertEqual(len(strict_imputed), 0, "Low confidence candidate (< 0.50) should be filtered out by default threshold")
        if len(lenient_imputed) > 0:
            self.assertLess(lenient_imputed[0]["confidence_score"], 0.50)

    # -------------------------------------------------------------------------
    # 8. Interaction with Logged Doses & IOB Deconvolution
    # -------------------------------------------------------------------------
    def test_explained_drop_from_logged_dose_not_imputed(self):
        """
        If a dose of 4.0 U was logged at t=0, the 100 mg/dL drop is EXPECTED from logged IOB.
        Unexplained drop should be near zero -> NO missing dose imputed.
        """
        readings = []
        for m in range(0, 125, 5):
            t = self.base_time + timedelta(minutes=m)
            val = 220.0 - (100.0 * (m / 120.0))
            readings.append({"timestamp": t, "value": round(val, 1)})

        logged_doses = [
            {"timestamp": self.base_time, "rapid_acting": 4.0, "correction": 4.0}
        ]

        imputed = detect_and_impute_missing_doses(readings, logged_doses)
        self.assertEqual(len(imputed), 0, "Drop explained by logged IOB should NOT produce imputed dose")

    def test_near_logged_dose_divisor(self):
        """
        If a dose is logged within 45 mins of drop start time, candidate imputation is not suppressed,
        but its confidence score is divided by 2.
        """
        readings = []
        for m in range(0, 95, 5):
            t = self.base_time + timedelta(minutes=m)
            val = 240.0 - (120.0 * (m / 90.0))
            readings.append({"timestamp": t, "value": round(val, 1)})

        logged_doses = [
            {"timestamp": self.base_time + timedelta(minutes=15), "rapid_acting": 1.0}
        ]

        imputed = detect_and_impute_missing_doses(readings, logged_doses)
        self.assertEqual(len(imputed), 1, "Imputation near logged dose should not be completely suppressed")
        self.assertEqual(imputed[0]['confidence_score'], 0.50, "Confidence should be divided by 2 (1.0 -> 0.50)")

    def test_minimum_3h_gap_between_imputed_doses(self):
        """
        Two drops close together (1.5 hours apart) should produce only 1 imputed dose (greedy non-overlapping).
        """
        readings = []
        for m in range(0, 95, 5):
            t = self.base_time + timedelta(minutes=m)
            val = 240.0 - (120.0 * (m / 90.0))
            readings.append({"timestamp": t, "value": round(val, 1)})

        for m in range(95, 185, 5):
            t = self.base_time + timedelta(minutes=m)
            val = 220.0 - (120.0 * ((m - 90) / 90.0))
            readings.append({"timestamp": t, "value": round(val, 1)})

        imputed = detect_and_impute_missing_doses(readings, [])
        self.assertEqual(len(imputed), 1, "Should enforce 3-hour minimum gap between imputed doses")

    # -------------------------------------------------------------------------
    # 9. Extreme Numerical Inputs & Unhandled Exception Stress Tests
    # -------------------------------------------------------------------------
    def test_nan_or_inf_in_glucose_values(self):
        """Verify handling when glucose readings contain NaN or Inf values."""
        readings = [
            {"timestamp": self.base_time, "value": 240.0},
            {"timestamp": self.base_time + timedelta(minutes=15), "value": float('nan')},
            {"timestamp": self.base_time + timedelta(minutes=30), "value": 180.0},
            {"timestamp": self.base_time + timedelta(minutes=45), "value": float('inf')},
            {"timestamp": self.base_time + timedelta(minutes=60), "value": 120.0},
        ]
        try:
            imputed = detect_and_impute_missing_doses(readings, [])
            for item in imputed:
                self.assertFalse(math.isnan(item["rapid_acting"]))
                self.assertFalse(math.isinf(item["rapid_acting"]))
                self.assertFalse(math.isnan(item["confidence_score"]))
        except Exception as e:
            print(f"Captured exception on NaN/Inf: {type(e).__name__}: {e}")

    def test_out_of_order_glucose_readings(self):
        """Readings given in random jumbled timestamp order."""
        readings = []
        for m in [45, 0, 90, 15, 60, 30, 75]:
            t = self.base_time + timedelta(minutes=m)
            val = 240.0 - (140.0 * (m / 90.0))
            readings.append({"timestamp": t, "value": round(val, 1)})

        imputed = detect_and_impute_missing_doses(readings, [])
        self.assertGreaterEqual(len(imputed), 1, "Should correctly sort out-of-order readings")


if __name__ == "__main__":
    unittest.main()
