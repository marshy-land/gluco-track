# Handoff Report: Database Schema & API Integration for Missing Dose Imputation (M2)

**Author**: Explorer 2  
**Milestone**: M2 (R2 Missing Dose Imputation Integration)  
**Date**: 2026-08-04  
**Working Directory**: `c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\teamwork_preview_explorer_m2_r1_2`  

---

## 1. Observation

Direct observations from codebase audit of `c:\Users\tugha\Documents\antigravity\noble-galileo`:

1. **`schema.sql` (lines 22-33)**:
   - Table `insulin_doses` is created with columns: `id`, `timestamp`, `rapid_acting`, `long_acting`, `meal`, `correction`, `user_change`, `device`, `serial_number`, `created_at`.
   - Lacks `is_imputed` and `confidence_score` columns.
   - Unique index `idx_insulin_doses_unique` exists on `(timestamp, rapid_acting, long_acting, meal, correction, user_change)`.

2. **`db.py` (lines 18-50, 166-234)**:
   - `init_db()` executes `schema.sql` and cleans up duplicate insulin doses based on `MIN(id)` per timestamp. `CREATE TABLE IF NOT EXISTS` will not add columns to pre-existing tables.
   - `insert_insulin_doses(doses)` (lines 166-215) checks `SELECT DISTINCT timestamp FROM insulin_doses WHERE timestamp = ANY(%s)` and inserts 8 explicit column fields without `is_imputed` or `confidence_score`.
   - `get_insulin_history(limit_hours=24)` (lines 217-234) queries `SELECT id, timestamp, rapid_acting, long_acting, meal, correction, user_change, device, serial_number FROM insulin_doses WHERE timestamp >= NOW() - INTERVAL %s ORDER BY timestamp ASC`.

3. **`app.py` (lines 46-52)**:
   - Route `/api/insulin/history` signature:
     ```python
     @app.get("/api/insulin/history")
     def api_insulin_history(hours: int = Query(default=24, ge=1, le=4320)):
         doses = get_insulin_history(hours)
         for d in doses:
             d['timestamp'] = d['timestamp'].isoformat()
         return doses
     ```
   - Currently accepts only `hours`. Lacks `include_imputed` parameter and missing dose imputation calculation logic.

4. **`PROJECT.md` Interface Contract (lines 40-54)**:
   - `GET /api/insulin/history?include_imputed=true`
   - Contract JSON response schema requires:
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

---

## 2. Logic Chain

1. **Observation 1 & 2** show that `insulin_doses` table currently stores only logged insulin doses and lacks `is_imputed` (`BOOLEAN`) and `confidence_score` (`DOUBLE PRECISION`).
2. **Observation 2** shows that `init_db()` runs `CREATE TABLE IF NOT EXISTS`. In PostgreSQL, if the table already exists, `CREATE TABLE IF NOT EXISTS` is a no-op and will not add missing columns. Therefore, adding `ALTER TABLE insulin_doses ADD COLUMN IF NOT EXISTS is_imputed BOOLEAN DEFAULT FALSE;` and `ALTER TABLE insulin_doses ADD COLUMN IF NOT EXISTS confidence_score DOUBLE PRECISION;` to `schema.sql` and `init_db()` ensures seamless migration across both new and pre-existing PostgreSQL databases.
3. **Observation 2** shows that `insert_insulin_doses` and `get_insulin_history` need to support reading and writing `is_imputed` and `confidence_score`. Setting default `is_imputed = FALSE` for logged doses preserves backward compatibility.
4. **Observation 3 & 4** show that `/api/insulin/history` needs to parse `include_imputed: bool = Query(default=False)`.
5. When `include_imputed=False`, the endpoint fetches logged doses where `is_imputed IS NOT TRUE`.
6. When `include_imputed=True`, the endpoint fetches logged doses AND calls `detect_and_impute_missing_doses(glucose_readings, logged_doses)` from `imputation.py`, merges both lists, tags logged doses with `is_imputed: False, confidence_score: null` and imputed doses with `is_imputed: True, confidence_score: float`, sorts them chronologically by ISO timestamp, and returns the combined dataset matching the interface contract in `PROJECT.md`.

---

## 3. Caveats

1. **Dependency on `imputation.py`**: The dynamic generation of missing doses in `/api/insulin/history?include_imputed=true` depends on the implementation of `detect_and_impute_missing_doses` in `imputation.py`. A defensive fallback (`try...except`) is designed in `app.py` so that if `imputation.py` is missing or raises an error, the endpoint gracefully returns the logged doses.
2. **Database Engine Scope**: Investigation confirmed PostgreSQL (`psycopg2`) is the target database engine used in `db.py`.
3. **Frontend Integration Scope**: Frontend rendering of imputed doses with Chart.js dashed borders/fills in `templates/index.html` relies on these API contract fields (`is_imputed` and `confidence_score`).

---

## 4. Conclusion

The database schema and API layer design for missing dose imputation is complete and fully specified:
1. `schema.sql` and `db.py` `init_db()` are upgraded with `ALTER TABLE insulin_doses ADD COLUMN IF NOT EXISTS is_imputed BOOLEAN DEFAULT FALSE;` and `ALTER TABLE insulin_doses ADD COLUMN IF NOT EXISTS confidence_score DOUBLE PRECISION;`.
2. `insert_insulin_doses` and `get_insulin_history` are updated to support `is_imputed` and `confidence_score`.
3. `/api/insulin/history` in `app.py` is updated to parse `include_imputed: bool = Query(default=False)`, merge logged doses with deconvolution outputs from `imputation.py` when `include_imputed=True`, and return JSON matching `PROJECT.md`.

---

## 5. Verification Method

To independently verify the database and API updates:

1. **Database Schema Verification**:
   - Run `python -c "import db; db.init_db()"` with PostgreSQL connected.
   - Inspect table schema using SQL:
     ```sql
     SELECT column_name, data_type, column_default 
     FROM information_schema.columns 
     WHERE table_name = 'insulin_doses';
     ```
   - Verify `is_imputed` (boolean, default false) and `confidence_score` (double precision) are present.

2. **API Endpoint Verification**:
   - Start Uvicorn server: `uvicorn app:app --port 8000`.
   - Test default query: `curl http://localhost:8000/api/insulin/history?hours=24`
     - Verify response contains logged doses with `is_imputed: false`.
   - Test imputed query: `curl http://localhost:8000/api/insulin/history?hours=24&include_imputed=true`
     - Verify response includes entries with `"is_imputed": true` and `"confidence_score": <float>`.

3. **Invalidation Conditions**:
   - If `/api/insulin/history?include_imputed=true` fails to return `is_imputed` or `confidence_score` keys in JSON items.
   - If database initialization throws SQL syntax or column duplication errors on existing databases.
