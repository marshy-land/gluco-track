"""
e2e_tests/test_m3_biometrics_adversarial.py
Adversarial Stress Test Suite for Milestone 3 (Circadian & Biometrics Bot Router).

Covers:
1. Malformed Webhook Updates (null callback_query, null message, invalid types, missing fields)
2. Foreign Callback Query Namespaces (gt:, med:, mh: properly ignored with foreign_namespace_ignored)
3. Group Chat Noise Filtering (ambient chatter ignored, bot commands processed, other bot commands ignored)
4. Rapid Double-Tap Callback Query Debouncing (same callback ID sliding window cache)
5. Telemetry Ingestion & Mathematical Boundaries (abnormal session formats, extreme heart rate values, ISF clamp bounds)
6. FastAPI Webhook Ingress Route (/api/biometrics/webhook) Edge Cases
"""

import os
import sys
import time
import math
import unittest
from typing import Dict, Any
from unittest.mock import patch, MagicMock

# Setup mock for psycopg2 if not installed
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
# Set up default db mocks if needed
db.get_health_sessions = MagicMock(return_value=[])
db.get_health_metrics = MagicMock(return_value=[])
db.get_system_setting = MagicMock(return_value={"enabled": True})
db.set_system_setting = MagicMock(return_value=None)

from fastapi.testclient import TestClient
from app import app
import biometrics_bot
from biometrics_bot import handle_biometrics_webhook, is_debounced, _processed_callbacks
from circadian_analysis import (
    calculate_sleep_stage_analytics,
    calculate_circadian_phase,
    calculate_nocturnal_rhr_metrics,
    calculate_dynamic_isf_modifier
)


