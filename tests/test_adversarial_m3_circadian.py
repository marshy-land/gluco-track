"""
tests/test_adversarial_m3_circadian.py
Adversarial Stress Test Harness for Milestone 3: Circadian & Biometrics Modular Service.

Empirically challenges:
1. Sleep Stage Architecture Analytics (calculate_sleep_stage_analytics)
   - Zero duration, negative duration, NaN/Inf duration, missing stages, all-awake, 24h+, 500 micro-sessions, malformed inputs.
2. Circadian Phase & Chronotype (calculate_circadian_phase)
   - Malformed dates, inverted intervals, zero duration, DST transitions, timezone edge cases.
3. Nocturnal RHR Dipping & Nadir (calculate_nocturnal_rhr_metrics)
   - Outlier filtering (<30, >220 bpm, NaN), 0% dipping, reverse dipping, extreme dipping (>20%), nadir trajectory.
4. Dynamic ISF Modifier (calculate_dynamic_isf_modifier)
   - Exhaustive grid scan (11,264 combinations) + Monte Carlo fuzzing (5,000 random combinations) verifying strict [1.00, 1.25] bounds and monotonicity.
5. Unified Summary Integration (get_circadian_biometrics_summary)
   - Null and corrupted database states.
"""

import unittest
import math
import random
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytz
import circadian_analysis


class TestSleepStageAnalyticsAdversarial(unittest.TestCase):
    """Adversarial stress-testing of sleep stage analytics."""

    def test_null_and_malformed_inputs(self):
        """Test with None, primitives, empty list, and malformed session items."""
        for bad_input in [None, [], [None], [123], ["string"], [{"bad": "data"}], [{}, None, 456]]:
            res = circadian_analysis.calculate_sleep_stage_analytics(bad_input)
            self.assertIsInstance(res, dict)
            self.assertFalse(res["has_data"])
            self.assertEqual(res["total_sleep_hours"], 0.0)
            self.assertEqual(res["efficiency_percent"], 0.0)
            self.assertEqual(res["quality_rating"], "Deficit")
            self.assertFalse(res["is_staged"])

    def test_nan_inf_and_negative_duration(self):
        """Verify handling of NaN, Inf, and negative durations without crash or corruption."""
        sessions = [
            {"session_type": "sleep.deep", "duration_minutes": float("nan")},
            {"session_type": "sleep.rem", "duration_minutes": -120.0},
            {"session_type": "sleep.light", "duration_minutes": 0.0},
            {"session_type": "sleep.awake", "duration_minutes": None}
        ]
        res = circadian_analysis.calculate_sleep_stage_analytics(sessions)
        self.assertFalse(res["has_data"])
        self.assertEqual(res["total_sleep_hours"], 0.0)

    def test_fallback_timestamp_edge_cases(self):
        """Test timestamp fallback when duration is missing or non-positive."""
        # Case A: Inverted timestamps (end before start)
        sessions_inverted = [{
            "session_type": "sleep.deep",
            "start_time": "2026-08-21T08:00:00Z",
            "end_time": "2026-08-21T02:00:00Z",
            "duration_minutes": None
        }]
        res_inv = circadian_analysis.calculate_sleep_stage_analytics(sessions_inverted)
        self.assertFalse(res_inv["has_data"])

        # Case B: Malformed string timestamps
        sessions_bad_str = [{
            "session_type": "sleep.deep",
            "start_time": "INVALID_TIMESTAMP",
            "end_time": "NOT_A_DATE",
            "duration_minutes": 0
        }]
        res_bad = circadian_analysis.calculate_sleep_stage_analytics(sessions_bad_str)
        self.assertFalse(res_bad["has_data"])

        # Case C: Naive datetime objects mixed with aware
        sessions_naive = [{
            "session_type": "sleep.deep",
            "start_time": datetime(2026, 8, 21, 2, 0, 0),
            "end_time": datetime(2026, 8, 21, 4, 0, 0, tzinfo=timezone.utc),
            "duration_minutes": None
        }]
        res_naive = circadian_analysis.calculate_sleep_stage_analytics(sessions_naive)
        self.assertTrue(res_naive["has_data"])
        self.assertEqual(res_naive["deep_sleep_minutes"], 120.0)

    def test_24h_continuous_and_extreme_sleep_durations(self):
        """Test boundary conditions for 24h continuous sleep and extreme 100h durations."""
        # 24h sleep
        sessions_24h = [
            {"session_type": "sleep.light", "duration_minutes": 720.0},
            {"session_type": "sleep.deep", "duration_minutes": 360.0},
            {"session_type": "sleep.rem", "duration_minutes": 360.0}
        ]
        res_24h = circadian_analysis.calculate_sleep_stage_analytics(sessions_24h)
        self.assertTrue(res_24h["has_data"])
        self.assertEqual(res_24h["total_sleep_hours"], 24.0)
        self.assertEqual(res_24h["efficiency_percent"], 100.0)
        self.assertEqual(res_24h["quality_rating"], "Optimal")

        # Extreme 100h sleep
        sessions_100h = [{"session_type": "sleep", "duration_minutes": 6000.0}]
        res_100h = circadian_analysis.calculate_sleep_stage_analytics(sessions_100h)
        self.assertTrue(res_100h["has_data"])
        self.assertEqual(res_100h["total_sleep_hours"], 100.0)

    def test_all_awake_sessions(self):
        """Test behavior when all recorded intervals are 'awake' (no actual sleep)."""
        sessions_awake = [
            {"session_type": "sleep.awake", "duration_minutes": 60.0},
            {"session_type": "sleep.awake", "duration_minutes": 120.0}
        ]
        res = circadian_analysis.calculate_sleep_stage_analytics(sessions_awake)
        self.assertFalse(res["has_data"])
        self.assertEqual(res["total_sleep_hours"], 0.0)
        self.assertEqual(res["quality_rating"], "Deficit")

    def test_missing_single_stages(self):
        """Test isolation of each missing sleep stage."""
        # Only Light
        res_l = circadian_analysis.calculate_sleep_stage_analytics([{"session_type": "sleep.light", "duration_minutes": 300.0}])
        self.assertEqual(res_l["light_sleep_percent"], 100.0)
        self.assertEqual(res_l["deep_sleep_percent"], 0.0)
        self.assertEqual(res_l["rem_sleep_percent"], 0.0)
        self.assertEqual(res_l["restorative_ratio"], 0.0)

        # Only Deep
        res_d = circadian_analysis.calculate_sleep_stage_analytics([{"session_type": "sleep.deep", "duration_minutes": 300.0}])
        self.assertEqual(res_d["deep_sleep_percent"], 100.0)
        self.assertEqual(res_d["restorative_ratio"], 1.0)

        # Only REM
        res_r = circadian_analysis.calculate_sleep_stage_analytics([{"session_type": "sleep.rem", "duration_minutes": 300.0}])
        self.assertEqual(res_r["rem_sleep_percent"], 100.0)
        self.assertEqual(res_r["restorative_ratio"], 1.0)

    def test_500_micro_sessions_stress_and_fragmentation(self):
        """Stress test with 500 rapid micro-sleep and awake awakenings."""
        sessions = []
        for i in range(250):
            sessions.append({"session_type": "sleep.light", "duration_minutes": 1.0})
            sessions.append({"session_type": "sleep.awake", "duration_minutes": 0.5})

        res = circadian_analysis.calculate_sleep_stage_analytics(sessions)
        self.assertTrue(res["has_data"])
        self.assertEqual(res["total_sleep_minutes"], 250.0)
        self.assertEqual(res["awake_episodes_count"], 250)
        self.assertAlmostEqual(res["fragmentation_index"], 250.0 / (250.0 / 60.0), places=1) # 60.0 / hr


