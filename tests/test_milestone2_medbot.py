"""
tests/test_milestone2_medbot.py
Comprehensive Unit & Integration Test Suite for Milestone 2:
Group vs. DM Dual Interaction Architecture & MedFlowAssist Bot (@medflowassist_bot).
"""

import uuid
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
import math

import db
import med_bot
from bot_client import get_bot_client


class TestMilestone2DatabaseCRUD(unittest.TestCase):
    """Verifies centralized medication CRUD functions in db.py."""

    def setUp(self):
        db.init_db()

    def test_preset_crud_and_soft_delete(self):
        # 1. Add preset
        med_id = db.add_medication_preset("Metformin ER", 500.0, "mg")
        self.assertIsInstance(med_id, int)
        self.assertGreater(med_id, 0)

        # 2. Query by ID
        preset = db.get_medication_preset_by_id(med_id)
        self.assertIsNotNone(preset)
        self.assertEqual(preset["name"], "Metformin ER")
        self.assertEqual(float(preset["default_dose"]), 500.0)
        self.assertEqual(preset["dose_unit"], "mg")
        self.assertTrue(preset["is_active"])

        # 3. Query by Name (case-insensitive)
        preset_lower = db.get_medication_preset_by_name("metformin er")
        self.assertIsNotNone(preset_lower)
        self.assertEqual(preset_lower["id"], med_id)

        # 4. Upsert with updated dose
        med_id_updated = db.add_medication_preset("metformin er", 1000.0, "mg")
        self.assertEqual(med_id_updated, med_id)
        preset_updated = db.get_medication_preset_by_id(med_id)
        self.assertEqual(float(preset_updated["default_dose"]), 1000.0)

        # 5. Soft Delete
        del_success = db.delete_medication_preset("Metformin ER")
        self.assertTrue(del_success)

        # Check that it is excluded from active presets
        active_presets = db.get_medication_presets(active_only=True)
        active_ids = [p["id"] for p in active_presets]
        self.assertNotIn(med_id, active_ids)

        # But exists when active_only=False
        all_presets = db.get_medication_presets(active_only=False)
        all_ids = [p["id"] for p in all_presets]
        self.assertIn(med_id, all_ids)

        # 6. Reactivate via add_medication_preset
        db.add_medication_preset("Metformin ER", 500.0, "mg")
        preset_reactivated = db.get_medication_preset_by_id(med_id)
        self.assertTrue(preset_reactivated["is_active"])

    def test_log_medication_dose_and_queries(self):
        # Create test preset with unique name for isolation
        med_name = f"Ibuprofen_{uuid.uuid4().hex[:6]}"
        med_id = db.add_medication_preset(med_name, 400.0, "mg")

        # Log doses
        now = datetime.now(timezone.utc)
        t1 = now - timedelta(hours=2)
        t2 = now - timedelta(minutes=15)

        log1 = db.log_medication_dose(med_id, 400.0, timestamp=t1, notes="Logged via quick button by Alice")
        log2 = db.log_medication_dose(med_id, 800.0, timestamp=t2, notes="Logged via quick button by Bob")

        self.assertIsInstance(log1, int)
        self.assertIsInstance(log2, int)

        # Query recent logs
        recent = db.get_recent_med_logs(limit=10, medication_name=med_name)
        self.assertEqual(len(recent), 2)
        # Verify reverse chronological ordering
        self.assertEqual(recent[0]["id"], log2)
        self.assertEqual(recent[0]["notes"], "Logged via quick button by Bob")
        self.assertEqual(recent[1]["id"], log1)

        # Test summary query
        summary = db.get_medication_summary(medication_name=med_name)
        self.assertEqual(len(summary), 1)
        item = summary[0]
        self.assertEqual(item["name"], med_name)
        self.assertEqual(item["count_24h"], 2)
        self.assertEqual(float(item["total_dose_24h"]), 1200.0)

        # Test delete log
        self.assertTrue(db.delete_medication_log(log1))
        self.assertTrue(db.delete_medication_log(log2))


