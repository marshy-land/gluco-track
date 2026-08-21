"""
tests/test_adversarial_m2_fuzz.py
Extended Adversarial Fuzzing Test Suite for Milestone 2:
MedFlowAssist Bot (@medflowassist_bot).

Deep fuzz testing covering:
1. Malformed callback payloads (type fuzzing, delimiter corruption, non-integer IDs, extreme strings)
2. Extensive null / None / missing field permutations across the Telegram update object hierarchy
3. Invalid / Boundary / Overflow doses in both callback buttons and text commands
4. Adversarial SQL injection strings & special characters in all bot input vectors
5. format_elapsed_time temporal fuzzing (future dates, naive/aware mix, corrupted strings, extreme timestamps)
"""

import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
import time
import math
import uuid

import db
import med_bot


class TestAdversarialFuzzCallbacks(unittest.TestCase):
    """
    Adversarial fuzz testing for callback query processing:
    - Non-string callback data (int, list, dict, None, bytes)
    - Corrupted delimiter syntax (missing parts, excessive parts, empty fields)
    - Non-integer med_id, negative med_id, float med_id, out-of-range med_id
    - Invalid doses in callback data (zero, negative, NaN, Inf, overflow)
    - Null / missing message, chat, and from sub-dictionaries
    """

    @classmethod
    def setUpClass(cls):
        db.init_db()

    @patch("bot_client.TelegramBotClient.send_message")
    @patch("bot_client.TelegramBotClient.answer_callback_query")
    @patch("bot_client.TelegramBotClient.edit_message_text")
    def test_fuzz_callback_data_types_and_values(self, mock_edit, mock_answer, mock_send):
        mock_answer.return_value = {"ok": True}
        mock_edit.return_value = {"ok": True}

        fuzz_callback_datas = [
            None,
            "",
            "   ",
            12345,
            12.34,
            [],
            {},
            True,
            False,
            "\x00\x00\x00",
            "med:",
            "med:log",
            "med:log:",
            "med:log::",
            "med:log:::",
            "med:log:not_an_int:10.0",
            "med:log:1.5:10.0",
            "med:log:-1:10.0",
            "med:log:0:10.0",
            "med:log:1:0",
            "med:log:1:-10",
            "med:log:1:-0.0001",
            "med:log:1:nan",
            "med:log:1:inf",
            "med:log:1:-inf",
            "med:log:1:1e999", # float overflow -> inf
            "med:log:1:not_a_float",
            "med:log:1:10.0:extra:extra2",
            "med:log:" + "9" * 100 + ":10.0",
            "med:del:",
            "med:del:not_an_id",
            "med:del:-5",
            "med:dismiss",
            "dismiss_med",
            "med:unknown_random_action_12345",
            "log_med:1:10.0",
            "log_med:invalid:format",
            "gt:random_foreign_action",
            "mh:random_foreign_action",
            "bio:random_foreign_action",
            "unknown_namespace:foo:bar",
            "💉💊🧪" * 20,
        ]

        for cb_data in fuzz_callback_datas:
            update = {
                "callback_query": {
                    "id": f"cb_fuzz_{uuid.uuid4().hex}",
                    "data": cb_data,
                    "message": {
                        "message_id": 100,
                        "chat": {"id": 123456, "type": "private"}
                    },
                    "from": {"first_name": "Fuzzer", "username": "fuzz_tester"}
                }
            }
            res = med_bot.handle_med_webhook(update)
            self.assertIsInstance(res, dict)
            self.assertIn(res.get("status"), ["ok", "ignored", "error"])

    @patch("bot_client.TelegramBotClient.send_message")
    @patch("bot_client.TelegramBotClient.answer_callback_query")
    @patch("bot_client.TelegramBotClient.edit_message_text")
    def test_fuzz_null_and_malformed_callback_subfields(self, mock_edit, mock_answer, mock_send):
        mock_answer.return_value = {"ok": True}
        mock_edit.return_value = {"ok": True}

        # Create a valid preset to test against
        med_id = db.add_medication_preset(f"NullFieldFuzz_{uuid.uuid4().hex[:6]}", 10.0, "mg")

        subfield_mutations = [
            # Missing or None 'id'
            {"data": f"med:log:{med_id}:10.0", "message": {"message_id": 1, "chat": {"id": 123}}, "from": {"first_name": "A"}},
            {"id": None, "data": f"med:log:{med_id}:10.0", "message": {"message_id": 1, "chat": {"id": 123}}, "from": {"first_name": "A"}},
            # Missing or None 'message'
            {"id": "cb1", "data": f"med:log:{med_id}:10.0", "from": {"first_name": "A"}},
            {"id": "cb2", "data": f"med:log:{med_id}:10.0", "message": None, "from": {"first_name": "A"}},
            {"id": "cb3", "data": f"med:log:{med_id}:10.0", "message": {}, "from": {"first_name": "A"}},
            # Corrupted message fields
            {"id": "cb4", "data": f"med:log:{med_id}:10.0", "message": {"message_id": None, "chat": None}, "from": {"first_name": "A"}},
            {"id": "cb5", "data": f"med:log:{med_id}:10.0", "message": {"message_id": "not_an_int", "chat": {"id": None}}, "from": {"first_name": "A"}},
            # Missing or None 'from'
            {"id": "cb6", "data": f"med:log:{med_id}:10.0", "message": {"message_id": 1, "chat": {"id": 123}}},
            {"id": "cb7", "data": f"med:log:{med_id}:10.0", "message": {"message_id": 1, "chat": {"id": 123}}, "from": None},
            {"id": "cb8", "data": f"med:log:{med_id}:10.0", "message": {"message_id": 1, "chat": {"id": 123}}, "from": {}},
            {"id": "cb9", "data": f"med:log:{med_id}:10.0", "message": {"message_id": 1, "chat": {"id": 123}}, "from": {"first_name": None, "last_name": None, "username": None}},
            {"id": "cb10", "data": f"med:log:{med_id}:10.0", "message": {"message_id": 1, "chat": {"id": 123}}, "from": {"first_name": 123, "last_name": 456, "username": 789}},
        ]

        for mutation in subfield_mutations:
            update = {"callback_query": mutation}
            res = med_bot.handle_med_webhook(update)
            self.assertIsInstance(res, dict)
            self.assertIn(res.get("status"), ["ok", "error", "ignored"])


