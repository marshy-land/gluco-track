"""
tests/test_milestone3_circadian.py
Comprehensive Unit & Integration Test Suite for Milestone 3: Circadian & Biometrics Modular Service.

Covers:
- Feature 10: Sleep Stage Architecture Analytics (TST, Efficiency %, Deep %, REM %, Light %, SFI)
- Feature 11: Circadian Phase, Sleep Midpoint (MSF), Chronotype & Nocturnal RHR Dipping
- Feature 12: Dynamic ISF Resistance Modifier (Multi-component physiological model & bounds)
- Feature 13: Biometrics Bot Commands, Callbacks, Debouncing, and Group/DM Dual-Mode Ingress
- Database integration with db.get_recent_sleep_summary
"""

import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import circadian_analysis
import db
import biometrics_bot


class TestSleepStageAnalytics(unittest.TestCase):
    """Tests for Feature 10: Sleep Stage Architecture Analytics."""

    def test_optimal_sleep_with_stages(self):
        """Verify analytics calculation with light, deep, rem, and awake stages."""
        sessions = [
            {"session_type": "sleep.light", "duration_minutes": 240.0},
            {"session_type": "sleep.deep", "duration_minutes": 90.0},
            {"session_type": "sleep.rem", "duration_minutes": 90.0},
            {"session_type": "sleep.awake", "duration_minutes": 30.0}
        ]
        res = circadian_analysis.calculate_sleep_stage_analytics(sessions)
        self.assertTrue(res["has_data"])
        self.assertEqual(res["total_sleep_hours"], 7.0)
        self.assertEqual(res["total_sleep_minutes"], 420.0)
        self.assertEqual(res["time_in_bed_hours"], 7.5)
        self.assertEqual(res["time_in_bed_minutes"], 450.0)
        self.assertAlmostEqual(res["efficiency_percent"], 93.3, places=1)
        self.assertAlmostEqual(res["deep_sleep_percent"], 21.4, places=1)
        self.assertAlmostEqual(res["rem_sleep_percent"], 21.4, places=1)
        self.assertAlmostEqual(res["light_sleep_percent"], 57.1, places=1)
        self.assertEqual(res["awake_episodes_count"], 1)
        self.assertEqual(res["quality_rating"], "Optimal")
        self.assertTrue(res["is_staged"])

    def test_moderate_sleep_duration(self):
        """Verify moderate sleep duration categorization."""
        sessions = [
            {"session_type": "sleep.light", "duration_minutes": 200.0},
            {"session_type": "sleep.deep", "duration_minutes": 80.0},
            {"session_type": "sleep.rem", "duration_minutes": 80.0},
            {"session_type": "sleep.awake", "duration_minutes": 20.0}
        ]
        res = circadian_analysis.calculate_sleep_stage_analytics(sessions)
        self.assertTrue(res["has_data"])
        self.assertEqual(res["total_sleep_hours"], 6.0)
        self.assertEqual(res["quality_rating"], "Moderate")

    def test_deficit_sleep_duration(self):
        """Verify sleep deficit categorization."""
        sessions = [
            {"session_type": "sleep.light", "duration_minutes": 120.0},
            {"session_type": "sleep.deep", "duration_minutes": 45.0},
            {"session_type": "sleep.rem", "duration_minutes": 45.0},
            {"session_type": "sleep.awake", "duration_minutes": 30.0}
        ]
        res = circadian_analysis.calculate_sleep_stage_analytics(sessions)
        self.assertTrue(res["has_data"])
        self.assertEqual(res["total_sleep_hours"], 3.5)
        self.assertEqual(res["quality_rating"], "Deficit")

    def test_empty_sessions(self):
        """Verify zero division immunity on empty sessions."""
        res = circadian_analysis.calculate_sleep_stage_analytics([])
        self.assertFalse(res["has_data"])
        self.assertEqual(res["total_sleep_hours"], 0.0)
        self.assertEqual(res["efficiency_percent"], 0.0)
        self.assertEqual(res["quality_rating"], "Deficit")

    def test_generic_unsegmented_sleep(self):
        """Verify non-staged generic sleep session handling."""
        sessions = [
            {"session_type": "sleep", "duration_minutes": 480.0}
        ]
        res = circadian_analysis.calculate_sleep_stage_analytics(sessions)
        self.assertTrue(res["has_data"])
        self.assertEqual(res["total_sleep_hours"], 8.0)
        self.assertEqual(res["efficiency_percent"], 100.0)
        self.assertFalse(res["is_staged"])
        self.assertEqual(res["deep_sleep_percent"], 0.0)

    def test_timestamp_fallback_calculation(self):
        """Verify duration calculation from start_time and end_time if duration_minutes missing."""
        sessions = [
            {
                "session_type": "sleep",
                "start_time": "2026-08-21T00:00:00Z",
                "end_time": "2026-08-21T07:30:00Z",
                "duration_minutes": None
            }
        ]
        res = circadian_analysis.calculate_sleep_stage_analytics(sessions)
        self.assertTrue(res["has_data"])
        self.assertEqual(res["total_sleep_hours"], 7.5)


