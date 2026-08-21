"""
telegram_bot.py
GlucoTrack Bot Handler (@gluco_track_bot).

Handles CGM telemetry queries, IOB tracking, carbohydrate estimation from photos,
meal bolus recommendations, and Lantus schedule tracking.
"""

import os
import json
import re
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
import pytz
import math
import requests
from dotenv import load_dotenv
import db
from nutrition_vision import estimate_carbohydrates_from_text, analyze_food_photo
from bot_client import get_bot_client, mask_token, TELEGRAM_API_BASE

load_dotenv()

EST_TZ = pytz.timezone("America/New_York")
NAMESPACE_PREFIX = "gt:"
FOREIGN_PREFIXES = ("med:", "mh:", "bio:")

_polling_running = False
_polling_thread = None


def get_gt_bot_client():
    return get_bot_client("gluco_track")


def get_telegram_config() -> dict:
    """Retrieves Telegram bot token and target chat/group ID."""
    client = get_gt_bot_client()
    tok = client.token
    cid = client.default_chat_id
    stored = db.get_system_setting("telegram_config") or {}
    enabled = stored.get("enabled", True) if isinstance(stored, dict) else True
    return {
        "bot_token": tok,
        "chat_id": cid,
        "enabled": enabled,
        "is_configured": bool(tok and cid)
    }


def save_telegram_config(bot_token: str, chat_id: Optional[str] = None, enabled: bool = True):
    """Saves Telegram configuration into database."""
    db.set_system_setting("telegram_config", {
        "bot_token": bot_token.strip() if bot_token else "",
        "chat_id": str(chat_id).strip() if chat_id else "",
        "enabled": enabled,
        "updated_at": datetime.now(timezone.utc).isoformat()
    })


def send_telegram_message(text: str, reply_markup: Optional[dict] = None, chat_id: Optional[str] = None, parse_mode: str = "HTML") -> dict:
    """Sends a message via Telegram Bot API to a private user or group chat."""
    client = get_gt_bot_client()
    return client.send_message(text, chat_id=chat_id, reply_markup=reply_markup, parse_mode=parse_mode)


def answer_callback_query(callback_query_id: str, text: Optional[str] = None, show_alert: bool = False) -> dict:
    """Acknowledges a Telegram inline button click."""
    client = get_gt_bot_client()
    return client.answer_callback_query(callback_query_id, text=text, show_alert=show_alert)


def edit_message_text(chat_id: str, message_id: int, text: str, reply_markup: Optional[dict] = None, parse_mode: str = "HTML") -> dict:
    """Updates the text of an existing Telegram message in private or group chat."""
    client = get_gt_bot_client()
    return client.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)


def delete_telegram_message(message_id: int, chat_id: Optional[str] = None) -> dict:
    """Deletes a Telegram message."""
    client = get_gt_bot_client()
    target_chat = chat_id or client.default_chat_id
    if not target_chat:
        return {"success": False, "error": "No chat_id specified"}
    return client.delete_message(chat_id=target_chat, message_id=message_id)


def schedule_message_deletion(message_id: int, minutes: int = 10):
    """Schedules a message ID to be deleted if not acted upon."""
    pending = db.get_system_setting("pending_deletions") or []
    pending.append({
        "message_id": message_id,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    })
    db.set_system_setting("pending_deletions", pending)


def cancel_message_deletion(message_id: int):
    """Cancels a pending message deletion (e.g. after user acts on it)."""
    pending = db.get_system_setting("pending_deletions") or []
    new_pending = [p for p in pending if isinstance(p, dict) and p.get("message_id") != message_id]
    if len(new_pending) != len(pending):
        db.set_system_setting("pending_deletions", new_pending)


def process_message_deletions():
    """Processes scheduled message deletions."""
    pending = db.get_system_setting("pending_deletions") or []
    if not pending:
        return

    now_utc = datetime.now(timezone.utc)
    new_pending = []
    changed = False

    for p in pending:
        try:
            if not isinstance(p, dict) or "expires_at" not in p:
                changed = True
                continue
            exp = datetime.fromisoformat(p["expires_at"])
            if now_utc >= exp:
                delete_telegram_message(p["message_id"])
                changed = True
            else:
                new_pending.append(p)
        except Exception:
            changed = True

    if changed:
        db.set_system_setting("pending_deletions", new_pending)


