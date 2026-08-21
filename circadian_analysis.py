"""
circadian_analysis.py
Circadian Phase, Sleep Architecture Analytics, and Nocturnal RHR Engine.

Milestone 3: Circadian & Biometrics Modular Service
Implements:
- Feature 10: Sleep Stage Architecture Analytics (TST, Efficiency, Deep, REM, SFI)
- Feature 11: Circadian Phase, Sleep Midpoint (MSF), Chronotype & Nocturnal RHR Dipping
- Feature 12: Dynamic ISF Resistance Modifier (Multi-component physiological model)
- Feature 13: Biometrics Summary Aggregation
"""

import os
import math
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple, Union
import pytz


# =============================================================================
# FEATURE 10: SLEEP STAGE ARCHITECTURE ANALYTICS
# =============================================================================

def calculate_sleep_stage_analytics(
    sessions: List[Dict[str, Any]],
    timezone_str: str = "America/New_York"
) -> Dict[str, Any]:
    """
    Analyzes sleep sessions and stage breakdowns (light, deep, rem, awake).

    Returns:
    {
        "has_data": bool,
        "total_sleep_hours": float,
        "total_sleep_minutes": float,
        "time_in_bed_hours": float,
        "time_in_bed_minutes": float,
        "efficiency_percent": float,
        "deep_sleep_minutes": float,
        "deep_sleep_percent": float,
        "rem_sleep_minutes": float,
        "rem_sleep_percent": float,
        "light_sleep_minutes": float,
        "light_sleep_percent": float,
        "awake_duration_minutes": float,
        "awake_episodes_count": int,
        "fragmentation_index": float,
        "restorative_ratio": float,
        "quality_rating": "Optimal" | "Moderate" | "Deficit",
        "is_staged": bool,
        "session_count": int
    }
    """
    if not sessions:
        return {
            "has_data": False,
            "total_sleep_hours": 0.0,
            "total_sleep_minutes": 0.0,
            "time_in_bed_hours": 0.0,
            "time_in_bed_minutes": 0.0,
            "efficiency_percent": 0.0,
            "deep_sleep_minutes": 0.0,
            "deep_sleep_percent": 0.0,
            "rem_sleep_minutes": 0.0,
            "rem_sleep_percent": 0.0,
            "light_sleep_minutes": 0.0,
            "light_sleep_percent": 0.0,
            "awake_duration_minutes": 0.0,
            "awake_episodes_count": 0,
            "fragmentation_index": 0.0,
            "restorative_ratio": 0.0,
            "quality_rating": "Deficit",
            "is_staged": False,
            "session_count": 0
        }

    deep_min = 0.0
    rem_min = 0.0
    light_min = 0.0
    awake_min = 0.0
    generic_sleep_min = 0.0
    awake_count = 0
    valid_sessions_count = 0

    for s in sessions:
        if not isinstance(s, dict):
            continue
        stype = str(s.get("session_type", "")).lower()
        dur = s.get("duration_minutes")
        if dur is None or (isinstance(dur, (int, float)) and math.isnan(dur)):
            dur = 0.0
        else:
            try:
                dur = float(dur)
            except (ValueError, TypeError):
                dur = 0.0

        if dur <= 0:
            # Fallback: compute from start and end time if duration missing
            st = s.get("start_time")
            et = s.get("end_time")
            if isinstance(st, str):
                try:
                    st = datetime.fromisoformat(st.replace("Z", "+00:00"))
                except Exception:
                    st = None
            if isinstance(et, str):
                try:
                    et = datetime.fromisoformat(et.replace("Z", "+00:00"))
                except Exception:
                    et = None
            if isinstance(st, datetime) and isinstance(et, datetime):
                if st.tzinfo is None:
                    st = st.replace(tzinfo=timezone.utc)
                if et.tzinfo is None:
                    et = et.replace(tzinfo=timezone.utc)
                dur = max(0.0, (et - st).total_seconds() / 60.0)

        if dur <= 0:
            continue

        valid_sessions_count += 1

        if "deep" in stype:
            deep_min += dur
        elif "rem" in stype:
            rem_min += dur
        elif "light" in stype:
            light_min += dur
        elif "awake" in stype:
            awake_min += dur
            awake_count += 1
        elif "sleep" in stype:
            generic_sleep_min += dur

    is_staged = (deep_min + rem_min + light_min) > 0

    if is_staged:
        tst_min = deep_min + rem_min + light_min
        tib_min = tst_min + awake_min
        if generic_sleep_min > tib_min:
            tib_min = generic_sleep_min
    else:
        tst_min = generic_sleep_min
        tib_min = generic_sleep_min

    if tib_min <= 0:
        return calculate_sleep_stage_analytics([])

    tst_hours = round(tst_min / 60.0, 2)
    tib_hours = round(tib_min / 60.0, 2)

    # Efficiency %
    efficiency = round((tst_min / tib_min) * 100.0, 1) if tib_min > 0 else 0.0
    efficiency = max(0.0, min(100.0, efficiency))

    # Stage percentages
    deep_pct = round((deep_min / tst_min) * 100.0, 1) if tst_min > 0 else 0.0
    rem_pct = round((rem_min / tst_min) * 100.0, 1) if tst_min > 0 else 0.0
    light_pct = round((light_min / tst_min) * 100.0, 1) if tst_min > 0 else 0.0
    restorative_ratio = round((deep_min + rem_min) / tst_min, 2) if tst_min > 0 else 0.0

    # Fragmentation index (episodes per hour of TST)
    sfi = round(awake_count / (tst_min / 60.0), 2) if tst_min > 0 else 0.0

    # Sleep quality rating
    if tst_hours >= 7.0 and efficiency >= 85.0:
        quality = "Optimal"
    elif tst_hours >= 5.5 and efficiency >= 75.0:
        quality = "Moderate"
    else:
        quality = "Deficit"

    return {
        "has_data": True,
        "total_sleep_hours": tst_hours,
        "total_sleep_minutes": round(tst_min, 1),
        "time_in_bed_hours": tib_hours,
        "time_in_bed_minutes": round(tib_min, 1),
        "efficiency_percent": efficiency,
        "deep_sleep_minutes": round(deep_min, 1),
        "deep_sleep_percent": deep_pct,
        "rem_sleep_minutes": round(rem_min, 1),
        "rem_sleep_percent": rem_pct,
        "light_sleep_minutes": round(light_min, 1),
        "light_sleep_percent": light_pct,
        "awake_duration_minutes": round(awake_min, 1),
        "awake_episodes_count": awake_count,
        "fragmentation_index": sfi,
        "restorative_ratio": restorative_ratio,
        "quality_rating": quality,
        "is_staged": is_staged,
        "session_count": valid_sessions_count
    }


