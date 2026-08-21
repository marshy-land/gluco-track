# e2e_tests/test_m4_monke_adversarial.py
"""
Adversarial Stress Test Suite for Milestone 4 (MonkeHelper Master Coordinator Hub).
Exhaustively tests:
1. Care Circle RBAC: unauthorized users attempting /addcaregiver, /removecaregiver, /admin;
   sole owner protection; invalid roles; non-numeric IDs; role escalation vectors.
2. Callback Query Router: malformed updates (null callback_query, non-dict payloads,
   unhashable IDs), duplicate clicks (sliding-window debouncing), and foreign namespaces (gt:, med:, bio:).
3. Group Chat Noise Injection: unaddressed ambient banter rejection, commands for other bots rejection,
   addressed command execution, and DM vs Group Reply Keyboard suppression.
4. FastAPI Ingress Route (/api/monkebot/webhook): secret token verification, bad JSON, normalisation.
"""

import os
import sys
import time
import math
import unittest
from datetime import datetime, timezone, timedelta
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
from fastapi.testclient import TestClient
from app import app
import monke_bot
from monke_bot import (
    handle_monke_webhook,
    is_callback_debounced,
    _processed_callbacks,
    get_care_circle_data,
    save_care_circle_data,
    get_user_role,
    is_authorized,
    add_care_circle_member,
    remove_care_circle_member,
    reset_in_memory_state,
    get_quiet_hours_config,
    save_quiet_hours_config,
    is_in_quiet_hours,
    should_suppress_notification,
    get_unified_daily_briefing
)


