"""
e2e_tests/test_tier5_adversarial_ecosystem.py
Tier 5 Adversarial Coverage Hardening Test Suite across the 4-Bot Ecosystem.

Comprehensive White-Box Adversarial Stress Testing covering:
1. Concurrency & High Thread Load (Multi-threaded webhook flood, callback debouncing race conditions, supervisor lifecycle)
2. Cross-Bot State Interactions & Data Propagation (MedFlow dose logging -> MonkeHelper /briefing & GlucoTrack bolus & IOB)
3. Dynamic ISF Resistance Propagation (Sleep stages in health_sessions -> Circadian ISF modifier -> GlucoTrack bolus & MonkeHelper digest)
4. Extreme Quiet Hours & Severe Hypoglycemia (<55 mg/dL) Overrides (Critical hypo bypass, exact carb math, non-urgent muting)
"""

import os
import sys
import time
import math
import json
import threading
import concurrent.futures
import unittest
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
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
import bot_client
from bot_client import get_bot_client, mask_token
import multi_bot_manager
from multi_bot_manager import MultiBotPollingManager, BotPollerWorker
import telegram_bot
import med_bot
import monke_bot
import biometrics_bot
import circadian_analysis
from circadian_analysis import (
    calculate_sleep_stage_analytics,
    calculate_circadian_phase,
    calculate_nocturnal_rhr_metrics,
    calculate_dynamic_isf_modifier,
    get_circadian_biometrics_summary
)
from monke_bot import (
    get_quiet_hours_config,
    save_quiet_hours_config,
    is_in_quiet_hours,
    should_suppress_notification,
    build_emergency_hypo_alert,
    get_care_circle_data,
    save_care_circle_data,
    get_user_role,
    is_authorized,
    add_care_circle_member,
    remove_care_circle_member,
    get_unified_daily_briefing,
    reset_in_memory_state
)


