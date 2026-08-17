import os
import json
import re
from datetime import datetime, timezone, timedelta
import pytz
import requests
from dotenv import load_dotenv
import db

load_dotenv()

TELEGRAM_API_BASE = "https://api.telegram.org/bot"

def get_telegram_config():
    """Retrieves Telegram bot token and target chat ID."""
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
    Sends a message via Telegram Bot API.
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
            return {"success": False, "status": resp.status_code, "error": resp.text}
    except Exception as e:
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
    """Updates the text of an existing Telegram message."""
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
        correction = suggest_correction(latest['value'], total_iob, target_glucose=120.0, isf=isf)
        proactive_alert = calculate_proactive_alert(latest['value'], predictions, total_iob, isf=isf, csf=csf)
        lantus_schedule = get_lantus_schedule_status()

        return {
            "glucose": latest['value'],
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

def handle_telegram_update(update):
    """
    Main entrypoint for processing incoming Telegram Webhook updates.
    """
    # 1. Handle Inline Button Clicks (Callback Queries)
    if "callback_query" in update:
        cb = update["callback_query"]
        cb_id = cb.get("id")
        cb_data = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id")
        msg_id = cb.get("message", {}).get("message_id")

        # A. Log Lantus Scheduled Dose
        if cb_data.startswith("took_lantus:"):
            units = float(cb_data.split(":")[1])
            now = datetime.now(timezone.utc)
            dose_dict = {
                "timestamp": now,
                "rapid_acting": 0.0,
                "long_acting": units,
                "meal": 0.0,
                "correction": 0.0,
                "user_change": 0.0,
                "device": "Telegram Interactive Confirmation",
                "serial_number": None
            }
            db.insert_insulin_doses([dose_dict])
            answer_callback_query(cb_id, f"Logged {units}U Lantus!")
            
            # Clear pending follow-up for this dose
            db.set_system_setting("pending_compliance_check", None)

            # Update message to show confirmation
            edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=(
                    f"<b>✅ Dose Confirmed & Logged</b>\n\n"
                    f"Recorded <b>{units:.1f} U Lantus</b> into Gluco Track at {now.strftime('%I:%M %p')}.\n\n"
                    f"<i>Next scheduled dose: in 12 hours.</i>"
                )
            )
            return {"status": "ok", "action": "lantus_logged"}

        # B. Log Rapid / Correction Dose
        elif cb_data.startswith("took_correction:"):
            units = float(cb_data.split(":")[1])
            now = datetime.now(timezone.utc)
            dose_dict = {
                "timestamp": now,
                "rapid_acting": units,
                "long_acting": 0.0,
                "meal": 0.0,
                "correction": units,
                "user_change": 0.0,
                "device": "Telegram Interactive Confirmation",
                "serial_number": None
            }
            db.insert_insulin_doses([dose_dict])
            answer_callback_query(cb_id, f"Logged {units}U Correction!")
            
            db.set_system_setting("pending_compliance_check", None)

            edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=(
                    f"<b>✅ Correction Confirmed & Logged</b>\n\n"
                    f"Recorded <b>{units:.1f} U Rapid Correction</b> at {now.strftime('%I:%M %p')}.\n"
                    f"Active IOB is now updating in the predictive model."
                )
            )
            return {"status": "ok", "action": "correction_logged"}

        # C. Snooze Reminder for 15 minutes
        elif cb_data.startswith("snooze:"):
            mins = int(cb_data.split(":")[1])
            answer_callback_query(cb_id, f"Snoozed for {mins} minutes.")
            
            # Set pending reminder
            snooze_until = (datetime.now(timezone.utc) + timedelta(minutes=mins)).isoformat()
            pending = db.get_system_setting("pending_compliance_check") or {}
            pending["snooze_until"] = snooze_until
            db.set_system_setting("pending_compliance_check", pending)

            edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=f"<b>⏳ Reminder Snoozed</b>\n\nI will check back with you in {mins} minutes!"
            )
            return {"status": "ok", "action": "snoozed"}

        # D. Skip Dose
        elif cb_data == "skip_dose":
            answer_callback_query(cb_id, "Dose skipped.")
            db.set_system_setting("pending_compliance_check", None)
            edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text="<b>❌ Dose Skipped</b>\n\nAcknowledged. Please monitor your blood sugar closely for any rising trends."
            )
            return {"status": "ok", "action": "skipped"}

        return {"status": "ok"}

    # 2. Handle Text Messages & Conversational Commands
    if "message" in update and "text" in update["message"]:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg["text"].strip()
        lower = text.lower()

        # Update saved chat_id if not yet configured
        config = get_telegram_config()
        if not config.get("chat_id"):
            save_telegram_config(config.get("bot_token") or "", chat_id)

        summary = get_live_patient_summary()

        # /start or /help
        if lower.startswith("/start") or lower.startswith("/help"):
            reply = (
                "👋 <b>Welcome to Gluco Track Assistant!</b>\n\n"
                "I actively monitor your blood sugar trends, manage your Lantus dosing schedule, follow up on compliance, and answer food & correction questions.\n\n"
                "<b>Quick Commands:</b>\n"
                "📊 <code>/status</code> — Live glucose, trend, IOB, and trajectory\n"
                "🍎 <code>/carbs</code> — Safe snack carb allowance & rescue carbs\n"
                "💉 <code>/dose</code> — Correction & insulin recommendation\n"
                "⏰ <code>/schedule</code> — Twice-daily Lantus (13U) schedule & countdown\n\n"
                "<i>You can also ask me anything in plain English! (e.g. 'Can I eat an apple?', 'What's my BG?', 'How many carbs can I have?')</i>"
            )
            send_telegram_message(reply, chat_id=chat_id)
            return {"status": "ok"}

        # /status or /bg
        if lower.startswith("/status") or lower.startswith("/bg") or "what's my blood sugar" in lower or "what is my bg" in lower or "current reading" in lower:
            if not summary:
                send_telegram_message("⚠️ No live glucose data found in database. Make sure Libre sync is active.", chat_id=chat_id)
                return {"status": "ok"}

            bg = summary["glucose"]
            iob = summary["iob"]
            sc = summary["safe_carbs"]
            corr = summary["correction"]
            preds = summary["predictions"]

            f60 = next((p['value'] for p in preds if p['minutes'] == 60), bg)
            f90 = next((p['value'] for p in preds if p['minutes'] == 90), bg)

            status_emoji = "🟢" if 70 <= bg <= 160 else ("🔴" if bg < 70 else "🟡")
            
            reply = (
                f"{status_emoji} <b>Current Blood Sugar: {bg:.0f} mg/dL</b>\n\n"
                f"• <b>Active IOB:</b> {iob:.2f} U\n"
                f"• <b>Forecast:</b> 60m: {f60:.0f} mg/dL | 90m: {f90:.0f} mg/dL\n"
                f"• <b>Safe Carb Allowance:</b> {sc.get('label', '--')}\n"
                f"• <b>Correction Needed:</b> {corr:.1f} U\n\n"
                f"<i>{summary['proactive_alert'].get('message', '')}</i>"
            )
            send_telegram_message(reply, chat_id=chat_id)
            return {"status": "ok"}

        # /carbs or questions about food/snacks
        if lower.startswith("/carbs") or "can i eat" in lower or "snack" in lower or "carbs" in lower or "hungry" in lower or "apple" in lower or "banana" in lower or "food" in lower:
            if not summary:
                send_telegram_message("⚠️ No live glucose data to calculate carb allowance.", chat_id=chat_id)
                return {"status": "ok"}

            sc = summary["safe_carbs"]
            bg = summary["glucose"]
            bucket = summary["bucket"].capitalize()

            if sc["type"] == "rescue":
                reply = (
                    f"🚨 <b>Rescue Carbs Required!</b>\n\n"
                    f"Current glucose is <b>{bg:.0f} mg/dL</b> with low trajectory.\n\n"
                    f"👉 Please consume <b>~{int(sc['grams'])}g of fast-acting carbohydrates</b> (juice, glucose tabs, honey) immediately to raise and stabilize your blood sugar."
                )
            elif sc["type"] == "restricted":
                reply = (
                    f"⚠️ <b>Elevated Glucose ({bg:.0f} mg/dL)</b>\n\n"
                    f"Carb intake should be limited right now. Opt for zero or very low carb snacks (nuts, cheese, celery, water) until your level normalizes."
                )
            else:
                reply = (
                    f"🍎 <b>Safe Carb Snack Allowance: ~{int(sc['grams'])}g</b>\n\n"
                    f"• <b>Current Glucose:</b> {bg:.0f} mg/dL ({bucket} window)\n"
                    f"• <b>Active Insulin (IOB):</b> {summary['iob']:.2f} U\n"
                    f"• <b>Guidance:</b> You can safely enjoy a snack containing up to <b>{int(sc['grams'])}g of carbs</b> without experiencing a sharp spike over 160 mg/dL.\n\n"
                    f"<i>{sc.get('explanation', '')}</i>"
                )
            send_telegram_message(reply, chat_id=chat_id)
            return {"status": "ok"}

        # /dose or /correction
        if lower.startswith("/dose") or lower.startswith("/correction") or "how much insulin" in lower or "need a bolus" in lower or "correct" in lower:
            if not summary:
                send_telegram_message("⚠️ No live glucose data to compute correction.", chat_id=chat_id)
                return {"status": "ok"}

            corr = summary["correction"]
            bg = summary["glucose"]
            iob = summary["iob"]
            isf = summary["isf"]

            if corr > 0.0:
                reply = (
                    f"💉 <b>Recommended Correction: {corr:.1f} U</b>\n\n"
                    f"• <b>Current Glucose:</b> {bg:.0f} mg/dL (Target: 120 mg/dL)\n"
                    f"• <b>Active IOB:</b> {iob:.2f} U (subtracted to prevent stacking)\n"
                    f"• <b>Current ISF:</b> 1U drops ~{isf:.0f} mg/dL\n\n"
                    f"Take <b>{corr:.1f} U</b> rapid-acting insulin to bring your level back to target."
                )
                keyboard = {
                    "inline_keyboard": [
                        [{"text": f"✓ Log {corr:.1f} U Correction", "callback_data": f"took_correction:{corr:.1f}"}],
                        [{"text": "⏳ Remind in 15m", "callback_data": "snooze:15"}, {"text": "❌ Skip", "callback_data": "skip_dose"}]
                    ]
                }
                send_telegram_message(reply, reply_markup=keyboard, chat_id=chat_id)
            else:
                reply = (
                    f"🟢 <b>No Correction Needed (0.0 U)</b>\n\n"
                    f"Current glucose is <b>{bg:.0f} mg/dL</b> with <b>{iob:.2f} U</b> active IOB. Your insulin coverage is sufficient."
                )
                send_telegram_message(reply, chat_id=chat_id)
            return {"status": "ok"}

        # /schedule or /lantus
        if lower.startswith("/schedule") or lower.startswith("/lantus") or "when is my next dose" in lower or "lantus" in lower:
            if not summary:
                send_telegram_message("⚠️ No schedule data available.", chat_id=chat_id)
                return {"status": "ok"}

            ls = summary["lantus_schedule"]
            next_d = ls["next_dose"]
            morn_icon = "✅" if ls["morning"]["taken"] else "⏳"
            eve_icon = "✅" if ls["evening"]["taken"] else "⏳"

            reply = (
                f"⏰ <b>Lantus Dosing Regimen (2x Daily / 26U Total)</b>\n\n"
                f"• {morn_icon} <b>Morning (6:00 AM):</b> 13.0 U {'(Logged)' if ls['morning']['taken'] else '(Pending)'}\n"
                f"• {eve_icon} <b>Evening (6:00 PM):</b> 13.0 U {'(Logged)' if ls['evening']['taken'] else '(Pending)'}\n\n"
                f"👉 <b>Next Scheduled Dose:</b> {next_d['name']} {next_d['countdown']}"
            )
            keyboard = {
                "inline_keyboard": [
                    [{"text": "✓ Log 13.0 U Lantus Now", "callback_data": "took_lantus:13.0"}]
                ]
            }
            send_telegram_message(reply, reply_markup=keyboard, chat_id=chat_id)
            return {"status": "ok"}

        # General conversational fallback
        if summary:
            bg = summary["glucose"]
            sc = summary["safe_carbs"]
            reply = (
                f"I hear you! Here is your live summary:\n\n"
                f"• <b>Blood Sugar:</b> {bg:.0f} mg/dL\n"
                f"• <b>Safe Carb Intake:</b> {sc.get('label', 'Normal')}\n"
                f"• <b>Active IOB:</b> {summary['iob']:.2f} U\n"
                f"• <b>Next Lantus Dose:</b> {summary['lantus_schedule']['next_dose']['name']} ({summary['lantus_schedule']['next_dose']['countdown']})\n\n"
                f"<i>Feel free to ask for <code>/carbs</code>, <code>/dose</code>, or <code>/status</code> anytime!</i>"
            )
        else:
            reply = "I'm your Gluco Track Assistant! Send /status to view your current metrics."
        
        send_telegram_message(reply, chat_id=chat_id)
        return {"status": "ok"}

    return {"status": "ok"}
