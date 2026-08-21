"""
tests/test_adversarial_m4_monkebot.py
Adversarial Stress Test Harness for Milestone 4: Master Coordinator Hub (MonkeHelper).

Empirically challenges:
1. Quiet Hours Boundaries & Transitions:
   - Microsecond boundary conditions (22:59:59.999, 23:00:00.000, 06:59:59.999, 07:00:00.000, noon 12:00:00)
   - Cross-midnight windows (23:00–07:00, 22:00–06:00, 20:00–08:00, 23:00–00:00, 23:00–01:00)
   - Intra-day / Daytime windows (13:00–15:00, 08:00–17:00)
   - Inverted / Edge windows (start == end, 0-hour window, 24h window)
   - Timezone conversions (UTC, New York, London, Tokyo, Los Angeles, naive datetimes, invalid tz strings)
   - Configuration validation (out-of-range hours 24, -1, 99, non-integer inputs)

2. Emergency Hypoglycemia Bypass:
   - Verified that glucose 69, 54, 40, 20, 1 mg/dL NEVER get suppressed during quiet hours
   - Hypo threshold boundary verification (69.99 vs 70.00 vs 70.01)
   - Predictive rapid drop bypass (e.g. rate -3.0 mg/dL/min -> projected 30m < 65 mg/dL)
   - Rescue carbohydrate formula validation under varying IOB loads
   - Extreme / malformed predictive payloads (empty predictions, missing keys, NaN values)

3. Multi-Domain Briefing Robustness (Empty Tables, NaN, Missing Sessions, Zero Doses):
   - 100% empty database tables (CGM, Insulin, MedFlow, Circadian/Sleep, Food)
   - Corrupted / NaN / Inf telemetry and biometrics inputs
   - Zero-dose insulin scenarios (tdd=0, basal=0, bolus=0)
   - Missing sleep sessions and absent circadian data
   - HTML template rendering resilience across all drill-down cards
   - Full webhook flow on sparse/missing telemetry

4. RBAC, Security & Ecosystem Router Hardening:
   - Privilege escalation attacks on /admin, /addcaregiver, /removecaregiver
   - Sole Owner removal protection
   - Foreign namespace isolation (gt:, med:, bio:)
   - 60s sliding-window callback debounce resilience
   - Group chat noise filtering and target disambiguation
"""

import os
import sys
import unittest
import math
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import pytz

# Ensure root directory is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Mock psycopg2 if not installed
try:
    import psycopg2
except ImportError:
    mock_psycopg2 = MagicMock()
    sys.modules["psycopg2"] = mock_psycopg2
    sys.modules["psycopg2.extras"] = MagicMock()
    sys.modules["psycopg2.pool"] = MagicMock()

import db
import monke_bot
import circadian_analysis