class TestCircadianPhaseAndChronotype(unittest.TestCase):
    """Tests for Feature 11: Circadian Phase, Midpoint (MSF), and Chronotype."""

    def test_intermediate_chronotype_cross_midnight(self):
        """Standard 23:00 to 07:00 sleep produces ~03:00 AM midpoint (Intermediate)."""
        sessions = [
            {
                "start_time": "2026-08-21T03:00:00Z",  # 23:00 EDT (UTC-4)
                "end_time": "2026-08-21T11:00:00Z",    # 07:00 EDT
                "duration_minutes": 480.0
            }
        ]
        res = circadian_analysis.calculate_circadian_phase(sessions, timezone_str="America/New_York")
        self.assertTrue(res["has_data"])
        self.assertEqual(res["sleep_start"], "11:00 PM")
        self.assertEqual(res["sleep_end"], "07:00 AM")
        self.assertEqual(res["sleep_midpoint"], "03:00 AM")
        self.assertEqual(res["sleep_midpoint_decimal"], 3.0)
        self.assertIn("Intermediate", res["chronotype"])

    def test_early_chronotype_morning_lark(self):
        """Early sleep 20:30 to 04:30 EDT produces 00:30 AM midpoint (Early / Lark)."""
        sessions = [
            {
                "start_time": "2026-08-21T00:30:00Z",  # 20:30 EDT
                "end_time": "2026-08-21T08:30:00Z",    # 04:30 EDT
                "duration_minutes": 480.0
            }
        ]
        res = circadian_analysis.calculate_circadian_phase(sessions, timezone_str="America/New_York")
        self.assertTrue(res["has_data"])
        self.assertEqual(res["sleep_midpoint"], "12:30 AM")
        self.assertIn("Early", res["chronotype"])

    def test_late_chronotype_night_owl(self):
        """Late sleep 02:00 to 10:00 EDT produces 06:00 AM midpoint (Late / Night Owl)."""
        sessions = [
            {
                "start_time": "2026-08-21T06:00:00Z",  # 02:00 EDT
                "end_time": "2026-08-21T14:00:00Z",    # 10:00 EDT
                "duration_minutes": 480.0
            }
        ]
        res = circadian_analysis.calculate_circadian_phase(sessions, timezone_str="America/New_York")
        self.assertTrue(res["has_data"])
        self.assertEqual(res["sleep_midpoint"], "06:00 AM")
        self.assertIn("Late", res["chronotype"])

    def test_empty_circadian_sessions(self):
        res = circadian_analysis.calculate_circadian_phase([])
        self.assertFalse(res["has_data"])
        self.assertEqual(res["chronotype"], "Unknown")


