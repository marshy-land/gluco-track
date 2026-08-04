# Scope: M3 - Time-of-Day Nutritional Impact Model & Dashboard Exposure

## Architecture
- Backend logic in `ml_heuristics.py` calculating time-of-day blood sugar impact modifiers ($M_{\text{tod}}$) across four circadian buckets:
  - Morning: 04:00 - 11:00
  - Afternoon: 11:00 - 17:00
  - Evening: 17:00 - 22:00
  - Night: 22:00 - 04:00
- Flask API endpoint in `app.py` (`/api/nutritional-impact` or `/api/nutritional-impact/summary`) serving model metrics and recommendations.
- Dashboard panel in `templates/index.html` presenting circadian impact modifiers, visual indicators, and personalized recommendations based on user historical glucose data.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Circadian Buckets & Modifiers ($M_{\text{tod}}$) | Calculate blood glucose impact modifiers per time bucket based on historical data | M3 | ORIGINAL_REQUEST §R3 |
| 2 | Nutritional Impact API Endpoint | `/api/nutritional-impact` endpoint returning modifiers and recommendations | M3 | ORIGINAL_REQUEST §R3 |
| 3 | Dashboard UI Panel | Dedicated visual panel in `templates/index.html` rendering modifiers and recommendations | M3 | ORIGINAL_REQUEST §R3 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | M3 | Time-of-Day Nutritional Impact Model & Dashboard UI | M1, M2 | DONE |

## Interface Contracts
### `ml_heuristics.py` ↔ `app.py`
- Function to compute nutritional impact modifiers given user glucose/meal history.
- Returns dict with keys for each time-of-day bucket, calculated modifier factors, confidence scores, and action items/recommendations.

### `app.py` ↔ `templates/index.html`
- JSON payload from `/api/nutritional-impact` consumed by JS in `templates/index.html` to populate the circadian nutritional impact card/panel.
