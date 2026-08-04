# Specification Mining & Requirements Analysis Report

**Project**: Gluco Track — Advanced Glucose & Nutritional Intelligence Platform  
**Author**: spec_miner_1  
**Date**: 2026-08-04  
**Target Repository**: `c:\Users\tugha\Documents\antigravity\noble-galileo`  

---

## 1. Executive Summary & Scope

This specification analysis defines the precise functional, technical, interface, and verification requirements for three core features in Gluco Track (Noble Galileo):
1. **R1: Literature-Backed Dietary Analysis Engine** — Programmatic detection of user-specific glucose/insulin anomalies, PubMed/OpenAlex API querying, and generation of `dietary_remedies_report.md` with formal literature citations and actionable interventions.
2. **R2: Missing Dose Imputation Integration** — Algorithmic detection and estimation of missing historical insulin correction doses based on continuous glucose drops, integrated into the local Python stack and rendered with distinct visual indicators on the Chart.js dashboard timeline.
3. **R3: Time-of-Day Nutritional Impact Model Integration** — Circadian analysis of meal glucose excursions across four time-of-day buckets (Morning, Afternoon, Evening, Night), exposed via REST APIs and rendered on the live dashboard.

---

## 2. Features Discovered

| # | Category | Feature | Description | Inputs | Outputs | Error Behavior | Discovered Via |
|---|----------|---------|-------------|--------|---------|----------------|----------------|
| 1 | R1: Dietary Analysis | Anomaly Trend Detection | Analyzes historical glucose (`glucose_readings`) and insulin (`insulin_doses`) to identify postprandial spikes, dawn phenomenon, nocturnal hypos, high CV/GMI. | Database records (7 to 90 days history) | Struct array of detected pattern anomalies (type, magnitude, frequency, timestamps) | Returns empty anomaly set if history < 24h or no pattern thresholds exceeded. | `ORIGINAL_REQUEST.md`, `db.py`, `ml_heuristics.py` |
| 2 | R1: Dietary Analysis | Medical Literature API Client | Programmatically queries PubMed (NCBI E-utilities) and OpenAlex APIs for peer-reviewed studies matching identified glucose anomalies. | Search queries (e.g. anomaly type + dietary interventions) | Struct array of literature matches (PMID/DOI, Title, Authors, Journal, Year, Abstract Summary, Intervention) | Fallback to cached literature repository or graceful summary if API rate-limited / offline. | `ORIGINAL_REQUEST.md`, PubMed/OpenAlex skill specs |
| 3 | R1: Dietary Analysis | Markdown Report Synthesizer | Generates `dietary_remedies_report.md` combining user trend statistics, evidence-based dietary interventions, and literature citations. | Detected anomalies + API literature matches + User stats | `dietary_remedies_report.md` file in workspace | Raises structured exception if file write permissions fail or data is null. | `ORIGINAL_REQUEST.md` Acceptance Criteria |
| 4 | R2: Imputation Engine | Missing Dose Detector | Identifies historical glucose drop patterns (unexplained rapid drop without logged insulin) where a correction dose occurred. | `glucose_readings`, `insulin_doses` | List of missing dose event windows with timestamps and estimated drops | Ignores drops with logged insulin within 2h window; handles noisy data via smoothing filter. | `ORIGINAL_REQUEST.md` § R2 |
| 5 | R2: Imputation Engine | Correction Dose Estimator | Calculates estimated missing insulin dose in Units using user's personalized or default time-of-day ISF ($U = \Delta G / \text{ISF}$). | Glucose drop magnitude $\Delta G$, time-of-day ISF | Imputed dose records marked with `is_imputed: true`, `confidence_score`, `rationale` | Defaults to global ISF (50.0 mg/dL/U) if time-bucket ISF unavailable. | `ml_heuristics.py`, `prediction.py` |
| 6 | R2: Visual Dashboard | Insulin Chart Visual Indicators | Renders imputed doses on `insulinChart` with distinct styling (dashed borders, hatched fill, distinct legend & tooltips). | `/api/insulin/history?include_imputed=true` | Rendered Chart.js canvas with visual distinction between actual and imputed doses | Renders logged doses normally if zero imputed doses returned. | `ORIGINAL_REQUEST.md`, `templates/index.html` |
| 7 | R3: Nutritional Model | Time-of-Day Excursion Engine | Calculates food/meal glucose impact metrics ($\Delta G_{\text{peak}}$, time-to-peak, recovery rate, impact modifier) across 4 circadian buckets. | 30-90 days glucose & insulin history | Time-of-day impact parameters per bucket (Morning, Afternoon, Evening, Night) | Uses global average sensitivity if bucket sample count < 3. | `ORIGINAL_REQUEST.md`, `ml_heuristics.py` |
| 8 | R3: Visual Dashboard | Circadian Impact Panel Exposure | Displays time-of-day glucose sensitivity modifiers and meal timing guidance on `index.html` glassmorphism card. | `/api/nutritional-impact` API response | UI Panel cards showing bucket modifiers, peak latency, and advice | Displays default baseline (1.0x sensitivity) if model not trained. | `ORIGINAL_REQUEST.md`, `templates/index.html` |

