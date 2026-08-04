"""
Empirical Stress Test Harness for Time-of-Day Nutritional Impact Model (ml_heuristics.py)
Author: Challenger 1 (Milestone 3)
"""

import os
import sys
import unittest
import math
from datetime import datetime, timezone, timedelta
import pytz

# Add workspace root to path
WORKSPACE_DIR = r"c:\Users\tugha\Documents\antigravity\noble-galileo"
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)

from ml_heuristics import (
    get_time_of_day_bucket,
    calculate_nutritional_impact_modifiers,
    get_nutritional_impact,
    FALLBACK_NUTRITIONAL_BUCKETS,
)

class TestNutritionalImpactStress(unittest.TestCase):
    """Stress tests for ml_heuristics time-of-day nutritional impact model."""

    # -------------------------------------------------------------------------
    # Scenario 1: Boundary Hours Testing
    # -------------------------------------------------------------------------
    def test_boundary_hours_get_time_of_day_bucket(self):
        """Test get_time_of_day_bucket at exact boundary timestamps in local time."""
        tz = pytz.timezone("America/New_York")
        
        # 04:00 (Boundary Morning start) -> Morning
        dt_0400 = tz.localize(datetime(2026, 8, 4, 4, 0, 0))
        self.assertEqual(get_time_of_day_bucket(dt_0400), "morning")

        # 03:59:59 (Boundary Night end) -> Night
        dt_0359 = tz.localize(datetime(2026, 8, 4, 3, 59, 59))
        self.assertEqual(get_time_of_day_bucket(dt_0359), "night")

        # 11:00 (Boundary Afternoon start) -> Afternoon
        dt_1100 = tz.localize(datetime(2026, 8, 4, 11, 0, 0))
        self.assertEqual(get_time_of_day_bucket(dt_1100), "afternoon")

        # 10:59:59 (Boundary Morning end) -> Morning
        dt_1059 = tz.localize(datetime(2026, 8, 4, 10, 59, 59))
        self.assertEqual(get_time_of_day_bucket(dt_1059), "morning")

        # 17:00 (Boundary Evening start) -> Evening
        dt_1700 = tz.localize(datetime(2026, 8, 4, 17, 0, 0))
        self.assertEqual(get_time_of_day_bucket(dt_1700), "evening")

        # 16:59:59 (Boundary Afternoon end) -> Afternoon
        dt_1659 = tz.localize(datetime(2026, 8, 4, 16, 59, 59))
        self.assertEqual(get_time_of_day_bucket(dt_1659), "afternoon")

        # 22:00 (Boundary Night start) -> Night
        dt_2200 = tz.localize(datetime(2026, 8, 4, 22, 0, 0))
        self.assertEqual(get_time_of_day_bucket(dt_2200), "night")

        # 21:59:59 (Boundary Evening end) -> Evening
        dt_2159 = tz.localize(datetime(2026, 8, 4, 21, 59, 59))
        self.assertEqual(get_time_of_day_bucket(dt_2159), "evening")

        # 00:00 (Midnight) -> Night
        dt_0000 = tz.localize(datetime(2026, 8, 4, 0, 0, 0))
        self.assertEqual(get_time_of_day_bucket(dt_0000), "night")

    def test_boundary_hours_meal_excursions(self):
        """Test excursions anchored at exact boundary hours in calculate_nutritional_impact_modifiers."""
        tz = pytz.timezone("America/New_York")
        base_date = datetime(2026, 8, 1, 0, 0, 0)
        
        doses = []
        readings = []
        
        # Log 3 meals at exact 04:00 (Morning boundary)
        for day in range(3):
            t_meal = tz.localize(base_date + timedelta(days=day, hours=4))
            doses.append({"timestamp": t_meal.isoformat(), "meal": 10.0, "rapid_acting": 2.0})
            readings.append({"timestamp": t_meal.isoformat(), "value": 100.0})
            readings.append({"timestamp": (t_meal + timedelta(minutes=60)).isoformat(), "value": 150.0})
            readings.append({"timestamp": (t_meal + timedelta(minutes=180)).isoformat(), "value": 110.0})

        # Log 3 meals at exact 11:00 (Afternoon boundary)
        for day in range(3):
            t_meal = tz.localize(base_date + timedelta(days=day, hours=11))
            doses.append({"timestamp": t_meal.isoformat(), "meal": 10.0, "rapid_acting": 2.0})
            readings.append({"timestamp": t_meal.isoformat(), "value": 100.0})
            readings.append({"timestamp": (t_meal + timedelta(minutes=45)).isoformat(), "value": 140.0})
            readings.append({"timestamp": (t_meal + timedelta(minutes=180)).isoformat(), "value": 105.0})

        # Log 3 meals at exact 17:00 (Evening boundary)
        for day in range(3):
            t_meal = tz.localize(base_date + timedelta(days=day, hours=17))
            doses.append({"timestamp": t_meal.isoformat(), "meal": 10.0, "rapid_acting": 2.0})
            readings.append({"timestamp": t_meal.isoformat(), "value": 100.0})
            readings.append({"timestamp": (t_meal + timedelta(minutes=50)).isoformat(), "value": 144.0})
            readings.append({"timestamp": (t_meal + timedelta(minutes=180)).isoformat(), "value": 108.0})

        # Log 3 meals at exact 22:00 (Night boundary)
        for day in range(3):
            t_meal = tz.localize(base_date + timedelta(days=day, hours=22))
            doses.append({"timestamp": t_meal.isoformat(), "meal": 10.0, "rapid_acting": 2.0})
            readings.append({"timestamp": t_meal.isoformat(), "value": 100.0})
            readings.append({"timestamp": (t_meal + timedelta(minutes=75)).isoformat(), "value": 156.0})
            readings.append({"timestamp": (t_meal + timedelta(minutes=180)).isoformat(), "value": 112.0})

        result = calculate_nutritional_impact_modifiers(readings=readings, doses=doses, timezone_str="America/New_York")
        buckets = result["time_buckets"]

        # Morning: peak rise 50.0 mg/dL, Afternoon baseline 40.0 mg/dL -> mod = 50/40 = 1.25
        self.assertAlmostEqual(buckets["Morning"]["peak_rise_mgdl"], 50.0, delta=0.5)
        self.assertAlmostEqual(buckets["Morning"]["modifier"], 1.25, delta=0.05)

        # Afternoon: peak rise 40.0 mg/dL -> mod = 1.00
        self.assertAlmostEqual(buckets["Afternoon"]["peak_rise_mgdl"], 40.0, delta=0.5)
        self.assertEqual(buckets["Afternoon"]["modifier"], 1.00)

        # Evening: peak rise 44.0 mg/dL -> mod = 44/40 = 1.10
        self.assertAlmostEqual(buckets["Evening"]["peak_rise_mgdl"], 44.0, delta=0.5)
        self.assertAlmostEqual(buckets["Evening"]["modifier"], 1.10, delta=0.05)

        # Night: peak rise 56.0 mg/dL -> mod = 56/40 = 1.40
        self.assertAlmostEqual(buckets["Night"]["peak_rise_mgdl"], 56.0, delta=0.5)
        self.assertAlmostEqual(buckets["Night"]["modifier"], 1.40, delta=0.05)

    # -------------------------------------------------------------------------
    # Scenario 2: Sparse Dataset vs Dense Dataset
    # -------------------------------------------------------------------------
    def test_sparse_dataset_zero_readings(self):
        """Zero readings / zero doses -> fallbacks for all buckets."""
        res = calculate_nutritional_impact_modifiers(readings=[], doses=[])
        buckets = res["time_buckets"]
        
        for name, expected in FALLBACK_NUTRITIONAL_BUCKETS.items():
            self.assertEqual(buckets[name]["peak_rise_mgdl"], expected["peak_rise_mgdl"])
            self.assertEqual(buckets[name]["peak_latency_min"], expected["peak_latency_min"])
            self.assertEqual(buckets[name]["modifier"], expected["modifier"])

    def test_sparse_dataset_one_or_two_readings(self):
        """1 or 2 readings (N_b < 3) -> fallbacks must trigger for under-sampled buckets."""
        tz = pytz.timezone("America/New_York")
        base_dt = tz.localize(datetime(2026, 8, 1, 8, 0)) # Morning
        
        # Add 2 meal doses in Morning (N_b = 2 < 3)
        doses = [
            {"timestamp": base_dt.isoformat(), "meal": 5.0},
            {"timestamp": (base_dt + timedelta(days=1)).isoformat(), "meal": 5.0}
        ]
        readings = [
            {"timestamp": base_dt.isoformat(), "value": 100.0},
            {"timestamp": (base_dt + timedelta(minutes=60)).isoformat(), "value": 160.0},
            {"timestamp": (base_dt + timedelta(days=1)).isoformat(), "value": 100.0},
            {"timestamp": (base_dt + timedelta(days=1, minutes=60)).isoformat(), "value": 160.0},
        ]
        
        res = calculate_nutritional_impact_modifiers(readings=readings, doses=doses)
        buckets = res["time_buckets"]

        # Morning should still trigger fallback because N_b = 2 < 3
        self.assertEqual(buckets["Morning"]["peak_rise_mgdl"], FALLBACK_NUTRITIONAL_BUCKETS["Morning"]["peak_rise_mgdl"])
        self.assertEqual(buckets["Morning"]["modifier"], FALLBACK_NUTRITIONAL_BUCKETS["Morning"]["modifier"])

    def test_threshold_transition_three_readings(self):
        """Exactly 3 readings (N_b = 3) -> empirical calculation overrides fallback."""
        tz = pytz.timezone("America/New_York")
        base_dt = tz.localize(datetime(2026, 8, 1, 8, 0))
        
        doses = []
        readings = []
        for day in range(3): # N_b = 3
            t = base_dt + timedelta(days=day)
            doses.append({"timestamp": t.isoformat(), "meal": 5.0})
            readings.append({"timestamp": t.isoformat(), "value": 100.0})
            readings.append({"timestamp": (t + timedelta(minutes=60)).isoformat(), "value": 170.0})

        res = calculate_nutritional_impact_modifiers(readings=readings, doses=doses)
        buckets = res["time_buckets"]

        # Morning should use empirical peak rise = 70.0 mg/dL instead of fallback (45.2)
        self.assertEqual(buckets["Morning"]["peak_rise_mgdl"], 70.0)

    def test_dense_dataset_50_plus_readings(self):
        """Dense dataset with 50+ readings per bucket -> fast and accurate execution."""
        tz = pytz.timezone("America/New_York")
        naive_start = datetime(2026, 7, 1, 0, 0)
        
        doses = []
        readings = []

        # Create 50 events per bucket (200 total excursions)
        bucket_hours = {"Morning": 8, "Afternoon": 13, "Evening": 18, "Night": 23}
        target_rises = {"Morning": 50.0, "Afternoon": 40.0, "Evening": 45.0, "Night": 60.0}
        
        count = 0
        for i in range(50):
            for bucket_name, hour in bucket_hours.items():
                dt_naive = naive_start + timedelta(days=i, hours=hour, minutes=(count % 15))
                t = tz.localize(dt_naive)
                rise = target_rises[bucket_name]
                doses.append({"timestamp": t.isoformat(), "meal": 8.0, "rapid_acting": 2.0})
                readings.append({"timestamp": t.isoformat(), "value": 100.0})
                readings.append({"timestamp": (t + timedelta(minutes=45)).isoformat(), "value": 100.0 + rise})
                readings.append({"timestamp": (t + timedelta(minutes=120)).isoformat(), "value": 105.0})
                count += 1

        start_time = datetime.now()
        res = calculate_nutritional_impact_modifiers(readings=readings, doses=doses)
        elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000.0
        
        buckets = res["time_buckets"]
        self.assertAlmostEqual(buckets["Morning"]["peak_rise_mgdl"], 50.0, delta=0.5)
        self.assertAlmostEqual(buckets["Afternoon"]["peak_rise_mgdl"], 40.0, delta=0.5)
        self.assertAlmostEqual(buckets["Evening"]["peak_rise_mgdl"], 45.0, delta=0.5)
        self.assertAlmostEqual(buckets["Night"]["peak_rise_mgdl"], 60.0, delta=0.5)

        self.assertAlmostEqual(buckets["Morning"]["modifier"], 1.25, delta=0.02)
        self.assertEqual(buckets["Afternoon"]["modifier"], 1.00)

        # Ensure performance is under 1000ms
        self.assertLess(elapsed_ms, 1000.0, f"Dense calculation took too long: {elapsed_ms:.1f}ms")

    # -------------------------------------------------------------------------
    # Scenario 3: Extreme Excursions & Edge Values
    # -------------------------------------------------------------------------
    def test_extreme_excursions_giant_spike(self):
        """Giant spike (+200 mg/dL rise) -> verified upper clamping (max 2.50)."""
        tz = pytz.timezone("America/New_York")
        base_dt = tz.localize(datetime(2026, 8, 1, 0, 0))
        
        doses = []
        readings = []
        
        # Morning giant spike: 100 -> 300 (+200 mg/dL rise)
        for day in range(3):
            t = base_dt + timedelta(days=day, hours=8)
            doses.append({"timestamp": t.isoformat(), "meal": 15.0})
            readings.append({"timestamp": t.isoformat(), "value": 100.0})
            readings.append({"timestamp": (t + timedelta(minutes=60)).isoformat(), "value": 300.0})

        # Afternoon normal rise: 100 -> 140 (+40 mg/dL rise)
        for day in range(3):
            t = base_dt + timedelta(days=day, hours=13)
            doses.append({"timestamp": t.isoformat(), "meal": 5.0})
            readings.append({"timestamp": t.isoformat(), "value": 100.0})
            readings.append({"timestamp": (t + timedelta(minutes=45)).isoformat(), "value": 140.0})

        res = calculate_nutritional_impact_modifiers(readings=readings, doses=doses)
        buckets = res["time_buckets"]

        # Morning raw modifier = 200 / 40 = 5.0 -> Clamped to 2.50
        self.assertEqual(buckets["Morning"]["peak_rise_mgdl"], 200.0)
        self.assertEqual(buckets["Morning"]["modifier"], 2.50)

    def test_flat_readings_and_negative_rises(self):
        """Flat readings (delta G approx 0) and negative rises -> fallbacks triggered safely."""
        tz = pytz.timezone("America/New_York")
        base_dt = tz.localize(datetime(2026, 8, 1, 0, 0))
        
        doses = []
        readings = []

        # 3 meals with flat glucose (100 -> 100) and dropping glucose (150 -> 90)
        for day in range(3):
            t = base_dt + timedelta(days=day, hours=8)
            doses.append({"timestamp": t.isoformat(), "meal": 5.0})
            readings.append({"timestamp": t.isoformat(), "value": 100.0})
            readings.append({"timestamp": (t + timedelta(minutes=60)).isoformat(), "value": 100.0})

        for day in range(3):
            t = base_dt + timedelta(days=day, hours=13)
            doses.append({"timestamp": t.isoformat(), "meal": 5.0})
            readings.append({"timestamp": t.isoformat(), "value": 150.0})
            readings.append({"timestamp": (t + timedelta(minutes=60)).isoformat(), "value": 90.0})

        res = calculate_nutritional_impact_modifiers(readings=readings, doses=doses)
        buckets = res["time_buckets"]

        # Non-positive rises filtered out -> N_b < 3 for all -> fallbacks used
        self.assertEqual(buckets["Morning"]["modifier"], FALLBACK_NUTRITIONAL_BUCKETS["Morning"]["modifier"])
        self.assertEqual(buckets["Afternoon"]["modifier"], FALLBACK_NUTRITIONAL_BUCKETS["Afternoon"]["modifier"])

    def test_continuous_spike_detection_fallback(self):
        """Continuous spike detection (Strategy 2) when dose records are sparse or absent."""
        tz = pytz.timezone("America/New_York")
        base_dt = tz.localize(datetime(2026, 8, 1, 0, 0))
        
        readings = []
        # Create un-docked continuous spikes (no doses logged) in Morning
        for day in range(5):
            t0 = base_dt + timedelta(days=day, hours=8)
            readings.append({"timestamp": t0.isoformat(), "value": 100.0})
            readings.append({"timestamp": (t0 + timedelta(minutes=15)).isoformat(), "value": 120.0}) # +20 spike
            readings.append({"timestamp": (t0 + timedelta(minutes=60)).isoformat(), "value": 150.0}) # peak +50
            readings.append({"timestamp": (t0 + timedelta(minutes=180)).isoformat(), "value": 110.0})

        res = calculate_nutritional_impact_modifiers(readings=readings, doses=[])
        buckets = res["time_buckets"]

        # Morning should pick up Strategy 2 spikes (N_b = 5 >= 3)
        self.assertEqual(buckets["Morning"]["peak_rise_mgdl"], 50.0)

    # -------------------------------------------------------------------------
    # Scenario 4: Multiple Timezones
    # -------------------------------------------------------------------------
    def test_multiple_timezones_bucket_mapping(self):
        """Verify timezone conversion across UTC, EST, JST, PST, and AEST."""
        # 2026-08-04 12:00:00 UTC
        utc_dt = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
        
        # UTC 12:00 -> Afternoon (11 <= 12 < 17)
        self.assertEqual(get_time_of_day_bucket(utc_dt, "UTC"), "afternoon")

        # America/New_York (EDT, UTC-4): 08:00 -> Morning (4 <= 8 < 11)
        self.assertEqual(get_time_of_day_bucket(utc_dt, "America/New_York"), "morning")

        # Asia/Tokyo (JST, UTC+9): 21:00 -> Evening (17 <= 21 < 22)
        self.assertEqual(get_time_of_day_bucket(utc_dt, "Asia/Tokyo"), "evening")

        # America/Los_Angeles (PDT, UTC-7): 05:00 -> Morning (4 <= 5 < 11)
        self.assertEqual(get_time_of_day_bucket(utc_dt, "America/Los_Angeles"), "morning")

        # Australia/Sydney (AEST, UTC+10): 22:00 -> Night (hour >= 22)
        self.assertEqual(get_time_of_day_bucket(utc_dt, "Australia/Sydney"), "night")

    def test_timezone_str_parameter_in_nutritional_model(self):
        """Verify calculate_nutritional_impact_modifiers honors timezone_str."""
        # Create meal doses at 12:00 UTC
        doses = []
        readings = []
        for day in range(3):
            t = datetime(2026, 8, 1 + day, 12, 0, 0, tzinfo=timezone.utc)
            doses.append({"timestamp": t.isoformat(), "meal": 5.0})
            readings.append({"timestamp": t.isoformat(), "value": 100.0})
            readings.append({"timestamp": (t + timedelta(minutes=60)).isoformat(), "value": 160.0})

        # Under UTC timezone: 12:00 UTC is Afternoon
        res_utc = calculate_nutritional_impact_modifiers(readings=readings, doses=doses, timezone_str="UTC")
        self.assertEqual(res_utc["time_buckets"]["Afternoon"]["peak_rise_mgdl"], 60.0)

        # Under America/New_York timezone: 12:00 UTC is 08:00 EDT -> Morning
        res_ny = calculate_nutritional_impact_modifiers(readings=readings, doses=doses, timezone_str="America/New_York")
        self.assertEqual(res_ny["time_buckets"]["Morning"]["peak_rise_mgdl"], 60.0)

    # -------------------------------------------------------------------------
    # Scenario 5: Schema Contract & Input Resilience
    # -------------------------------------------------------------------------
    def test_schema_contract_compliance(self):
        """Verify strict adherence to PROJECT.md response schema."""
        res = calculate_nutritional_impact_modifiers(readings=[], doses=[])
        
        self.assertIn("time_buckets", res)
        self.assertIn("recommendations", res)
        self.assertIsInstance(res["recommendations"], list)
        self.assertGreater(len(res["recommendations"]), 0)

        expected_buckets = ["Morning", "Afternoon", "Evening", "Night"]
        for b_name in expected_buckets:
            self.assertIn(b_name, res["time_buckets"])
            bucket_data = res["time_buckets"][b_name]
            self.assertIn("peak_rise_mgdl", bucket_data)
            self.assertIn("peak_latency_min", bucket_data)
            self.assertIn("modifier", bucket_data)
            
            self.assertIsInstance(bucket_data["peak_rise_mgdl"], float)
            self.assertIsInstance(bucket_data["peak_latency_min"], int)
            self.assertIsInstance(bucket_data["modifier"], float)

    def test_unsorted_and_dirty_inputs(self):
        """Verify resilience against unsorted timestamps, strings, and missing fields."""
        tz = pytz.timezone("UTC")
        
        # Unsorted doses and readings at 13:00 UTC (Afternoon under UTC)
        doses = [
            {"timestamp": "2026-08-03T13:00:00Z", "meal": "5.0"}, # string float
            {"timestamp": "2026-08-01T13:00:00Z", "meal": 5.0, "rapid_acting": None}, # missing rapid
            {"timestamp": "2026-08-02T13:00:00Z", "meal": 5.0},
        ]
        readings = [
            {"timestamp": "2026-08-03T13:45:00Z", "value": "150.0"}, # string float
            {"timestamp": "2026-08-01T13:00:00Z", "value": 100.0},
            {"timestamp": "2026-08-02T13:45:00Z", "value": 150.0},
            {"timestamp": "2026-08-01T13:45:00Z", "value": 150.0},
            {"timestamp": "2026-08-03T13:00:00Z", "value": 100.0},
            {"timestamp": "2026-08-02T13:00:00Z", "value": 100.0},
        ]

        res = calculate_nutritional_impact_modifiers(readings=readings, doses=doses, timezone_str="UTC")
        buckets = res["time_buckets"]
        
        # Afternoon empirical rise should equal 50.0 mg/dL despite unsorted input
        self.assertEqual(buckets["Afternoon"]["peak_rise_mgdl"], 50.0)

if __name__ == "__main__":
    unittest.main(verbosity=2)
