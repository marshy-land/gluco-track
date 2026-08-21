"""
tests/test_milestone4_monkebot.py
Comprehensive Milestone 4 Unit & Integration Test Suite for MonkeHelper Master Hub (@monkehelper_bot).

Covers:
- Feature 14: Multi-Domain Health Synthesis & Executive Daily Briefing (/briefing)
- Feature 15: Nighttime Quiet Hours & Emergency Hypoglycemia Alert Bypass
- Feature 16: Care Circle Role-Based Access Control (RBAC)
- Feature 17: Multi-Bot Ecosystem Health Router & Subsystem Observer
"""

import os
import sys
import unittest
import math
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import pytz

# Ensure root directory is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Mock psycopg2 if not installed
try:
    import psycopg2
except ImportError:
    mock_psycopg2 = MagicMock()
    sys.modules["psycopg2"] = mock_psycopg2
    sys.modules["psycopg2.extras"] = MagicMock()
    sys.modules["psycopg2.pool"] = MagicMock()

import db
import monke_bot
from bot_client import get_bot_client


class TestMilestone4MonkeBot(unittest.TestCase):

    def setUp(self):
        # Reset in-memory cache and state between each test
        monke_bot.reset_in_memory_state()

        # Prevent DB TCP socket timeouts during test execution
        self.conn_patcher = patch("db.get_connection", side_effect=Exception("Offline DB test"))
        self.conn_patcher.start()

    def tearDown(self):
        self.conn_patcher.stop()

    # =========================================================================
    # FEATURE 14: MULTI-DOMAIN HEALTH SYNTHESIS & BRIEFING
    # =========================================================================

    @patch("db.get_latest_reading")
    @patch("db.get_history")
    @patch("db.get_statistics")
    @patch("db.get_insulin_history")
    @patch("db.get_recent_med_logs")
    @patch("db.get_medication_presets")
    @patch("db.get_medication_summary")
    @patch("db.get_food_history")
    @patch("circadian_analysis.get_circadian_biometrics_summary")
    def test_get_unified_daily_briefing_aggregation(
        self,
        mock_bio,
        mock_food,
        mock_med_sum,
        mock_presets,
        mock_med_logs,
        mock_insulin,
        mock_stats,
        mock_history,
        mock_latest
    ):
        """Feature 14: Verify cross-domain aggregation synthesizes all physiological domains."""
        now = datetime.now(timezone.utc)
        mock_latest.return_value = {"value": 115.0, "timestamp": now}
        mock_history.return_value = [
            {"value": 115.0, "timestamp": now},
            {"value": 110.0, "timestamp": now - timedelta(minutes=15)},
            {"value": 105.0, "timestamp": now - timedelta(minutes=30)}
        ]
        mock_stats.return_value = {
            "average_glucose": 118.0,
            "total_readings": 96,
            "time_in_range": {"target_percent": 90.0, "low_percent": 1.0, "high_percent": 9.0}
        }
        mock_insulin.return_value = [
            {"long_acting": 13.0, "timestamp": now - timedelta(hours=6), "is_imputed": False},
            {"long_acting": 13.0, "timestamp": now - timedelta(hours=18), "is_imputed": False},
            {"rapid_acting": 4.0, "meal": 0.0, "correction": 0.0, "user_change": 0.0, "timestamp": now - timedelta(hours=2), "is_imputed": False}
        ]
        mock_presets.return_value = [{"id": 1, "name": "metformin"}, {"id": 2, "name": "lisinopril"}]
        mock_med_logs.return_value = [
            {"id": 1, "name": "Metformin", "dose_taken": 500.0, "dose_unit": "mg", "timestamp": now - timedelta(hours=3), "notes": "by Owner"}
        ]
        mock_med_sum.return_value = [{"name": "Metformin", "total_doses": 1}]
        mock_food.return_value = [
            {"carbs_g": 45.0, "food_type": "Chicken and Rice", "timestamp": now - timedelta(hours=4)}
        ]
        mock_bio.return_value = {
            "sleep": {"total_hours_24h": 7.8, "efficiency_percent": 92.5, "deep_percent": 21.0, "rem_percent": 23.0, "quality_rating": "Optimal"},
            "circadian": {"sleep_midpoint": "03:45 AM", "chronotype": "Intermediate (Balanced)"},
            "rhr": {"daytime_baseline": 65.0, "nocturnal_baseline": 55.0, "dipping_percent": -15.4, "dipper_category": "Normal Dipper", "nadir_bpm": 50.0, "nadir_time": "04:10 AM"},
            "isf": {"modifier": 1.00, "explanation": "Baseline insulin sensitivity intact."}
        }

        briefing = monke_bot.get_unified_daily_briefing(hours=24)

        # 1. CGM domain
        self.assertIn("cgm", briefing)
        self.assertEqual(briefing["cgm"]["current_glucose"], 115.0)
        self.assertEqual(briefing["cgm"]["mean_glucose"], 118.0)
        self.assertAlmostEqual(briefing["cgm"]["gmi"], round(3.31 + 0.02392 * 118.0, 2), places=2)
        self.assertEqual(briefing["cgm"]["tir_percent"], 90.0)

        # 2. Insulin domain
        self.assertIn("insulin", briefing)
        self.assertEqual(briefing["insulin"]["basal_units"], 26.0)
        self.assertEqual(briefing["insulin"]["bolus_units"], 4.0)
        self.assertEqual(briefing["insulin"]["tdd"], 30.0)

        # 3. Medications domain
        self.assertIn("medications", briefing)
        self.assertEqual(briefing["medications"]["active_presets_count"], 2)
        self.assertEqual(len(briefing["medications"]["recent_intakes"]), 1)

        # 4. Circadian & Sleep domain
        self.assertIn("circadian", briefing)
        self.assertEqual(briefing["circadian"]["total_sleep_hours"], 7.8)
        self.assertEqual(briefing["circadian"]["efficiency_percent"], 92.5)
        self.assertEqual(briefing["circadian"]["isf_modifier"], 1.00)

        # 5. Nutrition domain
        self.assertIn("nutrition", briefing)
        self.assertEqual(briefing["nutrition"]["total_carbs_g"], 45.0)
        self.assertGreater(briefing["nutrition"]["total_protein_g"], 0.0)

        # 6. Digest text
        digest = briefing.get("digest_text", "")
        self.assertIn("Executive Health Briefing", digest)
        self.assertIn("Glucose & CGM", digest)
        self.assertIn("Insulin", digest)
        self.assertIn("Medications", digest)
        self.assertIn("Sleep & Circadian", digest)

    @patch("bot_client.TelegramBotClient.send_message")
    def test_briefing_slash_command(self, mock_send):
        """Feature 14: Verify /briefing command returns structured payload and sends formatted card."""
        mock_send.return_value = {"ok": True, "result": {"message_id": 999}}
        
        update = {
            "update_id": 8001,
            "message": {
                "message_id": 101,
                "chat": {"id": 555, "type": "private"},
                "from": {"id": 101, "first_name": "Alex"},
                "text": "/briefing"
            }
        }
        res = monke_bot.handle_monke_webhook(update)
        self.assertEqual(res.get("status"), "ok")
        self.assertEqual(res.get("action"), "briefing_sent")
        self.assertIn("briefing", res)
        self.assertTrue(mock_send.called)

    @patch("bot_client.TelegramBotClient.edit_message_text")
    @patch("bot_client.TelegramBotClient.answer_callback_query")
    def test_briefing_interactive_drilldowns(self, mock_answer, mock_edit):
        """Feature 14: Verify interactive inline buttons for domain drill-downs."""
        mock_answer.return_value = {"ok": True}
        mock_edit.return_value = {"ok": True}

        # 1. Glucose drill-down
        res_g = monke_bot.handle_monke_webhook({
            "callback_query": {
                "id": "cb_g1",
                "data": "mh:briefing:glucose",
                "message": {"message_id": 50, "chat": {"id": 555}},
                "from": {"id": 101, "first_name": "Alex"}
            }
        })
        self.assertEqual(res_g.get("status"), "ok")
        self.assertEqual(res_g.get("action"), "briefing_drilldown_shown")
        self.assertEqual(res_g.get("subview"), "glucose")

        # 2. Meds drill-down
        res_m = monke_bot.handle_monke_webhook({
            "callback_query": {
                "id": "cb_m1",
                "data": "mh:briefing:meds",
                "message": {"message_id": 50, "chat": {"id": 555}},
                "from": {"id": 101, "first_name": "Alex"}
            }
        })
        self.assertEqual(res_m.get("status"), "ok")
        self.assertEqual(res_m.get("subview"), "meds")

        # 3. Sleep drill-down
        res_s = monke_bot.handle_monke_webhook({
            "callback_query": {
                "id": "cb_s1",
                "data": "mh:briefing:sleep",
                "message": {"message_id": 50, "chat": {"id": 555}},
                "from": {"id": 101, "first_name": "Alex"}
            }
        })
        self.assertEqual(res_s.get("status"), "ok")
        self.assertEqual(res_s.get("subview"), "sleep")

        # 4. Nutrition drill-down
        res_n = monke_bot.handle_monke_webhook({
            "callback_query": {
                "id": "cb_n1",
                "data": "mh:briefing:nutrition",
                "message": {"message_id": 50, "chat": {"id": 555}},
                "from": {"id": 101, "first_name": "Alex"}
            }
        })
        self.assertEqual(res_n.get("status"), "ok")
        self.assertEqual(res_n.get("subview"), "nutrition")

        # 5. Return to Main Briefing
        res_main = monke_bot.handle_monke_webhook({
            "callback_query": {
                "id": "cb_main",
                "data": "mh:briefing:main",
                "message": {"message_id": 50, "chat": {"id": 555}},
                "from": {"id": 101, "first_name": "Alex"}
            }
        })
        self.assertEqual(res_main.get("status"), "ok")
        self.assertEqual(res_main.get("action"), "briefing_refreshed")

    # =========================================================================
    # FEATURE 15: QUIET HOURS & EMERGENCY HYPO BYPASS
    # =========================================================================

    def test_quiet_hours_evaluation_boundaries(self):
        """Feature 15: Exact microsecond boundary tests for cross-midnight window (23:00 to 07:00)."""
        tz = pytz.timezone("America/New_York")
        
        # 22:59:59 -> Inactive (False)
        dt_2259 = tz.localize(datetime(2026, 8, 21, 22, 59, 59))
        self.assertFalse(monke_bot.is_in_quiet_hours(dt_2259, start_hour=23, end_hour=7))

        # 23:00:00 -> Active (True)
        dt_2300 = tz.localize(datetime(2026, 8, 21, 23, 0, 0))
        self.assertTrue(monke_bot.is_in_quiet_hours(dt_2300, start_hour=23, end_hour=7))

        # 02:30:00 -> Active (True)
        dt_0230 = tz.localize(datetime(2026, 8, 22, 2, 30, 0))
        self.assertTrue(monke_bot.is_in_quiet_hours(dt_0230, start_hour=23, end_hour=7))

        # 06:59:59 -> Active (True)
        dt_0659 = tz.localize(datetime(2026, 8, 22, 6, 59, 59))
        self.assertTrue(monke_bot.is_in_quiet_hours(dt_0659, start_hour=23, end_hour=7))

        # 07:00:00 -> Inactive (False)
        dt_0700 = tz.localize(datetime(2026, 8, 22, 7, 0, 0))
        self.assertFalse(monke_bot.is_in_quiet_hours(dt_0700, start_hour=23, end_hour=7))

    def test_should_suppress_notification_routine_vs_emergency_hypo(self):
        """Feature 15: Routine check-in is suppressed during quiet hours, but hypo (<70) bypasses unconditionally."""
        tz = pytz.timezone("America/New_York")
        quiet_dt = tz.localize(datetime(2026, 8, 22, 3, 15, 0))

        # 1. Routine check-in during quiet hours -> Suppressed
        suppressed, reason, meta = monke_bot.should_suppress_notification(
            event_type="lantus_reminder",
            glucose_value=125.0,
            dt=quiet_dt
        )
        self.assertTrue(suppressed)
        self.assertEqual(reason, "quiet_hours")
        self.assertEqual(meta.get("reason"), "quiet_hours")

        # 2. Critical Hypo (54 mg/dL) during quiet hours -> Unconditional Bypass
        suppressed_hypo, reason_hypo, meta_hypo = monke_bot.should_suppress_notification(
            event_type="urgent_low",
            glucose_value=54.0,
            dt=quiet_dt,
            iob=1.0
        )
        self.assertFalse(suppressed_hypo, "Emergency hypo must NOT be suppressed during quiet hours.")
        self.assertEqual(reason_hypo, "emergency_hypo_bypass")
        self.assertEqual(meta_hypo.get("urgency"), "critical_low")
        self.assertGreaterEqual(meta_hypo.get("recommended_rescue_carbs", 0), 15)

        # 3. Predicted Rapid Drop (<65 in 30m) -> Bypass
        preds = [{"minutes": 30, "value": 62.0}]
        suppressed_drop, reason_drop, meta_drop = monke_bot.should_suppress_notification(
            event_type="rapid_drop",
            glucose_value=85.0,
            dt=quiet_dt,
            predictions=preds
        )
        self.assertFalse(suppressed_drop)
        self.assertEqual(reason_drop, "emergency_hypo_bypass")
        self.assertEqual(meta_drop.get("urgency"), "rapid_drop")

    @patch("bot_client.TelegramBotClient.send_message")
    def test_quiethours_command_options(self, mock_send):
        """Feature 15: Test /quiethours status, /quiethours 22 8, and /quiethours off."""
        mock_send.return_value = {"ok": True}

        # 1. Status
        res_stat = monke_bot.handle_quiethours_command("/quiethours status", user_id="101", chat_id="555")
        self.assertEqual(res_stat.get("status"), "ok")
        self.assertEqual(res_stat.get("action"), "quiet_hours_status_sent")

        # 2. Update window
        res_set = monke_bot.handle_quiethours_command("/quiethours 22 8", user_id="101", chat_id="555")
        self.assertEqual(res_set.get("status"), "ok")
        self.assertEqual(res_set.get("action"), "quiet_hours_updated")
        self.assertEqual(res_set["config"]["start_hour"], 22)
        self.assertEqual(res_set["config"]["end_hour"], 8)

        # 3. Toggle off
        res_off = monke_bot.handle_quiethours_command("/quiethours off", user_id="101", chat_id="555")
        self.assertEqual(res_off.get("status"), "ok")
        self.assertEqual(res_off.get("action"), "quiet_hours_toggled")
        self.assertFalse(res_off["config"]["enabled"])

    # =========================================================================
    # FEATURE 16: CARE CIRCLE ROLE-BASED ACCESS CONTROL (RBAC)
    # =========================================================================

    def test_care_circle_bootstrap_and_role_checks(self):
        """Feature 16: Verify bootstrapping and role hierarchy resolution."""
        data = monke_bot.get_care_circle_data()
        self.assertIn("owner_id", data)
        self.assertIn("members", data)

        # Primary Owner check
        self.assertEqual(monke_bot.get_user_role("101"), "Owner")
        self.assertTrue(monke_bot.is_authorized("101", "Owner"))
        self.assertTrue(monke_bot.is_authorized("101", "Caregiver"))
        self.assertTrue(monke_bot.is_authorized("101", "Viewer"))

        # Primary Caregiver check
        self.assertEqual(monke_bot.get_user_role("202"), "Caregiver")
        self.assertFalse(monke_bot.is_authorized("202", "Owner"))
        self.assertTrue(monke_bot.is_authorized("202", "Caregiver"))
        self.assertTrue(monke_bot.is_authorized("202", "Viewer"))

        # Unregistered user check
        self.assertEqual(monke_bot.get_user_role("999999"), "None")
        self.assertFalse(monke_bot.is_authorized("999999", "Viewer"))

    def test_add_and_remove_caregiver_lifecycle(self):
        """Feature 16: Add Caregiver, verify elevation, then remove and verify revocation."""
        # 1. Add Caregiver
        ok, msg = monke_bot.add_care_circle_member("505", "Caregiver", name="Dr. Alex", added_by="101")
        self.assertTrue(ok)
        self.assertEqual(monke_bot.get_user_role("505"), "Caregiver")
        self.assertTrue(monke_bot.is_authorized("505", "Caregiver"))
        self.assertFalse(monke_bot.is_authorized("505", "Owner"))

        # 2. Add Viewer
        ok_v, _ = monke_bot.add_care_circle_member("606", "Viewer", name="Observer", added_by="101")
        self.assertTrue(ok_v)
        self.assertEqual(monke_bot.get_user_role("606"), "Viewer")
        self.assertTrue(monke_bot.is_authorized("606", "Viewer"))
        self.assertFalse(monke_bot.is_authorized("606", "Caregiver"))

        # 3. Remove Caregiver
        ok_rem, _ = monke_bot.remove_care_circle_member("505")
        self.assertTrue(ok_rem)
        self.assertEqual(monke_bot.get_user_role("505"), "None")

        # 4. Protection: Cannot remove sole owner
        ok_sole_owner, err = monke_bot.remove_care_circle_member("101")
        self.assertFalse(ok_sole_owner)
        self.assertIn("Cannot remove", err)

    @patch("bot_client.TelegramBotClient.send_message")
    def test_rbac_command_permission_enforcement(self, mock_send):
        """Feature 16: Verify /addcaregiver, /removecaregiver, and /admin enforce Owner role."""
        mock_send.return_value = {"ok": True}

        # 1. Owner executing /admin -> Allowed
        res_owner = monke_bot.handle_monke_webhook({
            "update_id": 9001,
            "message": {"chat": {"id": 101, "type": "private"}, "from": {"id": 101}, "text": "/admin"}
        })
        self.assertEqual(res_owner.get("status"), "ok")
        self.assertEqual(res_owner.get("action"), "admin_action_performed")

        # 2. Viewer executing /admin -> Denied
        # Register user 303 as Viewer
        monke_bot.add_care_circle_member("303", "Viewer", name="Dr. Viewer", added_by="101")
        res_viewer = monke_bot.handle_monke_webhook({
            "update_id": 9002,
            "message": {"chat": {"id": 303, "type": "private"}, "from": {"id": 303}, "text": "/admin"}
        })
        self.assertEqual(res_viewer.get("status"), "denied")
        self.assertEqual(res_viewer.get("action"), "permission_denied")
        self.assertIn("Permission denied", res_viewer.get("message", ""))

        # 3. Viewer attempting /addcaregiver -> Denied
        res_add_denied = monke_bot.handle_monke_webhook({
            "update_id": 9003,
            "message": {"chat": {"id": 303, "type": "private"}, "from": {"id": 303}, "text": "/addcaregiver 404 Caregiver"}
        })
        self.assertEqual(res_add_denied.get("status"), "denied")

        # 4. Owner adding caregiver -> Allowed
        res_add_ok = monke_bot.handle_monke_webhook({
            "update_id": 9004,
            "message": {"chat": {"id": 101, "type": "private"}, "from": {"id": 101}, "text": "/addcaregiver 404 Caregiver"}
        })
        self.assertEqual(res_add_ok.get("status"), "ok")
        self.assertEqual(res_add_ok.get("action"), "caregiver_added")

    # =========================================================================
    # FEATURE 17: MULTI-BOT ECOSYSTEM ROUTER & OBSERVER
    # =========================================================================

    @patch("bot_client.TelegramBotClient.send_message")
    def test_status_and_bots_commands(self, mock_send):
        """Feature 17: Verify /status and /bots aggregate ecosystem health and directory."""
        mock_send.return_value = {"ok": True}

        # 1. /status command
        res_status = monke_bot.handle_monke_webhook({
            "update_id": 9101,
            "message": {"chat": {"id": 101, "type": "private"}, "from": {"id": 101}, "text": "/status"}
        })
        self.assertEqual(res_status.get("status"), "ok")
        self.assertEqual(res_status.get("action"), "status_card_sent")

        # 2. /bots command
        res_bots = monke_bot.handle_monke_webhook({
            "update_id": 9102,
            "message": {"chat": {"id": 101, "type": "private"}, "from": {"id": 101}, "text": "/bots"}
        })
        self.assertEqual(res_bots.get("status"), "ok")
        self.assertEqual(res_bots.get("action"), "bots_card_sent")

    def test_foreign_namespace_immediate_rejection(self):
        """Feature 17: MonkeHelper must immediately reject callbacks from foreign namespaces (gt:, med:, bio:)."""
        for foreign_cb in ["gt:meal:45", "med:log:1:10.0", "bio:sync:now"]:
            res = monke_bot.handle_monke_webhook({
                "callback_query": {
                    "id": "foreign_test",
                    "data": foreign_cb,
                    "message": {"message_id": 12, "chat": {"id": 101}},
                    "from": {"id": 101}
                }
            })
            self.assertEqual(res.get("status"), "ignored")
            self.assertEqual(res.get("action"), "foreign_namespace_ignored")

    @patch("bot_client.TelegramBotClient.answer_callback_query")
    def test_60s_sliding_window_debouncing(self, mock_answer):
        """Feature 17: Replaying the same callback_query.id within 60s returns action='debounced'."""
        mock_answer.return_value = {"ok": True}
        cb_payload = {
            "callback_query": {
                "id": "repeat_query_999",
                "data": "mh:status:refresh",
                "message": {"message_id": 12, "chat": {"id": 101}},
                "from": {"id": 101}
            }
        }

        # First tap -> Processed
        res1 = monke_bot.handle_monke_webhook(cb_payload)
        self.assertEqual(res1.get("status"), "ok")
        self.assertEqual(res1.get("action"), "status_refreshed")

        # Second tap within 60s -> Debounced
        res2 = monke_bot.handle_monke_webhook(cb_payload)
        self.assertEqual(res2.get("status"), "ok")
        self.assertEqual(res2.get("action"), "debounced")

    def test_group_noise_and_target_disambiguation(self):
        """Feature 17: Verify group chat ambient noise filtering and other-bot disambiguation."""
        # 1. Ambient conversation in supergroup without commands/mentions -> Ignored
        res_ambient = monke_bot.handle_monke_webhook({
            "update_id": 9201,
            "message": {
                "chat": {"id": -10012345, "type": "supergroup"},
                "from": {"id": 202, "first_name": "Mom"},
                "text": "Hey what should we have for dinner tonight?"
            }
        })
        self.assertEqual(res_ambient.get("status"), "ignored")
        self.assertEqual(res_ambient.get("action"), "group_noise_ignored")

        # 2. Command explicitly directed at another bot (e.g. /status@medflowassist_bot) -> Ignored
        res_other = monke_bot.handle_monke_webhook({
            "update_id": 9202,
            "message": {
                "chat": {"id": -10012345, "type": "supergroup"},
                "from": {"id": 202},
                "text": "/status@medflowassist_bot"
            }
        })
        self.assertEqual(res_other.get("status"), "ignored")
        self.assertEqual(res_other.get("action"), "command_for_other_bot")


if __name__ == "__main__":
    unittest.main()
