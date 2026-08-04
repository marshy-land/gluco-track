"""
Targeted Empirical Bug Reproduction Script for ml_heuristics.py
Testing calculate_personalized_isf, train_predictive_model, and predict_adaptive_glucose
"""
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from ml_heuristics import (
    calculate_personalized_isf,
    train_predictive_model,
    predict_adaptive_glucose
)

class TestMLHeuristicsBugs(unittest.TestCase):

    def test_calculate_personalized_isf_string_numbers(self):
        """
        Verify if calculate_personalized_isf crashes with TypeError when doses or readings
        contain string formatted numbers (e.g. from CSV or external inputs).
        """
        now = datetime.now(timezone.utc)
        mock_doses = [
            {
                "timestamp": now - timedelta(hours=5),
                "rapid_acting": "3.5",  # string number
                "meal": "0.0",
                "correction": "0.0",
                "user_change": "0.0"
            }
        ]
        mock_readings = [
            {"timestamp": now - timedelta(hours=5), "value": "180.0"},  # string number
            {"timestamp": now - timedelta(hours=1), "value": "100.0"}   # string number
        ]

        with patch("db.get_insulin_history", return_value=mock_doses), \
             patch("db.get_history", return_value=mock_readings):
            try:
                res = calculate_personalized_isf()
                print("calculate_personalized_isf returned:", res)
            except Exception as e:
                print("BUG REPRODUCED in calculate_personalized_isf:", type(e), e)
                raise e

    def test_predict_adaptive_glucose_string_iob(self):
        """
        Verify if predict_adaptive_glucose crashes with TypeError when iob_val or reading values
        are string formatted numbers.
        """
        now = datetime.now(timezone.utc)
        mock_readings = [
            {"timestamp": now - timedelta(minutes=60), "value": "120.0"},
            {"timestamp": now - timedelta(minutes=45), "value": "125.0"},
            {"timestamp": now - timedelta(minutes=30), "value": "130.0"},
            {"timestamp": now - timedelta(minutes=15), "value": "135.0"},
            {"timestamp": now, "value": "140.0"}
        ]

        with patch("ml_heuristics.load_heuristics_params", return_value={
            "model_trained": True,
            "coefficients": [1.0, 0.5, 0.2, 0.1, 0.05, 0.0, 0.0, -2.0]
        }):
            try:
                res = predict_adaptive_glucose(mock_readings, iob_val="1.5")
                print("predict_adaptive_glucose returned:", res)
            except Exception as e:
                print("BUG REPRODUCED in predict_adaptive_glucose:", type(e), e)
                raise e

if __name__ == "__main__":
    unittest.main()
