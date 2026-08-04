# Algorithmic & Technical Design Analysis: Gluco Track Advanced Features (R1, R2, R3)

**Author:** explorer_2  
**Target Project:** Gluco Track (`c:\Users\tugha\Documents\antigravity\noble-galileo`)  
**Date:** 2026-08-04  
**Status:** Completed Investigation  

---

## Executive Summary

This document provides a comprehensive technical, mathematical, and algorithmic design specification for the three feature enhancements defined in `ORIGINAL_REQUEST.md`:
1. **R1: Literature-Backed Dietary Analysis Engine**: Automated detection of 6 specific metabolic trend anomalies from historical glucose and insulin telemetry, programmatically mapped to PubMed, OpenAlex, and Europe PMC scientific APIs, yielding a structured markdown report (`dietary_remedies_report.md`).
2. **R2: Missing Dose Imputation Integration**: A hybrid Pharmacodynamic (PD) Inversion and Statistical Regression engine to identify unlogged historical correction doses, backed by database schema extensions and distinct visual dataset rendering on Chart.js dashboard charts.
3. **R3: Time-of-Day Nutritional Impact Model**: A parametric mathematical formulation and linear interaction model quantifying diurnal variation in carbohydrate glycemic impact across 4 temporal buckets (`morning`, `afternoon`, `evening`, `night`), integrated into FastAPI endpoints and interactive dashboard UI elements.

---

## Section 1: Requirement R1 — Literature-Backed Dietary Analysis Engine

### 1.1 Scientific Literature APIs Architectural Evaluation

To generate evidence-based dietary recommendations, the system must interface with external medical literature APIs. Below is a comparative evaluation of primary scientific endpoints:

| Feature / Metric | NCBI PubMed (E-utilities) | OpenAlex API | Europe PMC REST API |
| :--- | :--- | :--- | :--- |
| **Base Endpoint** | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/` | `https://api.openalex.org/works` | `https://www.ebi.ac.uk/europepmc/webservices/rest/search` |
| **Authentication** | Optional API Key (`NCBI_API_KEY`) | Free (Polite email header recommended) | None required |
| **Rate Limit (No Key)** | 3 requests / second | 10 requests / second | 10 requests / second |
| **Rate Limit (With Key)**| 10 requests / second | High-throughput pool available | Unlimited standard usage |
| **Response Format** | XML, JSON (`retmode=json` for `esummary`) | JSON (Native REST JSON) | JSON (`format=json`) |
| **Query Features** | MeSH vocabulary, field tags (`[tiab]`, `[mesh]`) | Full-text title/abstract, concepts filtering | Full-text boolean, MeSH, open access filter |
| **Abstract Payload** | Available via `efetch.fcgi` (XML) | Inverted Index format (requires reconstruction) | Direct text string |

#### Primary API Selection & Resilience Strategy
- **Primary Endpoint**: **NCBI PubMed via E-utilities** (`esearch.fcgi` + `esummary.fcgi` / `efetch.fcgi`). PubMed provides gold-standard peer-reviewed medical indexing with standardized MeSH terms.
- **Fallback Endpoint**: **OpenAlex API** or **Europe PMC API**. If PubMed returns rate-limiting HTTP 429 or network timeouts, the query engine fails over to OpenAlex with polite header `User-Agent: GlucoTrack/1.0 (mailto:admin@glucotrack.local)`.
- **Implementation Strategy**:
  1. Use standard Python `requests` library (already present in `requirements.txt`).
  2. Implement an exponential backoff decorator (`max_retries=3`, `backoff_factor=1.5`).
  3. Cache API results locally in PostgreSQL or JSON cache to avoid redundant network calls during report re-generation.

---

### 1.2 Glucose & Insulin Trend Detection Strategy

The system reads historical records from `glucose_readings` and `insulin_doses` over a configurable window (default: 14 to 30 days) and runs an anomaly detection pipeline evaluating 6 clinical metabolic patterns:

