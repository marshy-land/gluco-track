"""
tests/test_adversarial_m2.py
Adversarial Stress Test Suite for Milestone 2:
Group vs. DM Dual Interaction Architecture & MedFlowAssist Bot (@medflowassist_bot).

Covers:
1. Multi-word and unicode medication names, extreme doses, non-numeric values, negative numbers, SQL injection strings.
2. Concurrent button clicks / double-tap debouncing (sliding-window TTL & multi-caregiver concurrency).
3. Group chat noise injection (verify ambient chatter, foreign bot commands, and irrelevant health discussions are ignored).
4. Empty history, large limits, pagination, reverse chronological ordering, and elapsed time formatting.
5. Malformed payloads, missing fields, foreign callback namespaces, and corrupted callback data.
"""

import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
import time
import math
import uuid

import db
import med_bot
from bot_client import get_bot_client


class TestAdversarialMedicationNamesAndDoses(unittest.TestCase):
    """
    Adversarial testing of medication names and dosage parsing:
    - Multi-word, unicode, symbols, Cyrillic, Greek, Arabic, CJK, emojis
    - SQL injection strings, apostrophes, quotes, extreme string lengths
    - Extreme dose bounds (microdoses, giant values, scientific notation)
    - Rejection of zero, negative, NaN, Inf, non-numeric strings
    """

    @classmethod
    def setUpClass(cls):
        db.init_db()

    @patch("bot_client.TelegramBotClient.send_message")
    def test_unicode_and_multilingual_medication_names(self, mock_send):
        mock_send.return_value = {"ok": True, "result": {"message_id": 1001}}

        test_cases = [
            # Cyrillic
            ("Парацетамол", 500.0, "мг", "/addpreset Парацетамол 500 мг"),
            # Greek
            ("Παρακεταμόλη", 1000.0, "mg", "/addpreset Παρακεταμόλη 1000 mg"),
            # Arabic
            ("باراسيتامول", 250.0, "mg", "/addpreset باراسيتامول 250 mg"),
            # CJK (Chinese)
            ("扑热息痛", 500.0, "mg", "/addpreset 扑热息痛 500 mg"),
            # Japanese Katakana
            ("アセトアミノフェン", 300.0, "mg", "/addpreset アセトアミノフェン 300 mg"),
            # Emojis and Complex Brand / Formula names
            ("💊 Omega-3 (EPA/DHA) Forte", 1000.0, "mg", "/addpreset 💊 Omega-3 (EPA/DHA) Forte 1000 mg"),
            ("Vitamin D3 + K2 Complex", 5000.0, "IU", "/addpreset Vitamin D3 + K2 Complex 5000 IU"),
            ("CoQ10 / Ubiquinol 100mg Extended", 100.0, "mg", "/addpreset CoQ10 / Ubiquinol 100mg Extended 100 mg"),
            # Apostrophes, quotes, and punctuation
            ("Dr. O'Connor's Rapid Pain Relief", 200.0, "mg", "/addpreset Dr. O'Connor's Rapid Pain Relief 200 mg"),
            ("Aspirin \"Low-Dose\"", 81.0, "mg", "/addpreset Aspirin \"Low-Dose\" 81 mg"),
            # SQL injection attempt string
            ("Med; DROP TABLE medication_logs; --", 50.0, "mg", "/addpreset Med; DROP TABLE medication_logs; -- 50 mg"),
        ]

        for expected_name, expected_dose, expected_unit, command_text in test_cases:
            update = {
                "message": {
                    "message_id": 10,
                    "chat": {"id": 123456, "type": "private"},
                    "from": {"first_name": "Adversary", "username": "adv_tester"},
                    "text": command_text
                }
            }
            res = med_bot.handle_med_webhook(update)
            self.assertEqual(res.get("status"), "ok", f"Failed for command: {command_text}")
            self.assertEqual(res.get("action"), "preset_added")
            self.assertEqual(res.get("details", {}).get("name"), expected_name)
            self.assertAlmostEqual(res.get("details", {}).get("dose"), expected_dose)
            self.assertEqual(res.get("details", {}).get("unit"), expected_unit)

            # Verify persistent DB retrieval
            preset = db.get_medication_preset_by_name(expected_name)
            self.assertIsNotNone(preset, f"Preset '{expected_name}' not found in DB")
            self.assertEqual(preset["name"], expected_name)
            self.assertAlmostEqual(float(preset["default_dose"]), expected_dose)
            self.assertEqual(preset["dose_unit"], expected_unit)

    @patch("bot_client.TelegramBotClient.send_message")
    def test_extreme_and_edge_case_doses(self, mock_send):
        mock_send.return_value = {"ok": True, "result": {"message_id": 1002}}

        # 1. Micro-doses / Small floats
        update_micro = {
            "message": {
                "chat": {"id": 123456, "type": "private"},
                "text": "/addpreset Fentanyl Transdermal 0.0125 mg"
            }
        }
        res_micro = med_bot.handle_med_webhook(update_micro)
        self.assertEqual(res_micro.get("status"), "ok")
        self.assertEqual(res_micro.get("details", {}).get("dose"), 0.0125)

        # 2. Large doses & Scientific notation
        update_sci = {
            "message": {
                "chat": {"id": 123456, "type": "private"},
                "text": "/addpreset Potassium Chloride 1e4 mcg"
            }
        }
        res_sci = med_bot.handle_med_webhook(update_sci)
        self.assertEqual(res_sci.get("status"), "ok")
        self.assertEqual(res_sci.get("details", {}).get("dose"), 10000.0)

        # 3. Giant numbers
        update_huge = {
            "message": {
                "chat": {"id": 123456, "type": "private"},
                "text": "/addpreset MegaEnzyme 999999999 units"
            }
        }
        res_huge = med_bot.handle_med_webhook(update_huge)
        self.assertEqual(res_huge.get("status"), "ok")
        self.assertEqual(res_huge.get("details", {}).get("dose"), 999999999.0)

    @patch("bot_client.TelegramBotClient.send_message")
    def test_rejection_of_invalid_and_negative_doses(self, mock_send):
        mock_send.return_value = {"ok": True}

        invalid_commands = [
            # Zero dose
            ("/addpreset Insulin Glargine 0 units", "invalid_dose_value"),
            ("/addpreset Metformin 0.0 mg", "invalid_dose_value"),
            # Negative doses
            ("/addpreset Lisinopril -10 mg", "invalid_dose_value"),
            ("/addpreset Atorvastatin -0.0001 mg", "invalid_dose_value"),
            ("/addpreset Morphine -1e3 mg", "invalid_dose_value"),
            # Non-numeric string doses
            ("/addpreset Tylenol five mg", "invalid_dose_format"),
            ("/addpreset Tylenol 10/20 mg", "invalid_dose_format"),
            ("/addpreset Tylenol two_tablets mg", "invalid_dose_format"),
            # NaN / Inf attempts
            ("/addpreset Glucagon nan mg", "invalid_dose_value"),
            ("/addpreset Glucagon inf mg", "invalid_dose_value"),
            ("/addpreset Glucagon -inf mg", "invalid_dose_value"),
            ("/addpreset Glucagon +inf mg", "invalid_dose_value"),
            # Truncated or missing syntax
            ("/addpreset", "invalid_format"),
            ("/addpreset Metformin", "invalid_format"),
            ("/addpreset Metformin 500", "invalid_format"),
        ]

        for cmd, expected_action in invalid_commands:
            update = {
                "message": {
                    "chat": {"id": 123456, "type": "private"},
                    "text": cmd
                }
            }
            res = med_bot.handle_med_webhook(update)
            self.assertEqual(res.get("status"), "error", f"Expected error for cmd: {cmd}")
            self.assertEqual(res.get("action"), expected_action, f"Expected action '{expected_action}' for cmd: {cmd}")

    @patch("bot_client.TelegramBotClient.send_message")
    def test_extreme_string_lengths_and_whitespace_padding(self, mock_send):
        mock_send.return_value = {"ok": True}

        # Excessive whitespace padding
        update_ws = {
            "message": {
                "chat": {"id": 123456, "type": "private"},
                "text": "   /addpreset     Hydrochlorothiazide      25.0      mg    "
            }
        }
        res_ws = med_bot.handle_med_webhook(update_ws)
        self.assertEqual(res_ws.get("status"), "ok")
        self.assertEqual(res_ws.get("details", {}).get("name"), "Hydrochlorothiazide")

        # Name exceeding 255 characters
        long_name = "SuperCalciMagZinc" + ("VeryLongMedicationName" * 15)
        self.assertGreater(len(long_name), 255)
        update_long = {
            "message": {
                "chat": {"id": 123456, "type": "private"},
                "text": f"/addpreset {long_name} 100 mg"
            }
        }
        res_long = med_bot.handle_med_webhook(update_long)
        self.assertEqual(res_long.get("status"), "error")
        self.assertEqual(res_long.get("action"), "missing_name")


