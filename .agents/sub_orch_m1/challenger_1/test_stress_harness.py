"""
Comprehensive Stress Test Harness for Milestone M1 / Requirement R1
Empirical verification of dietary analysis engine, report generator, literature API fallbacks, and link formatting.
"""

import sys
import os
import re
import math
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

# Add repository root to path
sys.path.insert(0, r"c:\Users\tugha\Documents\antigravity\noble-galileo")

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

def test_stress_empty_and_corrupted_datasets():
    print("--- Testing Stress Condition: Empty & Corrupted Datasets ---")
    
    # 1. Empty dataset
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
    temp_report_path = r"c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\sub_orch_m1\challenger_1\test_empty_report.md"
    out_path = generate_report(readings=[], output_path=temp_report_path, use_network=False)
    assert os.path.exists(out_path)
    with open(out_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "# Executive Summary" in content

    # 2. Corrupted / Missing values dataset
    corrupted_readings = [
        {"timestamp": "2026-08-01T12:00:00Z", "value": None},
        {"timestamp": "2026-08-01T12:15:00Z"},  # missing value key
        {"value": 150.0},  # missing timestamp key
        {"timestamp": "invalid-timestamp", "value": 120.0},
        {"timestamp": "2026-08-01T13:00:00Z", "value": 220.0}, # valid spike reading
    ]
    
    stats_corr = calculate_glycemic_stats(corrupted_readings)
    assert stats_corr.total_readings == 2  # values 150.0 and 220.0
    assert stats_corr.mean_glucose == 185.0

    print("✅ Empty & Corrupted Datasets Test Passed!")


def test_stress_extreme_glycemic_volatility():
    print("--- Testing Stress Condition: Extreme Glycemic Volatility (CV > 50%) ---")
    
    # Construct extreme volatility dataset: readings oscillating between 40 mg/dL and 380 mg/dL
    base_dt = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    extreme_readings = []
    for i in range(24):
        val = 40.0 if i % 2 == 0 else 380.0
        ts = (base_dt + timedelta(hours=i)).isoformat()
        extreme_readings.append({"timestamp": ts, "value": val})

    stats, summary = analyze_glucose_dataset(extreme_readings, timezone_str="UTC")
    
    print(f"  Calculated Mean: {stats.mean_glucose} mg/dL, SD: {stats.std_dev}, CV: {stats.cv_percent}%")
    assert stats.cv_percent > 50.0, f"Expected CV > 50%, got {stats.cv_percent}%"
    assert summary.high_variability_days >= 1

    # Render report under extreme CV
    temp_report_path = r"c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\sub_orch_m1\challenger_1\test_extreme_cv_report.md"
    citations = fetch_literature_for_anomalies(["high_glycemic_variability"], use_network=False)
    report_md = render_markdown_report(stats, summary, citations, timezone_str="UTC")
    
    assert "High Volatility" in report_md
    assert f"| **Glycemic Variability (CV)** | {stats.cv_percent}% | <= 36.0% | High Volatility |" in report_md

    print("✅ Extreme Glycemic Volatility (CV > 50%) Test Passed!")


def test_stress_somogyi_effect_triggers():
    print("--- Testing Stress Condition: Somogyi Effect Triggers ---")

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
        {"timestamp": "2026-08-01T02:30:00Z", "value": 105.0}, # Stable overnight
        {"timestamp": "2026-08-01T03:30:00Z", "value": 100.0},
        {"timestamp": "2026-08-01T05:00:00Z", "value": 130.0},
        {"timestamp": "2026-08-01T06:30:00Z", "value": 165.0}, # Morning rise (+65 mg/dL)
        {"timestamp": "2026-08-01T07:45:00Z", "value": 155.0},
    ]

    dawn_true = detect_dawn_phenomenon(true_dawn_readings, timezone_str="UTC")
    assert len(dawn_true) == 1, f"Expected 1 Dawn Phenomenon event, got {len(dawn_true)}"
    assert dawn_true[0].peak_value == 165.0

    print("✅ Somogyi Effect Trigger & Exclusion Test Passed!")


def test_stress_api_fallbacks():
    print("--- Testing Stress Condition: Scientific API Fallbacks & Offline Mode ---")

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

    print("✅ Scientific API Fallbacks & Offline Mode Test Passed!")


def test_validate_link_formats_in_generated_report():
    print("--- Testing Link Format Validation in generated dietary_remedies_report.md ---")

    # Generate actual report file in repo root and in challenger dir
    repo_report_path = r"c:\Users\tugha\Documents\antigravity\noble-galileo\dietary_remedies_report.md"
    generate_report(output_path=repo_report_path, use_network=False)
    
    assert os.path.exists(repo_report_path), "dietary_remedies_report.md does not exist at repo root!"
    
    with open(repo_report_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Find all PMID lines and links
    # Format required: All PMID links must match https://pubmed.ncbi.nlm.nih.gov/<PMID>/
    pmid_link_pattern = re.compile(r"https:\/\/pubmed\.ncbi\.nlm\.nih\.gov\/([^\/\s\)]+)\/?")
    pmid_matches = pmid_link_pattern.findall(content)
    
    print(f"  Found {len(pmid_matches)} PMID URL occurrences in report.")
    assert len(pmid_matches) > 0, "No PMID links found in generated report!"

    # Check exact markdown formatting for PMIDs: - **PMID:** [<PMID>](https://pubmed.ncbi.nlm.nih.gov/<PMID>/)
    pmid_md_lines = [line for line in content.splitlines() if "**PMID:**" in line]
    for line in pmid_md_lines:
        print(f"  Verifying PMID line: {line.strip()}")
        # Check that it matches `[<PMID>](https://pubmed.ncbi.nlm.nih.gov/<PMID>/)`
        match = re.search(r"\[(\d+)\]\((https:\/\/pubmed\.ncbi\.nlm\.nih\.gov\/\1\/)\)", line)
        assert match is not None, f"PMID link format invalid in line: '{line}'! Expected format: [PMID](https://pubmed.ncbi.nlm.nih.gov/PMID/)"

    # Find all DOI lines and links
    # Format required: All DOI links must match https://doi.org/<DOI>
    doi_md_lines = [line for line in content.splitlines() if "**DOI:**" in line]
    print(f"  Found {len(doi_md_lines)} DOI markdown lines in report.")
    assert len(doi_md_lines) > 0, "No DOI lines found in generated report!"

    for line in doi_md_lines:
        print(f"  Verifying DOI line: {line.strip()}")
        # Check that it matches `[<DOI>](https://doi.org/<DOI>)`
        # Note: DOI might be e.g. 10.2337/dc15-0429
        match = re.search(r"\[([^\]]+)\]\((https:\/\/doi\.org\/\1)\)", line)
        assert match is not None, f"DOI link format invalid in line: '{line}'! Expected format: [DOI](https://doi.org/DOI)"

    print("✅ Report Link Format Validation Passed!")


if __name__ == "__main__":
    test_stress_empty_and_corrupted_datasets()
    test_stress_extreme_glycemic_volatility()
    test_stress_somogyi_effect_triggers()
    test_stress_api_fallbacks()
    test_validate_link_formats_in_generated_report()
    print("\n🎉 ALL STRESS TESTS AND HARNESSES PASSED SUCCESSFULLY!")
