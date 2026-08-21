import os
import threading
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Union
import psycopg2
import psycopg2.extras
from psycopg2.extras import execute_values
from dotenv import load_dotenv

# Load env variables (useful for local development)
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

_init_db_lock = threading.Lock()

def get_connection():
    """Establishes and returns a connection to the PostgreSQL database."""
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable is not set.")
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """Initializes the database and cleans up any duplicate insulin logs."""
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    if not os.path.exists(schema_path):
        print("schema.sql not found, skipping table initialization.")
        return

    with open(schema_path, "r") as f:
        schema_sql = f.read()

    with _init_db_lock:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                # Acquire advisory lock to prevent concurrent DDL deadlocks across threads/processes
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
                    
                    # Safe schema migrations for food_logs
                    try:
                        cur.execute("ALTER TABLE food_logs ADD COLUMN IF NOT EXISTS is_imputed BOOLEAN DEFAULT FALSE;")
                        cur.execute("ALTER TABLE food_logs ADD COLUMN IF NOT EXISTS confidence_score DOUBLE PRECISION;")
                        cur.execute("ALTER TABLE insulin_doses ADD COLUMN IF NOT EXISTS synced_to_libreview BOOLEAN DEFAULT FALSE;")
                        cur.execute("ALTER TABLE food_logs ADD COLUMN IF NOT EXISTS synced_to_libreview BOOLEAN DEFAULT FALSE;")
                    except psycopg2.errors.UndefinedTable:
                        pass # food_logs doesn't exist yet, that's fine, schema.sql creates it. Wait, schema_sql is executed before this.
                        
                finally:
                    cur.execute("SELECT pg_advisory_unlock(987654321);")
            conn.commit()
            print("Database initialized and schema updated.")
        except Exception as e:
            conn.rollback()
            print(f"Error initializing database: {e}")
            raise e
        finally:
            conn.close()

def insert_readings(readings):
    """
    Inserts a list of readings into the database, ignoring duplicates.
    Each reading in the list should be a tuple or dictionary containing:
    (timestamp, value, type, device, serial_number, record_type)
    """
    if not readings:
        return 0

    conn = get_connection()
    inserted_count = 0
    try:
        with conn.cursor() as cur:
            # We use INSERT ... ON CONFLICT (timestamp, value) DO NOTHING
            # to prevent duplicates based on our unique index.
            query = """
                INSERT INTO glucose_readings 
                (timestamp, value, type, device, serial_number, record_type)
                VALUES %s
                ON CONFLICT (timestamp, value) DO NOTHING
            """
            
            # Formatting the readings tuple list
            data = [
                (
                    r['timestamp'], 
                    r['value'], 
                    r['type'], 
                    r.get('device'), 
                    r.get('serial_number'), 
                    r.get('record_type')
                )
                for r in readings
            ]
            
            execute_values(cur, query, data)
            inserted_count = cur.rowcount
            
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Error inserting readings: {e}")
        raise e
    finally:
        conn.close()
        
    return inserted_count

def get_latest_reading():
    """Fetches the single most recent glucose reading."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, timestamp, value, type, device, serial_number 
                FROM glucose_readings 
                ORDER BY timestamp DESC 
                LIMIT 1
            """)
            return cur.fetchone()
    except Exception as e:
        print(f"Error fetching latest reading: {e}")
        return None
    finally:
        conn.close()

def get_history(limit_hours=24):
    """Fetches historical readings within the last N hours, ordered chronologically."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, timestamp, value, type, device, serial_number 
                FROM glucose_readings 
                WHERE timestamp >= NOW() - INTERVAL %s
                ORDER BY timestamp ASC
            """, (f"{limit_hours} hours",))
            return cur.fetchall()
    except Exception as e:
        print(f"Error fetching history: {e}")
        return []
    finally:
        conn.close()

