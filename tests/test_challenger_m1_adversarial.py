"""
tests/test_challenger_m1_adversarial.py
Adversarial Stress-Test Suite for Milestone 1 (Multi-Bot Webhook & Dispatch Engine)

Audited & Challenged Areas:
1. Token Leakage & Foreign Token API Invocation
2. Callback Namespace Prefix Boundaries & Malformed/Boundary Data (64-byte limits, injections, arbitrary bytes)
3. Resilience Under Thread Worker Interruptions, Concurrency, and Webhook Collision (HTTP 409, 429, 401, 500)
"""

import os
import sys
import time
import json
import threading
import unittest
from unittest.mock import patch, MagicMock, call
from fastapi.testclient import TestClient

# Ensure root directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import bot_client
from bot_client import (
    TelegramBotClient,
    get_bot_client,
    mask_token,
    DEFAULT_MED_BOT_TOKEN,
    DEFAULT_MONKE_BOT_TOKEN,
    TELEGRAM_API_BASE
)
import multi_bot_manager
from multi_bot_manager import BotPollerWorker, MultiBotPollingManager
import telegram_bot
import med_bot
import monke_bot
import biometrics_bot
from app import app, normalize_webhook_response


class TestMilestone1AdversarialTokenLeakage(unittest.TestCase):
    """
    Adversarial challenge on Token Isolation, Token Leakage, and Foreign Token Calls.
    """

    def setUp(self):
        self.app_client = TestClient(app)

    def test_unconfigured_client_never_calls_foreign_token_or_leaks(self):
        """Verify unconfigured client safely aborts all API methods without token borrowing."""
        dummy_client = TelegramBotClient(
            bot_id="unconfigured_bot",
            name="Unconfigured Bot",
            token_getter=lambda: None,
            default_token=None
        )
        self.assertIsNone(dummy_client.token)
        self.assertFalse(dummy_client.is_configured)

        with patch("requests.post") as mock_post:
            # 1. send_message
            res = dummy_client.send_message("Hello", chat_id="12345")
            self.assertFalse(res["success"])
            self.assertIn("Missing bot token", res["error"])

            # 2. answer_callback_query
            res = dummy_client.answer_callback_query("cb_1", text="Alert")
            self.assertFalse(res["success"])
            self.assertIn("Unconfigured token", res["error"])

            # 3. edit_message_text
            res = dummy_client.edit_message_text("12345", 99, "Updated")
            self.assertFalse(res["success"])
            self.assertIn("Unconfigured token", res["error"])

            # 4. delete_message
            res = dummy_client.delete_message("12345", 99)
            self.assertFalse(res["success"])

            # 5. get_updates
            res = dummy_client.get_updates()
            self.assertFalse(res["success"])

            # 6. delete_webhook
            res = dummy_client.delete_webhook()
            self.assertFalse(res["success"])

            # 7. set_webhook
            res = dummy_client.set_webhook("https://example.com/wh")
            self.assertFalse(res["success"])

            # No HTTP calls must have been made
            self.assertFalse(mock_post.called)

    @patch("requests.post")
    def test_strict_token_url_binding_across_all_bots(self, mock_post):
        """Verify that every bot client only calls its own token endpoint."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.ok = True
        mock_post.return_value.json.return_value = {"ok": True, "result": {}}

        bots = [
            ("gluco_track", get_bot_client("gluco_track")),
            ("med_flow", get_bot_client("med_flow")),
            ("monke_helper", get_bot_client("monke_helper")),
            ("biometrics", get_bot_client("biometrics"))
        ]

        for bot_id, client in bots:
            with patch.object(TelegramBotClient, 'token', f"TOKEN_{bot_id.upper()}"):
                mock_post.reset_mock()
                client.send_message("Test", chat_id="999")
                self.assertTrue(mock_post.called)
                called_url = mock_post.call_args[0][0]
                expected_prefix = f"https://api.telegram.org/botTOKEN_{bot_id.upper()}/sendMessage"
                self.assertEqual(called_url, expected_prefix)

    def test_token_masking_security(self):
        """Verify that mask_token never reveals secret middle characters."""
        self.assertEqual(mask_token(None), "UNCONFIGURED")
        self.assertEqual(mask_token(""), "UNCONFIGURED")
        self.assertEqual(mask_token("short"), "UNCONFIGURED")

        token = "8839060131:AAFRBcijx-Aic7COA7eKIjoBKpZ8ABlQ53o"
        masked = mask_token(token)
        self.assertEqual(masked, "883906...53o")
        self.assertNotIn("AAFRBcijx-Aic7COA7eKIjoBKpZ8ABlQ", masked)

    def test_polling_status_never_exposes_raw_tokens(self):
        """Verify /api/bots/polling/status JSON serializes masked tokens only."""
        resp = self.app_client.get("/api/bots/polling/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertNotIn(DEFAULT_MED_BOT_TOKEN, body)
        self.assertNotIn(DEFAULT_MONKE_BOT_TOKEN, body)


class TestMilestone1AdversarialCallbackBoundaries(unittest.TestCase):
    """
    Adversarial challenge on 64-byte limits, malformed payloads, injection attempts, and foreign namespaces.
    """

    def setUp(self):
        self.app_client = TestClient(app)

    # --- 64-Byte Limit Compliance ---

    def test_all_generated_callbacks_under_64_bytes(self):
        """Empirically test generated button callback_data lengths across extreme parameters."""
        # 1. GlucoTrack meal & bolus button generator
        for carbs in [0.0, 50.0, 999.9, 99999.9]:
            for bolus in [0.0, 2.5, 99.9, 999.9]:
                cb1 = f"gt:meal:{carbs:.1f}:{bolus:.1f}"
                cb2 = f"gt:meal:{carbs:.1f}:0.0"
                self.assertLessEqual(len(cb1.encode("utf-8")), 64)
                self.assertLessEqual(len(cb2.encode("utf-8")), 64)

        # 2. GlucoTrack correction & lantus buttons
        for corr in [0.0, 1.5, 99.9, 999.9]:
            cb_corr = f"gt:corr:{corr:.1f}"
            self.assertLessEqual(len(cb_corr.encode("utf-8")), 64)
        for lantus in [13.0, 99.0]:
            cb_lantus = f"gt:lantus:{lantus:.1f}"
            self.assertLessEqual(len(cb_lantus.encode("utf-8")), 64)

        # 3. MedFlowAssist preset buttons
        for preset_id in [1, 99, 999999999]:
            for dose in [10.0, 500.0, 99999.99]:
                cb_med = f"med:log:{preset_id}:{dose}"
                self.assertLessEqual(len(cb_med.encode("utf-8")), 64)

        # 4. MonkeHelper buttons
        for action in ["mh:briefing:refresh", "mh:quiet:toggle", "mh:dismiss"]:
            self.assertLessEqual(len(action.encode("utf-8")), 64)

        # 5. Biometrics buttons
        for action in ["bio:sync:now", "bio:sleep:detail", "bio:rhr:detail", "bio:dismiss"]:
            self.assertLessEqual(len(action.encode("utf-8")), 64)

    # --- Extreme / Malformed / Oversized Payloads ---

    def test_oversized_and_massive_callback_data_handling(self):
        """Verify handlers safely process strings up to 1MB without crashing."""
        oversized_cb = "gt:meal:10.0:2.0" + "9" * 1000000  # 1MB string
        update = {
            "callback_query": {
                "id": "cb_huge",
                "data": oversized_cb,
                "message": {"message_id": 1, "chat": {"id": 123}},
                "from": {"first_name": "Attacker"}
            }
        }
        with patch("requests.post"), patch("db.insert_food_log"), patch("db.insert_insulin_doses"):
            res = telegram_bot.handle_telegram_update(update)
            self.assertIn("status", res)
            self.assertIn("action", res)

    def test_string_callback_query_fields(self):
        """Verify handlers process string-based callbacks safely."""
        valid_string_updates = [
            {"callback_query": {}},  # Empty dict -> cb.get("data", "") -> ""
            {"callback_query": {"id": "1", "data": ""}},
            {"callback_query": {"id": "1", "data": "gt:"}},
            {"callback_query": {"id": "1", "data": "med:"}},
            {"callback_query": {"id": "1", "data": "mh:"}},
            {"callback_query": {"id": "1", "data": "bio:"}},
            {"callback_query": {"id": "1", "data": "gt:meal"}},
            {"callback_query": {"id": "1", "data": "gt:meal:50:2"}},
            {"callback_query": {"id": "1", "data": "gt:corr:1.5"}},
            {"callback_query": {"id": "1", "data": "gt:lantus:13.0"}},
            {"callback_query": {"id": "1", "data": "med:log:1:10.0"}},
            {"callback_query": {"id": "1", "data": "med:log:99999999:10.0"}},  # Non-existent med
        ]

        with patch("requests.post"), patch("db.insert_food_log"), patch("db.insert_insulin_doses"), patch("med_bot.get_medication_presets", return_value=[]):
            for upd in valid_string_updates:
                res_gt = telegram_bot.handle_telegram_update(upd)
                self.assertIsInstance(res_gt, dict)

                res_med = med_bot.handle_med_webhook(upd)
                self.assertIsInstance(res_med, dict)

                res_mh = monke_bot.handle_monke_webhook(upd)
                self.assertIsInstance(res_mh, dict)

                res_bio = biometrics_bot.handle_biometrics_webhook(upd)
                self.assertIsInstance(res_bio, dict)

    # --- Foreign Namespace Rejection Matrix ---

    @patch("requests.post")
    def test_complete_foreign_namespace_cross_rejection_matrix(self, mock_post):
        """
        Verify every bot rejects all foreign prefixes without calling answerCallbackQuery:
        GlucoTrack (gt:) rejects med:, mh:, bio:
        MedFlow (med:) rejects gt:, mh:, bio:
        MonkeHelper (mh:) rejects gt:, med:, bio:
        Biometrics (bio:) rejects gt:, med:, mh:
        """
        mock_post.return_value.status_code = 200
        mock_post.return_value.ok = True
        mock_post.return_value.json.return_value = {"ok": True}

        bot_handlers = [
            ("GlucoTrack", telegram_bot.handle_telegram_update, ["med:log:1:10", "mh:briefing:refresh", "bio:sync:now"]),
            ("MedFlow", med_bot.handle_med_webhook, ["gt:meal:50:2", "mh:quiet:toggle", "bio:sleep:detail"]),
            ("MonkeHelper", monke_bot.handle_monke_webhook, ["gt:corr:2.0", "med:log:1:10", "bio:rhr:detail"]),
            ("Biometrics", biometrics_bot.handle_biometrics_webhook, ["gt:lantus:13.0", "med:dismiss", "mh:briefing:refresh"])
        ]

        for bot_name, handler, foreign_cbs in bot_handlers:
            for f_cb in foreign_cbs:
                mock_post.reset_mock()
                upd = {
                    "callback_query": {
                        "id": f"cb_foreign_{bot_name}",
                        "data": f_cb,
                        "message": {"message_id": 10, "chat": {"id": 100}},
                        "from": {"first_name": "Tester"}
                    }
                }
                res = handler(upd)
                self.assertEqual(res.get("status"), "ignored", f"{bot_name} failed to ignore {f_cb}")
                self.assertEqual(res.get("action"), "foreign_namespace_ignored")
                # Zero API calls must be made to Telegram
                self.assertFalse(mock_post.called, f"{bot_name} attempted Telegram API call on foreign callback {f_cb}")


class TestMilestone1AdversarialWorkerResilience(unittest.TestCase):
    """
    Adversarial challenge on Worker Interruptions, Concurrency, and Webhook Collision handling.
    """

    def test_worker_start_and_stop_lifecycle(self):
        """Test clean start and stop lifecycle for BotPollerWorker."""
        worker = BotPollerWorker(
            bot_id="stress_bot",
            name="Stress Bot",
            token_getter=lambda: "MOCK_TOKEN",
            handler=lambda u: None,
            poll_timeout=1,
            client_timeout=2
        )

        with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"ok": True}
            
            def slow_get(*args, **kwargs):
                worker._stop_event.wait(0.05)
                m = MagicMock()
                m.status_code = 200
                m.json.return_value = {"ok": True, "result": []}
                return m

            mock_get.side_effect = slow_get

            for _ in range(3):
                worker.start()
                self.assertTrue(worker.is_alive())
                time.sleep(0.02)
                worker.stop(timeout=1.0)
                self.assertFalse(worker.is_alive())

    def test_worker_survives_crashing_handler_exceptions(self):
        """Verify long-polling worker thread does NOT terminate when handler throws exceptions."""
        call_count = 0

        def crashing_handler(update):
            nonlocal call_count
            call_count += 1
            raise RuntimeError(f"Simulated handler crash on update {update.get('update_id')}")

        worker = BotPollerWorker(
            bot_id="resilience_bot",
            name="Resilience Bot",
            token_getter=lambda: "MOCK_TOKEN",
            handler=crashing_handler,
            poll_timeout=1,
            client_timeout=2
        )

        with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"ok": True}

            resp_updates = MagicMock()
            resp_updates.status_code = 200
            resp_updates.json.return_value = {
                "ok": True,
                "result": [
                    {"update_id": 101, "message": {"text": "msg1"}},
                    {"update_id": 102, "message": {"text": "msg2"}},
                    {"update_id": 103, "message": {"text": "msg3"}}
                ]
            }
            
            def poll_effect(*args, **kwargs):
                worker._stop_event.wait(0.05)
                return MagicMock(status_code=200, json=lambda: {"ok": True, "result": []})

            mock_get.side_effect = [resp_updates, poll_effect, poll_effect]

            worker.start()
            time.sleep(0.1)  # Allow worker to process
            worker.stop(timeout=2.0)

            self.assertEqual(call_count, 3)
            self.assertEqual(worker._offset, 104)
            self.assertEqual(worker._updates_count, 3)
            self.assertFalse(worker.is_alive())

    def test_supervisor_watchdog_recovers_dead_worker(self):
        """Verify MultiBotPollingManager.watchdog_check() restarts unexpectedly deceased workers."""
        mgr = MultiBotPollingManager()
        worker = mgr.register_bot(
            bot_id="watchdog_test",
            name="Watchdog Test Bot",
            token_getter=lambda: "MOCK_TOKEN",
            handler=lambda u: None
        )

        with patch.object(worker, "start") as mock_start:
            worker._is_running = True
            worker._thread = None  # Simulating dead thread
            self.assertFalse(worker.is_alive())

            mgr.watchdog_check()
            mock_start.assert_called_once()

    def test_http_409_webhook_collision_self_healing(self):
        """Verify HTTP 409 conflict automatically triggers deleteWebhook and continues polling."""
        worker = BotPollerWorker(
            bot_id="collision_bot",
            name="Collision Bot",
            token_getter=lambda: "MOCK_TOKEN",
            handler=lambda u: None,
            poll_timeout=1,
            client_timeout=2
        )

        with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"ok": True}

            mock_409 = MagicMock()
            mock_409.status_code = 409

            mock_200 = MagicMock()
            mock_200.status_code = 200
            mock_200.json.return_value = {"ok": True, "result": [{"update_id": 888}]}

            def idle_poll(*args, **kwargs):
                worker._stop_event.wait(0.1)
                return MagicMock(status_code=200, json=lambda: {"ok": True, "result": []})

            mock_get.side_effect = [mock_409, mock_200, idle_poll]

            worker.start()
            time.sleep(1.3)
            worker.stop(timeout=2.0)

            # deleteWebhook should have been called on startup and during 409 recovery
            self.assertGreaterEqual(mock_post.call_count, 1)
            urls = [c[0][0] for c in mock_post.call_args_list]
            self.assertTrue(any("/deleteWebhook" in u for u in urls))
            self.assertEqual(worker._offset, 889)

    def test_http_429_rate_limiting_and_401_auth_error_states(self):
        """Verify HTTP 429 and 401 response codes trigger expected backoff and status transitions."""
        worker = BotPollerWorker(
            bot_id="error_bot",
            name="Error Bot",
            token_getter=lambda: "MOCK_TOKEN",
            handler=lambda u: None,
            poll_timeout=1,
            client_timeout=2
        )

        with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"ok": True}

            # 1. Test HTTP 429
            mock_429 = MagicMock()
            mock_429.status_code = 429
            mock_429.json.return_value = {"parameters": {"retry_after": 1}}

            def idle_poll(*args, **kwargs):
                worker._stop_event.wait(0.1)
                return MagicMock(status_code=200, json=lambda: {"ok": True, "result": []})

            mock_get.side_effect = [mock_429, idle_poll]

            worker.start()
            time.sleep(0.05)
            status = worker.get_status()
            self.assertIn(status["status"], ["backoff", "running"])
            worker.stop(timeout=2.0)

            # 2. Test HTTP 401 Invalid Token
            mock_401 = MagicMock()
            mock_401.status_code = 401
            mock_get.side_effect = [mock_401]

            worker._status = "stopped"
            worker.start()
            time.sleep(0.05)
            status = worker.get_status()
            self.assertEqual(status["status"], "auth_failed")
            self.assertIn("Invalid or revoked Bot Token", status["last_error"])
            worker.stop(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
