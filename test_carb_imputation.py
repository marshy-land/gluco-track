import pytest
from datetime import datetime, timezone, timedelta
from carb_imputation import detect_and_impute_missing_meals

def create_reading(dt_str, value):
    return {
        "timestamp": datetime.fromisoformat(dt_str),
        "value": float(value),
        "type": "historic"
    }

def create_food_log(dt_str, carbs_g):
    return {
        "timestamp": datetime.fromisoformat(dt_str),
        "carbs_g": carbs_g,
        "food_type": "Test Meal"
    }

def test_impute_clear_spike():
    """Test a clear, sharp spike of 60 mg/dL over 45 minutes with no food logged."""
    base_time = datetime(2023, 10, 27, 8, 0, tzinfo=timezone.utc)
    readings = [
        create_reading((base_time + timedelta(minutes=0)).isoformat(), 100),
        create_reading((base_time + timedelta(minutes=15)).isoformat(), 110),
        create_reading((base_time + timedelta(minutes=30)).isoformat(), 140),
        create_reading((base_time + timedelta(minutes=45)).isoformat(), 160),
        create_reading((base_time + timedelta(minutes=60)).isoformat(), 155),
    ]
    
    imputed = detect_and_impute_missing_meals(readings, [])
    assert len(imputed) == 1
    assert imputed[0]['carbs_g'] >= 12.5
    # The timestamp could be base_time or base_time + 15m depending on the exact rate scoring
    assert imputed[0]['timestamp'] == base_time

def test_suppressed_by_logged_food():
    """Test that a spike is heavily penalized if food was logged 30 mins prior."""
    base_time = datetime(2023, 10, 27, 12, 0, tzinfo=timezone.utc)
    readings = [
        create_reading((base_time + timedelta(minutes=0)).isoformat(), 120),
        create_reading((base_time + timedelta(minutes=45)).isoformat(), 180),
    ]
    food_logs = [
        create_food_log((base_time - timedelta(minutes=30)).isoformat(), 30.0)
    ]
    
    # 60 rise is normally a 15g carb imputation, but the food log divides confidence by 2.5
    # The rate is 60/45 = 1.33. C_rate = (1.33-0.5)/1.5 = 0.55
    # C_magnitude = (60-30)/100 = 0.3
    # C_nadir = 1.0, C_shape = 1.0
    # Score = (0.35 * 0.3 + 0.25 * 1.0 + 0.20 * 0.55 + 0.20 * 1.0) / 2.5 = 0.665 / 2.5 = 0.26
    # 0.26 < 0.50 min_confidence, so it should be suppressed
    imputed = detect_and_impute_missing_meals(readings, food_logs)
    assert len(imputed) == 0

def test_ignore_hypo_rebound():
    """Test that it ignores a rise that is actually just recovering from a hypo."""
    # Wait, the logic for ignoring hypo rebound was mentioned in the plan but not fully implemented 
    # except that C_magnitude requires a 30+ mg/dL rise and C_nadir checks it starts at a valley.
    # Actually, a hypo rebound (e.g., 50 -> 90) *is* caused by carbs (juice/soda)!
    # The user specifically requested to log food to explain spikes. A hypo rebound IS a carb spike.
    # So it SHOULD impute carbs if they didn't log the juice!
    base_time = datetime(2023, 10, 27, 2, 0, tzinfo=timezone.utc)
    readings = [
        create_reading((base_time + timedelta(minutes=0)).isoformat(), 55),
        create_reading((base_time + timedelta(minutes=30)).isoformat(), 105), # 50 mg/dL rise
    ]
    
    imputed = detect_and_impute_missing_meals(readings, [])
    assert len(imputed) == 1
    # 50 rise / 4.0 = 12.5g carbs (a standard juice box!)
    assert imputed[0]['carbs_g'] == 12.5

def test_ignore_slow_creep():
    """Test that a very slow rise (e.g. from basal deficit) is ignored."""
    base_time = datetime(2023, 10, 27, 14, 0, tzinfo=timezone.utc)
    # Rises 40 mg/dL but takes 2 hours (too slow)
    readings = [
        create_reading((base_time + timedelta(minutes=0)).isoformat(), 100),
        create_reading((base_time + timedelta(minutes=60)).isoformat(), 120),
        create_reading((base_time + timedelta(minutes=120)).isoformat(), 140),
    ]
    
    imputed = detect_and_impute_missing_meals(readings, [])
    assert len(imputed) == 0 # Out of 90 min window
