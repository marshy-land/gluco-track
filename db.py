import os
import threading
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
