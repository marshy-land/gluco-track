import math
from datetime import datetime, timezone
import pytz
import db

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

def predict_glucose(readings, minutes_ahead=[15, 30, 60, 90, 120], dampening_half_life=25):
    """
    Predicts glucose values for future time offsets using a dampened linear trend or adaptive ML model.
    `readings` is a list of dictionaries with 'timestamp' and 'value'.
    `dampening_half_life` is the time (in minutes) for the trend slope to decay by half.
    """
    if not readings:
        return []

    parsed_readings = []
    for r in readings:
        if isinstance(r, dict) and 'timestamp' in r and r.get('value') is not None:
            dt = parse_dt(r['timestamp'])
            if dt is not None:
                try:
                    val = float(r['value'])
                    if not (math.isnan(val) or math.isinf(val)):
                        parsed_readings.append({'timestamp': dt, 'value': val})
                except (ValueError, TypeError):
                    pass

    if len(parsed_readings) < 2:
        return []

    # Sort readings chronologically
    sorted_readings = sorted(parsed_readings, key=lambda x: x['timestamp'])

    # Try adaptive predictive model first
    try:
        from ml_heuristics import predict_adaptive_glucose
        latest_time = sorted_readings[-1]['timestamp']
        try:
            doses = db.get_insulin_history(4)
        except Exception:
            doses = []
        iob_val = calculate_iob(doses, current_time=latest_time)
        
        adaptive_preds = predict_adaptive_glucose(sorted_readings, iob_val)
        if adaptive_preds:
            return adaptive_preds
    except Exception as e:
        print(f"Adaptive prediction failed, falling back to linear: {e}")
    
    # Use the last 8 readings (approx. 2 hours of passive/live readings) for the local trend
    trend_readings = sorted_readings[-8:]
    if len(trend_readings) < 2:
        return []

    # Calculate timestamps relative to the latest reading (in minutes)
    latest_reading = trend_readings[-1]
    latest_time = latest_reading['timestamp']
    latest_val = latest_reading['value']

    times_min = []
    vals = []
    for r in trend_readings:
        delta = (r['timestamp'] - latest_time).total_seconds() / 60.0
        times_min.append(delta)
        vals.append(r['value'])

    # Perform a simple linear regression to find the current velocity (slope in mg/dL/min)
    n = len(times_min)
    sum_x = sum(times_min)
    sum_y = sum(vals)
    sum_xx = sum(x * x for x in times_min)
    sum_xy = sum(times_min[i] * vals[i] for i in range(n))
    
    denom = (n * sum_xx - sum_x * sum_x)
    if abs(denom) < 1e-5:
        # Fallback to simple difference if math fails
        slope = (vals[-1] - vals[0]) / (times_min[-1] - times_min[0]) if (times_min[-1] - times_min[0]) != 0 else 0.0
    else:
        slope = (n * sum_xy - sum_x * sum_y) / denom

    # Limit slope to reasonable ranges (+5 mg/dL/min or -5 mg/dL/min)
    slope = max(-5.0, min(5.0, slope))

    predictions = []
    # Slope dampening factor: lambda = ln(2) / half_life
    decay_constant = math.log(2) / dampening_half_life

    for mins in minutes_ahead:
        dampened_delta = (1.0 - math.exp(-decay_constant * mins)) / decay_constant
        projected_val = latest_val + slope * dampened_delta
        
        # Clamp predictions to realistic physiological limits (40 to 400 mg/dL)
        projected_val = max(40.0, min(400.0, projected_val))
        
        predictions.append({
            "minutes": mins,
            "value": round(projected_val, 1),
            "trend_rate": round(slope, 2)
        })

    return predictions

