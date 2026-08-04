# Technical Design & Analysis: Pharmacodynamic Deconvolution for Missing Insulin Dose Imputation

## Executive Summary
This document defines the mathematical formulation, confidence scoring mechanism, and architectural blueprint for the backend pharmacodynamic (PD) deconvolution model required for Milestone M2 (Requirement R2: Missing Dose Imputation Integration). 

The goal of this system is to identify unlogged insulin correction doses by analyzing surrounding continuous glucose monitoring (CGM) trends. When an unexplained sharp drop in blood glucose occurs that cannot be accounted for by logged insulin doses or expected physiological drift, the algorithm inverts the Scheiner parabolic insulin decay curve bounded by time-of-day Insulin Sensitivity Factors (ISFs) to estimate the missing insulin dose (in Units) and assign a calibrated confidence score ($C \in [0.0, 1.0]$).

---

## 1. Audit of Existing Baseline Codebase

### 1.1 `prediction.py` (Insulin-on-Board & Decay Curves)
- **Scheiner Parabolic Decay Model**:
  `calculate_iob(doses, current_time, action_duration_mins=240)` implements Scheiner's parabolic decay equation:
  $$IOB(t) = \sum_{k} U_k \cdot \left(1 - \frac{t - t_k}{T}\right)^2 \quad \text{for } 0 \le t - t_k \le T$$
  where $U_k$ is the rapid-acting insulin dose administered at time $t_k$, $T = 240$ minutes (4 hours) is the total insulin action duration, and $t$ is the current evaluation timestamp.
- **Cumulative Insulin Action**:
  The fraction of insulin action exerted by elapsed time $\Delta t = t - t_k$ is:
  $$F_{\text{act}}(\Delta t) = 1 - \frac{IOB(\Delta t)}{U} = 1 - \left(1 - \frac{\Delta t}{T}\right)^2 = \frac{2\Delta t}{T} - \left(\frac{\Delta t}{T}\right)^2$$
- **Correction Suggestion Logic**:
  `suggest_correction()` calculates required bolus using:
  $$\text{Correction} = \max\left(0, \frac{G_{\text{current}} - G_{\text{target}}}{ISF} - IOB\right)$$

### 1.2 `ml_heuristics.py` (Circadian Time-of-Day Buckets & ISF Computation)
- **Time-of-Day Bucketing**:
  `get_time_of_day_bucket(dt, timezone_str)` divides the 24-hour day into 4 circadian windows:
  - `morning`: 04:00 – 10:59
  - `afternoon`: 11:00 – 16:59
  - `evening`: 17:00 – 21:59
  - `night`: 22:00 – 03:59
- **ISF Computation & Fallbacks**:
  `calculate_personalized_isf(hours_back)` inspects pure correction events (no stacking within 4 hours, no meal carbs) and computes empirical ISF as:
  $$ISF_{\text{empirical}} = \frac{G_{\text{start}} - G_{\text{end}}}{U_{\text{dose}}}$$
  If empirical data is sparse (< 3 events per bucket), it falls back to global average ISF or default $50.0 \text{ mg/dL/U}$.

### 1.3 `db.py` & `schema.sql` (Database Persistence Layer)
- `glucose_readings`: Stores `timestamp`, `value` (mg/dL), `type` (`historic`, `scan`, `live`).
- `insulin_doses`: Stores `timestamp`, `rapid_acting`, `long_acting`, `meal`, `correction`, `user_change`.
- Current `insulin_doses` table lacks fields to distinguish imputed entries from user-logged entries.

---

## 2. Pharmacodynamic Deconvolution Mechanics

### 2.1 Forward vs. Inverse Glucose Response Model
In forward modeling:
$$\Delta G(t) = G(t) - G(t_0) = - ISF(t_0) \int_{t_0}^t h(\tau - t_d) \cdot U \, d\tau$$
where $h(\Delta t) = -\frac{d}{d\Delta t} \left(1 - \frac{\Delta t}{T}\right)^2 = \frac{2}{T}\left(1 - \frac{\Delta t}{T}\right)$ is the instantaneous pharmacodynamic rate of action.