---

## 3. Detailed Requirement Specifications

### 3.1 R1: Literature-Backed Dietary Analysis Engine (`dietary_remedies_report.md`)

#### Functional Requirements
1. **Trend Detection Logic**:
   - **Postprandial Spikes**: Excursions exceeding 180 mg/dL (or delta rise > 50 mg/dL within 90 minutes following a meal or dose timestamp).
   - **Dawn Phenomenon**: Glucose readings consistently rising above 130 mg/dL between 04:00 AM and 08:00 AM without preceding nocturnal hypoglycemia or late-night carb log.
   - **Nocturnal Hypoglycemia**: Glucose drops below 70 mg/dL between 23:00 PM and 06:00 AM.
   - **Glycemic Variability**: Glucose Coefficient of Variation ($CV = (\sigma / \mu) \times 100$) exceeding 36% or Time-in-Range (70-180 mg/dL) below 70%.
2. **Scientific Literature API Probing**:
   - **PubMed API (NCBI E-utilities)**:
     - Search endpoint: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={query}&retmode=json`
     - Summary endpoint: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id={pmids}&retmode=json`
   - **OpenAlex API**:
     - Endpoint: `https://api.openalex.org/works?search={query}&filter=is_oa:true`
   - Queries must pair detected anomalies with nutritional/dietary interventions (e.g., `postprandial glucose vinegar premeal`, `dawn phenomenon resistance exercise dietary protein`, `resistant starch glycemic variability`).
3. **Report Generation Standards (`dietary_remedies_report.md`)**:
   - Must be written to `dietary_remedies_report.md` in the working directory or root.
   - Section 1: **Executive Summary & User Glucose Snapshot** (Average glucose, GMI, TIR %, CV %, evaluated timeframe).
   - Section 2: **Detected Anomaly Profiles** (Detailed breakdown of detected spikes, dawn phenomenon events, nocturnal hypos with timestamps and frequencies).
   - Section 3: **Evidence-Based Dietary Interventions & Literature Mapping** (Actionable dietary strategies linked directly to user anomalies, e.g., food sequencing, vinegar ingestion, fiber fortification, low-GI swaps, evening macronutrient adjustment).
   - Section 4: **Formal Scientific Bibliography** (Numbered reference list with Title, Authors, Journal, Publication Year, and PubMed URL `https://pubmed.ncbi.nlm.nih.gov/{PMID}/` or DOI URL `https://doi.org/{DOI}`).

#### Acceptance Criteria & Verification
- `dietary_remedies_report.md` exists, is valid Markdown, and contains at least 3 distinct literature citations with working PMIDs/DOIs.
- Interventions map directly to user's empirical data statistics (not generic static text).
- Code runs locally via CLI script or API endpoint without external failures (with offline fallback if scientific APIs rate-limit).

---

### 3.2 R2: Missing Dose Imputation Integration

