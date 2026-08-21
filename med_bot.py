import os
import re
import requests
from datetime import datetime, timezone
import db

TELEGRAM_API_BASE = "https://api.telegram.org/bot"

# Helper to get Med Bot config
def get_med_bot_config():
    """Returns the Med Bot token and associated chat ID from system settings."""
    config = db.get_system_setting("med_bot_config") or {}
    if not config.get("bot_token"):
        config["bot_token"] = os.getenv("MED_BOT_TOKEN", "")
    return config

def save_med_bot_config(token, chat_id):
    db.set_system_setting("med_bot_config", {"bot_token": token, "chat_id": chat_id})

def send_med_message(text, reply_markup=None, chat_id=None):
    config = get_med_bot_config()
    token = config.get("bot_token")
    if not chat_id:
        chat_id = config.get("chat_id")
        
    if not token or not chat_id:
        print("[MedBot] Missing token or chat_id.")
        return {"success": False, "error": "Missing token or chat_id"}
        
    url = f"{TELEGRAM_API_BASE}{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
        
    try:
        resp = requests.post(url, json=payload, timeout=8)
        if resp.status_code == 200:
            return {"success": True, "result": resp.json().get("result")}
        else:
            return {"success": False, "error": resp.text}
    except Exception as e:
        return {"success": False, "error": str(e)}

def answer_callback_query(callback_query_id, text=None):
    config = get_med_bot_config()
    token = config.get("bot_token")
    if not token:
        return
    url = f"{TELEGRAM_API_BASE}{token}/answerCallbackQuery"
    try:
        requests.post(url, json={"callback_query_id": callback_query_id, "text": text}, timeout=8)
    except Exception:
        pass

def edit_message_text(chat_id, message_id, text, reply_markup=None):
    config = get_med_bot_config()
    token = config.get("bot_token")
    if not token:
        return
    url = f"{TELEGRAM_API_BASE}{token}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(url, json=payload, timeout=8)
    except Exception:
        pass

# --- DB Helpers for Meds ---

def get_medication_presets():
    conn = db.get_connection()
    try:
        with conn.cursor(cursor_factory=db.psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM medication_types WHERE is_active = TRUE ORDER BY name;")
            return cur.fetchall()
    finally:
        conn.close()

def add_medication_preset(name, default_dose, dose_unit):
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO medication_types (name, default_dose, dose_unit)
                VALUES (%s, %s, %s)
                ON CONFLICT (LOWER(name)) DO UPDATE 
                SET default_dose = EXCLUDED.default_dose, dose_unit = EXCLUDED.dose_unit, is_active = TRUE
                RETURNING id;
                """,
                (name, default_dose, dose_unit)
            )
            med_id = cur.fetchone()[0]
        conn.commit()
        return med_id
    finally:
        conn.close()

def log_medication_dose(medication_id, dose_taken, notes=None):
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO medication_logs (medication_id, timestamp, dose_taken, notes)
                VALUES (%s, %s, %s, %s)
                """,
                (medication_id, datetime.now(timezone.utc), dose_taken, notes)
            )
        conn.commit()
    finally:
        conn.close()

