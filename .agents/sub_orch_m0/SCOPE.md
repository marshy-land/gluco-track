# Scope: Milestone M0 - E2E Testing Track

## Architecture & Design
- Framework: pytest / python standard test suite runner for E2E tests in `e2e_tests/`
- Test Philosophy: Opaque-box, requirement-driven E2E testing for Gluco Track features (R1, R2, R3).
- Runner entrypoint: `python e2e_tests/run_tests.py` or `pytest e2e_tests/`.

## Feature Inventory Mapping
| # | Feature | Requirements | Scope | Assigned Tier |
|---|---------|--------------|-------|---------------|
| 1 | R1 Literature-Backed Dietary Analysis | R1.1 Anomaly detection, R1.2 Literature query, R1.3 Report generation (`dietary_remedies_report.md`) | Tier 1, 2, 3, 4 |
| 2 | R2 Missing Dose Imputation Integration | R2.1 Pharmacodynamic deconvolution model, R2.2 DB & API `/api/insulin/history?include_imputed=true`, R2.3 UI visual indicators | Tier 1, 2, 3, 4 |
| 3 | R3 Time-of-Day Nutritional Impact Model | R3.1 Diurnal meal impact model, R3.2 API `/api/nutritional-impact`, R3.3 UI panel | Tier 1, 2, 3, 4 |

## Test Tier Decomposition
- **Tier 1: Feature Coverage (>=5 test cases per feature)**
  - R1: 5 tests (anomaly detection logic, PubMed/OpenAlex API integration, report structure & markdown section checks, citation validation, actionable plan verification)
  - R2: 5 tests (imputation model output validity, DB schema `is_imputed` flag, API endpoint filter `include_imputed=true` response format, confidence score bounds [0,1], visual chart styling properties)
  - R3: 5 tests (time bucket calculations for Morning/Afternoon/Evening/Night, API endpoint `/api/nutritional-impact` output format, peak rise/latency values, modifier multiplier bounds, UI panel data binding)
  - Total Tier 1: 15 tests
- **Tier 2: Boundary & Corner Cases (>=5 test cases per feature)**
  - R1: 5 tests (empty historical data, flat line glucose data, all high glucose, all low glucose, missing API network connections/timeouts)
  - R2: 5 tests (zero missing doses, 100% missing doses, noisy glucose data, extreme peak spikes, negative values handling)
  - R3: 5 tests (boundary timestamps between time-buckets e.g. 05:59 vs 06:00, single meal data, zero glucose rise, extreme latency inputs, missing historical time-of-day records)
  - Total Tier 2: 15 tests
- **Tier 3: Cross-Feature Interactions (pairwise testing)**
  - Pairwise R1 x R2: Anomaly detection with imputed doses in historical dataset
  - Pairwise R2 x R3: Imputed doses combined with time-of-day nutritional impact calculations
  - Pairwise R1 x R3: Dietary remedy recommendations considering time-of-day glycemic impact
  - Total Tier 3: 3 tests
- **Tier 4: Real-World Application Scenarios**
  - Scenario 1: Full multi-day LibreView CSV export import -> anomaly detection -> literature report generation -> missing dose imputation -> diurnal meal impact calculation -> dashboard rendering.
  - Scenario 2: Severe Dawn Phenomenon & Nocturnal Hypo patient profile E2E simulation.
  - Scenario 3: High Glycemic Variability & frequent unlogged correction doses patient profile E2E simulation.
  - Total Tier 4: 3 tests

Total minimum planned test cases: 36 test cases.

## Interface Contracts & Verification Mechanisms
- `TEST_INFRA.md`: Project root test infrastructure specification.
- `TEST_READY.md`: Project root readiness signal containing test runner command, tier counts, and feature checklist.

## Sub-Milestone Execution Plan
| # | Task | Target Output | Status |
|---|------|---------------|--------|
| 1 | Create test runner infra & Tier 1 tests | `e2e_tests/run_tests.py`, `e2e_tests/test_tier1_*.py` | DONE |
| 2 | Create Tier 2 Boundary & Edge tests | `e2e_tests/test_tier2_*.py` | DONE |
| 3 | Create Tier 3 Cross-Feature tests | `e2e_tests/test_tier3_*.py` | DONE |
| 4 | Create Tier 4 Real-World Scenarios | `e2e_tests/test_tier4_*.py` | DONE |
| 5 | Run & verify test suite execution | Test execution pass report (36/36 pass) | DONE |
| 6 | Create TEST_INFRA.md & publish TEST_READY.md | `TEST_INFRA.md`, `TEST_READY.md` | DONE |
