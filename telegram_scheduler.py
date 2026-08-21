import time
import threading
from datetime import datetime, timezone, timedelta
import pytz
import db
from telegram_bot import send_telegram_message, get_live_patient_summary, get_telegram_config, start_telegram_polling

_scheduler_running = False
_scheduler_thread = None
EST_TZ = pytz.timezone("America/New_York")

# Progressive, low-demand check-in prompts designed to support routine without triggering anxiety or pressure
GENTLE_PROMPTS = {
    "morning": [
        "🌅 <b>Morning routine window is open • 13.0 U Lantus</b>\nBG: <b>{bg} mg/dL</b> • Whenever you're ready.",
        "☀️ <b>Gentle check-in for your 13.0 U morning dose.</b>\nReady whenever you are today.",
        "🌿 <b>Checking in on your 13.0 U dose whenever you get a free moment.</b>",
        "🌤️ <b>Still keeping track of your morning 13.0 U dose whenever you're set.</b>"
    ],
    "evening": [
        "🌇 <b>Evening routine window is open • 13.0 U Lantus</b>\nBG: <b>{bg} mg/dL</b> • Whenever you're ready.",
        "🌙 <b>Gentle check-in for your 13.0 U evening dose.</b>\nReady whenever you are tonight.",
        "🌿 <b>Checking in on your 13.0 U dose whenever you get a quiet moment.</b>",
        "✨ <b>Still keeping track of your evening 13.0 U dose whenever you're set.</b>"
    ]
}

# Progressive delays between checks in minutes: +45m -> +75m -> +120m
PROGRESSIVE_DELAYS = [45, 75, 120]