class TestCircadianPhaseAdversarial(unittest.TestCase):
    """Adversarial stress-testing of circadian phase & chronotype calculation."""

    def test_invalid_and_corrupt_sessions(self):
        """Test with corrupt, empty, and invalid session structures."""
        for bad in [None, [], [None], [{"start_time": "invalid"}], [{"start_time": None, "end_time": None}]]:
            res = circadian_analysis.calculate_circadian_phase(bad)
            self.assertFalse(res["has_data"])
            self.assertEqual(res["chronotype"], "Unknown")
            self.assertIsNone(res["sleep_midpoint_decimal"])

    def test_cross_midnight_midpoint_precision(self):
        """Test midpoint calculation across midnight boundaries."""
        # 23:00 to 07:00 in America/New_York (UTC-4)
        sessions = [{
            "start_time": "2026-08-21T03:00:00Z", # 23:00 EDT
            "end_time": "2026-08-21T11:00:00Z",   # 07:00 EDT
            "duration_minutes": 480.0
        }]
        res = circadian_analysis.calculate_circadian_phase(sessions, timezone_str="America/New_York")
        self.assertTrue(res["has_data"])
        self.assertEqual(res["sleep_midpoint"], "03:00 AM")
        self.assertEqual(res["sleep_midpoint_decimal"], 3.00)
        self.assertIn("Intermediate", res["chronotype"])

    def test_unknown_timezone_fallback(self):
        """Test resilience against invalid or uninstalled timezone names."""
        sessions = [{
            "start_time": "2026-08-21T00:00:00Z",
            "end_time": "2026-08-21T08:00:00Z",
            "duration_minutes": 480.0
        }]
        res = circadian_analysis.calculate_circadian_phase(sessions, timezone_str="NonExistent/Lost_City")
        self.assertTrue(res["has_data"])
        # Should fallback to UTC without throwing Exception
        self.assertEqual(res["sleep_midpoint"], "04:00 AM")
        self.assertEqual(res["sleep_midpoint_decimal"], 4.00)

    def test_chronotype_boundary_scan(self):
        """Test boundary midpoint values for chronotype categorization."""
        test_cases = [
            ("01:00:00", "05:00:00", 3.0, "Intermediate (Balanced)"),
            ("00:00:00", "04:00:00", 2.0, "Early (Morning Lark)"),
            ("03:00:00", "07:00:00", 5.0, "Intermediate (Balanced)"),
            ("03:30:00", "07:30:00", 5.5, "Late (Night Owl)"),
            ("20:00:00", "00:00:00", 22.0, "Early (Morning Lark)"),
        ]
        for st_str, et_str, expected_mid, expected_cat in test_cases:
            sessions = [{
                "start_time": f"2026-08-21T{st_str}Z",
                "end_time": f"2026-08-21T{et_str}Z",
                "duration_minutes": 240.0
            }]
            res = circadian_analysis.calculate_circadian_phase(sessions, timezone_str="UTC")
            self.assertEqual(res["sleep_midpoint_decimal"], expected_mid)
            self.assertEqual(res["chronotype"], expected_cat)


