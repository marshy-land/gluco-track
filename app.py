import os
import shutil
import tempfile
import math
from datetime import datetime, timezone
from typing import Optional, Any, Dict, List
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


# Mount static directory for PWA assets
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Serve visual dashboard on root route (Manager View)
@app.get("/", response_class=HTMLResponse)
def read_dashboard():
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if not os.path.exists(template_path):
        raise HTTPException(status_code=404, detail="Dashboard template not found.")
    
    with open(template_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)

# Serve simplified Mobile-First Patient PWA
@app.get("/patient", response_class=HTMLResponse)
@app.get("/app", response_class=HTMLResponse)
def read_patient_portal():
    template_path = os.path.join(os.path.dirname(__file__), "templates", "patient.html")
    if not os.path.exists(template_path):
        raise HTTPException(status_code=404, detail="Patient template not found.")
    
    with open(template_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)

# Serve PWA manifest
@app.get("/manifest.json")
def get_pwa_manifest():
    manifest_path = os.path.join(os.path.dirname(__file__), "static", "manifest.json")
    if not os.path.exists(manifest_path):
        raise HTTPException(status_code=404, detail="Manifest not found.")
    with open(manifest_path, "r", encoding="utf-8") as f:
        from starlette.responses import Response
        return Response(content=f.read(), media_type="application/manifest+json")

# Serve Service Worker
@app.get("/sw.js")
def get_pwa_service_worker():
    sw_path = os.path.join(os.path.dirname(__file__), "static", "sw.js")
    if not os.path.exists(sw_path):
        raise HTTPException(status_code=404, detail="Service worker not found.")
    with open(sw_path, "r", encoding="utf-8") as f:
        from starlette.responses import Response
        return Response(content=f.read(), media_type="application/javascript")

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
        for k, v in list(d.items()):
            if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
                d[k] = None

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
    """Calculates forecasts out to 3 hours (180 mins) and estimates conservative correction bolus requirements."""
    latest = get_latest_reading()
    if not latest:
        return JSONResponse(status_code=404, content={"message": "No glucose readings found to forecast."})
    
    # We fetch the last 3 hours of readings to compute trends robustly
    history = get_history(3)
    predictions = predict_glucose(history)
    
    # We fetch recent insulin doses in the last 4 hours to compute IOB
    recent_doses = get_insulin_history(4, include_imputed=True)
    
    confirmed_doses = [d for d in recent_doses if not d.get('is_imputed')]
    # Only include estimated doses with >= 95% certainty in total active insulin
    estimated_doses = [d for d in recent_doses if d.get('is_imputed') and d.get('confidence_score', 0.0) >= 0.95]
    
    iob_confirmed = calculate_iob(confirmed_doses)
    iob_estimated = calculate_iob(estimated_doses)
    total_iob = iob_confirmed + iob_estimated
    
    # Fetch recent food logs (last 2 hours) for post-action suppression
    recent_carbs = db.get_food_history(2, include_imputed=False)

    # Estimate correction bolus or required carbs
    from prediction import suggest_carbs, calculate_safe_carb_allowance, calculate_proactive_alert, get_lantus_schedule_status
    
    forecasted_30m = latest['value']
    forecasted_60m = latest['value']
    for p in predictions:
        if p['minutes'] == 30:
            forecasted_30m = p['value']
        elif p['minutes'] == 60:
            forecasted_60m = p['value']

    tz_str = os.getenv("LIBRE_TIMEZONE", "America/New_York")

    # Resolve what ISF/CSF was actually used to display on UI
    used_isf = isf
    used_csf = 4.0
    if used_isf is None:
        try:
            from ml_heuristics import load_heuristics_params, get_time_of_day_bucket
            params = load_heuristics_params()
            bucket = get_time_of_day_bucket(latest['timestamp'], timezone_str=tz_str)
            used_isf = params.get("isf", {}).get(bucket, 50.0)
            used_csf = params.get("csf", {}).get(bucket, 4.0)
        except Exception:
            used_isf = 50.0
            used_csf = 4.0

    suggested_insulin = suggest_correction(
        latest['value'],
        total_iob,
        target_glucose=target,
        isf=used_isf,
        current_time=latest['timestamp'],
        forecasted_glucose=forecasted_60m,
        timezone_str=tz_str,
        check_lantus_window=True,
        recent_doses=confirmed_doses
    )
    suggested_carbohydrates = suggest_carbs(
        latest['value'],
        forecasted_30m,
        total_iob,
        target_glucose=100.0,
        current_time=latest['timestamp']
    )

    # Safe carb allowance (either rescue carbs or safe snack allowance or awaiting absorption)
    safe_carbs = calculate_safe_carb_allowance(
        current_glucose=latest['value'],
        forecasted_60m=forecasted_60m,
        iob=total_iob,
        isf=used_isf,
        csf=used_csf,
        upper_limit=160.0,
        recent_carbs=recent_carbs,
        current_time=latest['timestamp']
    )

    # Proactive alert focusing on forward trajectory (>1 hour out)
    proactive_alert = calculate_proactive_alert(
        current_glucose=latest['value'],
        predictions=predictions,
        iob=total_iob,
        isf=used_isf,
        csf=used_csf,
        current_time=latest['timestamp'],
        recent_doses=confirmed_doses,
        recent_carbs=recent_carbs,
        timezone_str=tz_str
    )

    # Lantus twice-daily schedule status (13U @ 6 AM / 13U @ 6 PM)
    lantus_status = get_lantus_schedule_status(timezone_str=tz_str)

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

