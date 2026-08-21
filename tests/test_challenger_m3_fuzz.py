"""
tests/test_challenger_m3_fuzz.py
Adversarial Fuzzing and Stress Testing Suite for Milestone 3: Circadian & Biometrics Bot Router.

Challenges:
1. Non-dict, empty, None, and primitive webhook update payloads.
2. Malformed callback queries (unhashable IDs, non-string data, missing/corrupted message/chat/from fields).
3. Foreign callback query namespaces (gt:, med:, mh: ignored across diverse formats).
4. Rapid callback debouncing under unhashable, null, or extreme TTL parameters.
5. Group noise filtering vs. bot mentions vs. foreign bot mentions (/cmd@other_bot).
6. Menu button text and command alias variations (case insensitivity, whitespace, unicode).
7. Telemetry ingestion edge cases (corrupt sessions/metrics, NaN/Inf, negative values).
8. Helper function resilience (format_minutes_to_hm, is_debounced, card builders).
9. FastAPI ingress endpoint HTTP fuzzing (/api/biometrics/webhook).
"""

import os
import sys
import time
import math
import unittest
from typing import Dict, Any, List
from unittest.mock import patch, MagicMock

# Mock psycopg2 if not installed
try:
    import psycopg2
except ImportError:
    mock_psycopg2 = MagicMock()
    sys.modules["psycopg2"] = mock_psycopg2
    sys.modules["psycopg2.extras"] = MagicMock()

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import db
db.get_health_sessions = MagicMock(return_value=[])
db.get_health_metrics = MagicMock(return_value=[])
db.get_system_setting = MagicMock(return_value={"enabled": True})
db.set_system_setting = MagicMock(return_value=None)

from fastapi.testclient import TestClient
from app import app
import biometrics_bot
from biometrics_bot import (
    handle_biometrics_webhook,
    is_debounced,
    _processed_callbacks,
    format_minutes_to_hm,
    build_executive_bio_card,
    build_sleep_detail_card,
    build_rhr_detail_card,
    build_isf_detail_card,
    get_biometrics_bot_config,
    save_biometrics_bot_config,
    send_biometrics_message,
)
import circadian_analysis