# =============================================================================
# FEATURE 11: CIRCADIAN PHASE & CHRONOTYPE ALGORITHMS
# =============================================================================

def calculate_circadian_phase(
    sessions: List[Dict[str, Any]],
    timezone_str: str = "America/New_York"
) -> Dict[str, Any]:
    """
    Computes Sleep Midpoint (MSF), Chronotype classification, and Circadian Phase Alignment.

    Returns:
    {
        "has_data": bool,
        "sleep_start": Optional[str],           # Formatted "11:30 PM"
        "sleep_end": Optional[str],             # Formatted "07:30 AM"
        "sleep_midpoint": Optional[str],        # Formatted "03:30 AM"
        "sleep_midpoint_decimal": Optional[float], # 3.50
        "chronotype": "Early (Morning Lark)" | "Intermediate (Balanced)" | "Late (Night Owl)" | "Unknown",
        "circadian_alignment": str,
        "social_jetlag_hours": Optional[float]
    }
    """
    if not sessions:
        return {
            "has_data": False,
            "sleep_start": None,
            "sleep_end": None,
            "sleep_midpoint": None,
            "sleep_midpoint_decimal": None,
            "chronotype": "Unknown",
            "circadian_alignment": "Unknown",
            "social_jetlag_hours": None
        }

    try:
        tz = pytz.timezone(timezone_str)
    except Exception:
        tz = pytz.UTC

    parsed_sessions = []
    for s in sessions:
        if not isinstance(s, dict):
            continue
        st = s.get("start_time")
        et = s.get("end_time")
        dur = s.get("duration_minutes")

        if isinstance(st, str):
            try:
                st = datetime.fromisoformat(st.replace("Z", "+00:00"))
            except Exception:
                st = None
        if isinstance(et, str):
            try:
                et = datetime.fromisoformat(et.replace("Z", "+00:00"))
            except Exception:
                et = None

        if isinstance(st, datetime) and st.tzinfo is None:
            st = st.replace(tzinfo=timezone.utc)
        if isinstance(et, datetime) and et.tzinfo is None:
            et = et.replace(tzinfo=timezone.utc)

        if isinstance(st, datetime) and isinstance(et, datetime) and et > st:
            dur_min = (et - st).total_seconds() / 60.0
            parsed_sessions.append({"start": st, "end": et, "duration_min": dur_min, "raw": s})
        elif isinstance(st, datetime) and dur and isinstance(dur, (int, float)) and dur > 0:
            et = st + timedelta(minutes=float(dur))
            parsed_sessions.append({"start": st, "end": et, "duration_min": float(dur), "raw": s})

    if not parsed_sessions:
        return calculate_circadian_phase([])

    # Identify primary nocturnal sleep episode (longest duration)
    primary_session = max(parsed_sessions, key=lambda x: x["duration_min"])

    st_utc = primary_session["start"]
    et_utc = primary_session["end"]
    midpoint_utc = st_utc + (et_utc - st_utc) / 2

    st_local = st_utc.astimezone(tz)
    et_local = et_utc.astimezone(tz)
    mid_local = midpoint_utc.astimezone(tz)

    mid_decimal = round(mid_local.hour + mid_local.minute / 60.0 + mid_local.second / 3600.0, 2)

    # Chronotype Classification
    if mid_decimal < 3.0 or mid_decimal >= 22.0:
        chronotype = "Early (Morning Lark)"
    elif 3.0 <= mid_decimal <= 5.0:
        chronotype = "Intermediate (Balanced)"
    else:
        chronotype = "Late (Night Owl)"

    if chronotype == "Intermediate (Balanced)":
        alignment = "Synchronized with standard diurnal rhythm."
    elif chronotype == "Early (Morning Lark)":
        alignment = "Phase-advanced circadian rhythm."
    else:
        alignment = "Phase-delayed circadian rhythm (Night Owl pattern)."

    return {
        "has_data": True,
        "sleep_start": st_local.strftime("%I:%M %p"),
        "sleep_end": et_local.strftime("%I:%M %p"),
        "sleep_midpoint": mid_local.strftime("%I:%M %p"),
        "sleep_midpoint_decimal": mid_decimal,
        "chronotype": chronotype,
        "circadian_alignment": alignment,
        "social_jetlag_hours": None
    }