```
                          ┌───────────────────────────┐
                          │  PostgreSQL Database      │
                          │  glucose_readings + doses │
                          └─────────────┬─────────────┘
                                        │ 14-30 Day Window
                                        ▼
                          ┌───────────────────────────┐
                          │  Trend Detection Pipeline │
                          └─────────────┬─────────────┘
                                        │
     ┌──────────────────┬───────────────┼───────────────┬──────────────────┐
     ▼                  ▼               ▼               ▼                  ▼
┌──────────────┐ ┌─────────────┐ ┌──────────────┐ ┌─────────────┐ ┌──────────────────┐
│ 1. Dawn      │ │ 2. Postprand│ │ 3. Nocturnal │ │ 4. Glycemic │ │ 5. Insulin       │
│ Phenomenon   │ │ Hyperglycemia│ │ Hypoglycemia │ │ Variability │ │ Resistance / ISF │
└──────────────┘ └─────────────┘ └──────────────┘ └─────────────┘ └──────────────────┘
```

#### 1. Dawn Phenomenon Engine
- **Clinical Definition**: A recurrent rise in fasting blood glucose between 04:00 and 08:00 without preceding nocturnal hypoglycemia.
- **Algorithm**:
  $$\Delta G_{\text{dawn}} = G_{\text{max}}(04:00 - 08:00) - G(03:00 - 04:00)$$
  - Trigger Condition: $\Delta G_{\text{dawn}} \ge 25 \text{ mg/dL}$, $G_{\text{min}}(00:00 - 04:00) \ge 70 \text{ mg/dL}$, and no carbohydrate/insulin dose logged within 4 hours prior.
  - Occurrence Threshold: Detected on $\ge 30\%$ of analyzed days.

#### 2. Postprandial Hyperglycemia Engine
- **Clinical Definition**: Excessive glucose excursion following meal intake.
- **Algorithm**:
  $$\Delta G_{\text{pp}} = G_{\text{peak}}(t_{\text{meal}}, t_{\text{meal}} + 180\text{m}) - G(t_{\text{meal}})$$
  - Trigger Condition: $G_{\text{peak}} > 180 \text{ mg/dL}$ OR $\Delta G_{\text{pp}} \ge 50 \text{ mg/dL}$.
  - Occurrence Threshold: Present in $> 40\%$ of logged meal windows.

#### 3. Nocturnal Hypoglycemia Engine
- **Clinical Definition**: Blood glucose dropping below safety limits during sleep.
- **Algorithm**:
  - Scan window: 00:00 to 06:00.
  - Trigger Condition: Continuous block of $\ge 15$ minutes with $G(t) < 70 \text{ mg/dL}$ (Level 1) or $G(t) < 54 \text{ mg/dL}$ (Level 2).
  - Occurrence Threshold: $\ge 2$ episodes over a 14-day window.

#### 4. Glycemic Variability (GV) Engine
- **Clinical Definition**: High glucose fluctuations increasing risk of vascular complications.
- **Algorithm**:
  $$\text{CV} = \left( \frac{\sigma_{\text{glucose}}}{\mu_{\text{glucose}}} \right) \times 100\%$$
  - Trigger Condition: $\text{CV} > 36\%$ OR Standard Deviation $\sigma > 40 \text{ mg/dL}$.

#### 5. Insulin Resistance / Low ISF Engine
- **Clinical Definition**: Blunted glucose lowering response per unit of rapid-acting insulin.
- **Algorithm**:
  $$\text{ISF}_{\text{empirical}} = \frac{G(t_0) - G(t_0 + 4\text{h})}{\text{Dose}_{\text{correction}}}$$
  - Trigger Condition: Calculated empirical ISF $< 30 \text{ mg/dL/U}$ or total daily dose (TDD) $> 0.8 \text{ U/kg/day}$.

