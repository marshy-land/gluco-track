# Analysis Report: Database Schema & API Integration for Missing Dose Imputation (M2)

**Author**: Explorer 2  
**Milestone**: M2 (R2 Missing Dose Imputation Integration)  
**Date**: 2026-08-04  
**Target Files**: `db.py`, `schema.sql`, `app.py`  

---

## Executive Summary

This report provides a comprehensive, evidence-based investigation and architectural design for integrating missing dose imputation into the database (`db.py`, `schema.sql`) and API layer (`app.py`).

Key findings and proposed designs:
1. **Database Schema**: The existing PostgreSQL table `insulin_doses` requires two new columns: `is_imputed` (`BOOLEAN DEFAULT FALSE`) and `confidence_score` (`DOUBLE PRECISION` / `FLOAT`). Safe execution requires `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` inside `init_db()` and `schema.sql` so existing deployments migrate automatically without error or data loss.
2. **Database Helper Functions**: `insert_insulin_doses()` and `get_insulin_history()` in `db.py` must be extended to accept, insert, and return `is_imputed` and `confidence_score`. `get_insulin_history()` should support filtering or returning both logged and imputed records.
3. **API Endpoint Contract**: GET `/api/insulin/history` in `app.py` must parse query parameters `hours: int = 24` and `include_imputed: bool = False`. When `include_imputed=true`, the endpoint invokes the missing dose deconvolution model (from `imputation.py`) using surrounding glucose readings (`get_history(hours)`), tags imputed doses with `"is_imputed": true` and calculated `"confidence_score"`, merges them with logged doses (`"is_imputed": false`, `"confidence_score": null`), and returns a unified chronologically sorted list.

---

## 1. Direct Observations & Code Audit

### 1.1 `schema.sql` Audit
- **Location**: `schema.sql:21-40`
- **Current Table Definition**:
  ```sql
  CREATE TABLE IF NOT EXISTS insulin_doses (
      id SERIAL PRIMARY KEY,
      timestamp TIMESTAMPTZ NOT NULL,
      rapid_acting DOUBLE PRECISION,  -- Rapid-Acting Insulin (units)
      long_acting DOUBLE PRECISION,   -- Long-Acting Insulin (units)
      meal DOUBLE PRECISION,          -- Meal Insulin (units)
      correction DOUBLE PRECISION,    -- Correction Insulin (units)
      user_change DOUBLE PRECISION,   -- User Change Insulin (units)
      device VARCHAR(100),
      serial_number VARCHAR(100),
      created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
  );

  CREATE UNIQUE INDEX IF NOT EXISTS idx_insulin_doses_unique 
  ON insulin_doses (timestamp, rapid_acting, long_acting, meal, correction, user_change);

  CREATE INDEX IF NOT EXISTS idx_insulin_doses_timestamp ON insulin_doses (timestamp DESC);
  ```

### 1.2 `db.py` Audit
- **Database Engine**: PostgreSQL (`psycopg2` driver).
- **`init_db()`** (`db.py:18-50`):
  Executes `schema.sql` statements and cleans up duplicate insulin doses based on `timestamp`.
  *Issue*: `CREATE TABLE IF NOT EXISTS` does not add columns if `insulin_doses` table already exists in the database.
- **`insert_insulin_doses(doses)`** (`db.py:166-215`):
  Checks existing timestamps using `SELECT DISTINCT timestamp FROM insulin_doses WHERE timestamp = ANY(%s)`.
  Inserts 8 columns: `(timestamp, rapid_acting, long_acting, meal, correction, user_change, device, serial_number)`.
- **`get_insulin_history(limit_hours=24)`** (`db.py:217-234`):
  Queries 9 columns: `id, timestamp, rapid_acting, long_acting, meal, correction, user_change, device, serial_number`.
  Does not currently fetch `is_imputed` or `confidence_score`.

### 1.3 `app.py` Audit
- **Framework**: FastAPI with Uvicorn.
- **Endpoint `/api/insulin/history`** (`app.py:46-52`):
  ```python
  @app.get("/api/insulin/history")
  def api_insulin_history(hours: int = Query(default=24, ge=1, le=4320)):
      """Retrieves insulin logs within the last N hours."""
      doses = get_insulin_history(hours)
      for d in doses:
          d['timestamp'] = d['timestamp'].isoformat()
      return doses
  ```
  Currently lacks `include_imputed` parameter and imputation model integration.

---

## 2. Architectural Design & Proposed Solutions

### 2.1 Database Schema Updates & Migration Strategy

#### Schema Column Additions
1. `is_imputed BOOLEAN DEFAULT FALSE` — Indicates whether the insulin dose is an imputed estimate (`True`) or a logged/imported dose (`False`).
2. `confidence_score DOUBLE PRECISION DEFAULT NULL` — Stores the confidence score (float between 0.0 and 1.0) for imputed doses. Null/None for logged doses.

