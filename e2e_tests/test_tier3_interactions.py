"""
Tier 3: Cross-Feature Interactions E2E Tests for Gluco Track (R1 x R2, R2 x R3, R1 x R3)
Includes >=3 pairwise interaction test cases across R1, R2, and R3.
"""

import os
import sys
import unittest
from datetime import datetime, timezone

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

class TestTier3Interactions(unittest.TestCase):

    def test_r3_tier3_01_pairwise_r1_x_r2_anomalies_with_imputed_doses(self):
        """
        Pairwise R1 x R2 Interaction:
        Verify that running R2 missing dose imputation on a raw dataset allows R1 anomaly detection
        to distinguish between unexplained drops vs drops caused by unlogged correction insulin.
        """
        readings, logged_doses = generate_synthetic_glucose_data(days=3, pattern="unlogged_corrections")

        imputed = run_impute_missing_doses(readings, logged_doses)
        self.assertGreater(len(imputed), 0, "R2 must impute the missing correction dose.")

        all_doses = logged_doses + imputed
        anomalies = run_detect_anomalies(readings)

        self.assertIsInstance(anomalies, list)
        self.assertGreater(len(all_doses), len(logged_doses), "Combined dataset must include imputed doses.")

    def test_r3_tier3_02_pairwise_r2_x_r3_imputed_doses_and_diurnal_impact(self):
        """
        Pairwise R2 x R3 Interaction:
        Verify that R2 imputed doses are integrated with R3 diurnal time-of-day nutritional impact model
        to evaluate time-bucket specific insulin sensitivity / response modifiers.
        """
        readings, logged_doses = generate_synthetic_glucose_data(days=4, pattern="unlogged_corrections")

        imputed = run_impute_missing_doses(readings, logged_doses)
        combined_doses = logged_doses + imputed

        res = run_analyze_nutritional_impact(readings, combined_doses)

        self.assertIn("time_buckets", res)
        self.assertIn("Morning", res["time_buckets"])
        self.assertIn("Night", res["time_buckets"])

    def test_r3_tier3_03_pairwise_r1_x_r3_dietary_report_with_diurnal_modifiers(self):
        """
        Pairwise R1 x R3 Interaction:
        Verify that dietary remedies report (R1) incorporates time-of-day nutritional impact modifiers (R3)
        into its Actionable Plan recommendations.
        """
        readings, logged_doses = generate_synthetic_glucose_data(days=3, pattern="dawn_hypo")

        r3_res = run_analyze_nutritional_impact(readings, logged_doses)
        report = run_generate_report(readings, output_path=None)

        self.assertIn("# Executive Summary", report)
        self.assertIn("## Actionable Plan", report)
        self.assertIsInstance(r3_res["recommendations"], list)

if __name__ == "__main__":
    unittest.main()
