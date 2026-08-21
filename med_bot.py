"""
med_bot.py
MedFlowAssist Bot (@medflowassist_bot).

Handles prescription & PRN medication logging, one-tap preset dose buttons,
intake timestamps, interval history, and medication regimen records.
"""

import os
import re
import time
import math
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Union
import db
from bot_client import get_bot_client, mask_token

NAMESPACE_PREFIX = "med:"
FOREIGN_PREFIXES = ("gt:", "mh:", "bio:")

# Sliding-window callback query debouncing cache (TTL 60 seconds)
_processed_callbacks: Dict[str, float] = {}


def get_med_bot_client():
    return get_bot_client("med_flow")


def get_med_bot_config() -> dict:
    """Returns the Med Bot token and associated chat ID from client/system settings."""
    client = get_med_bot_client()
    tok = client.token
    cid = client.default_chat_id
    stored = db.get_system_setting("med_bot_config") or {}
    enabled = stored.get("enabled", True) if isinstance(stored, dict) else True
    return {
        "bot_token": tok,
        "chat_id": cid,
        "enabled": enabled,
        "is_configured": bool(tok and cid)
    }


def save_med_bot_config(token: str, chat_id: Optional[str] = None, enabled: bool = True):
    """Saves Med Bot configuration into database."""
    db.set_system_setting("med_bot_config", {
        "bot_token": token.strip() if token else "",
        "chat_id": str(chat_id).strip() if chat_id else "",
        "enabled": enabled,
        "updated_at": datetime.now(timezone.utc).isoformat()
    })


def send_med_message(text: str, reply_markup: Optional[dict] = None, chat_id: Optional[str] = None) -> dict:
    client = get_med_bot_client()
    return client.send_message(text, chat_id=chat_id, reply_markup=reply_markup)


def answer_callback_query(callback_query_id: str, text: Optional[str] = None, show_alert: bool = False) -> dict:
    client = get_med_bot_client()
    return client.answer_callback_query(callback_query_id, text=text, show_alert=show_alert)


def edit_message_text(chat_id: str, message_id: int, text: str, reply_markup: Optional[dict] = None) -> dict:
    client = get_med_bot_client()
    return client.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup)


def delete_med_message(chat_id: str, message_id: int) -> dict:
    client = get_med_bot_client()
    return client.delete_message(chat_id=chat_id, message_id=message_id)


# --- DB Helpers (delegated to db.py for consistency) ---

def get_medication_presets(active_only: bool = True) -> List[Dict[str, Any]]:
    return db.get_medication_presets(active_only=active_only)


def get_medication_preset_by_id(preset_id: int) -> Optional[Dict[str, Any]]:
    return db.get_medication_preset_by_id(preset_id)


def get_medication_preset_by_name(name: str) -> Optional[Dict[str, Any]]:
    return db.get_medication_preset_by_name(name)


def add_medication_preset(name: str, default_dose: float, dose_unit: str) -> int:
    return db.add_medication_preset(name, default_dose, dose_unit)


def delete_medication_preset(name_or_id: Union[str, int]) -> bool:
    return db.delete_medication_preset(name_or_id)


def log_medication_dose(
    medication_id: int,
    dose_taken: float,
    timestamp: Optional[datetime] = None,
    notes: Optional[str] = None
) -> int:
    return db.log_medication_dose(medication_id, dose_taken, timestamp, notes)


def get_recent_med_logs(
    limit: int = 15,
    medication_name: Optional[str] = None,
    medication_id: Optional[int] = None,
    hours: Optional[int] = None,
    limit_hours: Optional[int] = None
) -> List[Dict[str, Any]]:
    return db.get_recent_med_logs(
        limit=limit,
        medication_name=medication_name,
        medication_id=medication_id,
        hours=hours,
        limit_hours=limit_hours
    )


def get_medication_summary(medication_name: Optional[str] = None) -> List[Dict[str, Any]]:
    return db.get_medication_summary(medication_name=medication_name)


# --- Time Formatting Helper ---