#### Migration / Initialization SQL
To ensure backward compatibility and idempotent execution, `schema.sql` and `init_db()` must include `ALTER TABLE` statements:
```sql
ALTER TABLE insulin_doses ADD COLUMN IF NOT EXISTS is_imputed BOOLEAN DEFAULT FALSE;
ALTER TABLE insulin_doses ADD COLUMN IF NOT EXISTS confidence_score DOUBLE PRECISION;
```

#### Unique Index Consideration
The existing unique index `idx_insulin_doses_unique` prevents duplicate logs on `(timestamp, rapid_acting, long_acting, meal, correction, user_change)`. Since logged doses have `is_imputed = FALSE` and imputed doses may be dynamically calculated or stored, keeping this index intact ensures logged manual/CSV doses will not collide with imputed records.

---

### 2.2 `db.py` Helper Function Updates

#### 1. `init_db()`
Add safe column migrations:
```python
def init_db():
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    if not os.path.exists(schema_path):
        print("schema.sql not found, skipping table initialization.")
        return

    with open(schema_path, "r") as f:
        schema_sql = f.read()

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(schema_sql)
            
            # Ensure columns exist on existing table instances
            cur.execute("""
                ALTER TABLE insulin_doses ADD COLUMN IF NOT EXISTS is_imputed BOOLEAN DEFAULT FALSE;
                ALTER TABLE insulin_doses ADD COLUMN IF NOT EXISTS confidence_score DOUBLE PRECISION;
            """)
            
            # Clean up duplicate insulin records
            cur.execute("""
                DELETE FROM insulin_doses 
                WHERE id NOT IN (
                    SELECT MIN(id) 
                    FROM insulin_doses 
                    GROUP BY timestamp
                )
            """)
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
```

#### 2. `insert_insulin_doses(doses)`
Support inserting optional `is_imputed` and `confidence_score`:
```python
def insert_insulin_doses(doses):
    if not doses:
        return 0

    conn = get_connection()
    inserted_count = 0
    try:
        with conn.cursor() as cur:
            timestamps = [d["timestamp"] for d in doses]
            cur.execute("SELECT DISTINCT timestamp FROM insulin_doses WHERE timestamp = ANY(%s)", (timestamps,))
            existing_timestamps = {row[0] for row in cur.fetchall()}

            filtered_doses = [d for d in doses if d["timestamp"] not in existing_timestamps]
            if not filtered_doses:
                return 0

            data = [
                (
                    d["timestamp"],
                    d.get("rapid_acting"),
                    d.get("long_acting"),
                    d.get("meal"),
                    d.get("correction"),
                    d.get("user_change"),
                    d.get("device"),
                    d.get("serial_number"),
                    d.get("is_imputed", False),
                    d.get("confidence_score")
                )
                for d in filtered_doses
            ]
            
            query = """
                INSERT INTO insulin_doses (
                    timestamp, rapid_acting, long_acting, meal, correction, user_change, device, serial_number, is_imputed, confidence_score
                ) VALUES %s
            """
            execute_values(cur, query, data)
            inserted_count = len(filtered_doses)
        conn.commit()
        return inserted_count
    except Exception as e:
        conn.rollback()
        print(f"Error inserting insulin doses: {e}")
        return 0
    finally:
        conn.close()
```

#### 3. `get_insulin_history(limit_hours=24, include_imputed=False)`
Update select query to include `is_imputed` and `confidence_score`, with optional SQL filtering:
```python
def get_insulin_history(limit_hours=24, include_imputed=False):
    """Retrieves insulin logs sorted chronologically."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if include_imputed:
                cur.execute("""
                    SELECT id, timestamp, rapid_acting, long_acting, meal, correction, user_change, device, serial_number, is_imputed, confidence_score 
                    FROM insulin_doses 
                    WHERE timestamp >= NOW() - INTERVAL %s
                    ORDER BY timestamp ASC
                """, (f"{limit_hours} hours",))
            else:
                cur.execute("""
                    SELECT id, timestamp, rapid_acting, long_acting, meal, correction, user_change, device, serial_number, is_imputed, confidence_score 
                    FROM insulin_doses 
                    WHERE timestamp >= NOW() - INTERVAL %s
                      AND (is_imputed IS FALSE OR is_imputed IS NULL)
                    ORDER BY timestamp ASC
                """, (f"{limit_hours} hours",))
            return cur.fetchall()
    except Exception as e:
        print(f"Error fetching insulin history: {e}")
        return []
    finally:
        conn.close()
```

---

### 2.3 API Endpoint `/api/insulin/history` Integration

#### Query Parameter Parsing
FastAPI parameter definition:
```python
@app.get("/api/insulin/history")
def api_insulin_history(
    hours: int = Query(default=24, ge=1, le=4320),
    include_imputed: bool = Query(default=False, description="Include estimated missing insulin doses")
):
```