def calculate_iob(doses, current_time=None, action_duration_mins=240):
    """
    Calculates active Insulin-on-Board (IOB) using Scheiner's parabolic decay model:
    IOB = Dose * (1 - t/duration)^2
    Only considers rapid-acting, meal, and correction insulin columns.
    `current_time` defaults to UTC now.
    """
    if not doses:
        return 0.0

    if current_time is None:
        current_time = datetime.now(timezone.utc)
    elif isinstance(current_time, str):
        current_time = parse_dt(current_time) or datetime.now(timezone.utc)
    elif isinstance(current_time, datetime) and current_time.tzinfo is None:
        current_time = pytz.utc.localize(current_time)

    total_iob = 0.0
    for d in doses:
        if not isinstance(d, dict) or 'timestamp' not in d:
            continue
        
        dose_time = parse_dt(d['timestamp'])
        if dose_time is None:
            continue
            
        elapsed_mins = (current_time - dose_time).total_seconds() / 60.0

        if elapsed_mins < 0:
            # Dose is logged in the future (safety fallback: count full dose)
            elapsed_mins = 0

        if elapsed_mins >= action_duration_mins:
            continue

        # Extract all rapid-acting components safely
        def _safe_float(val):
            if val is None:
                return 0.0
            try:
                v = float(val)
                return 0.0 if (math.isnan(v) or math.isinf(v)) else v
            except (ValueError, TypeError):
                return 0.0

        rapid = _safe_float(d.get("rapid_acting"))
        meal = _safe_float(d.get("meal"))
        correction = _safe_float(d.get("correction"))
        raw_user_change = d.get("user_change")
        user_change = _safe_float(raw_user_change) if not (rapid or meal or correction) else 0.0
        
        rapid_dose = rapid + meal + correction + user_change
        if rapid_dose <= 0:
            continue

        # Scheiner parabolic decay curve
        iob_fraction = (1.0 - (elapsed_mins / action_duration_mins)) ** 2
        total_iob += rapid_dose * iob_fraction

    return round(total_iob, 2)

def suggest_correction(current_glucose, iob, target_glucose=120, isf=None, current_time=None):
    """
    Suggests correction insulin units:
    Correction = (Current Glucose - Target Glucose) / ISF - IOB
    """
    try:
        current_glucose = float(current_glucose)
        target_glucose = float(target_glucose)
        iob = float(iob) if iob is not None else 0.0
    except (ValueError, TypeError):
        return 0.0

    if math.isnan(current_glucose) or math.isinf(current_glucose) or math.isnan(target_glucose) or math.isinf(target_glucose):
        return 0.0

    if current_glucose <= target_glucose:
        return 0.0

    if isf is None:
        try:
            from ml_heuristics import load_heuristics_params, get_time_of_day_bucket
            params = load_heuristics_params()
            t = current_time or datetime.now(timezone.utc)
            if isinstance(t, str):
                t = parse_dt(t) or datetime.now(timezone.utc)
            bucket = get_time_of_day_bucket(t)
            isf = params.get("isf", {}).get(bucket, 50.0)
        except Exception:
            isf = 50.0

    try:
        isf = float(isf)
        if math.isnan(isf) or math.isinf(isf) or isf <= 0:
            isf = 50.0
    except (ValueError, TypeError):
        isf = 50.0

    needed_bolus = (current_glucose - target_glucose) / isf
    suggested = needed_bolus - iob
    return round(max(0.0, suggested), 2)

def suggest_carbs(current_glucose, forecasted_glucose, iob, target_glucose=100, current_time=None):
    """
    Suggests carbohydrate intake in grams if glucose is forecasted to be low.
    """
    try:
        current_glucose = float(current_glucose)
        target_glucose = float(target_glucose)
        forecasted_glucose = float(forecasted_glucose)
        iob = float(iob) if iob is not None else 0.0
    except (ValueError, TypeError):
        return 0.0

    if forecasted_glucose >= target_glucose and current_glucose >= target_glucose:
        return 0.0

    try:
        from ml_heuristics import load_heuristics_params, get_time_of_day_bucket
        params = load_heuristics_params()
        t = current_time or datetime.now(timezone.utc)
        if isinstance(t, str):
            t = parse_dt(t) or datetime.now(timezone.utc)
        bucket = get_time_of_day_bucket(t)
        csf = params.get("csf", {}).get(bucket, 4.0)
        isf = params.get("isf", {}).get(bucket, 50.0)
    except Exception:
        csf = 4.0
        isf = 50.0

    try:
        csf = float(csf)
        if math.isnan(csf) or math.isinf(csf) or csf <= 0:
            csf = 4.0
    except (ValueError, TypeError):
        csf = 4.0

    # Calculate required rise to reach target.
    # Add (IOB * ISF) to counteract active insulin.
    required_rise = (target_glucose - forecasted_glucose) + (iob * isf)
    
    if required_rise <= 0:
        return 0.0
        
    suggested_carbs = required_rise / csf
    return round(suggested_carbs, 1)


