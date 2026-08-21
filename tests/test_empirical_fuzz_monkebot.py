"""
tests/test_empirical_fuzz_monkebot.py
Empirical Fuzzing and Stress Harness for Milestone 4 (MonkeHelper Master Hub):
1. 0.0 mg/dL, Negative, and Boundary Glucose Values.
2. Sparse / Null / Nested Empty Database Dictionaries across all domains.
3. NaN, +Inf, -Inf, and Non-Numeric Metric Resilience.
4. System Settings (Quiet Hours & Care Circle) Corruption Fuzzing.
5. Fuzz-Mutated Telegram Webhook and Callback Query Payloads.
"""

import os
import sys
import math
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import pytz

# Add workspace root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Ensure mock psycopg2 if needed
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


class TestMonkeBotEmpiricalFuzzing(unittest.TestCase):
    """Empirical Fuzzing Test Suite for monke_bot.py."""

    def setUp(self):
        monke_bot.reset_in_memory_state()
        # Fast DB disconnection fallback (prevents 30s TCP timeouts in offline mode)
        self.patch_db = patch("db.get_connection", side_effect=Exception("Offline DB test"))
        self.mock_db = self.patch_db.start()

        # Mock Telegram Bot API network calls across all tests
        self.patch_send = patch("bot_client.TelegramBotClient.send_message", return_value={"ok": True})
        self.patch_edit = patch("bot_client.TelegramBotClient.edit_message_text", return_value={"ok": True})
        self.patch_answer = patch("bot_client.TelegramBotClient.answer_callback_query", return_value={"ok": True})
        self.patch_delete = patch("bot_client.TelegramBotClient.delete_message", return_value={"ok": True})
        self.mock_send = self.patch_send.start()
        self.mock_edit = self.patch_edit.start()
        self.mock_answer = self.patch_answer.start()
        self.mock_delete = self.patch_delete.start()

    def tearDown(self):
        self.patch_db.stop()
        self.patch_send.stop()
        self.patch_edit.stop()
        self.patch_answer.stop()
        self.patch_delete.stop()

    # =========================================================================
    # 1. 0.0 mg/dL, Negative, and Boundary Glucose Values Fuzzing
    # =========================================================================
    def test_zero_mgdl_glucose_emergency_bypass(self):
        """0.0 mg/dL glucose must be recognized as critical hypoglycemia (<70) and NEVER suppressed."""
        tz = pytz.timezone("America/New_York")
        quiet_dt = tz.localize(datetime(2026, 8, 22, 3, 0, 0))

        for zero_val in [0.0, 0, 0.0001, -0.0, -5.0, -100.0]:
            suppressed, reason, meta = monke_bot.should_suppress_notification(
                event_type="glucose_reading",
                glucose_value=zero_val,
                dt=quiet_dt,
                iob=0.0
            )
            self.assertFalse(suppressed, f"CRITICAL: Glucose {zero_val} was suppressed during quiet hours!")
            self.assertEqual(reason, "emergency_hypo_bypass")
            self.assertEqual(meta["urgency"], "critical_low")
            self.assertGreaterEqual(meta["recommended_rescue_carbs"], 15)

    def test_zero_mgdl_glucose_alert_formatting(self):
        """0.0 mg/dL glucose alert must format valid HTML without crashing or ZeroDivisionError."""
        alert_html = monke_bot.build_emergency_hypo_alert(glucose=0.0, iob=0.0, trend_arrow="⇊", trend_desc="Critical Low")
        self.assertIsInstance(alert_html, str)
        self.assertIn("CRITICAL HYPOGLYCEMIA ALERT", alert_html)
        self.assertIn("0 mg/dL", alert_html)
        self.assertIn("fast-acting carbs", alert_html)

    def test_zero_mgdl_in_unified_briefing_and_drilldown(self):
        """0.0 mg/dL in latest reading must propagate safely into briefing, cards, and status."""
        now = datetime.now(timezone.utc)
        with patch("db.get_latest_reading", return_value={"value": 0.0, "timestamp": now}), \
             patch("db.get_history", return_value=[{"value": 0.0, "timestamp": now}]), \
             patch("db.get_statistics", return_value={"average_glucose": 0.0, "total_readings": 1, "time_in_range": {"target_percent": 0.0, "low_percent": 100.0, "high_percent": 0.0}}), \
             patch("db.get_insulin_history", return_value=[]), \
             patch("db.get_recent_med_logs", return_value=[]), \
             patch("db.get_medication_presets", return_value=[]), \
             patch("db.get_medication_summary", return_value=[]), \
             patch("db.get_food_history", return_value=[]), \
             patch("circadian_analysis.get_circadian_biometrics_summary", return_value={}):

            briefing = monke_bot.get_unified_daily_briefing(hours=24)
            self.assertEqual(briefing["cgm"]["current_glucose"], 0.0)
            self.assertEqual(briefing["cgm"]["mean_glucose"], 0.0)
            self.assertTrue(briefing["alerts"]["urgent_active"])

            # Verify HTML digest contains 0 mg/dL and critical alert note
            digest = briefing["digest_text"]
            self.assertIn("0 mg/dL", digest)
            self.assertIn("Active critical hypoglycemia", digest)

            # Verify glucose drilldown card
            card, kb = monke_bot.build_glucose_drilldown_card(briefing["cgm"])
            self.assertIn("0 mg/dL", card)

            # Verify status card
            status_card, _ = monke_bot.build_multi_bot_status_card()
            self.assertIn("0 mg/dL", status_card)

    # =========================================================================
    # 2. Sparse / Null / Nested Empty Dictionaries Fuzzing
    # =========================================================================
    def test_sparse_and_null_database_responses(self):
        """Fuzz with empty, None, and irregularly structured dictionary responses."""
        sparse_stats_cases = [
            {},
            {"average_glucose": None},
            {"time_in_range": None},
            {"time_in_range": {}},
            {"time_in_range": {"target_percent": None}},
            {"time_in_range": {"in_range_percent": 75.0}},
            {"time_in_range": {"hypo_percent": 5.0, "hyper_percent": 20.0}},
            {"total_readings": None}
        ]

        for sparse_stat in sparse_stats_cases:
            with patch("db.get_latest_reading", return_value=None), \
                 patch("db.get_history", return_value=[]), \
                 patch("db.get_statistics", return_value=sparse_stat), \
                 patch("db.get_insulin_history", return_value=[]), \
                 patch("db.get_recent_med_logs", return_value=[]), \
                 patch("db.get_medication_presets", return_value=[]), \
                 patch("db.get_medication_summary", return_value=[]), \
                 patch("db.get_food_history", return_value=[]), \
                 patch("circadian_analysis.get_circadian_biometrics_summary", return_value={}):

                briefing = monke_bot.get_unified_daily_briefing(hours=24)
                self.assertIsInstance(briefing, dict)
                self.assertIsInstance(briefing["digest_text"], str)

    # =========================================================================
    # 3. NaN, +Inf, -Inf Float Values Fuzzing
    # =========================================================================
    def test_nan_and_inf_numeric_metrics_in_briefing(self):
        """Fuzz numeric fields with NaN, +Inf, -Inf to ensure graceful formatting."""
        now = datetime.now(timezone.utc)
        nan_float = float("nan")

        with patch("db.get_latest_reading", return_value={"value": nan_float, "timestamp": now}), \
             patch("db.get_history", return_value=[{"value": nan_float, "timestamp": now}]), \
             patch("db.get_statistics", return_value={
                 "average_glucose": nan_float,
                 "total_readings": 1,
                 "time_in_range": {"target_percent": nan_float, "low_percent": nan_float, "high_percent": nan_float}
             }), \
             patch("db.get_insulin_history", return_value=[{
                 "long_acting": nan_float, "rapid_acting": nan_float, "meal": nan_float, "correction": nan_float, "user_change": nan_float
             }]), \
             patch("db.get_recent_med_logs", return_value=[{
                 "name": "Metformin", "dose_taken": 500, "dose_unit": "mg", "timestamp": now, "notes": ""
             }]), \
             patch("db.get_medication_presets", return_value=[{"name": "Metformin"}]), \
             patch("db.get_medication_summary", return_value=[{"name": "Metformin"}]), \
             patch("db.get_food_history", return_value=[{"carbs_g": nan_float, "food_type": "Meal", "timestamp": now}]), \
             patch("circadian_analysis.get_circadian_biometrics_summary", return_value={
                 "sleep": {"total_hours_24h": nan_float, "efficiency_percent": nan_float, "deep_percent": nan_float, "rem_percent": nan_float},
                 "circadian": {"sleep_midpoint": "03:30 AM", "chronotype": "Intermediate"},
                 "rhr": {"dipping_percent": nan_float, "daytime_baseline": nan_float, "nocturnal_baseline": nan_float, "nadir_bpm": nan_float},
                 "isf": {"modifier": nan_float, "explanation": "Baseline intact."}
             }):

            briefing = monke_bot.get_unified_daily_briefing(hours=24)
            self.assertIsInstance(briefing, dict)
            self.assertIsInstance(briefing["digest_text"], str)

            # Drilldown cards
            card_g, _ = monke_bot.build_glucose_drilldown_card(briefing["cgm"])
            self.assertIsInstance(card_g, str)

            card_m, _ = monke_bot.build_meds_drilldown_card(briefing["insulin"], briefing["medications"])
            self.assertIsInstance(card_m, str)

            card_s, _ = monke_bot.build_sleep_drilldown_card(briefing["circadian"])
            self.assertIsInstance(card_s, str)

            card_n, _ = monke_bot.build_nutrition_drilldown_card(briefing["nutrition"])
            self.assertIsInstance(card_n, str)

    # =========================================================================
    # 4. Telegram Webhook Payload Fuzzing
    # =========================================================================
    def test_webhook_fuzz_mutations(self):
        """Fuzz handle_monke_webhook with random, nested, malformed, and out-of-spec payloads."""
        fuzz_updates = [
            {},
            {"update_id": "not_an_int"},
            {"message": 12345},
            {"message": {"text": None, "chat": None, "from": None}},
            {"message": {"text": "", "chat": {"id": 101, "type": "private"}}},
            {"message": {"text": "   ", "chat": {"id": "101"}}},
            {"message": {"text": "/briefing", "chat": {"id": 101, "type": "private"}, "from": {"id": 101}}},
            {"message": {"text": "/status", "chat": {"id": 101, "type": "group"}}},
            {"message": {"text": "/quiethours nan inf", "chat": {"id": 101}}},
            {"message": {"text": "/quiethours 25 -5", "chat": {"id": 101}}},
            {"message": {"text": "/addcaregiver", "chat": {"id": 101}, "from": {"id": 101}}},
            {"message": {"text": "/addcaregiver notanid InvalidRole", "chat": {"id": 101}, "from": {"id": 101}}},
            {"message": {"text": "/removecaregiver", "chat": {"id": 101}, "from": {"id": 101}}},
            {"callback_query": None},
            {"callback_query": "not a dict"},
            {"callback_query": {"id": None, "data": None}},
            {"callback_query": {"id": "123", "data": 12345}},
            {"callback_query": {"id": "cb1", "data": "mh:unknown:action"}},
            {"callback_query": {"id": "cb2", "data": "mh:quiet:set:nan:inf"}},
            {"callback_query": {"id": "cb3", "data": "mh:quiet:set:25:99"}},
            {"callback_query": {"id": "cb4", "data": "mh:briefing:glucose", "message": {"chat": {"id": 101}, "message_id": 5}}},
            {"callback_query": {"id": "cb5", "data": "mh:briefing:meds", "message": {"chat": {"id": 101}, "message_id": 5}}},
            {"callback_query": {"id": "cb6", "data": "mh:briefing:sleep", "message": {"chat": {"id": 101}, "message_id": 5}}},
            {"callback_query": {"id": "cb7", "data": "mh:briefing:nutrition", "message": {"chat": {"id": 101}, "message_id": 5}}},
            {"callback_query": {"id": "cb8", "data": "mh:dismiss", "message": {"chat": {"id": 101}, "message_id": 5}}},
        ]

        for i, update in enumerate(fuzz_updates):
            try:
                res = monke_bot.handle_monke_webhook(update)
                self.assertIsInstance(res, dict, f"Update index {i} did not return a dict: {res}")
                self.assertIn("status", res, f"Update index {i} missing status key: {res}")
            except Exception as e:
                self.fail(f"handle_monke_webhook crashed on fuzz update index {i} ({update!r}) with {type(e).__name__}: {e}")


if __name__ == "__main__":
    unittest.main()
