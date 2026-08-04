"""
Missing Dose Imputation Model for Gluco Track (Requirement R2)

Pharmacodynamic deconvolution inverting Scheiner decay curve bounded by time-of-day ISFs
to estimate unlogged insulin correction doses from surrounding glucose drops.
"""

import math
from datetime import datetime, timezone, timedelta
import pytz

from prediction import calculate_iob
from ml_heuristics import load_heuristics_params, get_time_of_day_bucket


def _safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        v = float(val)
        return default if (math.isnan(v) or math.isinf(v)) else v
    except (ValueError, TypeError):
        return default


def _to_utc_dt(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:
            return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return pytz.utc.localize(dt)
        return dt.astimezone(pytz.utc)
    return None


def detect_and_impute_missing_doses(
    glucose_readings,
    logged_doses,
    timezone_str="America/New_York",
    min_confidence=0.50
):
    """
    Scans glucose readings for unexplained drops, applies Scheiner curve deconvolution
    bounded by time-of-day ISFs, computes confidence scores, and returns imputed dose objects.
    """
    min_confidence = _safe_float(min_confidence, 0.50)
    if not glucose_readings or len(glucose_readings) < 4:
        return []

    # Pre-normalize timestamps to timezone-aware UTC datetimes
    norm_readings = []
    for r in glucose_readings:
        if not isinstance(r, dict) or 'timestamp' not in r or 'value' not in r:
            continue
        ts = _to_utc_dt(r['timestamp'])
        val = r['value']
        try:
            val_float = float(val)
        except (ValueError, TypeError):
            continue
        if ts is not None and not (math.isnan(val_float) or math.isinf(val_float)):
            r_copy = dict(r)
            r_copy['timestamp'] = ts
            r_copy['value'] = val_float
            norm_readings.append(r_copy)

    if len(norm_readings) < 4:
        return []

    norm_doses = []
    if logged_doses:
        for d in logged_doses:
            if isinstance(d, dict) and 'timestamp' in d:
                ts = _to_utc_dt(d['timestamp'])
                if ts is not None:
                    d_copy = dict(d)
                    d_copy['timestamp'] = ts
                    norm_doses.append(d_copy)

    # Sort glucose readings chronologically
    sorted_readings = sorted(norm_readings, key=lambda r: r['timestamp'])
    
    # Sort logged doses chronologically
    sorted_doses = sorted(norm_doses, key=lambda d: d['timestamp']) if norm_doses else []

    # Helper: resolve ISF for timestamp
    heuristics_params = load_heuristics_params()
    isf_map = heuristics_params.get("isf", {})

    def get_isf_for_time(dt):
        bucket = get_time_of_day_bucket(dt, timezone_str)
        val = isf_map.get(bucket) if isf_map.get(bucket) is not None else isf_map.get("global")
        if val is None or val <= 0.0:
            return 50.0
        return val

    candidates = []
    n = len(sorted_readings)

    # Search for drop windows
    # Window duration between 45 minutes and 240 minutes (4 hours)
    for i in range(n):
        r_start = sorted_readings[i]
        t_start = r_start['timestamp']
        try:
            g_start = float(r_start['value'])
        except (ValueError, TypeError):
            continue

        # Ensure tz-aware t_start
        if t_start.tzinfo is None:
            t_start = pytz.utc.localize(t_start)

        # Skip if starting glucose is too low for correction dose (e.g. < 120 mg/dL)
        if g_start < 120.0:
            continue

        # Carb Rebound Filter: Check if this peak was preceded by a rapid rise (e.g., soda correction).
        # Look back up to 45 minutes before t_start to see if glucose rose by more than 35 mg/dL.
        is_rebound = False
        for k in range(i - 1, -1, -1):
            r_prev = sorted_readings[k]
            t_prev = r_prev['timestamp']
            if t_prev.tzinfo is None:
                t_prev = pytz.utc.localize(t_prev)
            
            # Stop looking back if we go beyond 45 minutes
            if (t_start - t_prev).total_seconds() > 2700:
                break
                
            try:
                g_prev = float(r_prev['value'])
                if g_start - g_prev > 35.0:
                    is_rebound = True
                    break
            except (ValueError, TypeError):
                continue
                
        if is_rebound:
            # This is a sharp peak caused by carbs, and the subsequent drop is just active insulin taking over again.
            continue

        # Look for nadir in window [t_start + 45m, t_start + 240m]
        for j in range(i + 1, n):
            r_curr = sorted_readings[j]
            t_curr = r_curr['timestamp']
            if t_curr.tzinfo is None:
                t_curr = pytz.utc.localize(t_curr)

            dt_mins = (t_curr - t_start).total_seconds() / 60.0

            if dt_mins < 45.0:
                continue
            if dt_mins > 240.0:
                break

            try:
                g_curr = float(r_curr['value'])
            except (ValueError, TypeError):
                continue
            obs_drop = g_start - g_curr

            # Needs a minimum drop of 25 mg/dL to consider
            if obs_drop < 25.0:
                continue

            # Calculate logged IOB at t_start and t_curr
            iob_start = calculate_iob(sorted_doses, current_time=t_start)
            iob_curr = calculate_iob(sorted_doses, current_time=t_curr)

            isf = get_isf_for_time(t_start)

            # Expected drop from logged IOB
            expected_drop_logged = max(0.0, (iob_start - iob_curr) * isf)

            unexplained_drop = obs_drop - expected_drop_logged

            if unexplained_drop < 20.0:
                continue

            # Scheiner decay curve cumulative fraction exerted after dt_mins
            # F_act(t) = 1.0 - (1.0 - min(t, 240) / 240)^2
            action_duration_mins = 240.0
            t_eval = min(dt_mins, action_duration_mins)
            f_act = 1.0 - ((1.0 - (t_eval / action_duration_mins)) ** 2)

            if f_act <= 0.05:
                continue

            # Dose estimation = unexplained_drop / (ISF * f_act)
            raw_imputed_dose = unexplained_drop / (isf * f_act)

            # Clamp estimated dose to physiological range [1.0 U, 15.0 U] and round to nearest whole integer
            imputed_dose = max(1.0, min(15.0, float(math.floor(raw_imputed_dose + 0.5))))

            # Check if there is already a logged dose near t_start (+/- 120 mins)
            near_logged = False
            for d in sorted_doses:
                d_time = d['timestamp']
                if d_time.tzinfo is None:
                    d_time = pytz.utc.localize(d_time)
                # Insulin takes 60-90 mins to peak. A drop starting up to 120 mins
                # after a dose is very likely caused by that dose.
                if abs((d_time - t_start).total_seconds()) <= 7200:  # 120 mins
                    near_logged = True
                    break

            confidence_divisor = 1.0
            if near_logged:
                # Instead of completely skipping, apply a divisor to the confidence score.
                # Since the drop is highly likely caused by the delayed peak of the recorded dose,
                # we use a strong divisor.
                confidence_divisor = 2.5

            # Compute Confidence Score Components:
            # 1. C_magnitude: scale up to 150 mg/dL drop (20 -> 0.0, 150+ -> 1.0)
            c_magnitude = min(1.0, max(0.0, (unexplained_drop - 20.0) / 130.0))

            # 2. C_shape: monotonicity ratio of glucose readings between i and j
            window_readings = sorted_readings[i:j+1]
            decreasing_steps = 0
            total_steps = max(1, len(window_readings) - 1)
            for k_idx in range(len(window_readings) - 1):
                if window_readings[k_idx + 1]['value'] <= window_readings[k_idx]['value'] + 5.0:  # allow minor noise
                    decreasing_steps += 1
            c_shape = decreasing_steps / total_steps

            # 3. C_hyper: starting hyperglycemia score (120 -> 0.0, 200+ -> 1.0)
            c_hyper = min(1.0, max(0.0, (g_start - 120.0) / 80.0))

            # 4. C_no_carb: absence of recent meal doses or glucose spikes before t_start
            c_no_carb = 1.0
            for d in sorted_doses:
                d_time = d['timestamp']
                if d_time.tzinfo is None:
                    d_time = pytz.utc.localize(d_time)
                if -7200 <= (d_time - t_start).total_seconds() <= 0:
                    if _safe_float(d.get('meal'), 0.0) > 0.0:
                        c_no_carb = 0.3
                        break

            # 5. C_peak: Ensure t_start is actually a peak, not halfway down a cliff.
            # Look back 45 mins. If any reading was significantly higher than g_start, penalize heavily.
            c_peak = 1.0
            for k in range(i - 1, -1, -1):
                r_prev = sorted_readings[k]
                t_prev = r_prev['timestamp']
                if t_prev.tzinfo is None:
                    t_prev = pytz.utc.localize(t_prev)
                if (t_start - t_prev).total_seconds() > 2700:
                    break
                try:
                    g_prev = float(r_prev['value'])
                    if g_prev > g_start + 10.0:
                        c_peak = 0.0  # It's halfway down a cliff!
                        break
                    elif g_prev > g_start:
                        c_peak = 0.5  # Slight downward slope before t_start
                except (ValueError, TypeError):
                    continue

            # Weightings updated to include c_peak
            confidence_score = round(
                (0.35 * c_magnitude + 0.20 * c_shape + 0.15 * c_hyper + 0.10 * c_no_carb + 0.20 * c_peak) / confidence_divisor,
                2
            )

            if confidence_score >= min_confidence:
                candidates.append({
                    "start_idx": i,
                    "nadir_idx": j,
                    "timestamp": t_start,
                    "dose": imputed_dose,
                    "confidence_score": confidence_score,
                    "unexplained_drop": unexplained_drop,
                    "g_start": g_start,
                    "g_nadir": g_curr
                })

    if not candidates:
        return []

    # Non-overlapping greedy selection:
    # Sort candidates by confidence score descending, then unexplained drop descending
    candidates.sort(key=lambda c: (c['confidence_score'], c['unexplained_drop']), reverse=True)

    selected_imputations = []
    used_timestamps = []

    for c in candidates:
        ts = c['timestamp']
        # Ensure minimum gap of 3 hours between imputed doses
        conflict = False
        for used_ts in used_timestamps:
            if abs((ts - used_ts).total_seconds()) < 10800:  # 3 hours
                conflict = True
                break

        if not conflict:
            selected_imputations.append({
                "id": None,
                "timestamp": ts,
                "rapid_acting": c['dose'],
                "long_acting": 0.0,
                "meal": 0.0,
                "correction": c['dose'],
                "user_change": 0.0,
                "device": "Missing Dose Imputation Model",
                "serial_number": None,
                "is_imputed": True,
                "confidence_score": c['confidence_score']
            })
            used_timestamps.append(ts)

    # Sort chronologically before returning
    selected_imputations.sort(key=lambda x: x['timestamp'])
    return selected_imputations