#### 6. Rebound Hyperglycemia (Somogyi Effect) Engine
- **Clinical Definition**: Nocturnal hypoglycemia triggering stress hormone counter-regulation leading to morning spike.
- **Algorithm**:
  - Trigger Condition: Nocturnal nadir $G(01:00 - 04:00) < 70 \text{ mg/dL}$ followed by morning peak $G(06:00 - 09:00) > 200 \text{ mg/dL}$.

---

### 1.3 Literature Search Strategy & Query Mapping

When an anomaly threshold is triggered, the search engine constructs structured medical queries:

| Detected Trend Pattern | Primary PubMed / OpenAlex Query String | Target Dietary Intervention Focus |
| :--- | :--- | :--- |
| **Dawn Phenomenon** | `("dawn phenomenon" OR "morning hyperglycemia") AND ("dietary intervention" OR "bedtime snack" OR "macronutrient timing" OR "protein snack")` | Bedtime low-GI protein/fat snacks, late-night carb restriction |
| **Postprandial Hyperglycemia** | `("postprandial hyperglycemia" OR "glucose excursion") AND ("glycemic index" OR "dietary fiber" OR "vinegar" OR "food sequencing")` | Pre-meal fiber (psyllium/inulin), acetic acid (vinegar), meal sequencing (protein/veg before carbs) |
| **Nocturnal Hypoglycemia** | `("nocturnal hypoglycemia" OR "nighttime hypoglycemia") AND ("uncooked cornstarch" OR "complex carbohydrate" OR "bedtime nutrition")` | Uncooked cornstarch, slow-release complex carbohydrates before sleep |
| **High Glycemic Variability** | `("glycemic variability" OR "glucose fluctuations") AND ("low glycemic index diet" OR "mediterranean diet" OR "dietary fiber")` | High viscous fiber, ultra-processed food elimination, low-GI whole foods |
| **Insulin Resistance** | `("insulin resistance" OR "insulin sensitivity") AND ("intermittent fasting" OR "polyphenols" OR "magnesium" OR "berberine")` | Polyphenol-rich foods, magnesium supplementation, caloric front-loading |

#### Filtering & Ranking Criteria
1. **Article Type Filter**: Prioritize Randomized Controlled Trials (RCTs), Systematic Reviews, Meta-Analyses (`"Clinical Trial"[Publication Type] OR "Meta-Analysis"[Publication Type]`).
2. **Recency Weighting**: Publication date $\ge 2015$ preferred.
3. **Relevancy Score**:
   $$\text{Score} = \text{MatchKeywords} + 2.0 \times \mathbb{I}_{\text{RCT/Meta}} + \ln(1 + \text{Citations}) - 0.05 \times (\text{CurrentYear} - \text{PubYear})$$

---

### 1.4 Output Structure for `dietary_remedies_report.md`

The generator exports `dietary_remedies_report.md` in the working directory using the following exact schema:

```markdown
# Literature-Backed Dietary Analysis & Clinical Remedies Report

**Generated:** YYYY-MM-DD HH:MM UTC  
**Analysis Period:** [Start Date] to [End Date]  
**Data Volume:** N glucose readings, M insulin doses  

---

## 1. Executive Summary & Metabolic Health Profile
- **Average Glucose:** XXX mg/dL | **GMI (Est. A1c):** X.X%
- **Time in Range (70-180 mg/dL):** XX.X%
- **Glycemic Variability (CV):** XX.X% (Status: Normal / Elevated)
- **Primary Identified Anomalies:** [List of flagged trends]

---

## 2. Identified Telemetry Anomalies & Clinical Metrics
### 2.1 [Anomaly Name, e.g., Dawn Phenomenon]
- **Frequency:** Flagged on N of M days (XX%)
- **Average Excursion Magnitude:** +XX mg/dL
- **Time Window:** 04:00 - 08:00

---

## 3. Medical Literature Review & Study Evidence
### Evidence for [Anomaly Name]
1. **Title:** [Paper Title]
   - **Authors & Journal:** [Authors], *[Journal Name]*, [Year]
   - **Identifiers:** PMID: [PMID] | DOI: [DOI]
   - **Study Type:** Randomized Controlled Trial / Meta-Analysis
   - **Key Finding:** [Summary of clinical trial outcome]

---

## 4. Actionable Dietary Remedies & Interventions
### Targeted Interventions for [Anomaly Name]
- [ ] **Intervention 1 (Macronutrient Timing):** [Description]
- [ ] **Intervention 2 (Glycemic Index / Pre-bolus strategy):** [Description]
- [ ] **Intervention 3 (Nutritional Supplementation / Fiber):** [Description]

---

## 5. Clinical Disclaimer
*This report is generated automatically by Gluco Track for informational and self-management research purposes only. It does not constitute medical advice, diagnosis, or treatment.*
```

