# Project Specification: Gluco Track Feature Enhancements

## Architecture
- **Framework**: Python 3.11+, FastAPI (`app.py`), Uvicorn ASGI server.
- **Database**: PostgreSQL (`psycopg2`), managed via `db.py` and `schema.sql`.
- **Analytics & ML**: `prediction.py` (IOB, correction math), `ml_heuristics.py` (Ridge regression, ISF computation, circadian time-of-day buckets).
- **Frontend Dashboard**: Single-page app (`templates/index.html`) rendered with HTML/CSS glassmorphic UI and Chart.js v4 (glucose line chart, insulin bar chart).
- **Data Ingestion**: `parser.py` (LibreView CSV export parser), `sync.py` (Abbott LibreLinkUp live polling).

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | E2E Test Suite Infra | Comprehensive requirement-driven opaque-box E2E test harness & Tiers 1-4 test cases | M0 | Survey / System Protocol |
| 2 | R1 Anomaly Detection Engine | Detect Postprandial Spikes, Dawn Phenomenon, Nocturnal Hypos, and Glycemic Variability | M1 | ORIGINAL_REQUEST §R1 |
| 3 | R1 Literature Search Pipeline | Query PubMed E-utilities and OpenAlex APIs for peer-reviewed dietary interventions | M1 | ORIGINAL_REQUEST §R1 |
| 4 | R1 Dietary Report Generator | Generate `dietary_remedies_report.md` with explicit data metrics, interventions, citations | M1 | ORIGINAL_REQUEST §R1 |
| 5 | R2 Missing Dose Imputation Model | Pharmacodynamic deconvolution algorithm to estimate unlogged insulin correction doses | M2 | ORIGINAL_REQUEST §R2 |
| 6 | R2 Imputation DB & API Integration | Store/flag imputed doses and expose via `/api/insulin/history?include_imputed=true` | M2 | ORIGINAL_REQUEST §R2 |
| 7 | R2 Dashboard Visual Indicators | Chart.js `insulinChart` dashed border/fill styling, legend indicator, and tooltips for imputed doses | M2 | ORIGINAL_REQUEST §R2 |
| 8 | R3 Time-of-Day Nutritional Impact Model | Model food/meal blood sugar impact modifiers ($M_{\text{tod}}$) across Morning, Afternoon, Evening, Night | M3 | ORIGINAL_REQUEST §R3 |
| 9 | R3 Nutritional Impact API & UI | `/api/nutritional-impact` endpoints and glassmorphic dashboard panel on `templates/index.html` | M3 | ORIGINAL_REQUEST §R3 |
| 10 | Final E2E Integration & Verification | 100% E2E test suite passing + Tier 5 adversarial testing + Forensic Integrity Audit | M4 | System Protocol |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M0 | E2E Testing Track | Requirement-driven test suite (Tiers 1-4), test runner, publish `TEST_READY.md` | none | DONE |
| M1 | R1 Literature-Backed Dietary Analysis | Anomaly detection, PubMed/OpenAlex client, `dietary_remedies_report.md` generator | none | DONE |
| M2 | R2 Missing Dose Imputation Integration | Imputation PD model, DB schema/flag, API query, Chart.js visual indicators | none | DONE |
| M3 | R3 Time-of-Day Nutritional Impact Model | Diurnal meal impact model, `/api/nutritional-impact` API, dashboard UI panel | none | DONE |
| M4 | Final E2E Integration & Integrity Audit | Validate 100% E2E tests pass, Tier 5 adversarial hardening, Forensic Audit | M0, M1, M2, M3 | DONE |

## Interface Contracts

### 1. Literature Analysis Engine & Report Generator (M1)
- Output File: `dietary_remedies_report.md` at workspace root.
- Required Sections: Executive Summary, Observed Glycemic Trends & Anomalies, Literature-Backed Dietary Interventions (with PubMed PMID & OpenAlex DOI links), Actionable Plan.
- CLI / Module interface: `python dietary_analysis.md` or `from dietary_analysis import generate_report`.

### 2. Missing Dose Imputation (M2)
- Imputation API: GET `/api/insulin/history?include_imputed=true`
- DB Record Flag: `is_imputed: boolean` (default false), `confidence_score: float`.
- JSON Response schema for insulin records:
  ```json
  {
    "id": 123,
    "timestamp": "2026-08-04T07:00:00Z",
    "rapid_acting": 2.5,
    "long_acting": 0.0,
    "correction": 2.5,
    "is_imputed": true,
    "confidence_score": 0.88
  }
  ```
- Frontend Chart.js `insulinChart`: Imputed bars MUST have `borderDash: [5, 5]`, distinct semi-transparent color (e.g. `rgba(168, 85, 247, 0.4)`), custom tooltip label ("Estimated Imputed Dose"), and legend entry.

### 3. Time-of-Day Nutritional Impact Model (M3)
- API Endpoint: GET `/api/nutritional-impact`
- JSON Response schema:
  ```json
  {
    "time_buckets": {
      "Morning": {"peak_rise_mgdl": 45.2, "peak_latency_min": 55, "modifier": 1.25},
      "Afternoon": {"peak_rise_mgdl": 35.0, "peak_latency_min": 45, "modifier": 1.00},
      "Evening": {"peak_rise_mgdl": 40.1, "peak_latency_min": 50, "modifier": 1.10},
      "Night": {"peak_rise_mgdl": 52.8, "peak_latency_min": 75, "modifier": 1.40}
    },
    "recommendations": [...]
  }
  ```
- UI Exposure: Glassmorphic panel on `templates/index.html` displaying circadian impact modifiers for Morning, Afternoon, Evening, Night.

## Code Layout & Write Boundaries
- `e2e_tests/`: Owned exclusively by E2E Testing Track subagents.
- `dietary_analysis.py`, `literature_api.py`: Owned exclusively by M1 subagents.
- `imputation.py`: Owned exclusively by M2 subagents.
- `nutritional_model.py`: Owned exclusively by M3 subagents.
- Shared files (`app.py`, `db.py`, `schema.sql`, `templates/index.html`): Modifications coordinated by milestone owners to prevent merge conflicts.