class TestMilestone2ElapsedFormatting(unittest.TestCase):
    """Verifies UTC-normalized elapsed time formatting logic."""

    def test_format_elapsed_time_intervals(self):
        now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)

        # Just now (< 1 min)
        t_just_now = now - timedelta(seconds=20)
        self.assertEqual(med_bot.format_elapsed_time(t_just_now, now), "just now")

        # Minutes ago (15m)
        t_15m = now - timedelta(minutes=15)
        self.assertEqual(med_bot.format_elapsed_time(t_15m, now), "15m ago")

        # Exact hours ago (2h)
        t_2h = now - timedelta(hours=2)
        self.assertEqual(med_bot.format_elapsed_time(t_2h, now), "2h ago")

        # Hours and minutes ago (2h 15m)
        t_2h15m = now - timedelta(hours=2, minutes=15)
        self.assertEqual(med_bot.format_elapsed_time(t_2h15m, now), "2h 15m ago")

        # Exact days ago (1d)
        t_1d = now - timedelta(days=1)
        self.assertEqual(med_bot.format_elapsed_time(t_1d, now), "1d ago")

        # Days and hours ago (1d 4h)
        t_1d4h = now - timedelta(days=1, hours=4)
        self.assertEqual(med_bot.format_elapsed_time(t_1d4h, now), "1d 4h ago")

        # String ISO parsing & None handling
        self.assertEqual(med_bot.format_elapsed_time(None), "never")
        self.assertEqual(med_bot.format_elapsed_time("invalid_iso_string"), "unknown")


class TestMilestone2MedBotCommands(unittest.TestCase):
    """Verifies MedFlowAssist bot commands and input parsing."""

    def setUp(self):
        db.init_db()

    @patch("bot_client.TelegramBotClient.send_message")
    def test_addpreset_success_and_multiword(self, mock_send):
        mock_send.return_value = {"ok": True, "result": {"message_id": 101}}

        # Single word drug
        update1 = {
            "message": {
                "message_id": 1,
                "chat": {"id": 12345, "type": "private"},
                "from": {"first_name": "TestUser"},
                "text": "/addpreset Gabapentin 300 mg"
            }
        }
        res1 = med_bot.handle_med_webhook(update1)
        self.assertEqual(res1.get("status"), "ok")
        self.assertEqual(res1.get("action"), "preset_added")
        self.assertEqual(res1.get("details", {}).get("name"), "Gabapentin")
        self.assertEqual(res1.get("details", {}).get("dose"), 300.0)
        self.assertEqual(res1.get("details", {}).get("unit"), "mg")

        # Multi-word drug with unicode & symbol
        update2 = {
            "message": {
                "message_id": 2,
                "chat": {"id": 12345, "type": "private"},
                "from": {"first_name": "TestUser"},
                "text": "/addpreset 💊 Hydrochlorothiazide / Valsartan 25.0 mg"
            }
        }
        res2 = med_bot.handle_med_webhook(update2)
        self.assertEqual(res2.get("status"), "ok")
        self.assertEqual(res2.get("action"), "preset_added")
        self.assertEqual(res2.get("details", {}).get("name"), "💊 Hydrochlorothiazide / Valsartan")
        self.assertEqual(res2.get("details", {}).get("dose"), 25.0)

    @patch("bot_client.TelegramBotClient.send_message")
    def test_addpreset_validation_errors(self, mock_send):
        mock_send.return_value = {"ok": True}

        # 1. Missing arguments
        update_miss = {
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/addpreset Tylenol"
            }
        }
        res_miss = med_bot.handle_med_webhook(update_miss)
        self.assertEqual(res_miss.get("status"), "error")
        self.assertEqual(res_miss.get("action"), "invalid_format")

        # 2. Non-numeric dose
        update_nan = {
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/addpreset Tylenol five mg"
            }
        }
        res_nan = med_bot.handle_med_webhook(update_nan)
        self.assertEqual(res_nan.get("status"), "error")
        self.assertEqual(res_nan.get("action"), "invalid_dose_format")

        # 3. Zero dose
        update_zero = {
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/addpreset Aspirin 0 mg"
            }
        }
        res_zero = med_bot.handle_med_webhook(update_zero)
        self.assertEqual(res_zero.get("status"), "error")
        self.assertEqual(res_zero.get("action"), "invalid_dose_value")

        # 4. Negative dose
        update_neg = {
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/addpreset Aspirin -500 mg"
            }
        }
        res_neg = med_bot.handle_med_webhook(update_neg)
        self.assertEqual(res_neg.get("status"), "error")
        self.assertEqual(res_neg.get("action"), "invalid_dose_value")

    @patch("bot_client.TelegramBotClient.send_message")
    def test_delpreset_command(self, mock_send):
        mock_send.return_value = {"ok": True}

        # Create preset to delete
        db.add_medication_preset("Atorvastatin", 20.0, "mg")

        # Delete existing preset
        update_del = {
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/delpreset Atorvastatin"
            }
        }
        res_del = med_bot.handle_med_webhook(update_del)
        self.assertEqual(res_del.get("status"), "ok")
        self.assertEqual(res_del.get("action"), "preset_deleted")

        # Delete non-existent preset
        update_del_missing = {
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/delpreset NonExistentDrug999"
            }
        }
        res_del_missing = med_bot.handle_med_webhook(update_del_missing)
        self.assertEqual(res_del_missing.get("status"), "error")
        self.assertEqual(res_del_missing.get("action"), "preset_not_found")

        # Missing argument
        update_del_noarg = {
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/delpreset"
            }
        }
        res_del_noarg = med_bot.handle_med_webhook(update_del_noarg)
        self.assertEqual(res_del_noarg.get("status"), "error")
        self.assertEqual(res_del_noarg.get("action"), "invalid_delpreset_format")

    @patch("bot_client.TelegramBotClient.send_message")
    def test_presets_listing(self, mock_send):
        mock_send.return_value = {"ok": True}

        db.add_medication_preset("Lisinopril", 10.0, "mg")
        update_list = {
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/presets"
            }
        }
        res = med_bot.handle_med_webhook(update_list)
        self.assertEqual(res.get("status"), "ok")
        self.assertEqual(res.get("action"), "presets_listed")
        self.assertIn("Lisinopril", res.get("text", ""))

    @patch("bot_client.TelegramBotClient.send_message")
    def test_log_menu_rendering(self, mock_send):
        mock_send.return_value = {"ok": True}

        db.add_medication_preset("Sertraline", 50.0, "mg")
        update_log = {
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/log"
            }
        }
        res = med_bot.handle_med_webhook(update_log)
        self.assertEqual(res.get("status"), "ok")
        self.assertEqual(res.get("action"), "log_menu_sent")


