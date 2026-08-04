"""
Milestone 3 (Iteration 2) Stress Test Harness
Location: c:\\Users\\tugha\\Documents\\antigravity\\noble-galileo\\.agents\\challenger_m3_r2_1\\test_stress_r2.py

Target requirements:
- Concurrent DB calls (init_db() multi-threading and mixed DB concurrency)
- Circadian time bucket calculations (get_time_of_day_bucket)
- Boundary hours and microsecond accuracy
- Timezone handling and corrupted inputs
- Time-of-day nutritional impact model (calculate_nutritional_impact_modifiers)
- High data volume performance & schema compliance
- FastAPI endpoint stress testing (/api/nutritional-impact)
"""

import sys
import os
import time
import math
import concurrent.futures
from datetime import datetime, timedelta, timezone
import pytz
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Ensure root directory is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import db
import ml_heuristics
from ml_heuristics import (
    get_time_of_day_bucket,
    calculate_nutritional_impact_modifiers,
    get_nutritional_impact,
    parse_dt,
    FALLBACK_NUTRITIONAL_BUCKETS
)
from app import app

client = TestClient(app)

# =====================================================================
# SECTION 1: CONCURRENT DB CALLS & THREAD SAFETY
# =====================================================================

def test_stress_concurrent_init_db_multithreaded():
    """
    Stress test init_db() with 30 concurrent threads calling it simultaneously.
    Verifies lock serialization prevents deadlocks and psycopg2 catalog errors.
    """
    num_threads = 30
    exceptions = []

    def call_init_db():
        try:
            db.init_db()
        except Exception as e:
            exceptions.append(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(call_init_db) for _ in range(num_threads)]
        concurrent.futures.wait(futures)

    assert len(exceptions) == 0, f"Concurrent init_db() raised {len(exceptions)} exceptions: {exceptions}"


def test_stress_concurrent_mixed_db_operations():
    """
    Stress test init_db() running concurrently with insert_readings, insert_insulin_doses, and get_history.
    Verifies system stability when DDL migrations occur alongside active DML queries.
    """
    num_threads = 20
    exceptions = []
    
    base_ts = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
    
    def worker_init():
        try:
            db.init_db()
        except Exception as e:
            exceptions.append(("init_db", e))

    def worker_insert_readings(idx):
        try:
            readings = [
                {
                    "timestamp": (base_ts + timedelta(minutes=idx * 5 + i)).isoformat(),
                    "value": 110.0 + i,
                    "type": 1,
                    "device": "TestDevice",
                    "serial_number": "SN123",
                    "record_type": 0
                }
                for i in range(5)
            ]
            db.insert_readings(readings)
        except Exception as e:
            exceptions.append(("insert_readings", e))

    def worker_insert_doses(idx):
        try:
            doses = [
                {
                    "timestamp": (base_ts + timedelta(hours=idx, minutes=i*10)).isoformat(),
                    "rapid_acting": 2.0,
                    "meal": 30.0,
                    "correction": 0.0,
                    "is_imputed": False
                }
                for i in range(3)
            ]
            db.insert_insulin_doses(doses)
        except Exception as e:
            exceptions.append(("insert_doses", e))

    def worker_query():
        try:
            _ = db.get_history(24)
            _ = db.get_insulin_history(24)
        except Exception as e:
            exceptions.append(("query", e))

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = []
        for i in range(num_threads):
            if i % 4 == 0:
                futures.append(executor.submit(worker_init))
            elif i % 4 == 1:
                futures.append(executor.submit(worker_insert_readings, i))
            elif i % 4 == 2:
                futures.append(executor.submit(worker_insert_doses, i))
            else:
                futures.append(executor.submit(worker_query))
        concurrent.futures.wait(futures)

    assert len(exceptions) == 0, f"Mixed DB operations raised exceptions: {exceptions}"


def test_init_db_missing_schema_file():
    """Verify init_db gracefully handles missing schema.sql without throwing an unhandled error."""
    with patch("os.path.exists", return_value=False):
        try:
            db.init_db()
        except Exception as e:
            pytest.fail(f"init_db() raised unexpected exception when schema.sql missing: {e}")


