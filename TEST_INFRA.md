# Test Infrastructure Specification — Gluco Track (M0 E2E Testing Track)

## Overview
This document specifies the requirement-driven, opaque-box E2E test harness and test suite built for Gluco Track features **R1** (Literature-Backed Dietary Analysis), **R2** (Missing Dose Imputation Integration), and **R3** (Time-of-Day Nutritional Impact Model).

## Philosophy & Progressive Testability
1. **Opaque-Box Testing**: All test cases test system behavior through public APIs, exported module interfaces, markdown report outputs, and UI layout contracts.
2. **Progressive Testability**: During milestone execution (M0 through M4), tests dynamically load real implementation modules (`dietary_analysis.py`, `imputation.py`, `nutritional_model.py`) if present. If modules are not yet implemented in earlier milestones, contract dispatchers fall back to verified reference contract specifications in `e2e_tests/contracts.py`.
3. **Isolation & Independence**: Each test case generates its own synthetic data or temporary file state without depending on test execution order or external network state.

---

## Directory Structure
```
c:\Users\tugha\Documents\antigravity\noble-galileo\
├── e2e_tests/
│   ├── __init__.py
│   ├── contracts.py                # Reference contract models, dynamic module loaders, synthetic data generators
│   ├── test_tier1_features.py      # Tier 1: Feature Coverage (15 test cases)
│   ├── test_tier2_boundaries.py    # Tier 2: Boundary & Corner Cases (15 test cases)
│   ├── test_tier3_interactions.py  # Tier 3: Cross-Feature Interactions (3 test cases)
│   ├── test_tier4_scenarios.py     # Tier 4: Real-World Scenarios (3 test cases)
│   └── run_tests.py                # Standalone formatted test runner script
├── TEST_INFRA.md                   # This specification file
└── TEST_READY.md                   # Readiness signal & completion certificate
```

---

## Test Tier Breakdown (36 Total Test Cases)

### Tier 1: Feature Coverage (15 Test Cases)
- **R1 Literature-Backed Dietary Analysis (5 Tests)**:
  - `test_r1_01_anomaly_detection_logic`: Identifies postprandial spikes, dawn phenomenon, nocturnal hypos, and variability.
  - `test_r1_02_literature_search_api_integration`: Structure and content of PubMed PMIDs and OpenAlex DOIs.
  - `test_r1_03_report_markdown_structure`: Generation of `dietary_remedies_report.md` with mandatory headers.
  - `test_r1_04_citation_validation`: Validates PMID and DOI markdown link formats.
  - `test_r1_05_actionable_plan_verification`: Verifies actionable recommendations mapped to detected anomalies.
- **R2 Missing Dose Imputation (5 Tests)**:
  - `test_r2_01_imputation_model_output_validity`: Pharmacodynamic deconvolution output structure and unit bounds.
  - `test_r2_02_db_schema_is_imputed_flag`: JSON/DB record contract for `is_imputed` (bool) and `confidence_score` (float).
  - `test_r2_03_api_insulin_history_filter`: Endpoint filter `GET /api/insulin/history?include_imputed=true`.
  - `test_r2_04_confidence_score_bounds`: Strictly enforces confidence score bounds in `[0.0, 1.0]`.
  - `test_r2_05_dashboard_chart_visual_styling`: Frontend Chart.js `insulinChart` styling configuration in `templates/index.html`.
- **R3 Time-of-Day Nutritional Impact (5 Tests)**:
  - `test_r3_01_time_bucket_calculations`: Bucket mapping for Morning, Afternoon, Evening, Night.
  - `test_r3_02_api_nutritional_impact_json_schema`: Schema for `GET /api/nutritional-impact` response.
  - `test_r3_03_peak_rise_and_latency_values`: Numerical accuracy of peak rise (mg/dL) and peak latency (min).
  - `test_r3_04_modifier_multiplier_bounds`: Multiplier bounds checking within `[0.5, 2.5]`.
  - `test_r3_05_ui_glassmorphic_panel_data_binding`: UI dashboard container verification in `templates/index.html`.

### Tier 2: Boundary & Corner Cases (15 Test Cases)
- **R1 Boundaries (5 Tests)**: Empty historical dataset, flatline glucose data, 100% hyperglycemia, 100% hypoglycemia, API network offline fallback.
- **R2 Boundaries (5 Tests)**: Zero missing doses, 100% missing doses, noisy/corrupt data handling, extreme peak spikes capping (<=15U), non-negative dose bounds.
- **R3 Boundaries (5 Tests)**: Hour boundary timestamps (05:59 vs 06:00), single meal input, zero glucose rise, extreme latency (up to 300m), multi-day gap handling.

### Tier 3: Cross-Feature Interactions (3 Test Cases)
- `test_r3_tier3_01_pairwise_r1_x_r2_anomalies_with_imputed_doses`: Anomaly detection on datasets with imputed vs logged doses.
- `test_r3_tier3_02_pairwise_r2_x_r3_imputed_doses_and_diurnal_impact`: Imputed dose estimation combined with diurnal impact modifiers.
- `test_r3_tier3_03_pairwise_r1_x_r3_dietary_report_with_diurnal_modifiers`: Integrating diurnal meal modifiers into `dietary_remedies_report.md` Actionable Plan.

### Tier 4: Real-World Scenarios (3 Test Cases)
- `test_r4_tier4_01_full_multiday_libreview_e2e_workflow`: 7-day LibreView CSV ingestion -> R2 imputation -> R3 nutritional impact -> R1 report generation.
- `test_r4_tier4_02_dawn_phenomenon_and_nocturnal_hypo_patient_profile`: 14-day telemetry simulation for severe Dawn & Hypo patient profile.
- `test_r4_tier4_03_high_glycemic_variability_unlogged_corrections_patient_profile`: 14-day telemetry simulation for high-variability unlogged correction profile.

---

## How to Execute the Suite

### Method 1: Standalone Formatted Test Runner Script (Recommended)
```bash
python e2e_tests/run_tests.py
```
Outputs formatted tier-by-tier execution logs, pass/fail status per tier, and exits with status code 0 if all tests pass.

### Method 2: Standard Pytest
```bash
python -m pytest e2e_tests/
```
Runs all 36 test cases using pytest runner.
