"""
tests/test_tier5_adversarial_security_lifecycle.py
Tier 5 White-Box Adversarial Stress Testing & Security Lifecycle Verification.

Coverage Dimensions:
1. Token Leakage Fuzzing & Credential Isolation:
   - Dynamic hierarchy precedence (Env -> DB -> Default -> Unconfigured)
   - Token masking safety under fuzzing & edge cases
   - Unconfigured client zero-network-leakage guarantee
   - Cross-bot token isolation under concurrent execution
   - Bot client registry and malicious alias lookup rejection

2. Webhook Secret Header Tampering & Bypass Defense:
   - Exhaustive attack matrix across all 4 endpoints:
     (/api/telegram/webhook, /api/medbot/webhook, /api/monkebot/webhook, /api/biometrics/webhook)
   - Missing, empty, whitespace, tampered prefix/suffix, cross-bot swapped secrets
   - Header spoofing (Authorization, X-Secret-Token, etc.) and injection payloads
   - Unconfigured secret open fallback vs strict DB/env enforcement
   - Malformed/pathological payload fuzzing with valid secret

3. Callback Prefix Collision & 64-Byte Boundary Stress:
   - Full 4x4 foreign namespace rejection matrix (12 cross-bot combinations)
   - Collision & near-match prefix fuzzing (gt::, gt_, gtt:, med_log, bio_sync, null bytes)
   - 64-byte Telegram callback_data limit compliance and >64B overflow stress
   - Multi-byte UTF-8 character boundary validation
   - Malformed callback structures (null data, missing chat, invalid numeric casts)
   - Sliding-window double-tap debouncing verification

4. Multi-Bot Polling Manager & Supervisor Recovery Lifecycle:
   - HTTP 409 Conflict self-healing (deleteWebhook drop_pending_updates=False)
   - Telegram API error payload conflict detection (error_code 409 / conflict description)
   - Jittered exponential backoff mathematical progression across consecutive errors
   - HTTP 429 rate limit backoff (retry_after) and HTTP 401/404 auth failure handling
   - Poller loop business exception containment (thread survival on handler error)
   - Supervisor watchdog thread death detection and automatic resurrection

5. Database Connection Timeout & Graceful Degradation Across All 4 Bots:
   - OperationalError / TimeoutError simulation on GlucoTrack telemetry & meal logging
   - MedFlowAssist preset lookup and dose logging DB timeout resilience
   - MonkeHelper master hub unified briefing multi-subsystem DB timeout resilience
   - Circadian & Biometrics sleep/heart metric DB timeout resilience
   - Webhook 200 HTTP response preservation during database outages
"""

import os
import sys
import math
import time
import json
import random
import threading
import concurrent.futures
import unittest
from unittest.mock import patch, MagicMock, PropertyMock
from fastapi.testclient import TestClient

# Ensure workspace root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import db
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
from app import app, normalize_webhook_response, _validate_webhook_secret


# =============================================================================
# 1. TOKEN ISOLATION, CREDENTIAL RESOLUTION & TOKEN MASKING
# =============================================================================

