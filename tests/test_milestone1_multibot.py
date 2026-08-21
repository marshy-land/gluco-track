"""
tests/test_milestone1_multibot.py
Milestone 1 Verification Test Suite:
- TelegramBotClient & Token Isolation
- Callback Query Namespace Prefixing & Foreign Rejection
- Ingress Webhook Routes & Response Normalization
- MultiBotPollingManager Supervisor & Worker Resiliency
"""

import os
import sys
import threading
import requests
import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Ensure root directory is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import bot_client
from bot_client import get_bot_client, TelegramBotClient, DEFAULT_MED_BOT_TOKEN, DEFAULT_MONKE_BOT_TOKEN
import telegram_bot
import med_bot
import monke_bot
import biometrics_bot
import multi_bot_manager
from multi_bot_manager import BotPollerWorker, MultiBotPollingManager
from app import app, normalize_webhook_response


class TestMilestone1MultiBot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    # ----------------------------------------------------------------------
    # 1. Token Isolation & Bot Client Tests
    # ----------------------------------------------------------------------

    def test_bot_client_registry_and_aliases(self):
        """Verify get_bot_client resolves canonical names and aliases correctly."""
        gt_client = get_bot_client("gluco_track")
        self.assertEqual(gt_client.bot_id, "gluco_track")
        self.assertEqual(get_bot_client("telegram").bot_id, "gluco_track")
        self.assertEqual(get_bot_client("gt").bot_id, "gluco_track")

        med_client = get_bot_client("med_flow")
        self.assertEqual(med_client.bot_id, "med_flow")
        self.assertEqual(get_bot_client("medbot").bot_id, "med_flow")
        self.assertEqual(get_bot_client("med").bot_id, "med_flow")

        monke_client = get_bot_client("monke_helper")
        self.assertEqual(monke_client.bot_id, "monke_helper")
        self.assertEqual(get_bot_client("monkebot").bot_id, "monke_helper")
        self.assertEqual(get_bot_client("mh").bot_id, "monke_helper")

        bio_client = get_bot_client("biometrics")
        self.assertEqual(bio_client.bot_id, "biometrics")
        self.assertEqual(get_bot_client("bio").bot_id, "biometrics")

        with self.assertRaises(KeyError):
            get_bot_client("unknown_bot_xyz")

    def test_token_resolution_precedence(self):
        """Verify credential precedence: custom getter -> default token."""
        # Custom client with getter returning None falls back to default
        client_with_default = TelegramBotClient(
            bot_id="test_bot",
            name="Test Bot",
            token_getter=lambda: None,
            default_token="DEFAULT_12345"
        )
        self.assertEqual(client_with_default.token, "DEFAULT_12345")

        # Custom client with getter returning active token overrides default
        client_with_override = TelegramBotClient(
            bot_id="test_bot",
            name="Test Bot",
            token_getter=lambda: "OVERRIDE_67890",
            default_token="DEFAULT_12345"
        )
        self.assertEqual(client_with_override.token, "OVERRIDE_67890")

        # MedFlow and MonkeHelper have predefined default tokens
        med_client = get_bot_client("med_flow")
        self.assertTrue(med_client.token is not None and len(med_client.token) > 10)

        monke_client = get_bot_client("monke_helper")
        self.assertTrue(monke_client.token is not None and len(monke_client.token) > 10)

    @patch("requests.post")
    def test_client_api_token_binding(self, mock_post):
        """Verify TelegramBotClient API requests hit the exact URL corresponding to that bot's token."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.ok = True
        mock_post.return_value.json.return_value = {"ok": True, "result": {"message_id": 999}}

        med_client = get_bot_client("med_flow")
        med_client.send_message("Test message", chat_id="123456")

        self.assertTrue(mock_post.called)
        called_url = mock_post.call_args[0][0]
        self.assertIn(med_client.token, called_url)
        self.assertIn("/sendMessage", called_url)

    # ----------------------------------------------------------------------
    # 2. Callback Namespacing & Foreign Rejection Tests
    # ----------------------------------------------------------------------

    @patch("requests.post")
    def test_glucotrack_namespace_and_foreign_rejection(self, mock_post):
        """Verify GlucoTrack handles gt: and legacy callbacks, but immediately rejects foreign namespaces."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.ok = True
        mock_post.return_value.json.return_value = {"ok": True}

        # 1. Own namespace: gt:snooze:60
        update_gt = {
            "callback_query": {
                "id": "cb_gt_1",
                "data": "gt:snooze:60",
                "message": {"message_id": 100, "chat": {"id": 1234}},
                "from": {"first_name": "Alex"}
            }
        }
        res = telegram_bot.handle_telegram_update(update_gt)
        self.assertEqual(res.get("status"), "ok")
        self.assertEqual(res.get("action"), "snoozed")

        # 2. Legacy callback: took_lantus:13.0
        with patch("db.insert_insulin_doses"):
            update_legacy = {
                "callback_query": {
                    "id": "cb_gt_2",
                    "data": "took_lantus:13.0",
                    "message": {"message_id": 101, "chat": {"id": 1234}},
                    "from": {"first_name": "Alex"}
                }
            }
            res = telegram_bot.handle_telegram_update(update_legacy)
            self.assertEqual(res.get("status"), "ok")
            self.assertEqual(res.get("action"), "lantus_logged")

        # 3. Foreign namespace: med:log:1:10.0 -> strictly ignored
        mock_post.reset_mock()
        update_foreign_med = {
            "callback_query": {
                "id": "cb_foreign_1",
                "data": "med:log:1:10.0",
                "message": {"message_id": 102, "chat": {"id": 1234}},
                "from": {"first_name": "Alex"}
            }
        }
        res = telegram_bot.handle_telegram_update(update_foreign_med)
        self.assertEqual(res.get("status"), "ignored")
        self.assertEqual(res.get("action"), "foreign_namespace_ignored")
        # Ensure GlucoTrack NEVER attempted to answer foreign callback query
        self.assertFalse(mock_post.called)

        # 4. Foreign namespace: mh:briefing:refresh -> strictly ignored
        update_foreign_mh = {
            "callback_query": {
                "id": "cb_foreign_2",
                "data": "mh:briefing:refresh",
                "message": {"message_id": 103, "chat": {"id": 1234}},
                "from": {"first_name": "Alex"}
            }
        }
        res = telegram_bot.handle_telegram_update(update_foreign_mh)
        self.assertEqual(res.get("status"), "ignored")
        self.assertEqual(res.get("action"), "foreign_namespace_ignored")

    @patch("requests.post")
    def test_medflow_namespace_and_foreign_rejection(self, mock_post):
        """Verify MedFlow handles med: and legacy callbacks, but immediately rejects foreign namespaces."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.ok = True
        mock_post.return_value.json.return_value = {"ok": True}

        # 1. Own namespace: med:dismiss
        update_med = {
            "callback_query": {
                "id": "cb_med_1",
                "data": "med:dismiss",
                "message": {"message_id": 200, "chat": {"id": 5678}},
                "from": {"first_name": "Jordan"}
            }
        }
        res = med_bot.handle_med_webhook(update_med)
        self.assertEqual(res.get("status"), "ok")
        self.assertEqual(res.get("action"), "dismissed")

        # 2. Foreign namespace: gt:lantus:13.0 -> strictly ignored
        mock_post.reset_mock()
        update_foreign_gt = {
            "callback_query": {
                "id": "cb_foreign_3",
                "data": "gt:lantus:13.0",
                "message": {"message_id": 201, "chat": {"id": 5678}},
                "from": {"first_name": "Jordan"}
            }
        }
        res = med_bot.handle_med_webhook(update_foreign_gt)
        self.assertEqual(res.get("status"), "ignored")
        self.assertEqual(res.get("action"), "foreign_namespace_ignored")
        self.assertFalse(mock_post.called)

    @patch("requests.post")
    def test_monke_and_biometrics_foreign_rejection(self, mock_post):
        """Verify MonkeHelper and Biometrics bots reject foreign namespaces."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.ok = True
        mock_post.return_value.json.return_value = {"ok": True}

        # Monke rejects gt: and med:
        res = monke_bot.handle_monke_webhook({"callback_query": {"id": "c1", "data": "gt:lantus:13.0"}})
        self.assertEqual(res.get("status"), "ignored")

        res = monke_bot.handle_monke_webhook({"callback_query": {"id": "c2", "data": "med:log:1:10.0"}})
        self.assertEqual(res.get("status"), "ignored")

        # Biometrics rejects gt:, med:, mh:
        res = biometrics_bot.handle_biometrics_webhook({"callback_query": {"id": "c3", "data": "gt:lantus:13.0"}})
        self.assertEqual(res.get("status"), "ignored")

        res = biometrics_bot.handle_biometrics_webhook({"callback_query": {"id": "c4", "data": "mh:quiet:toggle"}})
        self.assertEqual(res.get("status"), "ignored")

    # ----------------------------------------------------------------------
    # 3. Webhook Ingress Endpoints & Normalizer Tests
    # ----------------------------------------------------------------------

    def test_normalize_webhook_response_helper(self):
        """Verify normalize_webhook_response guarantees schema contract."""
        res1 = normalize_webhook_response({"status": "ok", "action": "custom_action", "details": {"foo": "bar"}})
        self.assertEqual(res1, {"status": "ok", "action": "custom_action", "details": {"foo": "bar"}})

        res2 = normalize_webhook_response({"status": "ignored", "extra_key": 123}, default_action="default_act")
        self.assertEqual(res2["status"], "ignored")
        self.assertEqual(res2["action"], "default_act")
        self.assertEqual(res2["details"]["extra_key"], 123)

        res3 = normalize_webhook_response(None, default_action="noop")
        self.assertEqual(res3, {"status": "ok", "action": "noop", "details": {}})

    def test_all_4_webhook_routes_post(self):
        """Verify all 4 webhook routes exist and return standardized JSON."""
        # 1. GlucoTrack Webhook
        resp_gt = self.client.post("/api/telegram/webhook", json={"update_id": 1001})
        self.assertEqual(resp_gt.status_code, 200)
        data_gt = resp_gt.json()
        self.assertIn("status", data_gt)
        self.assertIn("action", data_gt)
        self.assertIn("details", data_gt)

        # 2. MedFlow Webhook
        resp_med = self.client.post("/api/medbot/webhook", json={"update_id": 1002})
        self.assertEqual(resp_med.status_code, 200)
        data_med = resp_med.json()
        self.assertIn("status", data_med)
        self.assertIn("action", data_med)
        self.assertIn("details", data_med)

        # 3. MonkeHelper Webhook
        resp_monke = self.client.post("/api/monkebot/webhook", json={"update_id": 1003})
        self.assertEqual(resp_monke.status_code, 200)
        data_monke = resp_monke.json()
        self.assertIn("status", data_monke)
        self.assertIn("action", data_monke)
        self.assertIn("details", data_monke)

        # 4. Biometrics Webhook
        resp_bio = self.client.post("/api/biometrics/webhook", json={"update_id": 1004})
        self.assertEqual(resp_bio.status_code, 200)
        data_bio = resp_bio.json()
        self.assertIn("status", data_bio)
        self.assertIn("action", data_bio)
        self.assertIn("details", data_bio)

    def test_webhook_secret_token_verification(self):
        """Verify secret token header validation."""
        with patch.dict(os.environ, {"TELEGRAM_WEBHOOK_SECRET": "secret_xyz_123"}):
            # Valid secret header
            resp_valid = self.client.post(
                "/api/telegram/webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": "secret_xyz_123"},
                json={"update_id": 2001}
            )
            self.assertEqual(resp_valid.status_code, 200)

            # Invalid secret header
            resp_invalid = self.client.post(
                "/api/telegram/webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong_secret"},
                json={"update_id": 2002}
            )
            self.assertEqual(resp_invalid.status_code, 403)

    def test_polling_management_endpoints(self):
        """Verify /api/bots/polling/status and control endpoints."""
        resp_status = self.client.get("/api/bots/polling/status")
        self.assertEqual(resp_status.status_code, 200)
        data = resp_status.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertIn("gluco_track", data.get("data", {}))
        self.assertIn("med_flow", data.get("data", {}))
        self.assertIn("monke_helper", data.get("data", {}))
        self.assertIn("biometrics", data.get("data", {}))

    # ----------------------------------------------------------------------
    # 4. MultiBotPollingManager Supervisor & Worker Resiliency Tests
    # ----------------------------------------------------------------------

    def test_worker_lifecycle_and_conflict_self_healing(self):
        """Verify BotPollerWorker starts, cleans webhook, handles 409 conflict, and stops cleanly."""
        handled_updates = []
        handled_event = threading.Event()

        def dummy_handler(update):
            handled_updates.append(update)
            handled_event.set()

        worker = BotPollerWorker(
            bot_id="test_worker",
            name="Test Worker",
            token_getter=lambda: "TEST_TOKEN_123",
            handler=dummy_handler,
            poll_timeout=1,
            client_timeout=2
        )

        with patch("multi_bot_manager.requests.post") as mock_post, patch("multi_bot_manager.requests.get") as mock_get:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"ok": True}

            # First getUpdates returns 409 Conflict, then 200 with updates
            mock_resp_409 = MagicMock()
            mock_resp_409.status_code = 409

            mock_resp_200 = MagicMock()
            mock_resp_200.status_code = 200
            mock_resp_200.json.return_value = {
                "ok": True,
                "result": [{"update_id": 501, "message": {"text": "hello"}}]
            }

            mock_get.side_effect = [mock_resp_409, mock_resp_200, requests.exceptions.Timeout("Timeout")]

            worker.start()
            self.assertTrue(worker.is_alive())

            # Wait for worker thread to process update
            handled_event.wait(timeout=2.0)

            # Stop worker
            worker.stop(timeout=2.0)
            self.assertFalse(worker.is_alive())

            # Verify deleteWebhook was called during startup/409 recovery
            self.assertTrue(mock_post.called)
            del_url = mock_post.call_args[0][0]
            self.assertIn("/deleteWebhook", del_url)
            self.assertEqual(len(handled_updates), 1)

    def test_worker_delete_webhook_direct(self):
        """Verify _delete_webhook sends drop_pending_updates=False payload."""
        worker = BotPollerWorker(
            bot_id="test_worker_2",
            name="Test Worker 2",
            token_getter=lambda: "TOKEN_XYZ",
            handler=lambda u: None
        )
        with patch("multi_bot_manager.requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"ok": True}

            res = worker._delete_webhook("TOKEN_XYZ")
            self.assertTrue(res)
            self.assertTrue(mock_post.called)
            called_url, called_kwargs = mock_post.call_args[0][0], mock_post.call_args[1]
            self.assertIn("TOKEN_XYZ/deleteWebhook", called_url)
            self.assertEqual(called_kwargs.get("json"), {"drop_pending_updates": False})


if __name__ == "__main__":
    unittest.main()