def calculate_safe_carb_allowance(current_glucose, forecasted_60m, iob, isf=50.0, csf=4.0, upper_limit=160.0):
    """
    Calculates the safe carb intake at any given moment:
    - If trending low/in deficit: returns the exact Rescue Carbs needed.
    - If in safe target range: returns the Safe Snack Allowance without exceeding upper_limit.
    - If elevated: returns 0g safe carbs (advise low/zero carb).
    """
    try:
        cur = float(current_glucose)
        f60 = float(forecasted_60m)
        iob_val = float(iob) if iob else 0.0
        isf_val = float(isf) if isf else 50.0
        csf_val = float(csf) if csf else 4.0
    except (ValueError, TypeError):
        return {"type": "unknown", "grams": 0, "label": "No Data", "explanation": "Awaiting glucose readings."}

    # 1. Check if low or projected to go low (<95 mg/dL)
    if cur < 90.0 or f60 < 95.0:
        needed_rise = max(0.0, (105.0 - min(cur, f60)) + (iob_val * isf_val))
        grams = round(needed_rise / csf_val, 0)
        return {
            "type": "rescue",
            "grams": max(5.0, grams),
            "label": f"Take ~{int(max(5.0, grams))}g Fast Carbs",
            "status": "warning_low",
            "explanation": f"Required to prevent/intercept low blood sugar (projected {f60:.0f} mg/dL)."
        }

    # 2. Check if blood sugar is high (>160 mg/dL)
    if cur > upper_limit:
        return {
            "type": "restricted",
            "grams": 0,
            "label": "0g (Elevated)",
            "status": "warning_high",
            "explanation": f"Glucose is elevated ({cur:.0f} mg/dL). Avoid carbs until level normalizes."
        }

    # 3. In-range safe snack allowance
    # Safe headroom = upper_limit - projected glucose + insulin headroom
    effective_glucose = max(cur, f60)
    headroom = (upper_limit - effective_glucose) + (iob_val * isf_val)
    safe_grams = max(0.0, headroom / csf_val)
    # Cap at a reasonable single snack ceiling (e.g. 35g)
    safe_grams = min(35.0, round(safe_grams, 0))

    if safe_grams >= 10.0:
        return {
            "type": "snack_allowance",
            "grams": safe_grams,
            "label": f"Safe Snack: ~{int(safe_grams)}g",
            "status": "optimal",
            "explanation": f"You can consume up to ~{int(safe_grams)}g carbs without spiking above {int(upper_limit)} mg/dL."
        }
    else:
        return {
            "type": "snack_allowance",
            "grams": safe_grams,
            "label": f"Small Snack: ~{int(safe_grams)}g",
            "status": "optimal",
            "explanation": f"Limited carb buffer ({int(safe_grams)}g) before reaching upper target threshold."
        }


def calculate_proactive_alert(current_glucose, predictions, iob, isf=50.0, csf=4.0):
    """
    Analyzes forecasts specifically >1 hour out (60, 90, 120 min) to generate
    early-warning alerts and preventative instructions.
    """
    if not predictions:
        return {
            "level": "neutral",
            "title": "Monitoring Trends",
            "message": "Collecting trend history to forecast >1 hour ahead.",
            "forecast_60": None,
            "forecast_90": None,
            "forecast_120": None
        }

    f60 = next((p['value'] for p in predictions if p['minutes'] == 60), None)
    f90 = next((p['value'] for p in predictions if p['minutes'] == 90), None)
    f120 = next((p['value'] for p in predictions if p['minutes'] == 120), None)

    # Find minimum and maximum future values over 60-120 minutes
    future_vals = [p for p in predictions if p['minutes'] >= 60]
    if not future_vals:
        future_vals = predictions

    min_future = min(future_vals, key=lambda x: x['value'])
    max_future = max(future_vals, key=lambda x: x['value'])

    # Low alert for >1 hour out (<75 mg/dL)
    if min_future['value'] < 75.0:
        needed_carbs = suggest_carbs(current_glucose, min_future['value'], iob)
        return {
            "level": "warning_low",
            "badge": "⚠️ Proactive Low Warning",
            "title": f"Projected {min_future['value']:.0f} mg/dL in {min_future['minutes']}m",
            "message": f"Pre-emptive Action: Consume ~{needed_carbs:.0f}g carbs now to prevent a low in {min_future['minutes']} minutes.",
            "forecast_60": f60,
            "forecast_90": f90,
            "forecast_120": f120,
            "target_action": "eat_carbs",
            "action_val": needed_carbs
        }

    # High alert for >1 hour out (>180 mg/dL)
    if max_future['value'] > 180.0:
        needed_correction = suggest_correction(max_future['value'], iob, target_glucose=120.0, isf=isf)
        return {
            "level": "warning_high",
            "badge": "⚠️ Proactive High Warning",
            "title": f"Projected {max_future['value']:.0f} mg/dL in {max_future['minutes']}m",
            "message": f"Pre-emptive Action: Projected rise to {max_future['value']:.0f} mg/dL. Consider ~{needed_correction:.1f} U correction bolus.",
            "forecast_60": f60,
            "forecast_90": f90,
            "forecast_120": f120,
            "target_action": "take_insulin",
            "action_val": needed_correction
        }

    # Stable in-range trajectory
    return {
        "level": "optimal",
        "badge": "🟢 Stable Trajectory (>1 Hour)",
        "title": f"In Target (60m: {f60:.0f} | 90m: {f90 or f60:.0f} mg/dL)",
        "message": "Projected blood sugar remains stable in target range (70–160 mg/dL) over the next 2+ hours.",
        "forecast_60": f60,
        "forecast_90": f90,
        "forecast_120": f120,
        "target_action": "none",
        "action_val": 0.0
    }