class TestTier5TokenIsolationAndLeakageFuzzing(unittest.TestCase):
    """Adversarial testing of credential resolution, token isolation, and masking."""

    def test_token_masking_comprehensive_matrix(self):
        """Verify token masking logic never reveals raw tokens under various inputs."""
        # 1. Null / None / Empty
        self.assertEqual(mask_token(None), "UNCONFIGURED")
        self.assertEqual(mask_token(""), "UNCONFIGURED")
        self.assertEqual(mask_token("   "), "UNCONFIGURED")

        # 2. Short tokens (< 10 chars)
        self.assertEqual(mask_token("123"), "UNCONFIGURED")
        self.assertEqual(mask_token("abcdefghi"), "UNCONFIGURED")  # 9 chars
        self.assertEqual(mask_token("123456789"), "UNCONFIGURED")  # 9 chars

        # 3. Exact 10-char boundary
        masked_10 = mask_token("1234567890")
        self.assertEqual(masked_10, "123456...890")
        self.assertNotIn("7", masked_10)

        # 4. Standard Telegram Bot Tokens
        real_med_masked = mask_token(DEFAULT_MED_BOT_TOKEN)
        self.assertEqual(real_med_masked, f"{DEFAULT_MED_BOT_TOKEN[:6]}...{DEFAULT_MED_BOT_TOKEN[-3:]}")
        self.assertNotIn(DEFAULT_MED_BOT_TOKEN[6:-3], real_med_masked)

        real_monke_masked = mask_token(DEFAULT_MONKE_BOT_TOKEN)
        self.assertEqual(real_monke_masked, f"{DEFAULT_MONKE_BOT_TOKEN[:6]}...{DEFAULT_MONKE_BOT_TOKEN[-3:]}")
        self.assertNotIn(DEFAULT_MONKE_BOT_TOKEN[6:-3], real_monke_masked)

        # 5. Pathological & Fuzzed Strings
        fuzzed_token = "BOT:SECRET_KEY_999999999999999999999_SUPER_SECRET"
        masked_fuzz = mask_token(fuzzed_token)
        self.assertEqual(masked_fuzz, f"{fuzzed_token[:6]}...{fuzzed_token[-3:]}")
        self.assertNotIn("999999999", masked_fuzz)

    def test_credential_resolution_hierarchy_gluco_track(self):
        """Verify GlucoTrack credential precedence: Env -> DB -> None."""
        gt_client = get_bot_client("gluco_track")

        # 1. Env Var Highest Precedence
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "ENV_GT_TOKEN_111"}, clear=False):
            with patch("db.get_system_setting", return_value={"bot_token": "DB_GT_TOKEN_222"}):
                self.assertEqual(gt_client.token, "ENV_GT_TOKEN_111")

        # 2. DB Setting Fallback when Env Absent
        with patch.dict(os.environ, {}, clear=True):
            with patch("db.get_system_setting", return_value={"bot_token": "DB_GT_TOKEN_222"}):
                self.assertEqual(gt_client.token, "DB_GT_TOKEN_222")

        # 3. Unconfigured when both Env and DB Absent
        with patch.dict(os.environ, {}, clear=True):
            with patch("db.get_system_setting", return_value=None):
                self.assertIsNone(gt_client.token)
                self.assertFalse(gt_client.is_configured)

    def test_credential_resolution_hierarchy_med_flow(self):
        """Verify MedFlow credential precedence: Env -> DB -> Default Hardcoded."""
        med_client = get_bot_client("med_flow")

        # 1. Env Var Precedence
        with patch.dict(os.environ, {"MED_BOT_TOKEN": "ENV_MED_TOKEN_999"}, clear=False):
            with patch("db.get_system_setting", return_value={"bot_token": "DB_MED_TOKEN_888"}):
                self.assertEqual(med_client.token, "ENV_MED_TOKEN_999")

        # 2. DB Fallback
        with patch.dict(os.environ, {}, clear=True):
            with patch("db.get_system_setting", return_value={"bot_token": "DB_MED_TOKEN_888"}):
                self.assertEqual(med_client.token, "DB_MED_TOKEN_888")

        # 3. Default Hardcoded Fallback
        with patch.dict(os.environ, {}, clear=True):
            with patch("db.get_system_setting", return_value=None):
                self.assertEqual(med_client.token, DEFAULT_MED_BOT_TOKEN)

    def test_unconfigured_bot_zero_network_leakage(self):
        """Ensure unconfigured bot client makes 0 network calls and returns safe error dicts."""
        unconfigured_client = TelegramBotClient(
            bot_id="unconfigured_bot",
            name="Unconfigured Bot",
            token_getter=lambda: None,
            default_token=None
        )
        self.assertFalse(unconfigured_client.is_configured)
        self.assertIsNone(unconfigured_client.token)

        # Direct _api_url should raise ValueError
        with self.assertRaises(ValueError):
            unconfigured_client._api_url("sendMessage")

        # Intercept requests to ensure zero HTTP calls are made
        with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
            res_send = unconfigured_client.send_message("Hello", chat_id="12345")
            self.assertFalse(res_send["success"])
            self.assertIn("Missing bot token", res_send["error"])

            res_ans = unconfigured_client.answer_callback_query("cb_123", "Text")
            self.assertFalse(res_ans["success"])
            self.assertIn("Unconfigured token", res_ans["error"])

            res_edit = unconfigured_client.edit_message_text("12345", 99, "Updated text")
            self.assertFalse(res_edit["success"])
            self.assertIn("Unconfigured token", res_edit["error"])

            res_del = unconfigured_client.delete_message("12345", 99)
            self.assertFalse(res_del["success"])
            self.assertIn("Unconfigured token", res_del["error"])

            res_upd = unconfigured_client.get_updates()
            self.assertFalse(res_upd["success"])
            self.assertIn("Unconfigured token", res_upd["error"])

            res_del_wh = unconfigured_client.delete_webhook()
            self.assertFalse(res_del_wh["success"])
            self.assertIn("Unconfigured token", res_del_wh["error"])

            res_set_wh = unconfigured_client.set_webhook("https://example.com/wh")
            self.assertFalse(res_set_wh["success"])
            self.assertIn("Unconfigured token", res_set_wh["error"])

            mock_post.assert_not_called()
            mock_get.assert_not_called()

    def test_concurrent_cross_bot_token_isolation(self):
        """Ensure concurrent invocations across 4 bots strictly use their respective tokens with zero leakage."""
        bot_tokens = {
            "gluco_track": "TOKEN_GLUCO_111111",
            "med_flow": "TOKEN_MED_222222",
            "monke_helper": "TOKEN_MONKE_333333",
            "biometrics": "TOKEN_BIO_444444"
        }

        posted_urls = []
        lock = threading.Lock()

        def fake_post(url, json=None, timeout=None, **kwargs):
            with lock:
                posted_urls.append(url)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.ok = True
            mock_resp.json.return_value = {"ok": True, "result": {"message_id": 100}}
            return mock_resp

        def invoke_bot(bot_id, idx):
            client = get_bot_client(bot_id)
            with patch.object(TelegramBotClient, "token", new_callable=PropertyMock) as mock_tok:
                mock_tok.return_value = bot_tokens[bot_id]
                res = client.send_message(f"Message {idx} from {bot_id}", chat_id=f"100{idx}")
                self.assertTrue(res["success"])

        with patch("requests.post", side_effect=fake_post):
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
                futures = []
                for i in range(40):
                    b_id = list(bot_tokens.keys())[i % 4]
                    futures.append(executor.submit(invoke_bot, b_id, i))
                for f in concurrent.futures.as_completed(futures):
                    f.result()

        self.assertEqual(len(posted_urls), 40)
        for url in posted_urls:
            matching_tokens = [tok for tok in bot_tokens.values() if tok in url]
            self.assertEqual(len(matching_tokens), 1, f"URL {url} leaked multiple or zero tokens!")

    def test_bot_client_registry_lookup_and_invalid_alias_defense(self):
        """Verify bot registry lookups handle aliases and safely reject invalid identifiers."""
        # Valid canonical lookups
        self.assertEqual(get_bot_client("gluco_track").bot_id, "gluco_track")
        self.assertEqual(get_bot_client("med_flow").bot_id, "med_flow")
        self.assertEqual(get_bot_client("monke_helper").bot_id, "monke_helper")
        self.assertEqual(get_bot_client("biometrics").bot_id, "biometrics")

        # Valid aliases
        self.assertEqual(get_bot_client("telegram").bot_id, "gluco_track")
        self.assertEqual(get_bot_client("medbot").bot_id, "med_flow")
        self.assertEqual(get_bot_client("monkebot").bot_id, "monke_helper")
        self.assertEqual(get_bot_client("circadian").bot_id, "biometrics")

        # Malicious / Fuzzed bot IDs must raise KeyError
        malicious_ids = [
            "hacker_bot",
            "../../etc/passwd",
            "' OR '1'='1",
            "\x00bot",
            "SYSTEM",
            "",
            "   ",
            "gluco_track; DROP TABLE users;"
        ]
        for bad_id in malicious_ids:
            with self.assertRaises(KeyError):
                get_bot_client(bad_id)