#### Imputation Dynamic Evaluation & Merging Flow
When `include_imputed=False`:
Returns logged doses (`is_imputed: false`, `confidence_score: null`).

When `include_imputed=True`:
1. Retrieve logged doses for the requested `hours`: `logged_doses = get_insulin_history(hours, include_imputed=False)`.
2. Retrieve glucose readings for the requested timeframe: `glucose_readings = get_history(hours + 4)`.
3. Invoke the imputation engine: `imputed_doses = detect_and_impute_missing_doses(glucose_readings, logged_doses)`.
4. Ensure default metadata formatting:
   - For logged doses: set `is_imputed` to `False` if None, `confidence_score` to `None`.
   - For imputed doses: ensure `is_imputed` is `True`, `confidence_score` is a float (e.g. `0.85`), `id` is `None` (or negative placeholder), and dose components (`rapid_acting`, `correction`) match contract specifications.
5. Merge `logged_doses` and `imputed_doses`, sort by `timestamp` ascending.
6. Format `timestamp` field to ISO 8601 string (`.isoformat()`).

#### JSON Output Contract Verification
The returned list matches the exact schema defined in `PROJECT.md`:
```json
[
  {
    "id": 12,
    "timestamp": "2026-08-04T05:30:00+00:00",
    "rapid_acting": 3.0,
    "long_acting": 0.0,
    "meal": 3.0,
    "correction": 0.0,
    "user_change": 0.0,
    "device": "FreeStyle Libre",
    "serial_number": "SN12345",
    "is_imputed": false,
    "confidence_score": null
  },
  {
    "id": null,
    "timestamp": "2026-08-04T07:15:00+00:00",
    "rapid_acting": 2.5,
    "long_acting": 0.0,
    "meal": 0.0,
    "correction": 2.5,
    "user_change": 0.0,
    "device": "Imputation Engine",
    "serial_number": null,
    "is_imputed": true,
    "confidence_score": 0.88
  }
]
```

---

## 3. Code Change Proposals

### Proposal 1: `schema.sql` Updates
```sql
-- Insulin doses table
CREATE TABLE IF NOT EXISTS insulin_doses (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    rapid_acting DOUBLE PRECISION,  -- Rapid-Acting Insulin (units)
    long_acting DOUBLE PRECISION,   -- Long-Acting Insulin (units)
    meal DOUBLE PRECISION,          -- Meal Insulin (units)
    correction DOUBLE PRECISION,    -- Correction Insulin (units)
    user_change DOUBLE PRECISION,   -- User Change Insulin (units)
    device VARCHAR(100),
    serial_number VARCHAR(100),
    is_imputed BOOLEAN DEFAULT FALSE,
    confidence_score DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
```

### Proposal 2: `app.py` Endpoint Implementation
```python
@app.get("/api/insulin/history")
def api_insulin_history(
    hours: int = Query(default=24, ge=1, le=4320),
    include_imputed: bool = Query(default=False, description="Include imputed missing insulin doses")
):
    """Retrieves insulin logs within the last N hours."""
    logged_doses = get_insulin_history(hours, include_imputed=False)
    
    # Ensure default fields on logged doses
    for d in logged_doses:
        d['is_imputed'] = bool(d.get('is_imputed', False))
        d['confidence_score'] = d.get('confidence_score')
        d['timestamp'] = d['timestamp'].isoformat() if isinstance(d['timestamp'], datetime) else str(d['timestamp'])

    if not include_imputed:
        return logged_doses

    # Integrate missing dose deconvolution if requested
    try:
        from imputation import detect_and_impute_missing_doses
        glucose_readings = get_history(hours + 4)
        imputed_doses = detect_and_impute_missing_doses(glucose_readings, logged_doses)
        
        for imp in imputed_doses:
            imp['is_imputed'] = True
            if isinstance(imp.get('timestamp'), datetime):
                imp['timestamp'] = imp['timestamp'].isoformat()
            
        combined = logged_doses + imputed_doses
        combined.sort(key=lambda x: x['timestamp'])
        return combined
    except Exception as e:
        print(f"Imputation engine calculation failed: {e}")
        return logged_doses
```

---

## 4. Impact Assessment & Edge Case Handling

1. **DB Column Migration Safety**: Using `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` guarantees that existing environments will auto-upgrade without crashing or requiring table recreation.
2. **Missing `imputation.py` Fallback**: Defensive try-except block in `app.py` ensures that if `imputation.py` fails or is not yet loaded, the API gracefully falls back to returning logged doses without crashing the server.
3. **Data Type Consistency**: Ensures `is_imputed` is strictly a boolean (`True`/`False`) and `confidence_score` is float or null, ensuring full compatibility with Chart.js frontend rendering.
