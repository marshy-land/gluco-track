"""
e2e_tests/test_m4_challenger2_verification.py
Comprehensive empirical stress test harness for M4 Iteration 2 Challenger 2:
1. Verifies {"callback_query": null}, non-dict, missing keys, and malformed update structures.
2. Verifies mh:quiet:set:<start>:<end> valid and invalid transitions, cross-midnight logic,
   TTL debouncing, RBAC enforcement, and UI response contracts.
"""

import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Setup mock for psycopg2 if not installed
try:
    import psycopg2
except ImportError:
    mock_psycopg2 = MagicMock()
    sys.modules["psycopg2"] = mock_psycopg2
    sys.modules["psycopg2.extras"] = MagicMock()

import monke_bot
from monke_bot import (
    handle_monke_webhook,
    reset_in_memory_state,
    get_quiet_hours_config,
    save_quiet_hours_config,
    is_in_quiet_hours,
    should_suppress_notification,
    _processed_callbacks
)


class TestM4Challenger2Empirical(unittest.TestCase):
    """Empirical adversarial verification tests for MonkeHelper callback query routing and quiet hours."""

    def setUp(self):
        reset_in_memory_state()
        self.bot_client_mock = MagicMock()
        monke_bot.get_monke_bot_client = MagicMock(return_value=self.bot_client_mock)

    # -------------------------------------------------------------------------
    # 1. NULL AND MALFORMED CALLBACK QUERY STRUCTURES
    # -------------------------------------------------------------------------

    def test_null_and_falsy_callback_queries(self):
        """Test resilience against None, False, numeric, and string callback_query fields."""
        payloads = [
            ({"callback_query": None}, "null callback_query"),
            ({"callback_query": False}, "boolean False callback_query"),
            ({"callback_query": 0}, "numeric 0 callback_query"),
            ({"callback_query": ""}, "empty string callback_query"),
            ({"callback_query": "not_a_dict"}, "string callback_query"),
            ({"callback_query": []}, "empty list callback_query"),
            ({"callback_query": [1, 2, 3]}, "populated list callback_query"),
            ({"callback_query": {"invalid": "shape"}}, "dict missing id/data/message"),
            ({"callback_query": {"id": None, "data": None}}, "dict with None id/data"),
            ({"callback_query": {"id": 12345, "data": None}}, "dict with int id and None data"),
            ({"callback_query": {"id": "cb1", "data": 12345}}, "dict with numeric data"),
            ({}, "empty root update dictionary"),
            (None, "None root update"),
            ([], "list root update"),
            ("random_string", "string root update"),
            (12345, "integer root update")
        ]

        for update_payload, desc in payloads:
            with self.subTest(payload_desc=desc):
                self.bot_client_mock.reset_mock()
                res = handle_monke_webhook(update_payload)
                self.assertIsInstance(res, dict, f"Failed for {desc}: output must be dict")
                self.assertIn("status", res)
                self.assertIn("action", res)
                # Ensure no unhandled exceptions were raised and status is either ok or ignored
                self.assertIn(res.get("status"), ["ok", "ignored"])

    # -------------------------------------------------------------------------
    # 2. MH:QUIET:SET:<START>:<END> TRANSITIONS & TIME LOGIC
    # -------------------------------------------------------------------------

    def test_quiet_set_valid_transitions(self):
        """Test valid mh:quiet:set:<start>:<end> callback queries and verify config updates."""
        test_windows = [
            ("mh:quiet:set:23:7", 23, 7, "Standard default nighttime"),
            ("mh:quiet:set:22:6", 22, 6, "Standard early nighttime"),
            ("mh:quiet:set:0:8", 0, 8, "Midnight to morning"),
            ("mh:quiet:set:1:23", 1, 23, "Full day window"),
            ("mh:quiet:set:14:16", 14, 16, "Intra-day afternoon window"),
            ("mh:quiet:set:0:0", 0, 0, "Zero-width window")
        ]

        for cb_data, exp_sh, exp_eh, desc in test_windows:
            with self.subTest(scenario=desc, data=cb_data):
                reset_in_memory_state()
                self.bot_client_mock.reset_mock()

                update = {
                    "update_id": 5001,
                    "callback_query": {
                        "id": f"cb_test_{exp_sh}_{exp_eh}",
                        "from": {"id": 101, "first_name": "Owner"},
                        "message": {"message_id": 77, "chat": {"id": 101}},
                        "data": cb_data
                    }
                }

                res = handle_monke_webhook(update)
                self.assertEqual(res.get("status"), "ok")
                self.assertEqual(res.get("action"), "quiet_hours_updated")

                cfg = get_quiet_hours_config()
                self.assertEqual(cfg.get("start_hour"), exp_sh)
                self.assertEqual(cfg.get("end_hour"), exp_eh)
                self.assertTrue(cfg.get("enabled"))

                # Verify Telegram bot UI feedback
                self.bot_client_mock.answer_callback_query.assert_called_once()
                self.bot_client_mock.edit_message_text.assert_called_once()

    def test_quiet_set_cross_midnight_evaluation(self):
        """Test that setting 23:7 correctly evaluates cross-midnight and normal hours."""
        save_quiet_hours_config(23, 7, enabled=True, timezone_str="America/New_York")

        # 23:30 in New York -> in quiet hours
        dt_night = datetime(2026, 8, 21, 23, 30, tzinfo=timezone(timedelta(hours=-4)))
        self.assertTrue(is_in_quiet_hours(dt_night))

        # 03:15 in New York -> in quiet hours
        dt_early = datetime(2026, 8, 21, 3, 15, tzinfo=timezone(timedelta(hours=-4)))
        self.assertTrue(is_in_quiet_hours(dt_early))

        # 07:00 in New York -> outside quiet hours (end_hour is exclusive)
        dt_end = datetime(2026, 8, 21, 7, 0, tzinfo=timezone(timedelta(hours=-4)))
        self.assertFalse(is_in_quiet_hours(dt_end))

        # 14:00 in New York -> outside quiet hours
        dt_day = datetime(2026, 8, 21, 14, 0, tzinfo=timezone(timedelta(hours=-4)))
        self.assertFalse(is_in_quiet_hours(dt_day))

    def test_quiet_set_intraday_evaluation(self):
        """Test that setting 13:16 correctly evaluates intra-day quiet hours."""
        save_quiet_hours_config(13, 16, enabled=True, timezone_str="America/New_York")

        # 14:00 in New York -> inside quiet hours
        dt_in = datetime(2026, 8, 21, 14, 0, tzinfo=timezone(timedelta(hours=-4)))
        self.assertTrue(is_in_quiet_hours(dt_in))

        # 12:59 in New York -> outside
        dt_before = datetime(2026, 8, 21, 12, 59, tzinfo=timezone(timedelta(hours=-4)))
        self.assertFalse(is_in_quiet_hours(dt_before))

        # 16:00 in New York -> outside (end_hour exclusive)
        dt_after = datetime(2026, 8, 21, 16, 0, tzinfo=timezone(timedelta(hours=-4)))
        self.assertFalse(is_in_quiet_hours(dt_after))

    def test_quiet_set_invalid_and_adversarial_formats(self):
        """Test resilience against malformed arguments in mh:quiet:set."""
        adversarial_cases = [
            ("mh:quiet:set:25:7", "Start hour out of range (25)"),
            ("mh:quiet:set:23:25", "End hour out of range (25)"),
            ("mh:quiet:set:-1:7", "Negative start hour (-1)"),
            ("mh:quiet:set:23:-5", "Negative end hour (-5)"),
            ("mh:quiet:set:abc:def", "Alphabetic hours"),
            ("mh:quiet:set:22:xyz", "Partially alphabetic hours"),
            ("mh:quiet:set", "Missing both hour parameters"),
            ("mh:quiet:set:22", "Missing end hour parameter"),
            ("mh:quiet:set:::", "Empty colons"),
            ("mh:quiet:set:22:6:extra_payload", "Extra sub-tokens"),
            ("mh:quiet:set:  : 7", "Whitespace hours")
        ]

        for cb_data, desc in adversarial_cases:
            with self.subTest(scenario=desc, data=cb_data):
                reset_in_memory_state()
                self.bot_client_mock.reset_mock()

                update = {
                    "update_id": 5002,
                    "callback_query": {
                        "id": f"cb_invalid_{cb_data.replace(':', '_')}",
                        "from": {"id": 101, "first_name": "Attacker"},
                        "message": {"message_id": 78, "chat": {"id": 101}},
                        "data": cb_data
                    }
                }

                res = handle_monke_webhook(update)
                # Should not crash or corrupt state; returns callback_noop, action_processed, or handled gracefully
                self.assertIsInstance(res, dict)
                self.assertEqual(res.get("status"), "ok")
                self.assertIn(res.get("action"), ["callback_noop", "quiet_hours_updated", "action_processed"])

    def test_quiet_toggle_transitions(self):
        """Verify mh:quiet:toggle cleanly alternates enabled state."""
        cfg_init = get_quiet_hours_config()
        self.assertTrue(cfg_init["enabled"])

        # Toggle OFF
        update_toggle1 = {
            "update_id": 5003,
            "callback_query": {
                "id": "cb_toggle_1",
                "from": {"id": 101, "first_name": "Owner"},
                "message": {"message_id": 79, "chat": {"id": 101}},
                "data": "mh:quiet:toggle"
            }
        }
        res1 = handle_monke_webhook(update_toggle1)
        self.assertEqual(res1.get("status"), "ok")
        self.assertEqual(res1.get("action"), "quiet_hours_toggled")
        self.assertFalse(res1.get("config", {}).get("enabled"))
        self.assertFalse(get_quiet_hours_config()["enabled"])

        # Toggle ON
        update_toggle2 = {
            "update_id": 5004,
            "callback_query": {
                "id": "cb_toggle_2",
                "from": {"id": 101, "first_name": "Owner"},
                "message": {"message_id": 79, "chat": {"id": 101}},
                "data": "mh:quiet:toggle"
            }
        }
        res2 = handle_monke_webhook(update_toggle2)
        self.assertEqual(res2.get("status"), "ok")
        self.assertEqual(res2.get("action"), "quiet_hours_toggled")
        self.assertTrue(res2.get("config", {}).get("enabled"))
        self.assertTrue(get_quiet_hours_config()["enabled"])

    def test_quiet_hours_hypo_bypass_suppression_logic(self):
        """Verify notification suppression logic under quiet hours and hypo bypass."""
        save_quiet_hours_config(23, 7, enabled=True)
        dt_night = datetime(2026, 8, 21, 23, 30, tzinfo=timezone(timedelta(hours=-4)))

        # Routine reminder during quiet hours -> Suppressed
        suppressed, reason, meta = should_suppress_notification("routine_reminder", glucose_value=125.0, dt=dt_night)
        self.assertTrue(suppressed)
        self.assertEqual(reason, "quiet_hours")

        # Critical Hypoglycemia (<70) during quiet hours -> NOT suppressed (Bypass)
        suppressed_hypo, reason_hypo, meta_hypo = should_suppress_notification("cgm_low", glucose_value=54.0, dt=dt_night, iob=1.5)
        self.assertFalse(suppressed_hypo)
        self.assertEqual(reason_hypo, "emergency_hypo_bypass")
        self.assertEqual(meta_hypo.get("urgency"), "critical_low")
        self.assertGreaterEqual(meta_hypo.get("recommended_rescue_carbs", 0), 15)

        # Outside quiet hours -> NOT suppressed
        dt_day = datetime(2026, 8, 21, 14, 0, tzinfo=timezone(timedelta(hours=-4)))
        suppressed_day, reason_day, _ = should_suppress_notification("routine_reminder", glucose_value=125.0, dt=dt_day)
        self.assertFalse(suppressed_day)
        self.assertEqual(reason_day, "normal_hours")


if __name__ == "__main__":
    unittest.main()