---

## Section 2: Requirement R2 — Missing Dose Imputation Integration

### 2.1 Problem Analysis & Physiological Mechanics

Type 1 diabetes users frequently omit logging correction doses when correcting acute hyperglycemia. This creates gaps in treatment history, distorts Insulin-on-Board (IOB) calculations, and degrades prediction accuracy.

To resolve this, Gluco Track requires an automated **Missing Dose Imputation Engine** that detects unexplained sharp glucose drops and estimates the unlogged rapid-acting correction units.

```
 Glucose (mg/dL)
  260 ┼───────┐ Peak Glucose
      │        ╲  Unexplained Rapid Drop
  200 │         ╲ (No Insulin Logged)
      │          ╲
  120 │           └───────────────── Nadir Glucose
      └─────────────────────────────────► Time (hours)
                ▲
                │ IMPUTED CORRECTION DOSE (Est: 2.5U)
```

---

### 2.2 Imputation Algorithms & Model Evaluation

Three candidate algorithmic formulations were evaluated for missing dose estimation:

#### Candidate 1: Pharmacodynamic (PD) Deconvolution Model (Selected)
- **Mathematical Principle**: Rapid-acting insulin clearance follows Scheiner's parabolic pharmacodynamic activity function:
  $$a(t) = -\frac{d\text{IOB}}{dt} = \frac{2 D}{\tau} \left(1 - \frac{t}{\tau}\right) \quad \text{for } 0 \le t \le \tau = 240\text{ mins}$$
  Assuming linear glucose lowering in the absence of carbohydrates:
  $$\Delta G = G_{\text{peak}} - G_{\text{nadir}} = D_{\text{estimated}} \times \text{ISF}(t_{\text{peak}})$$
- **Inversion Formula**:
  $$\hat{D}_{\text{imputed}} = \frac{G_{\text{peak}} - G_{\text{nadir}}}{\text{ISF}(t_{\text{peak}})}$$
- **Detection Trigger**:
  1. Peak glucose $G_{\text{peak}} \ge 160 \text{ mg/dL}$.
  2. Sustained negative rate of change $\frac{dG}{dt} \le -1.2 \text{ mg/dL/min}$ over $\ge 45$ continuous minutes.
  3. Nadir reached within 120-240 minutes of peak.
  4. No logged insulin dose within $[-60\text{m}, +30\text{m}]$ of $t_{\text{peak}}$.
  5. Active logged IOB at peak is insufficient to account for $>50\%$ of observed drop.

#### Candidate 2: Machine Learning Regression & Anomaly Classifier
- **Features**: $[G_{\text{peak}}, G_{\text{nadir}}, \Delta G, \text{slope}_{\text{max}}, \Delta t_{\text{drop}}, \text{IOB}_{\text{logged}}, \sin(\text{hour}), \cos(\text{hour})]$.
- **Method**: Two-stage Random Forest. Stage 1 classifies whether drop was driven by exogenous insulin vs physical activity. Stage 2 estimates dose magnitude.
- **Pros/Cons**: Higher accuracy on complex multi-variable patterns, but requires external labeled ground-truth training datasets.

#### Candidate 3: Moving Window Physiological Rule Engine
- **Method**: Simple sliding threshold checking glucose delta $\Delta G > 60 \text{ mg/dL}$ over 2 hours.
- **Pros/Cons**: Computationally lightweight but tends to overestimate doses during prolonged exercise or baseline drift.