#### Functional Requirements
1. **Imputation Algorithm Logic**:
   - Scan historical continuous glucose readings for unannotated correction events:
     - Starting condition: Glucose peak $G_{\text{start}} \ge 180\text{ mg/dL}$.
     - Drop condition: Glucose drops by $\ge 40\text{ mg/dL}$ over a window of 60 to 180 minutes at a rate $\ge 0.8\text{ mg/dL/min}$.
     - Exclusion condition: No rapid-acting, meal, or correction insulin dose logged in `insulin_doses` within a window of $[t_{\text{start}} - 60\text{ min}, t_{\text{start}} + 60\text{ min}]$.
   - Estimated Dose Calculation:
     $$\text{Imputed Dose (U)} = \frac{G_{\text{start}} - G_{\text{trough}}}{\text{ISF}_{\text{time\_of\_day}}}$$
     where $\text{ISF}_{\text{time\_of\_day}}$ is retrieved from `ml_heuristics.load_heuristics_params()`.
2. **Database & API Schema**:
   - Endpoint: `GET /api/insulin/history?hours=N&include_imputed=true`
   - Data structure for imputed doses:
     ```json
     {
       "id": "imputed_101",
       "timestamp": "2026-08-03T14:30:00Z",
       "rapid_acting": null,
       "long_acting": null,
       "meal": null,
       "correction": 2.4,
       "user_change": null,
       "device": "Imputation Engine v1.0",
       "serial_number": null,
       "is_imputed": true,
       "confidence": 0.85,
       "rationale": "Unlogged correction event: Glucose dropped 120 mg/dL over 120m without recorded dose."
     }
     ```
3. **Visual Dashboard Integration (`templates/index.html`)**:
   - `insulinChart` (Chart.js) must add a dedicated dataset for `Imputed Correction Doses`.
   - Visual attributes:
     - Color: Semi-transparent purple/amber (`rgba(168, 85, 247, 0.7)`).
     - Border: Dashed line (`borderDash: [4, 4]`).
     - Legend item: `Imputed Correction (Estimated)`.
     - Tooltip label: `Imputed Correction: 2.4 U (Estimated - Unlogged Event)`.
4. **Local Execution Stability**:
   - Must execute in sub-100ms for 30 days of data.
   - Must not mutate stored actual doses in `insulin_doses` table (imputed doses generated dynamically or stored in a separate table/flag).

#### Acceptance Criteria & Verification
- `insulinChart` visually displays imputed doses distinct from actual logged doses.
- Imputation script runs locally without syntax errors, runtime crashes, or DB locks.

---

### 3.3 R3: Time-of-Day Nutritional Impact Model Integration

#### Functional Requirements
1. **Circadian Impact Model Logic**:
   - Segment historical meal/glucose data into 4 circadian buckets:
     - **Morning**: 04:00 – 11:00
     - **Afternoon**: 11:00 – 17:00
     - **Evening**: 17:00 – 22:00
     - **Night**: 22:00 – 04:00
   - For each bucket, compute:
     - Average postprandial peak rise ($\Delta G_{\text{peak}} = G_{\text{max}} - G_{\text{baseline}}$).
     - Time to peak latency ($T_{\text{peak}}$ in minutes).
     - Time-of-day Glucose Impact Modifier ($M_{\text{bucket}} = \Delta G_{\text{bucket}} / \Delta G_{\text{global\_avg}}$).
     - Relative Carbohydrate Sensitivity Index (e.g., Morning = 1.35x, Afternoon = 0.90x, Evening = 1.10x, Night = 1.45x).
2. **API Endpoint (`/api/nutritional-impact`)**:
   - Returns JSON payload:
     ```json
     {
       "model_status": "trained",
       "global_avg_rise": 45.2,
       "time_of_day_impacts": {
         "morning": { "impact_modifier": 1.35, "avg_peak_rise": 61.0, "time_to_peak_mins": 45, "sensitivity": "High" },
         "afternoon": { "impact_modifier": 0.88, "avg_peak_rise": 39.8, "time_to_peak_mins": 60, "sensitivity": "Normal" },
         "evening": { "impact_modifier": 1.12, "avg_peak_rise": 50.6, "time_to_peak_mins": 75, "sensitivity": "Moderate" },
         "night": { "impact_modifier": 1.40, "avg_peak_rise": 63.3, "time_to_peak_mins": 90, "sensitivity": "Very High" }
       },
       "recommendation": "Morning meals produce 35% higher glycemic excursions. Consider higher fiber/protein pre-loads."
     }
     ```