class TestAdversarialConcurrencyAndDebouncing(unittest.TestCase):
    """
    Adversarial testing of button clicks and race conditions:
    - Rapid-fire identical callback_query_id clicks (double-tap debouncing)
    - Multi-caregiver concurrent logging with separate callback IDs
    - Debouncing sliding-window TTL expiration
    - Dismissal debouncing
    """

    @classmethod
    def setUpClass(cls):
        db.init_db()

    @patch("bot_client.TelegramBotClient.send_message")
    @patch("bot_client.TelegramBotClient.answer_callback_query")
    @patch("bot_client.TelegramBotClient.edit_message_text")
    def test_rapid_double_tap_debouncing(self, mock_edit, mock_answer, mock_send):
        mock_answer.return_value = {"ok": True}
        mock_edit.return_value = {"ok": True}

        # Create preset
        med_id = db.add_medication_preset("AdversarialDoseMed", 50.0, "mg")

        cb_id = f"cb_rapid_double_tap_{int(time.time() * 1000)}"
        callback_payload = {
            "callback_query": {
                "id": cb_id,
                "data": f"med:log:{med_id}:50.0",
                "message": {
                    "message_id": 901,
                    "chat": {"id": -100999888, "type": "supergroup"}
                },
                "from": {"first_name": "Alice", "last_name": "Smith", "username": "alice_care"}
            }
        }

        # First tap -> Should succeed and log dose
        res1 = med_bot.handle_med_webhook(callback_payload)
        self.assertEqual(res1.get("status"), "ok")
        self.assertEqual(res1.get("action"), "dose_logged")
        self.assertEqual(res1.get("details", {}).get("user"), "Alice Smith")
        first_log_id = res1.get("details", {}).get("log_id")
        self.assertIsNotNone(first_log_id)

        # Immediate Second Tap (duplicate cb_id) -> Should be debounced
        res2 = med_bot.handle_med_webhook(callback_payload)
        self.assertEqual(res2.get("status"), "ok")
        self.assertEqual(res2.get("action"), "debounced")
        self.assertEqual(res2.get("details", {}).get("callback_id"), cb_id)

        # Rapid succession of 10 more taps with identical cb_id
        for i in range(10):
            res_burst = med_bot.handle_med_webhook(callback_payload)
            self.assertEqual(res_burst.get("action"), "debounced")

    @patch("bot_client.TelegramBotClient.send_message")
    @patch("bot_client.TelegramBotClient.answer_callback_query")
    @patch("bot_client.TelegramBotClient.edit_message_text")
    def test_multi_caregiver_concurrent_clicks(self, mock_edit, mock_answer, mock_send):
        """Two different caregivers click buttons concurrently with distinct callback IDs."""
        mock_answer.return_value = {"ok": True}
        mock_edit.return_value = {"ok": True}

        med_id = db.add_medication_preset("ConcurrentPRNMed", 20.0, "mg")

        cb_alice = {
            "callback_query": {
                "id": f"cb_alice_{time.time()}",
                "data": f"med:log:{med_id}:20.0",
                "message": {"message_id": 501, "chat": {"id": -100112233, "type": "supergroup"}},
                "from": {"first_name": "Alice", "last_name": "Walker"}
            }
        }

        cb_bob = {
            "callback_query": {
                "id": f"cb_bob_{time.time()}",
                "data": f"med:log:{med_id}:20.0",
                "message": {"message_id": 501, "chat": {"id": -100112233, "type": "supergroup"}},
                "from": {"first_name": "Bob", "last_name": "Dylan"}
            }
        }

        res_a = med_bot.handle_med_webhook(cb_alice)
        res_b = med_bot.handle_med_webhook(cb_bob)

        self.assertEqual(res_a.get("status"), "ok")
        self.assertEqual(res_a.get("action"), "dose_logged")
        self.assertEqual(res_a.get("details", {}).get("user"), "Alice Walker")

        self.assertEqual(res_b.get("status"), "ok")
        self.assertEqual(res_b.get("action"), "dose_logged")
        self.assertEqual(res_b.get("details", {}).get("user"), "Bob Dylan")

    @patch("bot_client.TelegramBotClient.send_message")
    @patch("bot_client.TelegramBotClient.answer_callback_query")
    @patch("bot_client.TelegramBotClient.edit_message_text")
    def test_debouncing_ttl_expiration(self, mock_edit, mock_answer, mock_send):
        """Simulate TTL expiration (>60s) in _processed_callbacks cache."""
        mock_answer.return_value = {"ok": True}
        mock_edit.return_value = {"ok": True}

        med_id = db.add_medication_preset("TTLDebounceMed", 10.0, "mg")
        cb_id = "cb_ttl_test_12345"

        payload = {
            "callback_query": {
                "id": cb_id,
                "data": f"med:log:{med_id}:10.0",
                "message": {"message_id": 601, "chat": {"id": 1234, "type": "private"}},
                "from": {"first_name": "Tester"}
            }
        }

        # First click
        res1 = med_bot.handle_med_webhook(payload)
        self.assertEqual(res1.get("action"), "dose_logged")

        # Force timestamp back 70 seconds to simulate time travel past 60s TTL
        med_bot._processed_callbacks[cb_id] = time.time() - 70.0

        # Click again after TTL -> Should be accepted as new
        res2 = med_bot.handle_med_webhook(payload)
        self.assertEqual(res2.get("action"), "dose_logged")


