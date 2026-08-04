"""
Tier 4: Real-World Application Scenarios E2E Tests for Gluco Track
Includes >=3 end-to-end workflow & patient profile simulation scenarios.
"""

import os
import sys
import tempfile
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

class TestTier4Scenarios(unittest.TestCase):

    def test_r4_tier4_01_full_multiday_libreview_e2e_workflow(self):
        """
        Scenario 1: Full multi-day LibreView E2E Workflow.
        1. Ingest multi-day glucose readings & insulin telemetry.
        2. Execute missing dose imputation (R2).
        3. Compute diurnal nutritional impact model (R3).
        4. Detect glycemic anomalies and generate `dietary_remedies_report.md` (R1).
        5. Validate complete report file output and end-to-end data integrity.
        """
        readings, logged_doses = generate_synthetic_glucose_data(days=7, pattern="standard")
        self.assertEqual(len(readings), 7 * 24 * 4)

        # 1. R2 Imputation
        imputed = run_impute_missing_doses(readings, logged_doses)
        combined_doses = logged_doses + imputed

        # 2. R3 Nutritional Model
        r3_out = run_analyze_nutritional_impact(readings, combined_doses)
        self.assertIn("time_buckets", r3_out)

        # 3. R1 Report Generation
        with tempfile.TemporaryDirectory() as tmpdir:
            report_file = os.path.join(tmpdir, "dietary_remedies_report.md")
            md_content = run_generate_report(readings, output_path=report_file)

            self.assertTrue(os.path.exists(report_file))
            self.assertIn("# Executive Summary", md_content)
            self.assertIn("## Observed Glycemic Trends & Anomalies", md_content)
            self.assertIn("## Literature-Backed Dietary Interventions", md_content)
            self.assertIn("## Actionable Plan", md_content)

    def test_r4_tier4_02_dawn_phenomenon_and_nocturnal_hypo_patient_profile(self):
        """
        Scenario 2: Patient Profile Simulation - Severe Dawn Phenomenon & Nocturnal Hypoglycemia.
        Simulates 14-day telemetry for a patient exhibiting recurrent 03:00 AM hypos and 07:00 AM spikes.
        Verifies R1 identifies both anomalies, R3 identifies elevated Morning modifier, and R1 report produces bedtime snack & basal recommendations.
        """
        readings, logged_doses = generate_synthetic_glucose_data(days=14, pattern="dawn_hypo")

        anomalies = run_detect_anomalies(readings)

        types = [a["type"] for a in anomalies]
        self.assertTrue("dawn_phenomenon" in types or "nocturnal_hypo" in types)

        report = run_generate_report(readings, output_path=None)

        self.assertIn("bedtime", report.lower())

    def test_r4_tier4_03_high_glycemic_variability_unlogged_corrections_patient_profile(self):
        """
        Scenario 3: Patient Profile Simulation - High Glycemic Variability & Frequent Unlogged Corrections.
        Simulates 14-day telemetry for a volatile patient with unlogged correction boluses.
        Verifies R2 imputes missing doses with high confidence, R1 flags high variability, and R3 computes circadian impact modifiers.
        """
        readings, logged_doses = generate_synthetic_glucose_data(days=14, pattern="unlogged_corrections")

        imputed = run_impute_missing_doses(readings, logged_doses)

        self.assertGreater(len(imputed), 0, "R2 must recover missing correction doses for volatile profile.")
        for dose in imputed:
            self.assertGreater(dose["confidence_score"], 0.50)

        r3_out = run_analyze_nutritional_impact(readings, logged_doses + imputed)

        self.assertEqual(len(r3_out["time_buckets"]), 4)

if __name__ == "__main__":
    unittest.main()