#### Algorithmic Comparison & Recommendation

| Evaluated Dimension | Candidate 1: PD Deconvolution | Candidate 2: ML Regression | Candidate 3: Rule Engine |
| :--- | :--- | :--- | :--- |
| **Mathematical Rigor** | High (Physiological model) | High (Data-driven) | Low (Heuristic) |
| **Interpretability** | 100% Deterministic | Black-box / Non-linear | High |
| **Dependencies** | Pure Python (`math`, `datetime`) | `scikit-learn` / `numpy` | Pure Python |
| **Execution Latency** | $< 15 \text{ ms}$ | $50-200 \text{ ms}$ | $< 5 \text{ ms}$ |
| **Clinical Safety** | High (Bounded by ISF) | Medium (Risk of overfit) | Medium (False positives) |

**Decision**: **Candidate 1 (Pharmacodynamic Deconvolution)** bounded by time-of-day ISFs from `ml_heuristics.py` is selected as the primary imputation engine.

---

### 2.3 Data Schema & API Design

To distinguish actual logged doses from estimated doses in PostgreSQL:

#### 1. Schema Modifications (`schema.sql` / Migration)
Add columns to `insulin_doses`:
```sql
ALTER TABLE insulin_doses 
ADD COLUMN IF NOT EXISTS is_imputed BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS confidence_score DOUBLE PRECISION DEFAULT NULL,
ADD COLUMN IF NOT EXISTS imputation_method VARCHAR(50) DEFAULT NULL;
```

#### 2. FastAPI Endpoints (`app.py`)
- `POST /api/insulin/impute/run`: Triggers the imputation scan over historical readings (e.g. last 7 to 30 days) and persists detected missing doses with `is_imputed = TRUE`.
- `GET /api/insulin/history?hours=24&include_imputed=true`: Returns logged and imputed insulin objects:
  ```json
  [
    {
      "id": 102,
      "timestamp": "2026-08-04T03:15:00Z",
      "correction": 2.5,
      "rapid_acting": 2.5,
      "is_imputed": true,
      "confidence_score": 0.88,
      "imputation_method": "pd_deconvolution"
    }
  ]
  ```

---

### 2.4 Dashboard Visual Differentiation Specs (Chart.js)

The frontend `index.html` uses Chart.js to display glucose and insulin charts. Imputed doses must be visually distinct:

```javascript
// Chart.js Insulin Dataset Configuration
const insulinDataset = {
    label: 'Logged Insulin (U)',
    data: loggedDoses,
    backgroundColor: 'rgba(59, 130, 246, 0.85)', // Solid Blue
    borderColor: '#3b82f6',
    borderWidth: 1
};

const imputedInsulinDataset = {
    label: 'Estimated Dose (Imputed)',
    data: imputedDoses,
    backgroundColor: 'rgba(245, 158, 11, 0.35)', // Translucent Amber
    borderColor: '#f59e0b',                     // Amber Border
    borderWidth: 2,
    borderDash: [4, 4],                          // Dashed Border
    pointStyle: 'rectRot',
    tooltip: {
        callbacks: {
            label: function(context) {
                const raw = context.raw;
                return ` [ESTIMATED] ${raw.y}U Correction (Conf: ${Math.round(raw.confidence * 100)}%)`;
            }
        }
    }
};
```

---

## Section 3: Requirement R3 — Time-of-Day Nutritional Impact Model

### 3.1 Mathematical & Statistical Model Formulation

Carbohydrate glycemic impact varies dramatically throughout the day due to circadian fluctuations in cortisol, growth hormone, and insulin sensitivity. 

```
Glycemic Impact Modifier M_tod
 1.6 ┼──────────────────────────────────────────────────────── Night (1.55x)
 1.4 ┼─────────────────────────── Evening (1.30x)
 1.0 ┼────── Morning (1.0x)
 0.8 ┼─────────────────── Afternoon (0.85x)
     └───────┬───────────┬───────────┬───────────┬────────────► Time of Day
           04:00       11:00       17:00       22:00
```