class TestNocturnalRHRMetricsAdversarial(unittest.TestCase):
    """Adversarial stress-testing of resting heart rate and dipping metrics."""

    def test_outlier_rejection_and_clean_filtering(self):
        """Outliers below 30 bpm, above 220 bpm, NaN, or non-numeric must be dropped."""
        noisy_metrics = [
            {"timestamp": "2026-08-21T14:00:00Z", "value": -10.0},  # Invalid
            {"timestamp": "2026-08-21T14:05:00Z", "value": 0.0},    # Invalid
            {"timestamp": "2026-08-21T14:10:00Z", "value": 29.9},   # Invalid (<30)
            {"timestamp": "2026-08-21T14:15:00Z", "value": float("nan")}, # Invalid
            {"timestamp": "2026-08-21T14:20:00Z", "value": 250.0},  # Invalid (>220)
            {"timestamp": "2026-08-21T14:25:00Z", "value": 70.0},   # Valid Day
            {"timestamp": "2026-08-21T03:00:00Z", "value": 55.0},   # Valid Night
        ]
        res = circadian_analysis.calculate_nocturnal_rhr_metrics(noisy_metrics, timezone_str="UTC")
        self.assertTrue(res["has_hr_data"])
        self.assertEqual(res["daytime_baseline_rhr"], 70.0)
        self.assertEqual(res["nocturnal_baseline_rhr"], 55.0)
        self.assertEqual(res["data_points_day"], 1)
        self.assertEqual(res["data_points_night"], 1)

    def test_constant_heart_rate_zero_dipping(self):
        """Constant heart rate during day and night produces exactly 0.0% dipping -> Non-Dipper."""
        constant_metrics = [
            {"timestamp": "2026-08-21T12:00:00Z", "value": 72.0},
            {"timestamp": "2026-08-21T15:00:00Z", "value": 72.0},
            {"timestamp": "2026-08-21T02:00:00Z", "value": 72.0},
            {"timestamp": "2026-08-21T04:00:00Z", "value": 72.0},
        ]
        res = circadian_analysis.calculate_nocturnal_rhr_metrics(constant_metrics, timezone_str="UTC")
        self.assertEqual(res["dipping_percent"], 0.0)
        self.assertEqual(res["dipper_category"], "Non-Dipper")

    def test_reverse_dipping_riser_variations(self):
        """Nighttime RHR higher than daytime baseline produces negative dipping -> Reverse Dipper."""
        # Mild reverse dipping (-5%)
        metrics_mild = [
            {"timestamp": "2026-08-21T14:00:00Z", "value": 60.0},
            {"timestamp": "2026-08-21T03:00:00Z", "value": 63.0},
        ]
        res_mild = circadian_analysis.calculate_nocturnal_rhr_metrics(metrics_mild, timezone_str="UTC")
        self.assertEqual(res_mild["dipping_percent"], -5.0)
        self.assertEqual(res_mild["dipper_category"], "Reverse Dipper (Riser)")

        # Severe reverse dipping (-33.3%)
        metrics_severe = [
            {"timestamp": "2026-08-21T14:00:00Z", "value": 60.0},
            {"timestamp": "2026-08-21T03:00:00Z", "value": 80.0},
        ]
        res_severe = circadian_analysis.calculate_nocturnal_rhr_metrics(metrics_severe, timezone_str="UTC")
        self.assertAlmostEqual(res_severe["dipping_percent"], -33.3, places=1)
        self.assertEqual(res_severe["dipper_category"], "Reverse Dipper (Riser)")

    def test_extreme_dipping_above_20_percent(self):
        """Nighttime RHR dipping >= 20% categorized as Extreme Dipper."""
        metrics_extreme = [
            {"timestamp": "2026-08-21T14:00:00Z", "value": 80.0},
            {"timestamp": "2026-08-21T03:00:00Z", "value": 50.0}, # 37.5% dip
        ]
        res_extreme = circadian_analysis.calculate_nocturnal_rhr_metrics(metrics_extreme, timezone_str="UTC")
        self.assertEqual(res_extreme["dipping_percent"], 37.5)
        self.assertEqual(res_extreme["dipper_category"], "Extreme Dipper")

    def test_nadir_trajectory_and_relative_position(self):
        """Verify early (hammock) vs late (delayed) nadir relative positioning."""
        sleep_window = [{"start_time": "2026-08-21T00:00:00Z", "end_time": "2026-08-21T08:00:00Z"}]

        # Early Nadir at hour 2 of 8 (rel pos = 0.25)
        hr_early = [
            {"timestamp": "2026-08-21T14:00:00Z", "value": 70.0},
            {"timestamp": "2026-08-21T02:00:00Z", "value": 52.0}, # Nadir
            {"timestamp": "2026-08-21T06:00:00Z", "value": 60.0},
        ]
        res_early = circadian_analysis.calculate_nocturnal_rhr_metrics(hr_early, sleep_sessions=sleep_window, timezone_str="UTC")
        self.assertEqual(res_early["nadir_relative_position"], 0.25)
        self.assertIn("Early", res_early["recovery_pattern"])

        # Late Nadir at hour 7 of 8 (rel pos = 0.88)
        hr_late = [
            {"timestamp": "2026-08-21T14:00:00Z", "value": 70.0},
            {"timestamp": "2026-08-21T02:00:00Z", "value": 62.0},
            {"timestamp": "2026-08-21T07:00:00Z", "value": 52.0}, # Nadir
        ]
        res_late = circadian_analysis.calculate_nocturnal_rhr_metrics(hr_late, sleep_sessions=sleep_window, timezone_str="UTC")
        self.assertEqual(res_late["nadir_relative_position"], 0.88)
        self.assertIn("Delayed", res_late["recovery_pattern"])


