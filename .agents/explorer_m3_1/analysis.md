# Analysis Report: R3 Time-of-Day Nutritional Impact Model & Dashboard Exposure

**Milestone**: Milestone 3 (R3 Time-of-Day Nutritional Impact Model)  
**Author**: Explorer 1  
**Working Directory**: `c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\explorer_m3_1`  
**Date**: 2026-08-04  

---

## 1. Executive Summary

This report presents the architectural analysis and implementation design for **R3: Time-of-Day Nutritional Impact Model & Dashboard Exposure**. The feature analyzes historical continuous glucose monitor (CGM) and insulin data across four circadian time-of-day buckets (Morning, Afternoon, Evening, Night) to quantify glucose excursion dynamics (peak rise in mg/dL, peak latency in minutes, and circadian modifier $M_{\text{tod}}$), serving these insights via `/api/nutritional-impact` and rendering them in a dedicated visual panel on the Gluco Track dashboard (`templates/index.html`).

---

## 2. Core Architecture & Existing System State

### 2.1 File System & Architecture Inventory
- **`ml_heuristics.py`**: Contains existing statistical heuristics, including `get_time_of_day_bucket(dt, timezone_str)`, `calculate_personalized_isf()`, and `train_predictive_model()`. Uses `pytz` for timezone adjustments and local `heuristics_params.json` for persistent model parameters.
- **`app.py`**: Built on FastAPI (not Flask, despite legacy naming conventions in prompt). Provides endpoints such as `/api/glucose/latest`, `/api/glucose/history`, `/api/predictions`, and `/api/heuristics/status`.
- **`db.py`**: Interface to PostgreSQL (`psycopg2`). Supports `get_history(limit_hours)` and `get_insulin_history(limit_hours)`.
- **`templates/index.html`**: Single Page Application with glassmorphic dark-mode styling (`.glass-panel`), Google Fonts (Inter & Outfit), Chart.js v4 integration, and vanilla JavaScript async fetch routines.

### 2.2 Existing Circadian Bucket Definition
`ml_heuristics.py` (lines 42–58) currently defines `get_time_of_day_bucket(dt, timezone_str)` with hour boundaries:
- **Morning**: `04:00 - 11:00` (`4 <= hour < 11`)
- **Afternoon**: `11:00 - 17:00` (`11 <= hour < 17`)
- **Evening**: `17:00 - 22:00` (`17 <= hour < 22`)
- **Night**: `22:00 - 04:00` (`hour >= 22 or hour < 4`)

*Note: Existing keys in `ml_heuristics.py` use lowercase (`"morning"`, `"afternoon"`, `"evening"`, `"night"`), whereas `PROJECT.md` API specification uses capitalized keys (`"Morning"`, `"Afternoon"`, `"Evening"`, `"Night"`). The API serialization layer will normalize bucket names to capitalized keys.*

---

## 3. Mathematical & Algorithmic Design for $M_{\text{tod}}$ (`ml_heuristics.py`)

### 3.1 Postprandial Excursion Extraction Algorithm
To compute time-of-day nutritional impact without explicit manual meal logs for every entry, the engine extracts postprandial glucose excursions from historical CGM timeseries:

1. **Excursion Event Onset Detection**:
   - Scan chronological glucose readings $G(t)$.
   - Identify candidate meal/spike events where either:
     - A rapid/meal insulin dose is recorded within 15 minutes, OR
     - A continuous glucose rise $\Delta G = G(t + \Delta t) - G(t) \ge 15\text{ mg/dL}$ occurs within 30 minutes in the absence of preceding hypo recovery.
2. **Peak Rise ($\Delta G_{\text{peak}}$) & Latency ($T_{\text{peak}}$) Calculation**:
   - For each excursion event at $t_{\text{start}}$, monitor glucose up to 180 minutes ($t_{\text{start}} + 3\text{h}$).
   - $G_{\text{peak}} = \max(\{G(t) \mid t_{\text{start}} \le t \le t_{\text{start}} + 180\text{m}\})$.
   - $\Delta G_{\text{peak}} = G_{\text{peak}} - G(t_{\text{start}})$.
   - $T_{\text{peak}} = t(G_{\text{peak}}) - t_{\text{start}}$ (latency in minutes).