#### 1. Temporal Bucketing Strategy
Aligning with `ml_heuristics.py`:
- **Morning**: 04:00 – 10:59 local time
- **Afternoon**: 11:00 – 16:59 local time
- **Evening**: 17:00 – 21:59 local time
- **Night**: 22:00 – 03:59 local time

#### 2. Postprandial Excursion Metric
For each meal event $i$ identified at time $t_i$:
$$\Delta G_{\text{pp}, i} = G_{\text{peak}}(t_i, t_i + 180\text{m}) - G(t_i)$$
$$\text{iAUC}_{3\text{h}, i} = \int_{t_i}^{t_i + 180\text{m}} \max(0, G(t) - G(t_i)) \, dt$$

#### 3. Mathematical Parametric Interaction Model
To quantify time-of-day food responsiveness independent of insulin dose, we fit a linear interaction regression:

$$\Delta G_{\text{pp}, i} = \beta_0 + \beta_{\text{carb}} \cdot C_i - \beta_{\text{ins}} \cdot D_i + \sum_{k \in \{\text{afternoon, evening, night}\}} \gamma_k \cdot (\mathbb{I}_{k, i} \cdot C_i) + \epsilon_i$$

Where:
- $C_i$: Meal carbohydrate content (g) or estimated carb proxy from meal insulin dose.
- $D_i$: Logged meal insulin units (U).
- $\mathbb{I}_{k, i}$: Indicator variable for time-of-day bucket $k$.
- $\gamma_k$: Differential carbohydrate impact coefficient relative to morning baseline.

The **Time-of-Day Glucose Impact Modifier** $M_k$ is derived as:
$$M_{\text{morning}} = 1.00$$
$$M_k = 1.00 + \frac{\gamma_k}{\beta_{\text{carb}}} \quad \text{for } k \in \{\text{afternoon}, \text{evening}, \text{night}\}$$

If $M_{\text{evening}} = 1.35$, a meal containing 50g of carbohydrates eaten in the evening produces $35\%$ greater blood sugar excursion than the exact same 50g meal eaten in the morning.

---

### 3.2 Model Diagnostics & Persistence

The model fitting logic will be added to `ml_heuristics.py`:
- Inputs: Meal events extracted from historical telemetry ($>14$ days).
- Matrix solving: Ridge Regression via existing pure-Python matrix helpers (`matmul`, `invert_matrix`).
- Persistence: Stored in `heuristics_params.json` under key `nutritional_impact`:

```json
{
  "nutritional_impact": {
    "model_trained": true,
    "modifiers": {
      "morning": 1.0,
      "afternoon": 0.88,
      "evening": 1.32,
      "night": 1.54
    },
    "mean_excursion_mgdl": {
      "morning": 42.5,
      "afternoon": 35.1,
      "evening": 58.4,
      "night": 67.2
    },
    "trained_at": "2026-08-04T07:20:00Z"
  }
}
```

---

### 3.3 Backend API Endpoints

Add new endpoints in `app.py`:
1. `GET /api/nutritional-impact/summary`: Returns the trained modifiers, mean excursions per bucket, and human-readable nutritional advice.
2. `POST /api/nutritional-impact/train`: Triggers historical meal extraction and interaction model fitting.
3. `POST /api/nutritional-impact/predict-meal`: Predicts expected 2-hour glucose peak given meal time and estimated carbs.

---

### 3.4 Dashboard UI Integration Specs

Add a dedicated glassmorphic section to `templates/index.html`:

1. **Diurnal Impact Card**: Visual bar / badge display showing relative multipliers for Morning (1.0x), Afternoon (0.88x), Evening (1.32x High Impact), Night (1.54x Critical Impact).
2. **Meal Impact Calculator Widget**: Interactive input where users select meal time and carb amount (e.g. 60g) to compute time-adjusted blood sugar impact before eating.

