import os
import time
import sys
from sync import run_sync

# How often to check for updates (in seconds)
# Default is 5 minutes (300 seconds) since Abbott limits rate of updates and sensor uploads.
CHECK_INTERVAL_SECONDS = int(os.getenv("SYNC_INTERVAL_SECONDS", 300))

def main():
    print("=============================================")
    print("Gluco Track Live Sync Daemon Starting...")
    print(f"Polling Interval: {CHECK_INTERVAL_SECONDS} seconds")
    print("=============================================")

    # Run initial sync on startup
    try:
        run_sync()
    except Exception as e:
        print(f"Initial sync failed on startup: {e}. Worker will continue polling...")

    while True:
        try:
            print(f"Waiting for next sync cycle (sleeping for {CHECK_INTERVAL_SECONDS}s)...")
            time.sleep(CHECK_INTERVAL_SECONDS)
            print(f"Sync cycle started at {datetime.now(pytz.utc).isoformat()}")
            run_sync()
        except KeyboardInterrupt:
            print("Sync daemon stopped by user.")
            sys.exit(0)
        except Exception as e:
            print(f"Error during sync cycle: {e}. Retrying in next interval...", file=sys.stderr)

if __name__ == "__main__":
    # Import datetime/pytz here to avoid polluting namespace or startup delays
    from datetime import datetime
    import pytz
    main()