def download_telegram_photo(file_id: str) -> Optional[bytes]:
    """Downloads a photo sent in Telegram by its file_id."""
    client = get_gt_bot_client()
    token = client.token
    if not token:
        return None
    url = f"{TELEGRAM_API_BASE}{token}/getFile?file_id={file_id}"
    try:
        res = requests.get(url, timeout=10)
        if res.ok:
            file_path = res.json().get("result", {}).get("file_path")
            if file_path:
                dl_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
                img_res = requests.get(dl_url, timeout=15)
                if img_res.ok:
                    return img_res.content
    except Exception as e:
        print(f"[TelegramBot] Error downloading photo: {e}")
    return None


def get_user_display_name(from_dict: Optional[dict]) -> str:
    """Formats a user's name concisely (e.g. 'Alex')."""
    if not from_dict or not isinstance(from_dict, dict):
        return "Member"
    first = from_dict.get("first_name", "").strip() if isinstance(from_dict.get("first_name"), str) else ""
    username = from_dict.get("username", "").strip() if isinstance(from_dict.get("username"), str) else ""
    return first or username or "Member"


def get_live_patient_summary() -> Optional[dict]:
    """Fetches live glucose, IOB, safe carbs, corrections, and predictions."""
    try:
        latest = db.get_latest_reading()
        if not latest:
            return None

        from prediction import (
            predict_glucose,
            calculate_iob,
            suggest_correction,
            suggest_carbs,
            calculate_safe_carb_allowance,
            calculate_proactive_alert,
            get_lantus_schedule_status
        )
        from ml_heuristics import load_heuristics_params, get_time_of_day_bucket

        history = db.get_history(3)
        predictions = predict_glucose(history)
        recent_doses = db.get_insulin_history(4, include_imputed=True)
        # Only include imputed doses if confidence >= 95%
        valid_doses = [d for d in recent_doses if not d.get('is_imputed') or d.get('confidence_score', 0.0) >= 0.95]
        total_iob = calculate_iob(valid_doses)

        params = load_heuristics_params()
        bucket = get_time_of_day_bucket(latest['timestamp'])
        isf = params.get("isf", {}).get(bucket, 50.0)
        csf = params.get("csf", {}).get(bucket, 4.0)

        f60 = next((p['value'] for p in predictions if p['minutes'] == 60), latest['value'])
        safe_carbs = calculate_safe_carb_allowance(latest['value'], f60, total_iob, isf=isf, csf=csf)
        correction = suggest_correction(latest['value'], total_iob, target_glucose=120.0, isf=isf, forecasted_glucose=f60)
        proactive_alert = calculate_proactive_alert(latest['value'], predictions, total_iob, isf=isf, csf=csf)
        lantus_schedule = get_lantus_schedule_status(timezone_str="America/New_York")

        return {
            "glucose": latest['value'],
            "trend": latest.get('trend', 'Stable'),
            "timestamp": latest['timestamp'],
            "iob": total_iob,
            "isf": isf,
            "csf": csf,
            "bucket": bucket,
            "predictions": predictions,
            "safe_carbs": safe_carbs,
            "correction": correction,
            "proactive_alert": proactive_alert,
            "lantus_schedule": lantus_schedule
        }
    except Exception as e:
        print(f"[TelegramBot] Error getting live summary: {e}")
        return None


def compute_meal_bolus(carbs_g: float, summary: Optional[dict]) -> float:
    """Calculates suggested meal insulin bolus based on carbs and trajectory-aware preemptive correction."""
    if not summary or carbs_g <= 0:
        return 0.0

    isf = summary.get("isf", 50.0)
    csf = summary.get("csf", 4.0)
    icr = max(isf / max(csf, 1.0), 8.0)

    carb_insulin = carbs_g / icr
    correction = summary.get("correction", 0.0)
    net_bolus = max(0.0, carb_insulin + correction)

    return round(net_bolus, 1)


