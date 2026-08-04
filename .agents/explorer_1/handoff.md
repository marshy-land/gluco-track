# Handoff Report: Gluco Track Codebase Investigation

**Agent**: explorer_1  
**Working Directory**: `c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\explorer_1`  
**Date**: 2026-08-04  
**Handoff Type**: Hard (Task Complete)  

---

## 1. Observation

### 1.1 Repository Structure & Language Baseline
- Files present in root directory `c:\Users\tugha\Documents\antigravity\noble-galileo`:
  - `main.py`, `app.py`, `db.py`, `parser.py`, `sync.py`, `import_csv.py`, `prediction.py`, `ml_heuristics.py`, `schema.sql`, `requirements.txt`, `Dockerfile`, `.env`, `README.md`, `templates/index.html`.
- `requirements.txt` specifies:
  - `requests>=2.25.0`, `pytz>=2021.1`, `psycopg2-binary>=2.9.0`, `python-dotenv>=0.19.0`, `fastapi>=0.100.0`, `uvicorn>=0.22.0`, `jinja2>=3.0.0`, `python-multipart>=0.0.6`.
- `Dockerfile` specifies:
  - Base image: `python:3.11-slim`
  - Entry point: `CMD ["python", "-u", "main.py"]`

### 1.2 Web Framework & Server Lifecycle
- `main.py`:
  - Line 33: `sync_thread = threading.Thread(target=sync_worker_loop, daemon=True)`
  - Line 44: `uvicorn.run(app, host=host, port=port)` (default port `8080`).
- `app.py`:
  - Line 14: `app = FastAPI(title="Gluco Track API", version="1.0.0")`
  - Line 17-25: `@app.get("/", response_class=HTMLResponse)` serves `templates/index.html`.
  - Routes defined: `/api/glucose/latest`, `/api/glucose/history`, `/api/insulin/history`, `/api/predictions`, `/api/heuristics/train`, `/api/heuristics/status`, `/api/insulin/log`, `/api/shortcut/log`, `/api/glucose/stats`, `/api/glucose/upload`.

### 1.3 Database Architecture & Data Parsing
- `schema.sql`:
  - Lines 3-12: `glucose_readings` table storing `timestamp TIMESTAMPTZ`, `value DOUBLE PRECISION`, `type VARCHAR(20)`, `device`, `serial_number`, `record_type`.
  - Lines 15-16: `CREATE UNIQUE INDEX IF NOT EXISTS idx_glucose_readings_unique ON glucose_readings (timestamp, value);`
  - Lines 22-33: `insulin_doses` table storing `timestamp TIMESTAMPTZ`, `rapid_acting`, `long_acting`, `meal`, `correction`, `user_change`, `device`, `serial_number`.
  - Lines 36-37: `CREATE UNIQUE INDEX IF NOT EXISTS idx_insulin_doses_unique ON insulin_doses (timestamp, rapid_acting, long_acting, meal, correction, user_change);`
- `db.py`:
  - Functions: `init_db()`, `insert_readings()`, `get_latest_reading()`, `get_history()`, `get_statistics()`, `insert_insulin_doses()`, `get_insulin_history()`.
- `parser.py`:
  - Lines 33-36: Dynamically scans CSV rows for headers matching `"Timestamp"` and `"Device"`.
  - Lines 116-178: Extracts historic glucose (Record Type 0), scan glucose (Record Type 1), and insulin dose columns (`Rapid-Acting`, `Long-Acting`, `Meal`, `Correction`, `User Change`). Converts local timestamps to UTC using `pytz`.
- `sync.py`:
  - `LibreLinkUpClient`: Authenticates with LibreLinkUp API, generates SHA-256 `Account-Id` header from `user_id`, fetches patient graph readings (12h) and latest measurement, deduplicates readings, and upserts to PostgreSQL via `insert_readings()`.

### 1.4 Visualization Components
- `templates/index.html`:
  - Single-page glassmorphism dashboard using Chart.js v4 + date-fns.
  - `glucoseChart` (Line chart): Displays historical glucose values with smooth bezier curve (`tension: 0.3`), target range band (70-180 mg/dL), and dotted projection overlay dataset for +15m, +30m, +60m forecasts.
  - `insulinChart` (Bar chart): Stacked bar chart showing Rapid-Acting (red `#ef4444`), Long-Acting (amber `#f59e0b`), Meal (emerald `#10b981`), Correction (blue `#3b82f6`).
  - Table `#insulin-table`: Lists recent dose timestamps and unit amounts.

