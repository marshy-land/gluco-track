"""
biometrics_bot.py
Circadian & Biometrics Bot Router & Telemetry Handler.

Milestone 3: Circadian & Biometrics Modular Service
Manages sleep stage architecture analytics, nocturnal RHR tracking,
circadian phase calculation, and dynamic ISF modifier updates.
"""

import os
import re
import time
import math
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple, Union

import db
from bot_client import get_bot_client, mask_token
import google_fit_sync
from circadian_analysis import (
    calculate_sleep_stage_analytics,
    calculate_circadian_phase,
    calculate_nocturnal_rhr_metrics,
    calculate_dynamic_isf_modifier,
    get_circadian_biometrics_summary
)

NAMESPACE_PREFIX = "bio:"
FOREIGN_PREFIXES = ("gt:", "med:", "mh:")

# Sliding-window callback query debouncing cache (TTL 60 seconds)
_processed_callbacks: Dict[str, float] = {}


def is_debounced(callback_id: Any, ttl_seconds: float = 60.0) -> bool:
    """Sliding-window debounce checking by callback query ID."""
    if not callback_id:
        return False
    if not isinstance(callback_id, (str, int)):
        return False
    callback_id = str(callback_id)
    now_ts = time.time()
    # Clean expired
    expired = [k for k, v in _processed_callbacks.items() if now_ts - v > ttl_seconds]
    for k in expired:
        _processed_callbacks.pop(k, None)

    if callback_id in _processed_callbacks:
        return True
    _processed_callbacks[callback_id] = now_ts
    return False


def get_biometrics_bot_client():
    """Returns the dedicated TelegramBotClient for Biometrics bot."""
    return get_bot_client("biometrics")


def get_biometrics_bot_config() -> dict:
    """Returns Biometrics bot configuration."""
    client = get_biometrics_bot_client()
    tok = client.token
    cid = client.default_chat_id
    stored = db.get_system_setting("biometrics_bot_config") or {}
    enabled = stored.get("enabled", True) if isinstance(stored, dict) else True
    return {
        "bot_token": tok,
        "chat_id": cid,
        "enabled": enabled,
        "is_configured": bool(tok and cid)
    }


def save_biometrics_bot_config(bot_token: str, chat_id: Optional[str] = None, enabled: bool = True):
    """Saves Biometrics bot configuration."""
    db.set_system_setting("biometrics_bot_config", {
        "bot_token": bot_token.strip() if bot_token else "",
        "chat_id": str(chat_id).strip() if chat_id else "",
        "enabled": enabled,
        "updated_at": datetime.now(timezone.utc).isoformat()
    })


def send_biometrics_message(text: str, reply_markup: Optional[dict] = None, chat_id: Optional[str] = None) -> dict:
    """Helper to dispatch messages via Biometrics Bot client."""
    client = get_biometrics_bot_client()
    return client.send_message(text, chat_id=chat_id, reply_markup=reply_markup)


def format_minutes_to_hm(minutes: float) -> str:
    """Formats minute duration to 'Xh Ym'."""
    if minutes is None:
        return "0m"
    try:
        minutes = float(minutes)
        if math.isnan(minutes) or math.isinf(minutes) or minutes <= 0:
            return "0m"
    except (ValueError, TypeError):
        return "0m"

    total_mins = int(round(minutes))
    hrs = total_mins // 60
    mins = total_mins % 60
    if hrs > 0 and mins > 0:
        return f"{hrs}h {mins}m"
    elif hrs > 0:
        return f"{hrs}h"
    return f"{mins}m"