class TestNocturnalRHRMetrics(unittest.TestCase):
    """Tests for Feature 11: Nocturnal RHR, Dipping %, and Nadir Trajectory."""

    def test_normal_dipper_detection(self):
        """Daytime 70 bpm, Night 58 bpm -> ~17.1% dipping (Normal Dipper)."""
        hr_points = [
            {"timestamp": "2026-08-21T14:00:00Z", "value": 70.0},
            {"timestamp": "2026-08-21T16:00:00Z", "value": 72.0},
            {"timestamp": "2026-08-21T18:00:00Z", "value": 68.0},
            # Nocturnal sleep window (04:00 to 11:00 UTC)
            {"timestamp": "2026-08-21T05:00:00Z", "value": 60.0},
            {"timestamp": "2026-08-21T07:00:00Z", "value": 54.0}, # Nadir
            {"timestamp": "2026-08-21T09:00:00Z", "value": 58.0}
        ]
        sleep_session = [{"start_time": "2026-08-21T04:00:00Z", "end_time": "2026-08-21T11:00:00Z"}]
        res = circadian_analysis.calculate_nocturnal_rhr_metrics(hr_points, sleep_sessions=sleep_session, timezone_str="UTC")
        self.assertTrue(res["has_hr_data"])
        self.assertEqual(res["daytime_baseline_rhr"], 70.0)
        self.assertEqual(res["nocturnal_baseline_rhr"], 57.3)
        self.assertEqual(res["nadir_bpm"], 54.0)
        self.assertEqual(res["dipper_category"], "Normal Dipper")
        self.assertGreaterEqual(res["dipping_percent"], 10.0)

    def test_non_dipper_detection(self):
        """Daytime 70 bpm, Night 67 bpm -> 4.3% dip (Non-Dipper)."""
        hr_points = [
            {"timestamp": "2026-08-21T14:00:00Z", "value": 70.0},
            {"timestamp": "2026-08-21T06:00:00Z", "value": 67.0}
        ]
        sleep_session = [{"start_time": "2026-08-21T04:00:00Z", "end_time": "2026-08-21T11:00:00Z"}]
        res = circadian_analysis.calculate_nocturnal_rhr_metrics(hr_points, sleep_sessions=sleep_session, timezone_str="UTC")
        self.assertEqual(res["dipper_category"], "Non-Dipper")

    def test_reverse_dipper_riser(self):
        """Daytime 65 bpm, Night 72 bpm -> Negative dip (Reverse Dipper / Riser)."""
        hr_points = [
            {"timestamp": "2026-08-21T14:00:00Z", "value": 65.0},
            {"timestamp": "2026-08-21T06:00:00Z", "value": 72.0}
        ]
        sleep_session = [{"start_time": "2026-08-21T04:00:00Z", "end_time": "2026-08-21T11:00:00Z"}]
        res = circadian_analysis.calculate_nocturnal_rhr_metrics(hr_points, sleep_sessions=sleep_session, timezone_str="UTC")
        self.assertIn("Reverse Dipper", res["dipper_category"])

    def test_extreme_dipper(self):
        """Daytime 80 bpm, Night 55 bpm -> 31.25% dip (Extreme Dipper)."""
        hr_points = [
            {"timestamp": "2026-08-21T14:00:00Z", "value": 80.0},
            {"timestamp": "2026-08-21T06:00:00Z", "value": 55.0}
        ]
        sleep_session = [{"start_time": "2026-08-21T04:00:00Z", "end_time": "2026-08-21T11:00:00Z"}]
        res = circadian_analysis.calculate_nocturnal_rhr_metrics(hr_points, sleep_sessions=sleep_session, timezone_str="UTC")
        self.assertEqual(res["dipper_category"], "Extreme Dipper")

    def test_empty_hr_metrics(self):
        res = circadian_analysis.calculate_nocturnal_rhr_metrics([])
        self.assertFalse(res["has_hr_data"])
        self.assertIsNone(res["nadir_bpm"])
        self.assertEqual(res["dipper_category"], "Unknown")


