import sys
import unittest
from datetime import datetime, timezone, timedelta
import pytz

from app import app
from ml_heuristics import get_time_of_day_bucket, calculate_nutritional_impact_modifiers, get_nutritional_impact

class TestNutritionalImpact(unittest.TestCase):

    def test_get_time_of_day_bucket(self):
        tz = pytz.timezone("America/New_York")
        
        # Morning: 04:00 - 11:00
        dt_morning = tz.localize(datetime(2026, 8, 4, 7, 30))
        self.assertEqual(get_time_of_day_bucket(dt_morning), "morning")
        
        # Afternoon: 11:00 - 17:00
        dt_afternoon = tz.localize(datetime(2026, 8, 4, 14, 0))
        self.assertEqual(get_time_of_day_bucket(dt_afternoon), "afternoon")
        
        # Evening: 17:00 - 22:00
        dt_evening = tz.localize(datetime(2026, 8, 4, 18, 30))
        self.assertEqual(get_time_of_day_bucket(dt_evening), "evening")
        
        # Night: 22:00 - 04:00
        dt_night1 = tz.localize(datetime(2026, 8, 4, 23, 15))
        dt_night2 = tz.localize(datetime(2026, 8, 4, 2, 45))
        self.assertEqual(get_time_of_day_bucket(dt_night1), "night")
        self.assertEqual(get_time_of_day_bucket(dt_night2), "night")

    def test_calculate_nutritional_impact_fallbacks(self):
        # When sparse data (N < 3 per bucket), fallbacks should trigger
        res = calculate_nutritional_impact_modifiers(readings=[], doses=[])
        
        self.assertIn("time_buckets", res)
        self.assertIn("recommendations", res)
        
        buckets = res["time_buckets"]
        self.assertEqual(buckets["Morning"]["modifier"], 1.25)
        self.assertEqual(buckets["Morning"]["peak_rise_mgdl"], 45.2)
        self.assertEqual(buckets["Morning"]["peak_latency_min"], 55)
        
        self.assertEqual(buckets["Afternoon"]["modifier"], 1.00)
        self.assertEqual(buckets["Afternoon"]["peak_rise_mgdl"], 35.0)
        self.assertEqual(buckets["Afternoon"]["peak_latency_min"], 45)
        
        self.assertEqual(buckets["Evening"]["modifier"], 1.10)
        self.assertEqual(buckets["Evening"]["peak_rise_mgdl"], 40.1)
        self.assertEqual(buckets["Evening"]["peak_latency_min"], 50)
        
        self.assertEqual(buckets["Night"]["modifier"], 1.40)
        self.assertEqual(buckets["Night"]["peak_rise_mgdl"], 52.8)
        self.assertEqual(buckets["Night"]["peak_latency_min"], 75)
        
        self.assertGreater(len(res["recommendations"]), 0)

    def test_calculate_nutritional_impact_excursions(self):
        tz = pytz.timezone("America/New_York")
        readings = []
        doses = []
        base_dt = tz.localize(datetime(2026, 8, 1, 0, 0))
        
        def add_excursion(start_dt, rise_amount, latency_mins):
            doses.append({
                "timestamp": start_dt.isoformat(),
                "meal": 5.0,
                "rapid_acting": 5.0
            })
            readings.append({"timestamp": start_dt.isoformat(), "value": 100.0})
            peak_dt = start_dt + timedelta(minutes=latency_mins)
            readings.append({"timestamp": peak_dt.isoformat(), "value": 100.0 + rise_amount})
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
        
        self.assertEqual(buckets["Afternoon"]["peak_rise_mgdl"], 40.0)
        self.assertEqual(buckets["Afternoon"]["modifier"], 1.00)
        
        self.assertEqual(buckets["Morning"]["peak_rise_mgdl"], 60.0)
        self.assertEqual(buckets["Morning"]["modifier"], 1.50)
        
        self.assertEqual(buckets["Evening"]["peak_rise_mgdl"], 50.0)
        self.assertEqual(buckets["Evening"]["modifier"], 1.25)
        
        self.assertEqual(buckets["Night"]["peak_rise_mgdl"], 80.0)
        self.assertEqual(buckets["Night"]["modifier"], 2.00)

if __name__ == '__main__':
    unittest.main()