class TestAdversarialFuzzTextCommands(unittest.TestCase):
    """
    Adversarial fuzz testing for text message commands:
    - Corrupt update objects (None, wrong types, missing chat, missing text)
    - Malformed /addpreset syntax, boundary values, scientific notation, injection
    - Malformed /history syntax, boundary limits, negative limits, non-numeric limits
    - Malformed /delpreset syntax, non-existent presets, injection
    """

    @classmethod
    def setUpClass(cls):
        db.init_db()

    @patch("bot_client.TelegramBotClient.send_message")
    def test_fuzz_malformed_message_objects(self, mock_send):
        mock_send.return_value = {"ok": True}

        malformed_updates = [
            None,
            {},
            [],
            12345,
            "just_a_string",
            {"message": None},
            {"message": {}},
            {"message": {"text": None}},
            {"message": {"chat": None, "text": "/help"}},
            {"message": {"chat": {}, "text": "/help"}},
            {"message": {"chat": {"id": None, "type": None}, "text": "/help"}},
            {"message": {"chat": {"id": "123", "type": 12345}, "text": "/help"}},
            {"message": {"chat": {"id": 123, "type": "private"}, "text": 12345}},
            {"message": {"chat": {"id": 123, "type": "private"}, "text": None}},
            {"message": {"chat": {"id": 123, "type": "private"}, "text": ""}},
            {"message": {"chat": {"id": 123, "type": "private"}, "text": "   "}},
            {"message": {"chat": {"id": 123, "type": "private"}, "text": "\n\t\r"}},
            {"message": {"chat": {"id": 123, "type": "private"}, "text": "\x00"}},
        ]

        for upd in malformed_updates:
            res = med_bot.handle_med_webhook(upd)
            self.assertIsInstance(res, dict)
            self.assertIn(res.get("status"), ["ok", "ignored", "error"])

    @patch("bot_client.TelegramBotClient.send_message")
    def test_fuzz_addpreset_variations(self, mock_send):
        mock_send.return_value = {"ok": True}

        fuzz_commands = [
            # Edge dose formats
            ("/addpreset TestMed 0.0000001 mg", "preset_added", "ok"),
            ("/addpreset TestMed 999999999.99 mg", "preset_added", "ok"),
            ("/addpreset TestMed 1e5 mg", "preset_added", "ok"),
            ("/addpreset TestMed 2.5e-3 mg", "preset_added", "ok"),
            # Invalid dose values
            ("/addpreset TestMed 0 mg", "invalid_dose_value", "error"),
            ("/addpreset TestMed 0.0 mg", "invalid_dose_value", "error"),
            ("/addpreset TestMed -0.0 mg", "invalid_dose_value", "error"),
            ("/addpreset TestMed -1 mg", "invalid_dose_value", "error"),
            ("/addpreset TestMed -0.0001 mg", "invalid_dose_value", "error"),
            ("/addpreset TestMed -1e5 mg", "invalid_dose_value", "error"),
            ("/addpreset TestMed nan mg", "invalid_dose_value", "error"),
            ("/addpreset TestMed +nan mg", "invalid_dose_value", "error"),
            ("/addpreset TestMed -nan mg", "invalid_dose_value", "error"),
            ("/addpreset TestMed inf mg", "invalid_dose_value", "error"),
            ("/addpreset TestMed +inf mg", "invalid_dose_value", "error"),
            ("/addpreset TestMed -inf mg", "invalid_dose_value", "error"),
            ("/addpreset TestMed 1e999 mg", "invalid_dose_value", "error"), # Overflow to inf
            # Non-numeric formats
            ("/addpreset TestMed one mg", "invalid_dose_format", "error"),
            ("/addpreset TestMed 1/2 mg", "invalid_dose_format", "error"),
            ("/addpreset TestMed 1,5 mg", "invalid_dose_format", "error"),
            ("/addpreset TestMed 0x10 mg", "invalid_dose_format", "error"),
            ("/addpreset TestMed #$! mg", "invalid_dose_format", "error"),
            # Syntax / argument count
            ("/addpreset", "invalid_format", "error"),
            ("/addpreset DrugName", "invalid_format", "error"),
            ("/addpreset DrugName 10", "invalid_format", "error"),
            ("/addpreset Drug Name With Spaces", "invalid_dose_format", "error"),
            # Multi-word names
            (f"/addpreset Advanced Multi Word Med {uuid.uuid4().hex[:4]} 12.5 mg", "preset_added", "ok"),
            # Extreme length name
            (f"/addpreset {'A' * 300} 10 mg", "missing_name", "error"),
        ]

        for cmd, expected_action, expected_status in fuzz_commands:
            update = {
                "message": {
                    "chat": {"id": 12345, "type": "private"},
                    "text": cmd
                }
            }
            res = med_bot.handle_med_webhook(update)
            self.assertEqual(
                res.get("status"), expected_status,
                f"Failed status for cmd: {cmd}. Got: {res}"
            )
            self.assertEqual(
                res.get("action"), expected_action,
                f"Failed action for cmd: {cmd}. Got: {res}"
            )

    @patch("bot_client.TelegramBotClient.send_message")
    def test_fuzz_history_command_variations(self, mock_send):
        mock_send.return_value = {"ok": True}

        # Seed data
        med_name = f"FuzzHistDrug_{uuid.uuid4().hex[:6]}"
        med_id = db.add_medication_preset(med_name, 50.0, "mg")
        for i in range(5):
            db.log_medication_dose(med_id, 50.0, notes=f"Fuzz dose {i}")

        history_commands = [
            "/history",
            "/history 1",
            "/history 5",
            "/history 50",
            "/history 500",  # Clamped to 50
            "/history 0",    # Clamped to 1
            "/history -1",   # Clamped to 1
            "/history -999", # Clamped to 1
            f"/history {med_name}",
            f"/history {med_name} 3",
            f"/history {med_name} 0",
            f"/history {med_name} -10",
            f"/history {med_name} 500",
            "/history NonExistentDrug123",
            "/history NonExistentDrug123 5",
            "/history ' OR 1=1 --",
            "/history ' OR '1'='1",
            "/history 💊 Complex Emojis Med",
        ]

        for cmd in history_commands:
            update = {
                "message": {
                    "chat": {"id": 12345, "type": "private"},
                    "text": cmd
                }
            }
            res = med_bot.handle_med_webhook(update)
            self.assertEqual(res.get("status"), "ok", f"Failed for history cmd: {cmd}")
            self.assertEqual(res.get("action"), "history_viewed")

    @patch("bot_client.TelegramBotClient.send_message")
    def test_fuzz_delpreset_command_variations(self, mock_send):
        mock_send.return_value = {"ok": True}

        # Create preset to delete
        target_name = f"ToDelete_{uuid.uuid4().hex[:6]}"
        db.add_medication_preset(target_name, 25.0, "mg")

        # 1. Delete existing
        upd1 = {"message": {"chat": {"id": 123, "type": "private"}, "text": f"/delpreset {target_name}"}}
        res1 = med_bot.handle_med_webhook(upd1)
        self.assertEqual(res1.get("status"), "ok")
        self.assertEqual(res1.get("action"), "preset_deleted")

        # 2. Delete non-existent (or already deleted)
        upd2 = {"message": {"chat": {"id": 123, "type": "private"}, "text": f"/delpreset {target_name}"}}
        res2 = med_bot.handle_med_webhook(upd2)
        self.assertEqual(res2.get("status"), "error")
        self.assertEqual(res2.get("action"), "preset_not_found")

        # 3. Malformed / empty syntax
        bad_del_commands = [
            "/delpreset",
            "/delpreset ",
            "/deletepreset",
            "/rmpreset",
        ]
        for cmd in bad_del_commands:
            upd = {"message": {"chat": {"id": 123, "type": "private"}, "text": cmd}}
            res = med_bot.handle_med_webhook(upd)
            self.assertEqual(res.get("status"), "error")
            self.assertEqual(res.get("action"), "invalid_delpreset_format")