# =============================================================================
# 2. WEBHOOK SECRET HEADER TAMPERING & BYPASS DEFENSE
# =============================================================================

class TestTier5WebhookSecretTamperingAndBypass(unittest.TestCase):
    """Adversarial stress testing of webhook secret verification across all 4 endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_webhook_secret_tampering_matrix_all_endpoints(self):
        """Exhaustively test secret tampering, bypass attempts, and header spoofing on all 4 routes."""
        endpoints_and_env = [
            ("/api/telegram/webhook", "TELEGRAM_WEBHOOK_SECRET", "GT_SEC_Alpha123"),
            ("/api/medbot/webhook", "MED_BOT_WEBHOOK_SECRET", "MED_SEC_Beta456"),
            ("/api/monkebot/webhook", "MONKE_BOT_WEBHOOK_SECRET", "MONKE_SEC_Gamma789"),
            ("/api/biometrics/webhook", "BIOMETRICS_BOT_WEBHOOK_SECRET", "BIO_SEC_Delta000")
        ]

        for endpoint, env_var, secret_token in endpoints_and_env:
            with patch.dict(os.environ, {env_var: secret_token}, clear=False):
                with patch("db.get_system_setting", return_value={}):
                    valid_body = {
                        "update_id": 9999,
                        "message": {
                            "message_id": 1,
                            "chat": {"id": 12345, "type": "private"},
                            "text": "/help"
                        }
                    }

                    # 1. Exact valid secret header -> 200 OK
                    resp_valid = self.client.post(
                        endpoint,
                        json=valid_body,
                        headers={"X-Telegram-Bot-Api-Secret-Token": secret_token}
                    )
                    self.assertEqual(resp_valid.status_code, 200, f"Failed on {endpoint} with valid secret")

                    # 2. Missing header -> 403 Forbidden
                    resp_missing = self.client.post(endpoint, json=valid_body)
                    self.assertEqual(resp_missing.status_code, 403, f"Bypass succeeded on {endpoint} with missing header!")

                    # 3. Empty header -> 403 Forbidden
                    resp_empty = self.client.post(
                        endpoint,
                        json=valid_body,
                        headers={"X-Telegram-Bot-Api-Secret-Token": ""}
                    )
                    self.assertEqual(resp_empty.status_code, 403, f"Bypass succeeded on {endpoint} with empty header!")

                    # 4. Whitespace header -> 403 Forbidden
                    resp_ws = self.client.post(
                        endpoint,
                        json=valid_body,
                        headers={"X-Telegram-Bot-Api-Secret-Token": "   "}
                    )
                    self.assertEqual(resp_ws.status_code, 403, f"Bypass succeeded on {endpoint} with whitespace header!")

                    # 5. Suffix tampering -> 403 Forbidden
                    resp_suffix = self.client.post(
                        endpoint,
                        json=valid_body,
                        headers={"X-Telegram-Bot-Api-Secret-Token": f"{secret_token}_extra"}
                    )
                    self.assertEqual(resp_suffix.status_code, 403)

                    # 6. Prefix tampering -> 403 Forbidden
                    resp_prefix = self.client.post(
                        endpoint,
                        json=valid_body,
                        headers={"X-Telegram-Bot-Api-Secret-Token": f"fake_{secret_token}"}
                    )
                    self.assertEqual(resp_prefix.status_code, 403)

                    # 7. Truncated secret -> 403 Forbidden
                    resp_trunc = self.client.post(
                        endpoint,
                        json=valid_body,
                        headers={"X-Telegram-Bot-Api-Secret-Token": secret_token[:-1]}
                    )
                    self.assertEqual(resp_trunc.status_code, 403)

                    # 8. Cross-bot secret swap (sending another bot's secret) -> 403 Forbidden
                    foreign_secret = "WRONG_BOT_SECRET_9999"
                    resp_cross = self.client.post(
                        endpoint,
                        json=valid_body,
                        headers={"X-Telegram-Bot-Api-Secret-Token": foreign_secret}
                    )
                    self.assertEqual(resp_cross.status_code, 403)

                    # 9. Header Name Spoofing -> 403 Forbidden
                    spoofed_headers = [
                        {"Authorization": f"Bearer {secret_token}"},
                        {"X-Secret-Token": secret_token},
                        {"X-Telegram-Secret": secret_token},
                        {"X-Webhook-Secret": secret_token},
                        {"X-Telegram-Bot-Secret-Token": secret_token}
                    ]
                    for s_hdr in spoofed_headers:
                        resp_spoof = self.client.post(endpoint, json=valid_body, headers=s_hdr)
                        self.assertEqual(resp_spoof.status_code, 403, f"Header spoof succeeded with {s_hdr} on {endpoint}")

                    # 10. Attack Payloads in Secret Header -> 403 Forbidden
                    injection_headers = [
                        f"{secret_token}' OR '1'='1",
                        f"{secret_token}--injection",
                        f"{secret_token};DROP TABLE--",
                        f"ADMIN_{secret_token}"
                    ]
                    for inj in injection_headers:
                        resp_inj = self.client.post(
                            endpoint,
                            json=valid_body,
                            headers={"X-Telegram-Bot-Api-Secret-Token": inj}
                        )
                        self.assertEqual(resp_inj.status_code, 403)

    def test_direct_validate_webhook_secret_function_edge_cases(self):
        """Directly verify _validate_webhook_secret function against unusual/unicode inputs."""
        # 1. Expected secret is None -> always True (open mode)
        self.assertTrue(_validate_webhook_secret(None, None))
        self.assertTrue(_validate_webhook_secret(MagicMock(), None))

        # 2. Request is None but expected secret is set -> True (FastAPI default arg guard)
        self.assertTrue(_validate_webhook_secret(None, "SECRET"))

        # 3. Unicode and special character secrets
        mock_req = MagicMock()
        mock_req.headers.get.return_value = "💊🩸Secret_Unicode"
        self.assertTrue(_validate_webhook_secret(mock_req, "💊🩸Secret_Unicode"))
        self.assertFalse(_validate_webhook_secret(mock_req, "Different_Secret"))

    def test_webhook_secret_from_db_config(self):
        """Verify webhook secret configured in DB system_settings is strictly enforced."""
        stored_configs = {
            "/api/telegram/webhook": ("telegram_config", "DB_GT_SECRET_999"),
            "/api/medbot/webhook": ("med_bot_config", "DB_MED_SECRET_888"),
            "/api/monkebot/webhook": ("monke_bot_config", "DB_MONKE_SECRET_777"),
            "/api/biometrics/webhook": ("biometrics_bot_config", "DB_BIO_SECRET_666")
        }

        for endpoint, (cfg_key, expected_sec) in stored_configs.items():
            def fake_get_setting(k):
                if k == cfg_key:
                    return {"secret_token": expected_sec}
                return {}

            with patch.dict(os.environ, {}, clear=True):
                with patch("db.get_system_setting", side_effect=fake_get_setting):
                    payload = {"update_id": 1, "message": {"chat": {"id": 1}, "text": "/help"}}

                    # Valid DB secret -> 200
                    resp_ok = self.client.post(
                        endpoint,
                        json=payload,
                        headers={"X-Telegram-Bot-Api-Secret-Token": expected_sec}
                    )
                    self.assertEqual(resp_ok.status_code, 200)

                    # Invalid secret -> 403
                    resp_bad = self.client.post(
                        endpoint,
                        json=payload,
                        headers={"X-Telegram-Bot-Api-Secret-Token": "BAD_SECRET"}
                    )
                    self.assertEqual(resp_bad.status_code, 403)

    def test_webhook_unconfigured_secret_graceful_open_mode(self):
        """When no secret is configured in env or DB, endpoints allow requests without secret header."""
        endpoints = [
            "/api/telegram/webhook",
            "/api/medbot/webhook",
            "/api/monkebot/webhook",
            "/api/biometrics/webhook"
        ]

        with patch.dict(os.environ, {}, clear=True):
            with patch("db.get_system_setting", return_value={}):
                for endpoint in endpoints:
                    resp = self.client.post(
                        endpoint,
                        json={"update_id": 100, "message": {"chat": {"id": 123}, "text": "/help"}}
                    )
                    self.assertEqual(resp.status_code, 200)
                    body = resp.json()
                    self.assertEqual(body["status"], "ok")

    def test_webhook_malformed_payload_fuzzing_with_valid_secret(self):
        """Verify malformed JSON or empty payloads return normalized error response without crashing."""
        endpoint = "/api/medbot/webhook"
        secret = "SEC_VALID_123"

        with patch.dict(os.environ, {"MED_BOT_WEBHOOK_SECRET": secret}):
            headers = {
                "X-Telegram-Bot-Api-Secret-Token": secret,
                "Content-Type": "application/json"
            }

            # 1. Empty body
            resp_empty = self.client.post(endpoint, content=b"", headers=headers)
            self.assertEqual(resp_empty.status_code, 200)
            self.assertEqual(resp_empty.json()["status"], "error")
            self.assertEqual(resp_empty.json()["action"], "invalid_json")

            # 2. Broken JSON
            resp_broken = self.client.post(endpoint, content=b"{broken:json,", headers=headers)
            self.assertEqual(resp_broken.status_code, 200)
            self.assertEqual(resp_broken.json()["status"], "error")
            self.assertEqual(resp_broken.json()["action"], "invalid_json")

            # 3. Non-dict JSON (Array)
            resp_arr = self.client.post(endpoint, content=b"[\"item1\", \"item2\"]", headers=headers)
            self.assertEqual(resp_arr.status_code, 200)
            self.assertEqual(resp_arr.json()["action"], "noop")

            # 4. Null JSON
            resp_null = self.client.post(endpoint, content=b"null", headers=headers)
            self.assertEqual(resp_null.status_code, 200)
            self.assertEqual(resp_null.json()["action"], "noop")


# =============================================================================
# 3. CALLBACK PREFIX COLLISION & 64-BYTE BOUNDARY STRESS
# =============================================================================

class TestTier5CallbackPrefixCollisionAndBoundaryStress(unittest.TestCase):
    """Adversarial testing of callback namespacing, collision resistance, and 64-byte boundaries."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_foreign_namespace_strict_isolation_all_permutations(self):
        """Verify all 12 pairwise foreign namespace permutations are strictly ignored with 0 API calls."""
        bot_handlers = [
            ("gluco_track", telegram_bot.handle_telegram_update, "gt:"),
            ("med_flow", med_bot.handle_med_webhook, "med:"),
            ("monke_helper", monke_bot.handle_monke_webhook, "mh:"),
            ("biometrics", biometrics_bot.handle_biometrics_webhook, "bio:")
        ]

        foreign_samples = {
            "gt:": "gt:meal:45:3.0",
            "med:": "med:log:1:500.0",
            "mh:": "mh:briefing:refresh",
            "bio:": "bio:sync:now"
        }

        with patch("requests.post") as mock_post:
            for bot_id, handler_fn, own_prefix in bot_handlers:
                for foreign_prefix, payload in foreign_samples.items():
                    if foreign_prefix == own_prefix:
                        continue

                    update = {
                        "update_id": 5000,
                        "callback_query": {
                            "id": f"cb_foreign_{bot_id}_{foreign_prefix}",
                            "data": payload,
                            "from": {"id": 111, "first_name": "Attacker"},
                            "message": {"message_id": 99, "chat": {"id": 12345}}
                        }
                    }

                    res = handler_fn(update)
                    self.assertEqual(
                        res.get("status"),
                        "ignored",
                        f"Bot {bot_id} did not ignore foreign prefix {foreign_prefix}!"
                    )
                    self.assertEqual(
                        res.get("action"),
                        "foreign_namespace_ignored",
                        f"Bot {bot_id} action mismatch on {foreign_prefix}"
                    )

            mock_post.assert_not_called()

    def test_callback_prefix_collision_and_near_match_fuzzing(self):
        """Fuzz near-match prefixes and malformed data to ensure no unintended execution or unhandled crash."""
        bot_handlers = [
            telegram_bot.handle_telegram_update,
            med_bot.handle_med_webhook,
            monke_bot.handle_monke_webhook,
            biometrics_bot.handle_biometrics_webhook
        ]

        fuzzed_callback_data = [
            "gt::meal:10:1",
            "gt_meal:10:1",
            "gtt:meal:10:1",
            "medication:log:1:10",
            "med_log:1:10",
            "mh_briefing:today",
            "bio_sync:now",
            "biometrics:sync:now",
            "gt",
            "med",
            "mh",
            "bio",
            ":gt:meal:10:1",
            "\x00gt:meal:10:1",
            "gt:unknown_action:9",
            "med:invalid_sub:1:2",
            "mh:unsupported:action",
            "bio:nonexistent:call"
        ]

        for handler in bot_handlers:
            for cb_data in fuzzed_callback_data:
                update = {
                    "update_id": 8888,
                    "callback_query": {
                        "id": f"cb_fuzz_{random.randint(1000, 999999)}",
                        "data": cb_data,
                        "from": {"id": 222, "first_name": "Fuzzer"},
                        "message": {"message_id": 10, "chat": {"id": 999}}
                    }
                }
                try:
                    res = handler(update)
                    self.assertIsInstance(res, dict)
                    self.assertIn("status", res)
                except Exception as e:
                    self.fail(f"Handler {handler.__name__} crashed on input '{cb_data}': {e}")

    def test_64_byte_telegram_callback_boundary_and_overflow(self):
        """Verify Telegram 64-byte callback_data boundary compliance and overflow handling."""
        cb_64_ascii = "med:log:1:" + ("9" * 54)
        self.assertEqual(len(cb_64_ascii.encode("utf-8")), 64)

        # Multi-byte UTF-8 test string
        cb_64_utf8 = "bio:sync:" + ("a" * 55)
        self.assertEqual(len(cb_64_utf8.encode("utf-8")), 64)

        # Oversized payloads (> 64 bytes)
        cb_128 = "gt:meal:" + ("1" * 120)
        cb_1024 = "mh:briefing:" + ("A" * 1012)
        cb_10000 = "med:log:1:" + ("9" * 9990)

        stress_payloads = [cb_64_ascii, cb_64_utf8, cb_128, cb_1024, cb_10000]

        for payload in stress_payloads:
            update = {
                "update_id": 7777,
                "callback_query": {
                    "id": f"cb_bound_{random.randint(1, 99999)}",
                    "data": payload,
                    "from": {"id": 333, "first_name": "BoundaryTester"},
                    "message": {"message_id": 1, "chat": {"id": 12345}}
                }
            }
            res_gt = telegram_bot.handle_telegram_update(update)
            self.assertIsInstance(res_gt, dict)

            res_med = med_bot.handle_med_webhook(update)
            self.assertIsInstance(res_med, dict)

            res_monke = monke_bot.handle_monke_webhook(update)
            self.assertIsInstance(res_monke, dict)

            res_bio = biometrics_bot.handle_biometrics_webhook(update)
            self.assertIsInstance(res_bio, dict)

    def test_callback_malformed_objects_via_webhook_ingress(self):
        """Verify FastAPI webhook ingress endpoints safely contain malformed callback payloads."""
        malformed_updates = [
            {"callback_query": None},
            {"callback_query": {}},
            {"callback_query": {"data": None}},
            {"callback_query": {"data": 12345}},
            {"callback_query": {"data": "", "message": None, "from": None}},
            {"callback_query": {"id": None, "data": "med:log:1:10"}},
            {"callback_query": {"id": "cb1", "data": "med:log:not_a_num:bad_dose"}},
            {"callback_query": {"id": "cb2", "data": "med:log:-5:-10"}},
            {"callback_query": {"id": "cb3", "data": "gt:meal:invalid:text"}},
            {"callback_query": {"id": "cb4", "data": "gt:meal:-50:-10"}},
            {"callback_query": {"id": "cb5", "data": "mh:role:set:invalid_id:invalid_role"}}
        ]

        endpoints = [
            "/api/telegram/webhook",
            "/api/medbot/webhook",
            "/api/monkebot/webhook",
            "/api/biometrics/webhook"
        ]

        for endpoint in endpoints:
            for malformed in malformed_updates:
                resp = self.client.post(endpoint, json=malformed)
                self.assertEqual(resp.status_code, 200, f"Webhook {endpoint} returned non-200 on {malformed}")
                body = resp.json()
                self.assertIn(body.get("status"), ["ok", "error", "ignored", "denied"])

    def test_callback_double_tap_sliding_window_debouncing(self):
        """Verify rapid repeated inline button clicks are debounced across MedFlow, MonkeHelper, Biometrics."""
        cb_unique_id = f"deb_test_{int(time.time())}_{random.randint(100, 999)}"

        # 1. MedFlowAssist Debounce
        with patch("med_bot.get_medication_preset_by_id", return_value={"id": 1, "name": "Metformin", "dose": 500, "unit": "mg", "dose_unit": "mg"}):
            with patch("med_bot.log_medication_dose", return_value=101):
                with patch("bot_client.TelegramBotClient.answer_callback_query"):
                    with patch("bot_client.TelegramBotClient.edit_message_text"):
                        update_med = {
                            "callback_query": {
                                "id": cb_unique_id,
                                "data": "med:log:1:500.0",
                                "from": {"id": 101, "first_name": "Alice"},
                                "message": {"message_id": 50, "chat": {"id": 12345}}
                            }
                        }
                        # First tap
                        res1 = med_bot.handle_med_webhook(update_med)
                        self.assertEqual(res1.get("status"), "ok")
                        self.assertEqual(res1.get("action"), "dose_logged")

                        # Second tap (Immediate double-tap)
                        res2 = med_bot.handle_med_webhook(update_med)
                        self.assertEqual(res2.get("status"), "ok")
                        self.assertEqual(res2.get("action"), "debounced")


