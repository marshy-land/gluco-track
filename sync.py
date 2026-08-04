import os
import sys
import argparse
from datetime import datetime
import pytz
import requests
from dotenv import load_dotenv

# Load env variables (useful for local development)
load_dotenv()

# We import db logic. If DB isn't configured, we'll gracefully handle it (e.g. during dry-runs)
try:
    from db import insert_readings, init_db
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False

# App constants
LLU_PRODUCT = "llu.android"
LLU_VERSION = os.getenv("LIBRE_LINK_UP_VERSION", "4.16.0")

REGIONS = {
    "us": "https://api-us.libreview.io",
    "eu": "https://api-eu.libreview.io",
    "eu2": "https://api-eu2.libreview.io",
    "ap": "https://api-ap.libreview.io",
    "co": "https://api-co.libreview.io",
    "ae": "https://api-ae.libreview.io",
}

class LibreLinkUpClient:
    def __init__(self, email, password, region="us"):
        self.email = email
        self.password = password
        self.region = region.lower()
        self.base_url = REGIONS.get(self.region, REGIONS["us"])
        self.token = None
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json",
            "product": LLU_PRODUCT,
            "version": LLU_VERSION,
            "Accept": "*/*",
        }

    def login(self):
        """Authenticates with the LibreLinkUp API and stores the auth token."""
        url = f"{self.base_url}/llu/auth/login"
        payload = {
            "email": self.email,
            "password": self.password,
        }
        
        print(f"Logging into LibreLinkUp at {url}...")
        response = requests.post(url, json=payload, headers=self.headers)
        
        if response.status_code != 200:
            body = response.text
            raise RuntimeError(
                f"Login failed (HTTP {response.status_code}): {body}\n"
                "Please verify your credentials and region."
            )

        data = response.json()
        status = data.get("status")
        if status != 0:
            raise RuntimeError(f"Login failed (API status {status}): {data.get('message')}")

        auth_ticket = data.get("data", {}).get("authTicket", {})
        self.token = auth_ticket.get("token")
        if not self.token:
            raise RuntimeError("Authentication succeeded but no token was returned.")
            
        # Get user ID and calculate Account-Id SHA-256 hash (required for API version >= 4.16.0)
        import hashlib
        user_data = data.get("data", {})
        user_info = user_data.get("user", {})
        user_id = user_info.get("id")
        
        if user_id:
            account_id_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
            self.headers["Account-Id"] = account_id_hash
            
        print("Login successful.")
        # Update headers with Authorization token
        self.headers["Authorization"] = f"Bearer {self.token}"
        return data

    def get_connections(self):
        """Retrieves connections (patients) linked to the account."""
        if not self.token:
            self.login()

        url = f"{self.base_url}/llu/connections"
        response = requests.get(url, headers=self.headers)
        
        if response.status_code != 200:
            raise RuntimeError(f"Failed to fetch connections (HTTP {response.status_code}): {response.text}")

        data = response.json()
        status = data.get("status")
        if status != 0:
            raise RuntimeError(f"Failed to fetch connections (API status {status}): {data.get('message')}")

        connections = data.get("data", [])
        return connections

    def get_glucose_history(self, patient_id):
        """Retrieves the last 12 hours of glucose data for a specific patient connection."""
        if not self.token:
            self.login()

        url = f"{self.base_url}/llu/connections/{patient_id}/graph"
        response = requests.get(url, headers=self.headers)
        
        if response.status_code != 200:
            raise RuntimeError(f"Failed to fetch glucose data (HTTP {response.status_code}): {response.text}")

        data = response.json()
        status = data.get("status")
        if status != 0:
            # Try to extract the inner error message if present
            err_msg = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else data.get("message")
            raise RuntimeError(f"Failed to fetch glucose data (API status {status}): {err_msg}")

        return data.get("data", {})

def parse_iso_timestamp(ts_str):
    """
    Parses timestamps returned by LibreLinkUp API.
    Handles 'MM/DD/YYYY H:MM:SS AM/PM' or ISO formats like '2026-08-04T12:00:00Z'.
    """
    try:
        # Try parsing ISO 8601 directly
        if "T" in ts_str:
            # Replaces Z with UTC offset if needed, then parses
            ts_str_clean = ts_str.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts_str_clean)
            return dt.astimezone(pytz.utc)
    except ValueError:
        pass

    # Fallback to typical US string date format
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%d/%m/%Y %I:%M:%S %p", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(ts_str, fmt)
            return pytz.utc.localize(dt)
        except ValueError:
            continue

    raise ValueError(f"Unrecognized timestamp format: {ts_str}")