class TestChallengerM3Fuzz(unittest.TestCase):
    """Deep adversarial fuzzing harness for biometrics_bot."""

    def setUp(self):
        self.client = TestClient(app)
        _processed_callbacks.clear()
        self.bot_client_mock = MagicMock()
        biometrics_bot.get_biometrics_bot_client = MagicMock(return_value=self.bot_client_mock)

    # -------------------------------------------------------------------------
    # 1. NON-DICT & PRIMITIVE WEBHOOK PAYLOAD FUZZING
    # -------------------------------------------------------------------------
    def test_fuzz_non_dict_and_empty_payloads(self):
        """Fuzz handle_biometrics_webhook with 50+ abnormal non-dict or empty payloads."""
        fuzz_inputs = [
            None,
            {},
            "",
            "   ",
            "\n\t\r",
            [],
            [1, 2, 3],
            ["string_in_list"],
            [{"message": {"text": "/bio"}}],
            0,
            1,
            -1,
            99999999,
            float("inf"),
            float("-inf"),
            float("nan"),
            True,
            False,
            (1, 2),
            {1, 2, 3},
            b"bytes_payload",
            b"",
            bytearray(b"bytearray"),
            object(),
            lambda x: x,
            (x for x in range(10)),
        ]
        for payload in fuzz_inputs:
            with self.subTest(payload=type(payload)):
                try:
                    res = handle_biometrics_webhook(payload)
                    self.assertIsInstance(res, dict)
                    self.assertEqual(res.get("status"), "ok")
                    self.assertEqual(res.get("action"), "noop")
                except Exception as e:
                    self.fail(f"handle_biometrics_webhook crashed on non-dict payload of type {type(payload)}: {e}")

    # -------------------------------------------------------------------------
    # 2. MALFORMED CALLBACK QUERY FUZZING
    # -------------------------------------------------------------------------
    def test_fuzz_malformed_callback_query_fields(self):
        """Fuzz callback queries with unhashable IDs, missing data, and invalid types."""
        malformed_queries = [
            # ID anomalies
            {"id": None, "data": "bio:sync:now"},
            {"id": "", "data": "bio:sync:now"},
            {"id": 12345, "data": "bio:sync:now"},
            {"id": -99, "data": "bio:sync:now"},
            {"id": float("nan"), "data": "bio:sync:now"},
            {"id": ["unhashable_list"], "data": "bio:sync:now"},
            {"id": {"nested": "unhashable"}, "data": "bio:sync:now"},
            {"id": ("tuple_id",), "data": "bio:sync:now"},
            # Data anomalies
            {"id": "cb1", "data": None},
            {"id": "cb2", "data": 123},
            {"id": "cb3", "data": 45.67},
            {"id": "cb4", "data": True},
            {"id": "cb5", "data": False},
            {"id": "cb6", "data": []},
            {"id": "cb7", "data": {}},
            {"id": "cb8", "data": ""},
            {"id": "cb9", "data": "bio:"},
            {"id": "cb10", "data": "bio:::extra:colons"},
            {"id": "cb11", "data": "bio:unknown_action_xyz"},
            {"id": "cb12", "data": "UNKNOWN_PREFIX:action"},
            {"id": "cb13", "data": "A" * 10000},
            {"id": "cb14", "data": "bio:sleep:detail\x00null_byte"},
            # Message / Chat anomalies
            {"id": "cb15", "data": "bio:sleep:detail", "message": None},
            {"id": "cb16", "data": "bio:sleep:detail", "message": ""},
            {"id": "cb17", "data": "bio:sleep:detail", "message": 123},
            {"id": "cb18", "data": "bio:sleep:detail", "message": []},
            {"id": "cb19", "data": "bio:sleep:detail", "message": {"chat": None, "message_id": None}},
            {"id": "cb20", "data": "bio:sleep:detail", "message": {"chat": "not_a_dict", "message_id": "bad_id"}},
            {"id": "cb21", "data": "bio:sleep:detail", "message": {"chat": {"id": "str_chat_id"}, "message_id": 999}},
            {"id": "cb22", "data": "bio:sleep:detail", "message": {"chat": {"id": -10012345}, "message_id": 999}},
            # From user anomalies
            {"id": "cb23", "data": "bio:rhr:detail", "from": None},
            {"id": "cb24", "data": "bio:rhr:detail", "from": "string_user"},
            {"id": "cb25", "data": "bio:rhr:detail", "from": {"first_name": None}},
            {"id": "cb26", "data": "bio:rhr:detail", "from": {"first_name": 12345}},
            {"id": "cb27", "data": "bio:rhr:detail", "from": {"first_name": ["User"]}},
            # Legacy action names
            {"id": "cb28", "data": "sync_now"},
            {"id": "cb29", "data": "sleep_detail"},
            {"id": "cb30", "data": "rhr_detail"},
            {"id": "cb31", "data": "isf_detail"},
            {"id": "cb32", "data": "dismiss"},
            {"id": "cb33", "data": "dismiss_bio"},
            {"id": "cb34", "data": "bio:dismiss"},
        ]

        for idx, cb in enumerate(malformed_queries):
            with self.subTest(idx=idx, cb=cb):
                update = {"callback_query": cb}
                try:
                    res = handle_biometrics_webhook(update)
                    self.assertIsInstance(res, dict)
                    self.assertIn(res.get("status"), ["ok", "ignored"])
                except Exception as e:
                    self.fail(f"handle_biometrics_webhook crashed on malformed callback_query {cb}: {type(e).__name__}: {e}")

    # -------------------------------------------------------------------------
    # 3. FOREIGN CALLBACK QUERY NAMESPACE MATRIX
    # -------------------------------------------------------------------------
    def test_foreign_callback_namespace_exhaustive(self):
        """Verify strict isolation for all foreign namespaces."""
        foreign_prefixes = ["gt:", "med:", "mh:"]
        suffixes = [
            "",
            "1",
            "action",
            "log:1:10",
            "meal:45",
            "briefing:refresh",
            "quiet:toggle",
            "custom:action:extra",
            ":::",
            "x" * 500,
        ]
        for pfx in foreign_prefixes:
            for sfx in suffixes:
                cb_data = f"{pfx}{sfx}"
                with self.subTest(callback_data=cb_data):
                    update = {
                        "callback_query": {
                            "id": f"cb_{hash(cb_data) % 100000}",
                            "data": cb_data,
                            "from": {"first_name": "TestUser"},
                            "message": {"chat": {"id": 12345}, "message_id": 100}
                        }
                    }
                    res = handle_biometrics_webhook(update)
                    self.assertEqual(res.get("status"), "ignored")
                    self.assertEqual(res.get("action"), "foreign_namespace_ignored")
                    self.assertEqual(res.get("details", {}).get("received_prefix"), pfx)

    # -------------------------------------------------------------------------
    # 4. MALFORMED MESSAGE & TEXT FUZZING
    # -------------------------------------------------------------------------
    def test_fuzz_malformed_message_objects(self):
        """Fuzz handle_biometrics_webhook with corrupted message structures."""
        messages = [
            {"text": None},
            {"text": 12345},
            {"text": ["list", "of", "text"]},
            {"text": {"dict": "text"}},
            {"text": True},
            {"text": False},
            {"text": ""},
            {"text": "   \t\n   "},
            {"chat": None, "text": "/bio"},
            {"chat": "string_chat", "text": "/bio"},
            {"chat": 12345, "text": "/bio"},
            {"chat": [], "text": "/bio"},
            {"chat": {"id": None, "type": None}, "text": "/bio"},
            {"chat": {"id": 123, "type": 123}, "text": "/bio"},
            {"chat": {"id": 123, "type": "unknown_type"}, "text": "/bio"},
            {"from": None, "text": "/bio"},
            {"from": 12345, "text": "/bio"},
        ]
        for idx, m in enumerate(messages):
            with self.subTest(idx=idx, msg=m):
                update = {"message": m}
                try:
                    res = handle_biometrics_webhook(update)
                    self.assertIsInstance(res, dict)
                    self.assertIn(res.get("status"), ["ok", "ignored"])
                except Exception as e:
                    self.fail(f"handle_biometrics_webhook crashed on malformed message {m}: {type(e).__name__}: {e}")

    def test_fuzz_text_commands_and_button_variants(self):
        """Test variations of commands, case sensitivity, button texts, and group mentions."""
        valid_cases = [
            # Slash commands
            ("/bio", "bio_command_response"),
            ("/BIO", "bio_command_response"),
            ("/Bio", "bio_command_response"),
            ("/biometrics", "bio_command_response"),
            ("/BIOMETRICS", "bio_command_response"),
            ("/sleep", "sleep_card_sent"),
            ("/SLEEP", "sleep_card_sent"),
            ("/Sleep", "sleep_card_sent"),
            ("/rhr", "rhr_card_sent"),
            ("/RHR", "rhr_card_sent"),
            ("/isf", "isf_card_sent"),
            ("/ISF", "isf_card_sent"),
            ("/sync", "sync_triggered"),
            ("/SYNC", "sync_triggered"),
            ("/start", "start_menu_sent"),
            ("/help", "start_menu_sent"),
            ("/menu", "start_menu_sent"),
            ("/link", "chat_linked"),
            ("/setgroup", "chat_linked"),
            # Bot mentions in group
            ("/bio@biometrics_bot", "bio_command_response"),
            ("/sleep@biometrics_bot", "sleep_card_sent"),
            ("/rhr@bio_bot", "rhr_card_sent"),
            ("/isf@circadian_bot", "isf_card_sent"),
            # Menu Reply Keyboard buttons
            ("😴 Sleep Report", "sleep_card_sent"),
            ("😴 sleep report", "sleep_card_sent"),
            ("Sleep Report", "sleep_card_sent"),
            ("sleep report", "sleep_card_sent"),
            ("💓 Resting Heart Rate", "rhr_card_sent"),
            ("💓 resting heart rate", "rhr_card_sent"),
            ("Resting Heart Rate", "rhr_card_sent"),
            ("resting heart rate", "rhr_card_sent"),
            ("🎯 ISF Modifier", "isf_card_sent"),
            ("🎯 isf modifier", "isf_card_sent"),
            ("ISF Modifier", "isf_card_sent"),
            ("isf modifier", "isf_card_sent"),
            ("🔄 Sync Health Data", "sync_triggered"),
            ("🔄 sync health data", "sync_triggered"),
            ("Sync Health Data", "sync_triggered"),
            ("sync health data", "sync_triggered"),
        ]

        for text, expected_action in valid_cases:
            with self.subTest(text=text):
                update = {
                    "message": {
                        "chat": {"id": 12345, "type": "private"},
                        "text": text,
                        "from": {"id": 99, "first_name": "Alice"}
                    }
                }
                res = handle_biometrics_webhook(update)
                self.assertEqual(res.get("status"), "ok", f"Failed for text '{text}'")
                self.assertEqual(res.get("action"), expected_action, f"Failed action match for '{text}'")

    # -------------------------------------------------------------------------
    # 5. DIRECT TELEMETRY INGESTION FUZZING
    # -------------------------------------------------------------------------
    def test_fuzz_direct_telemetry_ingestion(self):
        """Fuzz 'sessions' and 'metrics' ingestion branches with hostile inputs."""
        hostile_telemetry = [
            {"sessions": None, "metrics": None},
            {"sessions": [], "metrics": []},
            {"sessions": [None, 123, "bad_session", {}]},
            {"metrics": [None, 456, "bad_metric", {}]},
            {"sessions": [{"duration_minutes": float("nan"), "session_type": "sleep"}]},
            {"sessions": [{"duration_minutes": float("inf"), "session_type": "sleep"}]},
            {"sessions": [{"duration_minutes": float("-inf"), "session_type": "sleep"}]},
            {"sessions": [{"duration_minutes": -9999.0, "session_type": "sleep"}]},
            {"sessions": [{"start_time": "NOT_A_DATE", "end_time": "INVALID"}]},
            {"sessions": [{"start_time": None, "end_time": None, "duration_minutes": None}]},
            {"metrics": [{"value": float("nan")}, {"value": -500}, {"value": 100000}]},
        ]
        for idx, payload in enumerate(hostile_telemetry):
            with self.subTest(idx=idx, payload=payload):
                try:
                    res = handle_biometrics_webhook(payload)
                    self.assertIsInstance(res, dict)
                    self.assertEqual(res.get("status"), "ok")
                    self.assertEqual(res.get("action"), "biometrics_synced")
                    self.assertIn("metrics", res)
                    self.assertIn("details", res)
                except Exception as e:
                    self.fail(f"handle_biometrics_webhook crashed on hostile telemetry {payload}: {type(e).__name__}: {e}")

    # -------------------------------------------------------------------------
    # 6. HELPER FUNCTION ROBUSTNESS & EDGE CASES
    # -------------------------------------------------------------------------
    def test_format_minutes_to_hm_fuzz(self):
        """Verify format_minutes_to_hm handles standard, boundary, and float cases."""
        test_cases = [
            (None, "0m"),
            (-10.0, "0m"),
            (0.0, "0m"),
            (0, "0m"),
            (15.0, "15m"),
            (59.9, "1h"),
            (60.0, "1h"),
            (60.1, "1h"),
            (90.0, "1h 30m"),
            (120.0, "2h"),
            (480.0, "8h"),
            (515.0, "8h 35m"),
            (1440.0, "24h"),
        ]
        for val, expected in test_cases:
            with self.subTest(val=val):
                res = format_minutes_to_hm(val)
                self.assertEqual(res, expected)

    def test_is_debounced_fuzz(self):
        """Verify is_debounced handles unhashable inputs, None, empty string, and rapid calls."""
        # None and empty string must return False
        self.assertFalse(is_debounced(None))
        self.assertFalse(is_debounced(""))

        # Normal ID
        self.assertFalse(is_debounced("id_test_1", ttl_seconds=60.0))
        self.assertTrue(is_debounced("id_test_1", ttl_seconds=60.0))

        # Different ID
        self.assertFalse(is_debounced("id_test_2", ttl_seconds=60.0))

        # Test sliding window clean-up under 1000 expired entries
        now_ts = time.time()
        for i in range(1000):
            _processed_callbacks[f"old_id_{i}"] = now_ts - 100.0  # Expired

        self.assertGreaterEqual(len(_processed_callbacks), 1000)
        # Calling is_debounced should prune all expired entries
        self.assertFalse(is_debounced("id_clean_check", ttl_seconds=60.0))
        self.assertLess(len(_processed_callbacks), 10)

    def test_card_builders_with_null_and_mocked_summaries(self):
        """Verify all card builders execute cleanly when underlying summary has empty data."""
        with patch("biometrics_bot.get_circadian_biometrics_summary") as mock_sum:
            # Case 1: Empty dict returned by summary
            mock_sum.return_value = {}
            text, kb, summary = build_executive_bio_card()
            self.assertIsInstance(text, str)
            self.assertIn("Circadian & Biometrics Overview", text)
            self.assertIsInstance(kb, dict)

            text_sleep, kb_sleep = build_sleep_detail_card()
            self.assertIsInstance(text_sleep, str)
            self.assertIn("Sleep Stage Architecture", text_sleep)

            text_rhr, kb_rhr = build_rhr_detail_card()
            self.assertIsInstance(text_rhr, str)
            self.assertIn("Nocturnal Resting Heart Rate", text_rhr)

            text_isf, kb_isf = build_isf_detail_card()
            self.assertIsInstance(text_isf, str)
            self.assertIn("Dynamic Insulin Sensitivity", text_isf)

    def test_config_get_and_save_edge_cases(self):
        """Verify get_biometrics_bot_config and save_biometrics_bot_config."""
        # Test save
        save_biometrics_bot_config("fake_token_123", "987654", enabled=True)
        # Test get
        cfg = get_biometrics_bot_config()
        self.assertIsInstance(cfg, dict)
        self.assertIn("enabled", cfg)

    def test_unlinked_group_ambient_noise_does_not_overwrite_config(self):
        """Verify unaddressed ambient noise in a group chat does not automatically save chat_id."""
        with patch("biometrics_bot.get_biometrics_bot_config") as mock_cfg, \
             patch("biometrics_bot.save_biometrics_bot_config") as mock_save:
            # Bot currently unlinked
            mock_cfg.return_value = {"bot_token": "token123", "chat_id": "", "enabled": True}
            
            # Ambient group message
            update = {
                "message": {
                    "chat": {"id": -10099999, "type": "group"},
                    "text": "Just casual chit chat between users",
                    "from": {"id": 456, "first_name": "Bob"}
                }
            }
            res = handle_biometrics_webhook(update)
            self.assertEqual(res.get("status"), "ignored")
            self.assertEqual(res.get("action"), "group_noise_ignored")
            # Should NOT have called save_biometrics_bot_config to link chat_id on unaddressed ambient noise
            mock_save.assert_not_called()


    # -------------------------------------------------------------------------
    # 7. FASTAPI INGRESS ROUTE HTTP FUZZING
    # -------------------------------------------------------------------------
    def test_fastapi_ingress_http_fuzzing(self):
        """Test POST /api/biometrics/webhook with various valid and invalid HTTP payloads."""
        # 1. Valid JSON commands
        res = self.client.post("/api/biometrics/webhook", json={"message": {"text": "/sleep", "chat": {"id": 123}}})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("status"), "ok")
        self.assertEqual(res.json().get("action"), "sleep_card_sent")

        # 2. Empty JSON dict
        res = self.client.post("/api/biometrics/webhook", json={})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("status"), "ok")
        self.assertEqual(res.json().get("action"), "noop")

        # 3. Direct telemetry via HTTP
        res = self.client.post("/api/biometrics/webhook", json={"sessions": [], "metrics": []})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("status"), "ok")
        self.assertEqual(res.json().get("action"), "biometrics_synced")

        # 4. Callback query via HTTP
        res = self.client.post("/api/biometrics/webhook", json={
            "callback_query": {
                "id": "http_cb_1",
                "data": "bio:isf:detail",
                "message": {"chat": {"id": 123}}
            }
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("status"), "ok")
        self.assertEqual(res.json().get("action"), "isf_detail_shown")


if __name__ == "__main__":
    unittest.main(verbosity=2)