def format_elapsed_time(past_time: Any, now_time: Optional[datetime] = None) -> str:
    """
    Formats elapsed time into concise human-readable strings:
    - < 1m: "just now"
    - < 60m: "{m}m ago"
    - < 24h: "{h}h {m}m ago" or "{h}h ago"
    - >= 24h: "{d}d {h}h ago" or "{d}d ago"
    """
    if past_time is None:
        return "never"

    if isinstance(past_time, (int, float)):
        try:
            past_time = datetime.fromtimestamp(past_time, tz=timezone.utc)
        except Exception:
            return "unknown"

    if isinstance(past_time, str):
        try:
            past_time = datetime.fromisoformat(past_time.replace("Z", "+00:00"))
        except Exception:
            return "unknown"

    if not isinstance(past_time, datetime):
        return "unknown"

    if now_time is None:
        now_time = datetime.now(timezone.utc)

    if past_time.tzinfo is None:
        past_time = past_time.replace(tzinfo=timezone.utc)
    if now_time.tzinfo is None:
        now_time = now_time.replace(tzinfo=timezone.utc)

    diff_seconds = max(0, int((now_time - past_time).total_seconds()))
    diff_minutes = diff_seconds // 60
    diff_hours = diff_minutes // 60
    diff_days = diff_hours // 24

    if diff_minutes < 1:
        return "just now"
    elif diff_minutes < 60:
        return f"{diff_minutes}m ago"
    elif diff_hours < 24:
        rem_min = diff_minutes % 60
        if rem_min > 0:
            return f"{diff_hours}h {rem_min}m ago"
        return f"{diff_hours}h ago"
    else:
        rem_hours = diff_hours % 24
        if rem_hours > 0:
            return f"{diff_days}d {rem_hours}h ago"
        return f"{diff_days}d ago"


def get_user_display_name(from_dict: Optional[dict]) -> str:
    """Extracts human-readable name from Telegram from object."""
    if not isinstance(from_dict, dict):
        return "User"
    first = str(from_dict.get("first_name") or "").strip()
    last = str(from_dict.get("last_name") or "").strip()
    username = str(from_dict.get("username") or "").strip()
    if first and last:
        return f"{first} {last}"
    elif first:
        return first
    elif username:
        return f"@{username}"
    return "User"


# --- Main Webhook Handler ---

