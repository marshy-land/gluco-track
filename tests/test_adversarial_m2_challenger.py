"""
tests/test_adversarial_m2_challenger.py
Empirical Adversarial Stress Tests for Milestone 2:
1. Data Safety & Soft Deletion Historical Integrity
2. User Attribution & Multi-User Edge Cases
3. Elapsed Time Calculation under Extremes & Negative Drift
"""

import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta, tzinfo
import time
import math
import uuid

import db
import med_bot
from bot_client import get_bot_client


class TestAdversarialDataSafetySoftDelete(unittest.TestCase):
    """Stress tests verifying that soft deletion never corrupts or deletes historical dose logs."""

    def setUp(self):
        db.init_db()

    def test_soft_delete_preserves_historical_logs(self):
        """Verify that soft-deleting a preset leaves all historical dose records 100% intact."""
        unique_name = f"TestDrug_{uuid.uuid4().hex[:8]}"
        med_id = db.add_medication_preset(unique_name, 50.0, "mg")
        self.assertIsInstance(med_id, int)

        # Insert multiple historical doses at different timestamps
        now = datetime.now(timezone.utc)
        timestamps = [
            now - timedelta(days=7),
            now - timedelta(days=3),
            now - timedelta(hours=12),
            now - timedelta(hours=2),
            now - timedelta(minutes=10),
            now
        ]
        log_ids = []
        for i, ts in enumerate(timestamps):
            lid = db.log_medication_dose(
                medication_id=med_id,
                dose_taken=50.0 * (i + 1),
                timestamp=ts,
                notes=f"Historical dose #{i+1} by Caregiver_{i}"
            )
            log_ids.append(lid)

        self.assertEqual(len(log_ids), 6)

        # Soft-delete the preset
        del_result = db.delete_medication_preset(unique_name)
        self.assertTrue(del_result)

        # 1. Preset is inactive
        preset_record = db.get_medication_preset_by_id(med_id)
        self.assertIsNotNone(preset_record)
        self.assertFalse(preset_record["is_active"])

        # 2. Preset excluded from active presets list
        active_presets = db.get_medication_presets(active_only=True)
        active_ids = [p["id"] for p in active_presets]
        self.assertNotIn(med_id, active_ids)

        # 3. Preset included in all presets list
        all_presets = db.get_medication_presets(active_only=False)
        all_ids = [p["id"] for p in all_presets]
        self.assertIn(med_id, all_ids)

        # 4. CRITICAL: All historical dose logs STILL EXIST in database
        logs_by_name = db.get_recent_med_logs(limit=50, medication_name=unique_name)
        self.assertEqual(len(logs_by_name), 6)
        # Reverse chronological ordering check
        self.assertEqual(logs_by_name[0]["id"], log_ids[-1])
        self.assertEqual(logs_by_name[-1]["id"], log_ids[0])

        logs_by_id = db.get_recent_med_logs(limit=50, medication_id=med_id)
        self.assertEqual(len(logs_by_id), 6)

        # Verify notes, dose, units
        for log_entry in logs_by_name:
            self.assertEqual(log_entry["name"], unique_name)
            self.assertEqual(log_entry["dose_unit"], "mg")
            self.assertIn("Historical dose", log_entry["notes"])

        # Clean up logs
        for lid in log_ids:
            db.delete_medication_log(lid)

    def test_soft_delete_idempotency_and_reactivation(self):
        """Verify repeated deletes are handled gracefully and re-adding reactivates without data loss."""
        unique_name = f"ReactivateDrug_{uuid.uuid4().hex[:8]}"
        med_id_1 = db.add_medication_preset(unique_name, 100.0, "mg")

        # Log dose
        log_id = db.log_medication_dose(med_id_1, 100.0, notes="Pre-deletion dose")

        # First delete -> True
        self.assertTrue(db.delete_medication_preset(unique_name))
        # Second delete -> False (already inactive)
        self.assertFalse(db.delete_medication_preset(unique_name))
        # Delete by ID when inactive -> False
        self.assertFalse(db.delete_medication_preset(med_id_1))

        # Re-add preset with updated dose
        med_id_2 = db.add_medication_preset(unique_name, 200.0, "mg")
        self.assertEqual(med_id_1, med_id_2)  # Reuses same primary key

        # Preset is now active again
        preset = db.get_medication_preset_by_id(med_id_1)
        self.assertTrue(preset["is_active"])
        self.assertEqual(float(preset["default_dose"]), 200.0)

        # Historical log is still attached
        logs = db.get_recent_med_logs(limit=10, medication_id=med_id_1)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["id"], log_id)
        self.assertEqual(logs[0]["notes"], "Pre-deletion dose")

        # Cleanup
        db.delete_medication_log(log_id)

    @patch("bot_client.TelegramBotClient.send_message")
    def test_history_query_on_soft_deleted_medication(self, mock_send):
        """Verify /history command behaves properly when querying a soft-deleted medication."""
        mock_send.return_value = {"ok": True}
        unique_name = f"HistDrug_{uuid.uuid4().hex[:8]}"
        med_id = db.add_medication_preset(unique_name, 25.0, "mg")
        log_id = db.log_medication_dose(med_id, 25.0, notes="Historical dose for soft-deleted med")

        # Soft delete
        db.delete_medication_preset(unique_name)

        # Query history via bot
        update = {
            "message": {
                "chat": {"id": 12345, "type": "private"},
                "text": f"/history {unique_name}"
            }
        }
        res = med_bot.handle_med_webhook(update)
        self.assertEqual(res.get("status"), "ok")
        self.assertEqual(res.get("action"), "history_viewed")
        self.assertIn(unique_name, res.get("text", ""))
        self.assertIn("Historical dose for soft-deleted med", res.get("text", ""))

        # Cleanup
        db.delete_medication_log(log_id)

    @patch("bot_client.TelegramBotClient.send_message")
    @patch("bot_client.TelegramBotClient.answer_callback_query")
    @patch("bot_client.TelegramBotClient.edit_message_text")
    def test_callback_dose_logging_on_soft_deleted_preset(self, mock_edit, mock_answer, mock_send):
        """Verify tapping a quick button for a medication that was just soft-deleted still logs dose safely."""
        mock_answer.return_value = {"ok": True}
        mock_edit.return_value = {"ok": True}
        unique_name = f"OrphanBtn_{uuid.uuid4().hex[:8]}"
        med_id = db.add_medication_preset(unique_name, 10.0, "mg")

        # Soft delete the preset
        db.delete_medication_preset(unique_name)

        # User taps an existing inline button that was rendered before deletion
        cb_update = {
            "callback_query": {
                "id": f"cb_{uuid.uuid4().hex[:8]}",
                "data": f"med:log:{med_id}:10.0",
                "from": {"first_name": "LateCaregiver"},
                "message": {
                    "message_id": 999,
                    "chat": {"id": 12345, "type": "group"}
                }
            }
        }
        res = med_bot.handle_med_webhook(cb_update)
        self.assertEqual(res.get("status"), "ok")
        self.assertEqual(res.get("action"), "dose_logged")
        log_id = res.get("details", {}).get("log_id")
        self.assertIsNotNone(log_id)

        # Verify log exists in db
        logs = db.get_recent_med_logs(limit=1, medication_id=med_id)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["id"], log_id)

        # Cleanup
        db.delete_medication_log(log_id)