class TestM3BiometricsAdversarial(unittest.TestCase):
    """Adversarial stress tests for biometrics_bot router and endpoints."""

    def setUp(self):
        self.client = TestClient(app)
        # Clear callback debounce cache before each test
        _processed_callbacks.clear()
        # Mock Telegram Bot Client methods to avoid network requests
        self.bot_client_mock = MagicMock()
        biometrics_bot.get_biometrics_bot_client = MagicMock(return_value=self.bot_client_mock)

    # =========================================================================
    # 1. MALFORMED WEBHOOK UPDATES
    # =========================================================================

    def test_malformed_empty_and_non_dict_updates(self):
        """Test completely empty or non-dict payloads."""
        payloads = [
            None,
            {},
            "",
            [],
            12345,
            True,
            False,
            "string_payload"
        ]
        for p in payloads:
            with self.subTest(payload=p):
                res = handle_biometrics_webhook(p)
                self.assertIsInstance(res, dict)
                self.assertEqual(res.get("status"), "ok")
                self.assertEqual(res.get("action"), "noop")

    def test_malformed_null_message(self):
        """Test update with message=None or non-dict message."""
        payloads = [
            {"message": None},
            {"message": ""},
            {"message": 123},
            {"message": []},
            {"message": False},
            {"message": {"text": None, "chat": None}},
            {"message": {"chat": {"type": None, "id": None}, "text": ""}},
            {"message": {"chat": {"type": 123, "id": "12345"}, "text": None}},
        ]
        for p in payloads:
            with self.subTest(payload=p):
                res = handle_biometrics_webhook(p)
                self.assertIsInstance(res, dict)
                self.assertIn(res.get("status"), ["ok", "ignored"])

    def test_malformed_null_callback_query(self):
        """Test update with callback_query=None or non-dict callback_query."""
        payloads = [
            {"callback_query": None},
            {"callback_query": ""},
            {"callback_query": 123},
            {"callback_query": []},
            {"callback_query": False},
            {"callback_query": "invalid_string"},
        ]
        for p in payloads:
            with self.subTest(payload=p):
                try:
                    res = handle_biometrics_webhook(p)
                    self.assertIsInstance(res, dict)
                    self.assertIn(res.get("status"), ["ok", "ignored"])
                except Exception as e:
                    self.fail(f"handle_biometrics_webhook crashed on malformed callback_query {p}: {type(e).__name__}: {e}")

    def test_malformed_callback_query_inner_fields(self):
        """Test callback_query dictionary with null or abnormal inner fields."""
        payloads = [
            {"callback_query": {}},
            {"callback_query": {"id": None, "data": None, "message": None, "from": None}},
            {"callback_query": {"id": 12345, "data": 999, "message": None}},
            {"callback_query": {"id": "cb_str", "data": None, "message": {"chat": None}}},
            {"callback_query": {"id": "cb_str2", "data": "bio:sync:now", "message": {"chat": {"id": None}}}},
            {"callback_query": {"id": "cb_str3", "data": "bio:sleep:detail", "message": "not_a_dict"}},
        ]
        for p in payloads:
            with self.subTest(payload=p):
                try:
                    res = handle_biometrics_webhook(p)
                    self.assertIsInstance(res, dict)
                    self.assertIn(res.get("status"), ["ok", "ignored"])
                except Exception as e:
                    self.fail(f"handle_biometrics_webhook crashed on inner malformed fields {p}: {type(e).__name__}: {e}")

    # =========================================================================
    # 2. FOREIGN CALLBACK NAMESPACES
    # =========================================================================

    def test_foreign_callback_namespaces_ignored(self):
        """Verify foreign namespaces (gt:, med:, mh:) are cleanly ignored with foreign_namespace_ignored."""
        foreign_cases = [
            ("gt:meal:45", "gt:"),
            ("gt:lantus:taken", "gt:"),
            ("gt:corr:2.5", "gt:"),
            ("med:log:1:10", "med:"),
            ("med:del:2", "med:"),
            ("med:custom:5", "med:"),
            ("mh:briefing:refresh", "mh:"),
            ("mh:quiet:toggle", "mh:"),
            ("mh:role:set:owner", "mh:"),
        ]
        for data, expected_prefix in foreign_cases:
            with self.subTest(callback_data=data):
                update = {
                    "callback_query": {
                        "id": f"cb_{data.replace(':', '_')}",
                        "data": data,
                        "from": {"first_name": "Alice"},
                        "message": {"chat": {"id": 12345}, "message_id": 99}
                    }
                }
                res = handle_biometrics_webhook(update)
                self.assertEqual(res.get("status"), "ignored", f"Failed for {data}")
                self.assertEqual(res.get("action"), "foreign_namespace_ignored", f"Failed for {data}")
                self.assertEqual(res.get("details", {}).get("received_prefix"), expected_prefix)
                self.assertEqual(res.get("details", {}).get("expected_prefix"), "bio:")

    def test_unrecognized_custom_namespace_noop(self):
        """Test non-bio non-foreign callback data falls back to callback_noop without crashing."""
        update = {
            "callback_query": {
                "id": "cb_unknown_999",
                "data": "unknown:action:123",
                "from": {"first_name": "Bob"},
                "message": {"chat": {"id": 12345}, "message_id": 99}
            }
        }
        res = handle_biometrics_webhook(update)
        self.assertEqual(res.get("status"), "ok")
        self.assertEqual(res.get("action"), "callback_noop")

    # =========================================================================
    # 3. GROUP CHAT NOISE FILTERING
    # =========================================================================

    def test_group_ambient_noise_ignored(self):
        """Verify non-command group chatter is ignored in group/supergroup."""
        noise_messages = [
            "Good morning team!",
            "Did anyone eat lunch yet?",
            "What is the weather like today?",
            "12345 67890",
            "bio without slash",
            "sleep without slash",
            "rhr without slash",
            "isf without slash",
            "Hey @someone look at this"
        ]
        for chat_type in ["group", "supergroup"]:
            for text in noise_messages:
                with self.subTest(chat_type=chat_type, text=text):
                    update = {
                        "message": {
                            "chat": {"id": -100123456789, "type": chat_type},
                            "text": text,
                            "from": {"id": 123, "first_name": "Charlie"}
                        }
                    }
                    res = handle_biometrics_webhook(update)
                    self.assertEqual(res.get("status"), "ignored", f"Expected ignored for text '{text}' in {chat_type}")
                    self.assertEqual(res.get("action"), "group_noise_ignored", f"Expected group_noise_ignored for '{text}'")

    def test_group_command_processing(self):
        """Verify valid bot commands and mentions are processed in group/supergroup."""
        commands = [
            ("/bio", "bio_command_response"),
            ("/sleep", "sleep_card_sent"),
            ("/rhr", "rhr_card_sent"),
            ("/isf", "isf_card_sent"),
            ("/sync", "sync_triggered"),
            ("/help", "start_menu_sent"),
            ("/menu", "start_menu_sent"),
            ("/bio@biometrics_bot", "bio_command_response"),
            ("/sleep@biometrics_bot", "sleep_card_sent"),
        ]
        for cmd, expected_action in commands:
            with self.subTest(cmd=cmd):
                update = {
                    "message": {
                        "chat": {"id": -100123456789, "type": "supergroup"},
                        "text": cmd,
                        "from": {"id": 123, "first_name": "Charlie"}
                    }
                }
                res = handle_biometrics_webhook(update)
                self.assertEqual(res.get("status"), "ok", f"Command '{cmd}' should return status ok")
                self.assertEqual(res.get("action"), expected_action, f"Command '{cmd}' action mismatch")

    def test_group_commands_for_other_bots_ignored(self):
        """Verify commands explicitly targeted at other bots (@medflowassist_bot, @monkehelper_bot) are ignored."""
        other_bot_commands = [
            "/log@medflowassist_bot",
            "/presets@medflowassist_bot",
            "/briefing@monkehelper_bot",
            "/roles@monkehelper_bot",
            "/lantus@glucotrack_bot",
        ]
        for cmd in other_bot_commands:
            with self.subTest(cmd=cmd):
                update = {
                    "message": {
                        "chat": {"id": -100123456789, "type": "group"},
                        "text": cmd,
                        "from": {"id": 123, "first_name": "Charlie"}
                    }
                }
                res = handle_biometrics_webhook(update)
                self.assertEqual(res.get("status"), "ignored", f"Command '{cmd}' should be ignored")
                self.assertEqual(res.get("action"), "command_for_other_bot", f"Command '{cmd}' should trigger command_for_other_bot")

    # =========================================================================
    # 4. RAPID DOUBLE-TAP CALLBACK DEBOUNCING
    # =========================================================================

    def test_double_tap_debouncing(self):
        """Verify that rapid successive callbacks with the same callback ID are debounced."""
        cb_id = "tap_sync_unique_id_101"
        update = {
            "callback_query": {
                "id": cb_id,
                "data": "bio:sync:now",
                "from": {"first_name": "Alice"},
                "message": {"chat": {"id": 12345}, "message_id": 888}
            }
        }

        # 1st Tap: Should execute
        res1 = handle_biometrics_webhook(update)
        self.assertEqual(res1.get("status"), "ok")
        self.assertEqual(res1.get("action"), "biometrics_synced")

        # 2nd Tap (identical ID immediately after): Should debounce
        res2 = handle_biometrics_webhook(update)
        self.assertEqual(res2.get("status"), "ok")
        self.assertEqual(res2.get("action"), "debounced")
        self.assertEqual(res2.get("details", {}).get("callback_id"), cb_id)

        # 3rd Tap: Still debounced
        res3 = handle_biometrics_webhook(update)
        self.assertEqual(res3.get("status"), "ok")
        self.assertEqual(res3.get("action"), "debounced")

    def test_different_callback_ids_not_debounced(self):
        """Verify that distinct callback IDs for the same action execute independently."""
        update1 = {
            "callback_query": {
                "id": "tap_1",
                "data": "bio:sleep:detail",
                "from": {"first_name": "Alice"},
                "message": {"chat": {"id": 12345}, "message_id": 888}
            }
        }
        update2 = {
            "callback_query": {
                "id": "tap_2",
                "data": "bio:sleep:detail",
                "from": {"first_name": "Alice"},
                "message": {"chat": {"id": 12345}, "message_id": 888}
            }
        }

        res1 = handle_biometrics_webhook(update1)
        self.assertEqual(res1.get("status"), "ok")
        self.assertEqual(res1.get("action"), "sleep_detail_shown")

        res2 = handle_biometrics_webhook(update2)
        self.assertEqual(res2.get("status"), "ok")
        self.assertEqual(res2.get("action"), "sleep_detail_shown")

    def test_debounce_ttl_expiration(self):
        """Verify that debounce cache entries expire after the TTL."""
        cb_id = "tap_expire_test"
        self.assertFalse(is_debounced(cb_id, ttl_seconds=0.1))
        # Immediately re-checking should be debounced
        self.assertTrue(is_debounced(cb_id, ttl_seconds=0.1))

        # Sleep past TTL
        time.sleep(0.15)
        # Should now allow re-execution
        self.assertFalse(is_debounced(cb_id, ttl_seconds=0.1))

    # =========================================================================
    # 5. TELEMETRY INGESTION & MATHEMATICAL BOUNDARIES
    # =========================================================================

    def test_direct_telemetry_ingestion_edge_cases(self):
        """Test telemetry ingestion with corrupted, negative, or NaN values."""
        malformed_updates = [
            {"sessions": None},
            {"metrics": None},
            {"sessions": [{"duration_minutes": float('nan')}]},
            {"sessions": [{"duration_minutes": -60.0}]},
            {"sessions": [{"start_time": "invalid_date", "end_time": "invalid_date"}]},
            {"sessions": [{"session_type": "sleep_deep", "duration_minutes": 120.0}, {"session_type": "sleep_rem", "duration_minutes": 90.0}]}
        ]
        for up in malformed_updates:
            with self.subTest(update=up):
                res = handle_biometrics_webhook(up)
                self.assertEqual(res.get("status"), "ok")
                self.assertEqual(res.get("action"), "biometrics_synced")

    def test_dynamic_isf_clamping_and_boundaries(self):
        """Test ISF modifier strictly satisfies [1.00, 1.25] clamp range under extreme inputs."""
        # Extreme sleep deprivation (0 hours) + no dipping
        m_deprived = calculate_dynamic_isf_modifier(total_sleep_hours=0.0, rhr_dipping_pct=-10.0)
        self.assertLessEqual(m_deprived["isf_modifier"], 1.25)
        self.assertGreaterEqual(m_deprived["isf_modifier"], 1.00)

        # Extreme excess sleep (20 hours) + hyper dipping (35%)
        m_excess = calculate_dynamic_isf_modifier(total_sleep_hours=20.0, rhr_dipping_pct=35.0)
        self.assertEqual(m_excess["isf_modifier"], 1.00)

        # Negative sleep hours
        m_neg = calculate_dynamic_isf_modifier(total_sleep_hours=-5.0)
        self.assertLessEqual(m_neg["isf_modifier"], 1.25)
        self.assertGreaterEqual(m_neg["isf_modifier"], 1.00)

    # =========================================================================
    # 6. FASTAPI WEBHOOK INGRESS ROUTE (/api/biometrics/webhook)
    # =========================================================================

    def test_ingress_endpoint_malformed_and_edge_cases(self):
        """Test HTTP POST /api/biometrics/webhook with various HTTP payloads."""
        # Valid update
        resp = self.client.post("/api/biometrics/webhook", json={"message": {"text": "/bio", "chat": {"id": 123, "type": "private"}}})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("status"), "ok")

        # Foreign namespace via HTTP
        resp_foreign = self.client.post("/api/biometrics/webhook", json={
            "callback_query": {
                "id": "http_foreign_1",
                "data": "med:log:1:10",
                "message": {"chat": {"id": 123}}
            }
        })
        self.assertEqual(resp_foreign.status_code, 200)
        self.assertEqual(resp_foreign.json().get("status"), "ignored")
        self.assertEqual(resp_foreign.json().get("action"), "foreign_namespace_ignored")

        # Group noise via HTTP
        resp_noise = self.client.post("/api/biometrics/webhook", json={
            "message": {
                "chat": {"id": -100111, "type": "group"},
                "text": "Hello world"
            }
        })
        self.assertEqual(resp_noise.status_code, 200)
        self.assertEqual(resp_noise.json().get("status"), "ignored")
        self.assertEqual(resp_noise.json().get("action"), "group_noise_ignored")


if __name__ == "__main__":
    unittest.main(verbosity=2)
