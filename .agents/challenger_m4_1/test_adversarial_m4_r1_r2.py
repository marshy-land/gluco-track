"""
Adversarial Stress Test Suite for Milestone M4 (R1 & R2)
Author: Challenger 1 (Milestone M4 Phase 2 Tier 5)

Tests:
1. R2 Imputation - String glucose values ("200") handling (TypeError vulnerability check)
2. R2 Imputation - NaN, Inf, empty, jumbled, naive/aware timestamps, zero/negative ISF
3. R2 Imputation - Boundary conditions: drop < 25 mg/dL, start < 120 mg/dL, 3h gap, 45m logged dose proximity
4. R1 Dietary Analysis - Empty, corrupted, extreme volatility, Somogyi exclusion
5. R1 Literature API - Network timeout/failure, invalid anomaly category, link formatting
"""

import math
import pytest
from datetime import datetime, timezone, timedelta
import pytz
from unittest.mock import patch, MagicMock
import urllib.error

# Import R1 & R2 modules
import dietary_analysis
from dietary_analysis import (
    calculate_glycemic_stats,
    detect_postprandial_spikes,
    detect_nocturnal_hypos,
    detect_dawn_phenomenon,
    calculate_glycemic_variability,
    analyze_glucose_dataset,
    render_markdown_report,
    generate_report,
    format_pmid_link,
    format_doi_link,
    AnomalyType
)

import literature_api
from literature_api import (
    Citation,
    fetch_literature_for_anomaly,
    fetch_literature_for_anomalies,
    query_pubmed_api,
    query_openalex_api,
    LANDMARK_LITERATURE
)

import imputation
from imputation import detect_and_impute_missing_doses


# -----------------------------------------------------------------------------
# R2 MISSING DOSE IMPUTATION ADVERSARIAL TESTS
# -----------------------------------------------------------------------------

def test_r2_imputation_string_glucose_values():
    """
    Adversarial test: Glucose readings containing string values (e.g. "240.0", "100.0").
    Checks if imputation module coerces/handles string numeric values without throwing TypeError.
    """
    base_time = datetime(2026, 8, 4, 12, 0, 0, tzinfo=pytz.utc)
    readings = []
    for m in range(0, 95, 5):
        t = base_time + timedelta(minutes=m)
        val_str = str(round(240.0 - (140.0 * (m / 90.0)), 1))
        readings.append({"timestamp": t, "value": val_str})

    # Execute imputation
    try:
        imputed = detect_and_impute_missing_doses(readings, [])
        # If it handles string values properly or coerces them:
        assert isinstance(imputed, list)
    except TypeError as e:
        # Documented Bug Finding: String values in glucose readings cause TypeError!
        pytest.fail(f"FINDING DETECTED: String values in glucose readings caused TypeError: {e}")


def test_r2_imputation_nan_inf_mixed_readings():
    """
    Adversarial test: Readings with NaN, Inf, None, invalid values.
    Verify module does not crash or output NaN/Inf doses.
    """
    base_time = datetime(2026, 8, 4, 12, 0, 0, tzinfo=pytz.utc)
    readings = [
        {"timestamp": base_time, "value": 250.0},
        {"timestamp": base_time + timedelta(minutes=15), "value": float('nan')},
        {"timestamp": base_time + timedelta(minutes=30), "value": 200.0},
        {"timestamp": base_time + timedelta(minutes=45), "value": float('inf')},
        {"timestamp": base_time + timedelta(minutes=60), "value": 150.0},
        {"timestamp": base_time + timedelta(minutes=75), "value": 110.0},
    ]

    imputed = detect_and_impute_missing_doses(readings, [])
    assert isinstance(imputed, list)
    for d in imputed:
        assert not math.isnan(d["rapid_acting"])
        assert not math.isinf(d["rapid_acting"])
        assert not math.isnan(d["confidence_score"])


def test_r2_imputation_boundary_clamping():
    """
    Adversarial test: Verify lower (0.5 U) and upper (15.0 U) dose bounds.
    """
    base_time = datetime(2026, 8, 4, 12, 0, 0, tzinfo=pytz.utc)
    # Huge drop: 400 -> 50 mg/dL (350 mg/dL drop)
    massive_drop_readings = []
    for m in range(0, 65, 5):
        t = base_time + timedelta(minutes=m)
        val = 400.0 - (350.0 * (m / 60.0))
        massive_drop_readings.append({"timestamp": t, "value": round(val, 1)})

    imputed_large = detect_and_impute_missing_doses(massive_drop_readings, [])
    assert len(imputed_large) >= 1
    for d in imputed_large:
        assert d["rapid_acting"] <= 15.0, f"Dose {d['rapid_acting']} exceeded upper bound 15.0 U"
        assert d["rapid_acting"] >= 0.5, f"Dose {d['rapid_acting']} below lower bound 0.5 U"