def get_lantus_schedule_status(timezone_str="America/New_York"):
    """
    Computes adherence and next due status for the 2x Daily Lantus Regimen:
    - 6:00 AM (13.0 Units)
    - 6:00 PM (13.0 Units)
    Total Daily: 26.0 Units
    """
    try:
        tz = pytz.timezone(timezone_str)
    except Exception:
        tz = pytz.utc

    now_local = datetime.now(timezone.utc).astimezone(tz)
    today_date = now_local.date()

    # Fetch last 36 hours of insulin logs to detect today's and yesterday's long-acting doses
    try:
        doses = db.get_insulin_history(36, include_imputed=False)
    except Exception:
        doses = []

    long_doses = []
    for d in doses:
        if d.get("long_acting") and float(d["long_acting"]) > 0:
            dt = parse_dt(d["timestamp"])
            if dt:
                dt_local = dt.astimezone(tz)
                long_doses.append({"time": dt_local, "units": float(d["long_acting"])})

    # Morning dose window: 04:00 to 12:00
    # Evening dose window: 16:00 to 23:59
    morning_taken = any(
        d["time"].date() == today_date and 4 <= d["time"].hour < 12 and d["units"] >= 8.0
        for d in long_doses
    )
    evening_taken = any(
        d["time"].date() == today_date and 16 <= d["time"].hour < 24 and d["units"] >= 8.0
        for d in long_doses
    )

    # Determine next scheduled dose
    morn_sched = tz.localize(datetime(today_date.year, today_date.month, today_date.day, 6, 0))
    eve_sched = tz.localize(datetime(today_date.year, today_date.month, today_date.day, 18, 0))

    if now_local < morn_sched:
        next_slot_time = morn_sched
        next_slot_name = "Morning Dose (13.0 U)"
        slot_status = "upcoming"
    elif now_local < eve_sched:
        if not morning_taken and (now_local - morn_sched).total_seconds() > 3600:
            next_slot_time = morn_sched
            next_slot_name = "Morning Dose (13.0 U) [OVERDUE]"
            slot_status = "overdue"
        else:
            next_slot_time = eve_sched
            next_slot_name = "Evening Dose (13.0 U)"
            slot_status = "upcoming"
    else:
        if not evening_taken and (now_local - eve_sched).total_seconds() > 3600:
            next_slot_time = eve_sched
            next_slot_name = "Evening Dose (13.0 U) [OVERDUE]"
            slot_status = "overdue"
        else:
            next_day = today_date + timedelta(days=1)
            next_slot_time = tz.localize(datetime(next_day.year, next_day.month, next_day.day, 6, 0))
            next_slot_name = "Tomorrow Morning (13.0 U)"
            slot_status = "upcoming"

    diff_secs = (next_slot_time - now_local).total_seconds()
    if diff_secs < 0:
        countdown_str = f"{int(abs(diff_secs)//60)} mins overdue"
    else:
        hrs = int(diff_secs // 3600)
        mins = int((diff_secs % 3600) // 60)
        countdown_str = f"in {hrs}h {mins}m" if hrs > 0 else f"in {mins}m"

    return {
        "regimen": "Twice Daily (13U @ 6 AM / 13U @ 6 PM)",
        "dose_units": 13.0,
        "total_daily_units": 26.0,
        "morning": {
            "time_str": "6:00 AM",
            "units": 13.0,
            "taken": morning_taken
        },
        "evening": {
            "time_str": "6:00 PM",
            "units": 13.0,
            "taken": evening_taken
        },
        "next_dose": {
            "name": next_slot_name,
            "time_str": next_slot_time.strftime("%I:%M %p"),
            "countdown": countdown_str,
            "status": slot_status,
            "units": 13.0
        }
    }

