"""
Unit tests for dietary_analysis.py (Milestone M1 / Requirement R1)

Tests:
  - Clinical statistics calculations (Mean, GMI consensus formula, CV %, TIR, TAR, TBR)
  - Anomaly detection algorithms:
      a. Postprandial Spikes (> 180 mg/dL)
      b. Dawn Phenomenon (04:00 - 08:00 AM rise)
      c. Somogyi Exclusion Check (verify nighttime hypo < 70 mg/dL excludes Dawn Phenomenon)
      d. Nocturnal Hypoglycemia (< 70 mg/dL between 22:00 - 06:00) with severity classification
      e. Glycemic Variability (CV > 36%)
  - Report generator end-to-end rendering and link formatting
"""

import os
import pytest
from datetime import datetime, timedelta, timezone

import dietary_analysis
from dietary_analysis import (
    calculate_glycemic_stats,
    detect_postprandial_spikes,
    detect_dawn_phenomenon,
    detect_nocturnal_hypos,
    calculate_glycemic_variability,
    generate_report,
    format_pmid_link,
    format_doi_link,
    AnomalyType
)


def test_glycemic_stats_calculation():
    """Verify Mean, GMI, CV %, TIR, TAR, TBR calculations against known values."""
    # Synthetic readings: 100, 120, 150, 180, 200 (Mean = 150.0)
    readings = [
        {"timestamp": f"2026-08-01T{i:02d}:00:00Z", "value": val}
        for i, val in enumerate([100.0, 120.0, 150.0, 180.0, 200.0])
    ]

    stats = calculate_glycemic_stats(readings)

    assert stats.total_readings == 5
    assert stats.mean_glucose == 150.0

    # GMI = 3.31 + 0.02392 * Mean = 3.31 + (0.02392 * 150.0) = 6.90
    expected_gmi = round(3.31 + (0.02392 * 150.0), 2)
    assert stats.gmi == expected_gmi

    # TIR (70-180 mg/dL): 100, 120, 150, 180 -> 4 out of 5 = 80.0%
    assert stats.tir_percent == 80.0

    # TAR (>180 mg/dL): 200 -> 1 out of 5 = 20.0%
    assert stats.tar_percent == 20.0

    # TBR (<70 mg/dL): 0 out of 5 = 0.0%
    assert stats.tbr_percent == 0.0


def test_detect_postprandial_spikes():
    """Verify detection of postprandial spikes (> 180 mg/dL)."""
    readings = [
        {"timestamp": "2026-08-01T12:00:00Z", "value": 110.0},
        {"timestamp": "2026-08-01T12:30:00Z", "value": 150.0},
        {"timestamp": "2026-08-01T13:00:00Z", "value": 210.0},  # Spike
        {"timestamp": "2026-08-01T13:30:00Z", "value": 235.0},  # Spike Peak
        {"timestamp": "2026-08-01T14:00:00Z", "value": 170.0},
    ]

    spikes = detect_postprandial_spikes(readings, timezone_str="UTC")
    assert len(spikes) == 1
    s = spikes[0]
    assert s.anomaly_type == AnomalyType.POSTPRANDIAL_SPIKE
    assert s.peak_value == 235.0
    assert s.nadir_value == 110.0  # baseline
    assert s.delta_value == 125.0  # 235 - 110
    assert s.severity == "Moderate"


def test_detect_dawn_phenomenon_valid():
    """Verify Dawn Phenomenon detection when morning glucose rises (04:00-08:00 AM) with NO nocturnal hypo."""
    readings = [
        # Nighttime readings (22:00 PM to 04:00 AM) - stable around 100 mg/dL (NO hypo)
        {"timestamp": "2026-08-01T02:00:00Z", "value": 105.0},
        {"timestamp": "2026-08-01T03:30:00Z", "value": 100.0},  # Baseline
        # Morning rise window (04:00 to 08:00 AM)
        {"timestamp": "2026-08-01T05:00:00Z", "value": 125.0},
        {"timestamp": "2026-08-01T06:30:00Z", "value": 155.0},  # Peak (+55 mg/dL rise)
        {"timestamp": "2026-08-01T07:45:00Z", "value": 145.0},
    ]

    dawn_events = detect_dawn_phenomenon(readings, timezone_str="UTC", rise_threshold=20.0)
    assert len(dawn_events) == 1
    d = dawn_events[0]
    assert d.anomaly_type == AnomalyType.DAWN_PHENOMENON
    assert d.peak_value == 155.0
    assert d.delta_value == 55.0