### 1.5 Build & Test Setup Commands & Results
- Execution of `pytest`:
  - Command: `pytest`
  - Output: `pytest : The term 'pytest' is not recognized as the name of a cmdlet, function, script file, or operable program.`
- Execution of `python -m pytest`:
  - Command: `python -m pytest`
  - Output: `No module named pytest`
- Execution of `python -m unittest discover`:
  - Command: `python -m unittest discover`
  - Output: `Ran 0 tests in 0.000s — NO TESTS RAN`

---

## 2. Logic Chain

1. **Architecture Identification**:
   - Observation 1.1 & 1.2 demonstrate that `main.py` starts a background thread running `sync_worker_loop()` and launches FastAPI via Uvicorn. `app.py` serves `templates/index.html` at `/` and provides JSON REST endpoints at `/api/...`.
   - *Logic*: The system is a hybrid Python/FastAPI web application and background polling daemon designed to run in Docker/Railway.

2. **Data Pipeline & Persistence**:
   - Observation 1.3 demonstrates PostgreSQL tables (`glucose_readings` and `insulin_doses`) with `TIMESTAMPTZ` timestamps and unique indexes preventing duplicate entries.
   - `parser.py` parses LibreView CSVs, and `sync.py` parses LibreLinkUp API payloads, feeding `db.py` functions `insert_readings()` and `insert_insulin_doses()`.
   - *Logic*: The database layer is established for glucose readings and 5 categories of insulin doses. However, no database table or API endpoint exists for carbohydrate/nutritional data or meal tracking.

3. **Analytics & Visualization Baseline**:
   - Observation 1.4 shows `prediction.py` and `ml_heuristics.py` calculate active IOB via Scheiner decay ($IOB = Dose \times (1 - \frac{t}{240})^2$), suggest correction boluses, compute time-of-day ISFs, and project future glucose.
   - `templates/index.html` visualizes glucose (`glucoseChart`) and insulin (`insulinChart`) using Chart.js.
   - *Logic*: Imputed insulin doses can be integrated into the dashboard by extending `insulin_doses` or creating an `imputed_doses` indicator/dataset, rendering them on `insulinChart` with distinct styling.

4. **Testing Status**:
   - Observation 1.5 shows no test files exist in the repository, and `pytest` is not installed in the current Python environment.
   - *Logic*: Automated testing must be initialized during subsequent development phases using Python's standard `unittest` library or by installing `pytest`.

---

## 3. Caveats

- **Database Connection**: Live database operations rely on a valid `DATABASE_URL` PostgreSQL instance. A Railway PostgreSQL connection string is present in `.env`, but connection status was not tested to avoid side-effects on live DB records during read-only investigation.
- **External API Keys**: LibreLinkUp sync relies on caregiver account credentials in `.env`.
- **Scope Limit**: Investigation was strictly read-only. No code modifications were made outside `.agents/explorer_1/`.

---

## 4. Conclusion

The existing Gluco Track application provides a solid foundation with Python 3.11+, FastAPI, PostgreSQL, and a Chart.js dashboard. All core glucose and insulin data flows (parsing, storing, querying, visualizing) are fully functional.

To meet downstream project goals:
1. **Missing Dose Imputation**: Imputed doses can be calculated in Python and stored/flagged in the database, then rendered with a distinct visual style (e.g. pattern/dashed bar) on `insulinChart`.
2. **Time-of-Day Nutritional Impact Model**: Requires extending the schema/data models to capture meal/carbohydrate data and time-of-day glucose response factors.
3. **Dietary Recommendations**: Scientific literature queries (PubMed / OpenAlex) can be implemented via standalone Python modules without altering existing web architecture.

---

## 5. Verification Method

To independently verify this codebase analysis:

1. **Inspect Architecture & Entry Points**:
   - Inspect `main.py`, `app.py`, `db.py`, `parser.py`, `sync.py`, `schema.sql`, `requirements.txt`.
2. **Inspect Dashboard Layout**:
   - Open `templates/index.html` and verify canvas elements `#glucoseChart` and `#insulinChart`, API fetch calls (`/api/glucose/history`, `/api/insulin/history`, `/api/predictions`), and dropzone upload logic.
3. **Verify Test Setup**:
   - Run `python -m unittest discover` in root directory `c:\Users\tugha\Documents\antigravity\noble-galileo` to verify 0 existing tests.
4. **Check Analysis Artifact**:
   - Review `c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\explorer_1\analysis.md`.
