import os
import json
import re
import time
import threading
from datetime import datetime, timezone, timedelta
import pytz
import requests
from dotenv import load_dotenv
import db
from nutrition_vision import estimate_carbohydrates_from_text, analyze_food_photo

load_dotenv()

TELEGRAM_API_BASE = "https://api.telegram.org/bot"
EST_TZ = pytz.timezone("America/New_York")

_polling_running = False
_polling_thread = None

def get_telegram_config():
    """Retrieves Telegram bot token and target chat/group ID."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        stored = db.get_system_setting("telegram_config")
        if stored and isinstance(stored, dict):
            token = token or stored.get("bot_token")
            chat_id = chat_id or stored.get("chat_id")

    return {
        "bot_token": token.strip() if token else None,
        "chat_id": str(chat_id).strip() if chat_id else None,
        "is_configured": bool(token and chat_id)
    }

def save_telegram_config(bot_token, chat_id, enabled=True):
    """Saves Telegram configuration into database."""
    db.set_system_setting("telegram_config", {
        "bot_token": bot_token.strip() if bot_token else "",
        "chat_id": str(chat_id).strip() if chat_id else "",
        "enabled": enabled,
        "updated_at": datetime.now(timezone.utc).isoformat()
    })

def send_telegram_message(text, reply_markup=None, chat_id=None, parse_mode="HTML"):
    """
    Sends a message via Telegram Bot API to a private user or group chat.
    """
    config = get_telegram_config()
    token = config.get("bot_token")
    target_chat = chat_id or config.get("chat_id")

    if not token or not target_chat:
        return {"success": False, "message": "Telegram Bot is not configured."}

    url = f"{TELEGRAM_API_BASE}{token}/sendMessage"
    payload = {
        "chat_id": target_chat,
        "text": text,
        "parse_mode": parse_mode
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        resp = requests.post(url, json=payload, timeout=12)
        if resp.ok:
            return {"success": True, "result": resp.json().get("result")}
        else:
            print(f"[TelegramBot] Send error ({resp.status_code}): {resp.text}")
            return {"success": False, "status": resp.status_code, "error": resp.text}
    except Exception as e:
        print(f"[TelegramBot] Send exception: {e}")
        return {"success": False, "error": str(e)}

def answer_callback_query(callback_query_id, text=None):
    """Acknowledges a Telegram inline button click."""
    config = get_telegram_config()
    token = config.get("bot_token")
    if not token:
        return
    url = f"{TELEGRAM_API_BASE}{token}/answerCallbackQuery"
    try:
        requests.post(url, json={"callback_query_id": callback_query_id, "text": text}, timeout=8)
    except Exception:
        pass

def edit_message_text(chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
    """Updates the text of an existing Telegram message in private or group chat."""
    config = get_telegram_config()
    token = config.get("bot_token")
    if not token:
        return
    url = f"{TELEGRAM_API_BASE}{token}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(url, json=payload, timeout=8)
    except Exception:
        pass

def download_telegram_photo(file_id):
    """Downloads a photo sent in Telegram by its file_id."""
    config = get_telegram_config()
    token = config.get("bot_token")
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

def get_user_display_name(from_dict):
    """Formats a user's name concisely (e.g. 'Alex')."""
    if not from_dict:
        return "Member"
    first = from_dict.get("first_name", "").strip()
    username = from_dict.get("username", "").strip()
    return first or username or "Member"

def get_live_patient_summary():
    """Fetches live glucose, IOB, safe carbs, corrections, and predictions."""
    try:
        latest = db.get_latest_reading()
        if not latest:
            return None

        from prediction import predict_glucose, calculate_iob, suggest_correction, suggest_carbs, calculate_safe_carb_allowance, calculate_proactive_alert, get_lantus_schedule_status
        from ml_heuristics import load_heuristics_params, get_time_of_day_bucket

        history = db.get_history(3)
        predictions = predict_glucose(history)
        doses = db.get_insulin_history(4, include_imputed=True)
        total_iob = calculate_iob(doses)

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