def get_recent_med_logs(limit=10):
    conn = db.get_connection()
    try:
        with conn.cursor(cursor_factory=db.psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT l.id, l.timestamp, l.dose_taken, l.notes, t.name, t.dose_unit
                FROM medication_logs l
                JOIN medication_types t ON l.medication_id = t.id
                ORDER BY l.timestamp DESC
                LIMIT %s
                """,
                (limit,)
            )
            return cur.fetchall()
    finally:
        conn.close()


# --- Main Webhook Handler ---

def handle_med_webhook(update):
    """Processes incoming updates for the Medication Tracker bot."""
    
    MAIN_MENU_KEYBOARD = {
        "keyboard": [
            [{"text": "💊 Log Meds"}, {"text": "📋 View History"}],
            [{"text": "⚙️ Med Presets"}]
        ],
        "resize_keyboard": True,
        "is_persistent": True
    }

    # 1. Handle Callback Queries (Inline Buttons)
    if "callback_query" in update:
        cb = update["callback_query"]
        cb_id = cb["id"]
        cb_data = cb.get("data", "")
        msg = cb.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        msg_id = msg.get("message_id")
        from_user = cb.get("from", {}).get("first_name", "User")

        if cb_data.startswith("log_med:"):
            # Format: log_med:{med_id}:{dose}
            parts = cb_data.split(":")
            if len(parts) == 3:
                med_id = int(parts[1])
                dose = float(parts[2])
                
                presets = get_medication_presets()
                med = next((p for p in presets if p["id"] == med_id), None)
                if med:
                    log_medication_dose(med_id, dose, f"Logged via quick button by {from_user}")
                    answer_callback_query(cb_id, f"Logged {dose} {med['dose_unit']} of {med['name']}")
                    edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=f"✅ <b>{from_user} logged {dose} {med['dose_unit']} of {med['name']}</b>"
                    )
                else:
                    answer_callback_query(cb_id, "Error: Medication not found.")
            return {"status": "ok"}
            
        elif cb_data == "dismiss_med":
            answer_callback_query(cb_id, "Dismissed.")
            edit_message_text(chat_id=chat_id, message_id=msg_id, text="<i>Menu closed.</i>")
            return {"status": "ok"}

    # 2. Handle Text Messages
    if "message" in update and "text" in update["message"]:
        msg = update["message"]
        chat_id = msg.get("chat", {}).get("id")
        raw_text = msg.get("text", "").strip()
        lower = raw_text.lower()
        
        config = get_med_bot_config()
        if not config.get("chat_id") or lower.startswith("/start") or lower.startswith("/link"):
            if config.get("bot_token"):
                save_med_bot_config(config["bot_token"], chat_id)
        
        if lower == "💊 log meds": lower = "/log"
        elif lower == "📋 view history": lower = "/history"
        elif lower == "⚙️ med presets": lower = "/presets"

        if lower.startswith("/start"):
            reply = (
                "💊 <b>Medication Tracker</b>\n\n"
                "Use the menu below to log PRN doses, manage your presets, and view your chronological history."
            )
            send_med_message(reply, reply_markup=MAIN_MENU_KEYBOARD, chat_id=chat_id)
            return {"status": "ok"}

        if lower.startswith("/log"):
            presets = get_medication_presets()
            if not presets:
                send_med_message("⚠️ No medication presets found. Use <code>/addpreset [Name] [Dose] [Unit]</code> to create one.", reply_markup=MAIN_MENU_KEYBOARD, chat_id=chat_id)
                return {"status": "ok"}
                
            buttons = []
            for p in presets:
                buttons.append([{"text": f"Log {p['default_dose']} {p['dose_unit']} {p['name']}", "callback_data": f"log_med:{p['id']}:{p['default_dose']}"}])
            buttons.append([{"text": "✕ Cancel", "callback_data": "dismiss_med"}])
            
            keyboard = {"inline_keyboard": buttons}
            send_med_message("Select a medication to log right now:", reply_markup=keyboard, chat_id=chat_id)
            return {"status": "ok"}

        if lower.startswith("/history"):
            logs = get_recent_med_logs(15)
            if not logs:
                send_med_message("No recent medications logged.", reply_markup=MAIN_MENU_KEYBOARD, chat_id=chat_id)
                return {"status": "ok"}
                
            lines = ["📋 <b>Recent Medications</b>\n"]
            for l in logs:
                est_offset = -5 * 3600 # rough EST offset
                ts = l['timestamp'].timestamp() + est_offset
                dt = datetime.fromtimestamp(ts, timezone.utc)
                time_str = dt.strftime("%b %d, %I:%M %p")
                lines.append(f"• <b>{l['dose_taken']} {l['dose_unit']} {l['name']}</b> <i>({time_str})</i>")
                
            send_med_message("\n".join(lines), reply_markup=MAIN_MENU_KEYBOARD, chat_id=chat_id)
            return {"status": "ok"}

        if lower.startswith("/presets"):
            presets = get_medication_presets()
            if not presets:
                send_med_message("No presets. Add one via:\n<code>/addpreset [Name] [Dose] [Unit]</code>\nExample: <code>/addpreset Lorazepam 1.0 mg</code>", reply_markup=MAIN_MENU_KEYBOARD, chat_id=chat_id)
            else:
                lines = ["⚙️ <b>Active Presets</b>\n"]
                for p in presets:
                    lines.append(f"• {p['name']}: {p['default_dose']} {p['dose_unit']}")
                lines.append("\n<i>To add more: /addpreset [Name] [Dose] [Unit]</i>")
                send_med_message("\n".join(lines), reply_markup=MAIN_MENU_KEYBOARD, chat_id=chat_id)
            return {"status": "ok"}

        if lower.startswith("/addpreset"):
            parts = raw_text.split()
            if len(parts) >= 4:
                unit = parts[-1]
                try:
                    dose = float(parts[-2])
                    name = " ".join(parts[1:-2])
                    add_medication_preset(name, dose, unit)
                    send_med_message(f"✅ Added preset: <b>{name}</b> ({dose} {unit})", reply_markup=MAIN_MENU_KEYBOARD, chat_id=chat_id)
                except ValueError:
                    send_med_message("⚠️ Dose must be a number. Example: <code>/addpreset Lorazepam 1.0 mg</code>", chat_id=chat_id)
            else:
                send_med_message("⚠️ Format: <code>/addpreset [Name] [Dose] [Unit]</code>\nExample: <code>/addpreset Oxycodone 5 mg</code>", chat_id=chat_id)
            return {"status": "ok"}

    return {"status": "ok"}