def test_init_db_connection_error():
    """Verify init_db handles database connection failures by raising or logging properly."""
    with patch("db.get_connection", side_effect=Exception("Database unreachable")):
        with pytest.raises(Exception, match="Database unreachable"):
            db.init_db()


# =====================================================================
# SECTION 2: CIRCADIAN TIME BUCKET CALCULATIONS & BOUNDARY HOURS
# =====================================================================

def test_circadian_bucket_exact_hours():
    """
    Verify get_time_of_day_bucket maps exact boundary hours accurately:
    Morning: 04:00 - 11:00 (4 <= hour < 11)
    Afternoon: 11:00 - 17:00 (11 <= hour < 17)
    Evening: 17:00 - 22:00 (17 <= hour < 22)
    Night: 22:00 - 04:00 (22 <= hour or hour < 4)
    """
    tz = pytz.timezone("America/New_York")
    
    # Boundary hour 04:00 -> Morning
    dt = tz.localize(datetime(2026, 8, 4, 4, 0, 0))
    assert get_time_of_day_bucket(dt) == "morning"

    # Boundary hour 11:00 -> Afternoon
    dt = tz.localize(datetime(2026, 8, 4, 11, 0, 0))
    assert get_time_of_day_bucket(dt) == "afternoon"

    # Boundary hour 17:00 -> Evening
    dt = tz.localize(datetime(2026, 8, 4, 17, 0, 0))
    assert get_time_of_day_bucket(dt) == "evening"

    # Boundary hour 22:00 -> Night
    dt = tz.localize(datetime(2026, 8, 4, 22, 0, 0))
    assert get_time_of_day_bucket(dt) == "night"

    # Midnight 00:00 -> Night
    dt = tz.localize(datetime(2026, 8, 4, 0, 0, 0))
    assert get_time_of_day_bucket(dt) == "night"


def test_circadian_bucket_microsecond_boundaries():
    """Verify subsecond boundary hour transitions."""
    tz = pytz.utc

    # 03:59:59.999999 -> Night
    dt = tz.localize(datetime(2026, 8, 4, 3, 59, 59, 999999))
    assert get_time_of_day_bucket(dt, "UTC") == "night"

    # 04:00:00.000000 -> Morning
    dt = tz.localize(datetime(2026, 8, 4, 4, 0, 0, 0))
    assert get_time_of_day_bucket(dt, "UTC") == "morning"

    # 10:59:59.999999 -> Morning
    dt = tz.localize(datetime(2026, 8, 4, 10, 59, 59, 999999))
    assert get_time_of_day_bucket(dt, "UTC") == "morning"

    # 11:00:00.000000 -> Afternoon
    dt = tz.localize(datetime(2026, 8, 4, 11, 0, 0, 0))
    assert get_time_of_day_bucket(dt, "UTC") == "afternoon"

    # 16:59:59.999999 -> Afternoon
    dt = tz.localize(datetime(2026, 8, 4, 16, 59, 59, 999999))
    assert get_time_of_day_bucket(dt, "UTC") == "afternoon"

    # 17:00:00.000000 -> Evening
    dt = tz.localize(datetime(2026, 8, 4, 17, 0, 0, 0))
    assert get_time_of_day_bucket(dt, "UTC") == "evening"

    # 21:59:59.999999 -> Evening
    dt = tz.localize(datetime(2026, 8, 4, 21, 59, 59, 999999))
    assert get_time_of_day_bucket(dt, "UTC") == "evening"

    # 22:00:00.000000 -> Night
    dt = tz.localize(datetime(2026, 8, 4, 22, 0, 0, 0))
    assert get_time_of_day_bucket(dt, "UTC") == "night"

    # 23:59:59.999999 -> Night
    dt = tz.localize(datetime(2026, 8, 4, 23, 59, 59, 999999))
    assert get_time_of_day_bucket(dt, "UTC") == "night"