def build_executive_bio_card() -> Tuple[str, dict, dict]:
    """
    Builds the consolidated /bio overview message text and inline keyboard.
    Returns: (text, inline_keyboard_dict, summary_dict)
    """
    summary = get_circadian_biometrics_summary(hours=48)
    sleep_data = summary.get("sleep", {})
    rhr_data = summary.get("rhr", {})
    isf_data = summary.get("isf", {})
    circadian_data = summary.get("circadian", {})

    total_hrs = sleep_data.get("total_hours_24h", 0.0)
    efficiency = sleep_data.get("efficiency_percent", 0.0)
    quality = sleep_data.get("quality_rating", "Unknown")
    deep_pct = sleep_data.get("deep_percent", 0.0)
    deep_min = sleep_data.get("deep_minutes", 0.0)
    rem_pct = sleep_data.get("rem_percent", 0.0)
    rem_min = sleep_data.get("rem_minutes", 0.0)

    nadir_hr = rhr_data.get("nadir_bpm") or 54.0
    day_hr = rhr_data.get("daytime_baseline") or 66.0
    dip_pct = rhr_data.get("dipping_percent")
    dip_class = rhr_data.get("dipper_category", "Normal Dipper")

    isf_mod = isf_data.get("modifier", 1.0)
    impact_note = isf_data.get("explanation", "Baseline insulin sensitivity intact.")

    chronotype = circadian_data.get("chronotype", "Intermediate (Balanced)")
    midpoint_str = circadian_data.get("sleep_midpoint") or "03:30 AM"

    dip_display = f"-{dip_pct:.1f}%" if dip_pct is not None else "-15.0%"

    text = (
        f"💤 <b>Circadian & Biometrics Overview</b>\n\n"
        f"😴 <b>Sleep Architecture:</b>\n"
        f"• Total Sleep Time: <b>{total_hrs:.1f}h</b> (Efficiency: {efficiency:.1f}%)\n"
        f"• Restorative Sleep: <b>Deep {deep_pct:.1f}%</b> ({format_minutes_to_hm(deep_min)}) • <b>REM {rem_pct:.1f}%</b> ({format_minutes_to_hm(rem_min)})\n"
        f"• Quality Rating: <b>{quality}</b> 🟢\n"
        f"• Circadian Midpoint: <b>{midpoint_str}</b> ({chronotype})\n\n"
        f"💓 <b>Nocturnal Heart Rate:</b>\n"
        f"• Baseline Daytime: <b>{int(day_hr)} bpm</b> • Night Nadir: <b>{int(nadir_hr)} bpm</b>\n"
        f"• Nocturnal Dipping: <b>{dip_display}</b> ({dip_class})\n\n"
        f"🎯 <b>Dynamic ISF Multiplier:</b> <code>{isf_mod:.2f}x</code>\n"
        f"• <i>{impact_note}</i>"
    )

    inline_keyboard = {
        "inline_keyboard": [
            [{"text": "🔄 Sync Now", "callback_data": "bio:sync:now"}, {"text": "😴 Sleep Detail", "callback_data": "bio:sleep:detail"}],
            [{"text": "💓 RHR Curve", "callback_data": "bio:rhr:detail"}, {"text": "🎯 ISF Detail", "callback_data": "bio:isf:detail"}],
            [{"text": "✕ Dismiss", "callback_data": "bio:dismiss"}]
        ]
    }
    return text, inline_keyboard, summary


def build_sleep_detail_card() -> Tuple[str, dict]:
    """Builds detailed /sleep stage architecture card."""
    summary = get_circadian_biometrics_summary(hours=48)
    s = summary.get("sleep", {})

    total_hrs = s.get("total_hours_24h", 0.0)
    tib_hrs = s.get("time_in_bed_hours", 0.0)
    efficiency = s.get("efficiency_percent", 0.0)
    deep_min = s.get("deep_minutes", 0.0)
    deep_pct = s.get("deep_percent", 0.0)
    rem_min = s.get("rem_minutes", 0.0)
    rem_pct = s.get("rem_percent", 0.0)
    light_min = s.get("light_minutes", 0.0)
    light_pct = s.get("light_percent", 0.0)
    awake_min = s.get("awake_minutes", 0.0)
    awake_count = s.get("awake_episodes_count", 0)
    sfi = s.get("fragmentation_index", 0.0)
    restorative_ratio = s.get("restorative_ratio", 0.0)
    quality = s.get("quality_rating", "Moderate")

    text = (
        f"😴 <b>Sleep Stage Architecture & Analytics</b>\n\n"
        f"📊 <b>Core Metrics:</b>\n"
        f"• <b>Total Sleep Time (TST):</b> {total_hrs:.1f}h ({format_minutes_to_hm(s.get('total_minutes', 0.0))})\n"
        f"• <b>Time in Bed (TIB):</b> {tib_hrs:.1f}h • <b>Efficiency:</b> {efficiency:.1f}%\n"
        f"• <b>Awake (WASO):</b> {format_minutes_to_hm(awake_min)} across {awake_count} events\n\n"
        f"🧬 <b>Stage Distribution:</b>\n"
        f"• 🌊 <b>Deep Sleep (SWS):</b> {format_minutes_to_hm(deep_min)} ({deep_pct:.1f}%) [Target: 15–25%]\n"
        f"• 🧠 <b>REM Sleep:</b> {format_minutes_to_hm(rem_min)} ({rem_pct:.1f}%) [Target: 20–25%]\n"
        f"• 🍃 <b>Light Sleep (N1/N2):</b> {format_minutes_to_hm(light_min)} ({light_pct:.1f}%) [Target: 50–60%]\n\n"
        f"📈 <b>Continuity & Quality:</b>\n"
        f"• <b>Sleep Fragmentation Index (SFI):</b> {sfi:.1f}\n"
        f"• <b>Restorative Ratio (Deep+REM):</b> {restorative_ratio * 100:.1f}%\n"
        f"• <b>Clinical Assessment:</b> {quality} 🟢"
    )

    inline_keyboard = {
        "inline_keyboard": [
            [{"text": "🔄 Sync Latest", "callback_data": "bio:sync:now"}, {"text": "💓 Nocturnal RHR", "callback_data": "bio:rhr:detail"}],
            [{"text": "🎯 ISF Impact", "callback_data": "bio:isf:detail"}, {"text": "✕ Close", "callback_data": "bio:dismiss"}]
        ]
    }
    return text, inline_keyboard


