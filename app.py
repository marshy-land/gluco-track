import os
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel
from fastapi import FastAPI, File, UploadFile, Query, HTTPException, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from parser import parse_libreview_csv
import db
from db import get_latest_reading, get_history, get_statistics, insert_readings, insert_insulin_doses, get_insulin_history
from prediction import predict_glucose, calculate_iob, suggest_correction

app = FastAPI(title="Gluco Track API", version="1.0.0")


def check_and_run_training_background():
    """Background worker that checks if the model needs training and runs it."""
    try:
        from ml_heuristics import train_predictive_model, train_imputation_calibration
        
        # Check last training time from db
        saved = db.get_system_setting("heuristics_params")
        if saved and isinstance(saved, dict):
            stats = saved.get("training_stats")
            if stats and "last_trained" in stats:
                last_trained_str = stats["last_trained"]
                try:
                    last_trained = datetime.fromisoformat(last_trained_str)
                    hours_since = (datetime.now(timezone.utc) - last_trained).total_seconds() / 3600
                    if hours_since < 24.0:
                        return # Too soon to retrain
                except ValueError:
                    pass
                    
        print("[Auto-Trainer] It has been >24h since last training. Running ML heuristics in background...")
        # Run training
        train_predictive_model(history_days=30)
        
        # Run Imputation Calibration
        readings = db.get_history(30 * 24)
        doses = db.get_insulin_history(30 * 24, include_imputed=False)
        calib_factor = train_imputation_calibration(readings, doses)
        
        db.set_system_setting("imputation_calibration_factor", calib_factor)
        print(f"[Auto-Trainer] Imputation calibration updated to {calib_factor:.2f}x.")
        
    except Exception as e:
        print(f"[Auto-Trainer] Background training failed: {e}")


# Serve visual dashboard on root route
@app.get("/", response_class=HTMLResponse)
def read_dashboard():
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if not os.path.exists(template_path):
        raise HTTPException(status_code=404, detail="Dashboard template not found.")
    
    with open(template_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)

# API Endpoints
@app.get("/api/glucose/latest")
def api_latest():
    reading = get_latest_reading()
    if not reading:
        return JSONResponse(status_code=404, content={"message": "No glucose readings found in the database."})
    
    # Format datetime as ISO string for JSON serialization
    reading['timestamp'] = reading['timestamp'].isoformat()
    return reading

@app.get("/api/glucose/history")
def api_history(hours: int = Query(default=24, ge=1, le=4320)):
    """Retrieves glucose readings within the last N hours (max 30 days / 720h)."""
    readings = get_history(hours)
    for r in readings:
        r['timestamp'] = r['timestamp'].isoformat()
    return readings

@app.get("/api/insulin/history")
def api_insulin_history(
    hours: int = Query(default=24, ge=1, le=4320),
    include_imputed: bool = Query(default=False)
):
    """Retrieves insulin logs within the last N hours, optionally including imputed missing doses."""
    doses = get_insulin_history(hours, include_imputed=include_imputed)
    for d in doses:
        if isinstance(d.get('timestamp'), datetime):
            d['timestamp'] = d['timestamp'].isoformat()
        if 'is_imputed' not in d or d['is_imputed'] is None:
            d['is_imputed'] = False

    if include_imputed:
        try:
            from imputation import detect_and_impute_missing_doses
            glucose_readings = get_history(hours + 4)
            raw_doses = get_insulin_history(hours + 4, include_imputed=False)
            
            imputed = detect_and_impute_missing_doses(glucose_readings, raw_doses, min_confidence=0.85)
            
            existing_ts_set = {d['timestamp'] for d in doses}
            for imp in imputed:
                ts_iso = imp['timestamp'].isoformat() if isinstance(imp['timestamp'], datetime) else imp['timestamp']
                if ts_iso not in existing_ts_set:
                    imp_copy = dict(imp)
                    imp_copy['timestamp'] = ts_iso
                    doses.append(imp_copy)
            
            doses.sort(key=lambda x: x['timestamp'])
        except Exception as e:
            print(f"Error executing missing dose imputation model: {e}")

    return doses