def test_circadian_bucket_timezone_conversions():
    """Verify timezone conversions accurately map UTC timestamps to local time buckets."""
    # 2026-08-04 15:00:00 UTC
    dt_utc = datetime(2026, 8, 4, 15, 0, 0, tzinfo=timezone.utc)

    # In America/New_York (EDT, UTC-4), local time is 11:00 AM -> Afternoon
    assert get_time_of_day_bucket(dt_utc, "America/New_York") == "afternoon"

    # In UTC, local time is 15:00 (3 PM) -> Afternoon
    assert get_time_of_day_bucket(dt_utc, "UTC") == "afternoon"

    # In Asia/Tokyo (UTC+9), local time is 00:00 AM (next day) -> Night
    assert get_time_of_day_bucket(dt_utc, "Asia/Tokyo") == "night"

    # In Europe/London (BST, UTC+1), local time is 16:00 (4 PM) -> Afternoon
    assert get_time_of_day_bucket(dt_utc, "Europe/London") == "afternoon"

    # In Australia/Sydney (AEST, UTC+10), local time is 01:00 AM (next day) -> Night
    assert get_time_of_day_bucket(dt_utc, "Australia/Sydney") == "night"


def test_circadian_bucket_invalid_timezone_fallback():
    """Verify get_time_of_day_bucket falls back safely to UTC when invalid timezone is passed."""
    dt = datetime(2026, 8, 4, 8, 0, 0, tzinfo=timezone.utc) # 08:00 UTC -> Morning
    
    # Invalid string
    assert get_time_of_day_bucket(dt, "Invalid/Timezone_Name") == "morning"
    
    # Empty string
    assert get_time_of_day_bucket(dt, "") == "morning"
    
    # None
    assert get_time_of_day_bucket(dt, None) == "morning"


def test_circadian_bucket_naive_datetime():
    """Verify get_time_of_day_bucket handles naive datetimes (assuming UTC)."""
    dt_naive = datetime(2026, 8, 4, 8, 0, 0)
    assert get_time_of_day_bucket(dt_naive, "UTC") == "morning"


# =====================================================================
# SECTION 3: NUTRITIONAL IMPACT MODEL & EDGE CASES
# =====================================================================

def test_nutritional_impact_empty_and_none_inputs():
    """Verify model fallback behavior for empty or None inputs."""
    res_empty = calculate_nutritional_impact_modifiers(readings=[], doses=[])
    assert "time_buckets" in res_empty
    assert "recommendations" in res_empty
    for b in ["Morning", "Afternoon", "Evening", "Night"]:
        assert b in res_empty["time_buckets"]
        assert res_empty["time_buckets"][b]["modifier"] == FALLBACK_NUTRITIONAL_BUCKETS[b]["modifier"]

    res_none = calculate_nutritional_impact_modifiers(readings=None, doses=None)
    assert "time_buckets" in res_none
    assert len(res_none["recommendations"]) > 0


def test_nutritional_impact_corrupted_data_resilience():
    """
    Stress test calculate_nutritional_impact_modifiers with corrupted, missing,
    or invalid data fields in readings and doses.
    """
    corrupted_readings = [
        {"timestamp": "2026-08-04T08:00:00Z", "value": None},
        {"timestamp": "invalid-timestamp", "value": 120.0},
        {"value": 150.0}, # missing timestamp
        {"timestamp": "2026-08-04T08:15:00Z", "value": "non-numeric"},
        {"timestamp": "2026-08-04T08:30:00Z", "value": 180.0},
        {"timestamp": "2026-08-04T08:45:00Z", "value": 200.0},
    ]

    corrupted_doses = [
        {"timestamp": None, "meal": 50.0},
        {"timestamp": "invalid-iso", "rapid_acting": 5.0},
        {"timestamp": "2026-08-04T08:00:00Z", "meal": "not-a-number"},
        {"timestamp": "2026-08-04T08:00:00Z", "meal": 40.0, "rapid_acting": 4.0},
    ]

    # Model must execute without throwing uncaught exceptions
    res = calculate_nutritional_impact_modifiers(readings=corrupted_readings, doses=corrupted_doses)
    assert "time_buckets" in res
    assert "recommendations" in res