def handle_med_webhook(update: dict) -> dict:
    """
    Processes incoming updates for the Medication Tracker bot (@medflowassist_bot).
    Returns standardized response: {"status": "ok"|"ignored"|"error", "action": str, "details": dict}
    """
    if not update or not isinstance(update, dict):
        return {"status": "ok", "action": "noop", "details": {"message": "Empty update"}}

    client = get_med_bot_client()

    MAIN_MENU_KEYBOARD = {
        "keyboard": [
            [{"text": "💊 Log Meds"}, {"text": "📋 View History"}],
            [{"text": "⚙️ Med Presets"}, {"text": "❓ Help"}]
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
        from_user = get_user_display_name(cb.get("from"))

        # Strict Foreign Namespace Check -> Immediate Ignore
        for foreign_prefix in FOREIGN_PREFIXES:
            if cb_data.startswith(foreign_prefix):
                return {
                    "status": "ignored",
                    "action": "foreign_namespace_ignored",
                    "reason": "foreign_namespace",
                    "details": {
                        "received_prefix": foreign_prefix,
                        "expected_prefix": NAMESPACE_PREFIX,
                        "callback_data": cb_data
                    }
                }

        # Sliding-Window Debounce Check (TTL 60s)
        now_ts = time.time()
        expired = [k for k, v in _processed_callbacks.items() if now_ts - v > 60.0]
        for k in expired:
            _processed_callbacks.pop(k, None)

        if cb_id and cb_id in _processed_callbacks:
            if cb_id:
                client.answer_callback_query(cb_id, "Already recorded.")
            return {"status": "ok", "action": "debounced", "details": {"callback_id": cb_id}}
        if cb_id:
            _processed_callbacks[cb_id] = now_ts

        # Namespaced Actions (med:log:) or Legacy Actions (log_med:)
        if cb_data.startswith("med:log:") or cb_data.startswith("log_med:"):
            raw_payload = cb_data[8:] if cb_data.startswith("med:log:") else cb_data[8:]
            parts = raw_payload.split(":")
            if len(parts) >= 2:
                try:
                    med_id = int(parts[0])
                    dose = float(parts[1])

                    if dose <= 0 or math.isnan(dose) or math.isinf(dose):
                        if cb_id:
                            client.answer_callback_query(cb_id, "Error: Dose must be a positive number.")
                        return {
                            "status": "error",
                            "action": "invalid_dose",
                            "message": "Dose must be a positive number",
                            "details": {"med_id": med_id, "dose": dose}
                        }

                    # Check preset in DB or via get_medication_presets
                    med = get_medication_preset_by_id(med_id)
                    if not med:
                        presets = get_medication_presets(active_only=False)
                        med = next((p for p in presets if p["id"] == med_id), None)

                    if med:
                        log_id = log_medication_dose(
                            medication_id=med_id,
                            dose_taken=dose,
                            notes=f"Logged via quick button by {from_user}"
                        )
                        if cb_id:
                            client.answer_callback_query(cb_id, f"Logged {dose:g} {med['dose_unit']} of {med['name']}")
                        if chat_id and msg_id:
                            client.edit_message_text(
                                chat_id=chat_id,
                                message_id=msg_id,
                                text=f"✅ <b>{from_user} logged {dose:g} {med['dose_unit']} of {med['name']}</b>"
                            )
                        return {
                            "status": "ok",
                            "action": "dose_logged",
                            "message": f"✅ {from_user} logged {dose:g} {med['dose_unit']} {med['name']}",
                            "details": {
                                "med_id": med_id,
                                "name": med["name"],
                                "dose": dose,
                                "unit": med["dose_unit"],
                                "logged_by": from_user,
                                "user": from_user,
                                "log_id": log_id
                            }
                        }
                    else:
                        if cb_id:
                            client.answer_callback_query(cb_id, "Error: Medication preset not found.")
                        return {"status": "error", "action": "medication_not_found", "details": {"med_id": med_id}}
                except Exception as e:
                    if cb_id:
                        client.answer_callback_query(cb_id, "Error logging medication.")
                    return {"status": "error", "action": "logging_error", "details": {"error": str(e)}}

            return {"status": "ok", "action": "malformed_callback", "details": {"callback_data": cb_data}}

        elif cb_data in ["med:dismiss", "dismiss_med"]:
            if cb_id:
                client.answer_callback_query(cb_id, "Dismissed.")
            if chat_id and msg_id:
                client.edit_message_text(chat_id=chat_id, message_id=msg_id, text="<i>Menu closed.</i>")
            return {"status": "ok", "action": "dismissed", "details": {}}

        return {"status": "ok", "action": "callback_noop", "details": {"data": cb_data}}

    # 2. Handle Text Messages
    msg = update.get("message")
    if isinstance(msg, dict):
        chat = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
        chat_id = chat.get("id")
        chat_type = chat.get("type", "private") if isinstance(chat.get("type"), str) else "private"
        raw_text = msg.get("text")

        if isinstance(raw_text, str) and raw_text.strip():
            # Check if group command is addressed to a different bot (e.g. /status@gluco_track_bot)
            cmd_match = re.search(r'^/([a-zA-Z0-9_]+)@([a-zA-Z0-9_]+)', raw_text.strip())
            if cmd_match:
                target_bot = cmd_match.group(2).lower()
                if target_bot not in ["medflowassist_bot", "medflow_bot", "med_bot"]:
                    return {
                        "status": "ignored",
                        "action": "command_for_other_bot",
                        "details": {"target_bot": target_bot, "command": cmd_match.group(1)}
                    }

            clean_text = re.sub(r'@[A-Za-z0-9_]+bot', '', raw_text, flags=re.IGNORECASE).strip()
            lower = clean_text.lower()

            config = get_med_bot_config()
            if not config.get("chat_id") or lower.startswith("/start") or lower.startswith("/link") or lower.startswith("/setgroup"):
                if config.get("bot_token"):
                    save_med_bot_config(config["bot_token"], chat_id)
                if lower.startswith("/link") or lower.startswith("/setgroup"):
                    client.send_message(f"✅ <b>MedFlow Linked Chat ID:</b> <code>{chat_id}</code>", chat_id=chat_id)
                    return {"status": "ok", "action": "chat_linked", "details": {"chat_id": chat_id}}

            # Group filtering: Ignore ambient noise in groups unless explicitly addressed
            if chat_type in ["group", "supergroup"]:
                is_addressed = raw_text.startswith("/") or ("@" in raw_text and ("@medflowassist_bot" in raw_text.lower() or "@medflow_bot" in raw_text.lower() or "@med_bot" in raw_text.lower()))
                if not is_addressed:
                    return {
                        "status": "ignored",
                        "action": "group_noise_ignored",
                        "reason": "ambient_noise_filtered",
                        "details": {"chat_id": chat_id, "chat_type": chat_type}
                    }

            # In DM mode only, attach persistent reply keyboards; in group chats, suppress them
            active_keyboard = MAIN_MENU_KEYBOARD if chat_type == "private" else None

            # Menu mapping
            if lower in ["💊 log meds", "log meds", "💊 log"]: lower = "/log"
            elif lower in ["📋 view history", "view history", "📋 history"]: lower = "/history"
            elif lower in ["⚙️ med presets", "med presets", "⚙️ presets"]: lower = "/presets"
            elif lower in ["❓ help", "help"]: lower = "/help"

            if lower.startswith("/start") or lower.startswith("/help"):
                reply = (
                    "💊 <b>Medication Tracker</b> (@medflowassist_bot)\n\n"
                    "• 💊 <code>/log</code> — Quick one-tap medication dose logging\n"
                    "• 📋 <code>/history</code> [Name] [Limit] — View chronological medication logs\n"
                    "• 📊 <code>/summary</code> [Name] — View 24h dosage summary and elapsed times\n"
                    "• ⚙️ <code>/presets</code> — View active presets\n"
                    "• ➕ <code>/addpreset [Name] [Dose] [Unit]</code> — Add/update a medication preset\n"
                    "• 🗑️ <code>/delpreset [Name]</code> — Remove a medication preset"
                )
                client.send_message(reply, reply_markup=active_keyboard, chat_id=chat_id)
                return {"status": "ok", "action": "start_menu_rendered", "reply_markup": active_keyboard, "details": {}}

            if lower.startswith("/log"):
                presets = get_medication_presets(active_only=True)
                if not presets:
                    client.send_message(
                        "⚠️ No medication presets found. Use <code>/addpreset [Name] [Dose] [Unit]</code> to create one.",
                        reply_markup=active_keyboard,
                        chat_id=chat_id
                    )
                    return {"status": "ok", "action": "no_presets_found", "details": {}}

                buttons = []
                for p in presets:
                    buttons.append([{
                        "text": f"💊 Log {p['default_dose']:g} {p['dose_unit']} {p['name']}",
                        "callback_data": f"med:log:{p['id']}:{p['default_dose']}"
                    }])
                buttons.append([{"text": "✕ Cancel", "callback_data": "med:dismiss"}])

                keyboard = {"inline_keyboard": buttons}
                client.send_message("Select a medication to log right now:", reply_markup=keyboard, chat_id=chat_id)
                return {"status": "ok", "action": "log_menu_sent", "details": {"presets_count": len(presets)}}

            if lower.startswith("/history"):
                parts = clean_text.split()
                med_filter = None
                limit = 15

                if len(parts) > 1:
                    if parts[-1].isdigit():
                        limit = max(1, min(50, int(parts[-1])))
                        if len(parts) > 2:
                            med_filter = " ".join(parts[1:-1]).strip()
                    else:
                        med_filter = " ".join(parts[1:]).strip()

                logs = get_recent_med_logs(limit=limit, medication_name=med_filter)
                if not logs:
                    if med_filter:
                        preset = get_medication_preset_by_name(med_filter)
                        if preset:
                            msg_text = f"📋 <b>Medication History: {preset['name']}</b>\n\nNo intake logs recorded yet for <b>{preset['name']}</b>.\n\nUse <code>/log</code> to record a dose."
                        else:
                            msg_text = f"⚠️ Medication <b>{med_filter}</b> not found.\n\nUse <code>/presets</code> to see registered medications or <code>/history</code> for all logs."
                    else:
                        msg_text = "No recent medications logged."
                    client.send_message(msg_text, reply_markup=active_keyboard, chat_id=chat_id)
                    return {"status": "ok", "action": "history_viewed", "text": msg_text, "count": 0, "details": {"filter": med_filter, "logs_count": 0}}

                title = f"📋 <b>Medication History ({med_filter})</b>:\n" if med_filter else "📋 <b>Recent Medications</b>:\n"
                lines = [title]
                now = datetime.now(timezone.utc)
                for l in logs:
                    elapsed = format_elapsed_time(l['timestamp'], now)
                    note_str = f" - <i>{l['notes']}</i>" if l.get('notes') else ""
                    lines.append(f"• <b>{l['dose_taken']:g} {l['dose_unit']} {l['name']}</b> ({elapsed}){note_str}")

                rendered_text = "\n".join(lines)
                client.send_message(rendered_text, reply_markup=active_keyboard, chat_id=chat_id)
                return {"status": "ok", "action": "history_viewed", "text": rendered_text, "count": len(logs), "details": {"filter": med_filter, "logs_count": len(logs)}}

            if lower.startswith("/summary"):
                parts = clean_text.split()
                med_filter = " ".join(parts[1:]).strip() if len(parts) > 1 else None

                summary_items = get_medication_summary(medication_name=med_filter)
                if not summary_items:
                    text_out = "📋 <b>Medication Regimen Summary</b>\n\nNo active medication presets configured.\nUse <code>/addpreset [Name] [Dose] [Unit]</code> to add one."
                    client.send_message(text_out, reply_markup=active_keyboard, chat_id=chat_id)
                    return {"status": "ok", "action": "summary_viewed", "text": text_out, "count": 0, "details": {"summary_count": 0}}

                lines = ["📊 <b>Medication Regimen Summary (24h)</b>:\n"]
                now = datetime.now(timezone.utc)
                for s in summary_items:
                    last_ts = s.get("last_timestamp")
                    last_str = format_elapsed_time(last_ts, now) if last_ts else "Never logged"
                    count_24 = s.get("count_24h", 0)
                    tot_24 = s.get("total_dose_24h", 0.0)
                    lines.append(
                        f"• <b>{s['name']}</b> ({s['default_dose']:g} {s['dose_unit']})\n"
                        f"  Last taken: {last_str}\n"
                        f"  Past 24h: {count_24} dose(s) ({tot_24:g} {s['dose_unit']} total)"
                    )
                rendered_text = "\n".join(lines)
                client.send_message(rendered_text, reply_markup=active_keyboard, chat_id=chat_id)
                return {"status": "ok", "action": "summary_viewed", "text": rendered_text, "count": len(summary_items), "details": {"summary_count": len(summary_items)}}

            if lower.startswith("/presets"):
                presets = get_medication_presets(active_only=True)
                if not presets:
                    text_out = "No presets configured.\nAdd one via:\n<code>/addpreset [Name] [Dose] [Unit]</code>\nExample: <code>/addpreset Lorazepam 1.0 mg</code>"
                    client.send_message(text_out, reply_markup=active_keyboard, chat_id=chat_id)
                    return {"status": "ok", "action": "presets_listed", "text": text_out, "count": 0, "details": {"presets_count": 0}}
                else:
                    lines = ["⚙️ <b>Active Medication Presets:</b>\n"]
                    for p in presets:
                        lines.append(f"• <b>{p['name']}</b>: {p['default_dose']:g} {p['dose_unit']}")
                    lines.append("\n<i>To log: tap 💊 Log Meds or /log</i>\n<i>To add: /addpreset [Name] [Dose] [Unit]</i>\n<i>To delete: /delpreset [Name]</i>")
                    text_out = "\n".join(lines)
                    client.send_message(text_out, reply_markup=active_keyboard, chat_id=chat_id)
                    return {"status": "ok", "action": "presets_listed", "text": text_out, "count": len(presets), "details": {"presets_count": len(presets)}}

            if lower.startswith("/addpreset"):
                parts = clean_text.split()
                if len(parts) >= 4:
                    unit = parts[-1]
                    try:
                        dose = float(parts[-2])
                        if dose <= 0 or math.isnan(dose) or math.isinf(dose):
                            client.send_message(
                                "⚠️ Dose must be a positive number greater than 0. Example: <code>/addpreset Lorazepam 1.0 mg</code>",
                                reply_markup=active_keyboard,
                                chat_id=chat_id
                            )
                            return {"status": "error", "message": "Dose must be a positive number greater than 0.", "action": "invalid_dose_value"}

                        name = " ".join(parts[1:-2]).strip()
                        if not name or len(name) > 255:
                            client.send_message(
                                "⚠️ Medication name must be between 1 and 255 characters.",
                                reply_markup=active_keyboard,
                                chat_id=chat_id
                            )
                            return {"status": "error", "message": "Missing medication name.", "action": "missing_name"}

                        med_id = add_medication_preset(name, dose, unit)
                        new_preset = {
                            "id": med_id,
                            "name": name,
                            "default_dose": dose,
                            "dose_unit": unit,
                            "is_active": True
                        }
                        client.send_message(
                            f"✅ Added preset: <b>{name}</b> ({dose:g} {unit})",
                            reply_markup=active_keyboard,
                            chat_id=chat_id
                        )
                        return {
                            "status": "ok",
                            "action": "preset_added",
                            "preset": new_preset,
                            "details": {"name": name, "dose": dose, "unit": unit, "id": med_id}
                        }
                    except ValueError:
                        client.send_message(
                            "⚠️ Dose must be a valid number. Example: <code>/addpreset Lorazepam 1.0 mg</code>",
                            reply_markup=active_keyboard,
                            chat_id=chat_id
                        )
                        return {"status": "error", "message": "Invalid dose format", "action": "invalid_dose_format"}
                else:
                    client.send_message(
                        "⚠️ Format: <code>/addpreset [Name] [Dose] [Unit]</code>\nExample: <code>/addpreset Oxycodone 5 mg</code>",
                        reply_markup=active_keyboard,
                        chat_id=chat_id
                    )
                    return {"status": "error", "message": "Format: /addpreset [Name] [Dose] [Unit]", "action": "invalid_format"}

            if lower.startswith("/delpreset") or lower.startswith("/deletepreset") or lower.startswith("/rmpreset"):
                parts = clean_text.split(maxsplit=1)
                if len(parts) >= 2 and parts[1].strip():
                    target = parts[1].strip()
                    deleted = delete_medication_preset(target)
                    if deleted:
                        client.send_message(
                            f"🗑️ Deleted preset: <b>{target}</b>",
                            reply_markup=active_keyboard,
                            chat_id=chat_id
                        )
                        return {"status": "ok", "action": "preset_deleted", "details": {"name": target}}
                    else:
                        client.send_message(
                            f"⚠️ Preset '<b>{target}</b>' not found or already deleted. Use <code>/presets</code> to view active presets.",
                            reply_markup=active_keyboard,
                            chat_id=chat_id
                        )
                        return {"status": "error", "action": "preset_not_found", "message": f"Preset '{target}' not found.", "details": {"name": target}}
                else:
                    client.send_message(
                        "⚠️ Format: <code>/delpreset [Name]</code>\nExample: <code>/delpreset Lorazepam</code>",
                        reply_markup=active_keyboard,
                        chat_id=chat_id
                    )
                    return {"status": "error", "action": "invalid_delpreset_format", "message": "Format: /delpreset [Name]"}

    return {"status": "ok", "action": "noop", "details": {}}