In inverse modeling (deconvolution):
Given an observed glucose drop $\Delta G_{\text{obs}} = G(t_{\text{start}}) - G(t_{\text{nadir}})$ over interval $[t_{\text{start}}, t_{\text{nadir}}]$, we isolate the component attributable to unlogged insulin by removing the effect of known logged IOB:
$$\Delta G_{\text{unexplained}} = \Delta G_{\text{obs}} - \Delta G_{\text{logged\_iob}}$$
$$\Delta G_{\text{logged\_iob}} = ISF(t_{\text{start}}) \cdot \left[ IOB_{\text{logged}}(t_{\text{start}}) - IOB_{\text{logged}}(t_{\text{nadir}}) \right]$$

The missing dose $U_{\text{imputed}}$ administered at $t_d \approx t_{\text{start}}$ is derived by inverting the cumulative action fraction $F_{\text{act}}$:
$$U_{\text{imputed}} = \frac{\Delta G_{\text{unexplained}}}{ISF(t_{\text{start}}) \cdot \left[ F_{\text{act}}(t_{\text{nadir}} - t_d) - F_{\text{act}}(t_{\text{start}} - t_d) \right]}$$

---

## 3. Detailed Mathematical Formulation & Algorithm Specification

### 3.1 Step-by-Step Algorithm Workflow

```
[ Glucose History ] ──> [ Sliding Window Drop Detector ]
                                 │
                                 ▼
                     [ Logged IOB Subtraction ]
                                 │
                                 ▼
                    [ Net Unexplained Drop? ] ──(No / < 15 mg/dL)──> [ Skip ]
                                 │ (Yes)
                                 ▼
                  [ ISF Time-of-Day Lookup ]
                                 │
                                 ▼
                [ Invert Scheiner Action Curve ] ──> [ Dose U_imputed ]
                                 │
                                 ▼
                 [ Multi-Factor Confidence Score ]
                                 │
                   (Confidence >= 0.50 & No Clashes)
                                 │
                                 ▼
                [ Imputed Insulin Record Created ]
```

### 3.2 Detection Criteria & Thresholds
1. **Sliding Window Search**:
   - Window duration $\Delta T_{\text{window}} \in [45 \text{ min}, 240 \text{ min}]$.
   - Glucose drop magnitude: $\Delta G_{\text{obs}} = G(t_{\text{start}}) - G(t_{\text{nadir}}) \ge 20.0 \text{ mg/dL}$.
   - Minimum drop rate: $R_{\text{drop}} = \frac{\Delta G_{\text{obs}}}{\Delta T_{\text{window}}} \ge 0.40 \text{ mg/dL/min}$.
2. **Net Unexplained Threshold**:
   - $\Delta G_{\text{unexplained}} = \Delta G_{\text{obs}} - \max(0, \Delta G_{\text{logged\_iob}}) \ge 15.0 \text{ mg/dL}$.
3. **Dose Bounds**:
   - Estimated dose $U_{\text{imputed}}$ is clamped to $[0.5 \text{ U}, 15.0 \text{ U}]$ and rounded to 1 decimal place.

### 3.3 Multi-Factor Confidence Scoring Model ($C \in [0.0, 1.0]$)

$$C = 0.35 \cdot C_{\text{magnitude}} + 0.30 \cdot C_{\text{shape}} + 0.20 \cdot C_{\text{hyper}} + 0.15 \cdot C_{\text{no\_carb}}$$

#### Factor 1: Unexplained Drop Magnitude ($C_{\text{magnitude}}$)
$$C_{\text{magnitude}} = \min\left(1.0, \frac{\Delta G_{\text{unexplained}}}{60.0}\right)$$
- Drops of $60 \text{ mg/dL}$ or more yield full magnitude confidence (1.0).

#### Factor 2: Curve Monotonicity & Shape Fit ($C_{\text{shape}}$)
Let $M$ be the total consecutive 15-minute readings in the drop window, and $k_{\text{down}}$ be the number of steps where $G(t_{m+1}) < G(t_m)$:
$$C_{\text{shape}} = \frac{k_{\text{down}}}{M}$$
- A monotonically descending curve without noisy reversals scores close to 1.0.

