"""
Challenger Stress Test Harness for Milestone M1 / Requirement R1
Empirical verification of dietary analysis engine, report generator, literature API fallbacks, and link formatting.
"""

import sys
import os
import re
import math
import urllib.error
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

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
    AnomalyType,
    GlycemicStats,
    AnomalySummary
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


def test_stress_empty_dataset(tmp_path):
    """Verify behavior when dataset is completely empty."""
    stats = calculate_glycemic_stats([])
    assert stats.total_readings == 0
    assert stats.mean_glucose == 0.0
    assert stats.cv_percent == 0.0

    spikes = detect_postprandial_spikes([])
    assert spikes == []

    hypos = detect_nocturnal_hypos([])
    assert hypos == []

    dawn = detect_dawn_phenomenon([])
    assert dawn == []

    cv, days, anomalies = calculate_glycemic_variability([])
    assert cv == 0.0 and days == 0 and anomalies == []

    stats, summary = analyze_glucose_dataset([])
    assert stats.total_readings == 0
    assert summary.postprandial_spikes_count == 0

    # Report generation with empty list
    temp_report_path = str(tmp_path / "test_empty_report.md")
    out_path = generate_report(readings=[], output_path=temp_report_path, use_network=False)
    assert os.path.exists(out_path)
    with open(out_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "# Executive Summary" in content


def test_stress_corrupted_dataset_handling():
    """Verify how dietary_analysis handles missing/corrupted fields in readings."""
    # Dataset with missing 'value', missing 'timestamp', and None values
    partially_corrupted = [
        {"timestamp": "2026-08-01T12:00:00Z", "value": None},
        {"timestamp": "2026-08-01T12:15:00Z"},  # missing value key
        {"value": 150.0},  # missing timestamp key
        {"timestamp": "2026-08-01T13:00:00Z", "value": 220.0},  # valid spike reading
    ]

    stats = calculate_glycemic_stats(partially_corrupted)
    assert stats.total_readings == 2  # values 150.0 and 220.0
    assert stats.mean_glucose == 185.0

    # Verify that invalid timestamp ISO string raises ValueError in parse_dt
    with pytest.raises(ValueError):
        dietary_analysis.parse_dt("invalid-timestamp-string")


def test_stress_extreme_glycemic_volatility(tmp_path):
    """Verify behavior under extreme glycemic volatility (CV > 50%)."""
    base_dt = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    extreme_readings = []
    # Readings oscillating between 40 mg/dL and 380 mg/dL
    for i in range(24):
        val = 40.0 if i % 2 == 0 else 380.0
        ts = (base_dt + timedelta(hours=i)).isoformat()
        extreme_readings.append({"timestamp": ts, "value": val})

    stats, summary = analyze_glucose_dataset(extreme_readings, timezone_str="UTC")

    assert stats.cv_percent > 50.0, f"Expected CV > 50%, got {stats.cv_percent}%"
    assert summary.high_variability_days >= 1

    # Render report under extreme CV
    citations = fetch_literature_for_anomalies(["high_glycemic_variability"], use_network=False)
    report_md = render_markdown_report(stats, summary, citations, timezone_str="UTC")

    assert "High Volatility" in report_md
    assert f"| **Glycemic Variability (CV)** | {stats.cv_percent}% | <= 36.0% | High Volatility |" in report_md


def test_stress_somogyi_effect_triggers():
    """Verify Somogyi effect trigger excludes Dawn Phenomenon when nocturnal hypo occurs."""
    # Scenario A: Overnight hypo at 02:30 AM (60 mg/dL) followed by morning rise at 06:00 AM (170 mg/dL)
    # Expected: Somogyi effect triggered -> Dawn Phenomenon EXCLUDED.
    somogyi_readings = [
        {"timestamp": "2026-08-01T02:30:00Z", "value": 60.0},  # Nocturnal hypo < 70
        {"timestamp": "2026-08-01T03:30:00Z", "value": 85.0},
        {"timestamp": "2026-08-01T05:00:00Z", "value": 130.0},
        {"timestamp": "2026-08-01T06:30:00Z", "value": 170.0},  # Rebound rise (+85 mg/dL from baseline)
        {"timestamp": "2026-08-01T07:45:00Z", "value": 160.0},
    ]

    dawn_somogyi = detect_dawn_phenomenon(somogyi_readings, timezone_str="UTC")
    assert len(dawn_somogyi) == 0, f"Expected 0 Dawn Phenomenon events due to Somogyi effect, got {len(dawn_somogyi)}"

    # Scenario B: True Dawn Phenomenon (overnight stable 105 mg/dL, morning rise to 165 mg/dL)
    # Expected: Dawn Phenomenon DETECTED.
    true_dawn_readings = [
        {"timestamp": "2026-08-01T02:30:00Z", "value": 105.0},  # Stable overnight
        {"timestamp": "2026-08-01T03:30:00Z", "value": 100.0},
        {"timestamp": "2026-08-01T05:00:00Z", "value": 130.0},
        {"timestamp": "2026-08-01T06:30:00Z", "value": 165.0},  # Morning rise (+65 mg/dL)
        {"timestamp": "2026-08-01T07:45:00Z", "value": 155.0},
    ]

    dawn_true = detect_dawn_phenomenon(true_dawn_readings, timezone_str="UTC")
    assert len(dawn_true) == 1, f"Expected 1 Dawn Phenomenon event, got {len(dawn_true)}"
    assert dawn_true[0].peak_value == 165.0


def test_stress_api_fallbacks():
    """Verify scientific API fallbacks (offline mode and simulated network error)."""
    literature_api.clear_cache()

    # 1. Test offline mode explicitly (use_network=False)
    for cat in ["postprandial_spike", "dawn_phenomenon", "nocturnal_hypo", "high_glycemic_variability"]:
        cites = fetch_literature_for_anomaly(cat, use_network=False)
        assert len(cites) > 0, f"Offline mode returned empty citations for {cat}"
        for c in cites:
            assert c.title != ""
            assert c.summary != ""
            assert c.pmid is not None or c.doi is not None

    # 2. Test network error / timeout simulation on PubMed & OpenAlex
    literature_api.clear_cache()

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Simulated Network Timeout / Offline")):
        # Attempt to fetch with use_network=True during network outage
        cites_fallback = fetch_literature_for_anomaly("postprandial_spike", custom_query="network_down_query", use_network=True)

        assert len(cites_fallback) > 0, "Fallback failed to return landmark citations during network failure"
        # Confirm it returns Tier 4 Landmark DB results
        landmark_titles = [c.title for c in LANDMARK_LITERATURE["postprandial_spike"]]
        assert cites_fallback[0].title in landmark_titles


def test_validate_link_formats_in_generated_report():
    """Verify link formats in generated dietary_remedies_report.md."""
    repo_report_path = r"c:\Users\tugha\Documents\antigravity\noble-galileo\dietary_remedies_report.md"
    generate_report(output_path=repo_report_path, use_network=False)

    assert os.path.exists(repo_report_path), "dietary_remedies_report.md does not exist at repo root!"

    with open(repo_report_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Find all PMID lines and links
    pmid_link_pattern = re.compile(r"https:\/\/pubmed\.ncbi\.nlm\.nih\.gov\/([^\/\s\)]+)\/?")
    pmid_matches = pmid_link_pattern.findall(content)

    assert len(pmid_matches) > 0, "No PMID links found in generated report!"

    # Check exact markdown formatting for PMIDs: - **PMID:** [<PMID>](https://pubmed.ncbi.nlm.nih.gov/<PMID>/)
    pmid_md_lines = [line for line in content.splitlines() if "**PMID:**" in line]
    for line in pmid_md_lines:
        match = re.search(r"\[(\d+)\]\((https:\/\/pubmed\.ncbi\.nlm\.nih\.gov\/\1\/)\)", line)
        assert match is not None, f"PMID link format invalid in line: '{line}'! Expected format: [PMID](https://pubmed.ncbi.nlm.nih.gov/PMID/)"

    # Find all DOI lines and links
    doi_md_lines = [line for line in content.splitlines() if "**DOI:**" in line]
    assert len(doi_md_lines) > 0, "No DOI lines found in generated report!"

    for line in doi_md_lines:
        match = re.search(r"\[([^\]]+)\]\((https:\/\/doi\.org\/\1)\)", line)
        assert match is not None, f"DOI link format invalid in line: '{line}'! Expected format: [DOI](https://doi.org/DOI)"