def test_nutritional_impact_modifier_clamping_and_extreme_excursions():
    """
    Verify modifier value clamping: raw_mod clamped to range [0.50, 2.50].
    """
    tz = pytz.utc
    base_dt = datetime(2026, 8, 1, 0, 0, tzinfo=tz)

    readings = []
    doses = []

    def add_bucket_excursions(hour, rise):
        for day in range(3):
            t_meal = base_dt + timedelta(days=day, hours=hour)
            doses.append({"timestamp": t_meal.isoformat(), "meal": 50.0, "rapid_acting": 5.0})
            readings.append({"timestamp": t_meal.isoformat(), "value": 100.0})
            readings.append({"timestamp": (t_meal + timedelta(minutes=45)).isoformat(), "value": 100.0 + rise})
            readings.append({"timestamp": (t_meal + timedelta(minutes=120)).isoformat(), "value": 100.0})

    # Afternoon (baseline): rise = 40.0
    add_bucket_excursions(13, 40.0)

    # Morning: extreme rise = 400.0 (raw mod = 10.0 -> clamped to 2.50)
    add_bucket_excursions(8, 400.0)

    # Evening: tiny rise = 5.0 (raw mod = 0.125 -> clamped to 0.50)
    add_bucket_excursions(18, 5.0)

    res = calculate_nutritional_impact_modifiers(readings=readings, doses=doses, timezone_str="UTC")
    buckets = res["time_buckets"]

    assert buckets["Morning"]["modifier"] == 2.50, f"Expected clamped 2.50, got {buckets['Morning']['modifier']}"
    assert buckets["Evening"]["modifier"] == 0.50, f"Expected clamped 0.50, got {buckets['Evening']['modifier']}"


def test_nutritional_impact_high_volume_performance():
    """
    Stress test performance with 10,000 readings and 2,000 doses.
    Model execution time should be under 2.0 seconds.
    """
    base_dt = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    readings = []
    doses = []

    # 10,000 readings (5-minute intervals over ~34 days)
    for i in range(10000):
        t = base_dt + timedelta(minutes=i * 5)
        readings.append({"timestamp": t.isoformat(), "value": 100.0 + (i % 50)})

    # 2,000 doses (every ~24 minutes)
    for j in range(2000):
        t = base_dt + timedelta(minutes=j * 24)
        doses.append({"timestamp": t.isoformat(), "meal": 30.0, "rapid_acting": 3.0})

    start_time = time.time()
    res = calculate_nutritional_impact_modifiers(readings=readings, doses=doses, timezone_str="UTC")
    elapsed = time.time() - start_time

    assert elapsed < 2.0, f"Execution took too long: {elapsed:.2f}s (target < 2.0s)"
    assert "time_buckets" in res


# =====================================================================
# SECTION 4: FASTAPI ENDPOINT STRESS & VERIFICATION
# =====================================================================

def test_api_nutritional_impact_schema_and_headers():
    """Verify GET /api/nutritional-impact endpoint returns valid schema and status 200."""
    response = client.get("/api/nutritional-impact")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    
    data = response.json()
    assert "time_buckets" in data
    assert "recommendations" in data

    for bucket in ["Morning", "Afternoon", "Evening", "Night"]:
        assert bucket in data["time_buckets"]
        b = data["time_buckets"][bucket]
        assert isinstance(b["peak_rise_mgdl"], (int, float))
        assert isinstance(b["peak_latency_min"], int)
        assert isinstance(b["modifier"], (int, float))

    assert isinstance(data["recommendations"], list)
    assert len(data["recommendations"]) > 0


def test_api_nutritional_impact_concurrent_requests():
    """Stress test GET /api/nutritional-impact with 20 concurrent client requests."""
    num_requests = 20
    results = []

    def make_request():
        res = client.get("/api/nutritional-impact")
        return res.status_code, res.json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_requests) as executor:
        futures = [executor.submit(make_request) for _ in range(num_requests)]
        for f in concurrent.futures.as_completed(futures):
            status, json_data = f.result()
            results.append((status, json_data))

    assert len(results) == num_requests
    for status, json_data in results:
        assert status == 200
        assert "time_buckets" in json_data