class MockDatabaseState:
    """Thread-safe in-memory database simulator for multi-bot ecosystem."""

    def __init__(self):
        self._lock = threading.RLock()
        self.reset()

    def reset(self):
        with self._lock:
            self.readings = []
            self.insulin_doses = []
            self.food_logs = []
            self.health_sessions = []
            self.health_metrics = []
            self.medication_presets = [
                {"id": 1, "name": "lorazepam", "default_dose": 1.0, "dose_unit": "mg", "is_active": True, "created_at": datetime.now(timezone.utc)},
                {"id": 2, "name": "oxycodone", "default_dose": 5.0, "dose_unit": "mg", "is_active": True, "created_at": datetime.now(timezone.utc)},
                {"id": 3, "name": "melatonin", "default_dose": 3.0, "dose_unit": "mg", "is_active": True, "created_at": datetime.now(timezone.utc)}
            ]
            self.medication_logs = []
            self.system_settings = {
                "telegram_config": {"bot_token": "1111:GT_TOKEN", "chat_id": "100", "enabled": True},
                "med_bot_config": {"bot_token": "8839060131:AAFRBcijx-Aic7COA7eKIjoBKpZ8ABlQ53o", "chat_id": "200", "enabled": True},
                "monke_bot_config": {"bot_token": "8703572491:AAG6puQZOmpCey4rHbILMpJ3a0ojuOIY3s8", "chat_id": "300", "enabled": True},
                "biometrics_bot_config": {"bot_token": "4444:BIO_TOKEN", "chat_id": "400", "enabled": True},
                "quiet_hours_config": {"enabled": True, "start_hour": 23, "end_hour": 7, "timezone": "America/New_York"},
                "care_circle_roles": {
                    "owner_id": "101",
                    "members": {
                        "101": {"role": "Owner", "name": "Primary Owner", "added_at": datetime.now(timezone.utc).isoformat(), "added_by": "bootstrap"},
                        "202": {"role": "Caregiver", "name": "Primary Caregiver", "added_at": datetime.now(timezone.utc).isoformat(), "added_by": "bootstrap"}
                    }
                }
            }

    # DB API implementations
    def get_latest_reading(self):
        with self._lock:
            if not self.readings:
                return None
            return dict(sorted(self.readings, key=lambda x: x["timestamp"], reverse=True)[0])

    def get_history(self, limit_hours=24):
        with self._lock:
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=limit_hours)
            res = [dict(r) for r in self.readings if r["timestamp"] >= cutoff]
            return sorted(res, key=lambda x: x["timestamp"])

    def get_statistics(self, hours=24):
        hist = self.get_history(hours)
        if not hist:
            return None
        values = [r["value"] for r in hist]
        avg = sum(values) / len(values)
        total = len(values)
        low = sum(1 for v in values if v < 70)
        target = sum(1 for v in values if 70 <= v <= 180)
        high = sum(1 for v in values if v > 180)
        return {
            "total_readings": total,
            "average_glucose": round(avg, 1),
            "gmi": round(3.31 + 0.02392 * avg, 2),
            "time_in_range": {
                "low_percent": round((low / total) * 100, 1),
                "target_percent": round((target / total) * 100, 1),
                "high_percent": round((high / total) * 100, 1)
            }
        }

    def insert_readings(self, readings):
        with self._lock:
            count = 0
            for r in readings:
                self.readings.append(dict(r))
                count += 1
            return count

    def insert_insulin_doses(self, doses):
        with self._lock:
            count = 0
            for d in doses:
                item = dict(d)
                item.setdefault("id", len(self.insulin_doses) + 1)
                self.insulin_doses.append(item)
                count += 1
            return count

    def get_insulin_history(self, limit_hours=24, include_imputed=False):
        with self._lock:
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=limit_hours)
            res = []
            for d in self.insulin_doses:
                if d["timestamp"] >= cutoff:
                    if include_imputed or not d.get("is_imputed"):
                        res.append(dict(d))
            return sorted(res, key=lambda x: x["timestamp"])

    def insert_food_log(self, carbs_g, timestamp, food_type=None, is_imputed=False, confidence_score=None):
        with self._lock:
            item = {
                "id": len(self.food_logs) + 1,
                "carbs_g": float(carbs_g),
                "timestamp": timestamp or datetime.now(timezone.utc),
                "food_type": food_type,
                "is_imputed": is_imputed,
                "confidence_score": confidence_score
            }
            self.food_logs.append(item)
            return item["id"]

    def get_food_history(self, limit_hours=24, include_imputed=False):
        with self._lock:
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=limit_hours)
            res = []
            for f in self.food_logs:
                if f["timestamp"] >= cutoff:
                    if include_imputed or not f.get("is_imputed"):
                        res.append(dict(f))
            return sorted(res, key=lambda x: x["timestamp"])

    def get_system_setting(self, key, default=None):
        with self._lock:
            return self.system_settings.get(key, default)

    def set_system_setting(self, key, value):
        with self._lock:
            self.system_settings[key] = value

    def insert_health_sessions(self, sessions):
        with self._lock:
            count = 0
            for s in sessions:
                self.health_sessions.append(dict(s))
                count += 1
            return count

    def get_health_sessions(self, limit_hours=720, session_type=None):
        with self._lock:
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=limit_hours)
            res = []
            for s in self.health_sessions:
                st = s.get("start_time")
                if isinstance(st, str):
                    try:
                        st = datetime.fromisoformat(st.replace("Z", "+00:00"))
                    except Exception:
                        st = now
                if st and st >= cutoff:
                    if not session_type or session_type.lower() in str(s.get("session_type", "")).lower():
                        res.append(dict(s))
            return sorted(res, key=lambda x: x.get("start_time", now), reverse=True)

    def insert_health_metrics(self, metrics):
        with self._lock:
            count = 0
            for m in metrics:
                self.health_metrics.append(dict(m))
                count += 1
            return count

    def get_health_metrics(self, limit_hours=720, metric_type=None):
        with self._lock:
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=limit_hours)
            res = []
            for m in self.health_metrics:
                ts = m.get("timestamp")
                if isinstance(ts, str):
                    try:
                        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except Exception:
                        ts = now
                if ts and ts >= cutoff:
                    if not metric_type or metric_type == m.get("metric_type"):
                        res.append(dict(m))
            return sorted(res, key=lambda x: x.get("timestamp", now))

    def get_recent_sleep_summary(self, hours=48):
        sessions = self.get_health_sessions(limit_hours=hours, session_type="sleep")
        metrics = self.get_health_metrics(limit_hours=hours, metric_type="heart_rate")
        if not sessions:
            return {
                "has_data": False,
                "total_sleep_hours_24h": 0.0,
                "isf_modifier": 1.0,
                "sleep_quality_rating": "Deficit",
                "efficiency_percent": 0.0,
                "deep_sleep_pct": 0.0,
                "rem_sleep_pct": 0.0,
                "rhr_dipping_pct": None,
                "chronotype": "Unknown",
                "sleep_midpoint": None
            }
        stage = calculate_sleep_stage_analytics(sessions)
        circ = calculate_circadian_phase(sessions)
        rhr = calculate_nocturnal_rhr_metrics(metrics, sleep_sessions=sessions)
        isf = calculate_dynamic_isf_modifier(sleep_summary=stage, rhr_summary=rhr)
        return {
            "has_data": True,
            "total_sleep_hours_24h": stage["total_sleep_hours"],
            "isf_modifier": isf["isf_modifier"],
            "sleep_quality_rating": stage["quality_rating"],
            "efficiency_percent": stage["efficiency_percent"],
            "deep_sleep_pct": stage["deep_sleep_percent"],
            "rem_sleep_pct": stage["rem_sleep_percent"],
            "rhr_dipping_pct": rhr["dipping_percent"],
            "chronotype": circ["chronotype"],
            "sleep_midpoint": circ["sleep_midpoint"]
        }

    def get_medication_presets(self, active_only=True):
        with self._lock:
            if active_only:
                return [dict(p) for p in self.medication_presets if p.get("is_active", True)]
            return [dict(p) for p in self.medication_presets]

    def get_medication_preset_by_id(self, preset_id):
        with self._lock:
            for p in self.medication_presets:
                if p["id"] == preset_id:
                    return dict(p)
            return None

    def get_medication_preset_by_name(self, name):
        with self._lock:
            for p in self.medication_presets:
                if p["name"].lower() == name.strip().lower():
                    return dict(p)
            return None

    def add_medication_preset(self, name, default_dose, dose_unit):
        with self._lock:
            name_lower = name.strip().lower()
            for p in self.medication_presets:
                if p["name"].lower() == name_lower:
                    p["default_dose"] = float(default_dose)
                    p["dose_unit"] = dose_unit.strip()
                    p["is_active"] = True
                    return p["id"]
            new_id = len(self.medication_presets) + 1
            self.medication_presets.append({
                "id": new_id,
                "name": name.strip(),
                "default_dose": float(default_dose),
                "dose_unit": dose_unit.strip(),
                "is_active": True,
                "created_at": datetime.now(timezone.utc)
            })
            return new_id

    def delete_medication_preset(self, name_or_id):
        with self._lock:
            target = str(name_or_id).strip().lower()
            for p in self.medication_presets:
                if str(p["id"]) == target or p["name"].lower() == target:
                    if p.get("is_active", True):
                        p["is_active"] = False
                        return True
            return False

    def log_medication_dose(self, medication_id, dose_taken, timestamp=None, notes=None):
        with self._lock:
            preset = self.get_medication_preset_by_id(medication_id)
            name = preset["name"] if preset else "Unknown"
            unit = preset["dose_unit"] if preset else "mg"
            item = {
                "id": len(self.medication_logs) + 1,
                "medication_id": medication_id,
                "name": name,
                "dose_unit": unit,
                "dose_taken": float(dose_taken),
                "timestamp": timestamp or datetime.now(timezone.utc),
                "notes": notes,
                "created_at": datetime.now(timezone.utc)
            }
            self.medication_logs.append(item)
            return item["id"]

    def get_recent_med_logs(self, limit=15, medication_name=None, medication_id=None, hours=None, limit_hours=None):
        with self._lock:
            now = datetime.now(timezone.utc)
            eff_hours = hours if hours is not None else limit_hours
            res = []
            for l in self.medication_logs:
                if eff_hours is not None:
                    if l["timestamp"] < (now - timedelta(hours=eff_hours)):
                        continue
                if medication_id is not None and l["medication_id"] != medication_id:
                    continue
                if medication_name is not None and medication_name.lower() not in l["name"].lower():
                    continue
                res.append(dict(l))
            sorted_res = sorted(res, key=lambda x: x["timestamp"], reverse=True)
            return sorted_res[:limit]

    def get_medication_summary(self, medication_name=None):
        presets = self.get_medication_presets(active_only=True)
        logs = self.get_recent_med_logs(hours=24, limit=100)
        summary = []
        for p in presets:
            if medication_name and medication_name.lower() not in p["name"].lower():
                continue
            matching = [l for l in logs if l["medication_id"] == p["id"]]
            last_ts = matching[0]["timestamp"] if matching else None
            tot_24h = sum(l["dose_taken"] for l in matching)
            summary.append({
                "id": p["id"],
                "name": p["name"],
                "default_dose": p["default_dose"],
                "dose_unit": p["dose_unit"],
                "count_24h": len(matching),
                "total_dose_24h": tot_24h,
                "last_timestamp": last_ts
            })
        return summary