def get_statistics(hours=24):
    """Computes key metrics like Average Glucose, GMI (Est. A1c), and Time-in-Range percentages."""
    readings = get_history(hours)
    if not readings:
        return None
    
    values = [r['value'] for r in readings]
    avg_glucose = sum(values) / len(values)
    
    # GMI (%) = 3.31 + 0.02392 * [mean glucose in mg/dL]
    gmi = 3.31 + (0.02392 * avg_glucose)
    
    low_count = sum(1 for v in values if v < 70)
    in_range_count = sum(1 for v in values if 70 <= v <= 180)
    high_count = sum(1 for v in values if v > 180)
    very_high_count = sum(1 for v in values if v > 250)
    
    total = len(values)
    
    return {
        "total_readings": total,
        "average_glucose": round(avg_glucose, 1),
        "gmi": round(gmi, 2),
        "time_in_range": {
            "low_percent": round((low_count / total) * 100, 1),
            "target_percent": round((in_range_count / total) * 100, 1),
            "high_percent": round((high_count / total) * 100, 1),
            "very_high_percent": round((very_high_count / total) * 100, 1),
        }
    }

def insert_insulin_doses(doses):
    """
    Inserts a list of insulin doses into the database, ignoring duplicates.
    """
    if not doses:
        return 0

    conn = get_connection()
    inserted_count = 0
    try:
        with conn.cursor() as cur:
            # Fetch existing timestamps in the range of the incoming doses to filter duplicates
            timestamps = [d["timestamp"] for d in doses]
            cur.execute("SELECT DISTINCT timestamp FROM insulin_doses WHERE timestamp = ANY(%s)", (timestamps,))
            existing_timestamps = {row[0] for row in cur.fetchall()}

            # Filter out duplicates
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

def get_insulin_history(limit_hours=24, include_imputed=False):
    """Retrieves insulin logs sorted chronologically."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                if include_imputed:
                    query = """
                        SELECT id, timestamp, rapid_acting, long_acting, meal, correction, user_change, device, serial_number, is_imputed, confidence_score 
                        FROM insulin_doses 
                        WHERE timestamp >= NOW() - INTERVAL %s
                        ORDER BY timestamp ASC
                    """
                else:
                    query = """
                        SELECT id, timestamp, rapid_acting, long_acting, meal, correction, user_change, device, serial_number, is_imputed, confidence_score 
                        FROM insulin_doses 
                        WHERE timestamp >= NOW() - INTERVAL %s AND (is_imputed IS NOT TRUE)
                        ORDER BY timestamp ASC
                    """
                cur.execute(query, (f"{limit_hours} hours",))
                return cur.fetchall()
            except Exception as col_err:
                conn.rollback()
                # Fallback for database tables prior to migration
                query = """
                    SELECT id, timestamp, rapid_acting, long_acting, meal, correction, user_change, device, serial_number 
                    FROM insulin_doses 
                    WHERE timestamp >= NOW() - INTERVAL %s
                    ORDER BY timestamp ASC
                """
                cur.execute(query, (f"{limit_hours} hours",))
                rows = cur.fetchall()
                for r in rows:
                    r["is_imputed"] = False
                    r["confidence_score"] = None
                return rows
    except Exception as e:
        print(f"Error fetching insulin history: {e}")
        return []
    finally:
        conn.close()

def delete_insulin_dose(dose_id: int) -> bool:
    """Deletes an insulin dose entry by its database ID."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM insulin_doses WHERE id = %s", (dose_id,))
            deleted = cur.rowcount
            conn.commit()
            return deleted > 0
    except Exception as e:
        print(f"Error deleting insulin dose {dose_id}: {e}")
        conn.rollback()
        raise e
    finally:
        conn.close()

def insert_food_log(carbs_g, timestamp, food_type=None, is_imputed=False, confidence_score=None):
    """Inserts a food/carbohydrate log."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO food_logs (timestamp, carbs_g, food_type, is_imputed, confidence_score)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (timestamp, carbs_g, food_type) DO NOTHING
                RETURNING id
            """, (timestamp, carbs_g, food_type, is_imputed, confidence_score))
            
            inserted = cur.fetchone()
            conn.commit()
            return inserted[0] if inserted else None
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def get_food_history(limit_hours=24, include_imputed=False):
    """Retrieves food logs for the given time window."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            query = """
                SELECT id, timestamp, carbs_g, food_type, is_imputed, confidence_score
                FROM food_logs 
                WHERE timestamp >= NOW() - INTERVAL %s
            """
            if not include_imputed:
                query += " AND is_imputed = FALSE"
                
            query += " ORDER BY timestamp ASC"
            
            cur.execute(query, (f"{limit_hours} hours",))
            return cur.fetchall()
    finally:
        conn.close()

import json

def get_system_setting(key, default=None):
    """Retrieves a JSON system setting by key."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM system_settings WHERE key = %s", (key,))
            row = cur.fetchone()
            if row:
                return row[0]
            return default
    except Exception as e:
        print(f"Error getting system setting {key}: {e}")
        return default
    finally:
        conn.close()

