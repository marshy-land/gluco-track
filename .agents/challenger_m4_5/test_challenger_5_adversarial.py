"""
Adversarial Test Suite for Challenger 5 (M4 Phase 2 Tier 5 Final Adversarial Re-verification)
Targeting R1 (dietary_analysis.py, literature_api.py) and R2 (imputation.py, prediction.py).
"""

import sys
import os
import math
import pytest
from datetime import datetime, timezone, timedelta

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import dietary_analysis
import literature_api
import prediction
import imputation


# ==============================================================================
# R1 Tests: dietary_analysis.py & literature_api.py
# ==============================================================================

class TestR1DietaryAnalysisAdversarial:
    """Adversarial edge-case tests for R1 dietary analysis and literature API."""

    def test_calculate_glycemic_stats_malformed_inputs(self):
        """Test calculate_glycemic_stats with non-dict items, string numbers, NaNs, and nulls."""
        readings = [
            None,
            "not_a_dict",
            12345,
            [],
            {"value": None, "timestamp": "2026-08-04T00:00:00Z"},
            {"value": "invalid_num", "timestamp": "2026-08-04T00:00:00Z"},
            {"value": "nan", "timestamp": "2026-08-04T00:00:00Z"},
            {"value": "inf", "timestamp": "2026-08-04T00:00:00Z"},
            {"value": "150.5", "timestamp": "2026-08-04T01:00:00Z"},
            {"value": 90, "timestamp": "2026-08-04T02:00:00Z"},
            {"value": "210.0", "timestamp": "2026-08-04T03:00:00Z"},
            {"value": 55.0, "timestamp": "2026-08-04T04:00:00Z"},
        ]
        stats = dietary_analysis.calculate_glycemic_stats(readings)
        assert stats.total_readings == 4
        assert stats.mean_glucose > 0
        assert stats.gmi > 0
        assert stats.cv_percent >= 0

    def test_detect_postprandial_spikes_edge_cases(self):
        """Test postprandial spike detection with mixed malformed items and boundary values."""
        readings = [
            {"value": "120.0", "timestamp": "2026-08-04T12:00:00Z"},
            {"value": "195.0", "timestamp": "2026-08-04T12:15:00Z"},
            {"value": "220.5", "timestamp": "2026-08-04T12:30:00Z"},
            {"value": "150.0", "timestamp": "2026-08-04T13:00:00Z"},
            None,
            "corrupted_entry",
            {"value": None, "timestamp": None},
        ]
        spikes = dietary_analysis.detect_postprandial_spikes(readings)
        assert isinstance(spikes, list)
        assert len(spikes) == 1
        assert spikes[0].peak_value == 220.5

    def test_detect_nocturnal_hypos_edge_cases(self):
        """Test nocturnal hypos detection with local time conversion and malformed inputs."""
        readings = [
            {"value": "65.0", "timestamp": "2026-08-04T03:00:00Z"},
            {"value": "50.0", "timestamp": "2026-08-04T03:15:00Z"},
            {"value": "110.0", "timestamp": "2026-08-04T08:00:00Z"},
        ]
        hypos = dietary_analysis.detect_nocturnal_hypos(readings)
        assert isinstance(hypos, list)

    def test_detect_dawn_phenomenon_edge_cases(self):
        """Test dawn phenomenon detection with Somogyi exclusion logic."""
        readings = [
            {"value": "110.0", "timestamp": "2026-08-04T03:00:00Z"},
            {"value": "160.0", "timestamp": "2026-08-04T06:00:00Z"},
            {"value": "170.0", "timestamp": "2026-08-04T07:00:00Z"},
        ]
        dawn = dietary_analysis.detect_dawn_phenomenon(readings)
        assert isinstance(dawn, list)

    def test_generate_report_end_to_end(self):
        """Test generate_report with empty list and output_path=None."""
        report = dietary_analysis.generate_report(readings=[], output_path=None, use_network=False)
        assert isinstance(report, str)
        assert "# Executive Summary" in report
        assert "Literature-Backed Dietary Remedies" in report

    def test_literature_api_fallback_hierarchy(self):
        """Test Tier 4 landmark literature fallback when use_network=False."""
        citations = literature_api.fetch_literature_for_anomaly("postprandial_spike", use_network=False)
        assert isinstance(citations, list)
        assert len(citations) > 0
        assert citations[0].pmid is not None
        assert citations[0].pubmed_url is not None


# ==============================================================================
# R2 Tests: prediction.py & imputation.py
# ==============================================================================