class TestAdversarialFuzzTimeFormatting(unittest.TestCase):
    """
    Adversarial fuzz testing for format_elapsed_time:
    - None inputs
    - Non-datetime types (strings, ints, floats, garbage objects)
    - Future timestamps (diff_seconds < 0 -> clamped to 0)
    - Naive and aware datetime combinations
    - Timestamps across 1m, 60m, 24h, multiple days, multiple years
    """

    def test_fuzz_format_elapsed_time_types(self):
        now = datetime.now(timezone.utc)

        self.assertEqual(med_bot.format_elapsed_time(None), "never")
        self.assertEqual(med_bot.format_elapsed_time("invalid_date_string"), "unknown")
        self.assertEqual(med_bot.format_elapsed_time([]), "unknown")
        self.assertEqual(med_bot.format_elapsed_time({}), "unknown")
        self.assertEqual(med_bot.format_elapsed_time(object()), "unknown")

        # Numeric timestamps
        now_ts = now.timestamp()
        self.assertEqual(med_bot.format_elapsed_time(now_ts, now), "just now")
        self.assertEqual(med_bot.format_elapsed_time(now_ts - 300, now), "5m ago")
        self.assertEqual(med_bot.format_elapsed_time(now_ts - 7200, now), "2h ago")
        self.assertEqual(med_bot.format_elapsed_time(now_ts - 90000, now), "1d 1h ago")

        # ISO string timestamps
        iso_str = (now - timedelta(minutes=15)).isoformat()
        self.assertEqual(med_bot.format_elapsed_time(iso_str, now), "15m ago")

        iso_z = (now - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(med_bot.format_elapsed_time(iso_z, now), "4h ago")

    def test_fuzz_format_elapsed_time_boundaries(self):
        now = datetime.now(timezone.utc)

        # 1. Past boundaries
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(seconds=0), now), "just now")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(seconds=30), now), "just now")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(seconds=59), now), "just now")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(seconds=60), now), "1m ago")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(minutes=59), now), "59m ago")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(minutes=60), now), "1h ago")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(hours=1, minutes=15), now), "1h 15m ago")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(hours=23, minutes=59), now), "23h 59m ago")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(hours=24), now), "1d ago")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(days=1, hours=5), now), "1d 5h ago")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(days=365), now), "365d ago")

        # 2. Future timestamps (clock skew)
        self.assertEqual(med_bot.format_elapsed_time(now + timedelta(minutes=10), now), "just now")

        # 3. Naive datetime vs Aware datetime mix
        naive_past = datetime(2026, 1, 1, 12, 0, 0)
        naive_now = datetime(2026, 1, 1, 12, 30, 0)
        self.assertEqual(med_bot.format_elapsed_time(naive_past, naive_now), "30m ago")

        aware_now = datetime(2026, 1, 1, 12, 30, 0, tzinfo=timezone.utc)
        self.assertEqual(med_bot.format_elapsed_time(naive_past, aware_now), "30m ago")