def set_system_setting(key, value):
    """Sets a JSON system setting by key."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO system_settings (key, value, updated_at)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (key) DO UPDATE SET 
                    value = EXCLUDED.value,
                    updated_at = EXCLUDED.updated_at
            """, (key, json.dumps(value)))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Error setting system setting {key}: {e}")
    finally:
        conn.close()


def get_unsynced_events():
    """Retrieves all non-imputed doses and food logs that haven't been synced to LibreView."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, timestamp, rapid_acting, long_acting, meal, correction, user_change, 'insulin' as event_type 
                FROM insulin_doses 
                WHERE is_imputed = FALSE AND synced_to_libreview = FALSE
                UNION ALL
                SELECT id, timestamp, NULL, NULL, carbs_g, NULL, NULL, 'food' as event_type
                FROM food_logs
                WHERE is_imputed = FALSE AND synced_to_libreview = FALSE
                ORDER BY timestamp ASC
            """)
            return cur.fetchall()
    except Exception as e:
        print(f"Error fetching unsynced events: {e}")
        return []
    finally:
        conn.close()

def mark_event_synced(event_id, event_type):
    """Marks an event as synced to LibreView."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if event_type == 'insulin':
                cur.execute("UPDATE insulin_doses SET synced_to_libreview = TRUE WHERE id = %s", (event_id,))
            elif event_type == 'food':
                cur.execute("UPDATE food_logs SET synced_to_libreview = TRUE WHERE id = %s", (event_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Error marking event synced: {e}")
    finally:
        conn.close()


def insert_health_sessions(sessions):
    """
    Inserts or updates a list of Google Health / Fit sessions (sleep, activity).
    Each session dict: {
        'session_id': str,
        'start_time': datetime/ISO,
        'end_time': datetime/ISO,
        'session_type': str (e.g. 'sleep', 'sleep.deep', 'sleep.light', 'sleep.rem', 'activity'),
        'session_name': str,
        'duration_minutes': float
    }
    """
    if not sessions:
        return 0

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            query = """
                INSERT INTO health_sessions (
                    session_id, start_time, end_time, session_type, session_name, duration_minutes
                ) VALUES %s
                ON CONFLICT (session_id) DO UPDATE SET
                    start_time = EXCLUDED.start_time,
                    end_time = EXCLUDED.end_time,
                    session_type = EXCLUDED.session_type,
                    session_name = EXCLUDED.session_name,
                    duration_minutes = EXCLUDED.duration_minutes
            """
            data = [
                (
                    s['session_id'],
                    s['start_time'],
                    s['end_time'],
                    s.get('session_type', 'sleep'),
                    s.get('session_name'),
                    s.get('duration_minutes')
                )
                for s in sessions
            ]
            execute_values(cur, query, data)
            count = len(sessions)
        conn.commit()
        return count
    except Exception as e:
        conn.rollback()
        print(f"Error inserting health sessions: {e}")
        return 0
    finally:
        conn.close()


def get_health_sessions(limit_hours=720, session_type=None):
    """Retrieves health sessions within the last limit_hours."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if session_type:
                query = """
                    SELECT id, session_id, start_time, end_time, session_type, session_name, duration_minutes, created_at
                    FROM health_sessions
                    WHERE start_time >= NOW() - INTERVAL %s AND session_type ILIKE %s
                    ORDER BY start_time DESC
                """
                cur.execute(query, (f"{limit_hours} hours", f"{session_type}%"))
            else:
                query = """
                    SELECT id, session_id, start_time, end_time, session_type, session_name, duration_minutes, created_at
                    FROM health_sessions
                    WHERE start_time >= NOW() - INTERVAL %s
                    ORDER BY start_time DESC
                """
                cur.execute(query, (f"{limit_hours} hours",))
            return cur.fetchall()
    except Exception as e:
        print(f"Error fetching health sessions: {e}")
        return []
    finally:
        conn.close()