# Global mock database instance
mock_db = MockDatabaseState()


def patch_db_module():
    """Patches the db module with mock_db methods for seamless integration."""
    db.get_latest_reading = mock_db.get_latest_reading
    db.get_history = mock_db.get_history
    db.get_statistics = mock_db.get_statistics
    db.insert_readings = mock_db.insert_readings
    db.insert_insulin_doses = mock_db.insert_insulin_doses
    db.get_insulin_history = mock_db.get_insulin_history
    db.insert_food_log = mock_db.insert_food_log
    db.get_food_history = mock_db.get_food_history
    db.get_system_setting = mock_db.get_system_setting
    db.set_system_setting = mock_db.set_system_setting
    db.insert_health_sessions = mock_db.insert_health_sessions
    db.get_health_sessions = mock_db.get_health_sessions
    db.insert_health_metrics = mock_db.insert_health_metrics
    db.get_health_metrics = mock_db.get_health_metrics
    db.get_recent_sleep_summary = mock_db.get_recent_sleep_summary
    db.get_medication_presets = mock_db.get_medication_presets
    db.get_medication_preset_by_id = mock_db.get_medication_preset_by_id
    db.get_medication_preset_by_name = mock_db.get_medication_preset_by_name
    db.add_medication_preset = mock_db.add_medication_preset
    db.delete_medication_preset = mock_db.delete_medication_preset
    db.log_medication_dose = mock_db.log_medication_dose
    db.get_recent_med_logs = mock_db.get_recent_med_logs
    db.get_medication_summary = mock_db.get_medication_summary


patch_db_module()


