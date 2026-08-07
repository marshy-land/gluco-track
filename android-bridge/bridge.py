import os
import time
import psycopg2
import psycopg2.extras
import uiautomator2 as u2
from datetime import datetime, timezone

DATABASE_URL = os.getenv("DATABASE_URL")
REDROID_HOST = os.getenv("REDROID_HOST", "redroid:5555")

def get_unsynced_events():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, timestamp, rapid_acting as amount, 'insulin' as event_type 
                FROM insulin_doses 
                WHERE is_imputed = FALSE AND synced_to_libreview = FALSE AND (rapid_acting > 0 OR correction > 0)
                UNION ALL
                SELECT id, timestamp, carbs_g as amount, 'food' as event_type
                FROM food_logs
                WHERE is_imputed = FALSE AND synced_to_libreview = FALSE AND carbs_g > 0
                ORDER BY timestamp ASC
            """)
            return cur.fetchall()
    except Exception as e:
        print(f"Error fetching unsynced events: {e}")
        return []
    finally:
        conn.close()

def mark_event_synced(event_id, event_type):
    conn = psycopg2.connect(DATABASE_URL)
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

def enter_dose_in_juggluco(d, event_type, amount, timestamp):
    print(f"Automating Juggluco for {event_type}: {amount} at {timestamp}")
    # 1. Launch Juggluco
    d.app_start("tk.glucodata")
    time.sleep(2)
    
    # 2. Open Left Menu (Juggluco has a left hamburger menu or tap left edge)
    # This is a generic UIAutomator script. Actual resource IDs will vary.
    # Tap the middle-left of the screen to open the menu in Juggluco
    d.click(10, d.info['displayHeight'] // 2)
    time.sleep(1)
    
    # 3. Click 'Amounts' (or 'New Amount')
    d(textContains="Amount").click(timeout=3)
    time.sleep(1)
    
    # 4. Input Amount
    if event_type == 'insulin':
        d(resourceId="tk.glucodata:id/insulin_input").set_text(str(amount))
    elif event_type == 'food':
        d(resourceId="tk.glucodata:id/carbohydrate_input").set_text(str(amount))
        
    # 5. Set Time (Juggluco defaults to current time, if we need backdating, we must interact with the time picker)
    # For MVP, we assume Juggluco is opened roughly around the time the dose is taken.
    # Advanced implementation would use d(resourceId="tk.glucodata:id/time_button").click() and set the exact hour/minute.
    
    # 6. Click OK / Save
    d(text="OK").click()
    time.sleep(1)
    
    # 7. Go back home
    d.press("home")

def main():
    print(f"Starting Juggluco Bridge Worker connecting to {REDROID_HOST}")
    
    # Connect to ReDroid device
    # u2 requires the ADB server to be connected
    os.system(f"adb connect {REDROID_HOST}")
    time.sleep(2)
    d = u2.connect(REDROID_HOST)
    
    while True:
        try:
            events = get_unsynced_events()
            for event in events:
                event_id = event['id']
                event_type = event['event_type']
                amount = event['amount']
                timestamp = event['timestamp']
                
                try:
                    enter_dose_in_juggluco(d, event_type, amount, timestamp)
                    mark_event_synced(event_id, event_type)
                    print(f"Successfully synced {event_type} id {event_id} to LibreView via Juggluco.")
                except Exception as ex:
                    print(f"Failed to enter dose in UI: {ex}")
                    # Reconnect ADB if crashed
                    os.system(f"adb connect {REDROID_HOST}")
                    d = u2.connect(REDROID_HOST)
                    
        except Exception as e:
            print(f"Loop error: {e}")
            
        time.sleep(60)

if __name__ == "__main__":
    main()