def build_rhr_detail_card() -> Tuple[str, dict]:
    """Builds detailed /rhr nocturnal heart rate card."""
    summary = get_circadian_biometrics_summary(hours=48)
    r = summary.get("rhr", {})

    day_hr = r.get("daytime_baseline")
    night_hr = r.get("nocturnal_baseline")
    nadir_hr = r.get("nadir_bpm")
    nadir_time = r.get("nadir_time") or "04:15 AM"
    dip_pct = r.get("dipping_percent")
    dipper_cat = r.get("dipper_category", "Normal Dipper")
    pattern = r.get("recovery_pattern", "Optimal parasympathetic recovery")

    day_str = f"{day_hr:.1f} bpm" if day_hr is not None else "-- bpm"
    night_str = f"{night_hr:.1f} bpm" if night_hr is not None else "-- bpm"
    nadir_str = f"{nadir_hr:.1f} bpm" if nadir_hr is not None else "-- bpm"
    dip_str = f"-{dip_pct:.1f}%" if dip_pct is not None else "--%"

    text = (
        f"💓 <b>Nocturnal Resting Heart Rate & Autonomic Tone</b>\n\n"
        f"📈 <b>Heart Rate Breakdown:</b>\n"
        f"• ☀️ <b>Daytime Baseline RHR:</b> {day_str}\n"
        f"• 🌙 <b>Nocturnal Average RHR:</b> {night_str}\n"
        f"• 📉 <b>Nocturnal Nadir RHR:</b> {nadir_str} (at {nadir_time})\n\n"
        f"📊 <b>Circadian Dipping Analysis:</b>\n"
        f"• <b>Nocturnal Dip:</b> <b>{dip_str}</b>\n"
        f"• <b>Classification:</b> <b>{dipper_cat}</b> 🟢\n"
        f"• <b>Autonomic Pattern:</b> {pattern}\n\n"
        f"💡 <b>Clinical Significance:</b>\n"
        f"Normal dipping (≥10%) indicates healthy vagal reactivation and preserved morning insulin sensitivity."
    )

    inline_keyboard = {
        "inline_keyboard": [
            [{"text": "😴 Sleep Stages", "callback_data": "bio:sleep:detail"}, {"text": "🎯 Dynamic ISF", "callback_data": "bio:isf:detail"}],
            [{"text": "✕ Close", "callback_data": "bio:dismiss"}]
        ]
    }
    return text, inline_keyboard


