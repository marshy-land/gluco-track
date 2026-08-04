import math
from datetime import datetime, timezone
import pytz
import db

def predict_glucose(readings, minutes_ahead=[15, 30, 60], dampening_half_life=25):
    """
    Predicts glucose values for future time offsets using a dampened linear trend.
    `readings` is a list of dictionaries with 'timestamp' and 'value'.
    `dampening_half_life` is the time (in minutes) for the trend slope to decay by half.
    """
    if len(readings) < 2:
        return []

    # Sort readings chronologically
    sorted_readings = sorted(readings, key=lambda x: x['timestamp'])
    
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
    # Slope of the regression line
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
        # Dampened slope projection: integration of slope * e^(-decay_constant * t)
        # Projected value = G(0) + slope * (1 - e^(-decay_constant * mins)) / decay_constant
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

    total_iob = 0.0
    for d in doses:
        # Ensure timezone-aware comparison
        dose_time = d['timestamp']
        if dose_time.tzinfo is None:
            dose_time = pytz.utc.localize(dose_time)
            
        elapsed_mins = (current_time - dose_time).total_seconds() / 60.0

        if elapsed_mins < 0:
            # Dose is logged in the future (safety fallback: count full dose)
            elapsed_mins = 0

        if elapsed_mins >= action_duration_mins:
            continue

        # Extract all rapid-acting components
        rapid = d.get("rapid_acting") or 0.0
        meal = d.get("meal") or 0.0
        correction = d.get("correction") or 0.0
        # If user_change is logged, treat it as rapid acting if no other type is declared
        user_change = d.get("user_change") or 0.0 if not (rapid or meal or correction) else 0.0
        
        rapid_dose = rapid + meal + correction + user_change
        if rapid_dose <= 0:
            continue

        # Scheiner parabolic decay curve
        iob_fraction = (1.0 - (elapsed_mins / action_duration_mins)) ** 2
        total_iob += rapid_dose * iob_fraction

    return round(total_iob, 2)

def suggest_correction(current_glucose, iob, target_glucose=120, isf=50):
    """
    Suggests correction insulin units:
    Correction = (Current Glucose - Target Glucose) / ISF - IOB
    """
    if current_glucose <= target_glucose:
        return 0.0

    needed_bolus = (current_glucose - target_glucose) / isf
    suggested = needed_bolus - iob
    return round(max(0.0, suggested), 2)