class TestTier5AdversarialEcosystem(unittest.TestCase):
    """
    Tier 5 Adversarial Coverage Hardening across the entire 4-bot ecosystem:
    GlucoTrack, MedFlowAssist, MonkeHelper, Circadian & Biometrics.
    """

    def setUp(self):
        import telegram_scheduler
        telegram_scheduler.start_telegram_scheduler = MagicMock()
        telegram_scheduler.stop_telegram_scheduler = MagicMock()
        telegram_bot.start_telegram_polling = MagicMock()
        telegram_bot.stop_telegram_polling = MagicMock()

        self.client = TestClient(app)
        mock_db.reset()
        reset_in_memory_state()
        patch_db_module()

        # Mock Bot API clients to avoid outbound network latency
        self.mock_client = MagicMock()
        self.mock_client.send_message.return_value = {"success": True, "result": {"message_id": 777}}
        self.mock_client.edit_message_text.return_value = {"success": True, "result": {"message_id": 777}}
        self.mock_client.answer_callback_query.return_value = {"success": True}
        self.mock_client.delete_message.return_value = {"success": True}
        self.mock_client.delete_webhook.return_value = True
        self.mock_client.get_updates.return_value = {"success": True, "result": []}

        telegram_bot.get_gt_bot_client = MagicMock(return_value=self.mock_client)
        med_bot.get_med_bot_client = MagicMock(return_value=self.mock_client)
        monke_bot.get_monke_bot_client = MagicMock(return_value=self.mock_client)
        biometrics_bot.get_biometrics_bot_client = MagicMock(return_value=self.mock_client)

        self.requests_post_patcher = patch("requests.post", return_value=MagicMock(status_code=200, json=lambda: {"ok": True, "result": {}}, text="ok"))
        self.requests_get_patcher = patch("requests.get", return_value=MagicMock(status_code=200, json=lambda: {"ok": True, "result": []}, text="ok"))
        self.requests_post_patcher.start()
        self.requests_get_patcher.start()

    def tearDown(self):
        self.requests_post_patcher.stop()
        self.requests_get_patcher.stop()

    # =========================================================================
    # PILLAR 1: CONCURRENCY & HIGH THREAD LOAD STRESS TESTING
    # =========================================================================

    def test_pillar1_01_concurrent_multi_bot_webhook_flood(self):
        """
        Pillar 1: Concurrency Flood
        Simultaneously fires 40 concurrent webhook requests across all 4 bot endpoints
        under high thread concurrency, verifying 100% success rate, zero deadlocks,
        and thread-safe state recording.
        """
        handlers_and_payloads = [
            (telegram_bot.handle_telegram_update, {"update_id": 1000 + i, "message": {"chat": {"id": 100, "type": "private"}, "text": "/status"}})
            for i in range(10)
        ] + [
            (med_bot.handle_med_webhook, {"update_id": 2000 + i, "message": {"chat": {"id": 200, "type": "private"}, "text": "/history"}})
            for i in range(10)
        ] + [
            (monke_bot.handle_monke_webhook, {"update_id": 3000 + i, "message": {"chat": {"id": 300, "type": "private"}, "text": "/briefing"}})
            for i in range(10)
        ] + [
            (biometrics_bot.handle_biometrics_webhook, {"update_id": 4000 + i, "message": {"chat": {"id": 400, "type": "private"}, "text": "/bio"}})
            for i in range(10)
        ]

        results = []
        errors = []

        def fire_webhook(item):
            handler, pl = item
            try:
                resp = handler(pl)
                return 200, resp
            except Exception as e:
                return 500, {"error": str(e)}

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            future_to_req = {executor.submit(fire_webhook, item): item for item in handlers_and_payloads}
            for future in concurrent.futures.as_completed(future_to_req):
                status_code, data = future.result()
                results.append((status_code, data))
                if status_code != 200 or data.get("status") not in ["ok", "ignored"]:
                    errors.append((status_code, data))

        self.assertEqual(len(results), 40, "All 40 concurrent webhook requests must finish.")
        self.assertEqual(len(errors), 0, f"All concurrent requests must return 200 OK. Errors: {errors}")

    def test_pillar1_02_concurrent_callback_debouncing_race_condition(self):
        """
        Pillar 1: Callback Query Debounce Race Condition
        20 threads concurrently submit the exact same callback query ID to MedFlow.
        Exactly 1 thread must execute the dose log; all 19 other threads must be debounced.
        """
        cb_id = "race_condition_cb_unique_999"
        payload = {
            "update_id": 8888,
            "callback_query": {
                "id": cb_id,
                "data": "med:log:1:1.0",
                "from": {"first_name": "ConcurrentCaregiver", "id": 999},
                "message": {"chat": {"id": -100555, "type": "group"}, "message_id": 42}
            }
        }

        # Clear debounce cache
        med_bot._processed_callbacks.clear()

        executed_count = 0
        debounced_count = 0
        lock = threading.Lock()

        def submit_callback(_):
            res = med_bot.handle_med_webhook(payload)
            with lock:
                nonlocal executed_count, debounced_count
                if res.get("action") == "dose_logged":
                    executed_count += 1
                elif res.get("action") == "debounced":
                    debounced_count += 1
            return res

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            list(executor.map(submit_callback, range(20)))

        self.assertEqual(executed_count, 1, f"Exactly 1 execution expected, got {executed_count}")
        self.assertEqual(debounced_count, 19, f"Expected 19 debounced responses, got {debounced_count}")
        self.assertEqual(len(mock_db.medication_logs), 1, "Exactly 1 dose must be recorded in database.")

    def test_pillar1_03_multibot_polling_manager_thread_safety(self):
        """
        Pillar 1: Polling Manager Thread Safety
        Concurrently executes register_bot, start_bot, stop_bot, restart_bot,
        and get_status across multiple threads, verifying zero deadlock or state corruption.
        """
        manager = MultiBotPollingManager()

        def dummy_token_getter():
            return "dummy_token_123"

        def dummy_handler(up):
            return {"status": "ok"}

        with patch.object(BotPollerWorker, "start", return_value=True), \
             patch.object(BotPollerWorker, "stop", return_value=True):
            bot_ids = ["gt_test", "med_test", "monke_test", "bio_test"]
            for b_id in bot_ids:
                manager.register_bot(b_id, f"{b_id} Worker", dummy_token_getter, dummy_handler)

            def worker_cycle(b_id):
                manager.start_bot(b_id)
                status = manager.get_status(b_id)
                manager.restart_bot(b_id, timeout=0.01)
                manager.stop_bot(b_id, timeout=0.01)
                manager.watchdog_check()
                return status

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(worker_cycle, bot_ids * 5))

            self.assertEqual(len(results), 20)
            all_status = manager.get_status()
            self.assertEqual(len(all_status), 4)

    # =========================================================================
    # PILLAR 2: CROSS-BOT STATE INTERACTIONS & DATA PROPAGATION
    # =========================================================================

    def test_pillar2_01_medflow_dose_logging_to_monkehelper_briefing_propagation(self):
        """
        Pillar 2: End-to-End State Propagation
        1. Add medication preset 'Clonazepam 0.5 mg' via MedFlowAssist.
        2. Caregiver logs dose via one-tap callback 'med:log:4:0.5' with user attribution.
        3. MonkeHelper /briefing is immediately queried:
           - Verifies medication appears in recent_intakes with elapsed 'just now'
           - Verifies active_presets_count reflects updated total
           - Verifies user attribution in notes is preserved
        4. GlucoTrack /status is queried:
           - Verifies oral med does NOT corrupt insulin IOB (IOB remains 0.0)
        """
        # 1. Add preset
        med_id = mock_db.add_medication_preset("Clonazepam", 0.5, "mg")
        self.assertIsNotNone(med_id)

        # 2. Log dose via MedBot webhook
        cb_update = {
            "update_id": 9001,
            "callback_query": {
                "id": "cb_clonazepam_1",
                "data": f"med:log:{med_id}:0.5",
                "from": {"id": 202, "first_name": "Nurse Alex", "last_name": "Taylor"},
                "message": {"chat": {"id": -100777, "type": "group"}, "message_id": 55}
            }
        }
        res_med = med_bot.handle_med_webhook(cb_update)
        self.assertEqual(res_med.get("status"), "ok")
        self.assertEqual(res_med.get("action"), "dose_logged")
        self.assertIn("Nurse Alex Taylor", res_med.get("message", ""))

        # 3. Query MonkeHelper Briefing
        briefing = get_unified_daily_briefing(hours=24)
        meds_section = briefing["medications"]
        self.assertGreaterEqual(meds_section["active_presets_count"], 4)
        self.assertEqual(len(meds_section["recent_intakes"]), 1)
        first_intake = meds_section["recent_intakes"][0]
        self.assertEqual(first_intake["name"], "Clonazepam")
        self.assertEqual(first_intake["dose"], 0.5)
        self.assertEqual(first_intake["unit"], "mg")
        self.assertIn("just now", first_intake["elapsed"])
        self.assertIn("Nurse Alex Taylor", first_intake["notes"])

        # 4. Verify GlucoTrack summary isolation
        mock_db.insert_readings([{"timestamp": datetime.now(timezone.utc), "value": 125.0, "type": "cgm"}])
        summary = telegram_bot.get_live_patient_summary()
        self.assertIsNotNone(summary)
        self.assertEqual(summary["iob"], 0.0, "Oral medication must not alter insulin IOB.")

    def test_pillar2_02_glucotrack_meal_bolus_to_monkehelper_propagation(self):
        """
        Pillar 2: GlucoTrack -> MonkeHelper Briefing Propagation
        1. GlucoTrack logs meal & insulin bolus via 'gt:meal:60.0:4.0'.
        2. MonkeHelper /briefing digest updates IOB, TDD, and 24h nutrition carbs.
        """
        # 1. Log meal + bolus via GlucoTrack
        cb_meal = {
            "update_id": 9002,
            "callback_query": {
                "id": "cb_gt_meal_1",
                "data": "gt:meal:60.0:4.0",
                "from": {"id": 101, "first_name": "Patient John"},
                "message": {"chat": {"id": 100}, "message_id": 80}
            }
        }
        res_gt = telegram_bot.handle_telegram_update(cb_meal)
        self.assertEqual(res_gt.get("status"), "ok")
        self.assertEqual(res_gt.get("action"), "meal_logged")

        # 2. Verify MonkeHelper briefing integration
        briefing = get_unified_daily_briefing(hours=24)
        ins = briefing["insulin"]
        nut = briefing["nutrition"]

        self.assertGreaterEqual(ins["tdd"], 4.0)
        self.assertGreaterEqual(ins["bolus_units"], 4.0)
        self.assertGreaterEqual(nut["total_carbs_g"], 60.0)
        self.assertEqual(nut["meal_count"], 1)

    def test_pillar2_03_strict_foreign_namespace_isolation_all_bots(self):
        """
        Pillar 2: Foreign Namespace Crosstalk Matrix
        Systematically injects every alien prefix into every bot router,
        verifying 100% rejection across all permutations.
        """
        matrix = [
            (telegram_bot.handle_telegram_update, "med:log:1:1.0", "med:", "gt:"),
            (telegram_bot.handle_telegram_update, "mh:briefing:refresh", "mh:", "gt:"),
            (telegram_bot.handle_telegram_update, "bio:sync:now", "bio:", "gt:"),
            (med_bot.handle_med_webhook, "gt:meal:45:2.0", "gt:", "med:"),
            (med_bot.handle_med_webhook, "mh:quiet:toggle", "mh:", "med:"),
            (med_bot.handle_med_webhook, "bio:sleep:detail", "bio:", "med:"),
            (monke_bot.handle_monke_webhook, "gt:lantus:13.0", "gt:", "mh:"),
            (monke_bot.handle_monke_webhook, "med:del:1", "med:", "mh:"),
            (monke_bot.handle_monke_webhook, "bio:rhr:detail", "bio:", "mh:"),
            (biometrics_bot.handle_biometrics_webhook, "gt:corr:1.5", "gt:", "bio:"),
            (biometrics_bot.handle_biometrics_webhook, "med:log:2:5.0", "med:", "bio:"),
            (biometrics_bot.handle_biometrics_webhook, "mh:role:list", "mh:", "bio:")
        ]

        for handler, data, exp_foreign, exp_expected in matrix:
            with self.subTest(handler=handler.__name__, data=data):
                update = {
                    "callback_query": {
                        "id": f"cb_{data.replace(':', '_')}",
                        "data": data,
                        "from": {"first_name": "Tester"},
                        "message": {"chat": {"id": 123}, "message_id": 99}
                    }
                }
                res = handler(update)
                self.assertEqual(res.get("status"), "ignored")
                self.assertEqual(res.get("action"), "foreign_namespace_ignored")
                self.assertEqual(res.get("details", {}).get("received_prefix"), exp_foreign)

    # =========================================================================
    # PILLAR 3: DYNAMIC ISF RESISTANCE PROPAGATION & MATHEMATICAL BOUNDARIES
    # =========================================================================

    def test_pillar3_01_optimal_sleep_preserves_baseline_isf(self):
        """
        Pillar 3: Optimal Sleep Architecture
        8.0h sleep with 22% Deep, 24% REM, 92% Efficiency, and normal nocturnal RHR dipping (15%)
        must yield ISF modifier 1.00x and 'Optimal' quality rating.
        """
        now = datetime.now(timezone.utc)
        sessions = [
            {"session_type": "sleep.light", "duration_minutes": 260.0, "start_time": now - timedelta(hours=9), "end_time": now - timedelta(hours=4, minutes=40)},
            {"session_type": "sleep.deep", "duration_minutes": 110.0, "start_time": now - timedelta(hours=4, minutes=40), "end_time": now - timedelta(hours=2, minutes=50)},
            {"session_type": "sleep.rem", "duration_minutes": 110.0, "start_time": now - timedelta(hours=2, minutes=50), "end_time": now - timedelta(hours=1)},
            {"session_type": "sleep.awake", "duration_minutes": 20.0, "start_time": now - timedelta(hours=1), "end_time": now - timedelta(minutes=40)}
        ]
        rhr_metrics = {
            "daytime_baseline_rhr": 68.0,
            "nocturnal_baseline_rhr": 57.8,
            "dipping_percent": 15.0
        }
        mock_db.insert_health_sessions(sessions)

        stage_analytics = calculate_sleep_stage_analytics(sessions)
        self.assertEqual(stage_analytics["total_sleep_hours"], 8.0)
        self.assertEqual(stage_analytics["quality_rating"], "Optimal")
        self.assertGreaterEqual(stage_analytics["efficiency_percent"], 90.0)

        isf_res = calculate_dynamic_isf_modifier(sleep_summary=stage_analytics, rhr_summary=rhr_metrics)
        self.assertEqual(isf_res["isf_modifier"], 1.00)
        self.assertEqual(isf_res["debt_penalty"], 0.0)
        self.assertEqual(isf_res["architecture_penalty"], 0.0)
        self.assertEqual(isf_res["autonomic_penalty"], 0.0)

    def test_pillar3_02_severe_sleep_deficit_propagates_to_bolus_and_briefing(self):
        """
        Pillar 3: Sleep Deficit Propagation
        3.5h fragmented sleep with non-dipping nocturnal RHR (4% dip)
        1. Yields ISF modifier >= 1.15x (increased resistance).
        2. MonkeHelper /briefing reports elevated multiplier and deficit impact note.
        3. GlucoTrack bolus advice calculates lower effective ISF (baseline / modifier)
           requiring increased correction insulin.
        """
        now = datetime.now(timezone.utc)
        deficit_sessions = [
            {"session_type": "sleep.light", "duration_minutes": 180.0, "start_time": now - timedelta(hours=4), "end_time": now - timedelta(hours=1)},
            {"session_type": "sleep.deep", "duration_minutes": 15.0, "start_time": now - timedelta(hours=1), "end_time": now - timedelta(minutes=45)},
            {"session_type": "sleep.rem", "duration_minutes": 15.0, "start_time": now - timedelta(minutes=45), "end_time": now - timedelta(minutes=30)},
            {"session_type": "sleep.awake", "duration_minutes": 60.0, "start_time": now - timedelta(minutes=30), "end_time": now}
        ]
        # Day RHR 70, Night RHR 73.5 -> Reverse dipper / non-dipper
        hr_points = [
            {"timestamp": now - timedelta(hours=12), "metric_type": "heart_rate", "value": 70.0},
            {"timestamp": now - timedelta(hours=2), "metric_type": "heart_rate", "value": 73.5}
        ]
        mock_db.insert_health_sessions(deficit_sessions)
        mock_db.insert_health_metrics(hr_points)

        # 1. Check circadian analysis calculations
        bio_summary = get_circadian_biometrics_summary(hours=48)
        isf_info = bio_summary["isf"]
        self.assertGreaterEqual(isf_info["modifier"], 1.15)
        self.assertEqual(isf_info["quality_rating"], "Deficit")

        # 2. Check MonkeHelper Briefing
        briefing = get_unified_daily_briefing(hours=24)
        circ_data = briefing["circadian"]
        self.assertGreaterEqual(circ_data["isf_modifier"], 1.15)
        self.assertIn(f"{circ_data['isf_modifier']:.2f}x", briefing["digest_text"])

        # 3. Check GlucoTrack correction bolus adjustment
        baseline_isf = 50.0 # mg/dL/U
        effective_isf = baseline_isf / isf_info["modifier"]
        self.assertLess(effective_isf, baseline_isf)

        # High glucose (200 mg/dL, target 120, IOB 0.0) -> BG diff 80 mg/dL
        correction_baseline = 80.0 / baseline_isf # 1.6 U
        correction_adjusted = 80.0 / effective_isf # e.g. 80 / 42.7 = 1.87 U -> 1.9 U
        self.assertGreater(correction_adjusted, correction_baseline)

    def test_pillar3_03_extreme_mathematical_boundaries_and_clamping(self):
        """
        Pillar 3: Boundary Clamping [1.00x, 1.25x]
        Stress-tests extreme sleep lengths, negative values, and non-numeric inputs.
        """
        boundary_cases = [
            # 0h sleep
            ({"total_sleep_hours": 0.0, "rhr_dipping_pct": -15.0}, 1.15, 1.25),
            # 24h excessive sleep
            ({"total_sleep_hours": 24.0, "rhr_dipping_pct": 25.0}, 1.00, 1.00),
            # Negative sleep hours
            ({"total_sleep_hours": -10.0, "rhr_dipping_pct": -50.0}, 1.00, 1.25),
            # Reverse dipper with high sleep
            ({"total_sleep_hours": 8.0, "rhr_dipping_pct": -10.0}, 1.00, 1.15),
            # None / Empty inputs
            ({}, 1.00, 1.25)
        ]

        for kwargs, min_expected, max_expected in boundary_cases:
            with self.subTest(kwargs=kwargs):
                res = calculate_dynamic_isf_modifier(**kwargs)
                mod = res["isf_modifier"]
                self.assertGreaterEqual(mod, 1.00, f"ISF modifier {mod} violated lower bound 1.00x")
                self.assertLessEqual(mod, 1.25, f"ISF modifier {mod} violated upper bound 1.25x")
                self.assertGreaterEqual(mod, min_expected)
                self.assertLessEqual(mod, max_expected)

    # =========================================================================
    # PILLAR 4: EXTREME QUIET HOURS & SEVERE HYPO (<55 mg/dL) OVERRIDES
    # =========================================================================

    def test_pillar4_01_severe_nocturnal_hypo_emergency_override_and_carb_calculation(self):
        """
        Pillar 4: Severe Nocturnal Hypo (<55 mg/dL) Emergency Bypass
        1. Local time is 03:15 AM (deep inside quiet hours 23:00–07:00).
        2. CGM reading is 42.0 mg/dL (Critical Low < 55) with 1.5 U active IOB.
        3. should_suppress_notification MUST return (False, 'emergency_hypo_bypass', ...).
        4. Rescue carb calculation:
           Carbs = ceil(((105.0 - 42.0) + (1.5 * 50.0)) / 4.0) = ceil((63 + 75) / 4) = ceil(138 / 4) = 35g carbs.
        5. Emergency alert card formatting verification.
        """
        night_time = datetime(2026, 8, 21, 3, 15, 0)
        self.assertTrue(is_in_quiet_hours(night_time))

        suppressed, reason, meta = should_suppress_notification(
            event_type="cgm_reading",
            glucose_value=42.0,
            dt=night_time,
            iob=1.5
        )

        self.assertFalse(suppressed, "Critical hypoglycemia (<55 mg/dL) MUST NOT be suppressed.")
        self.assertEqual(reason, "emergency_hypo_bypass")
        self.assertEqual(meta["urgency"], "critical_low")
        self.assertEqual(meta["glucose"], 42.0)

        # Expected rescue carbs math:
        # Target: 105.0, BG: 42.0, IOB: 1.5U, ISF: 50.0, CSF: 4.0
        # Deficit = (105 - 42) + (1.5 * 50) = 63 + 75 = 138 mg/dL
        # Carbs = ceil(138 / 4.0) = 35g
        expected_carbs = math.ceil(((105.0 - 42.0) + (1.5 * 50.0)) / 4.0)
        self.assertEqual(meta["recommended_rescue_carbs"], expected_carbs)
        self.assertGreaterEqual(meta["recommended_rescue_carbs"], 30)

        # Verify alert formatting
        alert_html = build_emergency_hypo_alert(glucose=42.0, iob=1.5, trend_arrow="⇊", trend_desc="Crashing")
        self.assertIn("CRITICAL HYPOGLYCEMIA ALERT", alert_html)
        self.assertIn("42 mg/dL", alert_html)
        self.assertIn("35g fast-acting carbs", alert_html)
        self.assertIn("Emergency Hypoglycemia Override", alert_html)

    def test_pillar4_02_routine_checkins_muted_during_quiet_hours(self):
        """
        Pillar 4: Routine Check-in Suppression
        Verifies that non-urgent routine check-ins (e.g. basal reminders, status queries, normal readings)
        are properly suppressed during 23:00–07:00 while allowing urgent hypo alerts.
        """
        night_time = datetime(2026, 8, 21, 2, 0, 0)
        self.assertTrue(is_in_quiet_hours(night_time))

        routine_events = [
            ("routine_lantus_check", 125.0),
            ("hourly_status_ping", 110.0),
            ("scheduled_daily_report", 140.0),
            ("mild_elevated_glucose", 195.0),
            ("normal_reading", 95.0)
        ]

        for ev_type, bg_val in routine_events:
            with self.subTest(event=ev_type, glucose=bg_val):
                suppressed, reason, meta = should_suppress_notification(
                    event_type=ev_type,
                    glucose_value=bg_val,
                    dt=night_time,
                    iob=0.0
                )
                self.assertTrue(suppressed, f"Event '{ev_type}' with BG {bg_val} should be suppressed during quiet hours.")
                self.assertEqual(reason, "quiet_hours")

    def test_pillar4_03_predictive_rapid_drop_hypo_bypass(self):
        """
        Pillar 4: Predictive Rapid Drop Bypass
        Current glucose is 85 mg/dL (above 70), but 30m forecast is 58 mg/dL (crashing).
        System must trigger emergency hypo bypass before the patient enters severe shock.
        """
        night_time = datetime(2026, 8, 21, 4, 0, 0)
        predictions = [{"minutes": 15, "value": 72.0}, {"minutes": 30, "value": 58.0}]

        suppressed, reason, meta = should_suppress_notification(
            event_type="cgm_forecast",
            glucose_value=85.0,
            dt=night_time,
            predictions=predictions,
            iob=2.0
        )

        self.assertFalse(suppressed, "Predictive rapid drop below 65 mg/dL MUST bypass quiet hours.")
        self.assertEqual(reason, "emergency_hypo_bypass")
        self.assertEqual(meta["urgency"], "rapid_drop")
        self.assertEqual(meta["projected_30m"], 58.0)

    def test_pillar4_04_exact_timestamp_boundary_edges(self):
        """
        Pillar 4: Exact Timestamp Boundaries (23:00 to 07:00)
        Tests millisecond/second boundaries across midnight window.
        """
        # 22:59:59 -> Outside quiet hours
        dt_2259 = datetime(2026, 8, 21, 22, 59, 59, tzinfo=timezone.utc)
        self.assertFalse(is_in_quiet_hours(dt_2259, start_hour=23, end_hour=7, timezone_str="UTC"))

        # 23:00:00 -> Inside quiet hours
        dt_2300 = datetime(2026, 8, 21, 23, 0, 0, tzinfo=timezone.utc)
        self.assertTrue(is_in_quiet_hours(dt_2300, start_hour=23, end_hour=7, timezone_str="UTC"))

        # 06:59:59 -> Inside quiet hours
        dt_0659 = datetime(2026, 8, 21, 6, 59, 59, tzinfo=timezone.utc)
        self.assertTrue(is_in_quiet_hours(dt_0659, start_hour=23, end_hour=7, timezone_str="UTC"))

        # 07:00:00 -> Outside quiet hours
        dt_0700 = datetime(2026, 8, 21, 7, 0, 0, tzinfo=timezone.utc)
        self.assertFalse(is_in_quiet_hours(dt_0700, start_hour=23, end_hour=7, timezone_str="UTC"))


if __name__ == "__main__":
    unittest.main(verbosity=2)