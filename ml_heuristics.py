import os
import json
import math
from datetime import datetime, timezone, timedelta
from bisect import bisect_left, bisect_right
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
    """Loads saved heuristics and ML model weights from the database."""
    default_params = {
        "isf": DEFAULT_ISFS.copy(),
        "model_trained": False,
        "coefficients": None,
        "training_stats": None
    }
    
    try:
        saved = db.get_system_setting("heuristics_params", default_params)
        if saved and isinstance(saved, dict):
            for k, v in default_params.items():
                if k not in saved:
                    saved[k] = v
            return saved
    except Exception as e:
        print(f"Error loading heuristics params from DB: {e}")
        
    return default_params

def save_heuristics_params(params):
    """Saves heuristics and ML model weights to the database."""
    try:
        db.set_system_setting("heuristics_params", params)
    except Exception as e:
        print(f"Error saving heuristics params to DB: {e}")

def get_time_of_day_bucket(dt, timezone_str="America/New_York"):
    """Determines the time-of-day bucket based on local time hour."""
    try:
        if not timezone_str:
            tz = pytz.utc
        else:
            tz = pytz.timezone(timezone_str)
    except (pytz.exceptions.UnknownTimeZoneError, KeyError, ValueError, AttributeError, Exception):
        tz = pytz.utc

    # Ensure dt is timezone aware (assume UTC if naive)
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    try:
        local_dt = dt.astimezone(tz)
    except Exception:
        local_dt = dt.astimezone(pytz.utc)

    hour = local_dt.hour
    
    if 4 <= hour < 11:
        return "morning"
    elif 11 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 22:
        return "evening"
    else:
        return "night"

def _safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        v = float(val)
        return default if (math.isnan(v) or math.isinf(v)) else v
    except (ValueError, TypeError):
        return default

