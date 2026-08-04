import os
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

# Load env variables (useful for local development)
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

def get_connection():
    """Establishes and returns a connection to the PostgreSQL database."""
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable is not set.")
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """Initializes the database by executing schema.sql if tables do not exist."""
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
        conn.commit()
        print("Database initialized successfully.")
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