def check_and_send_scheduled_alerts():
    """
    Periodic monitor with continuous, gentle, progressive check-ins and urgent-only alerts.
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
    today_str = today_date.isoformat()

    summary = get_live_patient_summary()
    if not summary:
        return

    ls = summary["lantus_schedule"]
    last_reminders = db.get_system_setting("last_lantus_reminders") or {}
    bg_val = f"{summary['glucose']:.0f}"

    # --- 1. Twice-Daily Scheduled Openings (06:00 AM & 18:00 PM EST) ---
    
    # Morning Window (06:00 AM - 06:15 AM EST)
    if now_est.hour == 6 and now_est.minute < 15 and not ls["morning"]["taken"]:
        if last_reminders.get("morning_date") != today_str:
            msg = GENTLE_PROMPTS["morning"][0].format(bg=bg_val)
            keyboard = {
                "inline_keyboard": [
                    [{"text": "✓ Done (13.0 U)", "callback_data": "took_lantus:13.0"}],
                    [{"text": "⏳ Later", "callback_data": "snooze:60"}, {"text": "✕ Skip today", "callback_data": "skip_dose"}]
                ]
            }
            send_telegram_message(msg, reply_markup=keyboard)
            last_reminders["morning_date"] = today_str
            db.set_system_setting("last_lantus_reminders", last_reminders)

            # Register progressive gentle check-in starting at +45m
            db.set_system_setting("pending_compliance_check", {
                "type": "morning_lantus",
                "step": 1,
                "sent_at": now_utc.isoformat(),
                "due_at": (now_utc + timedelta(minutes=45)).isoformat(),
                "date": today_str
            })

    # Evening Window (18:00 PM - 18:15 PM EST)
    elif now_est.hour == 18 and now_est.minute < 15 and not ls["evening"]["taken"]:
        if last_reminders.get("evening_date") != today_str:
            msg = GENTLE_PROMPTS["evening"][0].format(bg=bg_val)
            keyboard = {
                "inline_keyboard": [
                    [{"text": "✓ Done (13.0 U)", "callback_data": "took_lantus:13.0"}],
                    [{"text": "⏳ Later", "callback_data": "snooze:60"}, {"text": "✕ Skip today", "callback_data": "skip_dose"}]
                ]
            }
            send_telegram_message(msg, reply_markup=keyboard)
            last_reminders["evening_date"] = today_str
            db.set_system_setting("last_lantus_reminders", last_reminders)

            # Register progressive gentle check-in starting at +45m
            db.set_system_setting("pending_compliance_check", {
                "type": "evening_lantus",
                "step": 1,
                "sent_at": now_utc.isoformat(),
                "due_at": (now_utc + timedelta(minutes=45)).isoformat(),
                "date": today_str
            })

    # --- 2. Gentle Progressive Follow-up Cycle ---
    pending = db.get_system_setting("pending_compliance_check")
    if pending and isinstance(pending, dict):
        due_str = pending.get("snooze_until") or pending.get("due_at")
        if due_str:
            try:
                due_dt = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
                if now_utc >= due_dt:
                    # Check if a long-acting dose was logged today since sent_at
                    sent_str = pending.get("sent_at", "")
                    sent_dt = datetime.fromisoformat(sent_str.replace("Z", "+00:00")) if sent_str else now_utc - timedelta(hours=6)
                    
                    recent_doses = db.get_insulin_history(12, include_imputed=False)
                    dose_logged = False
                    for d in recent_doses:
                        ts = d.get("timestamp")
                        if isinstance(ts, str):
                            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        if ts and ts >= sent_dt and (d.get("long_acting") or 0) >= 8.0:
                            dose_logged = True
                            break

                    if not dose_logged:
                        period = "morning" if "morning" in pending.get("type", "") else "evening"
                        step = pending.get("step", 1)
                        prompt_list = GENTLE_PROMPTS[period]
                        prompt_idx = min(step, len(prompt_list) - 1)
                        msg = prompt_list[prompt_idx].format(bg=bg_val)

                        keyboard = {
                            "inline_keyboard": [
                                [{"text": "✓ Done (13.0 U)", "callback_data": "took_lantus:13.0"}],
                                [{"text": "⏳ Later", "callback_data": "snooze:60"}, {"text": "✕ Skip today", "callback_data": "skip_dose"}]
                            ]
                        }
                        send_telegram_message(msg, reply_markup=keyboard)

                        # Advance to next progressive interval
                        if step < len(PROGRESSIVE_DELAYS):
                            next_delay = PROGRESSIVE_DELAYS[step]
                            pending["step"] = step + 1
                            pending["sent_at"] = now_utc.isoformat()
                            pending["due_at"] = (now_utc + timedelta(minutes=next_delay)).isoformat()
                            pending.pop("snooze_until", None)
                            db.set_system_setting("pending_compliance_check", pending)
                        else:
                            # Concluded today's gentle cycle
                            db.set_system_setting("pending_compliance_check", None)
                    else:
                        # Already recorded
                        db.set_system_setting("pending_compliance_check", None)
            except Exception as e:
                print(f"[TelegramScheduler] Error in gentle follow-up: {e}")
                db.set_system_setting("pending_compliance_check", None)

    # --- 3. High-Importance Alerts ONLY (Urgent Low or Sustained High) ---
    bg = summary["glucose"]
    iob = summary["iob"]
    preds = summary.get("predictions", [])
    
    f30_60 = [p for p in preds if 30 <= p.get('minutes', 0) <= 60]
    min_f = min([p['value'] for p in f30_60]) if f30_60 else bg
    max_f = max([p['value'] for p in f30_60]) if f30_60 else bg

    is_urgent_low = (bg < 70.0) or (min_f < 65.0 and bg < 90.0)
    is_urgent_high = (bg > 240.0 and iob < 1.0) or (max_f > 250.0 and iob < 0.5)

    if is_urgent_low or is_urgent_high:
        last_alert = db.get_system_setting("last_proactive_alert") or {}
        last_time_str = last_alert.get("timestamp")
        send_alert = True

        if last_time_str:
            try:
                last_time = datetime.fromisoformat(last_time_str.replace("Z", "+00:00"))
                cooldown_secs = 5400 if is_urgent_low else 9000
                if bg < 55.0 and last_alert.get("level") != "critical_low":
                    send_alert = True
                elif (now_utc - last_time).total_seconds() < cooldown_secs:
                    send_alert = False
            except Exception:
                pass

        if send_alert:
            time_str = now_est.strftime("%I:%M %p EST")
            if is_urgent_low:
                carbs_needed = 15 if bg < 60 else 10
                msg = (
                    f"🚨 <b>Low Glucose Alert</b> ({time_str})\n"
                    f"<b>{bg:.0f} mg/dL</b> ↘ (Projected <{min_f:.0f})\n"
                    f"👉 Take <b>~{carbs_needed}g fast-acting carbs</b> now."
                )
                alert_type = "critical_low" if bg < 55 else "urgent_low"
            else:
                corr = summary.get("correction", 0.0)
                corr_txt = f"Recommended correction: <b>{corr:.1f} U</b>" if corr > 0 else "Monitor trend"
                msg = (
                    f"⚠️ <b>High Glucose Alert</b> ({time_str})\n"
                    f"<b>{bg:.0f} mg/dL</b> ↗ • IOB: {iob:.1f} U\n"
                    f"👉 {corr_txt}."
                )
                alert_type = "urgent_high"
                
            keyboard = None
            if not is_urgent_low and summary.get("correction", 0.0) > 0:
                corr_val = summary.get("correction", 0.0)
                keyboard = {
                    "inline_keyboard": [
                        [{"text": f"✓ Log {corr_val:.1f} U Correction", "callback_data": f"took_correction:{corr_val:.1f}"}]
                    ]
                }

            send_telegram_message(msg, reply_markup=keyboard)
            db.set_system_setting("last_proactive_alert", {
                "timestamp": now_utc.isoformat(),
                "type": alert_type,
                "glucose": bg
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