def handle_telegram_update(update: dict) -> dict:
    """
    Main entrypoint for processing incoming Telegram updates for GlucoTrack.
    Returns standardized response: {"status": "ok"|"ignored"|"error", "action": str, "details": dict}
    """
    if not update or not isinstance(update, dict):
        return {"status": "ok", "action": "noop", "details": {"message": "Empty update"}}

    client = get_gt_bot_client()

    # 1. Handle Inline Button Clicks (Callback Queries)
    if "callback_query" in update:
        cb = update["callback_query"]
        cb_id = cb.get("id")
        cb_data = cb.get("data", "")
        if not isinstance(cb_data, str):
            cb_data = str(cb_data) if cb_data is not None else ""

        msg = cb.get("message")
        chat_dict = msg.get("chat") if isinstance(msg, dict) and isinstance(msg.get("chat"), dict) else {}
        chat_id = chat_dict.get("id")
        msg_id = msg.get("message_id") if isinstance(msg, dict) else None
        actor_name = get_user_display_name(cb.get("from"))
        now_est = datetime.now(timezone.utc).astimezone(EST_TZ)
        time_str = now_est.strftime("%I:%M %p EST")

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

        # A. Log Meal & Optional Bolus
        if cb_data.startswith("gt:meal:") or cb_data.startswith("log_meal:"):
            payload = cb_data[8:] if cb_data.startswith("gt:meal:") else cb_data[9:]
            parts = payload.split(":")
            carbs = float(parts[0]) if len(parts) > 0 and parts[0] else 0.0
            if math.isnan(carbs) or math.isinf(carbs) or carbs < 0:
                carbs = 0.0
            bolus = float(parts[1]) if len(parts) > 1 and parts[1] else 0.0
            if math.isnan(bolus) or math.isinf(bolus) or bolus < 0:
                bolus = 0.0
            now_utc = datetime.now(timezone.utc)

            db.insert_food_log(carbs_g=carbs, timestamp=now_utc, food_type=f"Logged via Telegram ({actor_name})")

            if bolus > 0.0:
                dose_dict = {
                    "timestamp": now_utc,
                    "rapid_acting": bolus,
                    "long_acting": 0.0,
                    "meal": bolus,
                    "correction": 0.0,
                    "user_change": 0.0,
                    "device": f"Telegram Meal Bolus ({actor_name})",
                    "serial_number": None
                }
                db.insert_insulin_doses([dose_dict])

            if cb_id:
                client.answer_callback_query(cb_id, f"Recorded {carbs:.0f}g carbs & {bolus:.1f}U bolus.")
            if msg_id:
                cancel_message_deletion(msg_id)
            bolus_text = f" + <b>{bolus:.1f}U</b>" if bolus > 0 else ""
            if chat_id and msg_id:
                client.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=f"✅ <b>{actor_name}: Recorded {carbs:.0f}g carbs{bolus_text}</b> • {time_str}"
                )
            return {
                "status": "ok",
                "action": "meal_logged",
                "details": {"carbs": carbs, "bolus": bolus, "user": actor_name}
            }

        # B. Log Lantus Scheduled Dose (13U)
        elif cb_data.startswith("gt:lantus:") or cb_data.startswith("took_lantus:"):
            payload = cb_data[10:] if cb_data.startswith("gt:lantus:") else cb_data[12:]
            try:
                units = float(payload) if payload else 13.0
            except Exception:
                units = 13.0

            now_utc = datetime.now(timezone.utc)
            dose_dict = {
                "timestamp": now_utc,
                "rapid_acting": 0.0,
                "long_acting": units,
                "meal": 0.0,
                "correction": 0.0,
                "user_change": 0.0,
                "device": f"Telegram ({actor_name})",
                "serial_number": None
            }
            db.insert_insulin_doses([dose_dict])
            if cb_id:
                client.answer_callback_query(cb_id, f"Recorded {units}U Lantus.")
            db.set_system_setting("pending_compliance_check", None)

            if chat_id and msg_id:
                client.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=f"✅ <b>{actor_name}: {units:.1f} U Lantus recorded</b> • {time_str}"
                )
            return {
                "status": "ok",
                "action": "lantus_logged",
                "details": {"units": units, "user": actor_name}
            }

        # C. Log Rapid / Correction Dose
        elif cb_data.startswith("gt:corr:") or cb_data.startswith("took_correction:"):
            payload = cb_data[8:] if cb_data.startswith("gt:corr:") else cb_data[16:]
            try:
                units = float(payload) if payload else 1.0
            except Exception:
                units = 1.0

            now_utc = datetime.now(timezone.utc)
            dose_dict = {
                "timestamp": now_utc,
                "rapid_acting": units,
                "long_acting": 0.0,
                "meal": 0.0,
                "correction": units,
                "user_change": 0.0,
                "device": f"Telegram ({actor_name})",
                "serial_number": None
            }
            db.insert_insulin_doses([dose_dict])
            if cb_id:
                client.answer_callback_query(cb_id, f"Recorded {units}U.")
            db.set_system_setting("pending_compliance_check", None)
            if msg_id:
                cancel_message_deletion(msg_id)

            if chat_id and msg_id:
                client.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=f"✅ <b>{actor_name}: {units:.1f} U rapid recorded</b> • {time_str}"
                )
            return {
                "status": "ok",
                "action": "correction_logged",
                "details": {"units": units, "user": actor_name}
            }

        # D. Snooze / Later
        elif cb_data.startswith("gt:snooze:") or cb_data.startswith("snooze:"):
            payload = cb_data[10:] if cb_data.startswith("gt:snooze:") else cb_data[7:]
            try:
                mins = int(payload) if payload else 60
            except Exception:
                mins = 60
            if cb_id:
                client.answer_callback_query(cb_id, "Took note.")
            if msg_id:
                cancel_message_deletion(msg_id)

            snooze_until = (datetime.now(timezone.utc) + timedelta(minutes=mins)).isoformat()
            pending = db.get_system_setting("pending_compliance_check") or {}
            if isinstance(pending, dict):
                pending["snooze_until"] = snooze_until
                db.set_system_setting("pending_compliance_check", pending)

            if chat_id and msg_id:
                client.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=f"⏳ <b>{actor_name}: Noted</b> — checking back later whenever you're ready."
                )
            return {
                "status": "ok",
                "action": "snoozed",
                "details": {"minutes": mins, "user": actor_name}
            }

        # E. Skip / Dismiss
        elif cb_data in ["gt:skip", "gt:dismiss", "skip_dose", "dismiss"]:
            if cb_id:
                client.answer_callback_query(cb_id, "Noted.")
            if msg_id:
                cancel_message_deletion(msg_id)
            db.set_system_setting("pending_compliance_check", None)

            if cb_data in ["gt:dismiss", "dismiss"]:
                if chat_id and msg_id:
                    client.delete_message(chat_id=chat_id, message_id=msg_id)
                return {"status": "ok", "action": "dismissed", "details": {"user": actor_name}}
            else:
                if chat_id and msg_id:
                    client.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=f"✕ <i>Noted for today by {actor_name}</i>"
                    )
                return {"status": "ok", "action": "skipped", "details": {"user": actor_name}}

        elif cb_data in ["gt:status", "check_status"]:
            summary = get_live_patient_summary()
            if summary:
                bg = summary["glucose"]
                if cb_id:
                    client.answer_callback_query(cb_id, f"Current BG: {bg:.0f} mg/dL")
            else:
                if cb_id:
                    client.answer_callback_query(cb_id, "No live data.")
            return {"status": "ok", "action": "status_checked", "details": {}}

        return {"status": "ok", "action": "callback_noop", "details": {"data": cb_data}}

    # 2. Handle Message Object
    msg = update.get("message")
    if isinstance(msg, dict):
        chat = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
        chat_id = chat.get("id")
        chat_type = chat.get("type", "private") if isinstance(chat.get("type"), str) else "private"

        # A. Photo Message
        photos = msg.get("photo")
        if isinstance(photos, list) and len(photos) > 0:
            caption = msg.get("caption", "").strip() if isinstance(msg.get("caption"), str) else ""
            best_photo = photos[-1] if isinstance(photos[-1], dict) else {}
            file_id = best_photo.get("file_id")

            if file_id:
                photo_bytes = download_telegram_photo(file_id)
                if photo_bytes:
                    analysis = analyze_food_photo(photo_bytes, caption=caption)
                    summary = get_live_patient_summary()
                    carbs_g = analysis.get("carbs_g", 35.0)
                    bolus = compute_meal_bolus(carbs_g, summary)

                    item_parts = []
                    for it in analysis.get("items", []):
                        if isinstance(it, dict):
                            item_parts.append(f"{it.get('name', 'Food')} ({it.get('carbs_g', 0):.0f}g)")

                    summary_items = " + ".join(item_parts) if item_parts else analysis.get('description', 'Visual Meal')
                    bg_val = f"{summary['glucose']:.0f}" if summary else "--"
                    iob_val = f"{summary['iob']:.1f}" if summary else "0.0"

                    card_text = (
                        f"📸 <b>Meal:</b> {summary_items}\n"
                        f"<b>Carbs:</b> ~{carbs_g:.0f}g | <b>IOB:</b> {iob_val}U | <b>BG:</b> {bg_val} mg/dL\n"
                        f"<b>Suggested Bolus:</b> <b>{bolus:.1f} U</b>"
                    )

                    keyboard = {
                        "inline_keyboard": [
                            [{"text": f"✓ Log {carbs_g:.0f}g + {bolus:.1f}U", "callback_data": f"gt:meal:{carbs_g:.1f}:{bolus:.1f}"}],
                            [{"text": f"Log {carbs_g:.0f}g Only", "callback_data": f"gt:meal:{carbs_g:.1f}:0.0"}, {"text": "✕", "callback_data": "gt:dismiss"}]
                        ]
                    }
                    res = send_telegram_message(card_text, reply_markup=keyboard, chat_id=chat_id)
                    if res and res.get("success") and res.get("result"):
                        schedule_message_deletion(res["result"]["message_id"], minutes=10)
                    return {"status": "ok", "action": "photo_analyzed", "details": {"carbs": carbs_g, "bolus": bolus}}

        # B. Group Invitations / Bot Added to Group
        new_members = msg.get("new_chat_members")
        if isinstance(new_members, list) and len(new_members) > 0:
            chat_title = chat.get("title") or "Care Circle"
            config = get_telegram_config()
            save_telegram_config(config.get("bot_token") or "", chat_id)

            welcome_text = (
                f"🎉 <b>Connected to {chat_title}!</b>\n"
                f"Active channel for Lantus routine reminders (6 AM & 6 PM EST), meal photo carb counts, and urgent alerts.\n"
                f"Commands: <code>/status</code>, <code>/carbs</code>, <code>/dose</code>, <code>/schedule</code>"
            )
            send_telegram_message(welcome_text, chat_id=chat_id)
            return {"status": "ok", "action": "group_welcome_sent", "details": {"chat_id": chat_id}}

        # C. Text Messages
        raw_text = msg.get("text")
        if isinstance(raw_text, str) and raw_text.strip():
            clean_text = re.sub(r'@[A-Za-z0-9_]+bot', '', raw_text, flags=re.IGNORECASE).strip()
            lower = clean_text.lower()
            sender_name = get_user_display_name(msg.get("from"))

            MAIN_MENU_KEYBOARD = {
                "keyboard": [
                    [{"text": "📊 Status"}, {"text": "🍎 Log Food"}],
                    [{"text": "💉 Check Dose"}, {"text": "⏰ Lantus Schedule"}]
                ],
                "resize_keyboard": True,
                "is_persistent": True
            }

            if lower == "📊 status":
                lower = "/status"
            elif lower == "🍎 log food":
                lower = "/food"
            elif lower == "💉 check dose":
                lower = "/dose"
            elif lower == "⏰ lantus schedule":
                lower = "/schedule"

            config = get_telegram_config()
            if not config.get("chat_id") or lower.startswith("/setgroup") or lower.startswith("/link"):
                save_telegram_config(config.get("bot_token") or "", chat_id)
                if lower.startswith("/setgroup") or lower.startswith("/link"):
                    send_telegram_message(f"✅ <b>Linked Chat ID:</b> <code>{chat_id}</code>", chat_id=chat_id)
                    return {"status": "ok", "action": "chat_linked", "details": {"chat_id": chat_id}}

            summary = get_live_patient_summary()
            now_est = datetime.now(timezone.utc).astimezone(EST_TZ)
            time_str = now_est.strftime("%I:%M %p EST")

            # 1. Direct Text Food Logging
            is_food_log = any(lower.startswith(k) for k in ["ate ", "had ", "eating ", "having ", "log meal ", "log food ", "food: ", "meal: "]) or \
                          ("carbs" in lower and any(v in lower for v in ["ate", "had", "eating", "having", "taking", "log"]))

            if is_food_log:
                estimation = estimate_carbohydrates_from_text(clean_text)
                carbs_g = estimation.get("carbs_g", 30.0)
                bolus = compute_meal_bolus(carbs_g, summary)

                item_parts = []
                for it in estimation.get("items", []):
                    if isinstance(it, dict):
                        item_parts.append(f"{it.get('name', 'Food')} ({it.get('carbs_g', 0):.0f}g)")
                summary_items = " + ".join(item_parts) if item_parts else estimation.get('description', clean_text)

                bg_val = f"{summary['glucose']:.0f}" if summary else "--"
                iob_val = f"{summary['iob']:.1f}" if summary else "0.0"

                card_text = (
                    f"🍽️ <b>Meal:</b> {summary_items}\n"
                    f"<b>Carbs:</b> ~{carbs_g:.0f}g | <b>IOB:</b> {iob_val}U | <b>BG:</b> {bg_val} mg/dL\n"
                    f"<b>Suggested Bolus:</b> <b>{bolus:.1f} U</b>"
                )

                keyboard = {
                    "inline_keyboard": [
                        [{"text": f"✓ Log {carbs_g:.0f}g + {bolus:.1f}U", "callback_data": f"gt:meal:{carbs_g:.1f}:{bolus:.1f}"}],
                        [{"text": f"Log {carbs_g:.0f}g Only", "callback_data": f"gt:meal:{carbs_g:.1f}:0.0"}, {"text": "✕", "callback_data": "gt:dismiss"}]
                    ]
                }
                res = send_telegram_message(card_text, reply_markup=keyboard, chat_id=chat_id)
                if res and res.get("success") and res.get("result"):
                    schedule_message_deletion(res["result"]["message_id"], minutes=10)
                return {"status": "ok", "action": "text_food_estimated", "details": {"carbs": carbs_g, "bolus": bolus}}

            # 2. Direct Dose Logging
            lantus_match = re.search(r'(?:took|injected|logged|take|did)\s*(\d+(?:\.\d+)?)\s*(?:u|units)?\s*(?:of)?\s*lantus', lower) or \
                           re.search(r'lantus\s*(?:dose)?\s*(?:taken|logged|done|took)', lower)
            if lantus_match:
                try:
                    units = float(lantus_match.group(1)) if lantus_match.groups() and lantus_match.group(1) else 13.0
                except Exception:
                    units = 13.0

                now_utc = datetime.now(timezone.utc)
                dose_dict = {
                    "timestamp": now_utc,
                    "rapid_acting": 0.0,
                    "long_acting": units,
                    "meal": 0.0,
                    "correction": 0.0,
                    "user_change": 0.0,
                    "device": f"Telegram ({sender_name})",
                    "serial_number": None
                }
                db.insert_insulin_doses([dose_dict])
                db.set_system_setting("pending_compliance_check", None)

                send_telegram_message(f"✅ <b>{sender_name}: {units:.1f} U Lantus recorded</b> • {time_str}", chat_id=chat_id)
                return {"status": "ok", "action": "lantus_direct_logged", "details": {"units": units}}

            rapid_match = re.search(r'(?:took|injected|logged|take)\s*(\d+(?:\.\d+)?)\s*(?:u|units)?\s*(?:of)?\s*(?:rapid|novolog|humalog|correction|bolus)', lower)
            if rapid_match:
                try:
                    units = float(rapid_match.group(1)) if rapid_match.groups() and rapid_match.group(1) else 1.0
                except Exception:
                    units = 1.0

                now_utc = datetime.now(timezone.utc)
                dose_dict = {
                    "timestamp": now_utc,
                    "rapid_acting": units,
                    "long_acting": 0.0,
                    "meal": 0.0,
                    "correction": units,
                    "user_change": 0.0,
                    "device": f"Telegram ({sender_name})",
                    "serial_number": None
                }
                db.insert_insulin_doses([dose_dict])
                db.set_system_setting("pending_compliance_check", None)

                send_telegram_message(f"✅ <b>{sender_name}: {units:.1f} U Rapid recorded</b> • {time_str}", chat_id=chat_id)
                return {"status": "ok", "action": "rapid_direct_logged", "details": {"units": units}}

            # /start or /help
            if lower.startswith("/start") or lower.startswith("/help"):
                reply = (
                    "👋 <b>Gluco Track Assistant</b>\n\n"
                    "• 📸 <b>Send Photo:</b> Visual meal carb counting & bolus advice\n"
                    "• 🍽️ <b>Text Food:</b> <i>'ate 2 slices toast + eggs'</i>\n"
                    "• 📊 <code>/status</code> — Live glucose, trend, IOB, forecast\n"
                    "• 🍎 <code>/carbs</code> — Safe snack allowance\n"
                    "• 💉 <code>/dose</code> — Bolus & correction check\n"
                    "• ⏰ <code>/schedule</code> — Lantus (6 AM / 6 PM EST) status"
                )
                send_telegram_message(reply, reply_markup=MAIN_MENU_KEYBOARD, chat_id=chat_id)
                return {"status": "ok", "action": "start_menu_sent", "details": {}}

            # /status or /bg
            if lower.startswith("/status") or lower.startswith("/bg") or any(q in lower for q in ["what's my blood sugar", "what is my bg", "what is the blood sugar", "current bg", "blood sugar", "glucose level"]):
                if not summary:
                    send_telegram_message("⚠️ No live glucose data available.", chat_id=chat_id)
                    return {"status": "ok", "action": "no_data_reply", "details": {}}

                bg = summary["glucose"]
                iob = summary["iob"]
                preds = summary.get("predictions", [])
                f60 = next((p['value'] for p in preds if isinstance(p, dict) and p.get('minutes') == 60), bg)
                sc = summary["safe_carbs"]
                corr = summary["correction"]

                status_emoji = "🟢" if 70 <= bg <= 160 else ("🔴" if bg < 70 else "🟡")
                corr_str = f"{corr:.1f} U" if corr > 0 else "None (0.0 U)"

                reply = (
                    f"{status_emoji} <b>{bg:.0f} mg/dL</b> • {time_str}\n"
                    f"<b>IOB:</b> {iob:.2f} U | <b>+60m Forecast:</b> {f60:.0f} mg/dL\n"
                    f"<b>Snack Allowance:</b> {sc.get('label', 'Normal')}\n"
                    f"<b>Correction:</b> {corr_str}"
                )
                send_telegram_message(reply, chat_id=chat_id)
                return {"status": "ok", "action": "status_sent", "details": {"glucose": bg, "iob": iob}}

            # /carbs
            if lower.startswith("/carbs") or any(q in lower for q in ["can i eat", "can they eat", "snack", "hungry", "is it safe to eat"]):
                if not summary:
                    send_telegram_message("⚠️ No live glucose data available.", chat_id=chat_id)
                    return {"status": "ok", "action": "no_data_reply", "details": {}}

                sc = summary["safe_carbs"]
                bg = summary["glucose"]

                if sc.get("type") == "rescue":
                    reply = (
                        f"🚨 <b>Low Trajectory: {bg:.0f} mg/dL</b> ({time_str})\n"
                        f"👉 Take <b>~{int(sc.get('grams', 15))}g fast-acting carbs</b> to stay steady."
                    )
                elif sc.get("type") == "restricted":
                    reply = (
                        f"⚠️ <b>Elevated Glucose: {bg:.0f} mg/dL</b>\n"
                        f"Carb buffer is limited right now. Low or zero-carb snacks are best until levels settle."
                    )
                else:
                    reply = (
                        f"🍎 <b>Safe Snack: ~{int(sc.get('grams', 20))}g carbs</b>\n"
                        f"BG: <b>{bg:.0f} mg/dL</b> • Active IOB: {summary['iob']:.2f} U"
                    )
                send_telegram_message(reply, reply_markup=MAIN_MENU_KEYBOARD, chat_id=chat_id)
                return {"status": "ok", "action": "carbs_sent", "details": {"type": sc.get("type")}}

            # /log or /food
            if lower.startswith("/log") or lower.startswith("/food"):
                reply = (
                    "🍎 <b>Food Logging</b>\n\n"
                    "To log food, just send me a picture of your meal, or describe it textually like:\n"
                    "<i>\"Ate 2 slices of pizza and a diet coke\"</i>"
                )
                send_telegram_message(reply, reply_markup=MAIN_MENU_KEYBOARD, chat_id=chat_id)
                return {"status": "ok", "action": "food_instructions_sent", "details": {}}

            # /dose or /insulin
            if lower.startswith("/dose") or lower.startswith("/insulin"):
                if not summary:
                    send_telegram_message("⚠️ No live data to calculate dose.", reply_markup=MAIN_MENU_KEYBOARD, chat_id=chat_id)
                    return {"status": "ok", "action": "no_data_reply", "details": {}}

                bg = summary["glucose"]
                iob = summary["iob"]
                corr = summary.get("correction", 0.0)

                if corr > 0:
                    reply = (
                        f"💉 <b>Recommended Correction: {corr:.1f} U</b>\n"
                        f"BG: <b>{bg:.0f} mg/dL</b> (Target: 120) • IOB: {iob:.2f} U"
                    )
                    keyboard = {
                        "inline_keyboard": [
                            [{"text": f"✓ Log {corr:.1f} U", "callback_data": f"gt:corr:{corr:.1f}"}],
                            [{"text": "⏳ Later", "callback_data": "gt:snooze:60"}, {"text": "✕ Skip", "callback_data": "gt:skip"}]
                        ]
                    }
                    res = send_telegram_message(reply, reply_markup=keyboard, chat_id=chat_id)
                    if res and res.get("success") and res.get("result"):
                        schedule_message_deletion(res["result"]["message_id"], minutes=10)
                    return {"status": "ok", "action": "correction_suggested", "details": {"correction": corr}}
                else:
                    reply = (
                        f"🟢 <b>No Correction Needed (0.0 U)</b>\n"
                        f"BG: <b>{bg:.0f} mg/dL</b> • IOB: {iob:.2f} U"
                    )
                    send_telegram_message(reply, reply_markup=MAIN_MENU_KEYBOARD, chat_id=chat_id)
                    return {"status": "ok", "action": "no_correction_needed", "details": {}}

            # /schedule or /lantus
            if lower.startswith("/schedule") or lower.startswith("/lantus") or any(q in lower for q in ["when is the next dose", "next lantus", "lantus schedule"]):
                if not summary:
                    send_telegram_message("⚠️ No schedule data available.", reply_markup=MAIN_MENU_KEYBOARD, chat_id=chat_id)
                    return {"status": "ok", "action": "no_data_reply", "details": {}}

                ls = summary["lantus_schedule"]
                next_d = ls["next_dose"]
                morn_icon = "✅" if ls["morning"]["taken"] else "⏳"
                eve_icon = "✅" if ls["evening"]["taken"] else "⏳"

                reply = (
                    f"⏰ <b>Lantus Schedule (2x13U EST)</b>\n"
                    f"• {morn_icon} <b>6:00 AM:</b> 13.0 U {'(Logged)' if ls['morning']['taken'] else '(Pending)'}\n"
                    f"• {eve_icon} <b>6:00 PM:</b> 13.0 U {'(Logged)' if ls['evening']['taken'] else '(Pending)'}\n"
                    f"Next: <b>{next_d['name']}</b> ({next_d['countdown']})"
                )
                keyboard = {
                    "inline_keyboard": [
                        [{"text": "✓ Done (13.0 U)", "callback_data": "gt:lantus:13.0"}]
                    ]
                }
                send_telegram_message(reply, reply_markup=keyboard, chat_id=chat_id)
                return {"status": "ok", "action": "schedule_sent", "details": {}}

            # Group chat noise filter
            if chat_type in ["group", "supergroup"] and not (
                raw_text.startswith("/") or "@" in raw_text or any(k in lower for k in ["glucose", "sugar", "insulin", "lantus", "carb", "eat", "snack", "dose", "correction", "bg", "food", "status", "schedule"])
            ):
                return {"status": "ignored", "action": "group_noise_ignored", "details": {}}

            # Fallback
            if summary:
                bg = summary["glucose"]
                reply = (
                    f"🟢 <b>{bg:.0f} mg/dL</b> • {time_str}\n"
                    f"IOB: {summary['iob']:.2f} U • Next Lantus: {summary['lantus_schedule']['next_dose']['name']} ({summary['lantus_schedule']['next_dose']['countdown']})\n"
                    f"<i>Use the buttons below to interact!</i>"
                )
            else:
                reply = "Gluco Track Assistant active. Use the menu buttons to interact."

            send_telegram_message(reply, reply_markup=MAIN_MENU_KEYBOARD, chat_id=chat_id)
            return {"status": "ok", "action": "fallback_handled", "details": {}}

    return {"status": "ok", "action": "noop", "details": {}}


