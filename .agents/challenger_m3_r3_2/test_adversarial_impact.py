import pytest
import concurrent.futures
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import app
import ml_heuristics

client = TestClient(app)

ENDPOINTS = ["/api/nutritional-impact", "/api/nutritional-impact/summary"]
REQUIRED_BUCKETS = ["Morning", "Afternoon", "Evening", "Night"]
REQUIRED_KEYS = {"peak_rise_mgdl", "peak_latency_min", "modifier"}

def assert_valid_nutritional_impact_schema(data):
    """Helper to validate JSON response schema against interface contract."""
    assert isinstance(data, dict), "Response root must be a dict"
    assert "time_buckets" in data, "Missing 'time_buckets' in response"
    assert "recommendations" in data, "Missing 'recommendations' in response"
    
    time_buckets = data["time_buckets"]
    assert isinstance(time_buckets, dict), "'time_buckets' must be a dict"
    for bucket in REQUIRED_BUCKETS:
        assert bucket in time_buckets, f"Missing bucket '{bucket}' in time_buckets"
        b_data = time_buckets[bucket]
        assert isinstance(b_data, dict), f"Bucket '{bucket}' must be a dict"
        for key in REQUIRED_KEYS:
            assert key in b_data, f"Missing key '{key}' in bucket '{bucket}'"
        
        # Data type and sanity checks
        assert isinstance(b_data["peak_rise_mgdl"], (int, float)), f"peak_rise_mgdl must be numeric in {bucket}"
        assert b_data["peak_rise_mgdl"] >= 0, f"peak_rise_mgdl must be >= 0 in {bucket}"
        assert isinstance(b_data["peak_latency_min"], int), f"peak_latency_min must be int in {bucket}"
        assert b_data["peak_latency_min"] >= 0, f"peak_latency_min must be >= 0 in {bucket}"
        assert isinstance(b_data["modifier"], (int, float)), f"modifier must be numeric in {bucket}"
        assert 0.50 <= b_data["modifier"] <= 2.50, f"modifier {b_data['modifier']} outside allowed range [0.50, 2.50] in {bucket}"

    recommendations = data["recommendations"]
    assert isinstance(recommendations, list), "'recommendations' must be a list"
    assert len(recommendations) > 0, "'recommendations' list must not be empty"
    for rec in recommendations:
        assert isinstance(rec, str) and len(rec) > 0, "Each recommendation must be a non-empty string"


# --- 1. Edge Case Inputs & Invalid Parameters ---

def test_invalid_hours_parameters():
    """Test out-of-range, non-integer, and malformed 'hours' query parameters."""
    invalid_params = [
        0,          # Below ge=1
        -1,         # Negative integer
        -720,       # Large negative
        4321,       # Above le=4320
        10000,      # Extremely large
        "abc",      # Non-numeric string
        "3.14",     # Float string
        "true",     # Boolean string
        "",         # Empty string
    ]
    
    for endpoint in ENDPOINTS:
        for val in invalid_params:
            res = client.get(f"{endpoint}?hours={val}")
            assert res.status_code == 422, f"Endpoint {endpoint}?hours={val} should return HTTP 422, got {res.status_code}"
            err_detail = res.json()
            assert "detail" in err_detail, f"Expected error detail in response for {endpoint}?hours={val}"


def test_valid_boundary_hours_parameters():
    """Test boundary values for 'hours' parameter (ge=1, le=4320)."""
    boundary_values = [1, 720, 4320]
    
    for endpoint in ENDPOINTS:
        for val in boundary_values:
            res = client.get(f"{endpoint}?hours={val}")
            assert res.status_code == 200, f"Endpoint {endpoint}?hours={val} failed with status {res.status_code}"
            assert_valid_nutritional_impact_schema(res.json())


def test_extra_query_parameters_ignored():
    """Verify that unexpected extra query parameters do not cause errors."""
    for endpoint in ENDPOINTS:
        res = client.get(f"{endpoint}?hours=720&unexpected_param=test&foo=bar")
        assert res.status_code == 200
        assert_valid_nutritional_impact_schema(res.json())


# --- 2. Endpoint Alias Parity ---

def test_endpoint_alias_parity():
    """Verify that /api/nutritional-impact and /api/nutritional-impact/summary return identical JSON."""
    for hours in [1, 720, 4320]:
        res1 = client.get(f"/api/nutritional-impact?hours={hours}")
        res2 = client.get(f"/api/nutritional-impact/summary?hours={hours}")
        assert res1.status_code == 200
        assert res2.status_code == 200
        assert res1.json() == res2.json(), f"Parity mismatch for hours={hours}"


# --- 3. Empty Database State ---

def test_empty_db_state_handling():
    """Verify API handles empty database gracefully with fallback defaults."""
    with patch("db.get_history", return_value=[]), \
         patch("db.get_insulin_history", return_value=[]):
        
        for endpoint in ENDPOINTS:
            res = client.get(endpoint)
            assert res.status_code == 200, f"Empty DB test failed for {endpoint}"
            data = res.json()
            assert_valid_nutritional_impact_schema(data)
            
            # Verify exact fallback metrics
            buckets = data["time_buckets"]
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


# --- 4. Corrupted / Malformed Database Data Resilience ---

def test_corrupted_db_data_resilience():
    """Verify API handles corrupted, null, NaN, Inf, and invalid types in DB without 500 errors."""
    corrupted_readings = [
        {"timestamp": "2026-08-01T10:00:00Z", "value": None},
        {"timestamp": "2026-08-01T10:05:00Z", "value": "invalid_number"},
        {"timestamp": "2026-08-01T10:10:00Z", "value": float("nan")},
        {"timestamp": "2026-08-01T10:15:00Z", "value": float("inf")},
        {"timestamp": "invalid_date_format", "value": 120.0},
        {"missing_timestamp": True, "value": 130.0},
        None,
        "not_a_dict",
        {"timestamp": "2026-08-01T10:20:00Z", "value": 140.0}, # Valid reading
    ]
    
    corrupted_doses = [
        {"timestamp": "2026-08-01T10:00:00Z", "meal": None, "rapid_acting": "bad"},
        {"timestamp": "2026-08-01T10:00:00Z", "meal": float("nan"), "rapid_acting": float("inf")},
        {"timestamp": "invalid_ts", "meal": 5.0},
        None,
        12345,
        {"timestamp": "2026-08-01T10:00:00Z", "meal": 5.0, "rapid_acting": 3.0}, # Valid dose
    ]

    with patch("db.get_history", return_value=corrupted_readings), \
         patch("db.get_insulin_history", return_value=corrupted_doses):
        
        for endpoint in ENDPOINTS:
            res = client.get(endpoint)
            assert res.status_code == 200, f"Corrupted DB test returned HTTP {res.status_code} for {endpoint}"
            assert_valid_nutritional_impact_schema(res.json())


# --- 5. Concurrent Requests Stress Test ---

def test_concurrent_requests():
    """Test 40 concurrent requests across both endpoints for thread safety and performance."""
    def make_request(url):
        return client.get(url)

    urls = (ENDPOINTS * 20) # 40 total requests
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(make_request, url) for url in urls]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
        
    for res in results:
        assert res.status_code == 200
        assert_valid_nutritional_impact_schema(res.json())