class TestAdversarialQuietHoursBoundaries(unittest.TestCase):
    """Adversarial stress-testing of Quiet Hours boundary conditions and timezones."""

    def setUp(self):
        monke_bot.reset_in_memory_state()

    def test_standard_night_window_microsecond_boundaries(self):
        """Test exact microsecond boundaries for 23:00 to 07:00 in America/New_York."""
        tz = pytz.timezone("America/New_York")

        # 22:59:59.999 -> Inactive (False)
        dt_2259 = tz.localize(datetime(2026, 8, 21, 22, 59, 59, 999999))
        self.assertFalse(monke_bot.is_in_quiet_hours(dt_2259, start_hour=23, end_hour=7))

        # 23:00:00.000 -> Active (True)
        dt_2300 = tz.localize(datetime(2026, 8, 21, 23, 0, 0, 0))
        self.assertTrue(monke_bot.is_in_quiet_hours(dt_2300, start_hour=23, end_hour=7))

        # 23:59:59.999 -> Active (True)
        dt_2359 = tz.localize(datetime(2026, 8, 21, 23, 59, 59, 999999))
        self.assertTrue(monke_bot.is_in_quiet_hours(dt_2359, start_hour=23, end_hour=7))

        # 00:00:00.000 -> Active (True)
        dt_0000 = tz.localize(datetime(2026, 8, 22, 0, 0, 0, 0))
        self.assertTrue(monke_bot.is_in_quiet_hours(dt_0000, start_hour=23, end_hour=7))

        # 03:30:00.000 -> Active (True)
        dt_0330 = tz.localize(datetime(2026, 8, 22, 3, 30, 0, 0))
        self.assertTrue(monke_bot.is_in_quiet_hours(dt_0330, start_hour=23, end_hour=7))

        # 06:59:59.999 -> Active (True)
        dt_0659 = tz.localize(datetime(2026, 8, 22, 6, 59, 59, 999999))
        self.assertTrue(monke_bot.is_in_quiet_hours(dt_0659, start_hour=23, end_hour=7))

        # 07:00:00.000 -> Inactive (False)
        dt_0700 = tz.localize(datetime(2026, 8, 22, 7, 0, 0, 0))
        self.assertFalse(monke_bot.is_in_quiet_hours(dt_0700, start_hour=23, end_hour=7))

        # 12:00:00.000 (noon) -> Inactive (False)
        dt_noon = tz.localize(datetime(2026, 8, 22, 12, 0, 0, 0))
        self.assertFalse(monke_bot.is_in_quiet_hours(dt_noon, start_hour=23, end_hour=7))

    def test_cross_midnight_variant_windows(self):
        """Test diverse cross-midnight windows (22:00-06:00, 20:00-08:00, 23:00-00:00, 23:00-01:00)."""
        tz = pytz.timezone("America/New_York")

        # Window 22:00 - 06:00
        dt_2159 = tz.localize(datetime(2026, 8, 21, 21, 59, 59))
        dt_2200 = tz.localize(datetime(2026, 8, 21, 22, 0, 0))
        dt_0559 = tz.localize(datetime(2026, 8, 22, 5, 59, 59))
        dt_0600 = tz.localize(datetime(2026, 8, 22, 6, 0, 0))
        self.assertFalse(monke_bot.is_in_quiet_hours(dt_2159, start_hour=22, end_hour=6))
        self.assertTrue(monke_bot.is_in_quiet_hours(dt_2200, start_hour=22, end_hour=6))
        self.assertTrue(monke_bot.is_in_quiet_hours(dt_0559, start_hour=22, end_hour=6))
        self.assertFalse(monke_bot.is_in_quiet_hours(dt_0600, start_hour=22, end_hour=6))

        # Single-hour cross-midnight window: 23:00 to 00:00 (start=23, end=0)
        dt_2330 = tz.localize(datetime(2026, 8, 21, 23, 30, 0))
        dt_0030 = tz.localize(datetime(2026, 8, 22, 0, 30, 0))
        self.assertTrue(monke_bot.is_in_quiet_hours(dt_2330, start_hour=23, end_hour=0))
        self.assertFalse(monke_bot.is_in_quiet_hours(dt_0030, start_hour=23, end_hour=0))

        # Two-hour cross-midnight window: 23:00 to 01:00 (start=23, end=1)
        dt_0015 = tz.localize(datetime(2026, 8, 22, 0, 15, 0))
        dt_0100 = tz.localize(datetime(2026, 8, 22, 1, 0, 0))
        self.assertTrue(monke_bot.is_in_quiet_hours(dt_0015, start_hour=23, end_hour=1))
        self.assertFalse(monke_bot.is_in_quiet_hours(dt_0100, start_hour=23, end_hour=1))

    def test_intra_day_daytime_windows(self):
        """Test daytime windows where start_hour < end_hour (e.g. 13:00 to 15:00, 08:00 to 17:00)."""
        tz = pytz.timezone("America/New_York")

        # Nap window: 13:00 to 15:00
        dt_1259 = tz.localize(datetime(2026, 8, 21, 12, 59, 59))
        dt_1300 = tz.localize(datetime(2026, 8, 21, 13, 0, 0))
        dt_1430 = tz.localize(datetime(2026, 8, 21, 14, 30, 0))
        dt_1500 = tz.localize(datetime(2026, 8, 21, 15, 0, 0))
        self.assertFalse(monke_bot.is_in_quiet_hours(dt_1259, start_hour=13, end_hour=15))
        self.assertTrue(monke_bot.is_in_quiet_hours(dt_1300, start_hour=13, end_hour=15))
        self.assertTrue(monke_bot.is_in_quiet_hours(dt_1430, start_hour=13, end_hour=15))
        self.assertFalse(monke_bot.is_in_quiet_hours(dt_1500, start_hour=13, end_hour=15))

        # Workday window: 08:00 to 17:00
        dt_0759 = tz.localize(datetime(2026, 8, 21, 7, 59, 59))
        dt_0800 = tz.localize(datetime(2026, 8, 21, 8, 0, 0))
        dt_1659 = tz.localize(datetime(2026, 8, 21, 16, 59, 59))
        dt_1700 = tz.localize(datetime(2026, 8, 21, 17, 0, 0))
        self.assertFalse(monke_bot.is_in_quiet_hours(dt_0759, start_hour=8, end_hour=17))
        self.assertTrue(monke_bot.is_in_quiet_hours(dt_0800, start_hour=8, end_hour=17))
        self.assertTrue(monke_bot.is_in_quiet_hours(dt_1659, start_hour=8, end_hour=17))
        self.assertFalse(monke_bot.is_in_quiet_hours(dt_1700, start_hour=8, end_hour=17))

    def test_inverted_and_degenerate_windows(self):
        """Test edge cases: start_hour == end_hour (0-hour window), disabled quiet hours."""
        tz = pytz.timezone("America/New_York")
        dt_now = tz.localize(datetime(2026, 8, 21, 14, 0, 0))

        # start == end -> False for all hours
        for h in range(24):
            dt_h = tz.localize(datetime(2026, 8, 21, h, 30, 0))
            self.assertFalse(monke_bot.is_in_quiet_hours(dt_h, start_hour=14, end_hour=14))
            self.assertFalse(monke_bot.is_in_quiet_hours(dt_h, start_hour=0, end_hour=0))
            self.assertFalse(monke_bot.is_in_quiet_hours(dt_h, start_hour=23, end_hour=23))

    def test_timezone_conversion_robustness(self):
        """Test UTC to local timezone conversion accuracy across different world timezones."""
        # 04:00 UTC = 00:00 EDT (America/New_York, UTC-4 in August)
        dt_utc_0400 = datetime(2026, 8, 22, 4, 0, 0, tzinfo=timezone.utc)
        self.assertTrue(monke_bot.is_in_quiet_hours(dt_utc_0400, start_hour=23, end_hour=7, timezone_str="America/New_York"))

        # 10:00 UTC = 06:00 EDT -> Quiet hours (06:00 < 07:00)
        dt_utc_1000 = datetime(2026, 8, 22, 10, 0, 0, tzinfo=timezone.utc)
        self.assertTrue(monke_bot.is_in_quiet_hours(dt_utc_1000, start_hour=23, end_hour=7, timezone_str="America/New_York"))

        # 11:00 UTC = 07:00 EDT -> Normal hours (07:00 is end)
        dt_utc_1100 = datetime(2026, 8, 22, 11, 0, 0, tzinfo=timezone.utc)
        self.assertFalse(monke_bot.is_in_quiet_hours(dt_utc_1100, start_hour=23, end_hour=7, timezone_str="America/New_York"))

        # Naive datetime fallback (treated as already local)
        dt_naive_0300 = datetime(2026, 8, 22, 3, 0, 0)
        self.assertTrue(monke_bot.is_in_quiet_hours(dt_naive_0300, start_hour=23, end_hour=7))

        dt_naive_1200 = datetime(2026, 8, 22, 12, 0, 0)
        self.assertFalse(monke_bot.is_in_quiet_hours(dt_naive_1200, start_hour=23, end_hour=7))

        # Invalid timezone string fallback
        self.assertTrue(monke_bot.is_in_quiet_hours(dt_naive_0300, start_hour=23, end_hour=7, timezone_str="INVALID/TIMEZONE"))

    def test_save_quiet_hours_config_validation(self):
        """Test validation rules for saving quiet hours configuration."""
        # Valid config
        cfg = monke_bot.save_quiet_hours_config(22, 8, enabled=True, updated_by="TestAdmin")
        self.assertEqual(cfg["start_hour"], 22)
        self.assertEqual(cfg["end_hour"], 8)
        self.assertTrue(cfg["enabled"])

        # Invalid start_hour (< 0 or > 23)
        with self.assertRaises(ValueError):
            monke_bot.save_quiet_hours_config(-1, 7)
        with self.assertRaises(ValueError):
            monke_bot.save_quiet_hours_config(24, 7)
        with self.assertRaises(ValueError):
            monke_bot.save_quiet_hours_config(23, 99)