def calculate_personalized_isf(hours_back=720, timezone_str="America/New_York"):
    """
    Analyzes historical data to compute personalized time-of-day ISFs.
    Finds 'pure correction events':
    - Dose taken (rapid, meal, correction, or change > 0)
    - No other doses taken within 4 hours
    - Computes delta glucose after 4 hours
    """
    # Fetch all insulin doses from the last N days
    try:
        doses = db.get_insulin_history(hours_back)
    except Exception:
        doses = []
    # Fetch all glucose readings from the last N days
    try:
        readings = db.get_history(hours_back + 8) # fetch extra history for endpoints
    except Exception:
        readings = []
    
    if not doses or not readings:
        return DEFAULT_ISFS.copy()

    parsed_readings = []
    for r in readings:
        if isinstance(r, dict) and 'timestamp' in r and r.get('value') is not None:
            dt = parse_dt(r['timestamp'])
            val = _safe_float(r['value'], None)
            if dt is not None and val is not None:
                parsed_readings.append({'timestamp': dt, 'value': val})

    if not parsed_readings:
        return DEFAULT_ISFS.copy()

    # Sort readings by timestamp for efficient lookup
    readings = sorted(parsed_readings, key=lambda r: r['timestamp'])
    
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

    parsed_doses = []
    for d in doses:
        if isinstance(d, dict) and 'timestamp' in d:
            dt = parse_dt(d['timestamp'])
            if dt is not None:
                parsed_doses.append({
                    'timestamp': dt,
                    'rapid_acting': _safe_float(d.get("rapid_acting")),
                    'meal': _safe_float(d.get("meal")),
                    'correction': _safe_float(d.get("correction")),
                    'user_change': _safe_float(d.get("user_change"))
                })

    doses = sorted(parsed_doses, key=lambda d: d['timestamp'])

    for i, dose in enumerate(doses):
        # Extract total rapid acting dose safely
        rapid = dose["rapid_acting"]
        meal = dose["meal"]
        correction = dose["correction"]
        raw_change = dose["user_change"]
        user_change = raw_change if not (rapid or meal or correction) else 0.0
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
            val_start = _safe_float(g_start['value'], None)
            val_end = _safe_float(g_end['value'], None)
            
            # We want to see a drop, if blood sugar actually rose it might indicate user ate carbs
            if val_start is not None and val_end is not None and val_start > val_end:
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
    try:
        readings = db.get_history(history_days * 24)
    except Exception:
        readings = []
    try:
        doses = db.get_insulin_history(history_days * 24 + 4)
    except Exception:
        doses = []
    
    parsed_readings = []
    for r in readings:
        if isinstance(r, dict) and 'timestamp' in r and r.get('value') is not None:
            dt = parse_dt(r['timestamp'])
            val = _safe_float(r['value'], None)
            if dt is not None and val is not None:
                parsed_readings.append({'timestamp': dt, 'value': val})

    if len(parsed_readings) < 15:
        return False, "Insufficient glucose readings to train model."

    readings = sorted(parsed_readings, key=lambda r: r['timestamp'])
    
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
        iob_float = _safe_float(iob_val, 0.0)
        
        # Circadian features
        try:
            tz = pytz.timezone(timezone_str)
        except Exception:
            tz = pytz.utc
        local_t = r['timestamp'].astimezone(tz)
        local_hour = local_t.hour + (local_t.minute / 60.0)
        sin_h = math.sin(2.0 * math.pi * local_hour / 24.0)
        cos_h = math.cos(2.0 * math.pi * local_hour / 24.0)
        
        # Feature row: [Intercept, G(t), G(t-15), G(t-30), G(t-60), sin_hour, cos_hour, IOB]
        X.append([1.0, float(val_t), float(val_15), float(val_30), float(val_60), sin_h, cos_h, iob_float])
        Y.append([float(target)])

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
    if not readings or len(readings) < 5:
        return None

    parsed_readings = []
    for r in readings:
        if isinstance(r, dict) and 'timestamp' in r and r.get('value') is not None:
            dt = parse_dt(r['timestamp'])
            val = _safe_float(r['value'], None)
            if dt is not None and val is not None:
                parsed_readings.append({'timestamp': dt, 'value': val})

    if len(parsed_readings) < 5:
        return None

    # Sort readings
    sorted_readings = sorted(parsed_readings, key=lambda r: r['timestamp'])
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
        return best_val if best_val is not None else val_t # fallback to current value if missing

    val_15 = find_val_offset(15)
    val_30 = find_val_offset(30)
    val_60 = find_val_offset(60)

    # Circadian features
    try:
        tz = pytz.timezone(timezone_str)
    except Exception:
        tz = pytz.utc
    local_t = latest['timestamp'].astimezone(tz)
    local_hour = local_t.hour + (local_t.minute / 60.0)
    
    sin_h = math.sin(2.0 * math.pi * local_hour / 24.0)
    cos_h = math.cos(2.0 * math.pi * local_hour / 24.0)
    
    iob_float = _safe_float(iob_val, 0.0)
    val_t_float = _safe_float(val_t, 0.0)
    val_15_float = _safe_float(val_15, 0.0)
    val_30_float = _safe_float(val_30, 0.0)
    val_60_float = _safe_float(val_60, 0.0)

    # Feature vector: [1, G(t), G(t-15), G(t-30), G(t-60), sin, cos, IOB]
    features = [1.0, val_t_float, val_15_float, val_30_float, val_60_float, sin_h, cos_h, iob_float]
    pred_30 = sum(features[i] * coef[i] for i in range(len(features)))
    
    # Sanity clamp
    pred_30 = max(40.0, min(400.0, pred_30))
    
    # Extrapolate 15m (halfway between current and 30m) and 60m (double trend)
    pred_15 = val_t_float + (pred_30 - val_t_float) * 0.5
    pred_15 = max(40.0, min(400.0, pred_15))
    
    pred_60 = val_t_float + (pred_30 - val_t_float) * 1.7 # dampened double trend
    pred_60 = max(40.0, min(400.0, pred_60))
    
    # Calculate trend rate (mg/dL/min)
    trend_rate = (pred_30 - val_t_float) / 30.0
    
    return [
        {"minutes": 15, "value": round(pred_15, 1), "trend_rate": round(trend_rate, 2)},
        {"minutes": 30, "value": round(pred_30, 1), "trend_rate": round(trend_rate, 2)},
        {"minutes": 60, "value": round(pred_60, 1), "trend_rate": round(trend_rate, 2)}
    ]