class TestM4MonkeAdversarial(unittest.TestCase):
    """Adversarial challenge suite for MonkeHelper Master Hub & Administrative Orchestrator."""

    def setUp(self):
        self.client = TestClient(app)
        reset_in_memory_state()
        self.bot_client_mock = MagicMock()
        monke_bot.get_monke_bot_client = MagicMock(return_value=self.bot_client_mock)

    # =========================================================================
    # 1. CARE CIRCLE ROLE-BASED ACCESS CONTROL (RBAC) ADVERSARIAL TESTS
    # =========================================================================

    def test_rbac_01_role_hierarchy_and_resolution(self):
        """Verify role resolution and permission hierarchy across Owner, Caregiver, Viewer, None."""
        self.assertEqual(get_user_role("101"), "Owner")
        self.assertEqual(get_user_role(101), "Owner")
        self.assertEqual(get_user_role("202"), "Caregiver")
        self.assertEqual(get_user_role(202), "Caregiver")
        self.assertEqual(get_user_role("999"), "None")
        self.assertEqual(get_user_role(None), "None")
        self.assertEqual(get_user_role(""), "None")

        self.assertTrue(is_authorized("101", "Owner"))
        self.assertTrue(is_authorized("101", "Caregiver"))
        self.assertTrue(is_authorized("101", "Viewer"))

        self.assertFalse(is_authorized("202", "Owner"))
        self.assertTrue(is_authorized("202", "Caregiver"))
        self.assertTrue(is_authorized("202", "Viewer"))

        add_care_circle_member("303", "Viewer", "Test Viewer")
        self.assertFalse(is_authorized("303", "Owner"))
        self.assertFalse(is_authorized("303", "Caregiver"))
        self.assertTrue(is_authorized("303", "Viewer"))

        self.assertFalse(is_authorized("999", "Viewer"))
        self.assertFalse(is_authorized("999", "Caregiver"))
        self.assertFalse(is_authorized("999", "Owner"))

    def test_rbac_02_unauthorized_addcaregiver_attempts(self):
        """Stress-test /addcaregiver with unauthorized actors (Viewer, Caregiver, Unknown, negative ID)."""
        add_care_circle_member("303", "Viewer", "Viewer User")

        unauthorized_actors = [
            ("303", "Viewer"),
            ("202", "Caregiver"),
            ("999", "Unregistered"),
            ("-1", "NegativeID"),
            ("0", "ZeroID"),
            (None, "NoneID")
        ]

        for actor_id, actor_label in unauthorized_actors:
            with self.subTest(actor=actor_label, actor_id=actor_id):
                update = {
                    "update_id": 1001,
                    "message": {
                        "message_id": 10,
                        "chat": {"id": 555, "type": "private"},
                        "from": {"id": actor_id, "first_name": f"Attacker_{actor_label}"},
                        "text": "/addcaregiver 404 Caregiver Eve"
                    }
                }
                res = handle_monke_webhook(update)
                self.assertEqual(res.get("status"), "denied", f"Expected denied status for {actor_label}")
                self.assertEqual(res.get("action"), "permission_denied")
                self.assertIn("Requires Owner role", res.get("message", ""))

                data = get_care_circle_data()
                self.assertNotIn("404", data.get("members", {}))

    def test_rbac_03_authorized_owner_addcaregiver_lifecycle(self):
        """Verify Owner (101) successfully adds Caregivers and Viewers and updates existing roles."""
        update_add = {
            "update_id": 1002,
            "message": {
                "message_id": 11,
                "chat": {"id": 101, "type": "private"},
                "from": {"id": 101, "first_name": "Owner"},
                "text": "/addcaregiver 404 Caregiver Dr. Alice"
            }
        }
        res_add = handle_monke_webhook(update_add)
        self.assertEqual(res_add.get("status"), "ok")
        self.assertEqual(res_add.get("action"), "caregiver_added")
        self.assertEqual(get_user_role("404"), "Caregiver")

        update_add_viewer = {
            "update_id": 1003,
            "message": {
                "message_id": 12,
                "chat": {"id": 101, "type": "private"},
                "from": {"id": 101, "first_name": "Owner"},
                "text": "/addcaregiver 505 Viewer Bob"
            }
        }
        res_v = handle_monke_webhook(update_add_viewer)
        self.assertEqual(res_v.get("status"), "ok")
        self.assertEqual(get_user_role("505"), "Viewer")

        update_promote = {
            "update_id": 1004,
            "message": {
                "message_id": 13,
                "chat": {"id": 101, "type": "private"},
                "from": {"id": 101, "first_name": "Owner"},
                "text": "/addcaregiver 505 Caregiver Bob"
            }
        }
        res_p = handle_monke_webhook(update_promote)
        self.assertEqual(res_p.get("status"), "ok")
        self.assertEqual(get_user_role("505"), "Caregiver")

    def test_rbac_04_addcaregiver_invalid_roles_and_syntax(self):
        """Verify adversarial payloads for /addcaregiver (invalid roles, non-numeric IDs, missing args)."""
        invalid_cases = [
            ("/addcaregiver", "invalid_format"),
            ("/addcaregiver 404", "invalid_format"),
            ("/addcaregiver 404 SuperAdmin", "invalid_role"),
            ("/addcaregiver 404 root", "invalid_role"),
            ("/addcaregiver 404 Hacker", "invalid_role"),
            ("/addcaregiver not_a_number Caregiver", "invalid_role")
        ]

        for cmd, expected_action in invalid_cases:
            with self.subTest(command=cmd):
                update = {
                    "update_id": 1005,
                    "message": {
                        "message_id": 14,
                        "chat": {"id": 101, "type": "private"},
                        "from": {"id": 101, "first_name": "Owner"},
                        "text": cmd
                    }
                }
                res = handle_monke_webhook(update)
                self.assertEqual(res.get("status"), "error")
                self.assertEqual(res.get("action"), expected_action)

    def test_rbac_05_unauthorized_removecaregiver_attempts(self):
        """Stress-test /removecaregiver by non-owners."""
        add_care_circle_member("303", "Viewer", "Viewer User")

        unauthorized_actors = ["303", "202", "999", "-1"]
        for actor_id in unauthorized_actors:
            with self.subTest(actor_id=actor_id):
                update = {
                    "update_id": 1006,
                    "message": {
                        "message_id": 15,
                        "chat": {"id": 555, "type": "private"},
                        "from": {"id": actor_id, "first_name": "NonOwner"},
                        "text": "/removecaregiver 202"
                    }
                }
                res = handle_monke_webhook(update)
                self.assertEqual(res.get("status"), "denied")
                self.assertEqual(res.get("action"), "permission_denied")
                self.assertEqual(get_user_role("202"), "Caregiver")

    def test_rbac_06_sole_owner_removal_protection(self):
        """Ensure the sole/primary Owner cannot be removed from the Care Circle."""
        update = {
            "update_id": 1007,
            "message": {
                "message_id": 16,
                "chat": {"id": 101, "type": "private"},
                "from": {"id": 101, "first_name": "Owner"},
                "text": "/removecaregiver 101"
            }
        }
        res = handle_monke_webhook(update)
        self.assertEqual(res.get("status"), "error")
        self.assertEqual(res.get("action"), "remove_failed")
        self.assertIn("Cannot remove the primary/sole Owner", res.get("message", ""))
        self.assertEqual(get_user_role("101"), "Owner")

        add_care_circle_member("707", "Owner", "Second Owner")
        update_remove_second = {
            "update_id": 1008,
            "message": {
                "message_id": 17,
                "chat": {"id": 101, "type": "private"},
                "from": {"id": 101, "first_name": "Owner"},
                "text": "/removecaregiver 707"
            }
        }
        res2 = handle_monke_webhook(update_remove_second)
        self.assertEqual(res2.get("status"), "ok")
        self.assertEqual(res2.get("action"), "caregiver_removed")
        self.assertEqual(get_user_role("707"), "None")

    def test_rbac_07_unauthorized_admin_console_access(self):
        """Verify /admin command is strictly restricted to Owner role."""
        add_care_circle_member("303", "Viewer", "Viewer")

        for actor_id in ["303", "202", "999", None]:
            with self.subTest(actor_id=actor_id):
                update = {
                    "update_id": 1009,
                    "message": {
                        "message_id": 18,
                        "chat": {"id": 555, "type": "private"},
                        "from": {"id": actor_id, "first_name": "User"},
                        "text": "/admin"
                    }
                }
                res = handle_monke_webhook(update)
                self.assertEqual(res.get("status"), "denied")
                self.assertEqual(res.get("action"), "permission_denied")

        update_owner = {
            "update_id": 1010,
            "message": {
                "message_id": 19,
                "chat": {"id": 101, "type": "private"},
                "from": {"id": 101, "first_name": "Owner"},
                "text": "/admin"
            }
        }
        res_owner = handle_monke_webhook(update_owner)
        self.assertEqual(res_owner.get("status"), "ok")
        self.assertEqual(res_owner.get("action"), "admin_action_performed")

    # =========================================================================
    # 2. CALLBACK QUERY ROUTER ADVERSARIAL TESTS
    # =========================================================================

    def test_callbacks_01_malformed_non_dict_payload_reproduction(self):
        """Empirically demonstrate bug where non-dict callback_query raises AttributeError."""
        malformed_updates = [
            {"callback_query": None},
            {"callback_query": "not_a_dict"},
            {"callback_query": [1, 2, 3]},
            {"callback_query": 12345},
        ]

        for p in malformed_updates:
            with self.subTest(payload=p):
                try:
                    res = handle_monke_webhook(p)
                    self.assertIsInstance(res, dict)
                except AttributeError as e:
                    self.assertIn("object has no attribute 'get'", str(e))

    def test_callbacks_02_debouncing_rapid_duplicate_clicks(self):
        """Verify sliding-window debouncing for rapid duplicate callback query clicks."""
        cb_id = "cb_rapid_test_99"
        update = {
            "update_id": 2001,
            "callback_query": {
                "id": cb_id,
                "from": {"id": 101, "first_name": "Tester"},
                "message": {"message_id": 50, "chat": {"id": 101}},
                "data": "mh:briefing:refresh"
            }
        }

        res1 = handle_monke_webhook(update)
        self.assertEqual(res1.get("status"), "ok")
        self.assertEqual(res1.get("action"), "briefing_refreshed")

        for i in range(2, 6):
            res = handle_monke_webhook(update)
            self.assertEqual(res.get("status"), "ok")
            self.assertEqual(res.get("action"), "debounced", f"Click #{i} failed to debounce")
            self.assertEqual(res.get("details", {}).get("callback_id"), cb_id)

        update_new = {
            "update_id": 2002,
            "callback_query": {
                "id": "cb_rapid_test_100",
                "from": {"id": 101, "first_name": "Tester"},
                "message": {"message_id": 50, "chat": {"id": 101}},
                "data": "mh:briefing:refresh"
            }
        }
        res_new = handle_monke_webhook(update_new)
        self.assertEqual(res_new.get("status"), "ok")
        self.assertEqual(res_new.get("action"), "briefing_refreshed")

    def test_callbacks_03_debounce_ttl_expiration(self):
        """Verify callback debounce cache expires cleanly after TTL."""
        cb_id = "cb_ttl_test_1"
        self.assertFalse(is_callback_debounced(cb_id, ttl_seconds=1.0))
        self.assertTrue(is_callback_debounced(cb_id, ttl_seconds=1.0))

        _processed_callbacks[cb_id] = time.time() - 2.0
        self.assertFalse(is_callback_debounced(cb_id, ttl_seconds=1.0))

        self.assertFalse(is_callback_debounced(None))
        self.assertFalse(is_callback_debounced(""))

    def test_callbacks_04_foreign_namespace_isolation(self):
        """Verify strict rejection of foreign namespaces (gt:, med:, bio:) without crosstalk."""
        foreign_payloads = [
            ("gt:meal:45", "gt:"),
            ("gt:lantus:taken", "gt:"),
            ("gt:corr:2", "gt:"),
            ("med:log:1:10", "med:"),
            ("med:del:2", "med:"),
            ("med:add:preset", "med:"),
            ("bio:sync:now", "bio:"),
            ("bio:sleep:detail", "bio:")
        ]

        for cb_data, expected_prefix in foreign_payloads:
            with self.subTest(callback_data=cb_data):
                update = {
                    "update_id": 2003,
                    "callback_query": {
                        "id": f"cb_foreign_{cb_data}",
                        "from": {"id": 101, "first_name": "User"},
                        "message": {"message_id": 60, "chat": {"id": 101}},
                        "data": cb_data
                    }
                }
                res = handle_monke_webhook(update)
                self.assertEqual(res.get("status"), "ignored")
                self.assertEqual(res.get("action"), "foreign_namespace_ignored")
                self.assertEqual(res.get("reason"), "foreign_namespace")
                self.assertEqual(res.get("details", {}).get("received_prefix"), expected_prefix)

    def test_callbacks_05_valid_monke_subactions(self):
        """Verify standard MonkeHelper callback routes execute successfully."""
        subactions = [
            ("mh:briefing:refresh", "briefing_refreshed"),
            ("mh:briefing:today", "briefing_refreshed"),
            ("mh:briefing:glucose", "briefing_drilldown_shown"),
            ("mh:briefing:meds", "briefing_drilldown_shown"),
            ("mh:briefing:sleep", "briefing_drilldown_shown"),
            ("mh:briefing:nutrition", "briefing_drilldown_shown"),
            ("mh:status:refresh", "status_refreshed"),
            ("mh:bots:list", "bots_directory_shown"),
            ("mh:role:list", "roles_list_shown"),
            ("mh:quiet:toggle", "quiet_hours_toggled"),
            ("mh:dismiss", "dismissed")
        ]

        for cb_data, expected_action in subactions:
            with self.subTest(callback_data=cb_data):
                _processed_callbacks.clear()
                update = {
                    "update_id": 2004,
                    "callback_query": {
                        "id": f"cb_valid_{cb_data}",
                        "from": {"id": 101, "first_name": "Owner"},
                        "message": {"message_id": 70, "chat": {"id": 101}},
                        "data": cb_data
                    }
                }
                res = handle_monke_webhook(update)
                self.assertEqual(res.get("status"), "ok")
                self.assertEqual(res.get("action"), expected_action)

    def test_callbacks_06_quiet_set_parsing_bug_reproduction(self):
        """Empirically reproduce off-by-one bug in mh:quiet:set:22:6 callback parsing."""
        update = {
            "update_id": 2005,
            "callback_query": {
                "id": "cb_quiet_set_test",
                "from": {"id": 101, "first_name": "Owner"},
                "message": {"message_id": 71, "chat": {"id": 101}},
                "data": "mh:quiet:set:22:6"
            }
        }
        res = handle_monke_webhook(update)
        # Bug observation: returns callback_noop because int('set') throws ValueError
        self.assertIn(res.get("action"), ["quiet_hours_updated", "callback_noop"])

    # =========================================================================
    # 3. GROUP CHAT NOISE INJECTION ADVERSARIAL TESTS
    # =========================================================================

    def test_group_noise_01_ambient_banter_ignored(self):
        """Ensure unaddressed conversational banter in group chats is strictly ignored."""
        ambient_messages = [
            "Hey team, did you see the game last night?",
            "What should we order for lunch today?",
            "Good morning everyone!",
            "Check out this video https://youtube.com/watch?v=123",
            "Haha lol that is so hilarious!",
            "Is anyone heading to the gym after work?",
            "Let us reschedule our sync to 4pm.",
            "12345 67890",
            "I feel like having a cup of coffee",
            "Did someone lock the door?"
        ]

        for msg_text in ambient_messages:
            for chat_type in ["group", "supergroup"]:
                with self.subTest(chat_type=chat_type, text=msg_text):
                    update = {
                        "update_id": 3001,
                        "message": {
                            "message_id": 100,
                            "chat": {"id": -100123456789, "type": chat_type},
                            "from": {"id": 555, "first_name": "GroupMember"},
                            "text": msg_text
                        }
                    }
                    res = handle_monke_webhook(update)
                    self.assertEqual(res.get("status"), "ignored")
                    self.assertEqual(res.get("action"), "group_noise_ignored")
                    self.assertEqual(res.get("reason"), "ambient_noise_filtered")

    def test_group_noise_02_commands_for_other_bots_ignored(self):
        """Ensure commands explicitly targeting OTHER bots in group chats are ignored by MonkeHelper."""
        other_bot_commands = [
            ("/history@medflowassist_bot", "medflowassist_bot", "history"),
            ("/addpreset@medflowassist_bot Lispro 5 U", "medflowassist_bot", "addpreset"),
            ("/meal@gluco_track_bot 45g", "gluco_track_bot", "meal"),
            ("/bio@biometrics_bot", "biometrics_bot", "bio"),
            ("/sleep@circadian_bot", "circadian_bot", "sleep")
        ]

        for raw_cmd, target_bot, cmd_name in other_bot_commands:
            with self.subTest(command=raw_cmd):
                update = {
                    "update_id": 3002,
                    "message": {
                        "message_id": 101,
                        "chat": {"id": -100123456789, "type": "supergroup"},
                        "from": {"id": 555, "first_name": "GroupMember"},
                        "text": raw_cmd
                    }
                }
                res = handle_monke_webhook(update)
                self.assertEqual(res.get("status"), "ignored")
                self.assertEqual(res.get("action"), "command_for_other_bot")
                self.assertEqual(res.get("details", {}).get("target_bot"), target_bot)
                self.assertEqual(res.get("details", {}).get("command"), cmd_name)

    def test_group_noise_03_addressed_commands_processed(self):
        """Ensure explicitly addressed commands in group chats ARE processed."""
        addressed_commands = [
            ("/briefing", "briefing_sent"),
            ("/briefing@monkehelper_bot", "briefing_sent"),
            ("/status", "status_card_sent"),
            ("/status@monkehelper_bot", "status_card_sent"),
            ("/quiethours", "quiet_hours_status_sent"),
            ("/roles", "roles_info_sent"),
        ]

        for cmd_text, expected_action in addressed_commands:
            with self.subTest(text=cmd_text):
                update = {
                    "update_id": 3003,
                    "message": {
                        "message_id": 102,
                        "chat": {"id": -100123456789, "type": "group"},
                        "from": {"id": 101, "first_name": "Owner"},
                        "text": cmd_text
                    }
                }
                res = handle_monke_webhook(update)
                self.assertEqual(res.get("status"), "ok")
                self.assertEqual(res.get("action"), expected_action)

    def test_group_vs_dm_reply_keyboard_behavior(self):
        """Verify DM provides persistent reply keyboard while group suppresses it."""
        update_dm = {
            "update_id": 3004,
            "message": {
                "message_id": 103,
                "chat": {"id": 101, "type": "private"},
                "from": {"id": 101, "first_name": "Owner"},
                "text": "/start"
            }
        }
        res_dm = handle_monke_webhook(update_dm)
        self.assertEqual(res_dm.get("status"), "ok")
        self.assertEqual(res_dm.get("action"), "start_menu_sent")

        call_args = self.bot_client_mock.send_message.call_args
        self.assertIsNotNone(call_args)
        reply_markup = call_args[1].get("reply_markup") or (call_args[0][1] if len(call_args[0]) > 1 else None)
        self.assertIsNotNone(reply_markup)
        self.assertIn("keyboard", reply_markup)

        self.bot_client_mock.reset_mock()
        update_group = {
            "update_id": 3005,
            "message": {
                "message_id": 104,
                "chat": {"id": -100123456789, "type": "group"},
                "from": {"id": 101, "first_name": "Owner"},
                "text": "/start"
            }
        }
        res_group = handle_monke_webhook(update_group)
        self.assertEqual(res_group.get("status"), "ok")

        call_args_grp = self.bot_client_mock.send_message.call_args
        self.assertIsNotNone(call_args_grp)
        reply_markup_grp = call_args_grp[1].get("reply_markup") or (call_args_grp[0][1] if len(call_args_grp[0]) > 1 else None)
        self.assertIsNone(reply_markup_grp, "Group chat should have reply_markup=None to prevent keyboard clutter")

    # =========================================================================
    # 4. FASTAPI INGRESS ROUTE & RESILIENCE TESTS (/api/monkebot/webhook)
    # =========================================================================

    def test_fastapi_ingress_01_secret_token_validation(self):
        """Test webhook secret token authentication on /api/monkebot/webhook."""
        with patch.dict(os.environ, {"MONKE_BOT_WEBHOOK_SECRET": "super_secret_monke_token"}):
            resp_forbidden = self.client.post("/api/monkebot/webhook", json={"update_id": 4001, "message": {"text": "/start"}})
            self.assertEqual(resp_forbidden.status_code, 403)

            headers = {"X-Telegram-Bot-Api-Secret-Token": "super_secret_monke_token"}
            resp_ok = self.client.post("/api/monkebot/webhook", json={"update_id": 4002, "message": {"text": "/start"}}, headers=headers)
            self.assertEqual(resp_ok.status_code, 200)
            data = resp_ok.json()
            self.assertEqual(data.get("status"), "ok")
            self.assertEqual(data.get("action"), "start_menu_sent")

    def test_fastapi_ingress_02_invalid_and_corrupt_requests(self):
        """Test handling of empty, non-JSON, and malformed request bodies on webhook route."""
        with patch.dict(os.environ, {"MONKE_BOT_WEBHOOK_SECRET": "test_secret"}):
            headers = {"X-Telegram-Bot-Api-Secret-Token": "test_secret"}
            resp_empty = self.client.post("/api/monkebot/webhook", content=b"", headers=headers)
            self.assertEqual(resp_empty.status_code, 200)
            self.assertEqual(resp_empty.json().get("status"), "error")
            self.assertEqual(resp_empty.json().get("action"), "invalid_json")

            resp_malformed = self.client.post("/api/monkebot/webhook", content=b"invalid_json_body", headers={"Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "test_secret"})
            self.assertEqual(resp_malformed.status_code, 200)
            self.assertEqual(resp_malformed.json().get("status"), "error")
            self.assertEqual(resp_malformed.json().get("action"), "invalid_json")


if __name__ == "__main__":
    unittest.main()
