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
def api_history(hours: int = Query(default=24, ge=1, le=720)):
    """Retrieves glucose readings within the last N hours (max 30 days / 720h)."""
    readings = get_history(hours)
    for r in readings:
        r['timestamp'] = r['timestamp'].isoformat()
    return readings

@app.get("/api/insulin/history")
def api_insulin_history(hours: int = Query(default=24, ge=1, le=720)):
    """Retrieves insulin logs within the last N hours."""
    doses = get_insulin_history(hours)
    for d in doses:
        d['timestamp'] = d['timestamp'].isoformat()
    return doses

@app.get("/api/predictions")
def api_predictions(
    target: float = Query(default=120.0, description="Target glucose in mg/dL"),
    isf: float = Query(default=50.0, description="Insulin Sensitivity Factor (ISF) in mg/dL/U")
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
    
    # Estimate correction bolus
    suggested = suggest_correction(latest['value'], iob, target_glucose=target, isf=isf)
    
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
            "isf": isf
        }
    }

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

@app.get("/api/glucose/stats")
def api_stats(hours: int = Query(default=24, ge=1, le=720)):
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
