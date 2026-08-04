"""
Adversarial & Empirical Test Suite for Challenger 7 (M4 Phase 2 Tier 5 Final Adversarial Re-verification)
Targeting imputation.py defensive parsing remediations and contract compliance.
"""

import sys
import os
import math
import pytest
from datetime import datetime, timezone, timedelta
import pytz

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import imputation
from imputation import _safe_float, _to_utc_dt, detect_and_impute_missing_doses


class TestImputationHelperFunctions:
    """Test lower-level helper functions in imputation.py."""

    def test_safe_float_inputs(self):
        assert _safe_float(None, 0.0) == 0.0
        assert _safe_float("15.5", 0.0) == 15.5
        assert _safe_float("0.50", 0.0) == 0.50
        assert _safe_float(42, 0.0) == 42.0
        assert _safe_float(3.14, 0.0) == 3.14
        # Malformed / boundary cases
        assert _safe_float("invalid", 0.5) == 0.5
        assert _safe_float(float("nan"), 0.0) == 0.0
        assert _safe_float(float("inf"), 0.0) == 0.0
        assert _safe_float(float("-inf"), 0.0) == 0.0
        assert _safe_float("nan", 0.0) == 0.0
        assert _safe_float("inf", 0.0) == 0.0
        assert _safe_float([], 1.0) == 1.0
        assert _safe_float({}, 1.0) == 1.0

    def test_to_utc_dt_inputs(self):
        # Valid inputs
        now_utc = datetime.now(timezone.utc)
        assert _to_utc_dt(now_utc) == now_utc
        
        now_iso = "2026-08-04T07:00:00Z"
        dt_res = _to_utc_dt(now_iso)
        assert dt_res is not None
        assert dt_res.utcoffset() == timedelta(0)

        naive_dt = datetime(2026, 8, 4, 7, 0, 0)
        dt_naive_res = _to_utc_dt(naive_dt)
        assert dt_naive_res is not None
        assert dt_naive_res.tzinfo is not None

        # Invalid / non-datetime inputs (should return None safely)
        assert _to_utc_dt(None) is None
        assert _to_utc_dt(1700000000) is None
        assert _to_utc_dt(1700000000.5) is None
        assert _to_utc_dt("1700000000") is None
        assert _to_utc_dt("invalid-date-string") is None
        assert _to_utc_dt([2026, 8, 4]) is None
        assert _to_utc_dt({"date": "2026-08-04"}) is None
        assert _to_utc_dt(True) is None


class TestDetectAndImputeAdversarial:
    """White-box adversarial testing of detect_and_impute_missing_doses."""

    def test_integer_and_float_timestamps_handled_safely(self):
        """Verify integer and float timestamps in glucose_readings do not raise AttributeError."""
        readings = [
            {"timestamp": 1700000000, "value": 220.0},
            {"timestamp": 1700000900, "value": 180.0},
            {"timestamp": 1700001800, "value": 140.0},
            {"timestamp": 1700002700, "value": 100.0},
            {"timestamp": 1700003600.5, "value": 90.0},
        ]
        result = detect_and_impute_missing_doses(readings, [])
        assert isinstance(result, list)

    def test_string_meal_doses_handled_safely(self):
        """Verify string meal values in logged_doses do not raise TypeError."""
        base_time = datetime.now(timezone.utc) - timedelta(hours=3)
        readings = [
            {"timestamp": (base_time + timedelta(minutes=i*15)).isoformat(), "value": 220.0 - i*20.0}
            for i in range(6)
        ]
        logged_doses = [
            {"timestamp": (base_time - timedelta(minutes=30)).isoformat(), "meal": "15.0"},
            {"timestamp": (base_time - timedelta(minutes=15)).isoformat(), "meal": "nan"},
            {"timestamp": (base_time).isoformat(), "meal": None},
        ]
        result = detect_and_impute_missing_doses(readings, logged_doses)
        assert isinstance(result, list)

    def test_string_min_confidence_handled_safely(self):
        """Verify string min_confidence values do not raise TypeError."""
        base_time = datetime.now(timezone.utc) - timedelta(hours=3)
        readings = [
            {"timestamp": (base_time + timedelta(minutes=i*15)).isoformat(), "value": 220.0 - i*20.0}
            for i in range(6)
        ]
        for min_conf in ["0.50", "0.7", "0", "invalid", None]:
            result = detect_and_impute_missing_doses(readings, [], min_confidence=min_conf)
            assert isinstance(result, list)

    def test_imputed_dose_schema_conformance(self):
        """Verify returned imputed dose objects strictly match required JSON schema."""
        base_time = datetime.now(timezone.utc) - timedelta(hours=3)
        readings = [
            {"timestamp": (base_time).isoformat(), "value": 230.0},
            {"timestamp": (base_time + timedelta(minutes=15)).isoformat(), "value": 210.0},
            {"timestamp": (base_time + timedelta(minutes=30)).isoformat(), "value": 175.0},
            {"timestamp": (base_time + timedelta(minutes=45)).isoformat(), "value": 140.0},
            {"timestamp": (base_time + timedelta(minutes=60)).isoformat(), "value": 110.0},
            {"timestamp": (base_time + timedelta(minutes=90)).isoformat(), "value": 90.0},
        ]
        result = detect_and_impute_missing_doses(readings, [], min_confidence=0.40)
        assert len(result) > 0
        imputed = result[0]
        assert imputed["is_imputed"] is True
        assert isinstance(imputed["confidence_score"], float)
        assert imputed["confidence_score"] >= 0.0
        assert imputed["rapid_acting"] > 0.0
        assert imputed["correction"] > 0.0
        assert imputed["device"] == "Missing Dose Imputation Model"

    def test_empty_and_insufficient_readings(self):
        assert detect_and_impute_missing_doses([], []) == []
        assert detect_and_impute_missing_doses(None, []) == []
        readings_3 = [
            {"timestamp": "2026-08-04T00:00:00Z", "value": 200.0},
            {"timestamp": "2026-08-04T00:15:00Z", "value": 180.0},
            {"timestamp": "2026-08-04T00:30:00Z", "value": 160.0},
        ]
        assert detect_and_impute_missing_doses(readings_3, []) == []