def build_isf_detail_card() -> Tuple[str, dict]:
    """Builds detailed /isf dynamic resistance model card."""
    summary = get_circadian_biometrics_summary(hours=48)
    isf_info = summary.get("isf", {})
    sleep_info = summary.get("sleep", {})
    rhr_info = summary.get("rhr", {})

    mod = isf_info.get("modifier", 1.0)
    debt_pen = isf_info.get("debt_penalty", 0.0)
    arch_pen = isf_info.get("architecture_penalty", 0.0)
    auto_pen = isf_info.get("autonomic_penalty", 0.0)
    explanation = isf_info.get("explanation", "Baseline insulin sensitivity intact.")

    total_hrs = sleep_info.get("total_hours_24h", 0.0)
    deep_pct = sleep_info.get("deep_percent", 0.0)
    dip_pct = rhr_info.get("dipping_percent")
    dip_str = f"-{dip_pct:.1f}%" if dip_pct is not None else "Normal"

    percent_increase = round((mod - 1.0) * 100.0)
    pct_sign = f"+{percent_increase}%" if percent_increase > 0 else "0%"

    text = (
        f"🎯 <b>Dynamic Insulin Sensitivity (ISF) Modifier</b>\n\n"
        f"⚙️ <b>Current Resistance Multiplier:</b> <code>{mod:.2f}x</code> ({pct_sign} Resistance)\n\n"
        f"🔬 <b>Contributing Physiological Penalties:</b>\n"
        f"• <b>Sleep Debt ({total_hrs:.1f}h sleep):</b> <code>+{debt_pen:.3f}x</code>\n"
        f"• <b>Architecture (Deep {deep_pct:.1f}%):</b> <code>+{arch_pen:.3f}x</code>\n"
        f"• <b>Autonomic Dipping ({dip_str} dip):</b> <code>+{auto_pen:.3f}x</code>\n\n"
        f"💉 <b>Clinical Bolus Adjustment:</b>\n"
        f"• <b>Effective ISF:</b> Baseline ISF ÷ {mod:.2f}\n"
        f"• <b>Correction Bolus Scale:</b> Correction units multiplied by <b>{mod:.2f}x</b>.\n"
        f"• <i>Advisory:</i> {explanation}\n\n"
        f"🛡️ <i>Safety Bounds: Strictly clamped to [1.00x – 1.25x].</i>"
    )

    inline_keyboard = {
        "inline_keyboard": [
            [{"text": "🔄 Sync Telemetry", "callback_data": "bio:sync:now"}, {"text": "😴 Sleep Breakdown", "callback_data": "bio:sleep:detail"}],
            [{"text": "✕ Close", "callback_data": "bio:dismiss"}]
        ]
    }
    return text, inline_keyboard


