# Scope: Milestone M1 — Literature-Backed Dietary Analysis Engine & Report Generator

## Overview
Milestone M1 implements Requirement R1: Literature-Backed Dietary Analysis Engine & Report Generator. It analyzes glucose data for glycemic anomalies, queries scientific APIs (PubMed, OpenAlex) for literature-backed remedies, and generates a structured report `dietary_remedies_report.md`.

## Deliverables & Acceptance Criteria
1. **Anomaly Detection Algorithms (`dietary_analysis.py`)**:
   - Postprandial Spikes (> 180 mg/dL following meals or general glucose readings)
   - Dawn Phenomenon (04:00 - 08:00 AM glycemic rise without nocturnal hypo trigger or specific morning elevation, with Somogyi exclusion check)
   - Nocturnal Hypos (< 70 mg/dL during nighttime hours 22:00 - 06:00, with Level 1 vs Level 2 Severe < 54 mg/dL classification)
   - Glycemic Variability (Coefficient of Variation CV = SD / Mean > 36%)
2. **Scientific API Integration (`literature_api.py`)**:
   - Programmatic integration with PubMed (NCBI E-utilities API) and OpenAlex API.
   - 4-Tier Resilience Strategy (Tier 1: SQLite Cache -> Tier 2: PubMed -> Tier 3: OpenAlex -> Tier 4: Offline Landmark DB).
   - Citation metadata extraction (title, authors, journal, year, PMID, DOI link).
   - Dynamic database path configuration (`set_db_cache_file`) and complete test isolation.
3. **Automated Report Generator (`dietary_remedies_report.md`)**:
   - Explicit user statistics (mean glucose, GMI/eA1c, % TIR/TAR/TBR, CV, anomaly counts).
   - Actionable dietary interventions tailored to detected glycemic anomalies.
   - Peer-reviewed literature citations complete with hyperlinked PMID (`https://pubmed.ncbi.nlm.nih.gov/<PMID>/`) and clickable DOI links (`https://doi.org/<DOI>`).

## Interface & File Ownership
- Primary code files: `dietary_analysis.py`, `literature_api.py`
- Target report file: `c:\Users\tugha\Documents\antigravity\noble-galileo\dietary_remedies_report.md`
- Tests: `tests/test_dietary_analysis.py`, `tests/test_literature_api.py`

## Iteration Status
- Status: **DONE**
- Current Iteration: 2
- Gate Result: PASS