class TestMilestone2CallbackHandling(unittest.TestCase):
    """Verifies callback query processing, attribution, and debouncing."""

    def setUp(self):
        db.init_db()

    @patch("bot_client.TelegramBotClient.answer_callback_query")
    @patch("bot_client.TelegramBotClient.edit_message_text")
    def test_callback_dose_logging_with_attribution(self, mock_edit, mock_answer):
        mock_answer.return_value = {"ok": True}
        mock_edit.return_value = {"ok": True}

        med_id = db.add_medication_preset("Lorazepam", 1.0, "mg")

        cb_update = {
            "callback_query": {
                "id": "cb_uniq_101",
                "data": f"med:log:{med_id}:1.0",
                "from": {"first_name": "Sarah", "last_name": "Connor", "username": "sconnor"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": -100987654, "type": "group"}
                }
            }
        }

        res = med_bot.handle_med_webhook(cb_update)
        self.assertEqual(res.get("status"), "ok")
        self.assertEqual(res.get("action"), "dose_logged")
        self.assertEqual(res.get("details", {}).get("logged_by"), "Sarah Connor")
        self.assertEqual(res.get("details", {}).get("dose"), 1.0)
        self.assertEqual(res.get("details", {}).get("unit"), "mg")

        # Verify edit_message_text was called with attribution
        mock_edit.assert_called_once()
        args, kwargs = mock_edit.call_args
        self.assertIn("Sarah Connor", kwargs.get("text", "") or args[2])

        # Verify DB entry
        logs = db.get_recent_med_logs(limit=1, medication_id=med_id)
        self.assertEqual(len(logs), 1)
        self.assertIn("Sarah Connor", logs[0]["notes"])

    @patch("bot_client.TelegramBotClient.answer_callback_query")
    def test_callback_debouncing_duplicate_clicks(self, mock_answer):
        mock_answer.return_value = {"ok": True}

        med_id = db.add_medication_preset("Zolpidem", 5.0, "mg")
        cb_id = "cb_debounce_test_999"

        cb_update = {
            "callback_query": {
                "id": cb_id,
                "data": f"med:log:{med_id}:5.0",
                "from": {"first_name": "Caregiver"},
                "message": {"message_id": 777, "chat": {"id": 1234}}
            }
        }

        # First click -> Processed
        res1 = med_bot.handle_med_webhook(cb_update)
        self.assertEqual(res1.get("status"), "ok")
        self.assertEqual(res1.get("action"), "dose_logged")

        # Immediate Second click -> Debounced
        res2 = med_bot.handle_med_webhook(cb_update)
        self.assertEqual(res2.get("status"), "ok")
        self.assertEqual(res2.get("action"), "debounced")

    def test_foreign_callback_namespace_isolation(self):
        # Callback meant for GlucoTrack or MonkeHelper sent to MedBot -> Ignored
        foreign_update = {
            "callback_query": {
                "id": "cb_foreign",
                "data": "gt:meal:45.0:3.5",
                "from": {"first_name": "Alice"}
            }
        }
        res = med_bot.handle_med_webhook(foreign_update)
        self.assertEqual(res.get("status"), "ignored")
        self.assertEqual(res.get("action"), "foreign_namespace_ignored")