def test_somogyi_exclusion_prevents_dawn_phenomenon():
    """
    Verify Somogyi Exclusion Check:
    If nocturnal glucose drops below 70 mg/dL between 22:00 PM and 04:00 AM,
    morning glucose rise must NOT be flagged as Dawn Phenomenon.
    """
    readings = [
        # Nighttime reading with Hypo (< 70 mg/dL) at 02:30 AM
        {"timestamp": "2026-08-01T02:30:00Z", "value": 62.0},  # Nocturnal Hypo trigger!
        {"timestamp": "2026-08-01T03:30:00Z", "value": 85.0},
        # Morning rise window (04:00 to 08:00 AM)
        {"timestamp": "2026-08-01T05:00:00Z", "value": 130.0},
        {"timestamp": "2026-08-01T06:30:00Z", "value": 160.0},  # Rebound rise
        {"timestamp": "2026-08-01T07:45:00Z", "value": 150.0},
    ]

    dawn_events = detect_dawn_phenomenon(readings, timezone_str="UTC", rise_threshold=20.0)
    # Must be excluded due to Somogyi effect!
    assert len(dawn_events) == 0


def test_detect_nocturnal_hypos():
    """Verify detection of nocturnal hypoglycemia (< 70 mg/dL between 22:00 - 06:00) and severity."""
    readings = [
        # Daytime reading < 70 (not nocturnal)
        {"timestamp": "2026-08-01T14:00:00Z", "value": 65.0},
        # Nighttime hypo at 02:00 AM (Level 1)
        {"timestamp": "2026-08-01T02:00:00Z", "value": 64.0},
        {"timestamp": "2026-08-01T02:15:00Z", "value": 58.0},  # Nadir
        # Nighttime hypo next day at 03:00 AM (Level 2 Severe < 54 mg/dL)
        {"timestamp": "2026-08-02T03:00:00Z", "value": 48.0},  # Severe Nadir
    ]

    hypos = detect_nocturnal_hypos(readings, timezone_str="UTC")
    assert len(hypos) == 2

    h1 = hypos[0]
    assert h1.nadir_value == 58.0
    assert h1.severity == "Level 1"

    h2 = hypos[1]
    assert h2.nadir_value == 48.0
    assert h2.severity == "Level 2 Severe"


def test_calculate_glycemic_variability():
    """Verify Glycemic Variability calculation (CV > 36%)."""
    # High variance dataset
    readings = [
        {"timestamp": "2026-08-01T01:00:00Z", "value": 60.0},
        {"timestamp": "2026-08-01T03:00:00Z", "value": 250.0},
        {"timestamp": "2026-08-01T05:00:00Z", "value": 70.0},
        {"timestamp": "2026-08-01T07:00:00Z", "value": 280.0},
        {"timestamp": "2026-08-01T09:00:00Z", "value": 55.0},
        {"timestamp": "2026-08-01T11:00:00Z", "value": 260.0},
        {"timestamp": "2026-08-01T13:00:00Z", "value": 80.0},
        {"timestamp": "2026-08-01T15:00:00Z", "value": 290.0},
    ]

    cv, high_cv_days, cv_anomalies = calculate_glycemic_variability(readings, timezone_str="UTC")
    assert cv > 36.0
    assert len(cv_anomalies) == 1
    assert cv_anomalies[0].anomaly_type == AnomalyType.HIGH_GLYCEMIC_VARIABILITY


def test_link_formatters():
    """Verify formatters for PMID and DOI links."""
    assert format_pmid_link("26106214") == "[26106214](https://pubmed.ncbi.nlm.nih.gov/26106214/)"
    assert format_pmid_link(None) == "N/A"

    assert format_doi_link("10.2337/dc15-0429") == "[10.2337/dc15-0429](https://doi.org/10.2337/dc15-0429)"
    assert format_doi_link("https://doi.org/10.2337/dc15-0429") == "[10.2337/dc15-0429](https://doi.org/10.2337/dc15-0429)"
    assert format_doi_link(None) == "N/A"


def test_generate_report_end_to_end(tmp_path):
    """Verify end-to-end report generation creates valid markdown report file offline."""
    out_file = tmp_path / "test_report.md"
    result_path = generate_report(readings=[], output_path=str(out_file), use_network=False)

    assert os.path.exists(result_path)
    with open(result_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "# Executive Summary" in content
    assert "## 1. Executive Summary & User Glycemic Statistics" in content
    assert "## Observed Glycemic Trends & Anomalies" in content
    assert "## Literature-Backed Dietary Interventions" in content
    assert "## 4. Peer-Reviewed Literature Citations" in content
    assert "## Actionable Plan" in content
    assert "## 6. Clinical Disclaimer" in content

    # Check PMID and DOI link structure in citations
    assert "https://pubmed.ncbi.nlm.nih.gov/" in content
    assert "https://doi.org/" in content
