import os
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel
from fastapi import FastAPI, File, UploadFile, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from parser import parse_libreview_csv
from db import get_latest_reading, get_history, get_statistics, insert_readings, insert_insulin_doses, get_insulin_history
from prediction import predict_glucose, calculate_iob, suggest_correction

app = FastAPI(title="Gluco Track API", version="1.0.0")

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
            
            imputed = detect_and_impute_missing_doses(glucose_readings, raw_doses)
            
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
    recent_doses = get_insulin_history(4)
    iob = calculate_iob(recent_doses)
    
    # Estimate correction bolus using correct timestamp context
    suggested = suggest_correction(latest['value'], iob, target_glucose=target, isf=isf, current_time=latest['timestamp'])
    
    # Resolve what ISF was actually used to display on UI
    used_isf = isf
    if used_isf is None:
        try:
            from ml_heuristics import load_heuristics_params, get_time_of_day_bucket
            params = load_heuristics_params()
            bucket = get_time_of_day_bucket(latest['timestamp'])
            used_isf = params.get("isf", {}).get(bucket, 50.0)
        except Exception:
            used_isf = 50.0

    # Format times for JSON response
    latest['timestamp'] = latest['timestamp'].isoformat()
    
    return {
        "current_glucose": latest['value'],
        "latest_reading": latest,
        "predictions": predictions,
        "active_iob": iob,
        "suggested_correction": suggested,
        "parameters": {
            "target_glucose": target,
            "isf": used_isf
        }
    }

@app.post("/api/heuristics/train")
def api_train_heuristics(days: int = Query(default=30, ge=7, le=90)):
    """Triggers the statistical machine learning model training job on the server."""
    try:
        from ml_heuristics import train_predictive_model
        success, msg = train_predictive_model(history_days=days)
        if not success:
            raise HTTPException(status_code=400, detail=msg)
        return {"success": True, "message": msg}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class FoodEntry(BaseModel):
    carbs_g: float
    food_type: str = None
    timestamp: datetime = None

@app.post("/api/food/log")
async def log_food(entry: FoodEntry):
    ts = entry.timestamp if entry.timestamp else datetime.now(timezone.utc)
    try:
        inserted_id = db.insert_food_log(
            carbs_g=entry.carbs_g,
            timestamp=ts,
            food_type=entry.food_type
        )
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
def api_log_insulin(dose: InsulinDoseLog):
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
        return {"message": "Insulin dose logged successfully.", "inserted": 1}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/api/shortcut/log")
def api_shortcut_log(
    units: float = Query(..., description="Units of insulin"),
    type: str = Query(..., description="Type of insulin (rapid, long, meal, correction, change)")
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
        return {"success": True, "message": f"Successfully logged {units}U of {dose_type} via Android Shortcut."}
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
async def api_upload(file: UploadFile = File(...)):
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

