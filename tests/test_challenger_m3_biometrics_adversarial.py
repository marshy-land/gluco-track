"""
tests/test_challenger_m3_biometrics_adversarial.py
Adversarial Verification Test Suite by M3 Iteration 2 Challenger 2.

Empirically tests:
1. Malformed and null callback query payloads across [None, "", 123, [], False, True, dicts].
2. Sliding-window callback query debouncing (rapid duplicate requests, TTL expiration, answerCallbackQuery attribution).
3. Strict foreign callback namespace rejection (gt:, med:, mh: ignored).
4. Edge cases in callback data parsing and error handling in biometrics_bot.
"""

import time
import unittest
from unittest.mock import MagicMock, patch

import biometrics_bot
from biometrics_bot import (
    handle_biometrics_webhook,
    is_debounced,
    _processed_callbacks,
    NAMESPACE_PREFIX,
    FOREIGN_PREFIXES
)


class TestM3Iter2ChallengerBiometrics(unittest.TestCase):
    """Deep adversarial verification of biometrics_bot callback handling and debouncing."""

    def setUp(self):
        _processed_callbacks.clear()
        self.mock_client = MagicMock()
        self.bot_client_patch = patch(
            "biometrics_bot.get_biometrics_bot_client",
            return_value=self.mock_client
        )
        self.bot_client_patch.start()
        self.sync_patch = patch(
            "google_fit_sync.sync_all_google_fit",
            return_value={"sleep": {"success": True}, "metrics": {"success": True}}
        )
        self.sync_patch.start()
        self.db_sessions_patch = patch("db.get_health_sessions", return_value=[])
        self.db_sessions_patch.start()
        self.db_metrics_patch = patch("db.get_health_metrics", return_value=[])
        self.db_metrics_patch.start()
        self.db_setting_patch = patch("db.get_system_setting", return_value={"enabled": True})
        self.db_setting_patch.start()

    def tearDown(self):
        self.db_setting_patch.stop()
        self.db_metrics_patch.stop()
        self.db_sessions_patch.stop()
        self.sync_patch.stop()
        self.bot_client_patch.stop()
        _processed_callbacks.clear()

    # =========================================================================
    # 1. MALFORMED / NULL CALLBACK QUERY PAYLOADS
    # =========================================================================

    def test_malformed_null_callback_query_primitives(self):
        """Verify update with callback_query set to various primitives [None, '', 123, [], False, True]."""
        primitive_cases = [
            ({"callback_query": None}, "null_value"),
            ({"callback_query": ""}, "empty_string"),
            ({"callback_query": 123}, "int_value"),
            ({"callback_query": []}, "empty_list"),
            ({"callback_query": False}, "bool_false"),
            ({"callback_query": True}, "bool_true"),
            ({"callback_query": [1, 2, 3]}, "list_with_items"),
            ({"callback_query": "random_string"}, "string_value"),
        ]
        for payload, label in primitive_cases:
            with self.subTest(label=label, payload=payload):
                res = handle_biometrics_webhook(payload)
                self.assertIsInstance(res, dict)
                self.assertIn(res.get("status"), ["ok", "ignored"])
                self.assertEqual(res.get("action"), "noop")

    def test_malformed_callback_query_data_types(self):
        """Verify inner callback_query['data'] containing non-string or malformed structures."""
        data_type_cases = [
            (None, "data_none"),
            ("", "data_empty_string"),
            (123, "data_integer"),
            (45.67, "data_float"),
            ([], "data_empty_list"),
            ([1, "a"], "data_populated_list"),
            (False, "data_bool_false"),
            (True, "data_bool_true"),
            ({}, "data_dict"),
            ({"action": "sync"}, "data_nested_dict"),
        ]
        for data_val, label in data_type_cases:
            with self.subTest(label=label, data=data_val):
                _processed_callbacks.clear()
                update = {
                    "callback_query": {
                        "id": f"cb_{label}",
                        "data": data_val,
                        "message": {"chat": {"id": 12345}, "message_id": 99},
                        "from": {"first_name": "TestUser"}
                    }
                }
                res = handle_biometrics_webhook(update)
                self.assertIsInstance(res, dict)
                self.assertIn(res.get("status"), ["ok", "ignored"])

    def test_malformed_callback_query_message_and_chat_fields(self):
        """Verify callback_query with missing, null, or corrupted message and chat hierarchies."""
        corrupted_hierarchies = [
            {"callback_query": {"id": "cb1", "data": "bio:sync:now", "message": None}},
            {"callback_query": {"id": "cb2", "data": "bio:sync:now", "message": ""}},
            {"callback_query": {"id": "cb3", "data": "bio:sync:now", "message": 123}},
            {"callback_query": {"id": "cb4", "data": "bio:sync:now", "message": []}},
            {"callback_query": {"id": "cb5", "data": "bio:sync:now", "message": {"chat": None}}},
            {"callback_query": {"id": "cb6", "data": "bio:sync:now", "message": {"chat": ""}}},
            {"callback_query": {"id": "cb7", "data": "bio:sync:now", "message": {"chat": 123}}},
            {"callback_query": {"id": "cb8", "data": "bio:sync:now", "message": {"chat": {"id": None}}}},
            {"callback_query": {"id": "cb9", "data": "bio:sleep:detail", "from": None}},
            {"callback_query": {"id": "cb10", "data": "bio:rhr:detail", "from": 123}},
            {"callback_query": {"id": "cb11", "data": "bio:isf:detail", "message": {"message_id": None}}},
            {"callback_query": {"id": "cb12", "data": "bio:dismiss", "message": {"chat": {"id": "invalid"}}}},
        ]
        for up in corrupted_hierarchies:
            with self.subTest(update=up):
                _processed_callbacks.clear()
                try:
                    res = handle_biometrics_webhook(up)
                    self.assertIsInstance(res, dict)
                    self.assertIn(res.get("status"), ["ok", "ignored"])
                except Exception as e:
                    self.fail(f"Crashed on corrupted hierarchy {up}: {type(e).__name__}: {e}")

    # =========================================================================
    # 2. SLIDING-WINDOW CALLBACK DEBOUNCING
    # =========================================================================

    def test_sliding_window_debouncing_rapid_burst(self):
        """Simulate rapid triple-tap burst for the same callback query ID."""
        cb_id = "burst_cb_test_001"
        update = {
            "callback_query": {
                "id": cb_id,
                "data": "bio:sleep:detail",
                "message": {"chat": {"id": 12345}, "message_id": 99},
                "from": {"first_name": "Alice"}
            }
        }

        # 1st Tap: Processed normally
        self.mock_client.reset_mock()
        res1 = handle_biometrics_webhook(update)
        self.assertEqual(res1.get("status"), "ok")
        self.assertEqual(res1.get("action"), "sleep_detail_shown")
        self.mock_client.answer_callback_query.assert_called_with(cb_id, "Sleep stages loaded.")

        # 2nd Tap (Immediate Duplicate): Debounced
        self.mock_client.reset_mock()
        res2 = handle_biometrics_webhook(update)
        self.assertEqual(res2.get("status"), "ok")
        self.assertEqual(res2.get("action"), "debounced")
        self.assertEqual(res2.get("details", {}).get("callback_id"), cb_id)
        self.mock_client.answer_callback_query.assert_called_with(cb_id, "Action already processed.")

        # 3rd Tap (Immediate Duplicate): Debounced again
        self.mock_client.reset_mock()
        res3 = handle_biometrics_webhook(update)
        self.assertEqual(res3.get("status"), "ok")
        self.assertEqual(res3.get("action"), "debounced")
        self.mock_client.answer_callback_query.assert_called_with(cb_id, "Action already processed.")

    def test_sliding_window_distinct_ids_concurrency(self):
        """Ensure distinct callback IDs are never incorrectly blocked by other IDs."""
        for i in range(20):
            cb_id = f"distinct_cb_{i}"
            update = {
                "callback_query": {
                    "id": cb_id,
                    "data": "bio:rhr:detail",
                    "message": {"chat": {"id": 12345}, "message_id": 99},
                    "from": {"first_name": f"User{i}"}
                }
            }
            res = handle_biometrics_webhook(update)
            self.assertEqual(res.get("status"), "ok")
            self.assertEqual(res.get("action"), "rhr_detail_shown")

    def test_sliding_window_ttl_expiration_and_cleanup(self):
        """Test TTL expiration and cache purge mechanism."""
        cb_id = "ttl_test_cb_1"
        self.assertFalse(is_debounced(cb_id, ttl_seconds=0.05))
        self.assertTrue(is_debounced(cb_id, ttl_seconds=0.05))

        # Sleep beyond TTL
        time.sleep(0.06)

        # Should be expired and allow re-registration
        self.assertFalse(is_debounced(cb_id, ttl_seconds=0.05))

    # =========================================================================
    # 3. FOREIGN CALLBACK REJECTION
    # =========================================================================

    def test_strict_foreign_prefixes_rejected(self):
        """Verify that GlucoTrack (gt:), MedFlow (med:), and MonkeHelper (mh:) prefixes are rejected."""
        foreign_actions = [
            ("gt:meal:50", "gt:"),
            ("gt:lantus:taken", "gt:"),
            ("gt:corr:1.5", "gt:"),
            ("gt:bolus:confirm", "gt:"),
            ("gt:", "gt:"),
            ("med:log:10:20", "med:"),
            ("med:del:5", "med:"),
            ("med:custom:12", "med:"),
            ("med:", "med:"),
            ("mh:briefing:refresh", "mh:"),
            ("mh:quiet:toggle", "mh:"),
            ("mh:role:set:caregiver", "mh:"),
            ("mh:", "mh:"),
        ]
        for data_str, expected_pfx in foreign_actions:
            with self.subTest(data=data_str, prefix=expected_pfx):
                _processed_callbacks.clear()
                update = {
                    "callback_query": {
                        "id": f"cb_{data_str}",
                        "data": data_str,
                        "message": {"chat": {"id": 12345}, "message_id": 99},
                        "from": {"first_name": "Charlie"}
                    }
                }
                res = handle_biometrics_webhook(update)
                self.assertEqual(res.get("status"), "ignored")
                self.assertEqual(res.get("action"), "foreign_namespace_ignored")
                self.assertEqual(res.get("details", {}).get("received_prefix"), expected_pfx)
                self.assertEqual(res.get("details", {}).get("expected_prefix"), NAMESPACE_PREFIX)

    def test_native_biometrics_callbacks_processed(self):
        """Verify that all valid biometrics callbacks execute successfully."""
        native_actions = [
            ("bio:sync:now", "biometrics_synced"),
            ("bio:sleep:detail", "sleep_detail_shown"),
            ("bio:rhr:detail", "rhr_detail_shown"),
            ("bio:isf:detail", "isf_detail_shown"),
            ("bio:dismiss", "dismissed"),
            ("sync_now", "biometrics_synced"),
            ("sleep_detail", "sleep_detail_shown"),
            ("rhr_detail", "rhr_detail_shown"),
            ("isf_detail", "isf_detail_shown"),
            ("dismiss_bio", "dismissed"),
        ]
        for data_str, expected_action in native_actions:
            with self.subTest(data=data_str, action=expected_action):
                _processed_callbacks.clear()
                update = {
                    "callback_query": {
                        "id": f"cb_{data_str.replace(':', '_')}",
                        "data": data_str,
                        "message": {"chat": {"id": 12345}, "message_id": 99},
                        "from": {"first_name": "Charlie"}
                    }
                }
                res = handle_biometrics_webhook(update)
                self.assertEqual(res.get("status"), "ok")
                self.assertEqual(res.get("action"), expected_action)


if __name__ == "__main__":
    unittest.main()
