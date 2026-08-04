import os
import json
import math
from datetime import datetime, timezone, timedelta
import pytz
import db
from prediction import calculate_iob

PARAMS_FILE = os.path.join(os.path.dirname(__file__), "heuristics_params.json")

DEFAULT_ISFS = {
    "morning": 50.0,
    "afternoon": 50.0,
    "evening": 50.0,
    "night": 50.0,
    "global": 50.0
}

def load_heuristics_params():
    """Loads saved heuristics and ML model weights from heuristics_params.json."""
    if os.path.exists(PARAMS_FILE):
        try:
            with open(PARAMS_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading heuristics params: {e}")
    return {
        "isf": DEFAULT_ISFS.copy(),
        "model_trained": False,
        "coefficients": None,
        "training_stats": None
    }

def save_heuristics_params(params):
    """Saves heuristics and ML model weights to heuristics_params.json."""
    try:
        with open(PARAMS_FILE, "w") as f:
            json.dump(params, f, indent=2)
    except Exception as e:
        print(f"Error saving heuristics params: {e}")

def get_time_of_day_bucket(dt, timezone_str="America/New_York"):
    """Determines the time-of-day bucket based on local time hour."""
    tz = pytz.timezone(timezone_str)
    # Ensure dt is timezone aware (assume UTC if naive)
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    local_dt = dt.astimezone(tz)
    hour = local_dt.hour
    
    if 4 <= hour < 11:
        return "morning"
    elif 11 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 22:
        return "evening"
    else:
        return "night"

def calculate_personalized_isf(hours_back=720, timezone_str="America/New_York"):
    """
    Analyzes historical data to compute personalized time-of-day ISFs.
    Finds 'pure correction events':
    - Dose taken (rapid, meal, correction, or change > 0)
    - No other doses taken within 4 hours
    - Computes delta glucose after 4 hours
    """
    # Fetch all insulin doses from the last N days
    doses = db.get_insulin_history(hours_back)
    # Fetch all glucose readings from the last N days
    readings = db.get_history(hours_back + 8) # fetch extra history for endpoints
    
    if not doses or not readings:
        return DEFAULT_ISFS.copy()

    # Sort readings by timestamp for efficient lookup
    readings = sorted(readings, key=lambda r: r['timestamp'])
    
    # Fast lookup function for closest reading
    def find_nearest_reading(target_time, max_diff_mins=20):
        # Simple linear scan since list is small/moderate
        best_r = None
        best_diff = timedelta(minutes=max_diff_mins)
        for r in readings:
            diff = abs(r['timestamp'] - target_time)
            if diff < best_diff:
                best_diff = diff
                best_r = r
        return best_r

    buckets = {
        "morning": [],
        "afternoon": [],
        "evening": [],
        "night": []
    }

    # Sort doses chronologically
    doses = sorted(doses, key=lambda d: d['timestamp'])

    for i, dose in enumerate(doses):
        # Extract total rapid acting dose
        rapid = dose.get("rapid_acting") or 0.0
        meal = dose.get("meal") or 0.0
        correction = dose.get("correction") or 0.0
        user_change = dose.get("user_change") or 0.0 if not (rapid or meal or correction) else 0.0
        total_rapid = rapid + meal + correction + user_change
        
        if total_rapid <= 0.2: # skip tiny doses or 0
            continue

        # Check for insulin stacking (other doses within +/- 4 hours)
        stacking = False
        for j, other_dose in enumerate(doses):
            if i == j:
                continue
            time_diff = abs(other_dose['timestamp'] - dose['timestamp'])
            if time_diff < timedelta(hours=4):
                stacking = True
                break
        
        if stacking:
            continue

        # Find start glucose
        g_start = find_nearest_reading(dose['timestamp'], max_diff_mins=15)
        # Find end glucose 4 hours later
        g_end = find_nearest_reading(dose['timestamp'] + timedelta(hours=4), max_diff_mins=30)

        if g_start and g_end:
            val_start = g_start['value']
            val_end = g_end['value']
            
            # We want to see a drop, if blood sugar actually rose it might indicate user ate carbs
            if val_start > val_end:
                empirical_isf = (val_start - val_end) / total_rapid
                # Sanity check: ISF should be between 10 and 150 mg/dL per unit
                if 10.0 <= empirical_isf <= 150.0:
                    bucket = get_time_of_day_bucket(dose['timestamp'], timezone_str)
                    buckets[bucket].append(empirical_isf)

    # Compute averages
    results = {}
    all_empirical_isfs = []
    
    for bucket, values in buckets.items():
        if len(values) >= 3:
            avg_isf = sum(values) / len(values)
            results[bucket] = round(avg_isf, 1)
            all_empirical_isfs.extend(values)
        else:
            results[bucket] = None # fallback will handle

    # Compute global average
    if all_empirical_isfs:
        global_avg = sum(all_empirical_isfs) / len(all_empirical_isfs)
        results["global"] = round(global_avg, 1)
    else:
        results["global"] = 50.0

    # Fill fallbacks
    for bucket in ["morning", "afternoon", "evening", "night"]:
        if results[bucket] is None:
            results[bucket] = results["global"]

    return results

# Pure Python Matrix Helpers
def transpose(A):
    return [list(x) for x in zip(*A)]

def matmul(A, B):
    m, n = len(A), len(A[0])
    p = len(B[0])
    C = [[0.0] * p for _ in range(m)]
    for i in range(m):
        for j in range(p):
            C[i][j] = sum(A[i][k] * B[k][j] for k in range(n))
    return C

def invert_matrix(A):
    n = len(A)
    # Augmented matrix [A | I]
    augmented = [row + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(A)]
    
    for i in range(n):
        # Find pivot row
        pivot_row = i
        for r in range(i + 1, n):
            if abs(augmented[r][i]) > abs(augmented[pivot_row][i]):
                pivot_row = r
        if abs(augmented[pivot_row][i]) < 1e-12:
            raise ValueError("Matrix is singular / poorly conditioned.")
            
        # Swap rows
        augmented[i], augmented[pivot_row] = augmented[pivot_row], augmented[i]
        
        # Scale pivot row
        pivot = augmented[i][i]
        for col in range(i, 2 * n):
            augmented[i][col] /= pivot
            
        # Eliminate column entries in other rows
        for r in range(n):
            if r != i:
                factor = augmented[r][i]
                for col in range(i, 2 * n):
                    augmented[r][col] -= factor * augmented[i][col]
                    
    # Return right-hand side (inverse)
    return [row[n:] for row in augmented]

def train_predictive_model(history_days=30, timezone_str="America/New_York"):
    """
    Extracts chronological training features and fits a Ridge Regression model.
    Predicts G(t + 30m) based on:
    - [1, G(t), G(t-15), G(t-30), G(t-60), sin_hour, cos_hour, IOB]
    """
    # Fetch data
    readings = db.get_history(history_days * 24)
    doses = db.get_insulin_history(history_days * 24 + 4)
    
    if len(readings) < 15:
        return False, "Insufficient glucose readings to train model."

    readings = sorted(readings, key=lambda r: r['timestamp'])
    
    # Create lookup map
    ts_map = {r['timestamp'].replace(second=0, microsecond=0): r['value'] for r in readings}
    
    # Sort readings
    X = []
    Y = []

    for r in readings:
        t = r['timestamp'].replace(second=0, microsecond=0)
        val_t = r['value']
        
        # Look backwards
        val_15 = ts_map.get(t - timedelta(minutes=15))
        val_30 = ts_map.get(t - timedelta(minutes=30))
        val_60 = ts_map.get(t - timedelta(minutes=60))
        
        # Look forwards (target 30 minutes ahead)
        target = ts_map.get(t + timedelta(minutes=30))
        
        if val_15 is None or val_30 is None or val_60 is None or target is None:
            continue
            
        # Calculate active IOB at time t
        iob_val = calculate_iob(doses, current_time=r['timestamp'])
        
        # Circadian features
        tz = pytz.timezone(timezone_str)
        local_t = r['timestamp'].astimezone(tz)
        local_hour = local_t.hour + (local_t.minute / 60.0)
        sin_h = math.sin(2.0 * math.pi * local_hour / 24.0)
        cos_h = math.cos(2.0 * math.pi * local_hour / 24.0)
        
        # Feature row: [Intercept, G(t), G(t-15), G(t-30), G(t-60), sin_hour, cos_hour, IOB]
        X.append([1.0, val_t, val_15, val_30, val_60, sin_h, cos_h, iob_val])
        Y.append([target])

    if len(X) < 20:
        return False, f"Insufficient continuous timeseries sequences (found {len(X)} samples, need at least 20)."

    # Solve Ridge Regression: beta = (X^T * X + alpha * I)^-1 * X^T * Y
    num_features = len(X[0])
    
    # Calculate X^T * X
    Xt = transpose(X)
    XtX = matmul(Xt, X)
    
    # Add Ridge regularization parameter (L2 regularization: alpha = 1.0)
    alpha = 5.0
    for k in range(num_features):
        XtX[k][k] += alpha
        
    # Invert (X^T * X + alpha * I)
    try:
        XtX_inv = invert_matrix(XtX)
    except Exception as e:
        return False, f"Matrix inversion failed during Ridge Regression: {e}"
        
    # Calculate X^T * Y
    XtY = matmul(Xt, Y)
    
    # Calculate beta = XtX_inv * XtY
    beta = matmul(XtX_inv, XtY)
    
    # Extract coefficients list
    coefficients = [b[0] for b in beta]

    # Save to params file
    params = load_heuristics_params()
    params["model_trained"] = True
    params["coefficients"] = coefficients
    params["training_stats"] = {
        "num_samples": len(X),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "history_days": history_days
    }
    
    # Recalculate ISF as part of training cycle
    params["isf"] = calculate_personalized_isf(history_days * 24, timezone_str)
    
    save_heuristics_params(params)
    
    return True, f"Successfully trained model on {len(X)} samples. Custom ISFs: Morning={params['isf']['morning']} U, Afternoon={params['isf']['afternoon']} U."

def predict_adaptive_glucose(readings, iob_val, timezone_str="America/New_York"):
    """
    Calculates forecasted glucose values for 15, 30, and 60 minutes
    using the trained Ridge Regression coefficients if available.
    Returns None if no model is trained.
    """
    params = load_heuristics_params()
    if not params.get("model_trained") or not params.get("coefficients"):
        return None

    coef = params["coefficients"]
    if len(readings) < 5:
        return None

    # Sort readings
    sorted_readings = sorted(readings, key=lambda r: r['timestamp'])
    latest = sorted_readings[-1]
    val_t = latest['value']
    
    # Find historical offsets
    def find_val_offset(mins_back, max_diff_mins=10):
        target_t = latest['timestamp'] - timedelta(minutes=mins_back)
        best_val = None
        best_diff = timedelta(minutes=max_diff_mins)
        for r in sorted_readings:
            diff = abs(r['timestamp'] - target_t)
            if diff < best_diff:
                best_diff = diff
                best_val = r['value']
        return best_val or val_t # fallback to current value if missing

    val_15 = find_val_offset(15)
    val_30 = find_val_offset(30)
    val_60 = find_val_offset(60)

    # Circadian features
    tz = pytz.timezone(timezone_str)
    local_t = latest['timestamp'].astimezone(tz)
    local_hour = local_t.hour + (local_t.minute / 60.0)
    
    # Function to forecast N minutes ahead using beta coefficients.
    # The trained model predicts +30m. We can extrapolate +15m, +30m, and +60m:
    # Let's calculate the predicted +30m target value first
    sin_h = math.sin(2.0 * math.pi * local_hour / 24.0)
    cos_h = math.cos(2.0 * math.pi * local_hour / 24.0)
    
    # Feature vector: [1, G(t), G(t-15), G(t-30), G(t-60), sin, cos, IOB]
    features = [1.0, val_t, val_15, val_30, val_60, sin_h, cos_h, iob_val]
    pred_30 = sum(features[i] * coef[i] for i in range(len(features)))
    
    # Sanity clamp
    pred_30 = max(40.0, min(400.0, pred_30))
    
    # Extrapolate 15m (halfway between current and 30m) and 60m (double trend)
    pred_15 = val_t + (pred_30 - val_t) * 0.5
    pred_15 = max(40.0, min(400.0, pred_15))
    
    pred_60 = val_t + (pred_30 - val_t) * 1.7 # dampened double trend
    pred_60 = max(40.0, min(400.0, pred_60))
    
    # Calculate trend rate (mg/dL/min)
    trend_rate = (pred_30 - val_t) / 30.0
    
    return [
        {"minutes": 15, "value": round(pred_15, 1), "trend_rate": round(trend_rate, 2)},
        {"minutes": 30, "value": round(pred_30, 1), "trend_rate": round(trend_rate, 2)},
        {"minutes": 60, "value": round(pred_60, 1), "trend_rate": round(trend_rate, 2)}
    ]
