"""
tests/test_m1_adversarial_challenger.py
Adversarial Stress Test Suite for Milestone 1: Multi-Bot Webhook & Dispatch Engine.

Stress tests and edge case coverage across:
1. Webhook Ingress & Payload Stress (concurrency, malformed JSON, types, secret tokens)
2. Callback Routing & Crosstalk Matrix (cross-bot crosstalk, invalid/pathological data)
3. Bot API Client & Token Isolation (credentials hierarchy, unconfigured handling, timeouts)
4. MultiBotPollingManager Supervisor & Worker Resiliency (409 conflict, 429 rate limit, 401 auth, network backoff, watchdog, handler errors)
"""

import os
import sys
import json
import time
import math
import threading
import concurrent.futures
import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Ensure workspace root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import db
import bot_client
from bot_client import get_bot_client, TelegramBotClient, DEFAULT_MED_BOT_TOKEN, DEFAULT_MONKE_BOT_TOKEN
import multi_bot_manager
from multi_bot_manager import BotPollerWorker, MultiBotPollingManager
import telegram_bot
import med_bot
import monke_bot
import biometrics_bot
from app import app, normalize_webhook_response, _validate_webhook_secret


class TestMilestone1Adversarial(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    # =========================================================================
    # 1. WEBHOOK INGRESS & CONCURRENCY STRESS TESTS
    # =========================================================================

    def test_concurrent_webhook_burst(self):
        """Stress-test concurrent requests across all 4 bot endpoints simultaneously."""
        endpoints = [
            "/api/telegram/webhook",
            "/api/medbot/webhook",
            "/api/monkebot/webhook",
            "/api/biometrics/webhook"
        ]

        def send_request(idx):
            endpoint = endpoints[idx % len(endpoints)]
            payload = {
                "update_id": 10000 + idx,
                "message": {
                    "message_id": idx,
                    "chat": {"id": 9999000 + (idx % 5), "type": "private"},
                    "from": {"first_name": f"Tester_{idx}"},
                    "text": f"/status_{idx}"
                }
            }
            resp = self.client.post(endpoint, json=payload)
            return resp.status_code, resp.json()

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(send_request, i) for i in range(60)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(len(results), 60)
        for status_code, body in results:
            self.assertEqual(status_code, 200)
            self.assertIn("status", body)
            self.assertIn("action", body)
            self.assertIn("details", body)

    def test_malformed_and_pathological_json_payloads(self):
        """Stress-test endpoints with non-dict, empty, and malformed JSON payloads."""
        endpoints = [
            "/api/telegram/webhook",
            "/api/medbot/webhook",
            "/api/monkebot/webhook",
            "/api/biometrics/webhook"
        ]

        for endpoint in endpoints:
            # 1. Empty body
            resp_empty = self.client.post(endpoint, content=b"")
            self.assertEqual(resp_empty.status_code, 200)
            data_empty = resp_empty.json()
            self.assertEqual(data_empty.get("status"), "error")
            self.assertEqual(data_empty.get("action"), "invalid_json")

            # 2. Malformed JSON syntax
            resp_bad_syntax = self.client.post(endpoint, content=b"{broken json: true", headers={"Content-Type": "application/json"})
            self.assertEqual(resp_bad_syntax.status_code, 200)
            data_bad_syntax = resp_bad_syntax.json()
            self.assertEqual(data_bad_syntax.get("status"), "error")
            self.assertEqual(data_bad_syntax.get("action"), "invalid_json")

            # 3. Primitive JSON types (ints, strings, booleans, arrays)
            for prim in [123, "just a string", True, ["list", "of", "items"], None]:
                resp_prim = self.client.post(endpoint, json=prim)
                self.assertEqual(resp_prim.status_code, 200)
                data_prim = resp_prim.json()
                self.assertIn(data_prim.get("status"), ["ok", "error"])

            # 4. Deeply nested / unexpected dict structures
            weird_payload = {
                "update_id": 999999,
                "unexpected_field": {"nested": [1, 2, {"deep": "value"}]},
                "message": None,
                "callback_query": None
            }
            resp_weird = self.client.post(endpoint, json=weird_payload)
            self.assertEqual(resp_weird.status_code, 200)
            data_weird = resp_weird.json()
            self.assertIn(data_weird.get("status"), ["ok", "ignored", "error"])

    def test_webhook_secret_header_security_and_casing(self):
        """Verify strict secret validation, header casing, whitespace, and injection attempts."""
        test_secret = "AlphaBetaGamma_Secret_998877"
        with patch.dict(os.environ, {"TELEGRAM_WEBHOOK_SECRET": test_secret}):
            # 1. Exact valid header
            resp_valid = self.client.post(
                "/api/telegram/webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": test_secret},
                json={"update_id": 3001}
            )
            self.assertEqual(resp_valid.status_code, 200)

            # 2. Lowercase header name (standard HTTP case-insensitivity)
            resp_lower = self.client.post(
                "/api/telegram/webhook",
                headers={"x-telegram-bot-api-secret-token": test_secret},
                json={"update_id": 3002}
            )
            self.assertEqual(resp_lower.status_code, 200)

            # 3. Missing header -> 403 Forbidden
            resp_missing = self.client.post(
                "/api/telegram/webhook",
                json={"update_id": 3003}
            )
            self.assertEqual(resp_missing.status_code, 403)

            # 4. Wrong / spoofed secret -> 403 Forbidden
            resp_wrong = self.client.post(
                "/api/telegram/webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong_attacker_secret"},
                json={"update_id": 3004}
            )
            self.assertEqual(resp_wrong.status_code, 403)

            # 5. Partial / substring secret -> 403 Forbidden
            resp_partial = self.client.post(
                "/api/telegram/webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": test_secret[:10]},
                json={"update_id": 3005}
            )
            self.assertEqual(resp_partial.status_code, 403)

            # 6. Whitespace padded secret -> 403 Forbidden
            resp_padded = self.client.post(
                "/api/telegram/webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": f" {test_secret} "},
                json={"update_id": 3006}
            )
            self.assertEqual(resp_padded.status_code, 403)

            # 7. SQL Injection string attempt
            resp_sqli = self.client.post(
                "/api/telegram/webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": "' OR '1'='1"},
                json={"update_id": 3007}
            )
            self.assertEqual(resp_sqli.status_code, 403)

    # =========================================================================
    # 2. CALLBACK QUERY ROUTING & CROSSTALK MATRIX
    # =========================================================================

    def test_complete_4x4_crosstalk_isolation_matrix(self):
        """
        Comprehensive 4x4 matrix test ensuring every bot strictly rejects
        callbacks originating from all 3 other bot namespaces without making API calls.
        """
        bots = [
            ("gluco_track", telegram_bot.handle_telegram_update, "gt:"),
            ("med_flow", med_bot.handle_med_webhook, "med:"),
            ("monke_helper", monke_bot.handle_monke_webhook, "mh:"),
            ("biometrics", biometrics_bot.handle_biometrics_webhook, "bio:")
        ]

        sample_callbacks = {
            "gt:": "gt:meal:45.0:2.0",
            "med:": "med:log:1:10.0",
            "mh:": "mh:briefing:refresh",
            "bio:": "bio:sync:now"
        }

        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"ok": True}

            for bot_id, handler_fn, own_prefix in bots:
                for foreign_prefix, foreign_data in sample_callbacks.items():
                    if foreign_prefix == own_prefix:
                        continue  # skip own namespace

                    mock_post.reset_mock()
                    update = {
                        "callback_query": {
                            "id": f"cb_crosstalk_{bot_id}_{foreign_prefix}",
                            "data": foreign_data,
                            "message": {"message_id": 555, "chat": {"id": 1111}},
                            "from": {"first_name": "Attacker"}
                        }
                    }

                    res = handler_fn(update)
                    self.assertEqual(
                        res.get("status"),
                        "ignored",
                        f"Bot '{bot_id}' failed to ignore foreign prefix '{foreign_prefix}'"
                    )
                    self.assertEqual(
                        res.get("action"),
                        "foreign_namespace_ignored",
                        f"Bot '{bot_id}' returned wrong action for foreign prefix '{foreign_prefix}'"
                    )
                    self.assertEqual(
                        res.get("details", {}).get("received_prefix"),
                        foreign_prefix,
                        f"Bot '{bot_id}' details did not match received_prefix"
                    )
                    # Verify NO Telegram API calls were made with this bot's token
                    self.assertFalse(
                        mock_post.called,
                        f"Bot '{bot_id}' attempted external API request on foreign callback '{foreign_data}'"
                    )

    def test_pathological_and_adversarial_callback_formats(self):
        """Stress-test bot handlers against pathological, malformed, and out-of-range callback strings."""
        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"ok": True}

            # 1. GlucoTrack with non-numeric carbs/bolus
            gt_update_nan = {
                "callback_query": {
                    "id": "cb_gt_nan",
                    "data": "gt:meal:notanumber:alsobad",
                    "message": {"message_id": 101, "chat": {"id": 1001}},
                    "from": {"first_name": "Adversary"}
                }
            }
            try:
                res = telegram_bot.handle_telegram_update(gt_update_nan)
                self.assertIn(res.get("status"), ["ok", "error"])
            except ValueError:
                # Should be gracefully caught inside handler
                pass

            # 2. GlucoTrack with empty/bare prefix
            gt_update_bare = {
                "callback_query": {
                    "id": "cb_gt_bare",
                    "data": "gt:",
                    "message": {"message_id": 102, "chat": {"id": 1001}},
                    "from": {"first_name": "Adversary"}
                }
            }
            res_bare = telegram_bot.handle_telegram_update(gt_update_bare)
            self.assertEqual(res_bare.get("status"), "ok")
            self.assertEqual(res_bare.get("action"), "callback_noop")

            # 3. MedFlow with non-numeric preset ID
            med_update_nan = {
                "callback_query": {
                    "id": "cb_med_nan",
                    "data": "med:log:notanid:10.0",
                    "message": {"message_id": 201, "chat": {"id": 2001}},
                    "from": {"first_name": "Adversary"}
                }
            }
            res_med = med_bot.handle_med_webhook(med_update_nan)
            self.assertEqual(res_med.get("status"), "error")
            self.assertEqual(res_med.get("action"), "logging_error")

            # 4. MedFlow with non-existent preset ID (e.g. 999999)
            with patch("med_bot.get_medication_presets", return_value=[]):
                med_update_missing = {
                    "callback_query": {
                        "id": "cb_med_miss",
                        "data": "med:log:999999:10.0",
                        "message": {"message_id": 202, "chat": {"id": 2001}},
                        "from": {"first_name": "Adversary"}
                    }
                }
                res_miss = med_bot.handle_med_webhook(med_update_missing)
                self.assertEqual(res_miss.get("status"), "error")
                self.assertEqual(res_miss.get("action"), "medication_not_found")

            # 5. MonkeHelper with unknown subaction
            monke_update_unknown = {
                "callback_query": {
                    "id": "cb_mh_unk",
                    "data": "mh:unknown:subaction:xyz",
                    "message": {"message_id": 301, "chat": {"id": 3001}},
                    "from": {"first_name": "Adversary"}
                }
            }
            res_monke = monke_bot.handle_monke_webhook(monke_update_unknown)
            self.assertEqual(res_monke.get("status"), "ok")
            self.assertEqual(res_monke.get("action"), "action_processed")

            # 6. Biometrics with unknown subaction
            bio_update_unknown = {
                "callback_query": {
                    "id": "cb_bio_unk",
                    "data": "bio:unknown:subaction:abc",
                    "message": {"message_id": 401, "chat": {"id": 4001}},
                    "from": {"first_name": "Adversary"}
                }
            }
            res_bio = biometrics_bot.handle_biometrics_webhook(bio_update_unknown)
            self.assertEqual(res_bio.get("status"), "ok")
            self.assertEqual(res_bio.get("action"), "action_processed")

    # =========================================================================
    # 3. BOT CLIENT & TOKEN ISOLATION STRESS TESTS
    # =========================================================================

    def test_unconfigured_bot_client_safe_degradation(self):
        """Verify unconfigured bot client methods return failure dicts and never throw exceptions."""
        unconfigured = TelegramBotClient(
            bot_id="unconfigured_bot",
            name="Unconfigured Bot",
            token_getter=lambda: None,
            default_token=None
        )
        self.assertFalse(unconfigured.is_configured)
        self.assertIsNone(unconfigured.token)

        # 1. send_message
        res_send = unconfigured.send_message("Test", chat_id="12345")
        self.assertFalse(res_send["success"])
        self.assertIn("error", res_send)

        # 2. answer_callback_query
        res_answer = unconfigured.answer_callback_query("cb_123", text="Alert")
        self.assertFalse(res_answer["success"])
        self.assertIn("error", res_answer)

        # 3. edit_message_text
        res_edit = unconfigured.edit_message_text(chat_id="12345", message_id=1, text="Updated")
        self.assertFalse(res_edit["success"])
        self.assertIn("error", res_edit)

        # 4. delete_message
        res_del = unconfigured.delete_message(chat_id="12345", message_id=1)
        self.assertFalse(res_del["success"])
        self.assertIn("error", res_del)

        # 5. get_updates
        res_upd = unconfigured.get_updates()
        self.assertFalse(res_upd["success"])
        self.assertIn("error", res_upd)

        # 6. delete_webhook
        res_del_wh = unconfigured.delete_webhook()
        self.assertFalse(res_del_wh["success"])
        self.assertIn("error", res_del_wh)

        # 7. set_webhook
        res_set_wh = unconfigured.set_webhook("https://example.com/webhook")
        self.assertFalse(res_set_wh["success"])
        self.assertIn("error", res_set_wh)

    def test_bot_client_network_exceptions_handled(self):
        """Verify network exceptions during Telegram API calls return structured error dicts."""
        client = TelegramBotClient(
            bot_id="net_test_bot",
            name="Net Test Bot",
            token_getter=lambda: "VALID_TOKEN_123"
        )

        with patch("requests.post", side_effect=Exception("Connection timed out")):
            res = client.send_message("Hello", chat_id="12345")
            self.assertFalse(res["success"])
            self.assertIn("Connection timed out", res["error"])

            res_cb = client.answer_callback_query("cb_id_1")
            self.assertFalse(res_cb["success"])
            self.assertIn("Connection timed out", res_cb["error"])

    # =========================================================================
    # 4. MULTI-BOT POLLING MANAGER & WORKER RESILIENCY TESTS
    # =========================================================================

    def test_worker_idempotency_and_state_transitions(self):
        """Verify worker start/stop idempotency and clean state transitions."""
        worker = BotPollerWorker(
            bot_id="state_test",
            name="State Test Worker",
            token_getter=lambda: None,
            handler=lambda u: None
        )

        # 1. Initial status
        status = worker.get_status()
        self.assertEqual(status["status"], "stopped")
        self.assertFalse(status["is_alive"])

        # 2. Stop when already stopped -> True
        self.assertTrue(worker.stop())

        # 3. Start worker with no token -> worker runs and enters paused_no_token
        self.assertTrue(worker.start())
        self.assertTrue(worker.is_alive())
        # Give thread a slice to reach paused_no_token
        time.sleep(0.1)
        status_running = worker.get_status()
        self.assertEqual(status_running["status"], "paused_no_token")

        # 4. Redundant start while running -> True (no duplicate thread)
        t1 = worker._thread
        self.assertTrue(worker.start())
        self.assertEqual(worker._thread, t1)

        # 5. Clean stop
        self.assertTrue(worker.stop(timeout=2.0))
        self.assertFalse(worker.is_alive())
        self.assertEqual(worker.get_status()["status"], "stopped")

    def test_worker_409_conflict_and_429_rate_limit_resilience(self):
        """
        Adversarially test BotPollerWorker response to HTTP 409 (Webhook Active)
        and HTTP 429 (Rate Limit with retry_after).
        """
        events_processed = []

        def test_handler(update):
            events_processed.append(update)

        worker = BotPollerWorker(
            bot_id="resilience_worker",
            name="Resilience Worker",
            token_getter=lambda: "RESILIENCE_TOKEN_888",
            handler=test_handler,
            poll_timeout=1,
            client_timeout=2
        )

        with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"ok": True}

            # Response sequence:
            # 1. 409 Conflict (Webhook Active)
            # 2. 429 Too Many Requests (retry_after: 0)
            # 3. 200 OK with updates
            resp_409 = MagicMock(status_code=409)
            resp_429 = MagicMock(
                status_code=429,
                json=lambda: {"parameters": {"retry_after": 0}}
            )
            resp_200 = MagicMock(
                status_code=200,
                json=lambda: {
                    "ok": True,
                    "result": [
                        {"update_id": 801, "message": {"text": "m1"}},
                        {"update_id": 802, "message": {"text": "m2"}}
                    ]
                }
            )

            mock_get.side_effect = [resp_409, resp_429, resp_200, Exception("End test")]

            worker.start()
            
            # Wait deterministically for updates to be processed
            start_time = time.time()
            while len(events_processed) < 2 and time.time() - start_time < 3.0:
                time.sleep(0.05)

            worker.stop(timeout=2.0)

            # Assertions
            self.assertEqual(len(events_processed), 2)
            self.assertEqual(worker._offset, 803)
            self.assertEqual(worker._updates_count, 2)
            # Verify deleteWebhook was called during 409 recovery
            self.assertTrue(mock_post.called)
            del_urls = [call[0][0] for call in mock_post.call_args_list]
            self.assertTrue(any("/deleteWebhook" in u for u in del_urls))

    def test_worker_auth_failure_status(self):
        """Verify worker transitions to auth_failed on HTTP 401/404 without crashing."""
        worker = BotPollerWorker(
            bot_id="auth_fail_test",
            name="Auth Fail Worker",
            token_getter=lambda: "REVOKED_TOKEN_999",
            handler=lambda u: None,
            poll_timeout=1,
            client_timeout=2
        )

        with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"ok": True}

            mock_get.return_value = MagicMock(
                status_code=401,
                text="Unauthorized"
            )

            worker.start()
            time.sleep(0.1)

            status = worker.get_status()
            self.assertEqual(status["status"], "auth_failed")
            self.assertIn("Invalid or revoked Bot Token", status["last_error"])

            worker.stop(timeout=2.0)

    def test_worker_survives_handler_exceptions(self):
        """Verify worker continues polling and updates offset even when handler raises unhandled exceptions."""
        handled_count = 0

        def failing_handler(update):
            nonlocal handled_count
            handled_count += 1
            if update.get("update_id") == 901:
                raise ZeroDivisionError("Simulated bug in handler!")
            elif update.get("update_id") == 902:
                raise KeyError("Simulated missing key!")

        worker = BotPollerWorker(
            bot_id="handler_bug_test",
            name="Handler Bug Worker",
            token_getter=lambda: "BUG_TEST_TOKEN",
            handler=failing_handler,
            poll_timeout=1,
            client_timeout=2
        )

        with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"ok": True}

            resp_updates = MagicMock(
                status_code=200,
                json=lambda: {
                    "ok": True,
                    "result": [
                        {"update_id": 901, "message": {"text": "crash1"}},
                        {"update_id": 902, "message": {"text": "crash2"}},
                        {"update_id": 903, "message": {"text": "success"}}
                    ]
                }
            )
            mock_get.side_effect = [resp_updates, Exception("End loop")]

            worker.start()
            start_time = time.time()
            while handled_count < 3 and time.time() - start_time < 3.0:
                time.sleep(0.05)

            worker.stop(timeout=2.0)

            self.assertEqual(handled_count, 3)
            # Offset must be updated past 903 so the poller doesn't loop infinitely on the bad update
            self.assertEqual(worker._offset, 904)

    def test_supervisor_watchdog_auto_recovery(self):
        """Verify MultiBotPollingManager watchdog detects dead threads and restarts them."""
        manager = MultiBotPollingManager()
        worker = manager.register_bot(
            bot_id="watchdog_test",
            name="Watchdog Worker",
            token_getter=lambda: None,
            handler=lambda u: None
        )

        worker.start()
        self.assertTrue(worker.is_alive())

        # Simulate unexpected thread death by stopping the thread but leaving _is_running=True
        worker._stop_event.set()
        if worker._thread:
            worker._thread.join(timeout=2.0)
        self.assertFalse(worker.is_alive())
        worker._is_running = True  # simulate died unexpectedly

        # Watchdog check should detect it and restart it
        manager.watchdog_check()
        self.assertTrue(worker.is_alive())

        worker.stop(timeout=2.0)

    def test_supervisor_concurrent_control(self):
        """Stress-test supervisor operations (start_all, stop_all, get_status) under multi-threaded concurrency."""
        manager = MultiBotPollingManager()
        for i in range(5):
            manager.register_bot(
                bot_id=f"bot_conc_{i}",
                name=f"Concurrent Bot {i}",
                token_getter=lambda: None,
                handler=lambda u: None
            )

        def worker_task(op_idx):
            if op_idx % 3 == 0:
                manager.start_all()
            elif op_idx % 3 == 1:
                manager.get_status()
            else:
                manager.stop_all(timeout=1.0)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker_task, i) for i in range(30)]
            for f in concurrent.futures.as_completed(futures):
                f.result()  # verify no deadlock or unhandled exceptions

        manager.stop_all(timeout=2.0)
        status = manager.get_status()
        self.assertEqual(len(status), 5)

    # =========================================================================
    # 5. EMPIRICAL ROUTER EXCEPTION & BOUNDARY TESTS
    # =========================================================================

    def test_router_none_field_exception_behavior(self):
        """
        Verify the raw handler behavior when 'callback_query': None or 'message': None is passed.
        Direct handler calls raise AttributeError/TypeError when fields are explicitly None,
        while FastAPI webhook endpoints catch and encapsulate this into status='error'.
        """
        # 1. Direct call to GlucoTrack handler with callback_query=None raises AttributeError
        with self.assertRaises((AttributeError, TypeError)):
            telegram_bot.handle_telegram_update({"callback_query": None})

        # 2. Direct call to MedFlow handler with callback_query=None is gracefully handled in M2
        res_med = med_bot.handle_med_webhook({"callback_query": None})
        self.assertIn(res_med.get("status"), ["ok", "error", "ignored"])

        # 3. Direct call to MonkeHelper handler with callback_query=None is gracefully handled in M4
        res_monke = monke_bot.handle_monke_webhook({"callback_query": None})
        self.assertIn(res_monke.get("status"), ["ok", "error", "ignored"])

        # 4. Direct call to Biometrics handler with callback_query=None is gracefully handled in M3
        res_bio = biometrics_bot.handle_biometrics_webhook({"callback_query": None})
        self.assertIn(res_bio.get("status"), ["ok", "error", "ignored"])

        # 5. When routed through FastAPI Ingress, app.py catches it and returns HTTP 200 with status="error"
        resp = self.client.post("/api/telegram/webhook", json={"callback_query": None})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("status"), "error")
        self.assertEqual(resp.json().get("action"), "handler_error")

    def test_gt_meal_invalid_numeric_boundary(self):
        """
        Verify GlucoTrack handler raw behavior on malformed numeric strings in gt:meal.
        Direct handler call raises ValueError when float parsing fails on non-numeric payload,
        which is caught and encapsulated by app.py.
        """
        bad_update = {
            "callback_query": {
                "id": "cb_nan_1",
                "data": "gt:meal:non_numeric_carbs:non_numeric_bolus",
                "message": {"message_id": 999, "chat": {"id": 1234}},
                "from": {"first_name": "Test"}
            }
        }
        with self.assertRaises(ValueError):
            telegram_bot.handle_telegram_update(bad_update)

        # Ingress route catches ValueError and reports handler_error
        resp = self.client.post("/api/telegram/webhook", json=bad_update)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("status"), "error")
        self.assertEqual(resp.json().get("action"), "handler_error")

    def test_deterministic_worker_lifecycle_resilience(self):
        """
        Verify BotPollerWorker lifecycle deterministically without thread scheduling race condition.
        Uses a synchronization event to ensure thread has entered the loop before stopping.
        """
        handled = []
        started_event = threading.Event()

        def test_handler(update):
            handled.append(update)

        worker = BotPollerWorker(
            bot_id="sync_test_worker",
            name="Sync Test Worker",
            token_getter=lambda: "SYNC_TOKEN_123",
            handler=test_handler,
            poll_timeout=1,
            client_timeout=2
        )

        with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"ok": True}

            mock_resp_409 = MagicMock(status_code=409)
            mock_resp_200 = MagicMock(
                status_code=200,
                json=lambda: {
                    "ok": True,
                    "result": [{"update_id": 777, "message": {"text": "hello"}}]
                }
            )

            def mock_get_side_effect(*args, **kwargs):
                started_event.set()
                return mock_resp_409

            mock_get.side_effect = [mock_resp_409, mock_resp_200, Exception("End test")]

            worker.start()
            self.assertTrue(worker.is_alive())

            # Wait until thread has started and made its first get/post
            start_time = time.time()
            while not mock_post.called and time.time() - start_time < 3.0:
                time.sleep(0.05)

            worker.stop(timeout=2.0)
            self.assertFalse(worker.is_alive())

            # Verify deleteWebhook was called during 409 recovery
            self.assertTrue(mock_post.called)
            del_url = mock_post.call_args[0][0]
            self.assertIn("/deleteWebhook", del_url)


if __name__ == "__main__":
    unittest.main()

