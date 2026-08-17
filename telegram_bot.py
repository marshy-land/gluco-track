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

def get_user_display_name(from_dict):
    """Formats a user's name for group transparency (e.g. 'John (@johndoe)')."""
    if not from_dict:
        return "Care team member"
    first = from_dict.get("first_name", "").strip()
    last = from_dict.get("last_name", "").strip()
    username = from_dict.get("username", "").strip()
    
    full_name = f"{first} {last}".strip() or "Care team member"
    if username:
        return f"{full_name} (@{username})"
    return full_name

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
        lantus_schedule = get_lantus_schedule_status(timezone_str="America/New_York")

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
    Main entrypoint for processing incoming Telegram Webhook or Long-Polling updates.
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

        # A. Log Lantus Scheduled Dose (13U)
        if cb_data.startswith("took_lantus:"):
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
            answer_callback_query(cb_id, f"Logged {units}U Lantus!")
            
            # Clear pending follow-up for this dose
            db.set_system_setting("pending_compliance_check", None)

            # Update message in group/chat
            edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=(
                    f"<b>✅ Dose Confirmed & Logged</b>\n\n"
                    f"<b>{actor_name}</b> recorded <b>{units:.1f} U Lantus</b> into Gluco Track at {now_est.strftime('%I:%M %p EST')}.\n\n"
                    f"<i>Next scheduled dose: in ~12 hours.</i>"
                )
            )
            return {"status": "ok", "action": "lantus_logged"}

        # B. Log Rapid / Correction Dose
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
            answer_callback_query(cb_id, f"Logged {units}U Correction!")
            
            db.set_system_setting("pending_compliance_check", None)

            edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=(
                    f"<b>✅ Correction Confirmed & Logged</b>\n\n"
                    f"<b>{actor_name}</b> logged <b>{units:.1f} U Rapid Correction</b> at {now_est.strftime('%I:%M %p EST')}.\n"
                    f"Active IOB is now updating in the predictive model."
                )
            )
            return {"status": "ok", "action": "correction_logged"}

        # C. Snooze Reminder for 15 minutes
        elif cb_data.startswith("snooze:"):
            try:
                mins = int(cb_data.split(":")[1])
            except Exception:
                mins = 15
            answer_callback_query(cb_id, f"Snoozed for {mins} minutes.")
            
            snooze_until = (datetime.now(timezone.utc) + timedelta(minutes=mins)).isoformat()
            pending = db.get_system_setting("pending_compliance_check") or {}
            pending["snooze_until"] = snooze_until
            db.set_system_setting("pending_compliance_check", pending)

            edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=f"<b>⏳ Reminder Snoozed by {actor_name}</b>\n\nI will check back with the group in {mins} minutes ({ (now_est + timedelta(minutes=mins)).strftime('%I:%M %p EST') })!"
            )
            return {"status": "ok", "action": "snoozed"}

        # D. Skip Dose
        elif cb_data == "skip_dose":
            answer_callback_query(cb_id, "Dose skipped.")
            db.set_system_setting("pending_compliance_check", None)
            edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=f"<b>❌ Dose Skipped (marked by {actor_name})</b>\n\nAcknowledged. Please monitor blood sugar closely for rising trends."
            )
            return {"status": "ok", "action": "skipped"}

        return {"status": "ok"}

    # 2. Handle Group Invitations / Bot Added to Group
    if "message" in update and "new_chat_members" in update["message"]:
        msg = update["message"]
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        chat_title = chat.get("title") or "Care Circle Group"

        config = get_telegram_config()
        # Automatically register this group chat ID for broadcasts
        save_telegram_config(config.get("bot_token") or "", chat_id)

        welcome_text = (
            f"🎉 <b>Hello {chat_title}!</b>\n\n"
            f"I am your <b>Gluco Track Care Circle Assistant</b>. I have linked this group (ID: <code>{chat_id}</code>) to broadcast notifications to everyone here!\n\n"
            f"<b>What I will do for the care team:</b>\n"
            f"• 🌅 <b>6:00 AM EST & 🌇 6:00 PM EST:</b> Scheduled Lantus 13.0 U dose reminders\n"
            f"• ⏱️ <b>15-Minute Follow-Ups:</b> If a dose hasn't been logged in the database\n"
            f"• ⚠️ <b>Proactive Alerts:</b> Early warnings >1 hour out for projected highs or lows\n"
            f"• 💬 <b>Team Q&A:</b> Anyone can ask questions or type <code>/status</code>, <code>/carbs</code>, <code>/dose</code>, <code>/schedule</code>!\n\n"
            f"<i>Tip: If BotFather group privacy is enabled, send <code>/setprivacy</code> -> <code>Disable</code> to @BotFather so I can read messages without needing explicit @mentions.</i>"
        )
        send_telegram_message(welcome_text, chat_id=chat_id)
        return {"status": "ok"}

    # 3. Handle Text Messages & Conversational Commands
    if "message" in update and "text" in update["message"]:
        msg = update["message"]
        chat = msg.get("chat", {})
        chat_id = msg.get("chat", {}).get("id")
        chat_type = chat.get("type", "private")
        raw_text = msg.get("text", "").strip()
        sender_name = get_user_display_name(msg.get("from"))

        # Strip bot mentions like /status@MyBot or @MyBot what is the bg?
        clean_text = re.sub(r'@[A-Za-z0-9_]+bot', '', raw_text, flags=re.IGNORECASE).strip()
        lower = clean_text.lower()

        # Update saved chat_id if not yet configured, or if /setgroup is used
        config = get_telegram_config()
        if not config.get("chat_id") or lower.startswith("/setgroup") or lower.startswith("/link"):
            save_telegram_config(config.get("bot_token") or "", chat_id)
            if lower.startswith("/setgroup") or lower.startswith("/link"):
                send_telegram_message(f"✅ <b>Linked Chat ID:</b> <code>{chat_id}</code> as the active broadcast channel.", chat_id=chat_id)
                return {"status": "ok"}

        summary = get_live_patient_summary()
        now_est = datetime.now(timezone.utc).astimezone(EST_TZ)

        # Direct Natural Language Dose Logging (e.g. "took 13u lantus", "logged 13u", "injected 4u rapid")
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
                "device": f"Telegram Text ({sender_name})",
                "serial_number": None
            }
            db.insert_insulin_doses([dose_dict])
            db.set_system_setting("pending_compliance_check", None)

            reply = (
                f"<b>✅ Lantus Dose Recorded</b>\n\n"
                f"Logged <b>{units:.1f} U Lantus</b> into Gluco Track at {now_est.strftime('%I:%M %p EST')} (reported by {sender_name}).\n\n"
                f"<i>Next scheduled dose: in ~12 hours.</i>"
            )
            send_telegram_message(reply, chat_id=chat_id)
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
                "device": f"Telegram Text ({sender_name})",
                "serial_number": None
            }
            db.insert_insulin_doses([dose_dict])
            db.set_system_setting("pending_compliance_check", None)

            reply = (
                f"<b>✅ Rapid Bolus Recorded</b>\n\n"
                f"Logged <b>{units:.1f} U Rapid Insulin</b> at {now_est.strftime('%I:%M %p EST')} (reported by {sender_name}).\n"
                f"Active IOB is now calculating in predictive projections."
            )
            send_telegram_message(reply, chat_id=chat_id)
            return {"status": "ok"}

        # /start or /help
        if lower.startswith("/start") or lower.startswith("/help"):
            is_group = chat_type in ["group", "supergroup"]
            group_note = f"\n👥 <b>Connected Group:</b> <code>{chat_id}</code>\n" if is_group else ""
            reply = (
                f"👋 <b>Welcome to Gluco Track Assistant!</b>{group_note}\n"
                "I actively monitor blood sugar trends, manage the twice-daily Lantus schedule (6 AM & 6 PM EST), follow up on compliance, and answer food & correction questions.\n\n"
                "<b>Quick Commands:</b>\n"
                "📊 <code>/status</code> — Live glucose, trend, IOB, and trajectory\n"
                "🍎 <code>/carbs</code> — Safe snack carb allowance & rescue carbs\n"
                "💉 <code>/dose</code> — Correction & insulin recommendations\n"
                "⏰ <code>/schedule</code> — Lantus (2x13U EST) schedule & countdown\n\n"
                "<b>Natural Chat Examples:</b>\n"
                "• <i>'What is the blood sugar right now?'</i>\n"
                "• <i>'Can they eat a banana for a snack?'</i>\n"
                "• <i>'Do we need a correction bolus?'</i>\n"
                "• <i>'Took 13u lantus'</i> (automatically logs the dose!)"
            )
            send_telegram_message(reply, chat_id=chat_id)
            return {"status": "ok"}

        # /status, /bg, or glucose questions
        if lower.startswith("/status") or lower.startswith("/bg") or any(q in lower for q in ["what's my blood sugar", "what is my bg", "what is the blood sugar", "what's the blood sugar", "current reading", "current bg", "blood sugar", "glucose level", "how is the blood sugar", "how are they doing"]):
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
            time_str = now_est.strftime("%I:%M %p EST")
            
            reply = (
                f"{status_emoji} <b>Current Blood Sugar: {bg:.0f} mg/dL</b> ({time_str})\n\n"
                f"• <b>Active IOB:</b> {iob:.2f} U\n"
                f"• <b>Forward Forecast:</b> +60m: <b>{f60:.0f} mg/dL</b> | +90m: <b>{f90:.0f} mg/dL</b>\n"
                f"• <b>Safe Carb Allowance:</b> {sc.get('label', '--')}\n"
                f"• <b>Correction Needed:</b> {corr:.1f} U\n\n"
                f"<i>{summary['proactive_alert'].get('message', '')}</i>"
            )
            send_telegram_message(reply, chat_id=chat_id)
            return {"status": "ok"}

        # /carbs or food questions
        if lower.startswith("/carbs") or any(q in lower for q in ["can i eat", "can they eat", "snack", "carbs", "hungry", "apple", "banana", "bread", "toast", "pasta", "food", "eat", "meal"]):
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
                    f"👉 Please consume <b>~{int(sc['grams'])}g of fast-acting carbohydrates</b> (juice, glucose tabs, honey) immediately to raise and stabilize blood sugar."
                )
            elif sc["type"] == "restricted":
                reply = (
                    f"⚠️ <b>Elevated Glucose ({bg:.0f} mg/dL)</b>\n\n"
                    f"Carb intake should be limited right now. Opt for zero or very low carb snacks (nuts, cheese, celery, water) until levels normalize."
                )
            else:
                reply = (
                    f"🍎 <b>Safe Carb Snack Allowance: ~{int(sc['grams'])}g</b>\n\n"
                    f"• <b>Current Glucose:</b> {bg:.0f} mg/dL ({bucket} window EST)\n"
                    f"• <b>Active Insulin (IOB):</b> {summary['iob']:.2f} U\n"
                    f"• <b>Guidance:</b> A snack containing up to <b>{int(sc['grams'])}g of carbs</b> can be enjoyed safely without spiking over 160 mg/dL.\n\n"
                    f"<i>{sc.get('explanation', '')}</i>"
                )
            send_telegram_message(reply, chat_id=chat_id)
            return {"status": "ok"}

        # /dose or /correction
        if lower.startswith("/dose") or lower.startswith("/correction") or any(q in lower for q in ["how much insulin", "need a bolus", "correct", "high", "take insulin", "correction dose"]):
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
                    f"• <b>Active IOB:</b> {iob:.2f} U (subtracted to prevent insulin stacking)\n"
                    f"• <b>Current ISF:</b> 1U drops ~{isf:.0f} mg/dL\n\n"
                    f"Recommended: <b>{corr:.1f} U</b> rapid-acting insulin to bring level back to target."
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
                    f"Current glucose is <b>{bg:.0f} mg/dL</b> with <b>{iob:.2f} U</b> active IOB. Insulin coverage is currently sufficient."
                )
                send_telegram_message(reply, chat_id=chat_id)
            return {"status": "ok"}

        # /schedule or /lantus
        if lower.startswith("/schedule") or lower.startswith("/lantus") or any(q in lower for q in ["when is the next dose", "when is my next dose", "next lantus", "lantus schedule", "dosing schedule"]):
            if not summary:
                send_telegram_message("⚠️ No schedule data available.", chat_id=chat_id)
                return {"status": "ok"}

            ls = summary["lantus_schedule"]
            next_d = ls["next_dose"]
            morn_icon = "✅" if ls["morning"]["taken"] else "⏳"
            eve_icon = "✅" if ls["evening"]["taken"] else "⏳"

            reply = (
                f"⏰ <b>Lantus Dosing Regimen (2x Daily / 26U Total EST)</b>\n\n"
                f"• {morn_icon} <b>Morning (6:00 AM EST):</b> 13.0 U {'(Logged)' if ls['morning']['taken'] else '(Pending)'}\n"
                f"• {eve_icon} <b>Evening (6:00 PM EST):</b> 13.0 U {'(Logged)' if ls['evening']['taken'] else '(Pending)'}\n\n"
                f"👉 <b>Next Scheduled Dose:</b> {next_d['name']} {next_d['countdown']}"
            )
            keyboard = {
                "inline_keyboard": [
                    [{"text": "✓ Log 13.0 U Lantus Now", "callback_data": "took_lantus:13.0"}]
                ]
            }
            send_telegram_message(reply, reply_markup=keyboard, chat_id=chat_id)
            return {"status": "ok"}

        # In group chats, ignore arbitrary conversational chit-chat unless addressed or contains health terms
        if chat_type in ["group", "supergroup"] and not (raw_text.startswith("/") or "@" in raw_text or any(k in lower for k in ["glucose", "sugar", "insulin", "lantus", "carb", "eat", "snack", "dose", "correction", "bg", "food"])):
            return {"status": "ignored"}

        # Conversational fallback
        if summary:
            bg = summary["glucose"]
            sc = summary["safe_carbs"]
            reply = (
                f"Here is the latest live summary for the care team:\n\n"
                f"• <b>Blood Sugar:</b> {bg:.0f} mg/dL ({now_est.strftime('%I:%M %p EST')})\n"
                f"• <b>Safe Carb Intake:</b> {sc.get('label', 'Normal')}\n"
                f"• <b>Active IOB:</b> {summary['iob']:.2f} U\n"
                f"• <b>Next Lantus Dose:</b> {summary['lantus_schedule']['next_dose']['name']} ({summary['lantus_schedule']['next_dose']['countdown']})\n\n"
                f"<i>Type <code>/status</code>, <code>/carbs</code>, <code>/dose</code>, or <code>/schedule</code> anytime!</i>"
            )
        else:
            reply = "I'm your Gluco Track Assistant! Send /status to view current metrics."
        
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

        # Delete any conflicting webhook on first run so getUpdates succeeds
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
            # Expected on idle long poll, continue immediately
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