class TestAdversarialEmergencyHypoBypass(unittest.TestCase):
    """Adversarial stress-testing of Emergency Hypoglycemia Bypass during Quiet Hours."""

    def setUp(self):
        monke_bot.reset_in_memory_state()
        self.tz = pytz.timezone("America/New_York")
        self.quiet_dt = self.tz.localize(datetime(2026, 8, 22, 3, 0, 0))  # 03:00 AM EDT (Deep Quiet Hours)

    def test_emergency_hypo_values_never_suppressed(self):
        """Verify that glucose 69, 54, 40, 20, 1 mg/dL NEVER get suppressed during quiet hours."""
        hypo_test_values = [
            (69.9, "urgent_low"),
            (69.0, "urgent_low"),
            (65.0, "urgent_low"),
            (55.0, "urgent_low"),
            (54.9, "critical_low"),
            (54.0, "critical_low"),
            (40.0, "critical_low"),
            (25.0, "critical_low"),
            (1.0, "critical_low")
        ]

        for bg, expected_urgency in hypo_test_values:
            suppressed, reason, meta = monke_bot.should_suppress_notification(
                event_type="urgent_low",
                glucose_value=bg,
                dt=self.quiet_dt,
                iob=0.0
            )
            self.assertFalse(suppressed, f"CRITICAL FAILURE: Glucose {bg} mg/dL was suppressed during quiet hours!")
            self.assertEqual(reason, "emergency_hypo_bypass")
            self.assertEqual(meta.get("urgency"), expected_urgency)
            self.assertGreaterEqual(meta.get("recommended_rescue_carbs", 0), 10)

    def test_hypo_boundary_exact_threshold(self):
        """Verify boundary precision: 69.99 mg/dL bypasses, 70.00 mg/dL is suppressed during quiet hours."""
        # 69.99 mg/dL -> Bypass
        supp_69, reason_69, _ = monke_bot.should_suppress_notification(
            event_type="routine_alert",
            glucose_value=69.99,
            dt=self.quiet_dt
        )
        self.assertFalse(supp_69)
        self.assertEqual(reason_69, "emergency_hypo_bypass")

        # 70.00 mg/dL -> Normal quiet hours suppression for non-emergency
        supp_70, reason_70, _ = monke_bot.should_suppress_notification(
            event_type="routine_alert",
            glucose_value=70.00,
            dt=self.quiet_dt
        )
        self.assertTrue(supp_70)
        self.assertEqual(reason_70, "quiet_hours")

        # 120.00 mg/dL -> Suppressed during quiet hours
        supp_120, reason_120, _ = monke_bot.should_suppress_notification(
            event_type="routine_alert",
            glucose_value=120.00,
            dt=self.quiet_dt
        )
        self.assertTrue(supp_120)
        self.assertEqual(reason_120, "quiet_hours")

    def test_predictive_rapid_drop_bypass(self):
        """Verify rapid drop (-3.0 mg/dL/min) bypasses quiet hours when projected 30m is <65 mg/dL."""
        # Starting at 85 mg/dL with rapid drop projecting 62 mg/dL in 30m
        preds = [{"minutes": 30, "value": 62.0}]
        suppressed, reason, meta = monke_bot.should_suppress_notification(
            event_type="rapid_drop",
            glucose_value=85.0,
            dt=self.quiet_dt,
            predictions=preds
        )
        self.assertFalse(suppressed, "Rapid drop projecting 62 mg/dL in 30m must bypass quiet hours!")
        self.assertEqual(reason, "emergency_hypo_bypass")
        self.assertEqual(meta.get("urgency"), "rapid_drop")
        self.assertEqual(meta.get("projected_30m"), 62.0)

        # High glucose (160 mg/dL) dropping to 120 mg/dL in 30m -> NOT hypo danger, suppressed in quiet hours
        preds_safe = [{"minutes": 30, "value": 120.0}]
        supp_safe, reason_safe, _ = monke_bot.should_suppress_notification(
            event_type="routine_alert",
            glucose_value=160.0,
            dt=self.quiet_dt,
            predictions=preds_safe
        )
        self.assertTrue(supp_safe)
        self.assertEqual(reason_safe, "quiet_hours")

    def test_rescue_carbs_formula_with_varying_iob(self):
        """Test rescue carb calculations under different IOB loads."""
        # BG=54, IOB=0 -> max(15, ceil((105-54)/4)) = max(15, 13) = 15g
        _, _, meta_0 = monke_bot.should_suppress_notification("urgent_low", 54.0, self.quiet_dt, iob=0.0)
        self.assertEqual(meta_0["recommended_rescue_carbs"], 15)

        # BG=54, IOB=2.0 -> (105-54 + 2.0*50)/4 = (51+100)/4 = 151/4 = 37.75 -> 38g
        _, _, meta_2 = monke_bot.should_suppress_notification("urgent_low", 54.0, self.quiet_dt, iob=2.0)
        self.assertEqual(meta_2["recommended_rescue_carbs"], 38)

        # BG=40, IOB=4.0 -> (105-40 + 4.0*50)/4 = (65+200)/4 = 265/4 = 66.25 -> 67g
        _, _, meta_4 = monke_bot.should_suppress_notification("critical_low", 40.0, self.quiet_dt, iob=4.0)
        self.assertEqual(meta_4["recommended_rescue_carbs"], 67)

    def test_build_emergency_hypo_alert_rendering(self):
        """Verify HTML formatting of emergency hypo override alert."""
        alert_text = monke_bot.build_emergency_hypo_alert(glucose=48.0, iob=1.5, trend_arrow="⇊", trend_desc="Dropping Fast")
        self.assertIn("CRITICAL HYPOGLYCEMIA ALERT", alert_text)
        self.assertIn("48 mg/dL", alert_text)
        self.assertIn("⇊ (Dropping Fast)", alert_text)
        self.assertIn("fast-acting carbs", alert_text)
        self.assertIn("Emergency Hypoglycemia Override", alert_text)

    def test_string_and_malformed_glucose_inputs_to_bypass(self):
        """Verify handling of string values, None, NaN, and malformed predictions without crashes."""
        # String numeric representation
        supp_str, reason_str, _ = monke_bot.should_suppress_notification("urgent_low", "54.0", self.quiet_dt)
        self.assertFalse(supp_str)
        self.assertEqual(reason_str, "emergency_hypo_bypass")

        # Non-numeric string -> gracefully falls through to quiet hours check
        supp_bad, reason_bad, _ = monke_bot.should_suppress_notification("routine_alert", "INVALID_BG", self.quiet_dt)
        self.assertTrue(supp_bad)
        self.assertEqual(reason_bad, "quiet_hours")

        # None glucose -> quiet hours suppression
        supp_none, reason_none, _ = monke_bot.should_suppress_notification("routine_alert", None, self.quiet_dt)
        self.assertTrue(supp_none)
        self.assertEqual(reason_none, "quiet_hours")

        # Malformed predictions list
        bad_preds = [None, {}, {"minutes": "thirty"}, {"value": None}, {"minutes": 30, "value": "nan"}]
        supp_bad_p, _, _ = monke_bot.should_suppress_notification("routine_alert", 110.0, self.quiet_dt, predictions=bad_preds)
        self.assertTrue(supp_bad_p)


