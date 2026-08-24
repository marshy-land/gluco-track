import pytest
from datetime import datetime, timezone, timedelta
import pytz
from fastapi.testclient import TestClient

from app import app
from prediction import (
    predict_glucose,
    suggest_correction,
    is_in_lantus_window,
    calculate_safe_carb_allowance,
    calculate_proactive_alert
)

client = TestClient(app)

def test_predict_glucose_3_hours():
    """Verify prediction engine projects out to 3 hours (180 mins)."""
    now = datetime.now(timezone.utc)
    readings = [
        {"timestamp": (now - timedelta(minutes=15 * i)).isoformat(), "value": 120 + i * 2}
        for i in range(10, -1, -1)
    ]
    preds = predict_glucose(readings, minutes_ahead=[15, 30, 60, 90, 120, 180])
    assert len(preds) == 6
    minutes = [p['minutes'] for p in preds]
    assert minutes == [15, 30, 60, 90, 120, 180]
    for p in preds:
        assert 40.0 <= p['value'] <= 400.0

def test_is_in_lantus_window():
    """Verify Lantus window detection at 6:00 AM and 6:00 PM Eastern."""
    tz = pytz.timezone("America/New_York")
    
    # 5:30 AM Eastern -> Inside Morning Window
    t_530_am = tz.localize(datetime(2026, 8, 24, 5, 30, 0))
    assert is_in_lantus_window(t_530_am, timezone_str="America/New_York", window_mins=60) is True

    # 6:15 AM Eastern -> Inside Morning Window
    t_615_am = tz.localize(datetime(2026, 8, 24, 6, 15, 0))
    assert is_in_lantus_window(t_615_am, timezone_str="America/New_York", window_mins=60) is True

    # 10:00 AM Eastern -> Outside Window
    t_1000_am = tz.localize(datetime(2026, 8, 24, 10, 0, 0))
    assert is_in_lantus_window(t_1000_am, timezone_str="America/New_York", window_mins=60) is False

    # 5:45 PM Eastern -> Inside Evening Window
    t_545_pm = tz.localize(datetime(2026, 8, 24, 17, 45, 0))
    assert is_in_lantus_window(t_545_pm, timezone_str="America/New_York", window_mins=60) is True

def test_suggest_correction_lantus_safety_lockout():
    """Verify correction bolus is locked to 0.0 U during Lantus administration window."""
    tz = pytz.timezone("America/New_York")
    t_morn_lantus = tz.localize(datetime(2026, 8, 24, 6, 5, 0)) # 6:05 AM
    
    # Blood sugar is 220 mg/dL (normally would warrant correction), but Lantus window is active
    dose = suggest_correction(
        current_glucose=220.0,
        iob=0.0,
        target_glucose=120.0,
        isf=40.0,
        current_time=t_morn_lantus,
        timezone_str="America/New_York",
        check_lantus_window=True
    )
    assert dose == 0.0

def test_suggest_correction_recent_bolus_suppression():
    """Verify correction bolus is suppressed if a dose was logged within 30 minutes."""
    tz = pytz.timezone("America/New_York")
    t_noon = tz.localize(datetime(2026, 8, 24, 12, 30, 0)) # 12:30 PM (outside lantus window)
    
    recent_doses = [
        {"timestamp": (t_noon - timedelta(minutes=10)).isoformat(), "rapid_acting": 2.0, "is_imputed": False}
    ]
    
    dose = suggest_correction(
        current_glucose=220.0,
        iob=0.5,
        target_glucose=120.0,
        isf=40.0,
        current_time=t_noon,
        timezone_str="America/New_York",
        check_lantus_window=True,
        recent_doses=recent_doses
    )
    assert dose == 0.0

def test_calculate_safe_carb_allowance_recent_carbs():
    """Verify recent carbs in last 30m suppresses repeated rescue alerts."""
    now = datetime.now(timezone.utc)
    recent_carbs = [
        {"timestamp": (now - timedelta(minutes=12)).isoformat(), "carbs_g": 20.0, "is_imputed": False}
    ]
    
    allowance = calculate_safe_carb_allowance(
        current_glucose=85.0, # low
        forecasted_60m=80.0,
        iob=0.0,
        recent_carbs=recent_carbs,
        current_time=now
    )
    assert allowance["type"] == "awaiting_absorption"
    assert "Active Carbs" in allowance["label"]

def test_patient_pwa_routes():
    """Verify that patient web app routes, manifest, and service worker load with 200 OK."""
    # Patient HTML
    res_patient = client.get("/patient")
    assert res_patient.status_code == 200
    assert "GlucoTrack" in res_patient.text
    assert "Safe Snack" in res_patient.text or "Carbohydrate Intake" in res_patient.text

    # Manifest JSON
    res_manifest = client.get("/manifest.json")
    assert res_manifest.status_code == 200
    assert "GlucoTrack Patient" in res_manifest.text

    # Service Worker JS
    res_sw = client.get("/sw.js")
    assert res_sw.status_code == 200
    assert "CACHE_NAME" in res_sw.text

    # Patient Summary API
    res_summary = client.get("/api/patient/summary")
    assert res_summary.status_code in [200, 404] # 200 if DB populated, 404 if fresh empty mock