@app.get("/api/patient/summary")
def api_patient_summary():
    """Aggregated, ultra-fast payload specifically tailored for the mobile patient PWA."""
    return api_predictions()

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
            
        return {"status": "success", "id": inserted_id, "timestamp": ts.isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/food/history")
async def get_food_history(hours: int = 24, include_imputed: bool = False):
    try:
        food_logs = db.get_food_history(limit_hours=hours, include_imputed=False)
        all_food = list(food_logs) if food_logs else []
        
        if include_imputed:
            try:
                from carb_imputation import detect_and_impute_missing_meals
                readings = db.get_history(limit_hours=hours)
                imputed_meals = detect_and_impute_missing_meals(
                    sorted_readings=sorted(readings, key=lambda x: x['timestamp']),
                    sorted_food_logs=sorted(food_logs, key=lambda x: x['timestamp']),
                    min_confidence=0.50
                )
                if imputed_meals:
                    all_food.extend(imputed_meals)
            except Exception as imp_err:
                print(f"Error imputing meals: {imp_err}")
            
        for f in all_food:
            if isinstance(f.get('timestamp'), datetime):
                f['timestamp'] = f['timestamp'].isoformat()
            if 'is_imputed' not in f or f['is_imputed'] is None:
                f['is_imputed'] = False
            for k, v in list(f.items()):
                if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
                    f[k] = None

        all_food.sort(key=lambda x: str(x.get('timestamp') or ''))
        return all_food
    except Exception as e:
        print(f"Error fetching food history: {e}")
        return []

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

@app.delete("/api/insulin/{dose_id}")
def api_delete_insulin_dose(dose_id: int):
    """Deletes a logged insulin dose from the database."""
    try:
        from db import delete_insulin_dose
        deleted = delete_insulin_dose(dose_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Insulin dose not found or already deleted.")
        return {"success": True, "message": f"Dose #{dose_id} deleted successfully."}
    except HTTPException:
        raise
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
    gemini_api_key: Optional[str] = None
    enabled: bool = True

@app.post("/api/telegram/config")
def api_save_telegram_config(payload: TelegramConfigRequest):
    """Saves Telegram Bot Token, Chat ID, and optional Gemini Vision API key."""
    try:
        from telegram_bot import save_telegram_config, start_telegram_polling
        from telegram_scheduler import start_telegram_scheduler
        save_telegram_config(payload.bot_token, payload.chat_id, payload.enabled)
        if payload.gemini_api_key is not None:
            db.set_system_setting("gemini_config", {"api_key": payload.gemini_api_key.strip()})
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

class CustomAlertRequest(BaseModel):
    message: str

@app.post("/api/telegram/custom_alert")
def api_send_custom_alert(req: CustomAlertRequest):
    """Sends a customizable alert based on projected dose need."""
    try:
        from telegram_bot import send_telegram_message, get_live_patient_summary
        summary = get_live_patient_summary()
        
        bg_text = f"{summary['glucose']:.0f} mg/dL" if summary else "Unknown"
        iob = summary['iob'] if summary else 0.0
        corr = summary.get("correction", 0.0) if summary else 0.0
        
        corr_txt = f"Projected Dose Need: <b>{corr:.1f} U</b>" if corr > 0 else "No correction needed currently."
        
        msg = (
            f"🔔 <b>Custom Alert</b>\n"
            f"<b>{bg_text}</b> • IOB: {iob:.1f} U\n"
            f"👉 {corr_txt}\n\n"
            f"<i>\"{req.message}\"</i>"
        )
        
        keyboard = None
        if corr > 0:
            keyboard = {
                "inline_keyboard": [
                    [{"text": f"✓ Log {corr:.1f} U Correction", "callback_data": f"took_correction:{corr:.1f}"}]
                ]
            }
            
        res = send_telegram_message(msg, reply_markup=keyboard)
        if res.get("success"):
            if keyboard and res.get("result"):
                from telegram_bot import schedule_message_deletion
                schedule_message_deletion(res["result"]["message_id"], minutes=10)
            return {"success": True, "message": "Custom alert sent successfully!"}
        else:
            raise HTTPException(status_code=400, detail=res.get("error", "Failed to send message via Telegram API."))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Multi-Bot Ingress Webhook Routes & Response Normalizer ---

def normalize_webhook_response(res: Any = None, default_action: str = "processed") -> dict:
    """Ensures consistent {"status": ..., "action": ..., "details": ...} JSON structure."""
    if not isinstance(res, dict):
        return {"status": "ok", "action": default_action, "details": {}}

    status = res.get("status", "ok")
    action = res.get("action", default_action)

    details = res.get("details")
    if not isinstance(details, dict):
        details = {k: v for k, v in res.items() if k not in ["status", "action"]}

    return {
        "status": status,
        "action": action,
        "details": details
    }


def _validate_webhook_secret(request: Request, expected_secret: Optional[str]) -> bool:
    if not expected_secret or request is None:
        return True
    header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    return header_secret == expected_secret


@app.post("/api/telegram/webhook")
async def api_telegram_webhook(request: Request = None):
    """Processes incoming updates and button clicks for GlucoTrack Bot."""
    try:
        if request is None:
            return {"status": "ok", "action": "noop", "details": {}}

        stored_cfg = db.get_system_setting("telegram_config") or {}
        secret = os.getenv("TELEGRAM_WEBHOOK_SECRET") or stored_cfg.get("secret_token")
        if not _validate_webhook_secret(request, secret):
            raise HTTPException(status_code=403, detail="Invalid webhook secret token.")

        try:
            body_bytes = await request.body()
            if not body_bytes or not body_bytes.strip():
                return {"status": "error", "action": "invalid_json", "details": {"message": "Empty request body"}}
            update = await request.json()
        except Exception:
            return {"status": "error", "action": "invalid_json", "details": {"message": "Invalid JSON body"}}

        if not update or not isinstance(update, dict):
            return {"status": "ok", "action": "noop", "details": {}}

        from telegram_bot import handle_telegram_update
        res = handle_telegram_update(update)
        return normalize_webhook_response(res, default_action="telegram_update_processed")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[GlucoTrack Ingress Error]: {e}")
        return {"status": "error", "action": "handler_error", "details": {"message": str(e), "type": type(e).__name__}}


@app.post("/api/medbot/webhook")
async def api_medbot_webhook(request: Request = None):
    """Processes incoming updates for MedFlowAssist Bot (@medflowassist_bot)."""
    try:
        if request is None:
            return {"status": "ok", "action": "noop", "details": {}}

        stored_cfg = db.get_system_setting("med_bot_config") or {}
        secret = os.getenv("MED_BOT_WEBHOOK_SECRET") or stored_cfg.get("secret_token")
        if not _validate_webhook_secret(request, secret):
            raise HTTPException(status_code=403, detail="Invalid webhook secret token.")

        try:
            body_bytes = await request.body()
            if not body_bytes or not body_bytes.strip():
                return {"status": "error", "action": "invalid_json", "details": {"message": "Empty request body"}}
            update = await request.json()
        except Exception:
            return {"status": "error", "action": "invalid_json", "details": {"message": "Invalid JSON body"}}

        if not update or not isinstance(update, dict):
            return {"status": "ok", "action": "noop", "details": {}}

        from med_bot import handle_med_webhook
        res = handle_med_webhook(update)
        return normalize_webhook_response(res, default_action="med_update_processed")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[MedFlow Ingress Error]: {e}")
        return {"status": "error", "action": "handler_error", "details": {"message": str(e), "type": type(e).__name__}}


@app.post("/api/monkebot/webhook")
async def api_monkebot_webhook(request: Request = None):
    """Processes incoming updates for MonkeHelper Master Hub (@monkehelper_bot)."""
    try:
        if request is None:
            return {"status": "ok", "action": "noop", "details": {}}

        stored_cfg = db.get_system_setting("monke_bot_config") or {}
        secret = os.getenv("MONKE_BOT_WEBHOOK_SECRET") or stored_cfg.get("secret_token")
        if not _validate_webhook_secret(request, secret):
            raise HTTPException(status_code=403, detail="Invalid webhook secret token.")

        try:
            body_bytes = await request.body()
            if not body_bytes or not body_bytes.strip():
                return {"status": "error", "action": "invalid_json", "details": {"message": "Empty request body"}}
            update = await request.json()
        except Exception:
            return {"status": "error", "action": "invalid_json", "details": {"message": "Invalid JSON body"}}

        if not update or not isinstance(update, dict):
            return {"status": "ok", "action": "noop", "details": {}}

        from monke_bot import handle_monke_webhook
        res = handle_monke_webhook(update)
        return normalize_webhook_response(res, default_action="monke_update_processed")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[MonkeHelper Ingress Error]: {e}")
        return {"status": "error", "action": "handler_error", "details": {"message": str(e), "type": type(e).__name__}}


@app.post("/api/biometrics/webhook")
async def api_biometrics_webhook(request: Request = None):
    """Processes incoming updates for Circadian & Biometrics Bot."""
    try:
        if request is None:
            return {"status": "ok", "action": "noop", "details": {}}

        stored_cfg = db.get_system_setting("biometrics_bot_config") or {}
        secret = os.getenv("BIOMETRICS_BOT_WEBHOOK_SECRET") or stored_cfg.get("secret_token")
        if not _validate_webhook_secret(request, secret):
            raise HTTPException(status_code=403, detail="Invalid webhook secret token.")

        try:
            body_bytes = await request.body()
            if not body_bytes or not body_bytes.strip():
                return {"status": "error", "action": "invalid_json", "details": {"message": "Empty request body"}}
            update = await request.json()
        except Exception:
            return {"status": "error", "action": "invalid_json", "details": {"message": "Invalid JSON body"}}

        if not update or not isinstance(update, dict):
            return {"status": "ok", "action": "noop", "details": {}}

        from biometrics_bot import handle_biometrics_webhook
        res = handle_biometrics_webhook(update)
        return normalize_webhook_response(res, default_action="biometrics_update_processed")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Biometrics Ingress Error]: {e}")
        return {"status": "error", "action": "handler_error", "details": {"message": str(e), "type": type(e).__name__}}


# --- Multi-Bot Polling Status & Lifecycle Management ---

@app.get("/api/bots/polling/status")
def api_bots_polling_status(bot_id: Optional[str] = None):
    """Returns diagnostic health and polling status of all registered bots."""
    try:
        from multi_bot_manager import multi_bot_manager
        return {"status": "ok", "data": multi_bot_manager.get_status(bot_id=bot_id)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/bots/polling/start")
def api_bots_polling_start(bot_id: Optional[str] = None):
    """Starts all bot polling workers or a specific bot."""
    try:
        from multi_bot_manager import multi_bot_manager
        if bot_id:
            res = multi_bot_manager.start_bot(bot_id)
        else:
            multi_bot_manager.start_all()
            res = True
        return {"status": "ok", "started": res}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/bots/polling/stop")
def api_bots_polling_stop(bot_id: Optional[str] = None):
    """Stops all bot polling workers or a specific bot."""
    try:
        from multi_bot_manager import multi_bot_manager
        if bot_id:
            res = multi_bot_manager.stop_bot(bot_id)
        else:
            multi_bot_manager.stop_all()
            res = True
        return {"status": "ok", "stopped": res}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/bots/polling/restart")
def api_bots_polling_restart(bot_id: Optional[str] = None):
    """Restarts bot polling workers."""
    try:
        from multi_bot_manager import multi_bot_manager
        if bot_id:
            res = multi_bot_manager.restart_bot(bot_id)
        else:
            multi_bot_manager.stop_all()
            multi_bot_manager.start_all()
            res = True
        return {"status": "ok", "restarted": res}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.on_event("startup")
def on_app_startup():
    """Starts background Telegram proactive monitor and long-polling daemon on application boot."""
    try:
        from telegram_scheduler import start_telegram_scheduler
        start_telegram_scheduler()
    except Exception as e:
        print(f"[AppStartup] Could not start Telegram scheduler: {e}")

    try:
        polling_enabled = os.getenv("ENABLE_BOT_POLLING", "false").lower() in ("true", "1", "yes")
        if polling_enabled:
            from multi_bot_manager import multi_bot_manager
            multi_bot_manager.start_all()
            print("[AppStartup] MultiBotPollingManager started.")
    except Exception as e:
        print(f"[AppStartup] Could not start MultiBotPollingManager: {e}")


@app.on_event("shutdown")
def on_app_shutdown():
    """Performs clean graceful shutdown of background polling threads and schedulers."""
    try:
        from multi_bot_manager import multi_bot_manager
        multi_bot_manager.stop_all(timeout=5.0)
        print("[AppShutdown] MultiBotPollingManager stopped.")
    except Exception as e:
        print(f"[AppShutdown] Error stopping MultiBotPollingManager: {e}")

    try:
        from telegram_scheduler import stop_telegram_scheduler
        stop_telegram_scheduler()
        print("[AppShutdown] Telegram scheduler stopped.")
    except Exception as e:
        print(f"[AppShutdown] Error stopping Telegram scheduler: {e}")