def run_sync(dry_run=False):
    """Logs into LibreLinkUp, fetches recent readings, and inserts them into PostgreSQL."""
    email = os.getenv("LIBRE_LINK_UP_EMAIL")
    password = os.getenv("LIBRE_LINK_UP_PASSWORD")
    region = os.getenv("LIBRE_LINK_UP_REGION", "us")

    if not email or not password:
        print("Error: LIBRE_LINK_UP_EMAIL or LIBRE_LINK_UP_PASSWORD environment variables are missing.")
        sys.exit(1)

    client = LibreLinkUpClient(email, password, region)
    
    try:
        connections = client.get_connections()
        if not connections:
            print("No connections found on this account. Make sure you have set up a connection / follower invite in FreeStyle LibreLink.")
            return

        print(f"Found {len(connections)} connection(s).")
        
        all_readings = []

        for conn in connections:
            patient_id = conn.get("patientId") or conn.get("id")
            patient_name = f"{conn.get('firstName', '')} {conn.get('lastName', '')}".strip()
            print(f"Fetching readings for connection: {patient_name} (Patient ID: {patient_id})")

            # Device info
            sensor_info = conn.get("sensor", {})
            serial_number = sensor_info.get("sn")
            device_model = "FreeStyle Libre"

            data = client.get_glucose_history(patient_id)
            
            # 1. Recent graph readings (last 12 hours)
            graph_data = data.get("graphData", [])
            for item in graph_data:
                try:
                    val = float(item.get("Value"))
                    ts_str = item.get("FactoryTimestamp") or item.get("Timestamp")
                    utc_dt = parse_iso_timestamp(ts_str)

                    all_readings.append({
                        "timestamp": utc_dt,
                        "value": val,
                        "type": "live",
                        "device": device_model,
                        "serial_number": serial_number,
                        "record_type": 0  # equivalent to historic
                    })
                except Exception as ex:
                    print(f"Skipping graph item {item}: {ex}")

            # 2. Most recent reading (single point)
            latest_meas = data.get("connection", {}).get("glucoseMeasurement", {})
            if latest_meas:
                try:
                    val = float(latest_meas.get("Value"))
                    ts_str = latest_meas.get("FactoryTimestamp") or latest_meas.get("Timestamp")
                    utc_dt = parse_iso_timestamp(ts_str)

                    all_readings.append({
                        "timestamp": utc_dt,
                        "value": val,
                        "type": "live",
                        "device": device_model,
                        "serial_number": serial_number,
                        "record_type": 1  # equivalent to scan
                    })
                except Exception as ex:
                    print(f"Skipping latest measurement item {latest_meas}: {ex}")

        # Deduplicate readings in memory before saving
        seen = set()
        unique_readings = []
        for r in all_readings:
            key = (r["timestamp"], r["value"])
            if key not in seen:
                seen.add(key)
                unique_readings.append(r)

        print(f"Collected {len(unique_readings)} unique readings from LibreLinkUp.")

        if dry_run:
            print("--- DRY RUN MODE (No DB Write) ---")
            print(f"Top 5 readings of {len(unique_readings)} total:")
            for r in sorted(unique_readings, key=lambda x: x["timestamp"], reverse=True)[:5]:
                print(f"Time: {r['timestamp']}, Value: {r['value']} mg/dL, Type: {r['type']}")
            return

        if not DB_AVAILABLE:
            print("Database helpers not loaded, cannot write to DB.")
            return

        # Ensure DB tables are initialized
        init_db()
        
        # Insert into DB
        inserted = insert_readings(unique_readings)
        print(f"Successfully synced: {inserted} new readings inserted (duplicates ignored).")

    except Exception as e:
        print(f"Sync error: {e}", file=sys.stderr)
        raise e

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync live LibreLinkUp readings to PostgreSQL")
    parser.add_argument("--dry-run", action="store_true", help="Print synced readings without saving to DB")
    args = parser.parse_args()

    run_sync(dry_run=args.dry_run)