@app.get("/api/predictions")
def api_predictions(
    target: float = Query(default=120.0, description="Target glucose in mg/dL"),
    isf: Optional[float] = Query(default=None, description="Insulin Sensitivity Factor (ISF) in mg/dL/U")
):
    """Calculates forecasts for the next 15, 30, and 60 minutes and estimates correction bolus requirements."""
    latest = get_latest_reading()
    if not latest:
        return JSONResponse(status_code=404, content={"message": "No glucose readings found to forecast."})
    
    # We fetch the last 3 hours of readings to compute trends robustly
    history = get_history(3)
    predictions = predict_glucose(history)
    
    # We fetch recent insulin doses in the last 4 hours to compute IOB
    recent_doses = get_insulin_history(4, include_imputed=True)
    
    confirmed_doses = [d for d in recent_doses if not d.get('is_imputed')]
    estimated_doses = [d for d in recent_doses if d.get('is_imputed')]
    
    iob_confirmed = calculate_iob(confirmed_doses)
    iob_estimated = calculate_iob(estimated_doses)
    total_iob = iob_confirmed + iob_estimated
    
    # Estimate correction bolus or required carbs
    from prediction import suggest_carbs, calculate_safe_carb_allowance, calculate_proactive_alert, get_lantus_schedule_status
    
    forecasted_30m = latest['value']
    forecasted_60m = latest['value']
    for p in predictions:
        if p['minutes'] == 30:
            forecasted_30m = p['value']
        elif p['minutes'] == 60:
            forecasted_60m = p['value']

    # Resolve what ISF/CSF was actually used to display on UI
    used_isf = isf
    used_csf = 4.0
    if used_isf is None:
        try:
            from ml_heuristics import load_heuristics_params, get_time_of_day_bucket
            params = load_heuristics_params()
            bucket = get_time_of_day_bucket(latest['timestamp'])
            used_isf = params.get("isf", {}).get(bucket, 50.0)
            used_csf = params.get("csf", {}).get(bucket, 4.0)
        except Exception:
            used_isf = 50.0
            used_csf = 4.0

    suggested_insulin = suggest_correction(latest['value'], total_iob, target_glucose=target, isf=used_isf, current_time=latest['timestamp'])
    suggested_carbohydrates = suggest_carbs(latest['value'], forecasted_30m, total_iob, target_glucose=100.0, current_time=latest['timestamp'])

    # Safe carb allowance (either rescue carbs or safe snack allowance)
    safe_carbs = calculate_safe_carb_allowance(
        current_glucose=latest['value'],
        forecasted_60m=forecasted_60m,
        iob=total_iob,
        isf=used_isf,
        csf=used_csf,
        upper_limit=160.0
    )

    # Proactive alert focusing on >1 hour out (60m, 90m, 120m)
    proactive_alert = calculate_proactive_alert(
        current_glucose=latest['value'],
        predictions=predictions,
        iob=total_iob,
        isf=used_isf,
        csf=used_csf
    )

    # Lantus twice-daily schedule status (13U @ 6 AM / 13U @ 6 PM)
    lantus_status = get_lantus_schedule_status(timezone_str=os.getenv("LIBRE_TIMEZONE", "America/New_York"))

    # Mutually exclusive actions: don't suggest insulin if we need carbs!
    if suggested_carbohydrates > 0.0:
        suggested_insulin = 0.0
    elif suggested_insulin > 0.0:
        suggested_carbohydrates = 0.0

    # Format times for JSON response
    latest['timestamp'] = latest['timestamp'].isoformat()
    
    return {
        "current_glucose": latest['value'],
        "latest_reading": latest,
        "predictions": predictions,
        "active_iob": iob_confirmed,
        "active_iob_estimated": iob_estimated,
        "total_iob": total_iob,
        "suggested_correction": suggested_insulin,
        "suggested_carbs": suggested_carbohydrates,
        "safe_carb_allowance": safe_carbs,
        "proactive_alert": proactive_alert,
        "lantus_schedule": lantus_status,
        "parameters": {
            "target_glucose": target,
            "isf": used_isf,
            "csf": used_csf
        }
    }