3. **Dashboard Exposure (`templates/index.html`)**:
   - Expose a dedicated glassmorphism panel: **"Time-of-Day Nutritional Impact"**.
   - Render impact modifiers for all 4 time buckets with visual badges (e.g., Red for > 1.2x high sensitivity, Green for normal).
   - Display recommended timing adjustments and peak latency.

#### Acceptance Criteria & Verification
- API endpoint `/api/nutritional-impact` returns valid JSON with all 4 time-of-day buckets.
- Dashboard fetches and displays these outputs clearly in the UI.

---

## 4. Edge Cases & Boundary Conditions Matrix

| # | Feature | Input Condition | Expected System Behavior |
|---|---------|-----------------|--------------------------|
| 1 | R1: Dietary Analysis | History < 24 hours or empty database | Report generator creates baseline report with warning notice "Insufficient data for full anomaly profiling", using clinical defaults. |
| 2 | R1: Literature Search | PubMed API offline / rate-limited (429 HTTP) | Catches HTTP error, logs warning, and switches to fallback local scientific citation database without crashing. |
| 3 | R1: Anomaly Detection | Continuous glucose readings with high noise / artifacts | Applies 5-point moving median filter before detecting peak spikes to prevent false positive anomaly flags. |
| 4 | R2: Imputation | User logged dose 10 minutes AFTER drop started | Imputation engine excludes event (classified as manually logged dose with delayed timestamp). |
| 5 | R2: Imputation | Glucose drop occurs during severe hypoglycemia (< 50 mg/dL) | Excludes from insulin dose imputation (classified as carb treatment recovery / compression artifact). |
| 6 | R2: Visual Chart | Zero imputed doses present in requested timeframe | `insulinChart` renders standard logged dose dataset without error; imputed dataset remains empty. |
| 7 | R3: Nutritional Model | Bucket has < 3 meal events in historical period | Falls back to global average impact modifier (1.0x) and displays "Insufficient data for bucket" indicator. |
| 8 | R3: Dashboard UI | API call to `/api/nutritional-impact` fails | Dashboard displays gracefully formatted fallback state with cached parameters and retry button. |

---

## 5. Interface Contracts & Data Formats

### 5.1 Component Boundaries
1. **`dietary_analysis.py`** (R1 Engine):
   - Imports: `db`, `ml_heuristics`, `requests`
   - Exports: `generate_dietary_report(history_days=30) -> str`, `query_literature_apis(query: str) -> list[dict]`
2. **`imputation_engine.py`** (R2 Engine):
   - Imports: `db`, `ml_heuristics`
   - Exports: `detect_and_impute_doses(hours=720) -> list[dict]`
3. **`nutritional_model.py`** (R3 Engine):
   - Imports: `db`, `ml_heuristics`
   - Exports: `analyze_circadian_nutritional_impact(history_days=30) -> dict`
4. **`app.py`** (FastAPI Layer):
   - Exposes `/api/reports/dietary`, `/api/insulin/imputed`, `/api/nutritional-impact` endpoints and serves `templates/index.html`.
5. **`templates/index.html`** (Front-End Layer):
   - Calls FastAPI endpoints and renders Chart.js charts and glassmorphism card panels.

---

## 6. Verification Methods

1. **R1 Verification**:
   - Run report generation script/API.
   - Inspect generated `dietary_remedies_report.md` for required headers, user statistical metrics, actionable remedies, and valid PubMed/DOI URLs.
2. **R2 Verification**:
   - Query `/api/insulin/history?include_imputed=true` with sample glucose drop dataset.
   - Open browser dashboard `http://localhost:8000/` and verify `insulinChart` renders dashed/distinct visual bars for imputed doses with hover tooltips.
3. **R3 Verification**:
   - Query `GET /api/nutritional-impact` and verify JSON format.
   - Inspect dashboard panel for Morning, Afternoon, Evening, and Night impact modifier cards.
