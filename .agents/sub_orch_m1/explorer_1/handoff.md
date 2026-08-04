# Handoff Report — Explorer 1 (Milestone M1 / Requirement R1)

## 1. Observation

### Existing Codebase Investigation & References
- **Working Directory**: `c:\Users\tugha\Documents\antigravity\noble-galileo`
- **Reference Documents**:
  - `ORIGINAL_REQUEST.md`: Requirement R1 (Literature-Backed Dietary Analysis & Report Generator).
  - `orchestrator/PROJECT.md`: Architecture specification, feature inventory (M1-M4), interface contracts.
  - `sub_orch_m1/SCOPE.md`: Scope for Milestone M1 (`dietary_analysis.py`, `literature_api.py`, `dietary_remedies_report.md`).

### 1. Database Schema & Data Models (`schema.sql`, `db.py`)
- **`glucose_readings` Table (`schema.sql`:3-16)**:
  - Columns: `id` (SERIAL PRIMARY KEY), `timestamp` (TIMESTAMPTZ NOT NULL, UTC), `value` (DOUBLE PRECISION NOT NULL, mg/dL), `type` (VARCHAR(20) NOT NULL: `'historic'`, `'scan'`, `'live'`), `device` (VARCHAR(100)), `serial_number` (VARCHAR(100)), `record_type` (INTEGER: 0 = continuous historic, 1 = manual scan), `created_at` (TIMESTAMPTZ).
  - Unique Constraint: `UNIQUE (timestamp, value)`.
  - Index: `idx_glucose_readings_timestamp ON glucose_readings (timestamp DESC)`.
- **`insulin_doses` Table (`schema.sql`:22-40)**:
  - Columns: `id`, `timestamp` (TIMESTAMPTZ), `rapid_acting`, `long_acting`, `meal`, `correction`, `user_change` (DOUBLE PRECISION), `device`, `serial_number`.
  - Unique Constraint: `UNIQUE (timestamp, rapid_acting, long_acting, meal, correction, user_change)`.
- **Database Access API (`db.py`)**:
  - `get_history(limit_hours=24)` (`db.py`:117): Fetches glucose readings within last N hours ordered chronologically (`timestamp ASC`) as `psycopg2.extras.RealDictCursor` objects. Returns list of dicts with keys: `id`, `timestamp` (datetime in UTC), `value`, `type`, `device`, `serial_number`.
  - `get_statistics(hours=24)` (`db.py`:135): Calculates mean glucose, GMI ($3.31 + 0.02392 \times \text{mean}$), and Time-In-Range percentages (<70 mg/dL, 70-180 mg/dL, >180 mg/dL, >250 mg/dL).
  - `get_insulin_history(limit_hours=24)` (`db.py`:217): Fetches chronological insulin log records.

### 2. Ingestion & Parsing Modules (`parser.py`, `sync.py`, `import_csv.py`)
- **LibreView CSV Parsing (`parser.py`:10-186)**:
  - `parse_libreview_csv(file_path, timezone_str)` handles UTF-8 BOM (`utf-8-sig`) exports from FreeStyle LibreView.
  - Dynamically detects header row containing "Timestamp" and "Device".
  - Converts naive timestamps using `pytz.timezone(timezone_str)` to UTC (`astimezone(pytz.utc)`).
  - Distinguishes continuous historic readings (`record_type=0`) and manual NFC scans (`record_type=1`).
- **Live Sync Daemon (`sync.py`:32-242)**:
  - `LibreLinkUpClient` authenticates against Abbott LibreLinkUp REST API (`api-us.libreview.io`).
  - Computes `Account-Id` SHA-256 hash header for API version 4.16.0 compatibility.
  - Inserts live readings into `glucose_readings` table with `type="live"`.

### 3. Analytics & Timezone Conventions (`ml_heuristics.py`, `prediction.py`, `.env`)
- **Timezone Handling**:
  - Default timezone configured in `.env` via `LIBRE_TIMEZONE=America/New_York` (fallback in `parser.py` is `America/Los_Angeles`).
  - `ml_heuristics.py`:42 (`get_time_of_day_bucket`) localizes UTC datetimes to local time using `dt.astimezone(pytz.timezone(timezone_str))` to determine hour of day.
