# Analysis: Time-of-Day Nutritional Impact Model ($M_{\text{tod}}$) & Circadian Bucketing

**Author**: Explorer 2 (Milestone 3)  
**Date**: 2026-08-04  
**Target Module**: `ml_heuristics.py`, `app.py`, `templates/index.html`

---

## 1. Executive Summary

This report provides a read-only architectural investigation and mathematical specification for **Milestone 3: Time-of-Day Nutritional Impact Model ($M_{\text{tod}}$) & Dashboard Exposure**. 

Key findings:
1. **Data Access & Schema**: Historical glucose logs are stored in `glucose_readings` (`TIMESTAMPTZ`), and meal events/boluses are stored in `insulin_doses` (`TIMESTAMPTZ`, with `meal` and `rapid_acting` columns).
2. **Circadian Grouping**: Readings and meal events map into 4 distinct time-of-day buckets using local timezone conversion:
   - **Morning**: 04:00 - 11:00 ($4 \le h < 11$)
   - **Afternoon**: 11:00 - 17:00 ($11 \le h < 17$)
   - **Evening**: 17:00 - 22:00 ($17 \le h < 22$)
   - **Night**: 22:00 - 04:00 ($h \ge 22$ or $h < 4$)
3. **$M_{\text{tod}}$ Derivation & Postprandial Math**: $M_{\text{tod}}$ is computed from postprandial glucose excursions ($\Delta G = G_{\text{peak}} - G_{\text{baseline}}$ within a 3-hour post-meal window), normalized against baseline (Afternoon or global mean rise), with unmitigated meal insulin correction.
4. **Fallback & Sparse Data**: When historical meal data per bucket is under threshold ($N_b < 3$), a 3-tier fallback hierarchy triggers: Empirical Bucket $\rightarrow$ Weighted Shrinkage / Global Mean $\rightarrow$ Physiological Literature Defaults.

---

## 2. Database Schema & Data Access Analysis

### 2.1 Table Structure & Storage (`schema.sql`)
- **`glucose_readings`**:
  - `id`: `SERIAL PRIMARY KEY`
  - `timestamp`: `TIMESTAMPTZ NOT NULL` (stored in UTC)
  - `value`: `DOUBLE PRECISION NOT NULL` (glucose in mg/dL)
  - `type`: `VARCHAR(20)` (`'historic'`, `'scan'`, `'live'`)
  - Indexed on `timestamp DESC` and unique index on `(timestamp, value)`.
- **`insulin_doses`**:
  - `id`: `SERIAL PRIMARY KEY`
  - `timestamp`: `TIMESTAMPTZ NOT NULL` (stored in UTC)
  - `rapid_acting`, `long_acting`, `meal`, `correction`, `user_change`: `DOUBLE PRECISION`
  - Indexed on `timestamp DESC`.

### 2.2 Data Ingestion & Timezone Mechanics (`parser.py`, `sync.py`, `db.py`)
- **LibreView CSV Import (`parser.py`)**:
  - Parses naive timestamps from CSV, localizes using `LIBRE_TIMEZONE` (default `America/Los_Angeles` or `America/New_York`), and converts to UTC before saving to PostgreSQL.
  - Extracts meal bolus entries (`Meal` column in LibreView CSV) into `insulin_doses`.
- **LibreLinkUp Live Sync (`sync.py`)**:
  - Fetches live readings via ISO 8601 strings and normalizes to UTC `datetime`.
- **Data Access Functions (`db.py`)**:
  - `get_history(limit_hours)`: Fetches glucose readings ordered chronologically.
  - `get_insulin_history(limit_hours)`: Fetches insulin logs ordered chronologically.

---

## 3. Circadian Bucketing Strategy

### 3.1 Time Bucket Boundaries
Local time hour $h \in [0, 23]$ determines bucket membership via half-open intervals $[t_{\text{start}}, t_{\text{end}})$:

| Bucket Name | Local Time Range | Boundary Condition ($h = \text{local\_hour}$) | Clinical Rationale |
| :--- | :--- | :--- | :--- |
| **Morning** | 04:00 – 11:00 | $4 \le h < 11$ | Dawn phenomenon, morning cortisol/growth hormone peak, high insulin resistance |
| **Afternoon** | 11:00 – 17:00 | $11 \le h < 17$ | Standard diurnal baseline, peak insulin sensitivity |
| **Evening** | 17:00 – 22:00 | $17 \le h < 22$ | Post-work meal response, moderate insulin sensitivity |
| **Night** | 22:00 – 04:00 | $h \ge 22 \lor h < 4$ | Late night snacks/nocturnal digestion, melatonin-induced insulin resistance |

### 3.2 Code Reference (`ml_heuristics.py`)
Existing helper function in `ml_heuristics.py` (lines 42–58):
```python
def get_time_of_day_bucket(dt, timezone_str="America/New_York"):
    tz = pytz.timezone(timezone_str)
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    local_dt = dt.astimezone(tz)
    hour = local_dt.hour
    
    if 4 <= hour < 11:
        return "morning"
    elif 11 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 22:
        return "evening"
    else:
        return "night"
```

---

## 4. Blood Glucose Impact Modifier ($M_{\text{tod}}$) Derivation Methodology