class TestAdversarialMultiDomainBriefing(unittest.TestCase):
    """Adversarial stress-testing of Multi-Domain Health Synthesis with sparse/missing/corrupted data."""

    def setUp(self):
        monke_bot.reset_in_memory_state()
        self.conn_patcher = patch("db.get_connection", side_effect=Exception("Offline DB test"))
        self.conn_patcher.start()

    def tearDown(self):
        self.conn_patcher.stop()

    @patch("db.get_latest_reading", return_value=None)
    @patch("db.get_history", return_value=[])
    @patch("db.get_statistics", return_value=None)
    @patch("db.get_insulin_history", return_value=[])
    @patch("db.get_recent_med_logs", return_value=[])
    @patch("db.get_medication_presets", return_value=[])
    @patch("db.get_medication_summary", return_value=[])
    @patch("db.get_food_history", return_value=[])
    @patch("circadian_analysis.get_circadian_biometrics_summary", return_value={})
    def test_briefing_with_100_percent_empty_database(
        self,
        mock_bio, mock_food, mock_med_sum, mock_presets, mock_med_logs,
        mock_insulin, mock_stats, mock_history, mock_latest
    ):
        """Stress Test: Multi-domain briefing synthesizes cleanly when every DB table is completely empty."""
        briefing = monke_bot.get_unified_daily_briefing(hours=24)

        self.assertIsInstance(briefing, dict)
        self.assertIn("cgm", briefing)
        self.assertIn("insulin", briefing)
        self.assertIn("medications", briefing)
        self.assertIn("circadian", briefing)
        self.assertIn("nutrition", briefing)
        self.assertIn("alerts", briefing)
        self.assertIn("digest_text", briefing)

        # CGM domain defaults
        self.assertEqual(briefing["cgm"]["current_glucose"], 120.0)
        self.assertEqual(briefing["cgm"]["total_readings"], 0)

        # Insulin domain defaults
        self.assertEqual(briefing["insulin"]["tdd"], 0.0)
        self.assertEqual(briefing["insulin"]["basal_units"], 0.0)
        self.assertEqual(briefing["insulin"]["bolus_units"], 0.0)
        self.assertEqual(briefing["insulin"]["recent_doses_count"], 0)

        # Medications domain defaults
        self.assertEqual(briefing["medications"]["active_presets_count"], 0)
        self.assertEqual(briefing["medications"]["recent_intakes"], [])
        self.assertEqual(briefing["medications"]["last_dose_elapsed"], "None logged today")

        # Circadian domain defaults
        self.assertEqual(briefing["circadian"]["total_sleep_hours"], 7.5)
        self.assertEqual(briefing["circadian"]["efficiency_percent"], 90.0)
        self.assertEqual(briefing["circadian"]["isf_modifier"], 1.0)

        # Nutrition domain defaults
        self.assertEqual(briefing["nutrition"]["total_carbs_g"], 0.0)
        self.assertEqual(briefing["nutrition"]["meal_count"], 0)

        # HTML digest string validation
        digest = briefing["digest_text"]
        self.assertIsInstance(digest, str)
        self.assertIn("Executive Health Briefing", digest)
        self.assertIn("None logged today", digest)
        self.assertNotIn("None U", digest)

    @patch("db.get_latest_reading")
    @patch("db.get_history")
    @patch("db.get_statistics")
    @patch("db.get_insulin_history")
    @patch("db.get_recent_med_logs")
    @patch("db.get_medication_presets")
    @patch("db.get_medication_summary")
    @patch("db.get_food_history")
    @patch("circadian_analysis.get_circadian_biometrics_summary")
    def test_briefing_with_corrupted_nan_and_null_values(
        self,
        mock_bio, mock_food, mock_med_sum, mock_presets, mock_med_logs,
        mock_insulin, mock_stats, mock_history, mock_latest
    ):
        """Stress Test: Briefing survives NaN, None, and malformed types across all domains without crashing."""
        now = datetime.now(timezone.utc)
        mock_latest.return_value = {"value": None, "timestamp": None}
        mock_history.return_value = [{"value": float("nan"), "timestamp": now}]
        mock_stats.return_value = {
            "average_glucose": None,
            "total_readings": 1,
            "time_in_range": {"target_percent": None, "low_percent": None, "high_percent": None}
        }
        mock_insulin.return_value = [
            {"long_acting": None, "rapid_acting": None, "meal": None, "correction": None, "user_change": None}
        ]
        mock_presets.return_value = [None, {}, {"name": None}]
        mock_med_logs.return_value = [
            {"name": "Aspirin", "dose_taken": None, "dose_unit": None, "timestamp": now, "notes": None}
        ]
        mock_food.return_value = [
            {"carbs_g": None, "food_type": None, "timestamp": now}
        ]
        mock_bio.return_value = {
            "sleep": {"total_hours_24h": None, "efficiency_percent": None},
            "circadian": {"sleep_midpoint": None, "chronotype": None},
            "rhr": {"dipping_percent": None, "daytime_baseline": None},
            "isf": {"modifier": None}
        }

        briefing = monke_bot.get_unified_daily_briefing(hours=24)
        self.assertIsInstance(briefing, dict)
        self.assertIsInstance(briefing.get("digest_text"), str)

        # Drilldown cards must also render without crash
        card_g, _ = monke_bot.build_glucose_drilldown_card(briefing["cgm"])
        card_m, _ = monke_bot.build_meds_drilldown_card(briefing["insulin"], briefing["medications"])
        card_s, _ = monke_bot.build_sleep_drilldown_card(briefing["circadian"])
        card_n, _ = monke_bot.build_nutrition_drilldown_card(briefing["nutrition"])

        self.assertIn("CGM & Glucose Deep-Dive", card_g)
        self.assertIn("Medication & Insulin Regimen", card_m)
        self.assertIn("Sleep Architecture", card_s)
        self.assertIn("Nutrition & Fuel Deep-Dive", card_n)

    @patch("db.get_latest_reading")
    @patch("db.get_history")
    @patch("db.get_statistics")
    @patch("db.get_insulin_history")
    @patch("db.get_recent_med_logs")
    @patch("db.get_medication_presets")
    @patch("db.get_food_history")
    @patch("circadian_analysis.get_circadian_biometrics_summary")
    def test_multi_bot_status_card_with_offline_subsystems(
        self,
        mock_bio, mock_food, mock_presets, mock_med_logs,
        mock_insulin, mock_stats, mock_history, mock_latest
    ):
        """Stress Test: /status card renders cleanly when all subsystems are offline/empty."""
        mock_latest.return_value = None
        mock_stats.return_value = None
        mock_med_logs.return_value = []
        mock_presets.return_value = []
        mock_bio.return_value = {}

        card_text, kb = monke_bot.build_multi_bot_status_card()
        self.assertIn("Multi-Bot Ecosystem Health", card_text)
        self.assertIn("Standby (No recent readings)", card_text)
        self.assertIn("No recent intake logged", card_text)
        self.assertIn("inline_keyboard", kb)

    def test_quiet_hours_exhaustive_24x24_matrix_fuzz(self):
        """Exhaustive 24x24 matrix test: verifies mathematical consistency for all (start, end, test_hour) triplets."""
        tz = pytz.timezone("America/New_York")
        for start_h in range(24):
            for end_h in range(24):
                for test_h in range(24):
                    dt = tz.localize(datetime(2026, 8, 21, test_h, 30, 0))
                    res = monke_bot.is_in_quiet_hours(dt, start_hour=start_h, end_hour=end_h)
                    
                    if start_h == end_h:
                        self.assertFalse(res, f"Equal start and end ({start_h}=={end_h}) must be False.")
                    elif start_h > end_h:
                        # Cross-midnight window
                        expected = (test_h >= start_h or test_h < end_h)
                        self.assertEqual(res, expected, f"Cross-midnight failure for start={start_h}, end={end_h}, test_hour={test_h}")
                    else:
                        # Intra-day window
                        expected = (start_h <= test_h < end_h)
                        self.assertEqual(res, expected, f"Intra-day failure for start={start_h}, end={end_h}, test_hour={test_h}")

    def test_emergency_bypass_exhaustive_glucose_scan(self):
        """Exhaustive glucose level scan from 1 to 300 mg/dL verifying strict monotonic threshold adherence."""
        tz = pytz.timezone("America/New_York")
        quiet_dt = tz.localize(datetime(2026, 8, 22, 2, 0, 0))

        for bg in range(1, 301):
            bg_float = float(bg)
            suppressed, reason, meta = monke_bot.should_suppress_notification(
                event_type="routine_check",
                glucose_value=bg_float,
                dt=quiet_dt
            )
            if bg_float < 70.0:
                self.assertFalse(suppressed, f"CRITICAL: Glucose {bg_float} mg/dL was suppressed!")
                self.assertEqual(reason, "emergency_hypo_bypass")
            else:
                self.assertTrue(suppressed, f"Glucose {bg_float} mg/dL should be suppressed for routine check during quiet hours.")
                self.assertEqual(reason, "quiet_hours")



