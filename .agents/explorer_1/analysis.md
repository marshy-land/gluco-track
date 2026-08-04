# Comprehensive Architectural and Codebase Analysis: Gluco Track

**Author**: explorer_1  
**Date**: 2026-08-04  
**Target Repository**: `c:\Users\tugha\Documents\antigravity\noble-galileo`  

---

## 1. Executive Summary & System Overview

**Gluco Track** is an open-source glucose and insulin tracking background service and web dashboard. It aggregates historical FreeStyle Libre continuous glucose monitoring (CGM) data from LibreView CSV exports and continuously syncs live readings from Abbott's LibreLinkUp cloud API into a PostgreSQL database.

The application serves a dual purpose:
1. **Background Live Sync Daemon**: Periodically authenticates with Abbott LibreLinkUp, fetches graph readings (last 12h) and instantaneous measurements, deduplicates them, and writes them to PostgreSQL.
2. **Interactive Web Dashboard & REST API**: Provides real-time glucose visualization, trend predictions (+15m, +30m, +60m), active Insulin-On-Board (IOB) tracking, correction bolus suggestions, statistical diagnostics (GMI, Time-in-Range), time-of-day Insulin Sensitivity Factor (ISF) calculation, and CSV file upload capabilities.

---

## 2. Overall Repository Layout & Technology Stack

### 2.1 Directory & File Layout

```
c:\Users\tugha\Documents\antigravity\noble-galileo/
├── app.py                # FastAPI web application & REST API endpoints
├── db.py                 # PostgreSQL connection pool & database CRUD operations
├── import_csv.py         # CLI script to backfill LibreView CSV exports into PostgreSQL
├── main.py               # Application entry point (runs sync daemon thread + Uvicorn web server)
├── ml_heuristics.py      # Time-of-day ISF calculator & Ridge Regression prediction model
├── parser.py             # Header-agnostic parser for LibreView CSV exports
├── prediction.py         # Glucose trend forecasting, Scheiner IOB calculator, correction logic
├── schema.sql            # Database schema definitions and indexes
├── sync.py               # LibreLinkUp API client and live sync worker logic
├── requirements.txt      # Python dependencies
├── Dockerfile            # Container definition for Railway / Docker deployments
├── .env                  # Environment variable configuration (credentials & DB connection)
├── README.md             # Project documentation and deployment guide
└── templates/
    └── index.html        # Single-page HTML/JS dashboard UI (Chart.js visualization)
```

### 2.2 Core Technology Stack

| Technology Component | Description / Package | Version Constraint / Details |
| :--- | :--- | :--- |
| **Language** | Python | 3.10+ (Tested on 3.11/3.12) |
| **Web Framework** | FastAPI | `fastapi>=0.100.0` |
| **ASGI Server** | Uvicorn | `uvicorn>=0.22.0` |
| **Database** | PostgreSQL | `psycopg2-binary>=2.9.0` |
| **HTTP Client** | Requests | `requests>=2.25.0` |
| **Timezones** | PyTZ | `pytz>=2021.1` |
| **Frontend UI** | HTML5 / CSS3 / Vanilla JS (ES6+) | Glassmorphism UI, Google Fonts (Inter & Outfit) |
| **Charting Library** | Chart.js v4 + Date Adapter | `chart.js` + `chartjs-adapter-date-fns` via CDN |
| **Containerization** | Docker | Base image `python:3.11-slim` |
| **Target Cloud** | Railway | Deployable worker container with inject `DATABASE_URL` |

---

## 3. Data Pipeline: Glucose Readings, Insulin Doses, and Missing Data Gaps

### 3.1 Database Schema (`schema.sql`)

The database consists of two primary tables:

1. **`glucose_readings`**:
   - `id`: `SERIAL PRIMARY KEY`
   - `timestamp`: `TIMESTAMPTZ NOT NULL` (stored in UTC)
   - `value`: `DOUBLE PRECISION NOT NULL` (glucose in mg/dL)
   - `type`: `VARCHAR(20) NOT NULL` (`'historic'`, `'scan'`, or `'live'`)
   - `device`: `VARCHAR(100)`
   - `serial_number`: `VARCHAR(100)`
   - `record_type`: `INTEGER` (`0` = Continuous/Historic, `1` = Manual Scan)
   - `created_at`: `TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP`
   - **Unique Index**: `idx_glucose_readings_unique` on `(timestamp, value)`
   - **Query Index**: `idx_glucose_readings_timestamp` on `(timestamp DESC)`