@app.post("/api/heuristics/train")
def api_train_heuristics(days: int = Query(default=30, ge=7, le=90)):
    """Triggers the statistical machine learning model training job on the server."""
    try:
        from ml_heuristics import train_predictive_model, train_imputation_calibration
        
        # Train ISF modifiers
        success, msg = train_predictive_model(history_days=days)
        if not success:
            raise HTTPException(status_code=400, detail=msg)
            
        # Run Imputation Calibration
        readings = db.get_history(days * 24)
        doses = db.get_insulin_history(days * 24, include_imputed=False)
        calib_factor = train_imputation_calibration(readings, doses)
        
        db.set_system_setting("imputation_calibration_factor", calib_factor)
        
        msg += f" Imputation calibration set to {calib_factor:.2f}x."
        return {"success": True, "message": msg}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class FoodEntry(BaseModel):
    carbs_g: float
    food_type: str = None
    timestamp: datetime = None

@app.post("/api/food/log")
async def log_food(entry: FoodEntry, background_tasks: BackgroundTasks):
    ts = entry.timestamp if entry.timestamp else datetime.now(timezone.utc)
    try:
        inserted_id = db.insert_food_log(
            carbs_g=entry.carbs_g,
            timestamp=ts,
            food_type=entry.food_type
        )
        
        if background_tasks:
            background_tasks.add_task(check_and_run_training_background)
            
        # Trigger emulator sync to LibreView
        import subprocess
        import sys
        try:
            subprocess.Popen([sys.executable, "sync_emulator.py", "--carbs", str(entry.carbs_g)])
        except Exception as e:
            print(f"Failed to trigger emulator sync: {e}")
            
        return {"status": "success", "id": inserted_id, "timestamp": ts.isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/food/history")
async def get_food_history(hours: int = 24, include_imputed: bool = False):
    try:
        food_logs = db.get_food_history(limit_hours=hours, include_imputed=False) # Always get false from DB to prevent feedback loop
        
        if include_imputed:
            # Generate missing meals on the fly
            from carb_imputation import detect_and_impute_missing_meals
            readings = db.get_history(limit_hours=hours)
            imputed_meals = detect_and_impute_missing_meals(
                sorted_readings=sorted(readings, key=lambda x: x['timestamp']),
                sorted_food_logs=sorted(food_logs, key=lambda x: x['timestamp']),
                min_confidence=0.50
            )
            # Merge and sort
            all_food = food_logs + imputed_meals
            all_food.sort(key=lambda x: x['timestamp'])
            return all_food
            
        return food_logs
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/heuristics/status")
def api_heuristics_status():
    """Retrieves the status, time-of-day ISF values, and training diagnostics of the heuristics engine."""
    try:
        from ml_heuristics import load_heuristics_params
        params = load_heuristics_params()
        return {
            "model_trained": params.get("model_trained", False),
            "isf": params.get("isf", {
                "morning": 50.0,
                "afternoon": 50.0,
                "evening": 50.0,
                "night": 50.0,
                "global": 50.0
            }),
            "csf": params.get("csf", {
                "morning": 4.0,
                "afternoon": 4.0,
                "evening": 4.0,
                "night": 4.0,
                "global": 4.0
            }),
            "training_stats": params.get("training_stats")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class InsulinDoseLog(BaseModel):
    timestamp: Optional[datetime] = None
    rapid_acting: Optional[float] = None
    long_acting: Optional[float] = None
    meal: Optional[float] = None
    correction: Optional[float] = None
    user_change: Optional[float] = None

@app.post("/api/insulin/log")
def api_log_insulin(dose: InsulinDoseLog, background_tasks: BackgroundTasks):
    """Logs a single insulin dose entry directly into the database."""
    ts = dose.timestamp or datetime.now(timezone.utc)
    
    if all(v is None for v in [dose.rapid_acting, dose.long_acting, dose.meal, dose.correction, dose.user_change]):
        raise HTTPException(status_code=400, detail="At least one insulin type value must be provided.")
        
    dose_dict = {
        "timestamp": ts,
        "rapid_acting": dose.rapid_acting,
        "long_acting": dose.long_acting,
        "meal": dose.meal,
        "correction": dose.correction,
        "user_change": dose.user_change,
        "device": "Manual Entry",
        "serial_number": None
    }
    
    try:
        inserted = insert_insulin_doses([dose_dict])
        if inserted == 0:
            return {"message": "Dose entry already exists (duplicate ignored).", "inserted": 0}
        
        if background_tasks:
            background_tasks.add_task(check_and_run_training_background)
            
        # Trigger emulator sync to LibreView
        import subprocess
        import sys
        
        # Calculate total units (combining any specific fields that aren't None)
        total_units = sum(filter(None, [dose.rapid_acting, dose.meal, dose.correction]))
        
        if total_units > 0:
            try:
                subprocess.Popen([sys.executable, "sync_emulator.py", "--insulin", str(total_units)])
            except Exception as e:
                print(f"Failed to trigger emulator sync: {e}")
                
        return {"message": "Insulin dose logged successfully.", "inserted": 1}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/api/shortcut/log")
def api_shortcut_log(
    units: float = Query(..., description="Units of insulin"),
    type: str = Query(..., description="Type of insulin (rapid, long, meal, correction, change)"),
    background_tasks: BackgroundTasks = None
):
    """Direct deep-link endpoint for Android App Actions / Shortcuts to log doses."""
    dose_type = type.lower()
    dose_dict = {
        "timestamp": datetime.now(timezone.utc),
        "rapid_acting": units if dose_type in ["rapid", "rapid_acting"] else 0.0,
        "long_acting": units if dose_type in ["long", "long_acting", "basal"] else 0.0,
        "meal": units if dose_type == "meal" else 0.0,
        "correction": units if dose_type == "correction" else 0.0,
        "user_change": units if dose_type in ["change", "user_change"] else 0.0,
        "device": "Android App Action",
        "serial_number": None
    }
    try:
        inserted = insert_insulin_doses([dose_dict])
        if inserted == 0:
            return {"success": False, "message": "Dose entry already exists."}
        
        if background_tasks:
            background_tasks.add_task(check_and_run_training_background)
        return {"success": True, "message": f"Successfully logged {units}U of {dose_type} via Android Shortcut."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/insulin/log-lantus-dose")
def api_log_lantus_scheduled_dose(
    units: float = Query(default=13.0, description="Dose units (default 13.0)"),
    background_tasks: BackgroundTasks = None
):
    """One-click endpoint to log the scheduled 13.0U Lantus dose (6 AM / 6 PM regimen)."""
    now = datetime.now(timezone.utc)
    dose_dict = {
        "timestamp": now,
        "rapid_acting": 0.0,
        "long_acting": units,
        "meal": 0.0,
        "correction": 0.0,
        "user_change": 0.0,
        "device": "Scheduled Lantus Regimen (2x13U)",
        "serial_number": None
    }
    try:
        inserted = insert_insulin_doses([dose_dict])
        if inserted == 0:
            return {"success": True, "message": f"Lantus dose ({units}U) was already recorded for this time."}
        if background_tasks:
            background_tasks.add_task(check_and_run_training_background)
        return {"success": True, "message": f"Successfully logged scheduled {units}U Lantus basal dose."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/glucose/stats")
def api_stats(hours: int = Query(default=24, ge=1, le=4320)):
    """Computes stats (average, GMI, Time-in-Range) for the last N hours."""
    stats = get_statistics(hours)
    if not stats:
        return JSONResponse(status_code=404, content={"message": "No data available in this time range."})
    return stats

@app.post("/api/glucose/upload")
async def api_upload(file: UploadFile = File(...), background_tasks: BackgroundTasks = None):
    """Uploads a LibreView CSV export to backfill historical glucose data and insulin doses."""
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported.")

    # Write to a temporary file
    suffix = os.path.splitext(file.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
        shutil.copyfileobj(file.file, tmp_file)
        tmp_path = tmp_file.name

    try:
        # Parse CSV file using configured timezone
        timezone = os.getenv("LIBRE_TIMEZONE", "America/New_York")
        readings, doses = parse_libreview_csv(tmp_path, timezone_str=timezone)
        
        if not readings and not doses:
            return {"message": "No valid glucose readings or insulin doses found in the uploaded file.", "inserted": 0, "inserted_doses": 0}

        # Insert to database
        inserted_readings = 0
        inserted_doses = 0
        
        if readings:
            inserted_readings = insert_readings(readings)
        if doses:
            inserted_doses = insert_insulin_doses(doses)
            
        return {
            "message": "CSV upload processed successfully.",
            "parsed": len(readings),
            "inserted": inserted_readings,
            "parsed_doses": len(doses),
            "inserted_doses": inserted_doses,
            "filename": file.filename
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error parsing CSV file: {str(e)}")
    finally:
        # Clean up temp file
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

@app.get("/api/nutritional-impact")
def api_nutritional_impact(hours: int = Query(default=720, ge=1, le=4320)):
    """Retrieves time-of-day nutritional impact modifiers (M_tod) and dynamic clinical recommendations."""
    try:
        from ml_heuristics import calculate_nutritional_impact_modifiers
        return calculate_nutritional_impact_modifiers(hours_back=hours)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/nutritional-impact/summary")
def api_nutritional_impact_summary(hours: int = Query(default=720, ge=1, le=4320)):
    """Alias route for /api/nutritional-impact."""
    return api_nutritional_impact(hours=hours)


# --- Google Health / Fitness Endpoints ---

class GoogleCredentialsRequest(BaseModel):
    client_id: str
    client_secret: str

@app.post("/api/health/credentials")
def api_save_google_credentials(payload: GoogleCredentialsRequest):
    """Saves the user's Google Cloud OAuth Client ID and Secret."""
    try:
        from google_fit_sync import save_google_credentials
        save_google_credentials(payload.client_id, payload.client_secret)
        return {"success": True, "message": "Google Cloud OAuth credentials saved."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/health/oauth/url")
def api_google_oauth_url(redirect_uri: Optional[str] = None):
    """Generates the Google OAuth 2.0 consent URL."""
    try:
        from google_fit_sync import get_authorization_url
        target_redirect = redirect_uri or "http://137.184.96.172:8000/api/health/oauth/callback"
        auth_url = get_authorization_url(target_redirect)
        return {"success": True, "url": auth_url}
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "message": str(e)})

@app.get("/api/health/oauth/callback")
def api_google_oauth_callback(
    code: Optional[str] = None,
    error: Optional[str] = None,
    background_tasks: BackgroundTasks = None
):
    """Handles OAuth 2.0 callback redirect from Google."""
    if error:
        return HTMLResponse(f"<h3>Google Authentication Failed</h3><p>{error}</p><a href='/'>Return to Dashboard</a>", status_code=400)
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code.")

    try:
        from google_fit_sync import exchange_code_for_tokens, sync_all_google_fit
        redirect_uri = "http://137.184.96.172:8000/api/health/oauth/callback"
        exchange_code_for_tokens(code, redirect_uri)
        
        # Trigger immediate initial sync in background
        if background_tasks:
            background_tasks.add_task(sync_all_google_fit)
        else:
            try:
                sync_all_google_fit()
            except Exception as se:
                print(f"Initial sync warning: {se}")

        return HTMLResponse("""
            <html>
                <head>
                    <meta http-equiv="refresh" content="2;url=/?google_fit_connected=1">
                    <style>
                        body { background: #0f172a; color: #f8fafc; font-family: sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; margin: 0; }
                        .card { background: #1e293b; padding: 2rem; border-radius: 12px; border: 1px solid #334155; text-align: center; }
                        h2 { color: #10b981; margin-top: 0; }
                    </style>
                </head>
                <body>
                    <div class="card">
                        <h2>Google Health Connected!</h2>
                        <p>Syncing your sleep and activity data... Redirecting to dashboard.</p>
                    </div>
                </body>
            </html>
        """)
    except Exception as e:
        return HTMLResponse(f"<h3>Google Fit Token Error</h3><p>{str(e)}</p><a href='/'>Return to Dashboard</a>", status_code=500)

@app.get("/api/health/status")
def api_health_status():
    """Returns connection status, recent sleep summary, and sync status."""
    try:
        tokens = db.get_system_setting("google_fit_tokens")
        connected = bool(tokens and isinstance(tokens, dict) and tokens.get("access_token"))
        
        last_sync = db.get_system_setting("google_fit_last_sync")
        sleep_summary = db.get_recent_sleep_summary(hours=48)
        
        return {
            "connected": connected,
            "last_sync": last_sync,
            "sleep_summary": sleep_summary
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/health/sync")
def api_trigger_health_sync(background_tasks: BackgroundTasks):
    """Triggers an on-demand sync with Google Fit."""
    try:
        from google_fit_sync import sync_all_google_fit
        background_tasks.add_task(sync_all_google_fit)
        return {"success": True, "message": "Google Fit sync started in background."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/health/sleep")
def api_health_sleep(hours: int = Query(default=720, ge=1, le=4320)):
    """Retrieves sleep sessions recorded from Google Fit."""
    sessions = db.get_health_sessions(limit_hours=hours, session_type="sleep")
    for s in sessions:
        if isinstance(s.get("start_time"), datetime):
            s["start_time"] = s["start_time"].isoformat()
        if isinstance(s.get("end_time"), datetime):
            s["end_time"] = s["end_time"].isoformat()
        if isinstance(s.get("created_at"), datetime):
            s["created_at"] = s["created_at"].isoformat()
    return sessions


# --- Telegram Proactive Assistant Endpoints ---

class TelegramConfigRequest(BaseModel):
    bot_token: str
    chat_id: str
    enabled: bool = True

@app.post("/api/telegram/config")
def api_save_telegram_config(payload: TelegramConfigRequest):
    """Saves Telegram Bot Token and Chat ID."""
    try:
        from telegram_bot import save_telegram_config, start_telegram_polling
        from telegram_scheduler import start_telegram_scheduler
        save_telegram_config(payload.bot_token, payload.chat_id, payload.enabled)
        start_telegram_polling()
        start_telegram_scheduler()
        return {"success": True, "message": "Telegram Bot configuration saved & poller active."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/telegram/status")
def api_telegram_status():
    """Returns Telegram configuration status."""
    try:
        from telegram_bot import get_telegram_config
        config = get_telegram_config()
        stored = db.get_system_setting("telegram_config") or {}
        return {
            "is_configured": config.get("is_configured", False),
            "chat_id": config.get("chat_id"),
            "enabled": stored.get("enabled", True),
            "has_token": bool(config.get("bot_token")),
            "last_alert": db.get_system_setting("last_proactive_alert"),
            "pending_compliance_check": db.get_system_setting("pending_compliance_check")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/telegram/test")
def api_send_telegram_test():
    """Sends a test interactive message to verify Telegram Bot connectivity."""
    try:
        from telegram_bot import send_telegram_message, get_live_patient_summary
        summary = get_live_patient_summary()
        bg_text = f"{summary['glucose']:.0f} mg/dL" if summary else "110 mg/dL"

        msg = (
            "🚀 <b>Gluco Track Assistant Connected!</b>\n\n"
            f"• <b>Current Glucose:</b> {bg_text}\n"
            "• <b>Lantus Regimen:</b> Twice Daily (13.0 U @ 6 AM & 6 PM)\n"
            "• <b>Compliance Checks:</b> Active (15–20m follow-ups)\n"
            "• <b>Early Warning Interventions:</b> Active (>1 hour forecasts)\n\n"
            "<i>Try clicking an action below or asking a question like 'Can I eat a snack?'</i>"
        )
        keyboard = {
            "inline_keyboard": [
                [{"text": "📊 Check Status", "callback_data": "check_status"}, {"text": "✓ Test Dose Log", "callback_data": "took_lantus:13.0"}]
            ]
        }
        res = send_telegram_message(msg, reply_markup=keyboard)
        if res.get("success"):
            return {"success": True, "message": "Test message sent to Telegram successfully!"}
        else:
            raise HTTPException(status_code=400, detail=res.get("error", "Failed to send message via Telegram API."))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/telegram/webhook")
async def api_telegram_webhook(request: Request = None):
    """Processes incoming updates and button clicks from Telegram Webhook."""
    try:
        from telegram_bot import handle_telegram_update
        data = await request.json()
        result = handle_telegram_update(data)
        return result or {"status": "ok"}
    except Exception as e:
        print(f"[TelegramWebhook] Error handling update: {e}")
        return {"status": "error", "message": str(e)}


@app.on_event("startup")
def on_app_startup():
    """Starts background Telegram proactive monitor on application boot."""
    try:
        from telegram_scheduler import start_telegram_scheduler
        start_telegram_scheduler()
    except Exception as e:
        print(f"[AppStartup] Could not start Telegram scheduler: {e}")