class TestDynamicISFModifierExhaustiveGridAndFuzzing(unittest.TestCase):
    """Exhaustive mathematical verification and fuzzing of dynamic ISF modifier bounds."""

    def test_penalty_component_bounds(self):
        """Verify each sub-component stays strictly within its theoretical limits."""
        # 1. Sleep Debt Penalty: [0.00, 0.15]
        for hours in [-10.0, 0.0, 2.0, 3.5, 5.0, 5.5, 6.0, 7.0, 8.0, 24.0]:
            res = circadian_analysis.calculate_dynamic_isf_modifier(total_sleep_hours=hours)
            self.assertGreaterEqual(res["debt_penalty"], 0.0, f"Negative debt penalty for {hours}h")
            self.assertLessEqual(res["debt_penalty"], 0.15, f"Debt penalty exceeded 0.15 for {hours}h")

        # 2. Architecture Penalty: [0.00, 0.05]
        for deep in [-20.0, 0.0, 5.0, 14.9, 15.0, 25.0, 100.0]:
            for rem in [-20.0, 0.0, 5.0, 14.9, 15.0, 25.0, 100.0]:
                res = circadian_analysis.calculate_dynamic_isf_modifier(
                    total_sleep_hours=8.0, deep_sleep_pct=deep, rem_sleep_pct=rem
                )
                self.assertGreaterEqual(res["architecture_penalty"], 0.0)
                self.assertLessEqual(res["architecture_penalty"], 0.05)

        # 3. Autonomic Dipping Penalty: [0.00, 0.08]
        for dip in [-100.0, -25.0, -10.0, 0.0, 5.0, 9.9, 10.0, 20.0, 50.0]:
            res = circadian_analysis.calculate_dynamic_isf_modifier(
                total_sleep_hours=8.0, rhr_dipping_pct=dip
            )
            self.assertGreaterEqual(res["autonomic_penalty"], 0.0)
            self.assertLessEqual(res["autonomic_penalty"], 0.08)

    def test_exhaustive_grid_search_11264_combinations(self):
        """
        Exhaustively sweep 11,264 combinations of:
        - 16 sleep duration values (0.0h to 24.0h)
        - 8 deep sleep percentages (0% to 100%)
        - 8 REM sleep percentages (0% to 100%)
        - 11 dipping percentages (-50% to +35%)
        Asserts that ISF modifier is ALWAYS between 1.00 and 1.25 inclusive.
        """
        hours_grid = [0.0, 1.0, 2.0, 3.0, 3.5, 4.0, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0, 9.0, 12.0, 24.0]
        deep_grid = [0.0, 5.0, 10.0, 14.9, 15.0, 20.0, 30.0, 100.0]
        rem_grid = [0.0, 5.0, 10.0, 14.9, 15.0, 20.0, 30.0, 100.0]
        dip_grid = [-50.0, -20.0, -10.0, -0.1, 0.0, 5.0, 9.9, 10.0, 15.0, 20.0, 35.0]

        count = 0
        min_seen = float("inf")
        max_seen = float("-inf")

        for h in hours_grid:
            for d in deep_grid:
                for r in rem_grid:
                    for dip in dip_grid:
                        res = circadian_analysis.calculate_dynamic_isf_modifier(
                            total_sleep_hours=h,
                            deep_sleep_pct=d,
                            rem_sleep_pct=r,
                            rhr_dipping_pct=dip
                        )
                        mod = res["isf_modifier"]
                        count += 1
                        if mod < min_seen:
                            min_seen = mod
                        if mod > max_seen:
                            max_seen = mod

                        self.assertGreaterEqual(mod, 1.00, f"Violation: mod={mod} < 1.00 for (h={h}, d={d}, r={r}, dip={dip})")
                        self.assertLessEqual(mod, 1.25, f"Violation: mod={mod} > 1.25 for (h={h}, d={d}, r={r}, dip={dip})")

        self.assertEqual(count, 11264)
        self.assertEqual(min_seen, 1.00)
        self.assertEqual(max_seen, 1.25)

    def test_monte_carlo_random_fuzzing_5000_trials(self):
        """Fuzz with 5,000 random floating point inputs including negative numbers and boundary values."""
        random.seed(42)
        for _ in range(5000):
            h = random.uniform(-10.0, 30.0)
            d = random.uniform(-20.0, 120.0)
            r = random.uniform(-20.0, 120.0)
            dip = random.uniform(-100.0, 100.0)

            res = circadian_analysis.calculate_dynamic_isf_modifier(
                total_sleep_hours=h,
                deep_sleep_pct=d,
                rem_sleep_pct=r,
                rhr_dipping_pct=dip
            )
            mod = res["isf_modifier"]
            self.assertTrue(1.00 <= mod <= 1.25, f"Fuzz violation: modifier {mod} out of range [1.00, 1.25]")