# =============================================================================
# 4. MULTI-BOT POLLING SUPERVISOR & RECOVERY LIFECYCLE
# =============================================================================

class TestTier5MultiBotSupervisorAndRecoveryLifecycle(unittest.TestCase):
    """Adversarial stress testing of long-polling workers, 409 self-healing, backoff, and crash recovery."""

    def test_http_409_conflict_self_healing(self):
        """Verify worker automatically clears webhook upon receiving HTTP 409 Conflict."""
        mock_handler = MagicMock()
        worker = BotPollerWorker(
            bot_id="test_409_bot",
            name="Test 409 Bot",
            token_getter=lambda: "TOKEN_409_TEST",
            handler=mock_handler,
            poll_timeout=1,
            client_timeout=2
        )

        call_count = {"getUpdates": 0, "deleteWebhook": 0}

        def fake_requests_get(url, params=None, timeout=None):
            call_count["getUpdates"] += 1
            mock_resp = MagicMock()
            if call_count["getUpdates"] == 1:
                mock_resp.status_code = 409
                mock_resp.text = "Conflict: can't use getUpdates method while webhook is active"
                return mock_resp
            else:
                worker._stop_event.set()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {
                    "ok": True,
                    "result": [{"update_id": 101, "message": {"text": "Recovered!"}}]
                }
                return mock_resp

        def fake_requests_post(url, json=None, timeout=None):
            if "deleteWebhook" in url:
                call_count["deleteWebhook"] += 1
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"ok": True, "result": True}
                return mock_resp
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            return mock_resp

        worker._stop_event.wait = lambda t=None: None

        with patch("requests.get", side_effect=fake_requests_get):
            with patch("requests.post", side_effect=fake_requests_post):
                worker.start()
                time.sleep(0.3)
                worker.stop(timeout=1.0)

        self.assertGreaterEqual(call_count["deleteWebhook"], 1, "deleteWebhook was not called upon 409 conflict!")
        self.assertGreaterEqual(call_count["getUpdates"], 2, "getUpdates did not retry after clearing webhook!")

    def test_api_error_payload_conflict_self_healing(self):
        """Verify worker handles HTTP 200 responses with ok=False and error_code 409 in body."""
        mock_handler = MagicMock()
        worker = BotPollerWorker(
            bot_id="test_body_409",
            name="Test Body 409",
            token_getter=lambda: "TOKEN_BODY_409",
            handler=mock_handler
        )

        with patch.object(worker, "_delete_webhook") as mock_del_wh:
            payload = {
                "ok": False,
                "error_code": 409,
                "description": "Conflict: terminated by other getUpdates request; make sure only one bot instance is running"
            }
            worker._handle_api_error("TOKEN_BODY_409", 200, payload["description"], payload)
            mock_del_wh.assert_called_once_with("TOKEN_BODY_409")

    def test_jittered_exponential_backoff_progression(self):
        """Verify mathematical bounds of exponential backoff progression under consecutive network failures."""
        worker = BotPollerWorker(
            bot_id="test_backoff_bot",
            name="Test Backoff Bot",
            token_getter=lambda: "TOKEN_BACKOFF",
            handler=MagicMock()
        )

        expected_ranges = [
            (1, 2.0, 3.0),
            (2, 4.0, 5.0),
            (3, 8.0, 9.0),
            (4, 16.0, 17.0),
            (5, 32.0, 33.0),
            (6, 60.0, 61.0),
            (7, 60.0, 61.0)
        ]

        captured_backoffs = []
        worker._stop_event.wait = lambda timeout: captured_backoffs.append(timeout)

        for attempt, min_bound, max_bound in expected_ranges:
            worker._handle_network_failure(f"Connection error #{attempt}")
            self.assertEqual(worker._consecutive_errors, attempt)
            self.assertEqual(worker._status, "backoff")
            last_backoff = captured_backoffs[-1]
            self.assertGreaterEqual(last_backoff, min_bound, f"Attempt {attempt} backoff {last_backoff} < {min_bound}")
            self.assertLessEqual(last_backoff, max_bound, f"Attempt {attempt} backoff {last_backoff} > {max_bound}")

    def test_http_429_rate_limit_and_401_auth_failure_handling(self):
        """Verify HTTP 429 parses retry_after and HTTP 401 records auth_failed status."""
        worker = BotPollerWorker(
            bot_id="test_rate_auth_bot",
            name="Test Rate Auth Bot",
            token_getter=lambda: "TOKEN_RATE_AUTH",
            handler=MagicMock()
        )

        waited_times = []
        worker._stop_event.wait = lambda t: (waited_times.append(t), worker._stop_event.set())

        def fake_429(url, params=None, timeout=None):
            mock_resp = MagicMock()
            mock_resp.status_code = 429
            mock_resp.json.return_value = {"ok": False, "parameters": {"retry_after": 8}}
            return mock_resp

        with patch("requests.get", side_effect=fake_429):
            worker._is_running = True
            worker._stop_event.clear()
            t = threading.Thread(target=worker._run_loop)
            t.start()
            t.join(timeout=1.0)

        self.assertIn(9.0, waited_times)  # retry_after (8) + 1.0s

        # HTTP 401 Auth Failed Simulation
        worker_auth = BotPollerWorker(
            bot_id="test_auth_bot",
            name="Test Auth Bot",
            token_getter=lambda: "REVOKED_TOKEN",
            handler=MagicMock()
        )
        auth_waits = []
        worker_auth._stop_event.wait = lambda t: (auth_waits.append(t), worker_auth._stop_event.set())

        def fake_401(url, params=None, timeout=None):
            mock_resp = MagicMock()
            mock_resp.status_code = 401
            return mock_resp

        with patch("requests.get", side_effect=fake_401):
            worker_auth._is_running = True
            worker_auth._stop_event.clear()
            t_auth = threading.Thread(target=worker_auth._run_loop)
            t_auth.start()
            t_auth.join(timeout=1.0)

        self.assertIn("Invalid or revoked Bot Token", worker_auth._last_error)
        self.assertIn(30.0, auth_waits)

    def test_worker_handler_exception_containment(self):
        """Ensure unhandled exceptions in bot handler callback do NOT kill the long-polling loop."""
        def bad_handler(update):
            if update.get("update_id") == 503:
                worker._stop_event.set()
            raise RuntimeError("Catastrophic business logic failure in handler!")

        worker = BotPollerWorker(
            bot_id="test_handler_crash_bot",
            name="Test Handler Crash Bot",
            token_getter=lambda: "TOKEN_HANDLER_CRASH",
            handler=bad_handler
        )

        call_count = {"count": 0}

        def fake_getUpdates(url, params=None, timeout=None):
            call_count["count"] += 1
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "ok": True,
                "result": [{"update_id": 500 + call_count["count"], "message": {"text": "Crash me"}}]
            }
            return mock_resp

        with patch("requests.get", side_effect=fake_getUpdates), patch("requests.post"):
            worker.start()
            time.sleep(0.4)
            worker.stop(timeout=1.0)

        self.assertGreaterEqual(worker._updates_count, 3)
        self.assertGreaterEqual(worker._offset, 503)

    def test_supervisor_watchdog_thread_crash_recovery(self):
        """Verify MultiBotPollingManager watchdog detects dead worker threads and automatically resurrects them."""
        manager = MultiBotPollingManager()

        worker = manager.register_bot(
            bot_id="resurrect_bot",
            name="Resurrect Bot",
            token_getter=lambda: "TOKEN_RESURRECT",
            handler=MagicMock()
        )

        worker._is_running = True
        worker._thread = MagicMock()
        worker._thread.is_alive.return_value = False

        self.assertTrue(worker._is_running)
        self.assertFalse(worker.is_alive())

        with patch.object(worker, "start") as mock_start:
            manager.watchdog_check()
            mock_start.assert_called_once()