class TestR2PredictionAdversarial:
    """Adversarial edge-case tests for R2 prediction module."""

    def test_calculate_iob_malformed_inputs(self):
        """Test calculate_iob with string numbers, non-dict elements, NaNs, and future timestamps."""
        now = datetime.now(timezone.utc)
        doses = [
            None,
            "invalid_dose",
            123,
            {"timestamp": (now - timedelta(minutes=30)).isoformat(), "rapid_acting": "3.5", "meal": "0.0", "correction": "1.0"},
            {"timestamp": (now - timedelta(minutes=60)).isoformat(), "rapid_acting": None, "meal": "nan", "correction": "2.0"},
            {"timestamp": (now + timedelta(minutes=10)).isoformat(), "rapid_acting": "1.0"},
        ]
        iob = prediction.calculate_iob(doses, current_time=now)
        assert isinstance(iob, float)
        assert iob >= 0.0

    def test_predict_glucose_malformed_inputs(self):
        """Test predict_glucose with string values, non-dict elements, and short input arrays."""
        now = datetime.now(timezone.utc)
        readings = [
            None,
            "text",
            {"timestamp": (now - timedelta(minutes=15)).isoformat(), "value": "180.5"},
            {"timestamp": now.isoformat(), "value": "165.0"},
        ]
        preds = prediction.predict_glucose(readings)
        assert isinstance(preds, list)
        if preds:
            assert "minutes" in preds[0]
            assert "value" in preds[0]

    def test_suggest_correction_edge_cases(self):
        """Test suggest_correction with string numbers, zero/negative ISF, and NaNs."""
        corr1 = prediction.suggest_correction("200.0", "1.5", target_glucose="120", isf="50")
        assert corr1 == 0.1  # (200 - 120)/50 - 1.5 = 1.6 - 1.5 = 0.1

        corr2 = prediction.suggest_correction("100.0", "0.0")
        assert corr2 == 0.0

        corr3 = prediction.suggest_correction("nan", "1.0")
        assert corr3 == 0.0

        corr4 = prediction.suggest_correction(200, 0, isf=0)
        assert corr4 > 0.0  # falls back to default ISF=50 -> (200-120)/50 = 1.6


class TestR2ImputationAdversarial:
    """Adversarial edge-case tests for R2 imputation module."""

    def test_imputation_non_dict_and_string_values(self):
        """Test detect_and_impute_missing_doses with valid string numbers and non-dict elements."""
        base_time = datetime.now(timezone.utc) - timedelta(hours=3)
        readings = [
            None,
            "corrupted_reading",
            12345,
            {"timestamp": (base_time).isoformat(), "value": "220.0"},
            {"timestamp": (base_time + timedelta(minutes=15)).isoformat(), "value": "200.0"},
            {"timestamp": (base_time + timedelta(minutes=30)).isoformat(), "value": "170.0"},
            {"timestamp": (base_time + timedelta(minutes=45)).isoformat(), "value": "140.0"},
            {"timestamp": (base_time + timedelta(minutes=60)).isoformat(), "value": "110.0"},
            {"timestamp": (base_time + timedelta(minutes=90)).isoformat(), "value": "95.0"},
        ]
        logged_doses = []
        imputed = imputation.detect_and_impute_missing_doses(readings, logged_doses)
        assert isinstance(imputed, list)

    def test_imputation_gap_integer_timestamps(self):
        """Test detect_and_impute_missing_doses when readings contain integer timestamps."""
        readings = [
            {"timestamp": 1700000000, "value": 220.0},
            {"timestamp": 1700000900, "value": 180.0},
            {"timestamp": 1700001800, "value": 140.0},
            {"timestamp": 1700002700, "value": 100.0},
        ]
        # Must execute without raising uncaught AttributeError/TypeError
        try:
            imputed = imputation.detect_and_impute_missing_doses(readings, [])
            assert isinstance(imputed, list)
        except Exception as exc:
            pytest.fail(f"imputation crashed on integer timestamps: {type(exc).__name__}: {exc}")

    def test_imputation_gap_string_meal_doses(self):
        """Test detect_and_impute_missing_doses when logged_doses contains string meal values."""
        base_time = datetime.now(timezone.utc) - timedelta(hours=3)
        readings = [
            {"timestamp": (base_time).isoformat(), "value": 220.0},
            {"timestamp": (base_time + timedelta(minutes=15)).isoformat(), "value": 200.0},
            {"timestamp": (base_time + timedelta(minutes=30)).isoformat(), "value": 170.0},
            {"timestamp": (base_time + timedelta(minutes=45)).isoformat(), "value": 140.0},
            {"timestamp": (base_time + timedelta(minutes=60)).isoformat(), "value": 110.0},
        ]
        logged_doses = [
            {"timestamp": (base_time - timedelta(minutes=30)).isoformat(), "meal": "15.0"}
        ]
        # Must execute without raising uncaught TypeError: '>' not supported between instances of 'str' and 'int'
        try:
            imputed = imputation.detect_and_impute_missing_doses(readings, logged_doses)
            assert isinstance(imputed, list)
        except Exception as exc:
            pytest.fail(f"imputation crashed on string meal doses: {type(exc).__name__}: {exc}")

    def test_imputation_gap_string_min_confidence(self):
        """Test detect_and_impute_missing_doses when min_confidence is passed as a string."""
        base_time = datetime.now(timezone.utc) - timedelta(hours=3)
        readings = [
            {"timestamp": (base_time).isoformat(), "value": 220.0},
            {"timestamp": (base_time + timedelta(minutes=15)).isoformat(), "value": 200.0},
            {"timestamp": (base_time + timedelta(minutes=30)).isoformat(), "value": 170.0},
            {"timestamp": (base_time + timedelta(minutes=45)).isoformat(), "value": 140.0},
            {"timestamp": (base_time + timedelta(minutes=60)).isoformat(), "value": 110.0},
        ]
        try:
            imputed = imputation.detect_and_impute_missing_doses(readings, [], min_confidence="0.50")
            assert isinstance(imputed, list)
        except Exception as exc:
            pytest.fail(f"imputation crashed on string min_confidence: {type(exc).__name__}: {exc}")
