import csv
import os
import re
from datetime import datetime
import pytz

# Default timezone for local timestamps in LibreView CSV
DEFAULT_TIMEZONE = os.getenv("LIBRE_TIMEZONE", "America/Los_Angeles")

def parse_libreview_csv(file_path, timezone_str=DEFAULT_TIMEZONE):
    """
    Parses a LibreView glucose history CSV file.
    Identifies the header row dynamically to handle varying metadata headers.
    
    Returns a list of dictionaries prepared for DB insertion.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    tz = pytz.timezone(timezone_str)
    readings = []
    
    with open(file_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        
        header = None
        header_index = 0
        
        # Read lines to find the header row dynamically
        for i, row in enumerate(reader):
            # The header row typically contains 'Device' and 'Device Timestamp'
            if any("Timestamp" in col for col in row) and any("Device" in col for col in row):
                header = [col.strip() for col in row]
                header_index = i
                break
                
        if not header:
            raise ValueError("Could not find the header row in the CSV file.")

        print(f"Found header at row {header_index}: {header}")

        # Map header columns to indexes
        col_map = {col: idx for idx, col in enumerate(header)}
        
        required_cols = ["Device Timestamp", "Record Type"]
        for col in required_cols:
            if not any(k for k in col_map.keys() if col in k):
                raise ValueError(f"Missing required column containing: '{col}'")

        # Find the exact column names
        timestamp_col = next(k for k in col_map.keys() if "Timestamp" in k)
        record_type_col = next(k for k in col_map.keys() if "Record Type" in k)
        device_col = next((k for k in col_map.keys() if "Device" == k), None)
        serial_col = next((k for k in col_map.keys() if "Serial" in k), None)
        
        # Glucose columns
        historic_col = next((k for k in col_map.keys() if "Historic Glucose" in k), None)
        scan_col = next((k for k in col_map.keys() if "Scan Glucose" in k), None)

        # Process data rows
        for row_num, row in enumerate(reader, start=header_index + 2):
            if not row or len(row) < len(col_map):
                continue
                
            try:
                raw_time = row[col_map[timestamp_col]].strip()
                if not raw_time:
                    continue
                
                # Parse timestamp (LibreView formats can include 24h or 12h AM/PM)
                dt = None
                formats = (
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", 
                    "%m-%d-%Y %H:%M:%S", "%m-%d-%Y %H:%M", 
                    "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
                    "%m-%d-%Y %I:%M %p", "%m-%d-%Y %I:%M:%S %p",
                    "%Y-%m-%d %I:%M %p", "%Y-%m-%d %I:%M:%S %p",
                    "%d-%m-%Y %I:%M %p", "%d-%m-%Y %I:%M:%S %p"
                )
                for fmt in formats:
                    try:
                        dt = datetime.strptime(raw_time, fmt)
                        break
                    except ValueError:
                        continue
                
                if not dt:
                    # Skip rows with unparseable timestamps
                    continue

                # Localize naive datetime to user's timezone, then convert to UTC
                localized_dt = tz.localize(dt)
                utc_dt = localized_dt.astimezone(pytz.utc)

                raw_record_type = row[col_map[record_type_col]].strip()
                if not raw_record_type:
                    continue
                record_type = int(raw_record_type)

                # Determine value and type
                value = None
                reading_type = None

                # Record Type: 0 = Historic (Continuous), 1 = Scan (Manual)
                if record_type == 0 and historic_col:
                    raw_val = row[col_map[historic_col]].strip()
                    if raw_val:
                        value = float(raw_val)
                        reading_type = "historic"
                elif record_type == 1 and scan_col:
                    raw_val = row[col_map[scan_col]].strip()
                    if raw_val:
                        value = float(raw_val)
                        reading_type = "scan"
                
                # If there's no value (e.g. food/insulin logs row), skip it
                if value is None:
                    continue

                device = row[col_map[device_col]].strip() if device_col else None
                serial = row[col_map[serial_col]].strip() if serial_col else None

                readings.append({
                    "timestamp": utc_dt,
                    "value": value,
                    "type": reading_type,
                    "device": device,
                    "serial_number": serial,
                    "record_type": record_type
                })

            except Exception as e:
                # Log parsing errors but keep processing other rows
                print(f"Skipping row {row_num} due to parsing error: {e}")
                continue

    print(f"Successfully parsed {len(readings)} readings from {file_path}")
    return readings

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python parser.py <path_to_csv>")
        sys.exit(1)
        
    csv_file = sys.argv[1]
    try:
        data = parse_libreview_csv(csv_file)
        if data:
            print("First 3 readings:")
            for r in data[:3]:
                print(r)
    except Exception as e:
        print(f"Error: {e}")