### 4.1 Postprandial Excursion Mathematics
For each meal event $k$ occurring at timestamp $t_{\text{meal}}$:
1. **Baseline Glucose ($G_{\text{base}}$)**: Reading closest to $t_{\text{meal}}$ within $[t_{\text{meal}} - 15\text{m}, t_{\text{meal}} + 15\text{m}]$.
2. **Postprandial Window**: $[t_{\text{meal}}, t_{\text{meal}} + 180\text{m}]$ (3 hours).
3. **Peak Glucose ($G_{\text{peak}}$)**: $\max \{ G(t) \mid t \in [t_{\text{meal}}, t_{\text{meal}} + 180\text{m}] \}$.
4. **Observed Peak Rise ($\Delta G_{\text{obs}}$)**: $\Delta G_{\text{obs}} = G_{\text{peak}} - G_{\text{base}}$ (in mg/dL).
5. **Insulin-Unmitigated Rise Estimate ($\Delta G_{\text{unmitigated}}$)**:
   If meal insulin $I_{\text{meal}}$ was administered at $t_{\text{meal}}$:
   $$\Delta G_{\text{unmitigated}} = \Delta G_{\text{obs}} + (I_{\text{meal}} \times \text{ISF}_b)$$
6. **Peak Latency ($T_{\text{peak}}$)**: Minutes elapsed from $t_{\text{meal}}$ to $G_{\text{peak}}$.

### 4.2 Deriving $M_{\text{tod}}$ per Bucket
For bucket $b \in \{\text{Morning}, \text{Afternoon}, \text{Evening}, \text{Night}\}$:
1. Average Peak Rise: $\overline{\Delta G}_b = \frac{1}{N_b} \sum_{i=1}^{N_b} \Delta G_{b, i}$.
2. Baseline Reference Rise ($\overline{\Delta G}_{\text{ref}}$): Afternoon average peak rise $\overline{\Delta G}_{\text{afternoon}}$ (or overall mean $\overline{\Delta G}_{\text{global}}$).
3. Raw Modifier:
   $$M_{\text{tod, raw}}(b) = \frac{\overline{\Delta G}_b}{\overline{\Delta G}_{\text{ref}}}$$
4. Clamped Modifier: $M_{\text{tod}}(b) = \max(0.50, \min(2.50, M_{\text{tod, raw}}(b)))$.

---

## 5. Fallback Hierarchy & Statistical Defaults for Sparse Data

When historical meal data is limited or absent, the model applies a 3-stage fallback hierarchy:

```
[Is Bucket Sample Count N_b >= 3?]
      │
      ├── YES ──> 1. Use Empirical Bucket Modifier M_tod(b)
      │
      └── NO ───> [Is Total Sample Count N_total >= 5?]
                        │
                        ├── YES ──> 2. Weighted Shrinkage (Empirical Bayes)
                        │              M_blended = w_b * M_empirical + (1 - w_b) * M_default
                        │              where w_b = N_b / (N_b + 3)
                        │
                        └── NO ───> 3. Literature / Physiological Defaults
```

### 5.1 Physiological Defaults Table
If $N_{\text{total}} < 5$, return the following medical literature defaults (based on circadian glucose tolerance studies):

| Bucket | Default $M_{\text{tod}}$ | Default Peak Rise ($\text{mg/dL}$) | Default Peak Latency ($\text{min}$) | Clinical Driver |
| :--- | :--- | :--- | :--- | :--- |
| **Morning** | `1.25` | `45.2` | `55` | Dawn phenomenon & morning cortisol surge |
| **Afternoon** | `1.00` | `35.0` | `45` | Diurnal baseline peak insulin sensitivity |
| **Evening** | `1.10` | `40.1` | `50` | Moderate evening insulin resistance |
| **Night** | `1.40` | `52.8` | `75` | Melatonin-induced glucose tolerance reduction |

---

## 6. Edge Cases & Mitigation Strategies

1. **Boundary Hour Ambiguity (04:00, 11:00, 17:00, 22:00)**:
   - Half-open condition $t_{\text{start}} \le h < t_{\text{end}}$ prevents double-counting.
   - If a 3-hour postprandial window spans across bucket boundary (e.g. meal at 10:30 AM ending at 01:30 PM), the entire excursion is assigned to the **meal start bucket** (Morning).
2. **Timezone Misalignment**:
   - `glucose_readings.timestamp` is stored in UTC. Evaluating raw UTC hours causes wrong bucket assignment (e.g. 12:00 UTC = 08:00 AM EDT).
   - **Mitigation**: ALWAYS convert UTC timestamp to local timezone (`dt.astimezone(pytz.timezone(user_tz))`) before extracting `.hour`.
3. **Insulin Stacking & Overlapping Meals**:
   - If secondary meal or correction dose occurs within the 3-hour window, excursion is confounded.
   - **Mitigation**: Filter out meal events where another dose occurs within $[t_{\text{meal}}, t_{\text{meal}} + 180\text{m}]$.
4. **Missing Sensor Data & Gaps**:
   - **Mitigation**: Require at least 6 readings ($> 50\%$ sample density) within the 180-minute window. Skip incomplete postprandial periods.