def compute_meal_bolus(carbs_g, summary):
    """Calculates suggested meal insulin bolus based on carbs and trajectory-aware preemptive correction."""
    if not summary or carbs_g <= 0:
        return 0.0
    
    isf = summary.get("isf", 50.0)
    csf = summary.get("csf", 4.0)
    icr = max(isf / max(csf, 1.0), 8.0) # e.g. 50 / 4 = 12.5g per 1U
    
    carb_insulin = carbs_g / icr
    correction = summary.get("correction", 0.0)
    net_bolus = max(0.0, carb_insulin + correction)
    
    return round(net_bolus, 1)

def handle_telegram_update(update):
    """
    Main entrypoint for processing incoming Telegram updates.
    """
    # 1. Handle Inline Button Clicks (Callback Queries)
    if "callback_query" in update:
        cb = update["callback_query"]
        cb_id = cb.get("id")
        cb_data = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id")
        msg_id = cb.get("message", {}).get("message_id")
        actor_name = get_user_display_name(cb.get("from"))
        now_est = datetime.now(timezone.utc).astimezone(EST_TZ)
        time_str = now_est.strftime("%I:%M %p EST")

        # A. Log Meal & Optional Bolus (e.g. "log_meal:55.0:2.5")
        if cb_data.startswith("log_meal:"):
            parts = cb_data.split(":")
            carbs = float(parts[1]) if len(parts) > 1 else 0.0
            bolus = float(parts[2]) if len(parts) > 2 else 0.0
            now_utc = datetime.now(timezone.utc)

            # Insert food log
            db.insert_food_log(carbs_g=carbs, timestamp=now_utc, food_type=f"Logged via Telegram ({actor_name})")

            # Insert insulin bolus if > 0
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

            answer_callback_query(cb_id, f"Recorded {carbs:.0f}g carbs & {bolus:.1f}U bolus.")
            bolus_text = f" + <b>{bolus:.1f}U</b>" if bolus > 0 else ""
            edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=f"✅ <b>{actor_name}: Recorded {carbs:.0f}g carbs{bolus_text}</b> • {time_str}"
            )
            return {"status": "ok", "action": "meal_logged"}

        # B. Log Lantus Scheduled Dose (13U)
        elif cb_data.startswith("took_lantus:"):
            try:
                units = float(cb_data.split(":")[1])
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
            answer_callback_query(cb_id, f"Recorded {units}U Lantus.")
            db.set_system_setting("pending_compliance_check", None)

            edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=f"✅ <b>{actor_name}: {units:.1f} U Lantus recorded</b> • {time_str}"
            )
            return {"status": "ok", "action": "lantus_logged"}

        # C. Log Rapid / Correction Dose
        elif cb_data.startswith("took_correction:"):
            try:
                units = float(cb_data.split(":")[1])
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
            answer_callback_query(cb_id, f"Recorded {units}U.")
            db.set_system_setting("pending_compliance_check", None)

            edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=f"✅ <b>{actor_name}: {units:.1f} U rapid recorded</b> • {time_str}"
            )
            return {"status": "ok", "action": "correction_logged"}

        # D. Snooze / Later (Calm, non-demanding space)
        elif cb_data.startswith("snooze:"):
            try:
                mins = int(cb_data.split(":")[1])
            except Exception:
                mins = 60
            answer_callback_query(cb_id, "Took note.")
            
            snooze_until = (datetime.now(timezone.utc) + timedelta(minutes=mins)).isoformat()
            pending = db.get_system_setting("pending_compliance_check") or {}
            pending["snooze_until"] = snooze_until
            db.set_system_setting("pending_compliance_check", pending)

            edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=f"⏳ <b>{actor_name}: Noted</b> — checking back later whenever you're ready."
            )
            return {"status": "ok", "action": "snoozed"}

        # E. Skip / Dismiss
        elif cb_data in ["skip_dose", "dismiss"]:
            answer_callback_query(cb_id, "Noted.")
            db.set_system_setting("pending_compliance_check", None)
            edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=f"✕ <i>Noted for today by {actor_name}</i>"
            )
            return {"status": "ok", "action": "dismissed"}

        return {"status": "ok"}

    # 2. Handle Photo Message (Computer Vision Meal Carbohydrate Estimation)
    if "message" in update and "photo" in update["message"]:
        msg = update["message"]
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        caption = msg.get("caption", "").strip()
        photos = msg.get("photo", [])
        
        if photos:
            best_photo = photos[-1]
            file_id = best_photo.get("file_id")
            
            photo_bytes = download_telegram_photo(file_id)
            if photo_bytes:
                analysis = analyze_food_photo(photo_bytes, caption=caption)
                summary = get_live_patient_summary()
                carbs_g = analysis.get("carbs_g", 35.0)
                bolus = compute_meal_bolus(carbs_g, summary)
                
                item_parts = []
                for it in analysis.get("items", []):
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
                        [{"text": f"✓ Log {carbs_g:.0f}g + {bolus:.1f}U", "callback_data": f"log_meal:{carbs_g:.1f}:{bolus:.1f}"}],
                        [{"text": f"Log {carbs_g:.0f}g Only", "callback_data": f"log_meal:{carbs_g:.1f}:0.0"}, {"text": "✕", "callback_data": "dismiss"}]
                    ]
                }
                send_telegram_message(card_text, reply_markup=keyboard, chat_id=chat_id)
                return {"status": "ok", "action": "photo_analyzed"}

    # 3. Handle Group Invitations / Bot Added to Group
    if "message" in update and "new_chat_members" in update["message"]:
        msg = update["message"]
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        chat_title = chat.get("title") or "Care Circle"

        config = get_telegram_config()
        save_telegram_config(config.get("bot_token") or "", chat_id)

        welcome_text = (
            f"🎉 <b>Connected to {chat_title}!</b>\n"
            f"Active channel for Lantus routine reminders (6 AM & 6 PM EST), meal photo carb counts, and urgent alerts.\n"
            f"Commands: <code>/status</code>, <code>/carbs</code>, <code>/dose</code>, <code>/schedule</code>"
        )
        send_telegram_message(welcome_text, chat_id=chat_id)
        return {"status": "ok"}

    # 4. Handle Text Messages, Food Logging & Conversational Commands
    if "message" in update and "text" in update["message"]:
        msg = update["message"]
        chat = msg.get("chat", {})
        chat_id = msg.get("chat", {}).get("id")
        chat_type = chat.get("type", "private")
        raw_text = msg.get("text", "").strip()
        sender_name = get_user_display_name(msg.get("from"))

        clean_text = re.sub(r'@[A-Za-z0-9_]+bot', '', raw_text, flags=re.IGNORECASE).strip()
        lower = clean_text.lower()

        config = get_telegram_config()
        if not config.get("chat_id") or lower.startswith("/setgroup") or lower.startswith("/link"):
            save_telegram_config(config.get("bot_token") or "", chat_id)
            if lower.startswith("/setgroup") or lower.startswith("/link"):
                send_telegram_message(f"✅ <b>Linked Chat ID:</b> <code>{chat_id}</code>", chat_id=chat_id)
                return {"status": "ok"}

        summary = get_live_patient_summary()
        now_est = datetime.now(timezone.utc).astimezone(EST_TZ)
        time_str = now_est.strftime("%I:%M %p EST")

        # A. Direct Text Food Logging (e.g. "ate 2 slices toast and an egg")
        is_food_log = any(lower.startswith(k) for k in ["ate ", "had ", "eating ", "having ", "log meal ", "log food ", "food: ", "meal: "]) or \
                      ("carbs" in lower and any(v in lower for v in ["ate", "had", "eating", "having", "taking", "log"]))

        if is_food_log:
            estimation = estimate_carbohydrates_from_text(clean_text)
            carbs_g = estimation.get("carbs_g", 30.0)
            bolus = compute_meal_bolus(carbs_g, summary)

            item_parts = []
            for it in estimation.get("items", []):
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
                    [{"text": f"✓ Log {carbs_g:.0f}g + {bolus:.1f}U", "callback_data": f"log_meal:{carbs_g:.1f}:{bolus:.1f}"}],
                    [{"text": f"Log {carbs_g:.0f}g Only", "callback_data": f"log_meal:{carbs_g:.1f}:0.0"}, {"text": "✕", "callback_data": "dismiss"}]
                ]
            }
            send_telegram_message(card_text, reply_markup=keyboard, chat_id=chat_id)
            return {"status": "ok"}

        # B. Direct Dose Logging (e.g. "took 13u lantus")
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
            return {"status": "ok"}

        rapid_match = re.search(r'(?:took|injected|logged|take)\s*(\d+(?:\.\d+)?)\s*(?:u|units)?\s*(?:of)?\s*(?:rapid|novolog|humalog|correction|bolus)', lower)
        if rapid_match:
            try:
                units = float(rapid_match.group(1))
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
            return {"status": "ok"}

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
            send_telegram_message(reply, chat_id=chat_id)
            return {"status": "ok"}

        # /status, /bg, or glucose questions
        if lower.startswith("/status") or lower.startswith("/bg") or any(q in lower for q in ["what's my blood sugar", "what is my bg", "what is the blood sugar", "current bg", "blood sugar", "glucose level"]):
            if not summary:
                send_telegram_message("⚠️ No live glucose data available.", chat_id=chat_id)
                return {"status": "ok"}

            bg = summary["glucose"]
            iob = summary["iob"]
            preds = summary.get("predictions", [])
            f60 = next((p['value'] for p in preds if p['minutes'] == 60), bg)
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
            return {"status": "ok"}

        # /carbs or questions about food/snacks
        if lower.startswith("/carbs") or any(q in lower for q in ["can i eat", "can they eat", "snack", "hungry", "is it safe to eat"]):
            if not summary:
                send_telegram_message("⚠️ No live glucose data available.", chat_id=chat_id)
                return {"status": "ok"}

            sc = summary["safe_carbs"]
            bg = summary["glucose"]

            if sc["type"] == "rescue":
                reply = (
                    f"🚨 <b>Low Trajectory: {bg:.0f} mg/dL</b> ({time_str})\n"
                    f"👉 Take <b>~{int(sc['grams'])}g fast-acting carbs</b> to stay steady."
                )
            elif sc["type"] == "restricted":
                reply = (
                    f"⚠️ <b>Elevated Glucose: {bg:.0f} mg/dL</b>\n"
                    f"Carb buffer is limited right now. Low or zero-carb snacks are best until levels settle."
                )
            else:
                reply = (
                    f"🍎 <b>Safe Snack: ~{int(sc['grams'])}g carbs</b>\n"
                    f"BG: <b>{bg:.0f} mg/dL</b> • Active IOB: {summary['iob']:.2f} U"
                )
            send_telegram_message(reply, chat_id=chat_id)
            return {"status": "ok"}

        # /dose or /correction
        if lower.startswith("/dose") or lower.startswith("/correction") or any(q in lower for q in ["how much insulin", "need a bolus", "correct", "high", "take insulin"]):
            if not summary:
                send_telegram_message("⚠️ No live data available.", chat_id=chat_id)
                return {"status": "ok"}

            corr = summary["correction"]
            bg = summary["glucose"]
            iob = summary["iob"]

            if corr > 0.0:
                reply = (
                    f"💉 <b>Recommended Correction: {corr:.1f} U</b>\n"
                    f"BG: <b>{bg:.0f} mg/dL</b> (Target: 120) • IOB: {iob:.2f} U"
                )
                keyboard = {
                    "inline_keyboard": [
                        [{"text": f"✓ Log {corr:.1f} U", "callback_data": f"took_correction:{corr:.1f}"}],
                        [{"text": "⏳ Later", "callback_data": "snooze:60"}, {"text": "✕ Skip", "callback_data": "skip_dose"}]
                    ]
                }
                send_telegram_message(reply, reply_markup=keyboard, chat_id=chat_id)
            else:
                reply = (
                    f"🟢 <b>No Correction Needed (0.0 U)</b>\n"
                    f"BG: <b>{bg:.0f} mg/dL</b> • IOB: {iob:.2f} U"
                )
                send_telegram_message(reply, chat_id=chat_id)
            return {"status": "ok"}

        # /schedule or /lantus
        if lower.startswith("/schedule") or lower.startswith("/lantus") or any(q in lower for q in ["when is the next dose", "next lantus", "lantus schedule"]):
            if not summary:
                send_telegram_message("⚠️ No schedule data available.", chat_id=chat_id)
                return {"status": "ok"}

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
                    [{"text": "✓ Done (13.0 U)", "callback_data": "took_lantus:13.0"}]
                ]
            }
            send_telegram_message(reply, reply_markup=keyboard, chat_id=chat_id)
            return {"status": "ok"}

        # In group chats, ignore general chatter unless addressed or has diabetes keywords
        if chat_type in ["group", "supergroup"] and not (raw_text.startswith("/") or "@" in raw_text or any(k in lower for k in ["glucose", "sugar", "insulin", "lantus", "carb", "eat", "snack", "dose", "correction", "bg", "food"])):
            return {"status": "ignored"}

        # Fallback short status
        if summary:
            bg = summary["glucose"]
            reply = (
                f"🟢 <b>{bg:.0f} mg/dL</b> • {time_str}\n"
                f"IOB: {summary['iob']:.2f} U • Next Lantus: {summary['lantus_schedule']['next_dose']['name']} ({summary['lantus_schedule']['next_dose']['countdown']})\n"
                f"<i>Use <code>/status</code>, <code>/carbs</code>, <code>/dose</code>, or send a meal photo!</i>"
            )
        else:
            reply = "Gluco Track Assistant active. Use /status to view readings."
        
        send_telegram_message(reply, chat_id=chat_id)
        return {"status": "ok"}

    return {"status": "ok"}

