import sys
import argparse
from parser import parse_libreview_csv
from db import insert_readings, init_db

def main():
    parser = argparse.ArgumentParser(description="Import LibreView CSV data into PostgreSQL")
    parser.add_argument("csv_path", help="Path to the downloaded LibreView CSV file")
    parser.add_argument("--tz", default="America/Los_Angeles", help="Timezone of the CSV device timestamps (default: America/Los_Angeles)")
    args = parser.parse_args()

    print("Initializing database...")
    init_db()

    print(f"Parsing CSV file: {args.csv_path} with timezone {args.tz}...")
    try:
        readings = parse_libreview_csv(args.csv_path, timezone_str=args.tz)
    except Exception as e:
        print(f"Error parsing CSV: {e}")
        sys.exit(1)

    if not readings:
        print("No readings parsed from CSV.")
        sys.exit(0)

    print(f"Inserting {len(readings)} readings into the database...")
    try:
        inserted = insert_readings(readings)
        print(f"Success! Inserted {inserted} new readings (skipped duplicates).")
    except Exception as e:
        print(f"Error inserting readings: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
