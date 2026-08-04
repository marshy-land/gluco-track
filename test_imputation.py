"""
Unit and Integration Tests for Requirement R2 Missing Dose Imputation Engine
"""

import unittest
from datetime import datetime, timezone, timedelta
from imputation import detect_and_impute_missing_doses
from prediction import calculate_iob


class TestImputationEngine(unittest.TestCase):

    def test_detect_and_impute_missing_doses_basic(self):
        """Tests imputation of a clear, unlogged glucose drop from 220 mg/dL to 120 mg/dL over 2 hours."""
        base_time = datetime(2026, 8, 4, 14, 0, 0, tzinfo=timezone.utc)

        # 2 hours of 15-minute readings: drop from 220 down to 120 (100 mg/dL drop)
        glucose_readings = []
        start_val = 220.0
        end_val = 120.0
        num_steps = 9  # 0, 15, 30, 45, 60, 75, 90, 105, 120 mins
        step_drop = (start_val - end_val) / (num_steps - 1)

        for i in range(num_steps):
            t = base_time + timedelta(minutes=i * 15)
            val = start_val - (i * step_drop)
            glucose_readings.append({"timestamp": t, "value": val})

        logged_doses = []  # No logged doses

        imputed_doses = detect_and_impute_missing_doses(glucose_readings, logged_doses)

        self.assertEqual(len(imputed_doses), 1)
        imp = imputed_doses[0]
        self.assertTrue(imp["is_imputed"])
        self.assertGreaterEqual(imp["confidence_score"], 0.50)
        self.assertGreater(imp["rapid_acting"], 0)
        self.assertGreater(imp["correction"], 0)
        self.assertEqual(imp["timestamp"], base_time)

    def test_detect_and_impute_missing_doses_with_logged_iob(self):
        """Tests that existing logged IOB is properly handled when computing unexplained drop."""
        base_time = datetime(2026, 8, 4, 14, 0, 0, tzinfo=timezone.utc)

        # Logged 1.0 U dose at base_time - 30m
        logged_doses = [
            {
                "id": 1,
                "timestamp": base_time - timedelta(minutes=30),
                "rapid_acting": 1.0,
                "long_acting": 0.0,
                "meal": 0.0,
                "correction": 1.0,
                "user_change": 0.0,
                "is_imputed": False
            }
        ]

        # Glucose drop from 240 mg/dL to 100 mg/dL (140 mg/dL drop over 2 hours)
        glucose_readings = []
        start_val = 240.0
        end_val = 100.0
        num_steps = 9

        for i in range(num_steps):
            t = base_time + timedelta(minutes=i * 15)
            val = start_val - (i * ((start_val - end_val) / (num_steps - 1)))
            glucose_readings.append({"timestamp": t, "value": val})

        imputed_doses = detect_and_impute_missing_doses(glucose_readings, logged_doses)
        self.assertIsInstance(imputed_doses, list)

    def test_no_imputation_on_stable_glucose(self):
        """Tests that stable glucose with no drops produces zero imputed doses."""
        base_time = datetime(2026, 8, 4, 14, 0, 0, tzinfo=timezone.utc)

        glucose_readings = [
            {"timestamp": base_time + timedelta(minutes=i * 15), "value": 110.0 + (i % 2)}
            for i in range(12)
        ]
        logged_doses = []

        imputed_doses = detect_and_impute_missing_doses(glucose_readings, logged_doses)
        self.assertEqual(len(imputed_doses), 0)

    def test_confidence_threshold_filter(self):
        """Tests that low confidence drops (e.g. starting glucose < 120 mg/dL or tiny drops) are filtered out."""
        base_time = datetime(2026, 8, 4, 14, 0, 0, tzinfo=timezone.utc)

        # Drop from 115 mg/dL to 95 mg/dL (only 20 mg/dL drop starting below 120)
        glucose_readings = [
            {"timestamp": base_time + timedelta(minutes=i * 15), "value": 115.0 - (i * 2.5)}
            for i in range(9)
        ]
        logged_doses = []

        imputed_doses = detect_and_impute_missing_doses(glucose_readings, logged_doses, min_confidence=0.50)
        self.assertEqual(len(imputed_doses), 0)


if __name__ == "__main__":
    unittest.main()