class TestAdversarialUserAttribution(unittest.TestCase):
    """Stress tests user attribution under diverse user object schemas and edge cases."""

    def test_get_user_display_name_variations(self):
        # 1. Full name
        self.assertEqual(
            med_bot.get_user_display_name({"first_name": "John", "last_name": "Doe", "username": "jdoe"}),
            "John Doe"
        )
        # 2. First name only
        self.assertEqual(
            med_bot.get_user_display_name({"first_name": "Alice", "username": "alice99"}),
            "Alice"
        )
        # 3. Username only (no first/last)
        self.assertEqual(
            med_bot.get_user_display_name({"username": "nurse_bot"}),
            "@nurse_bot"
        )
        # 4. Whitespace only first/last with username
        self.assertEqual(
            med_bot.get_user_display_name({"first_name": "   ", "last_name": " ", "username": "caregiver_jane"}),
            "@caregiver_jane"
        )
        # 5. Empty dictionary
        self.assertEqual(med_bot.get_user_display_name({}), "User")
        # 6. None
        self.assertEqual(med_bot.get_user_display_name(None), "User")
        # 7. Non-dict types (integers, strings, booleans, lists)
        self.assertEqual(med_bot.get_user_display_name(12345), "User")
        self.assertEqual(med_bot.get_user_display_name("string_user"), "User")
        self.assertEqual(med_bot.get_user_display_name(["user1"]), "User")
        self.assertEqual(med_bot.get_user_display_name(True), "User")

        # 8. Unicode, emoji, special characters in names
        self.assertEqual(
            med_bot.get_user_display_name({"first_name": "Dr. 🩺", "last_name": "Müller"}),
            "Dr. 🩺 Müller"
        )
        self.assertEqual(
            med_bot.get_user_display_name({"first_name": "<script>alert(1)</script>", "last_name": "Smith"}),
            "<script>alert(1)</script> Smith"
        )

    @patch("bot_client.TelegramBotClient.answer_callback_query")
    @patch("bot_client.TelegramBotClient.edit_message_text")
    def test_multi_user_concurrent_clicks_attribution(self, mock_edit, mock_answer):
        """Simulate two different caregivers clicking inline buttons for different/same presets."""
        mock_answer.return_value = {"ok": True}
        mock_edit.return_value = {"ok": True}

        med_id = db.add_medication_preset(f"MultiUserDrug_{uuid.uuid4().hex[:6]}", 10.0, "mg")

        # User A clicks
        cb_update_a = {
            "callback_query": {
                "id": f"cb_user_a_{uuid.uuid4().hex[:6]}",
                "data": f"med:log:{med_id}:10.0",
                "from": {"first_name": "Alice", "last_name": "Caregiver", "username": "alice_c"},
                "message": {"message_id": 1001, "chat": {"id": -100555}}
            }
        }
        res_a = med_bot.handle_med_webhook(cb_update_a)
        self.assertEqual(res_a.get("status"), "ok")
        self.assertEqual(res_a.get("details", {}).get("logged_by"), "Alice Caregiver")
        log_id_a = res_a.get("details", {}).get("log_id")

        # User B clicks (different user, different callback id)
        cb_update_b = {
            "callback_query": {
                "id": f"cb_user_b_{uuid.uuid4().hex[:6]}",
                "data": f"med:log:{med_id}:10.0",
                "from": {"first_name": "Bob", "username": "bob_doctor"},
                "message": {"message_id": 1002, "chat": {"id": -100555}}
            }
        }
        res_b = med_bot.handle_med_webhook(cb_update_b)
        self.assertEqual(res_b.get("status"), "ok")
        self.assertEqual(res_b.get("details", {}).get("logged_by"), "Bob")
        log_id_b = res_b.get("details", {}).get("log_id")

        # Verify both logs exist and are correctly attributed
        logs = db.get_recent_med_logs(limit=2, medication_id=med_id)
        self.assertEqual(len(logs), 2)
        notes_list = [l["notes"] for l in logs]
        self.assertIn("Logged via quick button by Alice Caregiver", notes_list)
        self.assertIn("Logged via quick button by Bob", notes_list)

        # Cleanup
        db.delete_medication_log(log_id_a)
        db.delete_medication_log(log_id_b)

    @patch("bot_client.TelegramBotClient.answer_callback_query")
    @patch("bot_client.TelegramBotClient.edit_message_text")
    def test_anonymous_callback_attribution(self, mock_edit, mock_answer):
        """Verify anonymous update (e.g. missing from field or empty) attributes to 'User' without crashing."""
        mock_answer.return_value = {"ok": True}
        mock_edit.return_value = {"ok": True}

        med_id = db.add_medication_preset(f"AnonDrug_{uuid.uuid4().hex[:6]}", 5.0, "mg")

        # Callback without 'from' field
        cb_update_anon = {
            "callback_query": {
                "id": f"cb_anon_{uuid.uuid4().hex[:6]}",
                "data": f"med:log:{med_id}:5.0",
                "message": {"message_id": 1003, "chat": {"id": 12345}}
            }
        }
        res = med_bot.handle_med_webhook(cb_update_anon)
        self.assertEqual(res.get("status"), "ok")
        self.assertEqual(res.get("details", {}).get("logged_by"), "User")
        log_id = res.get("details", {}).get("log_id")

        log = db.get_recent_med_logs(limit=1, medication_id=med_id)
        self.assertEqual(log[0]["notes"], "Logged via quick button by User")

        # Cleanup
        db.delete_medication_log(log_id)