class TestAdversarialGroupChatNoiseInjection(unittest.TestCase):
    """
    Adversarial testing of group chat filtering:
    - Ambient conversational chatter (must be completely IGNORED)
    - Medical/health conversations without command (must be IGNORED)
    - Commands directed at other bots (e.g. /status@gluco_track_bot, /bio@circadian_bot) (must be IGNORED)
    - Targeted commands and mentions (must be PROCESSED)
    - Reply Keyboard suppression in group chats vs presence in DM
    """

    @classmethod
    def setUpClass(cls):
        db.init_db()

    @patch("bot_client.TelegramBotClient.send_message")
    def test_ambient_group_noise_injection(self, mock_send):
        mock_send.return_value = {"ok": True}

        noise_messages = [
            "Good morning care team!",
            "Did anyone check on grandma today?",
            "I took some aspirin and drank 500ml water",
            "Insulin dose was at 8 AM",
            "What is the plan for tomorrow's clinic visit?",
            "1234567890",
            "💊🩺🏥 Emergency contact updated",
            "https://example.com/medical-study",
            "lol that was funny",
            "preset: 500mg metformin", # Contains keywords but no command / mention
        ]

        for text in noise_messages:
            for chat_type in ["group", "supergroup"]:
                update = {
                    "message": {
                        "message_id": 100,
                        "chat": {"id": -100555666, "type": chat_type, "title": "Care Circle Group"},
                        "from": {"first_name": "FamilyMember"},
                        "text": text
                    }
                }
                res = med_bot.handle_med_webhook(update)
                self.assertEqual(
                    res.get("status"), "ignored",
                    f"Ambient noise was NOT ignored for '{text}' in {chat_type}"
                )
                self.assertEqual(res.get("action"), "group_noise_ignored")
                self.assertEqual(res.get("reason"), "ambient_noise_filtered")
                # Ensure bot did NOT send any reply to group for noise
                mock_send.assert_not_called()

    @patch("bot_client.TelegramBotClient.send_message")
    def test_foreign_bot_commands_in_group(self, mock_send):
        mock_send.return_value = {"ok": True}

        foreign_commands = [
            "/status@gluco_track_bot",
            "/briefing@monkehelper_bot",
            "/bio@circadian_bot",
            "/sleep@biometrics_bot",
            "/help@some_other_bot",
            "/log@gluco_track_bot", # command has /log but directed to glucotrack
        ]

        for cmd in foreign_commands:
            update = {
                "message": {
                    "message_id": 101,
                    "chat": {"id": -100555666, "type": "supergroup"},
                    "from": {"first_name": "Caregiver"},
                    "text": cmd
                }
            }
            res = med_bot.handle_med_webhook(update)
            self.assertEqual(res.get("status"), "ignored", f"Foreign command '{cmd}' was not ignored")
            self.assertEqual(res.get("action"), "command_for_other_bot")
            mock_send.assert_not_called()

    @patch("bot_client.TelegramBotClient.send_message")
    def test_targeted_group_commands_and_mentions(self, mock_send):
        mock_send.return_value = {"ok": True}

        db.add_medication_preset("GroupTestMed", 100.0, "mg")

        targeted_commands = [
            "/log@medflowassist_bot",
            "/presets@medflowassist_bot",
            "/history@medflowassist_bot",
            "/summary@medflowassist_bot",
            "/addpreset@medflowassist_bot Melatonin 3 mg",
            "/log", # Plain /log in group is also treated as command
            "/presets",
        ]

        for cmd in targeted_commands:
            update = {
                "message": {
                    "message_id": 102,
                    "chat": {"id": -100555666, "type": "group"},
                    "from": {"first_name": "NurseAlice"},
                    "text": cmd
                }
            }
            res = med_bot.handle_med_webhook(update)
            self.assertEqual(res.get("status"), "ok", f"Targeted command '{cmd}' failed in group")
            self.assertIn(res.get("action"), [
                "log_menu_sent", "presets_listed", "history_viewed", "summary_viewed", "preset_added"
            ])

    @patch("bot_client.TelegramBotClient.send_message")
    def test_group_vs_dm_reply_keyboard_behavior(self, mock_send):
        """In private DM chats, attach persistent ReplyKeyboardMarkup; in group chats, suppress it."""
        mock_send.return_value = {"ok": True}

        # 1. Private Chat DM
        update_dm = {
            "message": {
                "chat": {"id": 12345, "type": "private"},
                "text": "/help"
            }
        }
        res_dm = med_bot.handle_med_webhook(update_dm)
        self.assertEqual(res_dm.get("status"), "ok")
        self.assertIsNotNone(res_dm.get("reply_markup"))
        self.assertTrue(res_dm.get("reply_markup", {}).get("is_persistent"))
        self.assertEqual(len(res_dm.get("reply_markup", {}).get("keyboard", [])), 2)

        # 2. Group Chat
        update_group = {
            "message": {
                "chat": {"id": -100888999, "type": "supergroup"},
                "text": "/help@medflowassist_bot"
            }
        }
        res_group = med_bot.handle_med_webhook(update_group)
        self.assertEqual(res_group.get("status"), "ok")
        self.assertIsNone(res_group.get("reply_markup"), "Reply keyboard must be suppressed in group chats")


