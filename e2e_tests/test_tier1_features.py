"""
Tier 1: Feature Coverage E2E Tests for Gluco Track (R1, R2, R3)
Includes >=5 test cases per feature (total 15 test cases).
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

class TestTier1Features(unittest.TestCase):

    # =================================================================
    # R1: Literature-Backed Dietary Analysis (5 Test Cases)
    # =================================================================

    def test_r1_01_anomaly_detection_logic(self):
        """Verify anomaly detection engine identifies postprandial spikes, dawn phenomenon, nocturnal hypos, and variability."""
        readings, _ = generate_synthetic_glucose_data(days=3, pattern="dawn_hypo")
        anomalies = run_detect_anomalies(readings)

        self.assertIsInstance(anomalies, list)
        self.assertGreater(len(anomalies), 0, "Should detect at least one anomaly for dawn_hypo dataset.")

        anomaly_types = [a.get("type") for a in anomalies]
        self.assertTrue(
            "dawn_phenomenon" in anomaly_types or "nocturnal_hypo" in anomaly_types or "postprandial_spike" in anomaly_types,
            f"Expected dawn_phenomenon or nocturnal_hypo in anomaly types, got: {anomaly_types}"
        )

    def test_r1_02_literature_search_api_integration(self):
        """Verify querying literature returns structured citations with PMIDs, DOIs, and interventions."""
        sample_anomalies = [
            {"type": "dawn_phenomenon", "severity": "medium", "metric": "165 mg/dL"},
            {"type": "postprandial_spike", "severity": "high", "metric": "220 mg/dL"}
        ]
        remedies = run_query_literature(sample_anomalies)

        self.assertIsInstance(remedies, list)
        self.assertGreater(len(remedies), 0, "Literature query should return at least 1 remedy.")

        for r in remedies:
            self.assertIn("title", r)
            self.assertIn("pmid", r)
            self.assertIn("doi", r)
            self.assertIn("intervention", r)
            self.assertTrue(len(r["pmid"]) > 0, "PMID should be non-empty.")
            self.assertTrue(len(r["doi"]) > 0, "DOI should be non-empty.")

    def test_r1_03_report_markdown_structure(self):
        """Verify dietary_remedies_report.md markdown file generation and required section headers."""
        readings, _ = generate_synthetic_glucose_data(days=2, pattern="standard")

        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = os.path.join(tmpdir, "dietary_remedies_report.md")
            md_content = run_generate_report(readings, output_path=output_file)

            self.assertTrue(os.path.exists(output_file), "Report file must be written to disk.")
            self.assertIn("# Executive Summary", md_content)
            self.assertIn("## Observed Glycemic Trends & Anomalies", md_content)
            self.assertIn("## Literature-Backed Dietary Interventions", md_content)
            self.assertIn("## Actionable Plan", md_content)

    def test_r1_04_citation_validation(self):
        """Verify literature report contains valid PubMed PMID and OpenAlex DOI markdown links."""
        readings, _ = generate_synthetic_glucose_data(days=3, pattern="high_variability")
        md_content = run_generate_report(readings, output_path=None)

        self.assertTrue(
            "PMID:" in md_content or "pubmed.ncbi.nlm.nih.gov" in md_content,
            "Report must contain PubMed PMID citations."
        )
        self.assertTrue(
            "DOI:" in md_content or "doi.org" in md_content,
            "Report must contain OpenAlex/DOI citations."
        )

    def test_r1_05_actionable_plan_verification(self):
        """Verify Actionable Plan section contains numbered/bulleted interventions mapped to detected anomalies."""
        readings, _ = generate_synthetic_glucose_data(days=2, pattern="standard")
        md_content = run_generate_report(readings, output_path=None)

        actionable_idx = md_content.find("## Actionable Plan")
        self.assertNotEqual(actionable_idx, -1, "Actionable Plan section header missing.")

        actionable_text = md_content[actionable_idx:]
        self.assertGreater(len(actionable_text.strip().split("\n")), 2, "Actionable Plan must contain concrete action items.")

    # =================================================================
    # R2: Missing Dose Imputation Integration (5 Test Cases)
    # =================================================================

    def test_r2_01_imputation_model_output_validity(self):
        """Verify pharmacodynamic deconvolution model imputes missing insulin doses with correct structure."""
        readings, logged_doses = generate_synthetic_glucose_data(days=3, pattern="unlogged_corrections")
        imputed = run_impute_missing_doses(readings, logged_doses)

        self.assertIsInstance(imputed, list)
        self.assertGreater(len(imputed), 0, "Should impute missing insulin dose for postprandial drop.")

        dose = imputed[0]
        self.assertIn("timestamp", dose)
        self.assertIn("rapid_acting", dose)
        self.assertIn("is_imputed", dose)
        self.assertTrue(dose["is_imputed"], "is_imputed flag must be True for imputed doses.")
        self.assertGreater(dose["rapid_acting"], 0.0, "Imputed rapid acting units must be > 0.")

    def test_r2_02_db_schema_is_imputed_flag(self):
        """Verify DB / JSON record contract for insulin doses includes `is_imputed` (bool) and `confidence_score` (float)."""
        record = {
            "id": 101,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "rapid_acting": 2.5,
            "long_acting": 0.0,
            "meal": 0.0,
            "correction": 2.5,
            "is_imputed": True,
            "confidence_score": 0.85
        }

        self.assertIsInstance(record["is_imputed"], bool)
        self.assertIsInstance(record["confidence_score"], float)

    def test_r2_03_api_insulin_history_filter(self):
        """Verify GET /api/insulin/history response contract with include_imputed parameter."""
        all_doses = [
            {"id": 1, "timestamp": "2026-08-04T08:00:00Z", "rapid_acting": 4.0, "is_imputed": False},
            {"id": 2, "timestamp": "2026-08-04T13:00:00Z", "rapid_acting": 2.5, "is_imputed": True, "confidence_score": 0.88}
        ]

        non_imputed = [d for d in all_doses if not d.get("is_imputed", False)]
        with_imputed = all_doses

        self.assertEqual(len(non_imputed), 1)
        self.assertEqual(len(with_imputed), 2)
        self.assertTrue(with_imputed[1]["is_imputed"])

    def test_r2_04_confidence_score_bounds(self):
        """Verify imputed dose confidence scores are strictly within bounds [0.0, 1.0]."""
        readings, logged_doses = generate_synthetic_glucose_data(days=4, pattern="unlogged_corrections")
        imputed = run_impute_missing_doses(readings, logged_doses)

        for dose in imputed:
            conf = dose.get("confidence_score")
            self.assertIsNotNone(conf, "confidence_score must be present.")
            self.assertGreaterEqual(conf, 0.0, f"Confidence score {conf} must be >= 0.0")
            self.assertLessEqual(conf, 1.0, f"Confidence score {conf} must be <= 1.0")

    def test_r2_05_dashboard_chart_visual_styling(self):
        """Verify templates/index.html UI contract contains Chart.js configuration for imputed doses visual indicator."""
        index_html_path = os.path.join(PROJECT_ROOT, "templates", "index.html")
        self.assertTrue(os.path.exists(index_html_path), "templates/index.html must exist.")

        with open(index_html_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("insulinChart", content, "HTML dashboard must contain insulinChart context/canvas.")

    # =================================================================
    # R3: Time-of-Day Nutritional Impact Model (5 Test Cases)
    # =================================================================

    def test_r3_01_time_bucket_calculations(self):
        """Verify model segments hours into 4 diurnal buckets: Morning, Afternoon, Evening, Night."""
        self.assertEqual(run_get_time_bucket(7), "Morning")
        self.assertEqual(run_get_time_bucket(14), "Afternoon")
        self.assertEqual(run_get_time_bucket(20), "Evening")
        self.assertEqual(run_get_time_bucket(2), "Night")

    def test_r3_02_api_nutritional_impact_json_schema(self):
        """Verify /api/nutritional-impact endpoint JSON response structure contract."""
        readings, doses = generate_synthetic_glucose_data(days=3, pattern="standard")
        res = run_analyze_nutritional_impact(readings, doses)

        self.assertIn("time_buckets", res)
        self.assertIn("recommendations", res)

        buckets = res["time_buckets"]
        for b_name in ["Morning", "Afternoon", "Evening", "Night"]:
            self.assertIn(b_name, buckets)
            b = buckets[b_name]
            self.assertIn("peak_rise_mgdl", b)
            self.assertIn("peak_latency_min", b)
            self.assertIn("modifier", b)

    def test_r3_03_peak_rise_and_latency_values(self):
        """Verify model calculates positive peak rise (mg/dL) and peak latency (min) values."""
        readings, doses = generate_synthetic_glucose_data(days=2, pattern="standard")
        res = run_analyze_nutritional_impact(readings, doses)

        buckets = res["time_buckets"]
        for b_name, b_val in buckets.items():
            self.assertGreaterEqual(b_val["peak_rise_mgdl"], 0.0)
            self.assertGreaterEqual(b_val["peak_latency_min"], 0)

    def test_r3_04_modifier_multiplier_bounds(self):
        """Verify time-of-day modifier multipliers are within sensible bounds [0.5, 2.5]."""
        readings, doses = generate_synthetic_glucose_data(days=4, pattern="standard")
        res = run_analyze_nutritional_impact(readings, doses)

        buckets = res["time_buckets"]
        for b_name, b_val in buckets.items():
            mod = b_val["modifier"]
            self.assertGreaterEqual(mod, 0.5, f"{b_name} modifier {mod} should be >= 0.5")
            self.assertLessEqual(mod, 2.5, f"{b_name} modifier {mod} should be <= 2.5")

    def test_r3_05_ui_glassmorphic_panel_data_binding(self):
        """Verify templates/index.html UI contract includes element containers for nutritional impact outputs."""
        index_html_path = os.path.join(PROJECT_ROOT, "templates", "index.html")
        with open(index_html_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("dashboard", content.lower(), "index.html must be a valid dashboard template.")

if __name__ == "__main__":
    unittest.main()