# --- Long Polling Daemon Worker (Backward-Compatible Wrapper) ---

def run_polling_worker():
    """
    Dedicated background long-polling worker loop.
    Fetches updates reliably from Telegram using bot_client.
    """
    global _polling_running
    offset = 0
    client = get_gt_bot_client()

    print("[TelegramPoller] Background long-polling worker started.")
    client.delete_webhook(drop_pending_updates=False)

    while _polling_running:
        if not client.token:
            time.sleep(5)
            continue

        try:
            res = client.get_updates(offset=offset, timeout=20)
            if res.get("success"):
                results = res.get("result", [])
                for update in results:
                    offset = max(offset, update["update_id"] + 1)
                    try:
                        handle_telegram_update(update)
                    except Exception as he:
                        print(f"[TelegramPoller] Error handling update {update.get('update_id')}: {he}")
            else:
                time.sleep(3)
        except Exception:
            time.sleep(3)


def start_telegram_polling():
    """Starts the Telegram background poller thread."""
    global _polling_running, _polling_thread
    if _polling_running:
        return
    _polling_running = True
    _polling_thread = threading.Thread(target=run_polling_worker, daemon=True)
    _polling_thread.start()


def stop_telegram_polling():
    """Stops the Telegram background poller."""
    global _polling_running
    _polling_running = False
