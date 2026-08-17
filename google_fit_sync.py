import os
import json
import urllib.parse
from datetime import datetime, timezone, timedelta
import requests
from dotenv import load_dotenv
import db

load_dotenv()

# Google OAuth Constants
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
FITNESS_API_BASE = "https://www.googleapis.com/fitness/v1/users/me"

# Standard Scopes for Sleep, Activity, Heart Rate, and Body metrics
FITNESS_SCOPES = [
    "https://www.googleapis.com/auth/fitness.sleep.read",
    "https://www.googleapis.com/auth/fitness.activity.read",
    "https://www.googleapis.com/auth/fitness.heart_rate.read",
    "https://www.googleapis.com/auth/fitness.body.read"
]


def get_google_credentials():
    """
    Retrieves Google Client ID and Secret from environment or database system_settings.
    """
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        stored = db.get_system_setting("google_oauth_credentials")
        if stored and isinstance(stored, dict):
            client_id = client_id or stored.get("client_id")
            client_secret = client_secret or stored.get("client_secret")
            
    return client_id, client_secret


def save_google_credentials(client_id, client_secret):
    """Saves Google OAuth client configuration to database."""
    db.set_system_setting("google_oauth_credentials", {
        "client_id": client_id.strip() if client_id else "",
        "client_secret": client_secret.strip() if client_secret else ""
    })


def get_authorization_url(redirect_uri, state=None):
    """
    Generates the Google OAuth 2.0 authorization URL for user consent.
    """
    client_id, _ = get_google_credentials()
    if not client_id:
        raise ValueError("Google Client ID is not configured. Please set GOOGLE_CLIENT_ID in your environment or Settings.")

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(FITNESS_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true"
    }
    if state:
        params["state"] = state

    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code_for_tokens(code, redirect_uri):
    """
    Exchanges the authorization code for access_token and refresh_token.
    """
    client_id, client_secret = get_google_credentials()
    if not client_id or not client_secret:
        raise ValueError("Google Client ID or Secret is not configured.")

    payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code"
    }

    resp = requests.post(GOOGLE_TOKEN_URL, data=payload, timeout=15)
    if not resp.ok:
        raise RuntimeError(f"Failed to exchange token with Google: {resp.status_code} {resp.text}")

    data = resp.json()
    now_ts = datetime.now(timezone.utc).timestamp()
    expires_in = data.get("expires_in", 3600)
    data["expires_at"] = now_ts + expires_in

    # Store tokens securely in system_settings
    db.set_system_setting("google_fit_tokens", data)
    return data


def get_valid_access_token():
    """
    Retrieves a valid Google access token, automatically refreshing it if expired.
    """
    tokens = db.get_system_setting("google_fit_tokens")
    if not tokens or not isinstance(tokens, dict) or "access_token" not in tokens:
        return None

    now_ts = datetime.now(timezone.utc).timestamp()
    expires_at = tokens.get("expires_at", 0)

    # If token is still valid (with 60s buffer), return it
    if now_ts < (expires_at - 60):
        return tokens["access_token"]

    # Token is expired or expiring, attempt refresh
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("[GoogleFit] No refresh token found. Re-authentication required.")
        return None

    client_id, client_secret = get_google_credentials()
    if not client_id or not client_secret:
        print("[GoogleFit] Missing Client ID/Secret for token refresh.")
        return None

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }

    try:
        resp = requests.post(GOOGLE_TOKEN_URL, data=payload, timeout=15)
        if resp.ok:
            new_tokens = resp.json()
            tokens["access_token"] = new_tokens["access_token"]
            tokens["expires_at"] = now_ts + new_tokens.get("expires_in", 3600)
            if "refresh_token" in new_tokens:
                tokens["refresh_token"] = new_tokens["refresh_token"]
            db.set_system_setting("google_fit_tokens", tokens)
            return tokens["access_token"]
        else:
            print(f"[GoogleFit] Token refresh failed: {resp.status_code} {resp.text}")
            return None
    except Exception as e:
        print(f"[GoogleFit] Exception during token refresh: {e}")
        return None


