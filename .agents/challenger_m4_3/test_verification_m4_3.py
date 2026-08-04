"""
Challenger 3 Adversarial Re-verification Test Suite (Milestone M4)
Location: .agents/challenger_m4_3/test_verification_m4_3.py

Focus: Deep edge-case verification for R1 (dietary_analysis.py) and R2 (imputation.py, prediction.py)
after Worker 1 defensive parsing remediation.
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

import dietary_analysis
from dietary_analysis import (
    calculate_glycemic_stats,
    detect_postprandial_spikes,
    detect_nocturnal_hypos,
    detect_dawn_phenomenon,
    calculate_glycemic_variability,
    analyze_glucose_dataset,
    generate_report
)

import imputation
from imputation import detect_and_impute_missing_doses

import prediction
from prediction import calculate_iob, predict_glucose, suggest_correction


# =============================================================================
# 1. R1 DIETARY ANALYSIS DEFENSIVE PARSING TESTS
# =============================================================================

def test_r1_string_and_corrupted_glucose_values():
    """Verify calculate_glycemic_stats handles string numbers, invalid strings, None, NaN, Inf."""
    readings = [
        {"timestamp": "2026-08-04T12:00:00Z", "value": "210.5"},  # String float
        {"timestamp": "2026-08-04T12:15:00Z", "value": "150"},    # String int
        {"timestamp": "2026-08-04T12:30:00Z", "value": None},     # None
        {"timestamp": "2026-08-04T12:45:00Z", "value": "N/A"},    # Non-numeric string
        {"timestamp": "2026-08-04T13:00:00Z", "value": float('nan')}, # NaN
        {"timestamp": "2026-08-04T13:15:00Z", "value": float('inf')}, # Inf
        {"timestamp": "2026-08-04T13:30:00Z", "value": 120.0},    # Valid float
        "not_a_dict",                                             # Non-dict item
        {},                                                       # Empty dict
    ]

    stats = calculate_glycemic_stats(readings)
    # Should only count the 3 valid numeric entries (210.5, 150, 120.0)
    assert stats.total_readings == 3
    assert stats.mean_glucose == 160.2  # (210.5 + 150 + 120) / 3 = 160.1666... -> 160.2


def test_r1_anomalies_defensive_parsing():
    """Verify anomaly detection functions parse string glucose readings without crashing."""
    now = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
    readings = [
        {"timestamp": (now + timedelta(minutes=i*15)).isoformat(), "value": str(200.0 + i*10)}
        for i in range(5)
    ]
    readings.append({"timestamp": (now + timedelta(minutes=90)).isoformat(), "value": "invalid"})
    readings.append({"timestamp": None, "value": "150.0"})

    spikes = detect_postprandial_spikes(readings)
    assert isinstance(spikes, list)

    hypos = detect_nocturnal_hypos(readings)
    assert isinstance(hypos, list)

    dawn = detect_dawn_phenomenon(readings)
    assert isinstance(dawn, list)

    cv, days, cv_anomalies = calculate_glycemic_variability(readings)
    assert isinstance(cv, float)
    assert isinstance(days, int)
    assert isinstance(cv_anomalies, list)


# =============================================================================
# 2. R2 MISSING DOSE IMPUTATION DEFENSIVE PARSING TESTS
# =============================================================================

def test_r2_imputation_string_readings_and_doses():
    """Verify detect_and_impute_missing_doses processes string glucose values and string doses."""
    base_time = datetime(2026, 8, 4, 12, 0, 0, tzinfo=pytz.utc)
    readings = []
    # Drop of 240.0 -> 100.0 over 90 mins (represented as string numbers)
    for m in range(0, 95, 5):
        t = base_time + timedelta(minutes=m)
        val_str = str(round(240.0 - (140.0 * (m / 90.0)), 1))
        readings.append({"timestamp": t.isoformat(), "value": val_str})

    # Logged doses with string numeric fields
    doses = [
        {"timestamp": (base_time - timedelta(hours=5)).isoformat(), "rapid_acting": "2.0", "meal": "0.0"}
    ]

    imputed = detect_and_impute_missing_doses(readings, doses)
    assert isinstance(imputed, list)
    assert len(imputed) == 1
    dose_obj = imputed[0]
    assert dose_obj["is_imputed"] is True
    assert isinstance(dose_obj["rapid_acting"], float)
    assert dose_obj["rapid_acting"] >= 0.5
    assert dose_obj["rapid_acting"] <= 15.0


def test_r2_imputation_corrupted_payload_safety():
    """Verify imputation handles corrupted data, None, NaNs, Infs, bad ISO strings."""
    base_time = datetime(2026, 8, 4, 12, 0, 0, tzinfo=pytz.utc)
    readings = [
        {"timestamp": base_time.isoformat(), "value": "250.0"},
        {"timestamp": "bad-timestamp", "value": "240.0"},
        {"timestamp": (base_time + timedelta(minutes=15)).isoformat(), "value": None},
        {"timestamp": (base_time + timedelta(minutes=30)).isoformat(), "value": "corrupted_val"},
        {"timestamp": (base_time + timedelta(minutes=45)).isoformat(), "value": float('nan')},
        {"timestamp": (base_time + timedelta(minutes=60)).isoformat(), "value": float('inf')},
        {"timestamp": (base_time + timedelta(minutes=75)).isoformat(), "value": "150.0"},
        {"timestamp": (base_time + timedelta(minutes=90)).isoformat(), "value": "100.0"},
        "non_dict_entry",
        None
    ]

    imputed = detect_and_impute_missing_doses(readings, None)
    assert isinstance(imputed, list)


# =============================================================================
# 3. R2 PREDICTION & IOB SAFETY TESTS
# =============================================================================

def test_r2_prediction_iob_string_doses():
    """Verify calculate_iob handles string numeric doses and invalid strings safely."""
    now = datetime.now(timezone.utc)
    doses = [
        {"timestamp": (now - timedelta(minutes=30)), "rapid_acting": "3.5", "meal": "1.0", "correction": "0.5"},
        {"timestamp": (now - timedelta(minutes=60)), "rapid_acting": None, "meal": "invalid", "correction": "2.0"},
        {"timestamp": (now - timedelta(minutes=90)), "rapid_acting": float('nan'), "user_change": "1.5"},
        "corrupted_dose_string"
    ]

    iob = calculate_iob(doses, current_time=now)
    assert isinstance(iob, float)
    assert iob >= 0.0
    assert not math.isnan(iob)
    assert not math.isinf(iob)


def test_r2_predict_glucose_string_readings():
    """Verify predict_glucose handles string values in readings."""
    now = datetime.now(timezone.utc)
    readings = [
        {"timestamp": now - timedelta(minutes=30), "value": "150.0"},
        {"timestamp": now - timedelta(minutes=15), "value": "160.5"},
        {"timestamp": now, "value": "170.0"}
    ]

    preds = predict_glucose(readings, minutes_ahead=[15, 30])
    assert isinstance(preds, list)


def test_r2_suggest_correction_string_glucose():
    """Verify suggest_correction handles string current_glucose."""
    res = suggest_correction(current_glucose="180.0", iob=1.0)
    assert isinstance(res, float)