2. **`insulin_doses`**:
   - `id`: `SERIAL PRIMARY KEY`
   - `timestamp`: `TIMESTAMPTZ NOT NULL`
   - `rapid_acting`: `DOUBLE PRECISION` (units)
   - `long_acting`: `DOUBLE PRECISION` (units)
   - `meal`: `DOUBLE PRECISION` (units)
   - `correction`: `DOUBLE PRECISION` (units)
   - `user_change`: `DOUBLE PRECISION` (units)
   - `device`: `VARCHAR(100)`
   - `serial_number`: `VARCHAR(100)`
   - `created_at`: `TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP`
   - **Unique Index**: `idx_insulin_doses_unique` on `(timestamp, rapid_acting, long_acting, meal, correction, user_change)`
   - **Query Index**: `idx_insulin_doses_timestamp` on `(timestamp DESC)`

### 3.2 CSV Parsing (`parser.py`)
- `parse_libreview_csv(file_path, timezone_str)`:
  - Dynamically locates header row containing `"Timestamp"` and `"Device"`.
  - Maps timestamps in various 12h/24h formats (`%Y-%m-%d %H:%M:%S`, `%m-%d-%Y %I:%M %p`, etc.) to UTC using `pytz`.
  - Extracts historic glucose (Record Type 0) and scan glucose (Record Type 1).
  - Extracts rapid-acting, long-acting, meal, correction, and user change insulin doses.
  - Returns tuple of `(readings, insulin_doses)`.

### 3.3 Live Sync (`sync.py`)
- `LibreLinkUpClient`:
  - Logins to regional LibreLinkUp auth server (`https://api-us.libreview.io/llu/auth/login`).
  - Computes `Account-Id` SHA-256 hash of `user_id` (required header for LLU API >= 4.16.0).
  - Fetches connected patient graph data (`/llu/connections/{patient_id}/graph`).
  - Parses 12-hour continuous readings (`graphData`) and single latest scan (`glucoseMeasurement`).
  - Deduplicates readings in memory before calling `insert_readings()`.

### 3.4 Predictive Analytics & Insulin Calculations (`prediction.py` & `ml_heuristics.py`)
- **Active Insulin-On-Board (IOB)**: Computed via `calculate_iob()` using Scheiner's 4-hour parabolic decay curve ($IOB = Dose \times (1 - \frac{t}{240})^2$) across rapid-acting, meal, and correction components.
- **Bolus Suggestion**: $Suggested = \max(0, \frac{CurrentGlucose - TargetGlucose}{ISF} - IOB)$.
- **Time-of-Day ISF**: Calculated in `calculate_personalized_isf()` for 4 buckets: Morning (04:00-11:00), Afternoon (11:00-17:00), Evening (17:00-22:00), Night (22:00-04:00) by analyzing isolated 4-hour correction events without stacking.
- **Glucose Prediction**: `predict_glucose()` forecasts glucose for +15m, +30m, +60m. Uses Ridge Regression model (`ml_heuristics.py`) if trained (using features $[1, G(t), G(t-15), G(t-30), G(t-60), \sin(h), \cos(h), IOB]$), or falls back to a dampened linear regression slope.

### 3.5 Current System Gaps (Against Project Goals R1, R2, R3)
1. **Meal/Nutritional Data**: Meal insulin values are recorded in `insulin_doses.meal`, but **there is NO database table or schema for carbohydrate intake (grams), food item descriptions, meal times, or time-of-day nutritional impact models**.
2. **Missing Insulin Dose Imputation**: While `calculate_iob()` and `suggest_correction()` handle logged doses, there is no automated model to impute unlogged/missing historical correction doses or visually distinguish imputed doses on the timeline chart.
3. **Dietary Recommendations & Medical Literature**: No connection or pipeline currently exists for querying scientific databases (PubMed, OpenAlex) or generating custom literature-backed dietary reports.

---

## 4. UI Layout, Chart Components, and Entry Point Scripts

### 4.1 Entry Point Scripts

1. **`main.py`**:
   - Primary production entry point.
   - Spawns background thread running `sync_worker_loop()` which calls `run_sync()` every 300s (`SYNC_INTERVAL_SECONDS`).
   - Runs Uvicorn Web Server hosting the FastAPI app (`app.py`) on `0.0.0.0:8080` (or `PORT` env var).