def insert_health_metrics(metrics):
    """
    Inserts a list of health metrics (e.g. steps, heart_rate).
    Each dict: {'timestamp': dt, 'metric_type': str, 'value': float}
    """
    if not metrics:
        return 0
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            query = """
                INSERT INTO health_metrics (timestamp, metric_type, value)
                VALUES %s
                ON CONFLICT (timestamp, metric_type) DO UPDATE SET
                    value = EXCLUDED.value
            """
            data = [(m['timestamp'], m['metric_type'], float(m['value'])) for m in metrics]
            execute_values(cur, query, data)
            count = len(metrics)
        conn.commit()
        return count
    except Exception as e:
        conn.rollback()
        print(f"Error inserting health metrics: {e}")
        return 0
    finally:
        conn.close()


def get_health_metrics(limit_hours=720, metric_type=None):
    """Retrieves health metrics within the last limit_hours."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if metric_type:
                query = """
                    SELECT id, timestamp, metric_type, value
                    FROM health_metrics
                    WHERE timestamp >= NOW() - INTERVAL %s AND metric_type = %s
                    ORDER BY timestamp ASC
                """
                cur.execute(query, (f"{limit_hours} hours", metric_type))
            else:
                query = """
                    SELECT id, timestamp, metric_type, value
                    FROM health_metrics
                    WHERE timestamp >= NOW() - INTERVAL %s
                    ORDER BY timestamp ASC
                """
                cur.execute(query, (f"{limit_hours} hours",))
            return cur.fetchall()
    except Exception as e:
        print(f"Error fetching health metrics: {e}")
        return []
    finally:
        conn.close()


def get_recent_sleep_summary(hours=48):
    """
    Calculates summary of recent sleep from health_sessions and health_metrics:
    - leverages circadian_analysis.py for sleep architecture, circadian phase, RHR dipping, and dynamic ISF modifier
    - maintains full backward compatibility for all existing consumers
    """
    sessions = get_health_sessions(limit_hours=hours, session_type="sleep")
    metrics = get_health_metrics(limit_hours=hours, metric_type="heart_rate")

    if not sessions:
        return {
            "has_data": False,
            "total_sleep_hours_24h": 0.0,
            "isf_modifier": 1.0,
            "latest_session": None,
            "sessions": [],
            "session_count": 0,
            "sleep_quality_rating": "unknown",
            "lifestyle_impact_note": "No Google Fit sleep data recorded yet.",
            "efficiency_percent": 0.0,
            "deep_sleep_pct": 0.0,
            "rem_sleep_pct": 0.0,
            "light_sleep_pct": 0.0,
            "deep_rem_ratio": 0.0,
            "rhr_dipping_pct": None,
            "chronotype": "Unknown",
            "sleep_midpoint": None
        }

    from circadian_analysis import (
        calculate_sleep_stage_analytics,
        calculate_circadian_phase,
        calculate_nocturnal_rhr_metrics,
        calculate_dynamic_isf_modifier
    )

    stage_analytics = calculate_sleep_stage_analytics(sessions)
    circadian_phase = calculate_circadian_phase(sessions)
    rhr_metrics = calculate_nocturnal_rhr_metrics(metrics, sleep_sessions=sessions)
    isf_data = calculate_dynamic_isf_modifier(sleep_summary=stage_analytics, rhr_summary=rhr_metrics)

    latest_s = sessions[0] if sessions else None
    latest_session_dict = None
    if latest_s:
        latest_session_dict = {
            "start": latest_s['start_time'].isoformat() if isinstance(latest_s.get('start_time'), datetime) else latest_s.get('start_time'),
            "end": latest_s['end_time'].isoformat() if isinstance(latest_s.get('end_time'), datetime) else latest_s.get('end_time'),
            "duration_minutes": latest_s.get('duration_minutes', 0),
            "type": latest_s.get('session_type', 'sleep'),
            "name": latest_s.get('session_name', 'Sleep')
        }

    total_hours = stage_analytics["total_sleep_hours"]
    efficiency = stage_analytics["efficiency_percent"]
    deep_pct = stage_analytics["deep_sleep_percent"]
    rem_pct = stage_analytics["rem_sleep_percent"]
    light_pct = stage_analytics["light_sleep_percent"]
    deep_rem_ratio = stage_analytics["restorative_ratio"]

    return {
        "has_data": True,
        "total_sleep_hours_24h": total_hours,
        "isf_modifier": isf_data["isf_modifier"],
        "sleep_quality_rating": stage_analytics["quality_rating"],
        "lifestyle_impact_note": isf_data["lifestyle_impact_note"],
        "efficiency_percent": efficiency,
        "deep_sleep_pct": deep_pct,
        "rem_sleep_pct": rem_pct,
        "light_sleep_pct": light_pct,
        "deep_rem_ratio": deep_rem_ratio,
        "rhr_dipping_pct": rhr_metrics["dipping_percent"],
        "chronotype": circadian_phase["chronotype"],
        "sleep_midpoint": circadian_phase["sleep_midpoint"],
        "latest_session": latest_session_dict,
        "sessions": sessions,
        "session_count": stage_analytics["session_count"],
        "analytics": stage_analytics,
        "rhr_metrics": rhr_metrics,
        "isf_breakdown": isf_data
    }


# =============================================================================
# MEDICATION MANAGEMENT (MedFlowAssist & Ecosystem)
# =============================================================================

def get_medication_presets(active_only: bool = True) -> List[Dict[str, Any]]:
    """Retrieves all medication presets ordered alphabetically by name."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if active_only:
                cur.execute(
                    "SELECT id, name, default_dose, dose_unit, is_active, created_at "
                    "FROM medication_types WHERE is_active = TRUE ORDER BY name ASC;"
                )
            else:
                cur.execute(
                    "SELECT id, name, default_dose, dose_unit, is_active, created_at "
                    "FROM medication_types ORDER BY name ASC;"
                )
            return cur.fetchall()
    finally:
        conn.close()