```html
<!-- Glass Panel: Time-of-Day Nutritional Impact -->
<div class="glass-panel">
    <h3>Time-of-Day Nutritional Impact Model</h3>
    <div class="impact-grid">
        <div class="impact-card morning">
            <span class="bucket-name">Morning (04-11)</span>
            <span class="multiplier-tag">1.0x Baseline</span>
        </div>
        <div class="impact-card evening highlight-amber">
            <span class="bucket-name">Evening (17-22)</span>
            <span class="multiplier-tag">+32% Higher Impact</span>
        </div>
    </div>
</div>
```

---

## Section 4: Architectural Integration & Implementation Roadmap

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Gluco Track Architecture                        │
└────────────────────────────────────────────────────────────────────────┘
                                    │
       ┌────────────────────────────┼────────────────────────────┐
       ▼                            ▼                            ▼
┌──────────────┐             ┌──────────────┐             ┌──────────────┐
│  Requirement │             │  Requirement │             │  Requirement │
│      R1      │             │      R2      │             │      R3      │
│ Literature   │             │ Dose Impute  │             │ Time-of-Day  │
└──────┬───────┘             └──────┬───────┘             └──────┬───────┘
       │                            │                            │
       ▼                            ▼                            ▼
┌──────────────┐             ┌──────────────┐             ┌──────────────┐
│ `dietary_    │             │ `db.py` /    │             │ `ml_         │
│ analysis.py` │             │ `schema.sql` │             │ heuristics`  │
│ (PubMed API) │             │ (PD Invert)  │             │ (Parametric) │
└──────┬───────┘             └──────┬───────┘             └──────┬───────┘
       │                            │                            │
       └────────────────────────────┼────────────────────────────┘
                                    ▼
                     ┌────────────────────────────┐
                     │   `app.py` FastAPI Server  │
                     └──────────────┬─────────────┘
                                    ▼
                     ┌────────────────────────────┐
                     │ `templates/index.html` UI  │
                     │  (Chart.js + Dashboard)    │
                     └────────────────────────────┘
```

### Implementation Task Breakdown for Downstream Implementers

1. **M1 (R1 Literature Analysis)**:
   - Create `dietary_analysis.py` module with PubMed/OpenAlex query builder and anomaly scanner.
   - Implement `dietary_remedies_report.md` file generator.
   - Add `POST /api/dietary-analysis/generate` endpoint to `app.py`.

2. **M2 (R2 Imputation Integration)**:
   - Update `schema.sql` and `db.py` to support `is_imputed`, `confidence_score`, `imputation_method`.
   - Implement PD Deconvolution algorithm in `imputation.py` or `ml_heuristics.py`.
   - Update `templates/index.html` Chart.js datasets to style imputed doses with dashed amber borders.

3. **M3 (R3 Time-of-Day Model)**:
   - Add parametric interaction regression to `ml_heuristics.py`.
   - Add `/api/nutritional-impact/*` endpoints in `app.py`.
   - Add glassmorphic "Time-of-Day Nutritional Impact" panel and interactive calculator in `templates/index.html`.

---

## Verification & Validation Plan

1. **R1 Verification**:
   - Run telemetry analysis over sample dataset.
   - Verify PubMed API calls execute successfully without 429 errors.
   - Confirm `dietary_remedies_report.md` is generated with valid PMIDs and actionable remedies.

2. **R2 Verification**:
   - Inject synthetic unlogged glucose drops into test database.
   - Verify `POST /api/insulin/impute/run` detects missing doses within $\pm 0.5\text{U}$ of target.
   - Confirm dashboard chart displays distinct amber dashed bars for imputed entries.

3. **R3 Verification**:
   - Train time-of-day model via `POST /api/heuristics/train`.
   - Confirm modifiers match expected diurnal pattern (Evening/Night > Morning/Afternoon).
   - Verify dashboard UI correctly renders modifier badges and meal simulator output.