def sync_google_fit_sleep(hours_back=720):
    """
    Fetches sleep sessions from Google Fit and inserts them into PostgreSQL.
    Google Fit Activity Type 72 = Sleep
    """
    token = get_valid_access_token()
    if not token:
        return {"success": False, "message": "Google Fit is not connected or token expired."}

    now = datetime.now(timezone.utc)
    start_time = now - timedelta(hours=hours_back)
    
    start_iso = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    url = f"{FITNESS_API_BASE}/sessions?startTime={start_iso}&endTime={end_iso}&activityType=72"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if not resp.ok:
            return {"success": False, "message": f"Google Fit API error: {resp.status_code} {resp.text}"}

        data = resp.json()
        sessions = data.get("session", [])
        
        parsed_sessions = []
        for s in sessions:
            start_ms = int(s.get("startTimeMillis", 0))
            end_ms = int(s.get("endTimeMillis", 0))
            if start_ms == 0 or end_ms == 0:
                continue

            start_dt = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc)
            end_dt = datetime.fromtimestamp(end_ms / 1000.0, tz=timezone.utc)
            duration_mins = (end_ms - start_ms) / (1000.0 * 60.0)

            session_id = s.get("id", f"sleep_{start_ms}")
            session_name = s.get("name", "Sleep Session")
            activity_type = s.get("activityType", 72)
            
            type_str = "sleep"
            if activity_type == 72:
                type_str = "sleep"
            elif activity_type == 109:
                type_str = "sleep.light"
            elif activity_type == 110:
                type_str = "sleep.deep"
            elif activity_type == 111:
                type_str = "sleep.rem"
            elif activity_type == 112:
                type_str = "sleep.awake"

            parsed_sessions.append({
                "session_id": session_id,
                "start_time": start_dt,
                "end_time": end_dt,
                "session_type": type_str,
                "session_name": session_name,
                "duration_minutes": round(duration_mins, 1)
            })

        count = db.insert_health_sessions(parsed_sessions)
        return {
            "success": True,
            "synced_count": count,
            "total_found": len(parsed_sessions)
        }

    except Exception as e:
        return {"success": False, "message": f"Exception fetching sleep data: {e}"}


def sync_google_fit_metrics(hours_back=168):
    """
    Fetches aggregate step counts and resting heart rates from Google Fit.
    """
    token = get_valid_access_token()
    if not token:
        return {"success": False, "message": "Not authenticated."}

    now = datetime.now(timezone.utc)
    start_time = now - timedelta(hours=hours_back)
    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    url = f"{FITNESS_API_BASE}/dataset:aggregate"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    payload = {
        "aggregateBy": [
            {"dataTypeName": "com.google.step_count.delta"},
            {"dataTypeName": "com.google.heart_rate.bpm"}
        ],
        "bucketByTime": {"durationMillis": 3600000},
        "startTimeMillis": start_ms,
        "endTimeMillis": end_ms
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
        if not resp.ok:
            return {"success": False, "message": f"Metrics API error: {resp.status_code}"}

        data = resp.json()
        metrics = []

        for bucket in data.get("bucket", []):
            b_start_ms = int(bucket.get("startTimeMillis", 0))
            b_time = datetime.fromtimestamp(b_start_ms / 1000.0, tz=timezone.utc)

            for dataset in bucket.get("dataset", []):
                data_type = dataset.get("dataSourceId", "")
                for point in dataset.get("point", []):
                    for val in point.get("value", []):
                        if "intVal" in val and "step_count" in data_type:
                            steps = val["intVal"]
                            if steps > 0:
                                metrics.append({
                                    "timestamp": b_time,
                                    "metric_type": "steps",
                                    "value": float(steps)
                                })
                        elif "fpVal" in val and "heart_rate" in data_type:
                            hr = val["fpVal"]
                            if hr > 0:
                                metrics.append({
                                    "timestamp": b_time,
                                    "metric_type": "heart_rate",
                                    "value": float(hr)
                                })

        count = db.insert_health_metrics(metrics)
        return {"success": True, "metrics_count": count}

    except Exception as e:
        return {"success": False, "message": str(e)}


def sync_all_google_fit():
    """Syncs both sleep sessions and health metrics."""
    sleep_res = sync_google_fit_sleep(hours_back=720)
    metrics_res = sync_google_fit_metrics(hours_back=168)
    
    db.set_system_setting("google_fit_last_sync", {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sleep": sleep_res,
        "metrics": metrics_res
    })
    
    return {
        "sleep": sleep_res,
        "metrics": metrics_res
    }