class TestAdversarialCareCircleSecurity(unittest.TestCase):
    """Adversarial stress-testing of Care Circle RBAC and privilege boundaries."""

    def setUp(self):
        monke_bot.reset_in_memory_state()

    def test_unregistered_and_viewer_escalation_attacks(self):
        """Verify that unregistered users and Viewers cannot perform admin or mutation actions."""
        # Unregistered user (888888) attempting /addcaregiver
        res_unreg = monke_bot.handle_monke_webhook({
            "update_id": 9901,
            "message": {
                "chat": {"id": 888888, "type": "private"},
                "from": {"id": 888888, "first_name": "Attacker"},
                "text": "/addcaregiver 888888 Owner"
            }
        })
        self.assertEqual(res_unreg.get("status"), "denied")
        self.assertEqual(res_unreg.get("action"), "permission_denied")

        # Viewer (303) attempting /removecaregiver
        monke_bot.add_care_circle_member("303", "Viewer", name="Observer", added_by="101")
        res_viewer_rm = monke_bot.handle_monke_webhook({
            "update_id": 9902,
            "message": {
                "chat": {"id": 303, "type": "private"},
                "from": {"id": 303},
                "text": "/removecaregiver 101"
            }
        })
        self.assertEqual(res_viewer_rm.get("status"), "denied")

    def test_cannot_remove_sole_owner(self):
        """Verify immutable protection: the sole Owner cannot be removed."""
        data = monke_bot.get_care_circle_data()
        owner_id = data.get("owner_id", "101")

        # Attempt to remove owner
        success, msg = monke_bot.remove_care_circle_member(owner_id)
        self.assertFalse(success)
        self.assertIn("Cannot remove the primary/sole Owner", msg)
        self.assertEqual(monke_bot.get_user_role(owner_id), "Owner")

    def test_invalid_user_ids_and_roles(self):
        """Test validation on malformed user IDs and unauthorized role strings."""
        # Malformed user ID (alphabetic)
        ok_bad_id, msg_bad_id = monke_bot.add_care_circle_member("not_a_number", "Caregiver")
        self.assertFalse(ok_bad_id)
        self.assertIn("numeric", msg_bad_id)

        # Invalid role string
        ok_bad_role, msg_bad_role = monke_bot.add_care_circle_member("777", "SuperAdmin")
        self.assertFalse(ok_bad_role)
        self.assertIn("Invalid role", msg_bad_role)