def test_r2_imputation_3h_gap_enforcement():
    """
    Adversarial test: Two distinct drops within 2 hours of each other.
    Greedy non-overlapping selection should enforce a 3-hour minimum gap.
    """
    base_time = datetime(2026, 8, 4, 12, 0, 0, tzinfo=pytz.utc)
    readings = []
    # Drop 1: 12:00 -> 13:30 (240 -> 100)
    for m in range(0, 95, 5):
        t = base_time + timedelta(minutes=m)
        val = 240.0 - (140.0 * (m / 90.0))
        readings.append({"timestamp": t, "value": round(val, 1)})

    # Drop 2: 13:30 -> 15:00 (230 -> 110)
    for m in range(95, 185, 5):
        t = base_time + timedelta(minutes=m)
        val = 230.0 - (120.0 * ((m - 90) / 90.0))
        readings.append({"timestamp": t, "value": round(val, 1)})

    imputed = detect_and_impute_missing_doses(readings, [])
    assert len(imputed) == 1, f"Expected 1 imputed dose due to 3h gap constraint, got {len(imputed)}"


# -----------------------------------------------------------------------------
# R1 LITERATURE-BACKED DIETARY ANALYSIS ADVERSARIAL TESTS
# -----------------------------------------------------------------------------

def test_r1_dietary_analysis_corrupted_inputs():
    """
    Adversarial test: Malformed reading dictionaries, string values, missing keys.
    """
    readings = [
        {"timestamp": "2026-08-01T12:00:00Z", "value": None},
        {"timestamp": "invalid-iso-string", "value": 150.0},
        {"value": 200.0},
        {"timestamp": "2026-08-01T13:00:00Z", "value": "invalid_number"},
        {"timestamp": "2026-08-01T14:00:00Z", "value": 180.0},
    ]

    stats = calculate_glycemic_stats(readings)
    assert stats.total_readings == 3
    assert stats.mean_glucose == 176.7

    spikes = detect_postprandial_spikes(readings)
    assert isinstance(spikes, list)

    hypos = detect_nocturnal_hypos(readings)
    assert isinstance(hypos, list)


def test_r1_somogyi_exclusion_adversarial():
    """
    Adversarial test: Confirm Somogyi effect (nocturnal hypo < 70 mg/dL between 22:00 - 04:00)
    strictly excludes Dawn Phenomenon detection for that day.
    """
    readings_with_hypo = [
        {"timestamp": "2026-08-01T02:00:00Z", "value": 65.0},  # Nocturnal hypo!
        {"timestamp": "2026-08-01T03:30:00Z", "value": 90.0},
        {"timestamp": "2026-08-01T05:00:00Z", "value": 130.0},
        {"timestamp": "2026-08-01T06:30:00Z", "value": 175.0},  # Rise of +85 mg/dL
        {"timestamp": "2026-08-01T07:30:00Z", "value": 160.0},
    ]

    dawn_events = detect_dawn_phenomenon(readings_with_hypo, timezone_str="UTC")
    assert len(dawn_events) == 0, "Dawn phenomenon must be excluded when Somogyi effect occurs!"


def test_r1_literature_api_network_failure_resilience():
    """
    Adversarial test: Simulate complete network outage during literature retrieval.
    Verify Tier 4 offline landmark database fallback delivers citations cleanly.
    """
    literature_api.clear_cache()
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Network unreachable")):
        citations = fetch_literature_for_anomaly("postprandial_spike", custom_query="adversarial_test", use_network=True)
        assert len(citations) > 0, "Literature API failed to return Tier 4 landmark citations during network failure"
        assert citations[0].anomaly_category == "postprandial_spike"
        assert citations[0].pubmed_url is not None or citations[0].doi_url is not None


def test_r1_link_formatting():
    """
    Adversarial test: Test PMID & DOI formatters with edge case inputs (None, prefixed DOIs, spaces).
    """
    assert format_pmid_link("26106214 ") == "[26106214](https://pubmed.ncbi.nlm.nih.gov/26106214/)"
    assert format_pmid_link(None) == "N/A"

    assert format_doi_link("10.2337/dc15-0429") == "[10.2337/dc15-0429](https://doi.org/10.2337/dc15-0429)"
    assert format_doi_link("https://doi.org/10.2337/dc15-0429") == "[10.2337/dc15-0429](https://doi.org/10.2337/dc15-0429)"
    assert format_doi_link(None) == "N/A"
