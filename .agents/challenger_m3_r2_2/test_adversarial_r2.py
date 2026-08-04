import sys
import os
import pytest
from unittest.mock import patch
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi.testclient import TestClient

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app import app

client = TestClient(app)

ENDPOINTS = [
    "/api/nutritional-impact",
    "/api/nutritional-impact/summary"
]

def test_endpoints_empty_db():
    """Test endpoints with empty database state (no glucose readings, no insulin doses)."""
    with patch("db.get_history", return_value=[]), \
         patch("db.get_insulin_history", return_value=[]):
        for endpoint in ENDPOINTS:
            response = client.get(endpoint)
            assert response.status_code == 200, f"Failed for {endpoint}: status {response.status_code}"
            data = response.json()
            assert "time_buckets" in data
            assert "recommendations" in data
            
            buckets = data["time_buckets"]
            for b in ["Morning", "Afternoon", "Evening", "Night"]:
                assert b in buckets
                assert "peak_rise_mgdl" in buckets[b]
                assert "peak_latency_min" in buckets[b]
                assert "modifier" in buckets[b]
            
            # Verify clinical fallbacks trigger when data is empty
            assert buckets["Morning"]["modifier"] == 1.25
            assert buckets["Afternoon"]["modifier"] == 1.00
            assert buckets["Evening"]["modifier"] == 1.10
            assert buckets["Night"]["modifier"] == 1.40
            assert len(data["recommendations"]) > 0

def test_endpoints_valid_hours_boundaries():
    """Test valid query parameter boundary conditions for `hours` (ge=1, le=4320)."""
    valid_hours = [1, 24, 720, 4320]
    for endpoint in ENDPOINTS:
        for h in valid_hours:
            response = client.get(f"{endpoint}?hours={h}")
            assert response.status_code == 200, f"Failed for {endpoint}?hours={h}: status {response.status_code}"
            data = response.json()
            assert "time_buckets" in data
            assert "recommendations" in data

def test_endpoints_invalid_hours_parameters():
    """Test invalid query parameter conditions for `hours` (out of range, bad types, negative values)."""
    invalid_hours = [0, -1, -720, 4321, 10000, "abc", "3.14", ""]
    for endpoint in ENDPOINTS:
        for h in invalid_hours:
            response = client.get(f"{endpoint}?hours={h}")
            assert response.status_code == 422, f"Expected 422 for {endpoint}?hours={h}, got {response.status_code}"

def test_endpoints_unexpected_query_parameters():
    """Test handling of unexpected or extra query parameters."""
    for endpoint in ENDPOINTS:
        response = client.get(f"{endpoint}?hours=24&unknown_param=foo&extra_arg=bar")
        assert response.status_code == 200
        data = response.json()
        assert "time_buckets" in data
        assert "recommendations" in data

def test_endpoints_concurrent_requests():
    """Test concurrent API requests to ensure thread safety and no deadlock or race conditions."""
    def make_request(idx):
        endpoint = ENDPOINTS[idx % len(ENDPOINTS)]
        hours = [1, 24, 720, 4320][idx % 4]
        res = client.get(f"{endpoint}?hours={hours}")
        return res.status_code, res.json()

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(make_request, i) for i in range(30)]
        results = [f.result() for f in as_completed(futures)]

    for status, data in results:
        assert status == 200
        assert "time_buckets" in data
        assert "recommendations" in data

def test_endpoints_db_exception_resilience():
    """Test endpoint resilience when database queries fail/throw exceptions."""
    with patch("db.get_history", side_effect=RuntimeError("Database connection lost")), \
         patch("db.get_insulin_history", side_effect=RuntimeError("Database connection lost")):
        for endpoint in ENDPOINTS:
            response = client.get(endpoint)
            assert response.status_code == 200, f"Failed for {endpoint}: status {response.status_code}"
            data = response.json()
            assert "time_buckets" in data
            assert "recommendations" in data
            assert data["time_buckets"]["Morning"]["modifier"] == 1.25

def test_endpoints_corrupted_db_records():
    """
    Adversarial test for corrupted or malformed database records.
    Exposes whether ml_heuristics.py:parse_dt handles unparseable date strings or non-numeric values.
    """
    corrupted_readings = [
        {'timestamp': 'invalid-date-format', 'value': 120.0},
        {'timestamp': '2026-08-04T00:00:00Z', 'value': 'not-a-number'},
        None,
        {},
        {'value': 100.0} # missing timestamp
    ]
    corrupted_doses = [
        {'timestamp': 'corrupted-timestamp', 'meal': 5.0},
        None,
        {'meal': 'bad_value'}
    ]
    with patch("db.get_history", return_value=corrupted_readings), \
         patch("db.get_insulin_history", return_value=corrupted_doses):
        for endpoint in ENDPOINTS:
            response = client.get(endpoint)
            # Check if API returns 200 (resilient) or 500 (uncaught exception in parse_dt / float conversion)
            if response.status_code != 200:
                print(f"[FAIL VULNERABILITY FOUND] {endpoint} returned {response.status_code} on corrupted DB records: {response.text}")
            assert response.status_code == 200, f"Endpoint {endpoint} crashed with {response.status_code} on corrupted DB records"

def test_endpoints_schema_contract():
    """Verify strict JSON schema adherence for time_buckets and recommendations."""
    for endpoint in ENDPOINTS:
        response = client.get(endpoint)
        assert response.status_code == 200
        data = response.json()
        
        assert isinstance(data, dict)
        assert set(data.keys()) == {"time_buckets", "recommendations"}
        
        buckets = data["time_buckets"]
        assert isinstance(buckets, dict)
        assert set(buckets.keys()) == {"Morning", "Afternoon", "Evening", "Night"}
        
        for name, bucket in buckets.items():
            assert isinstance(bucket, dict)
            assert set(bucket.keys()) == {"peak_rise_mgdl", "peak_latency_min", "modifier"}
            assert isinstance(bucket["peak_rise_mgdl"], (int, float))
            assert isinstance(bucket["peak_latency_min"], (int, float))
            assert isinstance(bucket["modifier"], (int, float))
            assert bucket["modifier"] > 0
            assert bucket["peak_rise_mgdl"] >= 0
            assert bucket["peak_latency_min"] >= 0
            
        recommendations = data["recommendations"]
        assert isinstance(recommendations, list)
        assert len(recommendations) > 0
        for rec in recommendations:
            assert isinstance(rec, str)
            assert len(rec.strip()) > 0

if __name__ == "__main__":
    tests = [
        test_endpoints_empty_db,
        test_endpoints_valid_hours_boundaries,
        test_endpoints_invalid_hours_parameters,
        test_endpoints_unexpected_query_parameters,
        test_endpoints_concurrent_requests,
        test_endpoints_db_exception_resilience,
        test_endpoints_corrupted_db_records,
        test_endpoints_schema_contract
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"[PASS] {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {test.__name__}: {e}")
            failed += 1
    print(f"\nSummary: {passed} passed, {failed} failed.")
    if failed > 0:
        sys.exit(1)