class TestDynamicISFModifier(unittest.TestCase):
    """Tests for Feature 12: Dynamic ISF Resistance Modifier."""

    def test_optimal_sleep_multiplier(self):
        """8.0h sleep with normal architecture -> exactly 1.00x modifier."""
        res = circadian_analysis.calculate_dynamic_isf_modifier(
            total_sleep_hours=8.0,
            deep_sleep_pct=22.0,
            rem_sleep_pct=24.0,
            rhr_dipping_pct=15.0
        )
        self.assertEqual(res["isf_modifier"], 1.00)
        self.assertEqual(res["debt_penalty"], 0.0)
        self.assertEqual(res["quality_rating"], "Optimal")

    def test_moderate_sleep_multiplier(self):
        """6.0h sleep -> 1.05x modifier."""
        res = circadian_analysis.calculate_dynamic_isf_modifier(
            total_sleep_hours=6.0,
            deep_sleep_pct=18.0,
            rem_sleep_pct=20.0,
            rhr_dipping_pct=12.0
        )
        self.assertEqual(res["isf_modifier"], 1.05)
        self.assertEqual(res["quality_rating"], "Moderate")

    def test_deficit_sleep_multiplier(self):
        """3.5h sleep -> >= 1.12x modifier."""
        res = circadian_analysis.calculate_dynamic_isf_modifier(
            total_sleep_hours=3.5
        )
        self.assertGreaterEqual(res["isf_modifier"], 1.12)
        self.assertEqual(res["quality_rating"], "Deficit")

    def test_zero_sleep_total_deprivation(self):
        """0.0h sleep -> >= 1.15x modifier."""
        res = circadian_analysis.calculate_dynamic_isf_modifier(
            total_sleep_hours=0.0
        )
        self.assertGreaterEqual(res["isf_modifier"], 1.15)
        self.assertEqual(res["quality_rating"], "Deficit")

    def test_additive_penalties_and_safety_clamping(self):
        """Severe deprivation (0h) + zero deep sleep + reverse dipping strictly clamped to 1.25x."""
        res = circadian_analysis.calculate_dynamic_isf_modifier(
            total_sleep_hours=0.0,
            deep_sleep_pct=0.0,
            rem_sleep_pct=0.0,
            rhr_dipping_pct=-15.0
        )
        self.assertEqual(res["isf_modifier"], 1.25)
        self.assertLessEqual(res["isf_modifier"], 1.25)
        self.assertGreaterEqual(res["isf_modifier"], 1.00)

    def test_extreme_high_sleep_clamping(self):
        """24.0h extreme sleep does not drop below 1.00x baseline."""
        res = circadian_analysis.calculate_dynamic_isf_modifier(
            total_sleep_hours=24.0
        )
        self.assertEqual(res["isf_modifier"], 1.00)

    def test_monotonicity_property(self):
        """As sleep duration decreases, modifier must be monotonically non-decreasing."""
        durations = [9.0, 8.0, 7.5, 7.0, 6.5, 6.0, 5.5, 5.0, 4.0, 3.0, 2.0, 1.0, 0.0]
        modifiers = [circadian_analysis.calculate_dynamic_isf_modifier(total_sleep_hours=d)["isf_modifier"] for d in durations]
        for i in range(len(modifiers) - 1):
            self.assertLessEqual(modifiers[i], modifiers[i + 1], f"Monotonicity violation between {durations[i]}h and {durations[i+1]}h")


class TestDbSleepSummaryIntegration(unittest.TestCase):
    """Tests for db.get_recent_sleep_summary integration with circadian analysis."""

    @patch("db.get_health_sessions")
    @patch("db.get_health_metrics")
    def test_db_summary_with_mock_data(self, mock_metrics, mock_sessions):
        mock_sessions.return_value = [
            {
                "id": 1,
                "session_id": "test_sess_1",
                "start_time": datetime.now(timezone.utc) - timedelta(hours=8),
                "end_time": datetime.now(timezone.utc) - timedelta(hours=1),
                "session_type": "sleep.deep",
                "duration_minutes": 120.0
            },
            {
                "id": 2,
                "session_id": "test_sess_2",
                "start_time": datetime.now(timezone.utc) - timedelta(hours=8),
                "end_time": datetime.now(timezone.utc) - timedelta(hours=1),
                "session_type": "sleep.rem",
                "duration_minutes": 120.0
            },
            {
                "id": 3,
                "session_id": "test_sess_3",
                "start_time": datetime.now(timezone.utc) - timedelta(hours=8),
                "end_time": datetime.now(timezone.utc) - timedelta(hours=1),
                "session_type": "sleep.light",
                "duration_minutes": 200.0
            }
        ]
        mock_metrics.return_value = [
            {"timestamp": datetime.now(timezone.utc) - timedelta(hours=12), "value": 72.0},
            {"timestamp": datetime.now(timezone.utc) - timedelta(hours=4), "value": 56.0}
        ]

        summary = db.get_recent_sleep_summary(hours=48)
        self.assertTrue(summary["has_data"])
        self.assertEqual(summary["total_sleep_hours_24h"], 7.33)
        self.assertEqual(summary["isf_modifier"], 1.00)
        self.assertEqual(summary["sleep_quality_rating"], "Optimal")
        self.assertIn("deep_sleep_pct", summary)
        self.assertIn("rhr_dipping_pct", summary)
        self.assertIn("chronotype", summary)
        self.assertIn("latest_session", summary)

    @patch("db.get_health_sessions")
    @patch("db.get_health_metrics")
    def test_db_summary_empty(self, mock_metrics, mock_sessions):
        mock_sessions.return_value = []
        mock_metrics.return_value = []
        summary = db.get_recent_sleep_summary(hours=48)
        self.assertFalse(summary["has_data"])
        self.assertEqual(summary["total_sleep_hours_24h"], 0.0)
        self.assertEqual(summary["isf_modifier"], 1.0)