# --- Long Polling Daemon Worker ---

def run_polling_worker():
    """
    Dedicated background long-polling worker loop.
    Fetches updates reliably from Telegram without requiring external webhooks or public ports.
    """
    global _polling_running
    offset = 0
    webhook_deleted = False

    print("[TelegramPoller] Background long-polling worker started.")

    while _polling_running:
        config = get_telegram_config()
        token = config.get("bot_token")

        if not token:
            time.sleep(5)
            continue

        if not webhook_deleted:
            try:
                del_url = f"{TELEGRAM_API_BASE}{token}/deleteWebhook"
                requests.post(del_url, json={"drop_pending_updates": False}, timeout=10)
                webhook_deleted = True
                print("[TelegramPoller] Confirmed clean webhook state for polling.")
            except Exception as e:
                print(f"[TelegramPoller] Webhook delete notice: {e}")

        url = f"{TELEGRAM_API_BASE}{token}/getUpdates"
        params = {
            "offset": offset,
            "timeout": 20,
            "allowed_updates": ["message", "callback_query", "my_chat_member"]
        }

        try:
            resp = requests.get(url, params=params, timeout=25)
            if resp.ok:
                data = resp.json()
                results = data.get("result", [])
                for update in results:
                    offset = max(offset, update["update_id"] + 1)
                    try:
                        handle_telegram_update(update)
                    except Exception as he:
                        print(f"[TelegramPoller] Error handling update {update.get('update_id')}: {he}")
            else:
                time.sleep(3)
        except requests.exceptions.Timeout:
            continue
        except Exception as e:
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
