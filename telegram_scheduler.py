import time
import threading
from datetime import datetime, timezone, timedelta
import pytz
import db
from telegram_bot import send_telegram_message, get_live_patient_summary, get_telegram_config, start_telegram_polling

_scheduler_running = False
_scheduler_thread = None
EST_TZ = pytz.timezone("America/New_York")

def check_and_send_scheduled_alerts():
    """
    Main periodic check function executed every 60 seconds:
    1. 6:00 AM EST and 6:00 PM EST Lantus Dose Reminders (13.0 U each)
    2. 15-20 min Compliance Check-Ins
    3. Proactive >1h Early Warning Trajectory Alerts
    """
    config = get_telegram_config()
    if not config.get("is_configured"):
        return

    stored_cfg = db.get_system_setting("telegram_config") or {}
    if stored_cfg.get("enabled") is False:
        return

    now_utc = datetime.now(timezone.utc)
    now_est = now_utc.astimezone(EST_TZ)
    today_date = now_est.date()

    summary = get_live_patient_summary()
    if not summary:
        return

    # --- 1. Twice-Daily Lantus Reminders (06:00 AM EST & 18:00 PM EST) ---
    ls = summary["lantus_schedule"]
    last_reminders = db.get_system_setting("last_lantus_reminders") or {}
    today_str = today_date.isoformat()

    # Morning Window (06:00 AM EST - 06:20 AM EST)
    if now_est.hour == 6 and now_est.minute < 20 and not ls["morning"]["taken"]:
        if last_reminders.get("morning_date") != today_str:
            bg_text = f"Current BG: <b>{summary['glucose']:.0f} mg/dL</b>"
            msg = (
                f"🌅 <b>Morning Lantus Reminder (6:00 AM EST)</b>\n\n"
                f"It's time for the scheduled <b>13.0 U Lantus</b> dose.\n"
                f"{bg_text} • Active IOB: {summary['iob']:.2f} U\n\n"
                f"<i>Please confirm once taken:</i>"
            )
            keyboard = {
                "inline_keyboard": [
                    [{"text": "✓ Took 13.0 U Lantus", "callback_data": "took_lantus:13.0"}],
                    [{"text": "⏳ Snooze 15m", "callback_data": "snooze:15"}, {"text": "❌ Skip", "callback_data": "skip_dose"}]
                ]
            }
            send_telegram_message(msg, reply_markup=keyboard)
            last_reminders["morning_date"] = today_str
            db.set_system_setting("last_lantus_reminders", last_reminders)

            # Register 15-minute compliance follow-up
            db.set_system_setting("pending_compliance_check", {
                "type": "morning_lantus",
                "units": 13.0,
                "sent_at": now_utc.isoformat(),
                "due_at": (now_utc + timedelta(minutes=15)).isoformat()
            })

    # Evening Window (18:00 PM EST - 18:20 PM EST)
    elif now_est.hour == 18 and now_est.minute < 20 and not ls["evening"]["taken"]:
        if last_reminders.get("evening_date") != today_str:
            bg_text = f"Current BG: <b>{summary['glucose']:.0f} mg/dL</b>"
            msg = (
                f"🌇 <b>Evening Lantus Reminder (6:00 PM EST)</b>\n\n"
                f"It's time for the scheduled <b>13.0 U Lantus</b> dose.\n"
                f"{bg_text} • Active IOB: {summary['iob']:.2f} U\n\n"
                f"<i>Please confirm once taken:</i>"
            )
            keyboard = {
                "inline_keyboard": [
                    [{"text": "✓ Took 13.0 U Lantus", "callback_data": "took_lantus:13.0"}],
                    [{"text": "⏳ Snooze 15m", "callback_data": "snooze:15"}, {"text": "❌ Skip", "callback_data": "skip_dose"}]
                ]
            }
            send_telegram_message(msg, reply_markup=keyboard)
            last_reminders["evening_date"] = today_str
            db.set_system_setting("last_lantus_reminders", last_reminders)

            # Register 15-minute compliance follow-up
            db.set_system_setting("pending_compliance_check", {
                "type": "evening_lantus",
                "units": 13.0,
                "sent_at": now_utc.isoformat(),
                "due_at": (now_utc + timedelta(minutes=15)).isoformat()
            })

    # --- 2. 15-20 Min Compliance Follow-Up ---
    pending = db.get_system_setting("pending_compliance_check")
    if pending and isinstance(pending, dict):
        due_str = pending.get("snooze_until") or pending.get("due_at")
        if due_str:
            try:
                due_dt = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
                if now_utc >= due_dt:
                    # Check if a dose was recorded since sent_at
                    sent_str = pending.get("sent_at", "")
                    sent_dt = datetime.fromisoformat(sent_str.replace("Z", "+00:00")) if sent_str else now_utc - timedelta(minutes=20)
                    
                    recent_doses = db.get_insulin_history(1, include_imputed=False)
                    dose_logged = False
                    for d in recent_doses:
                        ts = d.get("timestamp")
                        if isinstance(ts, str):
                            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        if ts and ts >= sent_dt and (d.get("long_acting") or 0) >= 8.0:
                            dose_logged = True
                            break

                    if not dose_logged:
                        dose_type_label = "Morning 13.0 U Lantus" if "morning" in pending.get("type", "") else "Evening 13.0 U Lantus"
                        msg = (
                            f"👋 <b>Compliance Check-in (15m follow-up)</b>\n\n"
                            f"Did the patient take their <b>{dose_type_label}</b> dose?\n\n"
                            f"Staying consistent with the twice-daily Eastern Time schedule (6 AM / 6 PM EST) ensures continuous 24-hour basal coverage."
                        )
                        keyboard = {
                            "inline_keyboard": [
                                [{"text": "✓ Yes, Log 13.0 U Now", "callback_data": "took_lantus:13.0"}],
                                [{"text": "⏳ Snooze Another 15m", "callback_data": "snooze:15"}, {"text": "❌ Skip", "callback_data": "skip_dose"}]
                            ]
                        }
                        send_telegram_message(msg, reply_markup=keyboard)

                    # Clear pending check so it doesn't loop
                    db.set_system_setting("pending_compliance_check", None)
            except Exception as e:
                print(f"[TelegramScheduler] Error in compliance check: {e}")
                db.set_system_setting("pending_compliance_check", None)

    # --- 3. Proactive >1-Hour Early Warning Interventions ---
    pa = summary.get("proactive_alert", {})
    level = pa.get("level")
    if level in ["warning_low", "warning_high"]:
        last_alert = db.get_system_setting("last_proactive_alert") or {}
        last_time_str = last_alert.get("timestamp")
        send_alert = True

        if last_time_str:
            try:
                last_time = datetime.fromisoformat(last_time_str.replace("Z", "+00:00"))
                # 45-minute throttle cooldown
                if (now_utc - last_time).total_seconds() < 2700:
                    send_alert = False
            except Exception:
                pass

        if send_alert:
            time_str = now_est.strftime("%I:%M %p EST")
            if level == "warning_low":
                msg = (
                    f"⚠️ <b>{pa.get('badge', 'Proactive Low Warning')}</b> ({time_str})\n\n"
                    f"<b>Current Glucose:</b> {summary['glucose']:.0f} mg/dL ↘\n"
                    f"<b>Trajectory:</b> {pa.get('title', '')}\n\n"
                    f"👉 <b>{pa.get('message', '')}</b>"
                )
            else:
                msg = (
                    f"⚠️ <b>{pa.get('badge', 'Proactive High Warning')}</b> ({time_str})\n\n"
                    f"<b>Current Glucose:</b> {summary['glucose']:.0f} mg/dL ↗\n"
                    f"<b>Trajectory:</b> {pa.get('title', '')}\n\n"
                    f"👉 <b>{pa.get('message', '')}</b>"
                )

            send_telegram_message(msg)
            db.set_system_setting("last_proactive_alert", {
                "timestamp": now_utc.isoformat(),
                "level": level,
                "title": pa.get("title")
            })

def scheduler_worker_loop():
    """Background loop running every 60 seconds."""
    global _scheduler_running
    print("[TelegramScheduler] Proactive background assistant started (Eastern Standard Time).")
    while _scheduler_running:
        try:
            check_and_send_scheduled_alerts()
        except Exception as e:
            print(f"[TelegramScheduler] Error in loop: {e}")
        time.sleep(60)

def start_telegram_scheduler():
    """Spawns the background scheduler and polling daemon threads."""
    global _scheduler_running, _scheduler_thread
    start_telegram_polling()
    if _scheduler_running:
        return
    _scheduler_running = True
    _scheduler_thread = threading.Thread(target=scheduler_worker_loop, daemon=True)
    _scheduler_thread.start()

def stop_telegram_scheduler():
    """Stops the background scheduler thread."""
    global _scheduler_running
    _scheduler_running = False