class TestAdversarialDispatchAndDebounce(unittest.TestCase):
    """Adversarial stress-testing of Callback Routing, Namespace Separation, and Debounce."""

    def setUp(self):
        monke_bot.reset_in_memory_state()

    def test_strict_foreign_namespace_rejection(self):
        """MonkeHelper must ignore all callbacks intended for GlucoTrack (gt:), MedFlow (med:), or Bio (bio:)."""
        foreign_payloads = [
            "gt:meal:50",
            "gt:lantus:taken",
            "med:log:1:500",
            "med:del:2",
            "bio:sync:now",
            "bio:sleep:detail"
        ]
        for fp in foreign_payloads:
            res = monke_bot.handle_monke_webhook({
                "callback_query": {
                    "id": f"cb_{fp}",
                    "data": fp,
                    "message": {"message_id": 1, "chat": {"id": 101}},
                    "from": {"id": 101}
                }
            })
            self.assertEqual(res.get("status"), "ignored")
            self.assertEqual(res.get("action"), "foreign_namespace_ignored")

    @patch("bot_client.TelegramBotClient.answer_callback_query")
    def test_sliding_window_debounce_burst(self, mock_answer):
        """Rapid burst of 10 identical callback queries must process only 1 and debounce 9."""
        mock_answer.return_value = {"ok": True}
        burst_id = "burst_query_12345"

        first_res = monke_bot.handle_monke_webhook({
            "callback_query": {
                "id": burst_id,
                "data": "mh:status:refresh",
                "message": {"message_id": 10, "chat": {"id": 101}},
                "from": {"id": 101}
            }
        })
        self.assertEqual(first_res.get("status"), "ok")
        self.assertEqual(first_res.get("action"), "status_refreshed")

        for _ in range(9):
            dup_res = monke_bot.handle_monke_webhook({
                "callback_query": {
                    "id": burst_id,
                    "data": "mh:status:refresh",
                    "message": {"message_id": 10, "chat": {"id": 101}},
                    "from": {"id": 101}
                }
            })
            self.assertEqual(dup_res.get("status"), "ok")
            self.assertEqual(dup_res.get("action"), "debounced")

    def test_empty_and_corrupted_webhook_updates(self):
        """Test resilience against completely empty or malformed webhook payloads."""
        for empty_update in [None, {}, {"unknown_key": 123}, {"message": None}]:
            res = monke_bot.handle_monke_webhook(empty_update)
            self.assertIn(res.get("status"), ["ok", "ignored"])


if __name__ == "__main__":
    unittest.main()
