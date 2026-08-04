import os
import time
import sys
import threading
import uvicorn
from sync import run_sync

# How often to check for updates (in seconds)
CHECK_INTERVAL_SECONDS = int(os.getenv("SYNC_INTERVAL_SECONDS", 300))

def sync_worker_loop():
    print("=============================================")
    print("Gluco Track Live Sync Daemon Started (Background)...")
    print(f"Polling Interval: {CHECK_INTERVAL_SECONDS} seconds")
    print("=============================================")

    # Run initial sync on startup
    try:
        run_sync()
    except Exception as e:
        print(f"Initial sync failed on startup: {e}. Worker will continue polling...")

    while True:
        try:
            time.sleep(CHECK_INTERVAL_SECONDS)
            print(f"Background Sync cycle started...")
            run_sync()
        except Exception as e:
            print(f"Error during sync cycle: {e}. Retrying in next interval...", file=sys.stderr)

def main():
    # Start the sync polling daemon as a background thread
    sync_thread = threading.Thread(target=sync_worker_loop, daemon=True)
    sync_thread.start()

    # Launch FastAPI application using Uvicorn
    # Get port and host from environment variables (Railway default is PORT)
    port = int(os.getenv("PORT", 8080))
    host = os.getenv("HOST", "0.0.0.0")

    print(f"Starting Web API and Dashboard on http://{host}:{port}...")
    # Import the app here to avoid circular imports during worker startup
    from app import app
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    main()