class TestAdversarialFuzzUserAttribution(unittest.TestCase):
    """
    Adversarial fuzz testing for get_user_display_name:
    - None from object
    - Empty from object
    - First name only, last name only, username only
    - Non-string types in name fields
    - Unicode / emojis / SQL injection in name fields
    """

    def test_fuzz_user_display_name(self):
        self.assertEqual(med_bot.get_user_display_name(None), "User")
        self.assertEqual(med_bot.get_user_display_name({}), "User")
        self.assertEqual(med_bot.get_user_display_name([]), "User")
        self.assertEqual(med_bot.get_user_display_name("string"), "User")

        # Normal cases
        self.assertEqual(med_bot.get_user_display_name({"first_name": "Alice", "last_name": "Smith"}), "Alice Smith")
        self.assertEqual(med_bot.get_user_display_name({"first_name": "Bob"}), "Bob")
        self.assertEqual(med_bot.get_user_display_name({"username": "charlie_care"}), "@charlie_care")
        self.assertEqual(med_bot.get_user_display_name({"last_name": "Doe"}), "User")

        # Non-string types
        self.assertEqual(med_bot.get_user_display_name({"first_name": 12345}), "12345")
        self.assertEqual(med_bot.get_user_display_name({"first_name": True}), "True")

        # Unicode & Emojis
        self.assertEqual(med_bot.get_user_display_name({"first_name": "👨‍⚕️ Dr. Sarah"}), "👨‍⚕️ Dr. Sarah")


if __name__ == "__main__":
    unittest.main()
