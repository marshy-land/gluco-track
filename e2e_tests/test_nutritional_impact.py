import pytest
from datetime import datetime, timezone, timedelta
import pytz
from fastapi.testclient import TestClient

from app import app
from ml_heuristics import get_time_of_day_bucket, calculate_nutritional_impact_modifiers, get_nutritional_impact

client = TestClient(app)

def test_get_time_of_day_bucket():
    tz = pytz.timezone("America/New_York")
    
    # Morning: 04:00 - 11:00
    dt_morning = tz.localize(datetime(2026, 8, 4, 7, 30))
    assert get_time_of_day_bucket(dt_morning) == "morning"
    
    # Afternoon: 11:00 - 17:00
    dt_afternoon = tz.localize(datetime(2026, 8, 4, 14, 0))
    assert get_time_of_day_bucket(dt_afternoon) == "afternoon"
    
    # Evening: 17:00 - 22:00
    dt_evening = tz.localize(datetime(2026, 8, 4, 18, 30))
    assert get_time_of_day_bucket(dt_evening) == "evening"
    
    # Night: 22:00 - 04:00
    dt_night1 = tz.localize(datetime(2026, 8, 4, 23, 15))
    dt_night2 = tz.localize(datetime(2026, 8, 4, 2, 45))
    assert get_time_of_day_bucket(dt_night1) == "night"
    assert get_time_of_day_bucket(dt_night2) == "night"

def test_calculate_nutritional_impact_fallbacks():
    # When sparse data (N < 3 per bucket), fallbacks should trigger
    res = calculate_nutritional_impact_modifiers(readings=[], doses=[])
    
    assert "time_buckets" in res
    assert "recommendations" in res
    
    buckets = res["time_buckets"]
    assert buckets["Morning"]["modifier"] == 1.25
    assert buckets["Morning"]["peak_rise_mgdl"] == 45.2
    assert buckets["Morning"]["peak_latency_min"] == 55
    
    assert buckets["Afternoon"]["modifier"] == 1.00
    assert buckets["Afternoon"]["peak_rise_mgdl"] == 35.0
    assert buckets["Afternoon"]["peak_latency_min"] == 45
    
    assert buckets["Evening"]["modifier"] == 1.10
    assert buckets["Evening"]["peak_rise_mgdl"] == 40.1
    assert buckets["Evening"]["peak_latency_min"] == 50
    
    assert buckets["Night"]["modifier"] == 1.40
    assert buckets["Night"]["peak_rise_mgdl"] == 52.8
    assert buckets["Night"]["peak_latency_min"] == 75
    
    assert len(res["recommendations"]) > 0

def test_calculate_nutritional_impact_excursions():
    # Build synthetic meal doses and glucose readings with 3 excursions per bucket
    tz = pytz.timezone("America/New_York")
    readings = []
    doses = []
    
    # Baseline time: August 1, 2026
    base_dt = tz.localize(datetime(2026, 8, 1, 0, 0))
    
    def add_excursion(start_dt, rise_amount, latency_mins):
        # Add meal dose
        doses.append({
            "timestamp": start_dt.isoformat(),
            "meal": 5.0,
            "rapid_acting": 5.0
        })
        # Add baseline reading
        readings.append({"timestamp": start_dt.isoformat(), "value": 100.0})
        # Add peak reading
        peak_dt = start_dt + timedelta(minutes=latency_mins)
        readings.append({"timestamp": peak_dt.isoformat(), "value": 100.0 + rise_amount})
        # Add intermediate / window end reading
        end_dt = start_dt + timedelta(minutes=120)
        readings.append({"timestamp": end_dt.isoformat(), "value": 110.0})

    # Morning meals (08:00): 3 events with 60 mg/dL rise, 60 min latency
    for day in range(3):
        t = base_dt + timedelta(days=day, hours=8)
        add_excursion(t, rise_amount=60.0, latency_mins=60)

    # Afternoon meals (13:00): 3 events with 40 mg/dL rise, 45 min latency (baseline bucket)
    for day in range(3):
        t = base_dt + timedelta(days=day, hours=13)
        add_excursion(t, rise_amount=40.0, latency_mins=45)

    # Evening meals (18:00): 3 events with 50 mg/dL rise, 50 min latency
    for day in range(3):
        t = base_dt + timedelta(days=day, hours=18)
        add_excursion(t, rise_amount=50.0, latency_mins=50)

    # Night meals (23:00): 3 events with 80 mg/dL rise, 75 min latency
    for day in range(3):
        t = base_dt + timedelta(days=day, hours=23)
        add_excursion(t, rise_amount=80.0, latency_mins=75)

    res = calculate_nutritional_impact_modifiers(readings=readings, doses=doses)
    buckets = res["time_buckets"]
    
    # Afternoon baseline peak rise = 40.0 mg/dL
    assert buckets["Afternoon"]["peak_rise_mgdl"] == 40.0
    assert buckets["Afternoon"]["modifier"] == 1.00
    
    # Morning modifier = 60.0 / 40.0 = 1.50
    assert buckets["Morning"]["peak_rise_mgdl"] == 60.0
    assert buckets["Morning"]["modifier"] == 1.50
    
    # Evening modifier = 50.0 / 40.0 = 1.25
    assert buckets["Evening"]["peak_rise_mgdl"] == 50.0
    assert buckets["Evening"]["modifier"] == 1.25
    
    # Night modifier = 80.0 / 40.0 = 2.00
    assert buckets["Night"]["peak_rise_mgdl"] == 80.0
    assert buckets["Night"]["modifier"] == 2.00
    
    # Check alias get_nutritional_impact
    res_alias = get_nutritional_impact()
    assert "time_buckets" in res_alias

def test_api_nutritional_impact_endpoint():
    response = client.get("/api/nutritional-impact")
    assert response.status_code == 200
    data = response.json()
    assert "time_buckets" in data
    assert "recommendations" in data
    
    for bucket in ["Morning", "Afternoon", "Evening", "Night"]:
        assert bucket in data["time_buckets"]
        b = data["time_buckets"][bucket]
        assert "peak_rise_mgdl" in b
        assert "peak_latency_min" in b
        assert "modifier" in b

    # Test alias route
    alias_response = client.get("/api/nutritional-impact/summary")
    assert alias_response.status_code == 200
    assert alias_response.json() == data
