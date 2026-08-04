# Milestone 3 Iteration 2 — Root Cause Analysis & Holistic Fix Plan

## Executive Summary

An empirical investigation of the 69 test cases across `tests/` and `e2e_tests/` was conducted. Out of 69 total tests, **61 passed and 8 failed**.

The 8 test failures stem from two distinct root causes:
1. **DB Migration Concurrency Deadlock** in `db.py` during concurrent `init_db()` executions (`test_init_db_idempotency_concurrent`).
2. **Report Generator Contract & Signature Mismatches** in `dietary_analysis.py` handling `output_path=None` and returning a file path string instead of markdown content (`test_r1_03`, `test_r1_04`, `test_r1_05`, `test_r1_tier2_01`, `test_r3_tier3_03`, `test_r4_tier4_01`, `test_r4_tier4_02`).

Both issues are well-isolated and can be fixed with targeted, backward-compatible updates to `db.py` and `dietary_analysis.py`. Below is the complete empirical analysis and holistic fix plan.

---

## 1. Failure Catalog & Empirical Evidence

| # | Test Case | Test File | Empirical Failure Symptom | Root Cause Category |
|---|---|---|---|---|
| 1 | `test_init_db_idempotency_concurrent` | `tests/test_challenger_api.py:131` | `psycopg2.errors.DeadlockDetected: deadlock detected` at `db.py:34` | DB Concurrency Deadlock |
| 2 | `test_r1_03_report_markdown_structure` | `e2e_tests/test_tier1_features.py:65` | `AssertionError: '# Executive Summary' not found in '.../dietary_remedies_report.md'` | Report Contract Mismatch |
| 3 | `test_r1_04_citation_validation` | `e2e_tests/test_tier1_features.py:79` | `TypeError: _path_normpath: path should be string, bytes or os.PathLike, not NoneType` | `output_path=None` Unhandled |
| 4 | `test_r1_05_actionable_plan_verification` | `e2e_tests/test_tier1_features.py:93` | `TypeError: _path_normpath: path should be string, bytes or os.PathLike, not NoneType` | `output_path=None` Unhandled |
| 5 | `test_r1_tier2_01_empty_historical_dataset` | `e2e_tests/test_tier2_boundaries.py:31` | `TypeError: _path_normpath: path should be string, bytes or os.PathLike, not NoneType` | `output_path=None` Unhandled |
| 6 | `test_r3_tier3_03_pairwise_r1_x_r3_dietary_report_with_diurnal_modifiers` | `e2e_tests/test_tier3_interactions.py:61` | `TypeError: _path_normpath: path should be string, bytes or os.PathLike, not NoneType` | `output_path=None` Unhandled |
| 7 | `test_r4_tier4_01_full_multiday_libreview_e2e_workflow` | `e2e_tests/test_tier4_scenarios.py:28` | `AssertionError: '# Executive Summary' not found in '.../dietary_remedies_report.md'` | Report Contract Mismatch |
| 8 | `test_r4_tier4_02_dawn_phenomenon_and_nocturnal_hypo_patient_profile` | `e2e_tests/test_tier4_scenarios.py:59` | `TypeError: _path_normpath: path should be string, bytes or os.PathLike, not NoneType` | `output_path=None` Unhandled |

---

## 2. In-Depth Root Cause Analysis

### Issue 1: Database Initialization Concurrency Deadlock (`test_init_db_idempotency_concurrent`)

- **File & Line**: `db.py`, lines 18–54 (`init_db()`).
- **Traceback**:
  ```
  psycopg2.errors.DeadlockDetected: deadlock detected
  DETAIL: Process 5412 waits for AccessExclusiveLock on relation 32770 of database 16384; blocked by process 5411.
  Process 5411 waits for AccessExclusiveLock on relation 32770 of database 16384; blocked by process 5412.
  db.py:34: cur.execute("ALTER TABLE insulin_doses ADD COLUMN IF NOT EXISTS is_imputed BOOLEAN DEFAULT FALSE;")
  ```