FALLBACK_NUTRITIONAL_BUCKETS = {
    "Morning": {"peak_rise_mgdl": 45.2, "peak_latency_min": 55, "modifier": 1.25},
    "Afternoon": {"peak_rise_mgdl": 35.0, "peak_latency_min": 45, "modifier": 1.00},
    "Evening": {"peak_rise_mgdl": 40.1, "peak_latency_min": 50, "modifier": 1.10},
    "Night": {"peak_rise_mgdl": 52.8, "peak_latency_min": 75, "modifier": 1.40}
}

def parse_dt(val):
    if isinstance(val, str):
        try:
            val = datetime.fromisoformat(val.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return pytz.utc.localize(val)
        return val
    return None

def calculate_nutritional_impact_modifiers(readings=None, doses=None, hours_back=720, timezone_str="America/New_York"):
    """
    Computes time-of-day blood sugar impact modifiers (M_tod) across 4 circadian buckets:
    Morning (04:00 - 11:00), Afternoon (11:00 - 17:00), Evening (17:00 - 22:00), Night (22:00 - 04:00).
    Provides clinical reference fallbacks when historical data in a bucket is sparse (N_b < 3).
    Generates dynamic personalized clinical recommendations.
    """
    if readings is None:
        try:
            readings = db.get_history(hours_back)
        except Exception:
            readings = []

    if doses is None:
        try:
            doses = db.get_insulin_history(hours_back)
        except Exception:
            doses = []

    # Parse and normalize timestamps
    parsed_readings = []
    for r in readings:
        if isinstance(r, dict) and 'timestamp' in r and r.get('value') is not None:
            dt = parse_dt(r['timestamp'])
            if dt:
                try:
                    f_val = float(r['value'])
                    if not (math.isnan(f_val) or math.isinf(f_val)):
                        parsed_readings.append({'timestamp': dt, 'value': f_val})
                except (TypeError, ValueError):
                    pass
    parsed_readings.sort(key=lambda r: r['timestamp'])
    reading_timestamps = [r['timestamp'] for r in parsed_readings]

    parsed_doses = []
    for d in doses:
        if isinstance(d, dict) and 'timestamp' in d:
            dt = parse_dt(d['timestamp'])
            if dt:
                try:
                    meal = float(d.get('meal') or 0.0)
                except (ValueError, TypeError):
                    meal = 0.0
                try:
                    rapid = float(d.get('rapid_acting') or 0.0)
                except (ValueError, TypeError):
                    rapid = 0.0
                if math.isnan(meal) or math.isinf(meal):
                    meal = 0.0
                if math.isnan(rapid) or math.isinf(rapid):
                    rapid = 0.0
                if meal > 0 or rapid > 0:
                    parsed_doses.append({'timestamp': dt, 'meal': meal, 'rapid': rapid})
    parsed_doses.sort(key=lambda d: d['timestamp'])

    bucket_excursions = {
        "Morning": [],
        "Afternoon": [],
        "Evening": [],
        "Night": []
    }

    # Strategy 1: Meal Dose Anchored Excursions
    if parsed_doses and parsed_readings:
        for dose in parsed_doses:
            t_meal = dose['timestamp']
            
            # Find baseline reading within [-15m, +15m] of meal dose
            b_start = bisect_left(reading_timestamps, t_meal - timedelta(seconds=900))
            b_end = bisect_right(reading_timestamps, t_meal + timedelta(seconds=900))
            baseline_candidates = parsed_readings[b_start:b_end]

            if not baseline_candidates:
                continue
            g_base = min(baseline_candidates, key=lambda r: abs((r['timestamp'] - t_meal).total_seconds()))['value']

            # Postprandial window: [t_meal, t_meal + 180m]
            w_start = bisect_left(reading_timestamps, t_meal)
            w_end = bisect_right(reading_timestamps, t_meal + timedelta(seconds=10800))
            window_readings = parsed_readings[w_start:w_end]

            if len(window_readings) >= 2:
                g_max_r = max(window_readings, key=lambda r: r['value'])
                g_peak = g_max_r['value']
                peak_rise = g_peak - g_base
                if peak_rise > 0:
                    latency_min = int(round((g_max_r['timestamp'] - t_meal).total_seconds() / 60.0))
                    bucket_lower = get_time_of_day_bucket(t_meal, timezone_str)
                    bucket = bucket_lower.capitalize()
                    if bucket in bucket_excursions:
                        bucket_excursions[bucket].append({
                            'peak_rise': peak_rise,
                            'latency': latency_min
                        })

    # Strategy 2: Continuous Glucose Spike Detection (if dose data is sparse)
    total_excursions = sum(len(v) for v in bucket_excursions.values())
    if total_excursions < 5 and len(parsed_readings) >= 10:
        last_event_time = None
        for i in range(len(parsed_readings) - 2):
            r0 = parsed_readings[i]
            t0 = r0['timestamp']
            
            if last_event_time and (t0 - last_event_time).total_seconds() < 7200: # 2 hours separation
                continue

            # Look ahead up to 30 mins for a rise >= 15 mg/dL
            n_start = bisect_right(reading_timestamps, t0)
            n_end = bisect_right(reading_timestamps, t0 + timedelta(seconds=1800))
            near_readings = parsed_readings[n_start:n_end]
            if not near_readings:
                continue
            
            max_near = max(near_readings, key=lambda r: r['value'])
            if max_near['value'] - r0['value'] >= 15.0:
                # Spike detected at t0! Track excursion for 180m
                win_end = bisect_right(reading_timestamps, t0 + timedelta(seconds=10800))
                window_readings = parsed_readings[i:win_end]
                if len(window_readings) >= 3:
                    g_base = r0['value']
                    g_max_r = max(window_readings, key=lambda r: r['value'])
                    peak_rise = g_max_r['value'] - g_base
                    if peak_rise > 0:
                        latency_min = int(round((g_max_r['timestamp'] - t0).total_seconds() / 60.0))
                        bucket = get_time_of_day_bucket(t0, timezone_str).capitalize()
                        if bucket in bucket_excursions:
                            bucket_excursions[bucket].append({
                                'peak_rise': peak_rise,
                                'latency': latency_min
                            })
                            last_event_time = t0

    # Process metrics per bucket
    time_buckets = {}
    
    # First pass: calculate empirical peak rise & latency for buckets with N >= 3
    empirical_rises = []
    for bucket in ["Morning", "Afternoon", "Evening", "Night"]:
        excs = bucket_excursions[bucket]
        if len(excs) >= 3:
            avg_rise = sum(e['peak_rise'] for e in excs) / len(excs)
            avg_lat = sum(e['latency'] for e in excs) / len(excs)
            time_buckets[bucket] = {
                "peak_rise_mgdl": round(avg_rise, 1),
                "peak_latency_min": int(round(avg_lat)),
                "N": len(excs)
            }
            empirical_rises.append(avg_rise)
        else:
            fb = FALLBACK_NUTRITIONAL_BUCKETS[bucket]
            time_buckets[bucket] = {
                "peak_rise_mgdl": fb["peak_rise_mgdl"],
                "peak_latency_min": fb["peak_latency_min"],
                "modifier": fb["modifier"],
                "N": len(excs)
            }

    # Determine baseline for modifier computation
    if time_buckets["Afternoon"]["N"] >= 3:
        baseline_rise = time_buckets["Afternoon"]["peak_rise_mgdl"]
    elif empirical_rises:
        baseline_rise = sum(empirical_rises) / len(empirical_rises)
    else:
        baseline_rise = 35.0

    # Second pass: calculate modifiers for non-sparse buckets
    for bucket in ["Morning", "Afternoon", "Evening", "Night"]:
        if time_buckets[bucket]["N"] >= 3:
            raw_mod = time_buckets[bucket]["peak_rise_mgdl"] / baseline_rise if baseline_rise > 0 else 1.0
            clamped_mod = max(0.50, min(2.50, round(raw_mod, 2)))
            time_buckets[bucket]["modifier"] = clamped_mod

    # Clean up response schema
    final_time_buckets = {}
    for bucket in ["Morning", "Afternoon", "Evening", "Night"]:
        final_time_buckets[bucket] = {
            "peak_rise_mgdl": time_buckets[bucket]["peak_rise_mgdl"],
            "peak_latency_min": time_buckets[bucket]["peak_latency_min"],
            "modifier": time_buckets[bucket]["modifier"]
        }

    # Generate dynamic recommendations
    recommendations = []
    
    night_mod = final_time_buckets["Night"]["modifier"]
    night_rise = final_time_buckets["Night"]["peak_rise_mgdl"]
    night_lat = final_time_buckets["Night"]["peak_latency_min"]
    if night_mod > 1.25 or night_rise > 48.0:
        recommendations.append(
            f"High nocturnal carb impact detected: Night meals cause +{night_rise:.1f} mg/dL spike with {night_lat} min peak latency. Avoid late-night carbohydrates after 22:00."
        )
    else:
        recommendations.append(
            f"Nocturnal glycemic response is stable ({night_mod:.2f}x modifier). Maintain current evening/night routines."
        )

    morn_mod = final_time_buckets["Morning"]["modifier"]
    if morn_mod > 1.15:
        pct = round((morn_mod - 1.0) * 100)
        recommendations.append(
            f"Dawn effect observed: Morning glucose rise is {pct}% higher than afternoon baseline. Consider increasing morning pre-bolus lead time or choosing lower glycemic index foods."
        )
    else:
        recommendations.append(
            f"Morning insulin sensitivity is within normal range ({morn_mod:.2f}x modifier)."
        )

    eve_mod = final_time_buckets["Evening"]["modifier"]
    if eve_mod > 1.15:
        pct = round((eve_mod - 1.0) * 100)
        recommendations.append(
            f"Elevated evening carb sensitivity: Evening glucose rise is {pct}% higher than baseline. Moderating evening carbohydrate portions is recommended."
        )

    aft_mod = final_time_buckets["Afternoon"]["modifier"]
    recommendations.append(
        f"Afternoon sensitivity is optimal ({aft_mod:.2f}x baseline multiplier). Best window for complex carbohydrate intake."
    )

    return {
        "time_buckets": final_time_buckets,
        "recommendations": recommendations
    }

def get_nutritional_impact(db_session=None, hours_back=720, timezone_str="America/New_York"):
    """Alias function wrapping calculate_nutritional_impact_modifiers."""
    return calculate_nutritional_impact_modifiers(readings=None, doses=None, hours_back=hours_back, timezone_str=timezone_str)



def train_imputation_calibration(readings, doses):
    """
    Compares the imputation algorithm's guesses against actual ground-truth logged doses
    to compute an empirical calibration multiplier.
    """
    from imputation import detect_and_impute_missing_doses
    
    # Filter to only include actual (non-imputed) doses that are > 0
    actual_doses = [d for d in doses if (d.get('rapid_acting') or 0) > 0 and not d.get('is_imputed')]
    
    if not actual_doses:
        return 1.0 # No ground truth to calibrate against
        
    # Create dummy doses with 0 rapid_acting so the imputation algorithm thinks they are missing
    # but still preserves meal info so it doesn't overly penalize confidence
    dummy_doses = []
    for d in doses:
        d_copy = dict(d)
        d_copy['rapid_acting'] = 0.0
        d_copy['correction'] = 0.0
        d_copy['meal'] = 0.0
        dummy_doses.append(d_copy)
        
    # We use a very low confidence threshold (0.20) to capture all potential drops
    # regardless of whether the algorithm thinks it's a "perfect" match, because
    # we know there WAS a dose here (we have ground truth).
    candidates = detect_and_impute_missing_doses(readings, dummy_doses, min_confidence=0.20)
    
    ratios = []
    for actual in actual_doses:
        actual_t = actual['timestamp']
        actual_val = float(actual['rapid_acting'])
        
        # Find closest candidate within 90 minutes
        closest = None
        min_diff = float('inf')
        for c in candidates:
            c_t = c['timestamp']
            diff = abs((c_t - actual_t).total_seconds())
            if diff < min_diff:
                min_diff = diff
                closest = c
                
        if closest and min_diff <= 5400: # 90 mins
            # 'rapid_acting' contains the dose in the returned candidates dict
            imputed_val = float(closest['rapid_acting']) 
            if imputed_val > 0:
                ratio = actual_val / imputed_val
                ratios.append(ratio)
                
    if ratios:
        # Calculate the median or average ratio. We'll use average.
        avg_ratio = sum(ratios) / len(ratios)
        # Clamp multiplier between 0.5x and 2.5x to prevent wild swings
        return max(0.5, min(2.5, avg_ratio))
        
    return 1.0