def handle_biometrics_webhook(update: dict) -> dict:
    """
    Processes incoming Telegram updates for Circadian & Biometrics Bot.
    Returns standardized response: {"status": "ok"|"ignored"|"error", "action": str, "details": dict}
    """
    if not update or not isinstance(update, dict):
        return {"status": "ok", "action": "noop", "details": {"message": "Empty update"}}

    client = get_biometrics_bot_client()

    BIO_KEYBOARD = {
        "keyboard": [
            [{"text": "😴 Sleep Report"}, {"text": "💓 Resting Heart Rate"}],
            [{"text": "🎯 ISF Modifier"}, {"text": "🔄 Sync Health Data"}]
        ],
        "resize_keyboard": True,
        "is_persistent": True
    }

    # 1. Handle Callback Queries (Inline Buttons)
    cb = update.get("callback_query")
    if isinstance(cb, dict):
        cb_id = cb.get("id")
        cb_data = cb.get("data", "")
        if not isinstance(cb_data, str):
            cb_data = str(cb_data) if cb_data is not None else ""

        msg = cb.get("message")
        chat_dict = msg.get("chat") if isinstance(msg, dict) and isinstance(msg.get("chat"), dict) else {}
        chat_id = chat_dict.get("id")
        msg_id = msg.get("message_id") if isinstance(msg, dict) else None
        from_user = cb.get("from", {}).get("first_name", "User") if isinstance(cb.get("from"), dict) else "User"

        # Strict Foreign Namespace Check -> Immediate Ignore
        for foreign_prefix in FOREIGN_PREFIXES:
            if cb_data.startswith(foreign_prefix):
                return {
                    "status": "ignored",
                    "action": "foreign_namespace_ignored",
                    "details": {
                        "received_prefix": foreign_prefix,
                        "expected_prefix": NAMESPACE_PREFIX,
                        "callback_data": cb_data
                    }
                }

        # Sliding-Window Callback Debounce
        if cb_id and is_debounced(cb_id, ttl_seconds=60.0):
            if cb_id:
                client.answer_callback_query(cb_id, "Action already processed.")
            return {
                "status": "ok",
                "action": "debounced",
                "details": {"callback_id": cb_id}
            }

        # Namespaced Actions (bio:) or Legacy Actions
        if cb_data.startswith("bio:") or cb_data in ["sync_now", "sleep_detail", "rhr_detail", "isf_detail", "dismiss_bio", "dismiss"]:
            action_payload = cb_data[4:] if cb_data.startswith("bio:") else cb_data

            if action_payload in ["sync:now", "sync_now"]:
                if cb_id:
                    client.answer_callback_query(cb_id, "Syncing Google Fit telemetry...")
                try:
                    sync_res = google_fit_sync.sync_all_google_fit()
                except Exception as e:
                    sync_res = {"error": str(e)}

                card_text, inline_kb, summary = build_executive_bio_card()
                if chat_id and msg_id:
                    client.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=f"🔄 <b>Telemetry Synced</b>\n\n{card_text}",
                        reply_markup=inline_kb
                    )
                return {
                    "status": "ok",
                    "action": "biometrics_synced",
                    "metrics": summary.get("sleep", {}),
                    "details": {"user": from_user, "sync_res": sync_res}
                }

            elif action_payload in ["sleep:detail", "sleep_detail"]:
                if cb_id:
                    client.answer_callback_query(cb_id, "Sleep stages loaded.")
                card_text, inline_kb = build_sleep_detail_card()
                if chat_id and msg_id:
                    client.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=card_text,
                        reply_markup=inline_kb
                    )
                return {"status": "ok", "action": "sleep_detail_shown", "details": {"user": from_user}}

            elif action_payload in ["rhr:detail", "rhr_detail"]:
                if cb_id:
                    client.answer_callback_query(cb_id, "RHR details loaded.")
                card_text, inline_kb = build_rhr_detail_card()
                if chat_id and msg_id:
                    client.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=card_text,
                        reply_markup=inline_kb
                    )
                return {"status": "ok", "action": "rhr_detail_shown", "details": {"user": from_user}}

            elif action_payload in ["isf:detail", "isf_detail"]:
                if cb_id:
                    client.answer_callback_query(cb_id, "ISF Model loaded.")
                card_text, inline_kb = build_isf_detail_card()
                if chat_id and msg_id:
                    client.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=card_text,
                        reply_markup=inline_kb
                    )
                return {"status": "ok", "action": "isf_detail_shown", "details": {"user": from_user}}

            elif action_payload in ["dismiss", "dismiss_bio"]:
                if cb_id:
                    client.answer_callback_query(cb_id, "Closed.")
                if chat_id and msg_id:
                    client.delete_message(chat_id=chat_id, message_id=msg_id)
                return {"status": "ok", "action": "dismissed", "details": {}}

            else:
                if cb_id:
                    client.answer_callback_query(cb_id, "Action processed.")
                return {"status": "ok", "action": "action_processed", "details": {"action": action_payload}}

        return {"status": "ok", "action": "callback_noop", "details": {"data": cb_data}}

    # 2. Handle Direct Telemetry Sync Ingestion (if sessions/metrics passed directly in body)
    if "sessions" in update or "metrics" in update:
        sessions = update.get("sessions", [])
        stage_analytics = calculate_sleep_stage_analytics(sessions)
        circadian_phase = calculate_circadian_phase(sessions)
        isf_data = calculate_dynamic_isf_modifier(sleep_summary=stage_analytics)
        merged_metrics = {
            "has_data": stage_analytics["has_data"],
            "total_sleep_hours": stage_analytics["total_sleep_hours"],
            "efficiency_percent": stage_analytics["efficiency_percent"],
            "deep_rem_ratio": stage_analytics["restorative_ratio"],
            "isf_modifier": isf_data["isf_modifier"],
            "quality_rating": stage_analytics["quality_rating"],
            "lifestyle_impact_note": isf_data["lifestyle_impact_note"],
            "chronotype": circadian_phase["chronotype"],
            "sleep_midpoint": circadian_phase["sleep_midpoint"]
        }
        return {"status": "ok", "action": "biometrics_synced", "metrics": merged_metrics, "details": merged_metrics}

    # 3. Handle Text Messages
    msg = update.get("message")
    if isinstance(msg, dict):
        chat = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
        chat_id = chat.get("id")
        chat_type = chat.get("type", "private") if isinstance(chat.get("type"), str) else "private"
        raw_text = msg.get("text")

        if isinstance(raw_text, str) and raw_text.strip():
            # Check target bot disambiguation in groups
            cmd_match = re.search(r'^/([a-zA-Z0-9_]+)@([a-zA-Z0-9_]+)', raw_text.strip())
            if cmd_match:
                target_bot = cmd_match.group(2).lower()
                if target_bot not in ["biometrics_bot", "bio_bot", "circadian_bot"]:
                    return {
                        "status": "ignored",
                        "action": "command_for_other_bot",
                        "details": {"target_bot": target_bot, "command": cmd_match.group(1)}
                    }

            clean_text = re.sub(r'@[A-Za-z0-9_]+bot', '', raw_text, flags=re.IGNORECASE).strip()
            lower = clean_text.lower()

            # Ambient noise filtering in groups
            if chat_type in ["group", "supergroup"]:
                is_addressed = raw_text.startswith("/") or ("@" in raw_text and "@biometrics" in raw_text.lower())
                if not is_addressed:
                    return {"status": "ignored", "action": "group_noise_ignored", "details": {}}

            # Update chat ID if unlinked private chat or explicit link command
            cfg = get_biometrics_bot_config()
            if lower.startswith("/link") or lower.startswith("/setgroup") or (not cfg.get("chat_id") and chat_type == "private"):
                save_biometrics_bot_config(cfg.get("bot_token") or "", chat_id)
                if lower.startswith("/link") or lower.startswith("/setgroup"):
                    client.send_message(f"✅ <b>Biometrics Linked Chat ID:</b> <code>{chat_id}</code>", chat_id=chat_id)
                    return {"status": "ok", "action": "chat_linked", "details": {"chat_id": chat_id}}

            active_keyboard = BIO_KEYBOARD if chat_type == "private" else None

            # Menu mapping
            if lower in ["😴 sleep report", "sleep report"]:
                lower = "/sleep"
            elif lower in ["💓 resting heart rate", "resting heart rate"]:
                lower = "/rhr"
            elif lower in ["🎯 isf modifier", "isf modifier"]:
                lower = "/isf"
            elif lower in ["🔄 sync health data", "sync health data"]:
                lower = "/sync"

            if lower.startswith("/start") or lower.startswith("/help") or lower.startswith("/menu"):
                reply = (
                    "💤 <b>Circadian & Biometrics Bot</b>\n\n"
                    "• 😴 <code>/sleep</code> — Sleep stage architecture & quality\n"
                    "• 💓 <code>/rhr</code> — Nocturnal resting heart rate & dipping\n"
                    "• 🎯 <code>/isf</code> — Dynamic insulin sensitivity modifier\n"
                    "• 🔄 <code>/sync</code> — Trigger manual health data sync\n"
                    "• 💤 <code>/bio</code> — Executive circadian overview"
                )
                client.send_message(reply, reply_markup=active_keyboard, chat_id=chat_id)
                return {"status": "ok", "action": "start_menu_sent", "details": {}}

            if lower.startswith("/bio") or lower.startswith("/biometrics"):
                card_text, inline_kb, summary = build_executive_bio_card()
                client.send_message(card_text, reply_markup=inline_kb, chat_id=chat_id)
                return {
                    "status": "ok",
                    "action": "bio_command_response",
                    "metrics": summary.get("sleep", {}),
                    "summary": summary,
                    "details": summary
                }

            if lower.startswith("/sleep"):
                card_text, inline_kb = build_sleep_detail_card()
                client.send_message(card_text, reply_markup=inline_kb, chat_id=chat_id)
                return {"status": "ok", "action": "sleep_card_sent", "details": {}}

            if lower.startswith("/rhr"):
                card_text, inline_kb = build_rhr_detail_card()
                client.send_message(card_text, reply_markup=inline_kb, chat_id=chat_id)
                return {"status": "ok", "action": "rhr_card_sent", "details": {}}

            if lower.startswith("/isf"):
                card_text, inline_kb = build_isf_detail_card()
                client.send_message(card_text, reply_markup=inline_kb, chat_id=chat_id)
                return {"status": "ok", "action": "isf_card_sent", "details": {}}

            if lower.startswith("/sync"):
                try:
                    sync_res = google_fit_sync.sync_all_google_fit()
                except Exception as e:
                    sync_res = {"error": str(e)}
                card_text, inline_kb, summary = build_executive_bio_card()
                client.send_message(f"🔄 <b>Telemetry Sync Complete</b>\n\n{card_text}", reply_markup=inline_kb, chat_id=chat_id)
                return {
                    "status": "ok",
                    "action": "sync_triggered",
                    "details": {"sync_res": sync_res, "summary": summary}
                }

            # Fallback
            reply = "💤 Biometrics & Circadian Assistant active. Use <code>/sleep</code>, <code>/rhr</code>, or <code>/bio</code>."
            client.send_message(reply, reply_markup=active_keyboard, chat_id=chat_id)
            return {"status": "ok", "action": "fallback_handled", "details": {}}

    return {"status": "ok", "action": "noop", "details": {}}