- **Mechanism**:
  When 5 concurrent worker threads execute `init_db()` simultaneously (via `ThreadPoolExecutor`), each thread opens an independent database connection and executes DDL statements (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ADD COLUMN IF NOT EXISTS`, `DELETE FROM insulin_doses`).
  In PostgreSQL, DDL modifications lock system catalog tables (`pg_class`, `pg_attribute`) using `AccessExclusiveLock`. Concurrent DDL operations across multiple transactions create circular lock waits, triggering PostgreSQL's deadlock detection and aborting transactions.

### Issue 2: Unhandled `output_path=None` in Report Generator (Tests 3, 4, 5, 6, 8)

- **File & Line**: `dietary_analysis.py`, lines 711–771 (`generate_report()`).
- **Traceback**:
  ```
  e2e_tests\contracts.py:458: in run_generate_report
      return mod.generate_report(readings, output_path=output_path)
  dietary_analysis.py:766: in generate_report
      abs_output_path = os.path.abspath(output_path)
  TypeError: _path_normpath: path should be string, bytes or os.PathLike, not NoneType
  ```
- **Mechanism**:
  Tests that do not require writing a report file to disk pass `output_path=None` to `run_generate_report()`. In `dietary_analysis.py:766`, `generate_report()` assumes `output_path` is a non-None string path and calls `os.path.abspath(output_path)`. When `output_path` is `None`, Python raises `TypeError: path should be string, bytes or os.PathLike, not NoneType`.

### Issue 3: Return Value Contract & Section Header Mismatch (Tests 2 & 7)

- **File & Line**: `dietary_analysis.py`, lines 555–708 (`render_markdown_report()`) and lines 766–771 (`generate_report()`).
- **Traceback**:
  ```
  e2e_tests\test_tier1_features.py:74: in test_r1_03_report_markdown_structure
      self.assertIn("# Executive Summary", md_content)
  AssertionError: '# Executive Summary' not found in 'C:\\Users\\...\\dietary_remedies_report.md'
  ```
- **Mechanism**:
  1. `generate_report()` in `dietary_analysis.py` returns `abs_output_path` (the string file path on disk) instead of `report_md` (the actual markdown text content of the report). When test assertions execute `self.assertIn("# Executive Summary", md_content)`, `md_content` contains the file path string `'C:\\Users\\...\\dietary_remedies_report.md'`, causing the assertion to fail.
  2. Additionally, `render_markdown_report()` in `dietary_analysis.py` used numbered headers such as `## 1. Executive Summary & User Glycemic Statistics` and `## 5. Actionable Weekly Implementation Plan`. The contract specification in `PROJECT.md` and assertions in `e2e_tests/` require exact header substrings:
     - `# Executive Summary`
     - `## Observed Glycemic Trends & Anomalies`
     - `## Literature-Backed Dietary Interventions`
     - `## Actionable Plan`

---

## 3. Proposed Fix Plan & Diff Specifications

### Fix Part 1: Thread & Process Safe DB Initialization (`db.py`)

In `db.py:init_db()`, wrap DDL execution in a PostgreSQL advisory lock (`SELECT pg_advisory_lock(987654321);`) and Python `threading.Lock()`.

```python
# Proposed change in db.py
import threading

_INIT_DB_LOCK = threading.Lock()

def init_db():
    """Initializes the database and cleans up any duplicate insulin logs with thread and process concurrency safety."""
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    if not os.path.exists(schema_path):
        print("schema.sql not found, skipping table initialization.")
        return

    with open(schema_path, "r") as f:
        schema_sql = f.read()

    with _INIT_DB_LOCK:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                # Use PostgreSQL advisory lock to prevent cross-process/cross-thread DDL deadlocks
                cur.execute("SELECT pg_advisory_lock(987654321);")
                try:
                    cur.execute(schema_sql)
                    
                    # Safe schema migrations for missing dose imputation fields
                    cur.execute("ALTER TABLE insulin_doses ADD COLUMN IF NOT EXISTS is_imputed BOOLEAN DEFAULT FALSE;")
                    cur.execute("ALTER TABLE insulin_doses ADD COLUMN IF NOT EXISTS confidence_score DOUBLE PRECISION;")
                    
                    # Clean up duplicate insulin records (if any)
                    cur.execute("""
                        DELETE FROM insulin_doses 
                        WHERE id NOT IN (
                            SELECT MIN(id) 
                            FROM insulin_doses 
                            GROUP BY timestamp
                        )
                    """)
                finally:
                    cur.execute("SELECT pg_advisory_unlock(987654321);")
            conn.commit()
            print("Database initialized and duplicate insulin logs cleaned up.")
        except Exception as e:
            conn.rollback()
            print(f"Error initializing database: {e}")
            raise e
        finally:
            conn.close()
```

### Fix Part 2: Report Generator Contract & Header Alignment (`dietary_analysis.py`)

1. In `dietary_analysis.py:generate_report()`:
   - Handle `output_path=None` gracefully.
   - Return `report_md` (the markdown text content string).

```python
# Proposed change in dietary_analysis.py: generate_report
def generate_report(
    readings: Optional[List[Dict[str, Any]]] = None,
    timezone_str: str = "America/New_York",
    output_path: Optional[str] = "dietary_remedies_report.md",
    use_network: bool = True
) -> str:
    """
    End-to-end report generation entrypoint.
    Returns the generated markdown report string. If output_path is specified, also writes to disk.
    """
    if readings is None:
        if db is not None:
            try:
                readings = db.get_history(limit_hours=720)
            except Exception as e:
                logger.warning(f"Could not fetch readings from database: {e}")
                readings = []
        else:
            readings = []

    # If still no readings, generate realistic baseline sample dataset for report demonstration
    if not readings:
        base_time = datetime.now(timezone.utc) - timedelta(days=7)
        sample_readings = []
        for day in range(7):
            day_dt = base_time + timedelta(days=day)
            sample_readings.append({"timestamp": (day_dt.replace(hour=3, minute=0)).isoformat(), "value": 62.0})
            sample_readings.append({"timestamp": (day_dt.replace(hour=7, minute=0)).isoformat(), "value": 155.0})
            sample_readings.append({"timestamp": (day_dt.replace(hour=9, minute=0)).isoformat(), "value": 110.0})
            sample_readings.append({"timestamp": (day_dt.replace(hour=13, minute=0)).isoformat(), "value": 215.0})
            sample_readings.append({"timestamp": (day_dt.replace(hour=14, minute=0)).isoformat(), "value": 195.0})
            sample_readings.append({"timestamp": (day_dt.replace(hour=19, minute=0)).isoformat(), "value": 205.0})
            sample_readings.append({"timestamp": (day_dt.replace(hour=22, minute=0)).isoformat(), "value": 125.0})
        readings = sample_readings

    # Run analysis
    stats, summary = analyze_glucose_dataset(readings, timezone_str=timezone_str)

    # Fetch literature citations for all 4 anomaly categories
    anomaly_cats = ["postprandial_spike", "dawn_phenomenon", "nocturnal_hypo", "high_glycemic_variability"]
    citations_by_cat = fetch_literature_for_anomalies(anomaly_cats, use_network=use_network)

    # Render report
    report_md = render_markdown_report(stats, summary, citations_by_cat, timezone_str=timezone_str)

    # Write to file if output_path is provided
    if output_path:
        abs_output_path = os.path.abspath(output_path)
        os.makedirs(os.path.dirname(abs_output_path), exist_ok=True)
        with open(abs_output_path, "w", encoding="utf-8") as f:
            f.write(report_md)

    return report_md
```

2. In `dietary_analysis.py:render_markdown_report()`:
   Align top-level Markdown headers to match contract requirements:
   - Line 555: `# Executive Summary`
   - Line 584: `## Observed Glycemic Trends & Anomalies`
   - Line 630: `## Literature-Backed Dietary Interventions`
   - Line 694: `## Actionable Plan`

---

## 4. Verification Plan

After the implementer applies the changes above:

1. **Run full test suite**:
   ```bash
   python -m pytest tests/ e2e_tests/ -v
   ```
   *Expected Result*: **69 passed, 0 failed** (100% green pass rate).

2. **Verify Concurrent DB Initialization**:
   ```bash
   python -m pytest tests/test_challenger_api.py -k test_init_db_idempotency_concurrent -v
   ```
   *Expected Result*: PASSED without deadlocks.

3. **Verify Report Generator File & String Contracts**:
   ```bash
   python -m pytest e2e_tests/test_tier1_features.py e2e_tests/test_tier2_boundaries.py e2e_tests/test_tier3_interactions.py e2e_tests/test_tier4_scenarios.py -v
   ```
   *Expected Result*: All 40 E2E tests PASSED.