#### Factor 3: Starting Hyperglycemia Level ($C_{\text{hyper}}$)
Correction boluses are clinically given when blood sugar is elevated:
$$C_{\text{hyper}} = \begin{cases} 
1.0 & \text{if } G(t_{\text{start}}) \ge 180 \text{ mg/dL} \\
0.5 + 0.5 \cdot \frac{G(t_{\text{start}}) - 140}{40} & \text{if } 140 \le G(t_{\text{start}}) < 180 \text{ mg/dL} \\
0.3 & \text{if } G(t_{\text{start}}) < 140 \text{ mg/dL}
\end{cases}$$

#### Factor 4: Meal & Refractory Isolation ($C_{\text{no\_carb}}$)
- If a logged meal or rapid dose occurred within $[-60 \text{ min}, +30 \text{ min}]$ of $t_{\text{start}}$, $C_{\text{no\_carb}} = 0.40$ (potential confounding meal dynamics).
- Otherwise, $C_{\text{no\_carb}} = 1.00$.

#### Acceptance Gate:
Only candidate imputations with $C \ge 0.50$ and no existing logged/imputed dose within $\pm 30$ minutes are accepted.

---

## 4. Architectural & Schema Migration Blueprint

### 4.1 Database Schema Additions (`schema.sql` & `db.py`)
To support imputed insulin doses alongside user-logged doses, the `insulin_doses` table requires two new columns:
```sql
ALTER TABLE insulin_doses ADD COLUMN IF NOT EXISTS is_imputed BOOLEAN DEFAULT FALSE;
ALTER TABLE insulin_doses ADD COLUMN IF NOT EXISTS confidence_score DOUBLE PRECISION DEFAULT NULL;
```

### 4.2 Module Design (`imputation.py`)
A new dedicated Python module `imputation.py` will be created with the following key functions:
1. `run_missing_dose_imputation(hours_back=720, min_confidence=0.50)`:
   - Fetches historical glucose and insulin logs.
   - Evaluates sliding windows to locate unexplained drops.
   - Computes $U_{\text{imputed}}$ via PD deconvolution and calculates confidence score $C$.
   - Returns list of formatted imputed dose dictionaries.
2. `sync_imputed_doses_to_db(hours_back=720)`:
   - Purges old imputed doses in the timeframe to allow idempotent re-computation.
   - Inserts newly imputed doses into `insulin_doses` with `is_imputed=True` and `confidence_score`.

### 4.3 API Endpoint Contract (`app.py`)
- Endpoint: `GET /api/insulin/history?include_imputed=true`
- Query Parameter: `include_imputed: bool` (default: `false`).
- JSON Response Schema:
```json
[
  {
    "id": 101,
    "timestamp": "2026-08-03T14:30:00Z",
    "rapid_acting": 2.5,
    "long_acting": 0.0,
    "meal": 0.0,
    "correction": 2.5,
    "user_change": 0.0,
    "device": "ImputationEngine_PD_v1",
    "serial_number": null,
    "is_imputed": true,
    "confidence_score": 0.82
  }
]
```

---

## 5. Summary Matrix of Design Parameters

| Parameter | Value / Formula | Rationale / Source |
|---|---|---|
| **Decay Model** | Scheiner Parabolic: $(1 - \Delta t / 240)^2$ | Co-located in `prediction.py:128` |
| **ISF Lookup** | `get_time_of_day_bucket()` + `load_heuristics_params()` | Co-located in `ml_heuristics.py:42` |
| **Drop Window** | 45 min to 240 min | Covers rapid-acting insulin peak & action tail |
| **Min Unexplained Drop** | 15.0 mg/dL net drop | Filters out sensor noise & minor fluctuations |
| **Min Confidence Cutoff** | 0.50 | Ensures only statistically robust doses are imputed |
| **Refractory Window** | $\pm 30$ min from existing doses | Prevents double-counting around logged entries |
