import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from app import app

client = TestClient(app)

ENDPOINTS = ["/api/nutritional-impact", "/api/nutritional-impact/summary"]

# --- SCENARIO 1: Invalid `hours` Query Parameter Values ---

@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("invalid_hours", [-10, -1, 0, 4321, 999999, "abc", "invalid", "12.5"])
def test_invalid_hours_parameter(endpoint, invalid_hours):
    """
    Test that invalid 'hours' query parameters (negative, 0, non-integer string, out of bounds)
    trigger HTTP 422 Unprocessable Entity error from FastAPI validation.
    """
    response = client.get(f"{endpoint}?hours={invalid_hours}")
    assert response.status_code == 422, (
        f"Expected HTTP 422 for {endpoint}?hours={invalid_hours}, got {response.status_code}"
    )
    data = response.json()
    assert "detail" in data, "FastAPI validation error response should contain 'detail' key"


# --- SCENARIO 2: Querying Endpoints when DB has No Readings ---

@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_no_readings_in_db_returns_fallbacks(endpoint):
    """
    Test that querying the endpoint when DB has zero readings returns HTTP 200
    and clinical reference fallback values as defined in the specification.
    """
    with patch("db.get_history", return_value=[]), patch("db.get_insulin_history", return_value=[]):
        response = client.get(endpoint)
        assert response.status_code == 200, f"Expected 200 OK when DB is empty, got {response.status_code}"
        data = response.json()
        
        # Verify root structure
        assert "time_buckets" in data
        assert "recommendations" in data
        
        buckets = data["time_buckets"]
        
        # Verify clinical reference fallbacks
        expected_fallbacks = {
            "Morning": {"peak_rise_mgdl": 45.2, "peak_latency_min": 55, "modifier": 1.25},
            "Afternoon": {"peak_rise_mgdl": 35.0, "peak_latency_min": 45, "modifier": 1.00},
            "Evening": {"peak_rise_mgdl": 40.1, "peak_latency_min": 50, "modifier": 1.10},
            "Night": {"peak_rise_mgdl": 52.8, "peak_latency_min": 75, "modifier": 1.40},
        }
        
        for bucket_name, expected in expected_fallbacks.items():
            assert bucket_name in buckets, f"Missing bucket {bucket_name} in time_buckets"
            b = buckets[bucket_name]
            assert pytest.approx(b["peak_rise_mgdl"], 0.01) == expected["peak_rise_mgdl"]
            assert b["peak_latency_min"] == expected["peak_latency_min"]
            assert pytest.approx(b["modifier"], 0.01) == expected["modifier"]

        # Verify recommendations list is non-empty
        assert isinstance(data["recommendations"], list)
        assert len(data["recommendations"]) > 0


# --- SCENARIO 3: Response JSON Schema Validation Against PROJECT.md Contract ---

@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_schema_validation_against_contract(endpoint):
    """
    Validates JSON response against the PROJECT.md interface contract:
    - Root object has 'time_buckets' (dict) and 'recommendations' (list of strings).
    - 'time_buckets' contains Morning, Afternoon, Evening, Night.
    - Each bucket has peak_rise_mgdl (number), peak_latency_min (number), modifier (number).
    """
    response = client.get(endpoint)
    assert response.status_code == 200
    data = response.json()
    
    assert isinstance(data, dict), "Response must be a JSON object"
    assert "time_buckets" in data, "Response missing 'time_buckets'"
    assert "recommendations" in data, "Response missing 'recommendations'"
    
    time_buckets = data["time_buckets"]
    assert isinstance(time_buckets, dict), "'time_buckets' must be an object"
    
    expected_buckets = ["Morning", "Afternoon", "Evening", "Night"]
    for b_name in expected_buckets:
        assert b_name in time_buckets, f"Bucket '{b_name}' missing from 'time_buckets'"
        b_val = time_buckets[b_name]
        assert isinstance(b_val, dict), f"Bucket '{b_name}' must be an object"
        
        assert "peak_rise_mgdl" in b_val, f"Bucket '{b_name}' missing 'peak_rise_mgdl'"
        assert "peak_latency_min" in b_val, f"Bucket '{b_name}' missing 'peak_latency_min'"
        assert "modifier" in b_val, f"Bucket '{b_name}' missing 'modifier'"
        
        assert isinstance(b_val["peak_rise_mgdl"], (int, float)), f"'{b_name}.peak_rise_mgdl' must be numeric"
        assert isinstance(b_val["peak_latency_min"], (int, float)), f"'{b_name}.peak_latency_min' must be numeric"
        assert isinstance(b_val["modifier"], (int, float)), f"'{b_name}.modifier' must be numeric"
        
        assert b_val["peak_rise_mgdl"] >= 0, f"'{b_name}.peak_rise_mgdl' must be non-negative"
        assert b_val["peak_latency_min"] > 0, f"'{b_name}.peak_latency_min' must be positive"
        assert b_val["modifier"] > 0, f"'{b_name}.modifier' must be positive"
        
    recommendations = data["recommendations"]
    assert isinstance(recommendations, list), "'recommendations' must be a list"
    for rec in recommendations:
        assert isinstance(rec, str), f"Recommendation item '{rec}' must be a string"
        assert len(rec.strip()) > 0, "Recommendation item cannot be empty string"


# --- SCENARIO 4: Boundary Values for 'hours' Parameter ---

@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("valid_hours", [1, 720, 4320])
def test_valid_hours_boundary_values(endpoint, valid_hours):
    """
    Test that valid boundary values (1, default 720, max 4320) return HTTP 200 OK.
    """
    response = client.get(f"{endpoint}?hours={valid_hours}")
    assert response.status_code == 200, f"Expected 200 for hours={valid_hours}, got {response.status_code}"


# --- SCENARIO 5: Robustness Under DB Connection Failure / Exception ---

@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_db_exception_resilience(endpoint):
    """
    Test that if database query raises an exception, the system handles it gracefully,
    returning fallbacks instead of crashing with HTTP 500.
    """
    with patch("db.get_history", side_effect=Exception("Database connection error")), \
         patch("db.get_insulin_history", side_effect=Exception("Database connection error")):
        response = client.get(endpoint)
        assert response.status_code == 200, f"Expected 200 fallback on DB error, got {response.status_code}"
        data = response.json()
        assert "time_buckets" in data


# --- SCENARIO 6: Parity Between Primary Route and Alias Route ---

def test_endpoint_alias_parity():
    """
    Verify that /api/nutritional-impact and /api/nutritional-impact/summary
    return identical data.
    """
    res1 = client.get("/api/nutritional-impact?hours=100")
    res2 = client.get("/api/nutritional-impact/summary?hours=100")
    
    assert res1.status_code == 200
    assert res2.status_code == 200
    assert res1.json() == res2.json()