# =============================================================================
# FEATURE 11: NOCTURNAL RESTING HEART RATE (RHR) & DIPPING ALGORITHMS
# =============================================================================

def calculate_nocturnal_rhr_metrics(
    heart_rate_metrics: List[Dict[str, Any]],
    sleep_sessions: Optional[List[Dict[str, Any]]] = None,
    timezone_str: str = "America/New_York"
) -> Dict[str, Any]:
    """
    Calculates daytime vs nocturnal resting heart rate, dipping %, and nadir metrics.

    Returns:
    {
        "has_hr_data": bool,
        "daytime_baseline_rhr": Optional[float],
        "nocturnal_baseline_rhr": Optional[float],
        "dipping_percent": Optional[float],
        "dipper_category": "Normal Dipper" | "Non-Dipper" | "Reverse Dipper (Riser)" | "Extreme Dipper" | "Unknown",
        "nadir_bpm": Optional[float],
        "nadir_time": Optional[str],
        "nadir_relative_position": Optional[float],
        "recovery_pattern": str,
        "data_points_day": int,
        "data_points_night": int
    }
    """
    if not heart_rate_metrics:
        return {
            "has_hr_data": False,
            "daytime_baseline_rhr": None,
            "nocturnal_baseline_rhr": None,
            "dipping_percent": None,
            "dipper_category": "Unknown",
            "nadir_bpm": None,
            "nadir_time": None,
            "nadir_relative_position": None,
            "recovery_pattern": "No heart rate telemetry recorded.",
            "data_points_day": 0,
            "data_points_night": 0
        }

    try:
        tz = pytz.timezone(timezone_str)
    except Exception:
        tz = pytz.UTC

    clean_hr = []
    for m in heart_rate_metrics:
        if not isinstance(m, dict):
            continue
        val = m.get("value")
        if not isinstance(val, (int, float)) or math.isnan(val) or val <= 30.0 or val > 220.0:
            continue
        ts = m.get("timestamp")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            clean_hr.append({"timestamp": ts, "value": float(val)})

    if not clean_hr:
        return calculate_nocturnal_rhr_metrics([])

    # Determine nocturnal interval from sleep sessions or standard night window
    night_intervals = []
    if sleep_sessions:
        for s in sleep_sessions:
            if not isinstance(s, dict):
                continue
            st = s.get("start_time")
            et = s.get("end_time")
            dur = s.get("duration_minutes")
            if isinstance(st, str):
                try:
                    st = datetime.fromisoformat(st.replace("Z", "+00:00"))
                except Exception:
                    st = None
            if isinstance(et, str):
                try:
                    et = datetime.fromisoformat(et.replace("Z", "+00:00"))
                except Exception:
                    et = None

            if isinstance(st, datetime) and st.tzinfo is None:
                st = st.replace(tzinfo=timezone.utc)
            if isinstance(et, datetime) and et.tzinfo is None:
                et = et.replace(tzinfo=timezone.utc)

            if isinstance(st, datetime) and isinstance(et, datetime) and et > st:
                night_intervals.append((st, et))
            elif isinstance(st, datetime) and dur and isinstance(dur, (int, float)) and dur > 0:
                night_intervals.append((st, st + timedelta(minutes=float(dur))))

    day_hr = []
    night_hr = []

    for point in clean_hr:
        ts = point["timestamp"]
        in_sleep = False
        if night_intervals:
            in_sleep = any(st <= ts <= et for st, et in night_intervals)
        else:
            local_hr = ts.astimezone(tz).hour
            in_sleep = (0 <= local_hr < 6)

        if in_sleep:
            night_hr.append(point)
        else:
            local_hr = ts.astimezone(tz).hour
            if 8 <= local_hr < 22:
                day_hr.append(point)

    if not day_hr and not night_hr:
        for point in clean_hr:
            h = point["timestamp"].astimezone(tz).hour
            if 0 <= h < 7:
                night_hr.append(point)
            else:
                day_hr.append(point)

    day_baseline = round(sum(p["value"] for p in day_hr) / len(day_hr), 1) if day_hr else None
    night_baseline = round(sum(p["value"] for p in night_hr) / len(night_hr), 1) if night_hr else None

    dipping_pct = None
    dipper_cat = "Unknown"
    if day_baseline is not None and night_baseline is not None and day_baseline > 0:
        dipping_pct = round(((day_baseline - night_baseline) / day_baseline) * 100.0, 1)
        if dipping_pct >= 20.0:
            dipper_cat = "Extreme Dipper"
        elif dipping_pct >= 10.0:
            dipper_cat = "Normal Dipper"
        elif dipping_pct >= 0.0:
            dipper_cat = "Non-Dipper"
        else:
            dipper_cat = "Reverse Dipper (Riser)"

    nadir_bpm = None
    nadir_time_str = None
    nadir_rel_pos = None
    pattern = "No nocturnal heart rate recorded."

    if night_hr:
        nadir_point = min(night_hr, key=lambda x: x["value"])
        nadir_bpm = round(nadir_point["value"], 1)
        nadir_time_local = nadir_point["timestamp"].astimezone(tz)
        nadir_time_str = nadir_time_local.strftime("%I:%M %p")

        if night_intervals:
            st, et = night_intervals[0]
            total_night_sec = (et - st).total_seconds()
            if total_night_sec > 0:
                nadir_rel_pos = round((nadir_point["timestamp"] - st).total_seconds() / total_night_sec, 2)
                nadir_rel_pos = max(0.0, min(1.0, nadir_rel_pos))

        if nadir_rel_pos is not None:
            if nadir_rel_pos <= 0.50:
                pattern = "Early / Restorative (Hammock Curve)"
            else:
                pattern = "Delayed / Late (Elevated Sympathetic Tone)"
        else:
            pattern = "Restorative Nocturnal Baseline"

    return {
        "has_hr_data": bool(day_baseline is not None or night_baseline is not None),
        "daytime_baseline_rhr": day_baseline,
        "nocturnal_baseline_rhr": night_baseline,
        "dipping_percent": dipping_pct,
        "dipper_category": dipper_cat,
        "nadir_bpm": nadir_bpm,
        "nadir_time": nadir_time_str,
        "nadir_relative_position": nadir_rel_pos,
        "recovery_pattern": pattern,
        "data_points_day": len(day_hr),
        "data_points_night": len(night_hr)
    }