class TestAdversarialHistorySummaryAndPagination(unittest.TestCase):
    """
    Adversarial testing of history inspection, limits, pagination, and summaries:
    - Empty history handling (overall and for specific existing/non-existing medications)
    - Extreme limit values (0, negative, 500, giant integers)
    - Reverse chronological sort verification
    - Elapsed time calculations across multiple temporal milestones
    """

    @classmethod
    def setUpClass(cls):
        db.init_db()

    @patch("bot_client.TelegramBotClient.send_message")
    def test_empty_history_edge_cases(self, mock_send):
        mock_send.return_value = {"ok": True}

        # 1. Non-existent medication history query
        update_nonexistent = {
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/history CompletelyNonExistentDrug999"
            }
        }
        res_non = med_bot.handle_med_webhook(update_nonexistent)
        self.assertEqual(res_non.get("status"), "ok")
        self.assertEqual(res_non.get("count"), 0)
        self.assertIn("not found", res_non.get("text", ""))

        # 2. Preset exists but has 0 logged doses
        db.add_medication_preset("UnusedMedicationPreset", 10.0, "mg")
        update_unused = {
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/history UnusedMedicationPreset"
            }
        }
        res_unused = med_bot.handle_med_webhook(update_unused)
        self.assertEqual(res_unused.get("status"), "ok")
        self.assertEqual(res_unused.get("count"), 0)
        self.assertIn("No intake logs recorded yet for", res_unused.get("text", ""))

    @patch("bot_client.TelegramBotClient.send_message")
    def test_history_limit_and_pagination_boundaries(self, mock_send):
        mock_send.return_value = {"ok": True}

        unique_drug = f"PaginationStressDrug_{uuid.uuid4().hex[:8]}"
        med_id = db.add_medication_preset(unique_drug, 25.0, "mg")

        # Insert 30 doses spaced out in time
        base_time = datetime.now(timezone.utc)
        inserted_ids = []
        for i in range(30):
            ts = base_time - timedelta(minutes=i * 10)
            lid = db.log_medication_dose(med_id, 25.0, timestamp=ts, notes=f"Dose #{i+1}")
            inserted_ids.append(lid)

        # 1. Query with specific limit 5
        update_5 = {
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": f"/history {unique_drug} 5"
            }
        }
        res_5 = med_bot.handle_med_webhook(update_5)
        self.assertEqual(res_5.get("status"), "ok")
        self.assertEqual(res_5.get("count"), 5)

        # 2. Query with giant limit 500 (must clamp to 50 max)
        update_500 = {
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": f"/history {unique_drug} 500"
            }
        }
        res_500 = med_bot.handle_med_webhook(update_500)
        self.assertEqual(res_500.get("status"), "ok")
        self.assertEqual(res_500.get("count"), 30) # Total 30 in DB <= 50 cap

        # 3. Query with limit 0 (must clamp to min 1)
        update_0 = {
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": f"/history {unique_drug} 0"
            }
        }
        res_0 = med_bot.handle_med_webhook(update_0)
        self.assertEqual(res_0.get("status"), "ok")
        self.assertEqual(res_0.get("count"), 1)

        # 4. Multi-word drug name + limit
        multi_drug = f"Multi Word Pagination Drug {uuid.uuid4().hex[:6]}"
        db.add_medication_preset(multi_drug, 50.0, "mg")
        update_multi = {
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": f"/history {multi_drug} 3"
            }
        }
        res_multi = med_bot.handle_med_webhook(update_multi)
        self.assertEqual(res_multi.get("status"), "ok")
        self.assertEqual(res_multi.get("details", {}).get("filter"), multi_drug)

    def test_reverse_chronological_ordering_and_elapsed_calculations(self):
        """Verify strict reverse chronological sort order and elapsed time formatting."""
        unique_drug = f"OrderingVerificationDrug_{uuid.uuid4().hex[:8]}"
        med_id = db.add_medication_preset(unique_drug, 100.0, "mg")

        now = datetime.now(timezone.utc)
        t_recent = now - timedelta(minutes=5)
        t_mid = now - timedelta(hours=3, minutes=30)
        t_old = now - timedelta(days=2, hours=4)

        lid_old = db.log_medication_dose(med_id, 100.0, timestamp=t_old, notes="Old dose")
        lid_mid = db.log_medication_dose(med_id, 100.0, timestamp=t_mid, notes="Mid dose")
        lid_recent = db.log_medication_dose(med_id, 100.0, timestamp=t_recent, notes="Recent dose")

        logs = db.get_recent_med_logs(limit=10, medication_id=med_id)
        self.assertGreaterEqual(len(logs), 3)

        # Verify strict descending order
        self.assertEqual(logs[0]["id"], lid_recent)
        self.assertEqual(logs[1]["id"], lid_mid)
        self.assertEqual(logs[2]["id"], lid_old)

        # Check elapsed string outputs
        self.assertEqual(med_bot.format_elapsed_time(t_recent, now), "5m ago")
        self.assertEqual(med_bot.format_elapsed_time(t_mid, now), "3h 30m ago")
        self.assertEqual(med_bot.format_elapsed_time(t_old, now), "2d 4h ago")