- **Dependencies (`requirements.txt`)**:
  - `requests>=2.25.0`, `pytz>=2021.1`, `psycopg2-binary>=2.9.0`, `python-dotenv>=0.19.0`, `fastapi>=0.100.0`, `uvicorn>=0.22.0`, `jinja2>=3.0.0`, `python-multipart>=0.0.6`.
- **Project Directory Structure**:
  - Project root contains flat structure: `app.py`, `db.py`, `parser.py`, `sync.py`, `prediction.py`, `ml_heuristics.py`, `main.py`, `schema.sql`, `requirements.txt`.
  - Tests directory `tests/` will be established co-located with source files.

---

## 2. Logic Chain

### 1. Data Contract & Ingestion Flow
`db.get_history(limit_hours)` returns a list of dictionaries with UTC Python `datetime` objects (`timestamp`) and float glucose levels (`value`).
Because glucose readings are stored in UTC in PostgreSQL, any circadian or time-of-day algorithm (Dawn Phenomenon between 04:00–08:00 AM, Nocturnal Hypos between 22:00–06:00 PM) MUST convert UTC `datetime` objects to the patient's local timezone using `pytz` prior to evaluating hour-of-day conditions.

### 2. Algorithmic Requirements for `dietary_analysis.py`
The module must detect four distinct glycemic anomalies:

1. **Postprandial Spikes (> 180 mg/dL)**:
   - *Logic*: Scan chronological readings for continuous excursions or peak events exceeding 180.0 mg/dL.
   - *Metrics*: Peak magnitude (mg/dL), spike duration (minutes), baseline pre-spike glucose, delta rise, time of day.
2. **Dawn Phenomenon (04:00 - 08:00 AM rise)**:
   - *Logic*: Convert timestamp to local time. Identify days where glucose steadily increases between 04:00 AM and 08:00 AM.
   - *Exclusion / Somogyi Check*: Verify that nighttime glucose (22:00 PM to 04:00 AM) did NOT drop below 70 mg/dL. If nocturnal hypo occurred prior, flag as Somogyi effect rather than true Dawn Phenomenon.
   - *Metrics*: Date of occurrence, 03:00-04:00 AM baseline, peak 04:00-08:00 AM value, delta rise.
3. **Nocturnal Hypoglycemia (< 70 mg/dL during 22:00 - 06:00)**:
   - *Logic*: Filter readings falling between 22:00 PM and 06:00 AM local time where glucose < 70.0 mg/dL.
   - *Severity Categorization*: Level 1 Hypo (< 70 mg/dL), Level 2 Severe Hypo (< 54 mg/dL).
   - *Metrics*: Minimum nadir (mg/dL), start time, end time, duration (minutes), Area Under Curve (AUC) below 70.
4. **Glycemic Variability (CV > 36%)**:
   - *Logic*: Calculate Mean Glucose ($\mu$) and Standard Deviation ($\sigma$).
   - *Equation*: $\text{CV} = \left(\frac{\sigma}{\mu}\right) \times 100\%$.
   - *Threshold*: Clinical target is $\text{CV} \le 36\%$. Anomaly triggered if $\text{CV} > 36.0\%$.

### 3. Software Architecture for `dietary_analysis.py`
To maximize maintainability, modularity, and testability, `dietary_analysis.py` should adopt the following design pattern:

```
+-----------------------------------------------------------------------------------+
|                                  dietary_analysis.py                               |
+-----------------------------------------------------------------------------------+
| Dataclasses / DTOs:                                                               |
|  - GlucosePoint(timestamp: datetime, value: float, type: str)                      |
|  - AnomalyType(Enum): POSTPRANDIAL_SPIKE, DAWN_PHENOMENON, NOCTURNAL_HYPO, ...     |
|  - AnomalyRecord(type, timestamp, peak_value, delta_value, duration_mins, ...)    |
|  - GlycemicSummary(mean_glucose, sd_glucose, cv_percent, gmi, total_readings, ...) |
+-----------------------------------------------------------------------------------+
| Engine & Detector Functions:                                                      |
|  - detect_postprandial_spikes(readings, threshold=180.0) -> List[AnomalyRecord]   |
|  - detect_dawn_phenomenon(readings, timezone_str) -> List[AnomalyRecord]           |
|  - detect_nocturnal_hypos(readings, timezone_str) -> List[AnomalyRecord]          |
|  - calculate_glycemic_variability(readings) -> dict                               |
|  - analyze_glucose_dataset(readings, timezone_str) -> GlycemicSummary            |
+-----------------------------------------------------------------------------------+
| Top-level API:                                                                    |
|  - generate_dietary_analysis_summary(hours=720, timezone_str="America/New_York")  |
+-----------------------------------------------------------------------------------+
```