class TestAdversarialElapsedFormatting(unittest.TestCase):
    """Stress tests elapsed time calculation across extreme durations, future timestamps, and timezone offsets."""

    def test_subsecond_and_exact_zero_elapsed(self):
        now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(med_bot.format_elapsed_time(now, now), "just now")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(microseconds=500), now), "just now")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(seconds=1), now), "just now")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(seconds=59), now), "just now")

    def test_minute_boundaries(self):
        now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(seconds=60), now), "1m ago")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(seconds=119), now), "1m ago")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(seconds=120), now), "2m ago")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(minutes=59, seconds=59), now), "59m ago")

    def test_hour_boundaries(self):
        now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(hours=1), now), "1h ago")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(hours=1, seconds=59), now), "1h ago")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(hours=1, minutes=1), now), "1h 1m ago")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(hours=1, minutes=59), now), "1h 59m ago")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(hours=23, minutes=59), now), "23h 59m ago")

    def test_day_boundaries_and_extreme_days(self):
        now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(days=1), now), "1d ago")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(days=1, hours=4), now), "1d 4h ago")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(days=30), now), "30d ago")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(days=365), now), "365d ago")
        self.assertEqual(med_bot.format_elapsed_time(now - timedelta(days=3650), now), "3650d ago")

    def test_negative_clock_drift_and_future_timestamps(self):
        """Verify clock skew/drift where past_time is in the future gracefully returns 'just now'."""
        now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)
        future_5s = now + timedelta(seconds=5)
        future_1h = now + timedelta(hours=1)
        future_100d = now + timedelta(days=100)

        self.assertEqual(med_bot.format_elapsed_time(future_5s, now), "just now")
        self.assertEqual(med_bot.format_elapsed_time(future_1h, now), "just now")
        self.assertEqual(med_bot.format_elapsed_time(future_100d, now), "just now")

    def test_timezone_aware_vs_naive_interoperability(self):
        """Verify mixed naive and timezone-aware datetimes do not raise TypeError."""
        now_aware = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)
        past_naive = datetime(2026, 8, 21, 11, 0, 0)
        self.assertEqual(med_bot.format_elapsed_time(past_naive, now_aware), "1h ago")

        now_naive = datetime(2026, 8, 21, 12, 0, 0)
        past_aware = datetime(2026, 8, 21, 11, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(med_bot.format_elapsed_time(past_aware, now_naive), "1h ago")

        # Non-UTC timezone (EST = -5h, JST = +9h)
        est = timezone(timedelta(hours=-5))
        jst = timezone(timedelta(hours=9))
        past_est = datetime(2026, 8, 21, 7, 0, 0, tzinfo=est)  # 12:00 UTC
        now_jst = datetime(2026, 8, 21, 23, 0, 0, tzinfo=jst)  # 14:00 UTC
        self.assertEqual(med_bot.format_elapsed_time(past_est, now_jst), "2h ago")

    def test_invalid_types_and_strings(self):
        self.assertEqual(med_bot.format_elapsed_time(None), "never")
        self.assertEqual(med_bot.format_elapsed_time("invalid_string"), "unknown")
        self.assertEqual(med_bot.format_elapsed_time("2026-08-21T11:00:00Z", datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)), "1h ago")


if __name__ == "__main__":
    unittest.main()