def get_medication_preset_by_id(preset_id: int) -> Optional[Dict[str, Any]]:
    """Fetches a specific medication preset by primary key."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, name, default_dose, dose_unit, is_active, created_at "
                "FROM medication_types WHERE id = %s;",
                (preset_id,)
            )
            return cur.fetchone()
    finally:
        conn.close()


def get_medication_preset_by_name(name: str) -> Optional[Dict[str, Any]]:
    """Fetches a medication preset using case-insensitive name lookup."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, name, default_dose, dose_unit, is_active, created_at "
                "FROM medication_types WHERE LOWER(name) = LOWER(%s);",
                (name.strip(),)
            )
            return cur.fetchone()
    finally:
        conn.close()


def add_medication_preset(name: str, default_dose: float, dose_unit: str) -> int:
    """
    Inserts a new medication preset or updates an existing one (case-insensitive).
    Reactivates soft-deleted presets (is_active = TRUE).
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO medication_types (name, default_dose, dose_unit, is_active)
                VALUES (%s, %s, %s, TRUE)
                ON CONFLICT (LOWER(name)) DO UPDATE 
                SET name = EXCLUDED.name,
                    default_dose = EXCLUDED.default_dose,
                    dose_unit = EXCLUDED.dose_unit,
                    is_active = TRUE
                RETURNING id;
                """,
                (name.strip(), default_dose, dose_unit.strip())
            )
            med_id = cur.fetchone()[0]
        conn.commit()
        return med_id
    finally:
        conn.close()


def delete_medication_preset(name_or_id: Union[str, int]) -> bool:
    """
    Soft-deletes a medication preset by ID or case-insensitive name (sets is_active = FALSE).
    Returns True if a preset was found and deactivated, False otherwise.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if isinstance(name_or_id, int) or (isinstance(name_or_id, str) and str(name_or_id).strip().isdigit()):
                cur.execute(
                    "UPDATE medication_types SET is_active = FALSE WHERE id = %s AND is_active = TRUE RETURNING id;",
                    (int(name_or_id),)
                )
            else:
                cur.execute(
                    "UPDATE medication_types SET is_active = FALSE WHERE LOWER(name) = LOWER(%s) AND is_active = TRUE RETURNING id;",
                    (str(name_or_id).strip(),)
                )
            row = cur.fetchone()
        conn.commit()
        return row is not None
    finally:
        conn.close()


def log_medication_dose(
    medication_id: int,
    dose_taken: float,
    timestamp: Optional[datetime] = None,
    notes: Optional[str] = None
) -> int:
    """Logs a single medication intake entry into medication_logs."""
    ts = timestamp or datetime.now(timezone.utc)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO medication_logs (medication_id, timestamp, dose_taken, notes)
                VALUES (%s, %s, %s, %s)
                RETURNING id;
                """,
                (medication_id, ts, dose_taken, notes)
            )
            log_id = cur.fetchone()[0]
        conn.commit()
        return log_id
    finally:
        conn.close()