3. **Bucket Aggregation & $M_{\text{tod}}$ Calculation**:
   - Group identified excursions by circadian bucket based on $t_{\text{start}}$.
   - Compute mean peak rise $\bar{\Delta G}_{\text{bucket}}$ and mean latency $\bar{T}_{\text{bucket}}$ for each bucket.
   - Establish baseline excursion: $\bar{\Delta G}_{\text{baseline}} = \bar{\Delta G}_{\text{Afternoon}}$ (or global average if Afternoon count $< 3$).
   - Compute circadian nutritional modifier:
     $$M_{\text{tod, bucket}} = \frac{\bar{\Delta G}_{\text{bucket}}}{\bar{\Delta G}_{\text{baseline}}}$$

### 3.2 Clinical Heuristic Defaults & Fallbacks
When historical dataset has $< 3$ valid excursion events for a bucket, default clinical reference values specified in `PROJECT.md` are used:
- **Morning**: `peak_rise_mgdl = 45.2`, `peak_latency_min = 55`, `modifier = 1.25`
- **Afternoon**: `peak_rise_mgdl = 35.0`, `peak_latency_min = 45`, `modifier = 1.00`
- **Evening**: `peak_rise_mgdl = 40.1`, `peak_latency_min = 50`, `modifier = 1.10`
- **Night**: `peak_rise_mgdl = 52.8`, `peak_latency_min = 75`, `modifier = 1.40`

### 3.3 Rule-Based Recommendation Generator
The engine evaluates the computed metrics against clinical rules to produce dynamic recommendations:
- **Night Spike Warning**: If `Night.modifier > 1.25` or `Night.peak_rise_mgdl > 48.0`:
  > *"High nocturnal carb impact detected: Night meals cause +{peak_rise} mg/dL spike with {peak_latency} min peak latency. Avoid late-night carbohydrates after 22:00."*
- **Dawn Phenomenon / Morning Sensitivity**: If `Morning.modifier > 1.15`:
  > *"Dawn effect observed: Morning glucose rise is {round((modifier-1)*100)}% higher than afternoon baseline. Consider increasing morning pre-bolus lead time or choosing lower glycemic index foods."*
- **Optimal Sensitivity Window**:
  > *"Afternoon sensitivity is optimal (1.00x baseline multiplier). Best window for complex carbohydrate intake."*

---

## 4. API Endpoint Specification (`app.py`)

### 4.1 Route Definition
- **Route**: `GET /api/nutritional-impact`
- **Alias Route**: `GET /api/nutritional-impact/summary`
- **Query Parameters**:
  - `hours` (optional `int`, default: `720`, min: `24`, max: `4320` — 30 days window)

### 4.2 JSON Response Contract (100% Schema Alignment)
```json
{
  "time_buckets": {
    "Morning": {
      "peak_rise_mgdl": 45.2,
      "peak_latency_min": 55,
      "modifier": 1.25
    },
    "Afternoon": {
      "peak_rise_mgdl": 35.0,
      "peak_latency_min": 45,
      "modifier": 1.00
    },
    "Evening": {
      "peak_rise_mgdl": 40.1,
      "peak_latency_min": 50,
      "modifier": 1.10
    },
    "Night": {
      "peak_rise_mgdl": 52.8,
      "peak_latency_min": 75,
      "modifier": 1.40
    }
  },
  "recommendations": [
    "High nocturnal carb impact detected: Night meals cause +52.8 mg/dL spike with 75 min peak latency. Avoid late-night carbohydrates after 22:00.",
    "Dawn effect observed: Morning glucose rise is 25% higher than afternoon baseline. Consider increasing morning pre-bolus lead time."
  ]
}
```

---

## 5. UI Dashboard Component Specification (`templates/index.html`)

### 5.1 Glassmorphic UI Panel Layout
A dedicated glassmorphic card will be added into the dashboard UI grid below the main charts and above/alongside the Heuristics section:

```html
<!-- Circadian Nutritional Impact Panel -->
<div class="glass-panel" style="display: flex; flex-direction: column; gap: 1rem;">
    <div style="display: flex; justify-content: space-between; align-items: center;">
        <h3 class="chart-title">Circadian Nutritional Impact Modifiers (M<sub>tod</sub>)</h3>
        <span class="status-indicator" style="font-size: 0.75rem;">Diurnal Excursion Model</span>
    </div>
    
    <!-- 4 Circadian Bucket Cards -->
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem;">
        <!-- Morning -->
        <div style="background: rgba(0,0,0,0.25); border: 1px solid var(--panel-border); border-radius: 12px; padding: 1rem;">
            ... Morning Stats & Badge ...
        </div>
        <!-- Afternoon -->
        <div style="background: rgba(0,0,0,0.25); border: 1px solid var(--panel-border); border-radius: 12px; padding: 1rem;">
            ... Afternoon Stats & Badge ...
        </div>
        <!-- Evening -->
        <div style="background: rgba(0,0,0,0.25); border: 1px solid var(--panel-border); border-radius: 12px; padding: 1rem;">
            ... Evening Stats & Badge ...
        </div>
        <!-- Night -->
        <div style="background: rgba(0,0,0,0.25); border: 1px solid var(--panel-border); border-radius: 12px; padding: 1rem;">
            ... Night Stats & Badge ...
        </div>
    </div>

    <!-- Dynamic Recommendations Box -->
    <div style="background: rgba(59, 130, 246, 0.05); border: 1px solid rgba(59, 130, 246, 0.2); border-radius: 12px; padding: 1rem;">
        <h4 style="font-size: 0.85rem; text-transform: uppercase; color: var(--color-accent); margin-bottom: 0.5rem;">Personalized Circadian Recommendations</h4>
        <ul id="nutr-recommendations-list" style="padding-left: 1.25rem; font-size: 0.85rem; color: var(--text-primary); display: flex; flex-direction: column; gap: 0.4rem;">
            <li>Loading circadian insights...</li>
        </ul>
    </div>
</div>
```

### 5.2 JavaScript Async Fetch Integration
Add `fetchNutritionalImpact()` in `templates/index.html`:
```javascript
async function fetchNutritionalImpact() {
    try {
        const res = await fetch('/api/nutritional-impact');
        if (!res.ok) throw new Error('Failed to fetch nutritional impact');
        const data = await res.json();
        
        // Update Bucket UI Badges (Morning, Afternoon, Evening, Night)
        ['Morning', 'Afternoon', 'Evening', 'Night'].forEach(bucket => {
            const bData = data.time_buckets[bucket];
            if (bData) {
                const key = bucket.toLowerCase();
                document.getElementById(`nutr-mod-${key}`).innerText = `${bData.modifier.toFixed(2)}x`;
                document.getElementById(`nutr-rise-${key}`).innerText = `+${bData.peak_rise_mgdl.toFixed(1)} mg/dL`;
                document.getElementById(`nutr-latency-${key}`).innerText = `${bData.peak_latency_min}m peak`;
            }
        });

        // Update Recommendations list
        const recList = document.getElementById('nutr-recommendations-list');
        if (data.recommendations && data.recommendations.length > 0) {
            recList.innerHTML = data.recommendations.map(r => `<li>${r}</li>`).join('');
        } else {
            recList.innerHTML = `<li>No circadian impact anomalies detected. Maintain standard meal timing.</li>`;
        }
    } catch (err) {
        console.warn("Could not fetch nutritional impact:", err);
    }
}
```

---

## 6. Implementation Code Modifications Summary

To fulfill Milestone 3 requirements, the following implementations are proposed (to be executed by Implementer subagent):

1. **`ml_heuristics.py`**:
   - Implement `calculate_nutritional_impact(hours_back=720, timezone_str="America/New_York")` function.
   - Computes peak rise, peak latency, $M_{\text{tod}}$ per time bucket with default fallbacks.
   - Generates string list of clinical recommendations.
2. **`app.py`**:
   - Add `@app.get("/api/nutritional-impact")` and `@app.get("/api/nutritional-impact/summary")` endpoints invoking `calculate_nutritional_impact()`.
3. **`templates/index.html`**:
   - Add glassmorphic panel HTML markup for circadian impact modifiers.
   - Add JS function `fetchNutritionalImpact()` and trigger it on initial load and post-CSV upload.

---

## 7. Verification Method
1. Launch FastAPI application server using `python main.py` or `uvicorn app:app --port 8080`.
2. Query `GET http://localhost:8080/api/nutritional-impact` and verify JSON schema returns `time_buckets` (`Morning`, `Afternoon`, `Evening`, `Night`) and `recommendations` list.
3. Open `http://localhost:8080/` in browser and confirm visual glassmorphic panel displays circadian modifiers ($M_{\text{tod}}$), peak rise, latency, and recommendations.
