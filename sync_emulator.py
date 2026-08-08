import subprocess
import time
import sys
import os

ADB_PATH = r"C:\Users\tugha\AppData\Local\Android\Sdk\platform-tools\adb.exe"
APP_PACKAGE = "com.abbott.adc.freestyle.libre.us"
APP_ACTIVITY = "com.freestylelibre.app.ui.MainActivity" # Common activity name, can be refined

# --- UI Coordinates (Adjust based on specific device resolution) ---
# To find exact coordinates, run: adb shell getevent -l 
# or use `android layout` to inspect the UI tree.
BTN_ADD_NOTE = (500, 2000)      # e.g., floating action button to log
BTN_INSULIN_TOGGLE = (200, 1000) # e.g., toggle switch for Rapid-Acting Insulin
BTN_CARBS_TOGGLE = (200, 1200)   # e.g., toggle switch for Carbs
INPUT_INSULIN = (800, 1000)      # e.g., text box for insulin units
INPUT_CARBS = (800, 1200)        # e.g., text box for carbs
BTN_SAVE = (900, 200)            # e.g., save or checkmark button top right

def run_adb(cmd):
    """Helper to run adb commands"""
    full_cmd = [ADB_PATH] + cmd
    print(f"Running: {' '.join(full_cmd)}")
    result = subprocess.run(full_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ADB Error: {result.stderr}")
    return result.stdout.strip()

def tap(x, y):
    """Simulate a tap on the screen at (x, y)"""
    run_adb(["shell", "input", "tap", str(x), str(y)])
    time.sleep(1)

def input_text(text):
    """Input text via keyboard"""
    run_adb(["shell", "input", "text", str(text)])
    time.sleep(1)

def open_app():
    """Launch the FreeStyle Libre app"""
    # Start the app using adb monkey (safest way to just bring it to front)
    run_adb(["shell", "monkey", "-p", APP_PACKAGE, "-c", "android.intent.category.LAUNCHER", "1"])
    time.sleep(4) # Wait for app to load

def log_data(insulin_units=None, carbs_g=None):
    """Automate the UI to log data"""
    open_app()
    
    # 1. Tap the "Add Log/Note" button on the home screen
    print("Tapping Add Note...")
    tap(*BTN_ADD_NOTE)
    time.sleep(1.5)
    
    # 2. If insulin is provided, log it
    if insulin_units is not None:
        print("Tapping Insulin toggle...")
        tap(*BTN_INSULIN_TOGGLE)
        
        print(f"Entering insulin units: {insulin_units}...")
        tap(*INPUT_INSULIN)
        input_text(str(insulin_units))
        
    # 3. If carbs are provided, log it
    if carbs_g is not None:
        print("Tapping Carbs toggle...")
        tap(*BTN_CARBS_TOGGLE)
        
        print(f"Entering carbs: {carbs_g}...")
        tap(*INPUT_CARBS)
        input_text(str(carbs_g))
        
    # 4. Save the log
    print("Tapping Save...")
    tap(*BTN_SAVE)
    time.sleep(2)
    
    print("Data logged successfully via emulator!")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Automate logging to Libre App via Emulator")
    parser.add_argument("--insulin", type=float, help="Units of rapid-acting insulin to log")
    parser.add_argument("--carbs", type=float, help="Grams of carbohydrates to log")
    
    args = parser.parse_args()
    if args.insulin is None and args.carbs is None:
        print("Please provide --insulin or --carbs to log.")
        sys.exit(1)
        
    # Verify adb connection
    devices = run_adb(["devices"])
    if "device" not in devices.split("\n")[1]: # quick check for connected device
        print("Error: No emulator or device connected.")
        sys.exit(1)
        
    log_data(insulin_units=args.insulin, carbs_g=args.carbs)