def get_recent_med_logs(
    limit: int = 15,
    medication_name: Optional[str] = None,
    medication_id: Optional[int] = None,
    hours: Optional[int] = None,
    limit_hours: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Fetches medication intake logs in reverse chronological order (newest first).
    Supports filtering by medication name (case-insensitive substring/exact), ID, or time window.
    """
    effective_hours = hours if hours is not None else limit_hours
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            query = """
                SELECT 
                    l.id,
                    l.medication_id,
                    t.name AS name,
                    t.dose_unit,
                    l.dose_taken,
                    l.timestamp,
                    l.notes,
                    l.created_at
                FROM medication_logs l
                JOIN medication_types t ON l.medication_id = t.id
                WHERE 1=1
            """
            params: List[Any] = []

            if medication_id is not None:
                query += " AND l.medication_id = %s"
                params.append(medication_id)
            elif medication_name:
                query += " AND (LOWER(t.name) = LOWER(%s) OR t.name ILIKE %s)"
                params.extend([medication_name.strip(), f"%{medication_name.strip()}%"])

            if effective_hours is not None and effective_hours > 0:
                query += " AND l.timestamp >= NOW() - INTERVAL %s"
                params.append(f"{effective_hours} hours")

            query += " ORDER BY l.timestamp DESC LIMIT %s;"
            params.append(limit)

            cur.execute(query, tuple(params))
            return cur.fetchall()
    finally:
        conn.close()


def get_medication_summary(
    medication_name: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Aggregates active medication types with:
    - Default preset dosage and unit
    - Most recent intake timestamp, dose taken, and notes
    - 24-hour intake count and total dosage amount
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            query = """
                WITH recent_24h AS (
                    SELECT 
                        medication_id,
                        COUNT(*) AS count_24h,
                        SUM(dose_taken) AS total_dose_24h
                    FROM medication_logs
                    WHERE timestamp >= NOW() - INTERVAL '24 hours'
                    GROUP BY medication_id
                ),
                latest_doses AS (
                    SELECT DISTINCT ON (medication_id)
                        medication_id,
                        timestamp AS last_timestamp,
                        dose_taken AS last_dose_taken,
                        notes AS last_notes
                    FROM medication_logs
                    ORDER BY medication_id, timestamp DESC
                )
                SELECT 
                    t.id,
                    t.name,
                    t.default_dose,
                    t.dose_unit,
                    t.is_active,
                    ld.last_timestamp,
                    ld.last_dose_taken,
                    ld.last_notes,
                    COALESCE(r24.count_24h, 0) AS count_24h,
                    COALESCE(r24.total_dose_24h, 0.0) AS total_dose_24h
                FROM medication_types t
                LEFT JOIN latest_doses ld ON t.id = ld.medication_id
                LEFT JOIN recent_24h r24 ON t.id = r24.medication_id
                WHERE t.is_active = TRUE
            """
            params: List[Any] = []
            if medication_name:
                query += " AND (LOWER(t.name) = LOWER(%s) OR t.name ILIKE %s)"
                params.extend([medication_name.strip(), f"%{medication_name.strip()}%"])

            query += " ORDER BY t.name ASC;"

            cur.execute(query, tuple(params) if params else None)
            return cur.fetchall()
    finally:
        conn.close()


def delete_medication_log(log_id: int) -> bool:
    """Deletes a single medication log entry by primary key ID."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM medication_logs WHERE id = %s RETURNING id;", (log_id,))
            deleted = cur.fetchone()
        conn.commit()
        return deleted is not None
    finally:
        conn.close()