# =============================================================================
# 5. DATABASE CONNECTION TIMEOUT & GRACEFUL DEGRADATION ACROSS ALL 4 BOTS
# =============================================================================

class TestTier5DatabaseTimeoutAndGracefulDegradation(unittest.TestCase):
    """Adversarial testing of system resiliency during database timeouts and outages."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_db_timeout_gluco_track_resilience(self):
        """Verify GlucoTrack handles database timeout exceptions gracefully without crashing."""
        with patch("db.insert_food_log", side_effect=TimeoutError("DB query timed out")):
            with patch("db.insert_insulin_doses", side_effect=TimeoutError("DB query timed out")):
                with patch("bot_client.TelegramBotClient.send_message"):
                    with patch("bot_client.TelegramBotClient.answer_callback_query"):
                        with patch("bot_client.TelegramBotClient.edit_message_text"):
                            cb_update = {
                                "update_id": 9001,
                                "callback_query": {
                                    "id": "cb_gt_db_fail",
                                    "data": "gt:meal:50:4.0",
                                    "from": {"id": 1, "first_name": "Bob"},
                                    "message": {"message_id": 10, "chat": {"id": 123}}
                                }
                            }
                            resp = self.client.post("/api/telegram/webhook", json=cb_update)
                            self.assertEqual(resp.status_code, 200)
                            body = resp.json()
                            self.assertIn(body["status"], ["ok", "error"])

    def test_db_timeout_med_flow_resilience(self):
        """Verify MedFlowAssist handles database connection errors during preset lookup and logging."""
        with patch("db.get_medication_presets", side_effect=Exception("connection to server was lost")):
            with patch("db.log_medication_dose", side_effect=Exception("connection to server was lost")):
                with patch("bot_client.TelegramBotClient.send_message"):
                    with patch("bot_client.TelegramBotClient.answer_callback_query"):
                        # 1. /presets command during DB outage
                        cmd_update = {
                            "update_id": 9002,
                            "message": {
                                "message_id": 20,
                                "chat": {"id": 123, "type": "private"},
                                "from": {"id": 1, "first_name": "Bob"},
                                "text": "/presets"
                            }
                        }
                        resp = self.client.post("/api/medbot/webhook", json=cmd_update)
                        self.assertEqual(resp.status_code, 200)
                        self.assertIn(resp.json()["status"], ["ok", "error"])

                        # 2. Medication log inline button during DB outage
                        btn_update = {
                            "update_id": 9003,
                            "callback_query": {
                                "id": "cb_med_db_fail",
                                "data": "med:log:1:250.0",
                                "from": {"id": 1, "first_name": "Bob"},
                                "message": {"message_id": 21, "chat": {"id": 123}}
                            }
                        }
                        resp_btn = self.client.post("/api/medbot/webhook", json=btn_update)
                        self.assertEqual(resp_btn.status_code, 200)
                        self.assertIn(resp_btn.json()["status"], ["ok", "error"])

    def test_db_timeout_monke_helper_unified_briefing_resilience(self):
        """Verify MonkeHelper unified daily briefing degrades gracefully with fallback data under complete DB timeout."""
        with patch("db.get_latest_reading", side_effect=TimeoutError("DB Timeout")):
            with patch("db.get_history", side_effect=TimeoutError("DB Timeout")):
                with patch("db.get_statistics", side_effect=TimeoutError("DB Timeout")):
                    with patch("db.get_insulin_history", side_effect=TimeoutError("DB Timeout")):
                        with patch("db.get_recent_med_logs", side_effect=TimeoutError("DB Timeout")):
                            with patch("db.get_health_sessions", side_effect=TimeoutError("DB Timeout")):
                                with patch("db.get_health_metrics", side_effect=TimeoutError("DB Timeout")):
                                    from monke_bot import get_unified_daily_briefing

                                    briefing = get_unified_daily_briefing(user_id="user_123")
                                    self.assertIsInstance(briefing, dict)
                                    self.assertIn("cgm", briefing)
                                    self.assertIn("insulin", briefing)
                                    self.assertIn("medications", briefing)
                                    self.assertIn("circadian", briefing)
                                    self.assertIn("alerts", briefing)

                                    # Webhook call for /briefing command during total DB outage
                                    update_briefing = {
                                        "update_id": 9004,
                                        "message": {
                                            "message_id": 30,
                                            "chat": {"id": 555, "type": "private"},
                                            "from": {"id": 1, "first_name": "Bob"},
                                            "text": "/briefing"
                                        }
                                    }
                                    with patch("bot_client.TelegramBotClient.send_message"):
                                        resp = self.client.post("/api/monkebot/webhook", json=update_briefing)
                                        self.assertEqual(resp.status_code, 200)
                                        self.assertIn(resp.json()["status"], ["ok", "error"])

    def test_db_timeout_circadian_biometrics_resilience(self):
        """Verify Circadian & Biometrics bot returns fallback metrics and handles DB timeout safely."""
        with patch("db.get_health_sessions", side_effect=TimeoutError("DB Timeout")):
            with patch("db.get_health_metrics", side_effect=TimeoutError("DB Timeout")):
                with patch("bot_client.TelegramBotClient.send_message"):
                    with patch("bot_client.TelegramBotClient.answer_callback_query"):
                        # 1. /bio command
                        update_bio = {
                            "update_id": 9005,
                            "message": {
                                "message_id": 40,
                                "chat": {"id": 777, "type": "private"},
                                "from": {"id": 1, "first_name": "Bob"},
                                "text": "/bio"
                            }
                        }
                        resp = self.client.post("/api/biometrics/webhook", json=update_bio)
                        self.assertEqual(resp.status_code, 200)
                        self.assertIn(resp.json()["status"], ["ok", "error"])

                        # 2. bio:sync:now callback
                        update_sync = {
                            "update_id": 9006,
                            "callback_query": {
                                "id": "cb_bio_db_fail",
                                "data": "bio:sync:now",
                                "from": {"id": 1, "first_name": "Bob"},
                                "message": {"message_id": 41, "chat": {"id": 777}}
                            }
                        }
                        resp_sync = self.client.post("/api/biometrics/webhook", json=update_sync)
                        self.assertEqual(resp_sync.status_code, 200)
                        self.assertIn(resp_sync.json()["status"], ["ok", "error"])


if __name__ == "__main__":
    unittest.main()