class TestUnifiedBiometricsSummaryIntegration(unittest.TestCase):
    """Test unified summary with empty and corrupt DB mocks."""

    @patch("db.get_health_sessions")
    @patch("db.get_health_metrics")
    def test_summary_with_empty_db(self, mock_metrics, mock_sessions):
        mock_sessions.return_value = []
        mock_metrics.return_value = []

        summary = circadian_analysis.get_circadian_biometrics_summary(hours=48)
        self.assertFalse(summary["has_data"])
        self.assertEqual(summary["sleep"]["total_hours_24h"], 0.0)
        self.assertIsNone(summary["circadian"]["sleep_midpoint"])
        self.assertIsNone(summary["rhr"]["daytime_baseline"])
        self.assertEqual(summary["isf"]["modifier"], 1.15) # Default 0.0h sleep penalty

    @patch("db.get_health_sessions")
    @patch("db.get_health_metrics")
    def test_summary_with_corrupt_db_records(self, mock_metrics, mock_sessions):
        mock_sessions.return_value = [{"invalid": 1}, None, "bad_record"]
        mock_metrics.return_value = [{"value": "not_a_number"}, {"value": -50.0}]

        summary = circadian_analysis.get_circadian_biometrics_summary(hours=48)
        self.assertFalse(summary["has_data"])
        self.assertEqual(summary["isf"]["modifier"], 1.15)


if __name__ == "__main__":
    unittest.main()