---

## 3. Caveats

1. **Database Connection in Unit Tests**: `db.get_history()` requires an active PostgreSQL connection. Unit tests in `tests/test_dietary_analysis.py` should accept pure list-of-dicts or `GlucosePoint` objects so tests can run in-memory without a live database.
2. **Irregular CGM Gaps**: CGM continuous historic readings occur at ~15-minute intervals, while manual scan readings and live sync readings occur at arbitrary timestamps. Sorting by timestamp before segmenting episodes is mandatory.
3. **Somogyi vs. Dawn Phenomenon Distinction**: Rebound hyperglycemia following nocturnal hypoglycemia resembles Dawn Phenomenon. The algorithm must explicitly check preceding nocturnal readings (22:00-04:00) to rule out hypoglycemia (<70 mg/dL) before confirming Dawn Phenomenon.

---

## 4. Conclusion

### Recommended Design Blueprint for `dietary_analysis.py`
1. **Module Structure**:
   - `dietary_analysis.py` at workspace root (`c:\Users\tugha\Documents\antigravity\noble-galileo\dietary_analysis.py`).
2. **Key Data Structures**:
   ```python
   from dataclasses import dataclass
   from enum import Enum
   from datetime import datetime
   from typing import List, Optional, Dict, Any

   class AnomalyType(str, Enum):
       POSTPRANDIAL_SPIKE = "postprandial_spike"
       DAWN_PHENOMENON = "dawn_phenomenon"
       NOCTURNAL_HYPO = "nocturnal_hypo"
       HIGH_GLYCEMIC_VARIABILITY = "high_glycemic_variability"

   @dataclass
   class AnomalyRecord:
       anomaly_type: AnomalyType
       timestamp: datetime
       end_timestamp: Optional[datetime]
       peak_value: float
       nadir_value: Optional[float]
       delta_value: float
       duration_minutes: float
       severity: str  # "Mild", "Moderate", "Severe"
       details: Dict[str, Any]

   @dataclass
   class GlycemicSummary:
       total_readings: int
       mean_glucose: float
       sd_glucose: float
       cv_percent: float
       gmi: float
       time_in_range: Dict[str, float]  # low, target, high, very_high
       anomalies: List[AnomalyRecord]
       anomaly_counts: Dict[str, int]
   ```
3. **Primary Functions**:
   - `detect_postprandial_spikes(readings, spike_threshold=180.0, min_rise=30.0)`
   - `detect_dawn_phenomenon(readings, timezone_str="America/New_York", start_hour=4, end_hour=8)`
   - `detect_nocturnal_hypos(readings, timezone_str="America/New_York", hypo_threshold=70.0)`
   - `calculate_glycemic_variability(readings)`
   - `analyze_glucose_dataset(readings, timezone_str="America/New_York") -> GlycemicSummary`
   - `generate_dietary_analysis_summary(hours=720, timezone_str=None) -> GlycemicSummary` (fetches from `db.get_history(hours)`)

---

## 5. Verification Method

### How Implementers and Reviewers Can Verify
1. **Codebase Inspection**:
   - Verify `dietary_analysis.py` is created at `c:\Users\tugha\Documents\antigravity\noble-galileo\dietary_analysis.py`.
   - Verify `tests/test_dietary_analysis.py` exists with comprehensive unit tests for all 4 anomaly detection algorithms.

2. **Automated Unit Testing**:
   - Run `python -m pytest tests/test_dietary_analysis.py` (or `pytest`).
   - Expected Test Coverage:
     - `test_detect_postprandial_spikes`: Verify spikes >180 mg/dL are detected with correct peak and duration.
     - `test_detect_dawn_phenomenon`: Verify morning rises (04:00-08:00 AM) are detected and Somogyi effect (<70 mg/dL at night) is correctly excluded.
     - `test_detect_nocturnal_hypos`: Verify nighttime readings <70 mg/dL (22:00-06:00) are detected and severity is assigned (<54 mg/dL as Severe).
     - `test_calculate_glycemic_variability`: Verify CV % calculation ($SD / Mean \times 100$) and threshold (>36%).
     - `test_empty_data_handling`: Verify engine handles empty or single-reading lists gracefully without crashing or division by zero.
