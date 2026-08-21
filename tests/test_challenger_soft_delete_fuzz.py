"""
tests/test_challenger_soft_delete_fuzz.py
Active Adversarial Fuzzing Suite:
Verifying that soft deletion rigorously preserves 100% of historical dose rows in medication_logs
under aggressive randomized lifecycles, rapid deletions, reactivations, and concurrent-style queries.
"""

import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
import random
import uuid
import string

import db
import med_bot


class TestSoftDeleteHistoricalFuzzing(unittest.TestCase):
    """Adversarial randomized fuzz harness for medication preset soft-deletion integrity."""

    def setUp(self):
        db.init_db()

    def test_randomized_multi_preset_fuzz_lifecycle(self):
        """
        Fuzz scenario:
        1. Generate N distinct presets with random names, doses, and units.
        2. Insert M historical dose logs with randomized timestamps across past 30 days.
        3. Execute random actions: soft-delete, log-more-doses, reactivate, query-history.
        4. Assert that NOT A SINGLE historical dose row is deleted or orphaned.
        """
        random.seed(42)
        n_presets = 10
        preset_data = {}
        all_logged_doses = {}  # med_id -> list of log_ids

        # Phase 1: Create presets
        run_id = uuid.uuid4().hex[:6]
        for i in range(n_presets):
            suffix = f"{run_id}_{i}"
            name = f"FuzzMed_{suffix}"
            dose = round(random.uniform(1.0, 500.0), 2)
            unit = random.choice(["mg", "mcg", "ml", "units", "tablets"])
            med_id = db.add_medication_preset(name, dose, unit)
            self.assertIsInstance(med_id, int)
            preset_data[med_id] = {
                "name": name,
                "dose": dose,
                "unit": unit,
                "is_active": True
            }
            all_logged_doses[med_id] = []

        # Phase 2: Insert initial batches of historical logs
        now = datetime.now(timezone.utc)
        total_logs_inserted = 0

        for med_id, p in preset_data.items():
            num_doses = random.randint(3, 5)
            for d in range(num_doses):
                offset_hours = random.randint(1, 720)
                ts = now - timedelta(hours=offset_hours)
                dose_val = p["dose"] * random.choice([0.5, 1.0, 2.0])
                notes = f"Fuzz dose #{d+1} for {p['name']} (Caregiver_{random.randint(1, 10)})"
                lid = db.log_medication_dose(
                    medication_id=med_id,
                    dose_taken=dose_val,
                    timestamp=ts,
                    notes=notes
                )
                self.assertIsInstance(lid, int)
                all_logged_doses[med_id].append(lid)
                total_logs_inserted += 1

        # Phase 3: Active randomized fuzz mutations (30 rounds)
        med_ids = list(preset_data.keys())
        for round_idx in range(30):
            action = random.choice(["delete", "delete_idempotent", "reactivate", "log_dose_active", "log_dose_inactive", "query_history"])
            target_id = random.choice(med_ids)
            p_info = preset_data[target_id]

            if action == "delete":
                was_active = p_info["is_active"]
                if random.choice([True, False]):
                    res = db.delete_medication_preset(p_info["name"])
                else:
                    res = db.delete_medication_preset(target_id)
                self.assertEqual(res, was_active)
                p_info["is_active"] = False

            elif action == "delete_idempotent":
                if not p_info["is_active"]:
                    self.assertFalse(db.delete_medication_preset(p_info["name"]))
                    self.assertFalse(db.delete_medication_preset(target_id))

            elif action == "reactivate":
                new_dose = round(random.uniform(5.0, 100.0), 1)
                new_id = db.add_medication_preset(p_info["name"], new_dose, p_info["unit"])
                self.assertEqual(new_id, target_id)
                p_info["is_active"] = True
                p_info["dose"] = new_dose

            elif action in ["log_dose_active", "log_dose_inactive"]:
                offset_min = random.randint(1, 1000)
                ts = now - timedelta(minutes=offset_min)
                lid = db.log_medication_dose(
                    medication_id=target_id,
                    dose_taken=p_info["dose"],
                    timestamp=ts,
                    notes=f"Mutation round {round_idx} log"
                )
                all_logged_doses[target_id].append(lid)
                total_logs_inserted += 1

            elif action == "query_history":
                hist = db.get_recent_med_logs(limit=100, medication_name=p_info["name"])
                expected_count = len(all_logged_doses[target_id])
                self.assertEqual(len(hist), expected_count, f"History count mismatch for {p_info['name']}")
                timestamps = [h["timestamp"] for h in hist]
                self.assertEqual(timestamps, sorted(timestamps, reverse=True))

        # Phase 4: Final verification across ALL presets
        total_remaining_logs = 0
        for med_id, p_info in preset_data.items():
            expected_log_ids = all_logged_doses[med_id]
            actual_logs = db.get_recent_med_logs(limit=200, medication_id=med_id)
            actual_ids = [l["id"] for l in actual_logs]

            # 1. 100% of log IDs exist
            self.assertEqual(len(actual_ids), len(expected_log_ids))
            self.assertEqual(set(actual_ids), set(expected_log_ids))
            total_remaining_logs += len(actual_ids)

            # 2. Preset record still exists in medication_types
            preset_record = db.get_medication_preset_by_id(med_id)
            self.assertIsNotNone(preset_record)
            self.assertEqual(preset_record["is_active"], p_info["is_active"])

        self.assertEqual(total_remaining_logs, total_logs_inserted)

        # Clean up
        for med_id, l_ids in all_logged_doses.items():
            for lid in l_ids:
                db.delete_medication_log(lid)

    @patch("bot_client.TelegramBotClient.send_message")
    def test_special_characters_sql_injection_names_soft_delete(self, mock_send):
        """Stress test medication names with apostrophes, emoji, and SQL special chars."""
        mock_send.return_value = {"ok": True}
        tricky_names = [
            f"O'Connor's Elixir_{uuid.uuid4().hex[:6]}",
            f"Advil (Ibuprofen) 200mg & Caffeine_{uuid.uuid4().hex[:6]}",
            f"Med--DROP TABLE medication_logs;--_{uuid.uuid4().hex[:6]}",
            f"💉 Insulin Glargine (Lantus®)_{uuid.uuid4().hex[:6]}",
            f"Drug-with-dashes_{uuid.uuid4().hex[:6]}"
        ]

        for name in tricky_names:
            med_id = db.add_medication_preset(name, 10.0, "mg")
            self.assertIsInstance(med_id, int)

            # Log 3 doses
            lids = []
            for i in range(3):
                lid = db.log_medication_dose(med_id, 10.0, notes=f"Tricky dose {i}")
                lids.append(lid)

            # Soft delete by exact string
            deleted = db.delete_medication_preset(name)
            self.assertTrue(deleted, f"Failed to soft-delete '{name}'")

            # Soft delete again -> False
            self.assertFalse(db.delete_medication_preset(name))

            # Query via bot history
            update = {
                "message": {
                    "chat": {"id": 99999, "type": "private"},
                    "text": f"/history {name}"
                }
            }
            res = med_bot.handle_med_webhook(update)
            self.assertEqual(res.get("status"), "ok")
            self.assertEqual(res.get("count"), 3)

            # All logs still exist in db
            logs = db.get_recent_med_logs(limit=10, medication_id=med_id)
            self.assertEqual(len(logs), 3)

            # Clean up
            for lid in lids:
                db.delete_medication_log(lid)


if __name__ == "__main__":
    unittest.main()