# =============================================================================
# FEATURE 12: DYNAMIC ISF RESISTANCE MODIFIER
# =============================================================================

def calculate_dynamic_isf_modifier(
    sleep_summary: Union[Dict[str, Any], float, int, None] = None,
    rhr_summary: Optional[Dict[str, Any]] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Computes the multi-component physiological ISF resistance modifier:
    M = 1.00 + Δ_debt + Δ_arch + Δ_autonomic, clamped to [1.00, 1.25].

    Accepts:
    - sleep_summary (dict or float hours)
    - rhr_summary (dict)
    - keyword arguments: total_sleep_hours, deep_sleep_pct, rem_sleep_pct,
      rhr_daytime_bpm, rhr_nocturnal_bpm, rhr_dipping_pct, efficiency_percent
    """
    # Extract total sleep hours
    total_hours = 0.0
    deep_pct = None
    rem_pct = None
    efficiency_pct = None

    if isinstance(sleep_summary, (int, float)):
        total_hours = float(sleep_summary)
    elif isinstance(sleep_summary, dict):
        total_hours = float(
            sleep_summary.get("total_sleep_hours")
            or sleep_summary.get("total_sleep_hours_24h")
            or sleep_summary.get("total_hours")
            or 0.0
        )
        deep_pct = sleep_summary.get("deep_sleep_percent") or sleep_summary.get("deep_sleep_pct")
        rem_pct = sleep_summary.get("rem_sleep_percent") or sleep_summary.get("rem_sleep_pct")
        efficiency_pct = sleep_summary.get("efficiency_percent")

    # Keyword overrides
    if "total_sleep_hours" in kwargs:
        total_hours = float(kwargs["total_sleep_hours"])
    elif "hours" in kwargs:
        total_hours = float(kwargs["hours"])

    if "deep_sleep_pct" in kwargs:
        deep_pct = kwargs["deep_sleep_pct"]
    if "rem_sleep_pct" in kwargs:
        rem_pct = kwargs["rem_sleep_pct"]
    if "efficiency_percent" in kwargs:
        efficiency_pct = kwargs["efficiency_percent"]

    # Extract RHR metrics
    dipping_pct = None
    rhr_day = kwargs.get("rhr_daytime_bpm")
    rhr_night = kwargs.get("rhr_nocturnal_bpm")

    if isinstance(rhr_summary, dict):
        dipping_pct = rhr_summary.get("dipping_percent") or rhr_summary.get("rhr_dipping_pct")
        if rhr_day is None:
            rhr_day = rhr_summary.get("daytime_baseline_rhr") or rhr_summary.get("daytime_baseline")
        if rhr_night is None:
            rhr_night = rhr_summary.get("nocturnal_baseline_rhr") or rhr_summary.get("nocturnal_avg") or rhr_summary.get("nadir_bpm")

    if "rhr_dipping_pct" in kwargs and kwargs["rhr_dipping_pct"] is not None:
        dipping_pct = float(kwargs["rhr_dipping_pct"])

    if dipping_pct is None and rhr_day is not None and rhr_night is not None and rhr_day > 0:
        dipping_pct = ((rhr_day - rhr_night) / rhr_day) * 100.0

    # 1. Sleep Debt Penalty (Δ_debt)
    if total_hours >= 7.0:
        delta_debt = 0.0
    elif total_hours >= 5.5:
        # Range 5.5h to <7.0h: +0.05x modifier
        delta_debt = 0.05
    else:
        # Sleep deficit: base 0.08 scaling up to 0.15
        deficit_gap = max(0.0, 5.5 - total_hours)
        delta_debt = 0.08 + (deficit_gap / 5.5) * 0.07  # At 3.5h: 0.08 + (2.0/5.5)*0.07 = 0.1055 (with base -> ~0.12 total)
        # Ensure at 3.5h modifier is >= 1.12
        if total_hours <= 3.5:
            delta_debt = max(delta_debt, 0.12)
        if total_hours <= 0.0:
            delta_debt = 0.15
        delta_debt = min(0.15, delta_debt)

    # 2. Sleep Architecture Penalty (Δ_arch)
    delta_arch = 0.0
    if deep_pct is not None and isinstance(deep_pct, (int, float)) and deep_pct < 15.0:
        gap_deep = max(0.0, 15.0 - deep_pct)
        delta_arch += min(0.04, 0.04 * (gap_deep / 15.0))

    if rem_pct is not None and isinstance(rem_pct, (int, float)) and rem_pct < 15.0:
        gap_rem = max(0.0, 15.0 - rem_pct)
        delta_arch += min(0.02, 0.02 * (gap_rem / 15.0))

    delta_arch = round(min(0.05, delta_arch), 3)

    # 3. Autonomic Dipping Penalty (Δ_autonomic)
    delta_auto = 0.0
    if dipping_pct is not None and isinstance(dipping_pct, (int, float)):
        if dipping_pct >= 10.0:
            delta_auto = 0.0
        elif 0.0 <= dipping_pct < 10.0:
            delta_auto = 0.05 * ((10.0 - dipping_pct) / 10.0)
        else: # Reverse dipper / riser
            delta_auto = min(0.08, 0.05 + 0.03 * (abs(dipping_pct) / 10.0))

    delta_auto = round(min(0.08, delta_auto), 3)

    # Combined Multiplier & Strict Clamping [1.00, 1.25]
    raw_modifier = 1.00 + delta_debt + delta_arch + delta_auto
    final_modifier = round(min(1.25, max(1.00, raw_modifier)), 2)

    # Sleep quality rating
    if total_hours >= 7.0 and (efficiency_pct is None or efficiency_pct >= 85.0):
        quality = "Optimal"
        impact_note = f"Well-rested ({total_hours:.1f}h). Baseline insulin sensitivity intact."
    elif total_hours >= 5.5 and (efficiency_pct is None or efficiency_pct >= 75.0):
        quality = "Moderate"
        impact_note = f"Mild sleep reduction ({total_hours:.1f}h). Slight insulin resistance possible."
    else:
        quality = "Deficit"
        impact_note = f"Sleep deficit ({total_hours:.1f}h). Elevated cortisol/growth hormone reduces insulin sensitivity."

    return {
        "isf_modifier": final_modifier,
        "baseline_multiplier": 1.00,
        "debt_penalty": round(delta_debt, 3),
        "architecture_penalty": delta_arch,
        "autonomic_penalty": delta_auto,
        "rhr_dipping_pct": round(dipping_pct, 1) if dipping_pct is not None else None,
        "quality_rating": quality,
        "lifestyle_impact_note": impact_note
    }


# =============================================================================
# FEATURE 13: UNIFIED CIRCADIAN BIOMETRICS SUMMARY
# =============================================================================

def get_circadian_biometrics_summary(
    hours: int = 48,
    timezone_str: str = "America/New_York"
) -> Dict[str, Any]:
    """
    Synthesizes health_sessions and health_metrics from database to produce
    comprehensive sleep architecture, circadian phase, nocturnal RHR, and dynamic ISF modifier.
    """
    import db

    sessions = db.get_health_sessions(limit_hours=hours, session_type="sleep")
    metrics = db.get_health_metrics(limit_hours=hours, metric_type="heart_rate")

    stage_analytics = calculate_sleep_stage_analytics(sessions, timezone_str=timezone_str)
    circadian_phase = calculate_circadian_phase(sessions, timezone_str=timezone_str)
    rhr_metrics = calculate_nocturnal_rhr_metrics(metrics, sleep_sessions=sessions, timezone_str=timezone_str)

    isf_data = calculate_dynamic_isf_modifier(
        sleep_summary=stage_analytics,
        rhr_summary=rhr_metrics
    )

    return {
        "has_data": stage_analytics["has_data"] or rhr_metrics["has_hr_data"],
        "sleep": {
            "total_hours_24h": stage_analytics["total_sleep_hours"],
            "total_minutes": stage_analytics["total_sleep_minutes"],
            "time_in_bed_hours": stage_analytics["time_in_bed_hours"],
            "efficiency_percent": stage_analytics["efficiency_percent"],
            "deep_percent": stage_analytics["deep_sleep_percent"],
            "deep_minutes": stage_analytics["deep_sleep_minutes"],
            "rem_percent": stage_analytics["rem_sleep_percent"],
            "rem_minutes": stage_analytics["rem_sleep_minutes"],
            "light_percent": stage_analytics["light_sleep_percent"],
            "light_minutes": stage_analytics["light_sleep_minutes"],
            "awake_minutes": stage_analytics["awake_duration_minutes"],
            "awake_episodes_count": stage_analytics["awake_episodes_count"],
            "fragmentation_index": stage_analytics["fragmentation_index"],
            "restorative_ratio": stage_analytics["restorative_ratio"],
            "quality_rating": stage_analytics["quality_rating"]
        },
        "circadian": {
            "sleep_start": circadian_phase["sleep_start"],
            "sleep_end": circadian_phase["sleep_end"],
            "sleep_midpoint": circadian_phase["sleep_midpoint"],
            "sleep_midpoint_decimal": circadian_phase["sleep_midpoint_decimal"],
            "chronotype": circadian_phase["chronotype"],
            "circadian_alignment": circadian_phase["circadian_alignment"]
        },
        "rhr": {
            "daytime_baseline": rhr_metrics["daytime_baseline_rhr"],
            "nocturnal_baseline": rhr_metrics["nocturnal_baseline_rhr"],
            "dipping_percent": rhr_metrics["dipping_percent"],
            "dipper_category": rhr_metrics["dipper_category"],
            "nadir_bpm": rhr_metrics["nadir_bpm"],
            "nadir_time": rhr_metrics["nadir_time"],
            "nadir_relative_position": rhr_metrics["nadir_relative_position"],
            "recovery_pattern": rhr_metrics["recovery_pattern"],
            "data_points_day": rhr_metrics["data_points_day"],
            "data_points_night": rhr_metrics["data_points_night"]
        },
        "isf": {
            "modifier": isf_data["isf_modifier"],
            "debt_penalty": isf_data["debt_penalty"],
            "architecture_penalty": isf_data["architecture_penalty"],
            "autonomic_penalty": isf_data["autonomic_penalty"],
            "quality_rating": isf_data["quality_rating"],
            "explanation": isf_data["lifestyle_impact_note"]
        }
    }