class TestAdversarialMalformedPayloadsAndNamespaces(unittest.TestCase):
    """
    Adversarial testing of corrupt payloads, missing fields, and cross-bot namespaces:
    - Empty and None update dictionaries
    - Foreign callback query namespaces (gt:, mh:, bio:)
    - Corrupted callback_data strings
    """

    @classmethod
    def setUpClass(cls):
        db.init_db()

    def test_corrupt_and_empty_payloads(self):
        empty_payloads = [
            None,
            {},
            {"message": None},
            {"message": {}},
            {"callback_query": None},
            {"callback_query": {}},
            {"some_unsupported_field": 123},
        ]

        for p in empty_payloads:
            res = med_bot.handle_med_webhook(p)
            self.assertIn(res.get("status"), ["ok", "ignored"])

    def test_foreign_callback_namespace_rejection(self):
        foreign_callbacks = [
            "gt:meal:45",
            "gt:lantus:taken",
            "gt:corr:2.5",
            "mh:briefing:refresh",
            "mh:quiet:toggle",
            "mh:role:set:caregiver",
            "bio:sync:now",
            "bio:sleep:detail",
        ]

        for cb_data in foreign_callbacks:
            update = {
                "callback_query": {
                    "id": f"cb_foreign_{time.time()}",
                    "data": cb_data,
                    "message": {"message_id": 1, "chat": {"id": 123, "type": "private"}},
                    "from": {"first_name": "ForeignUser"}
                }
            }
            res = med_bot.handle_med_webhook(update)
            self.assertEqual(res.get("status"), "ignored", f"Foreign callback '{cb_data}' was not ignored")
            self.assertEqual(res.get("action"), "foreign_namespace_ignored")
            self.assertEqual(res.get("reason"), "foreign_namespace")

    @patch("bot_client.TelegramBotClient.answer_callback_query")
    def test_corrupted_callback_data(self, mock_answer):
        mock_answer.return_value = {"ok": True}

        corrupt_callbacks = [
            "med:log:not_an_int:not_a_float",
            "med:log:99999999:50.0", # Non-existent preset ID
            "med:log:",
            "med:unknown_action:foo",
            "med:del:invalid_id",
        ]

        for cb_data in corrupt_callbacks:
            update = {
                "callback_query": {
                    "id": f"cb_corrupt_{time.time()}",
                    "data": cb_data,
                    "message": {"message_id": 2, "chat": {"id": 123, "type": "private"}},
                    "from": {"first_name": "Tester"}
                }
            }
            res = med_bot.handle_med_webhook(update)
            self.assertIn(res.get("status"), ["ok", "error"])


if __name__ == "__main__":
    unittest.main()
