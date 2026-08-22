import math
from datetime import timedelta
import pytz

def _safe_float(val, default=0.0):
    try:
        f = float(val) if val is not None else default
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (ValueError, TypeError):
        return default

def detect_and_impute_missing_meals(sorted_readings, sorted_food_logs, min_confidence=0.50):
    """
    Detects unexplained sharp rises in glucose that suggest unlogged carbohydrate consumption.
    Imputes a 'meal' event with estimated grams of carbs.
    
    :param sorted_readings: List of glucose dicts sorted by timestamp ASC
    :param sorted_food_logs: List of food log dicts sorted by timestamp ASC
    :param min_confidence: Minimum confidence score to produce an imputation (0.0 to 1.0)
    :return: List of imputed food dictionaries
    """
    n = len(sorted_readings)
    candidates = []

    # Search for sharp rise windows
    for i in range(n):
        r_start = sorted_readings[i]
        t_start = r_start['timestamp']
        if t_start.tzinfo is None:
            t_start = pytz.utc.localize(t_start)
        
        try:
            g_start = float(r_start['value'])
        except (ValueError, TypeError):
            continue

        # Look for peak in window [t_start + 15m, t_start + 90m]
        for j in range(i + 1, n):
            r_curr = sorted_readings[j]
            t_curr = r_curr['timestamp']
            if t_curr.tzinfo is None:
                t_curr = pytz.utc.localize(t_curr)
            
            dt_mins = (t_curr - t_start).total_seconds() / 60.0
            
            if dt_mins < 15.0:
                continue
            if dt_mins > 90.0:
                break
                
            try:
                g_curr = float(r_curr['value'])
            except (ValueError, TypeError):
                continue
                
            obs_rise = g_curr - g_start
            
            # Minimum rise threshold to consider a meal
            if obs_rise < 30.0:
                continue
                
            # Check if there is already a logged meal near t_start (-60 mins to +30 mins)
            near_logged = False
            for f in sorted_food_logs:
                f_time = f['timestamp']
                if f_time.tzinfo is None:
                    f_time = pytz.utc.localize(f_time)
                
                # If food was logged up to 60 mins before or 30 mins after start of spike
                if -3600 <= (f_time - t_start).total_seconds() <= 1800:
                    near_logged = True
                    break
                    
            confidence_divisor = 1.0
            if near_logged:
                # Divisor prevents duplicate logs if a small carb dose was logged but the spike was massive
                confidence_divisor = 2.5
                
            # Carb Ratio Heuristic: Assume 1g carb raises glucose by 4 mg/dL
            # (In a more advanced model, this would use personalized ISF/ICR ratios)
            carb_ratio = 4.0
            raw_imputed_carbs = obs_rise / carb_ratio
            
            # Clamp estimated carbs to realistic range [5g, 150g]
            imputed_carbs = max(5.0, min(150.0, round(raw_imputed_carbs, 1)))

            # Compute Confidence Score Components
            
            # 1. C_magnitude: scale up to 100 mg/dL rise (30 -> 0.0, 130+ -> 1.0)
            c_magnitude = min(1.0, max(0.0, (obs_rise - 30.0) / 100.0))
            
            # 2. C_shape: monotonicity ratio (is it a clean spike?)
            window_readings = sorted_readings[i:j+1]
            increasing_steps = 0
            total_steps = max(1, len(window_readings) - 1)
            for k_idx in range(len(window_readings) - 1):
                if window_readings[k_idx + 1]['value'] >= window_readings[k_idx]['value'] - 5.0: # allow minor noise
                    increasing_steps += 1
            c_shape = increasing_steps / total_steps
            
            # 3. C_rate: how fast did it rise? (mg/dL per minute)
            rate = obs_rise / max(dt_mins, 1.0)
            c_rate = min(1.0, max(0.0, (rate - 0.5) / 1.5))
            
            # 4. C_nadir: Ensure t_start is actually a valley (start of spike), not halfway up a mountain
            c_nadir = 1.0
            for k in range(i - 1, -1, -1):
                r_prev = sorted_readings[k]
                t_prev = r_prev['timestamp']
                if t_prev.tzinfo is None:
                    t_prev = pytz.utc.localize(t_prev)
                if (t_start - t_prev).total_seconds() > 2700:
                    break
                try:
                    g_prev = float(r_prev['value'])
                    if g_prev < g_start - 10.0:
                        c_nadir = 0.0  # Halfway up a mountain
                        break
                except (ValueError, TypeError):
                    continue

            raw_conf = (0.35 * c_magnitude + 0.25 * c_shape + 0.20 * c_rate + 0.20 * c_nadir) / max(confidence_divisor, 0.1)
            confidence_score = round(min(1.0, max(0.0, raw_conf)), 2)
            
            if confidence_score >= min_confidence:
                candidates.append({
                    "start_idx": i,
                    "peak_idx": j,
                    "timestamp": t_start,
                    "carbs_g": imputed_carbs,
                    "confidence_score": confidence_score,
                    "obs_rise": obs_rise
                })
                
    # Sort candidates by confidence score descending, then magnitude descending
    candidates.sort(key=lambda c: (c['confidence_score'], c['obs_rise']), reverse=True)
    
    final_imputations = []
    used_indices = set()
    
    for c in candidates:
        # Check overlap
        if c['start_idx'] in used_indices or c['peak_idx'] in used_indices:
            continue
            
        overlap = False
        for used_idx in used_indices:
            if c['start_idx'] <= used_idx <= c['peak_idx']:
                overlap = True
                break
        if overlap:
            continue
            
        # Minimum 2-hour gap between imputed meals to prevent compounding
        too_close = False
        for f in final_imputations:
            if abs((c['timestamp'] - f['timestamp']).total_seconds()) < 7200:
                too_close = True
                break
        if too_close:
            continue
            
        final_imputations.append(c)
        for idx in range(c['start_idx'], c['peak_idx'] + 1):
            used_indices.add(idx)
            
    # Format output
    imputed_food = []
    for c in final_imputations:
        imputed_food.append({
            "timestamp": c['timestamp'],
            "carbs_g": c['carbs_g'],
            "food_type": "Imputed Meal",
            "is_imputed": True,
            "confidence_score": c['confidence_score']
        })
        
    return imputed_food