class TestBiometricsBotHandlers(unittest.TestCase):
    """Tests for biometrics_bot.py commands, callbacks, debouncing, and group noise filtering."""

    @patch("bot_client.TelegramBotClient.send_message")
    def test_bio_command_handler(self, mock_send):
        update = {
            "update_id": 9001,
            "message": {
                "chat": {"id": 12345, "type": "private"},
                "from": {"id": 100, "first_name": "Alice"},
                "text": "/bio"
            }
        }
        res = biometrics_bot.handle_biometrics_webhook(update)
        self.assertEqual(res["status"], "ok")
        self.assertIn(res["action"], ["bio_command_response", "biometrics_card_sent"])
        self.assertIn("metrics", res)
        mock_send.assert_called_once()

    @patch("bot_client.TelegramBotClient.send_message")
    def test_sleep_command_handler(self, mock_send):
        update = {
            "update_id": 9002,
            "message": {
                "chat": {"id": 12345, "type": "private"},
                "text": "/sleep"
            }
        }
        res = biometrics_bot.handle_biometrics_webhook(update)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["action"], "sleep_card_sent")

    @patch("bot_client.TelegramBotClient.send_message")
    def test_rhr_command_handler(self, mock_send):
        update = {
            "update_id": 9003,
            "message": {
                "chat": {"id": 12345, "type": "private"},
                "text": "/rhr"
            }
        }
        res = biometrics_bot.handle_biometrics_webhook(update)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["action"], "rhr_card_sent")

    @patch("bot_client.TelegramBotClient.send_message")
    def test_isf_command_handler(self, mock_send):
        update = {
            "update_id": 9004,
            "message": {
                "chat": {"id": 12345, "type": "private"},
                "text": "/isf"
            }
        }
        res = biometrics_bot.handle_biometrics_webhook(update)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["action"], "isf_card_sent")

    @patch("bot_client.TelegramBotClient.edit_message_text")
    @patch("bot_client.TelegramBotClient.answer_callback_query")
    def test_callback_sleep_detail(self, mock_answer, mock_edit):
        update = {
            "update_id": 9005,
            "callback_query": {
                "id": "cb_unique_001",
                "data": "bio:sleep:detail",
                "message": {"chat": {"id": 12345}, "message_id": 77},
                "from": {"first_name": "Alice"}
            }
        }
        res = biometrics_bot.handle_biometrics_webhook(update)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["action"], "sleep_detail_shown")
        mock_edit.assert_called_once()
        mock_answer.assert_called_once()

    @patch("bot_client.TelegramBotClient.answer_callback_query")
    def test_callback_debouncing(self, mock_answer):
        update = {
            "update_id": 9006,
            "callback_query": {
                "id": "cb_debounce_test_123",
                "data": "bio:rhr:detail",
                "message": {"chat": {"id": 12345}, "message_id": 78},
                "from": {"first_name": "Bob"}
            }
        }
        # First call succeeds
        res1 = biometrics_bot.handle_biometrics_webhook(update)
        self.assertEqual(res1["status"], "ok")
        self.assertEqual(res1["action"], "rhr_detail_shown")

        # Immediate duplicate call is debounced
        res2 = biometrics_bot.handle_biometrics_webhook(update)
        self.assertEqual(res2["status"], "ok")
        self.assertEqual(res2["action"], "debounced")

    def test_foreign_namespace_ignored(self):
        update = {
            "update_id": 9007,
            "callback_query": {
                "id": "cb_foreign_1",
                "data": "med:log:1:10.0",
                "message": {"chat": {"id": 12345}, "message_id": 79}
            }
        }
        res = biometrics_bot.handle_biometrics_webhook(update)
        self.assertEqual(res["status"], "ignored")
        self.assertEqual(res["action"], "foreign_namespace_ignored")

    def test_group_noise_filtered(self):
        update = {
            "update_id": 9008,
            "message": {
                "chat": {"id": -100999, "type": "group"},
                "text": "Hey what did everyone eat for lunch today?"
            }
        }
        res = biometrics_bot.handle_biometrics_webhook(update)
        self.assertEqual(res["status"], "ignored")
        self.assertEqual(res["action"], "group_noise_ignored")

    def test_cross_bot_command_targeting_ignored(self):
        update = {
            "update_id": 9009,
            "message": {
                "chat": {"id": -100999, "type": "supergroup"},
                "text": "/addpreset@medflowassist_bot Metformin 500 mg"
            }
        }
        res = biometrics_bot.handle_biometrics_webhook(update)
        self.assertEqual(res["status"], "ignored")
        self.assertEqual(res["action"], "command_for_other_bot")


if __name__ == "__main__":
    unittest.main()