class TestMilestone2GroupVsDMDualMode(unittest.TestCase):
    """Verifies Group vs DM dual interaction mode and ambient noise filtering."""

    def setUp(self):
        db.init_db()

    def test_group_ambient_noise_filtering(self):
        # Casual conversation in group chat -> Ignored
        ambient_group_update = {
            "message": {
                "message_id": 10,
                "chat": {"id": -1001234567, "type": "supergroup"},
                "from": {"first_name": "Uncle Bob"},
                "text": "What time is dinner tonight everyone?"
            }
        }
        res = med_bot.handle_med_webhook(ambient_group_update)
        self.assertEqual(res.get("status"), "ignored")
        self.assertEqual(res.get("action"), "group_noise_ignored")
        self.assertEqual(res.get("reason"), "ambient_noise_filtered")

    @patch("bot_client.TelegramBotClient.send_message")
    def test_group_explicit_command_responds_without_reply_keyboard(self, mock_send):
        mock_send.return_value = {"ok": True}

        group_cmd_update = {
            "message": {
                "message_id": 11,
                "chat": {"id": -1001234567, "type": "supergroup"},
                "from": {"first_name": "Nurse Joy"},
                "text": "/presets"
            }
        }
        res = med_bot.handle_med_webhook(group_cmd_update)
        self.assertEqual(res.get("status"), "ok")
        self.assertEqual(res.get("action"), "presets_listed")

        # Verify ReplyKeyboardMarkup is NOT sent to group
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        self.assertIsNone(kwargs.get("reply_markup"))

    @patch("bot_client.TelegramBotClient.send_message")
    def test_dm_mode_provides_persistent_reply_keyboard(self, mock_send):
        mock_send.return_value = {"ok": True}

        dm_start_update = {
            "message": {
                "message_id": 12,
                "chat": {"id": 88888, "type": "private"},
                "from": {"first_name": "Patient"},
                "text": "/start"
            }
        }
        res = med_bot.handle_med_webhook(dm_start_update)
        self.assertEqual(res.get("status"), "ok")
        self.assertEqual(res.get("action"), "start_menu_rendered")

        # Verify ReplyKeyboardMarkup is attached in DM
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        reply_markup = kwargs.get("reply_markup") or (args[1] if len(args) > 1 else None)
        self.assertIsNotNone(reply_markup)
        self.assertIn("keyboard", reply_markup)
        self.assertTrue(reply_markup.get("is_persistent"))

    def test_group_command_for_other_bot_ignored(self):
        other_bot_update = {
            "message": {
                "message_id": 13,
                "chat": {"id": -1001234567, "type": "group"},
                "from": {"first_name": "User"},
                "text": "/status@gluco_track_bot"
            }
        }
        res = med_bot.handle_med_webhook(other_bot_update)
        self.assertEqual(res.get("status"), "ignored")
        self.assertEqual(res.get("action"), "command_for_other_bot")


class TestMilestone2HistoryAndSummary(unittest.TestCase):
    """Verifies /history and /summary outputs and filtering."""

    def setUp(self):
        db.init_db()

    @patch("bot_client.TelegramBotClient.send_message")
    def test_history_and_summary_flow(self, mock_send):
        mock_send.return_value = {"ok": True}

        # Setup preset and logs
        med_id = db.add_medication_preset("Amoxicillin", 500.0, "mg")
        db.log_medication_dose(med_id, 500.0, notes="Logged via quick button by Dr. Smith")

        # Test /history
        hist_update = {
            "message": {
                "chat": {"id": 1234, "type": "private"},
                "text": "/history Amoxicillin"
            }
        }
        res_hist = med_bot.handle_med_webhook(hist_update)
        self.assertEqual(res_hist.get("status"), "ok")
        self.assertEqual(res_hist.get("action"), "history_viewed")
        self.assertIn("Amoxicillin", res_hist.get("text", ""))

        # Test /summary
        sum_update = {
            "message": {
                "chat": {"id": 1234, "type": "private"},
                "text": "/summary"
            }
        }
        res_sum = med_bot.handle_med_webhook(sum_update)
        self.assertEqual(res_sum.get("status"), "ok")
        self.assertEqual(res_sum.get("action"), "summary_viewed")
        self.assertIn("Amoxicillin", res_sum.get("text", ""))


if __name__ == "__main__":
    unittest.main()