2. **`app.py`**:
   - Defines FastAPI routes:
     - `GET /`: Dashboard UI (`templates/index.html`)
     - `GET /api/glucose/latest`: Returns latest reading JSON
     - `GET /api/glucose/history`: Returns last N hours of glucose readings
     - `GET /api/insulin/history`: Returns last N hours of insulin doses
     - `GET /api/predictions`: Computes +15m, +30m, +60m forecasts, IOB, correction bolus suggestion
     - `POST /api/heuristics/train`: Triggers Ridge Regression model training
     - `GET /api/heuristics/status`: Returns training status and time-of-day ISFs
     - `POST /api/insulin/log`: Manually logs an insulin dose
     - `GET /api/shortcut/log`: Android Shortcut deep-link endpoint for quick dose logging
     - `GET /api/glucose/stats`: Calculates avg glucose, GMI (Est A1c), Time-in-Range %
     - `POST /api/glucose/upload`: Uploads LibreView CSV export to backfill data
3. **`sync.py`**:
   - CLI script (`python sync.py [--dry-run]`) to test or execute single sync.
4. **`import_csv.py`**:
   - CLI script (`python import_csv.py <path_to_csv> [--tz America/Los_Angeles]`) to backfill CSV data into DB.

### 4.2 UI Layout & Chart Components (`templates/index.html`)

- **Top Row**:
  - *Current Status Card*: Big numerical glucose display, status glow (emerald for target 70-180, red for low <70, amber for high >180), trend arrow indicator (1-5 mapping).
  - *Predictive Insights Card*: +15m, +30m, +60m forecasted values, active IOB display (U), suggested bolus (U).
  - *Summary Metrics Card*: Average glucose, GMI (Est A1C %), Time in Target %, and color-coded Time-in-Range progress bar.
- **Main Chart Panel (`glucoseChart`)**:
  - Line chart rendered using Chart.js with date-fns time adapter.
  - Features continuous glucose line with gradient area fill, target range shaded box (70-180 mg/dL), dotted line overlay for +15m/+30m/+60m predictions.
  - Time range selector buttons (3H, 6H, 24H, 3D, 7D, 30D, 90D).
- **Insulin Timeline (`insulinChart`) & Log Table**:
  - Bar chart showing insulin doses by category (Rapid-Acting, Long-Acting, Meal, Correction).
  - Chronological tabular view of recent insulin doses.
- **Action Grid**:
  - Historical Data Upload dropzone (drag-and-drop CSV parser).
  - Manual Insulin Log Form.
  - Heuristics & Tuning status panel (morning, afternoon, evening, night ISFs + Retrain button).

---

## 5. Build and Test Setup

### 5.1 Dependencies & Build Configuration
- Dependencies defined in `requirements.txt`:
  ```
  requests>=2.25.0
  pytz>=2021.1
  psycopg2-binary>=2.9.0
  python-dotenv>=0.19.0
  fastapi>=0.100.0
  uvicorn>=0.22.0
  jinja2>=3.0.0
  python-multipart>=0.0.6
  ```
- Deployment configuration defined in `Dockerfile`:
  - Base: `python:3.11-slim`
  - Installs requirements with `pip install --no-cache-dir -r requirements.txt`
  - Command: `python -u main.py`

### 5.2 Test Setup Evaluation
- **Test Framework**: No test runner configuration or test directory currently exists in the codebase.
- **Verification Commands Executed**:
  - `pytest`: Fails (`pytest` not installed in environment).
  - `python -m pytest`: Fails (`No module named pytest`).
  - `python -m unittest discover`: Output: `Ran 0 tests in 0.000s`.
- **Recommendation**: For upcoming implementation phases, unit tests should be introduced using Python's standard `unittest` framework (which requires no external dependencies) or `pytest` should be added to test dependencies.

---

## 6. Summary of Architectural Findings for Downstream Implementation

1. **Database Readiness**: `glucose_readings` and `insulin_doses` schema and bulk-upsert mechanisms are solid. New features (such as imputed doses or meal/nutritional data) will require database schema migrations or additions to `schema.sql` and `db.py`.
2. **Dashboard Extensibility**: `templates/index.html` uses clean Chart.js datasets. Imputed doses can be added as a separate, distinct dataset on `insulinChart` (e.g., hashed/dashed bars or distinct color markers).
3. **Prediction Engine**: `ml_heuristics.py` provides pure Python linear algebra matrix operations (`transpose`, `matmul`, `invert_matrix`) without requiring heavy ML libraries like `scikit-learn` or `numpy`, keeping the container lightweight.
