# Analysis: Visual Indicators for Imputed Insulin Doses on Chart.js `insulinChart`

**Explorer**: Explorer 3 (Milestone M2 — Missing Dose Imputation Integration)  
**Target File**: `templates/index.html`  
**Date**: 2026-08-04  

---

## 1. Executive Summary

This investigation analyzes `templates/index.html` to design the visual indicators for missing historical insulin doses estimated by the pharmacodynamic imputation engine (Milestone M2).

### Key Discoveries:
1. **Single File Architecture**: All frontend markup, styling (glassmorphism CSS), and JavaScript logic reside within `templates/index.html`. No external `.js` files exist in static assets.
2. **Chart.js Setup**: Chart.js v4.x and `chartjs-adapter-date-fns` are loaded via CDN (lines 12–13).
3. **Current API Call**: Line 934 calls `fetch('/api/insulin/history?hours=${hours}')` without the required `include_imputed=true` query parameter.
4. **Current Chart Dataset Structure**: `renderInsulinChart(doses)` (lines 966–1055) initializes a single `bar` chart with 4 datasets (`Rapid-Acting`, `Long-Acting`, `Meal`, `Correction`).
5. **Imputed Dose Differentiation**: Adding a 5th dataset (`'Imputed (Estimated)'`) or splitting correction datasets natively provides a distinct legend item, dashed stroke (`borderDash: [5, 5]`), distinct purple translucent fill (`rgba(168, 85, 247, 0.35)`), and extended tooltip callbacks showing dose, timestamp, imputation flag, and confidence score.

---

## 2. Current Implementation Analysis

### 2.1 DOM & Component Locations in `templates/index.html`

- **Canvas Element**: Line 464
  ```html
  <div class="glass-panel" style="min-height: 280px;">
      <h3 class="chart-title" style="margin-bottom: 1.25rem;">Insulin Doses Timeline</h3>
      <div style="position: relative; height: 210px; width: 100%;">
          <canvas id="insulinChart"></canvas>
      </div>
  </div>
  ```

- **Global Instance**: Line 598
  ```javascript
  let insulinChart = null;
  ```

- **Data Fetch Function**: Lines 932–963 (`fetchInsulinHistory(hours)`)
  ```javascript
  async function fetchInsulinHistory(hours) {
      try {
          const res = await fetch(`/api/insulin/history?hours=${hours}`);
          if (!res.ok) throw new Error();
          const data = await res.json();
          ...
          renderInsulinChart(data);
      } catch (err) { ... }
  }
  ```

- **Chart Render Function**: Lines 966–1055 (`renderInsulinChart(doses)`)
  - Currently filters doses by field (`rapid_acting`, `long_acting`, `meal`, `correction`) and creates 4 bar datasets.
  - Does NOT filter by `is_imputed` flag or format confidence scores in tooltips.

---

## 3. Data Integration Contract (`/api/insulin/history?include_imputed=true`)

When updated to `GET /api/insulin/history?hours=${hours}&include_imputed=true`, the API returns an array of dose objects formatted as:

```json
[
  {
    "id": 101,
    "timestamp": "2026-08-04T05:30:00Z",
    "rapid_acting": 2.5,
    "long_acting": null,
    "meal": null,
    "correction": 2.5,
    "user_change": null,
    "is_imputed": false,
    "confidence_score": null
  },
  {
    "id": 102,
    "timestamp": "2026-08-04T06:15:00Z",
    "rapid_acting": 1.8,
    "long_acting": null,
    "meal": null,
    "correction": 1.8,
    "user_change": null,
    "is_imputed": true,
    "confidence_score": 0.88
  }
]
```

### Data Categorization Logic:
- **Logged Rapid-Acting**: `doses.filter(d => !d.is_imputed && d.rapid_acting !== null)`
- **Logged Long-Acting**: `doses.filter(d => !d.is_imputed && d.long_acting !== null)`
- **Logged Meal**: `doses.filter(d => !d.is_imputed && d.meal !== null)`
- **Logged Correction**: `doses.filter(d => !d.is_imputed && d.correction !== null)`
- **Imputed Doses (Estimated)**: `doses.filter(d => d.is_imputed === true)`

---

## 4. Visual Design Specification

### 4.1 Visual Attributes for Imputed Dataset

| Property | Value / Specification | Rationale |
|---|---|---|
| **Dataset Label** | `'Imputed (Estimated)'` | Clear distinction in legend and tooltips |
| **Fill Color (`backgroundColor`)** | `'rgba(168, 85, 247, 0.35)'` | Translucent purple (#a855f7) to indicate non-manually logged estimation |
| **Border Color (`borderColor`)** | `'rgba(168, 85, 247, 0.9)'` | Solid purple border outlining the bar |
| **Border Width (`borderWidth`)** | `2` | Sufficient width for dashed pattern clarity |
| **Border Dash (`borderDash`)** | `[5, 5]` | Explicit requirement: 5px dash, 5px space stroke |
| **Bar Thickness (`barThickness`)** | `8` | Slightly wider or equal to logged correction bars (6px) |

### 4.2 Legend Configuration
Chart.js automatically displays the dataset label `'Imputed (Estimated)'` in the chart legend alongside `Rapid-Acting`, `Long-Acting`, `Meal`, and `Correction`.

### 4.3 Custom Tooltip Callback Design
The tooltip must present:
1. Dose value (e.g. `1.8 U`)
2. Timestamp (formatted via Chart.js date adapter or local time)
3. Imputation status (`[Imputed / Estimated]`)
4. Confidence score (e.g. `Confidence: 88%` or `0.88`)

#### Proposed Tooltip Callback:
```javascript
tooltip: {
    backgroundColor: 'rgba(17, 18, 36, 0.95)',
    borderColor: 'rgba(168, 85, 247, 0.5)',
    borderWidth: 1,
    titleColor: '#9ca3af',
    bodyColor: '#f3f4f6',
    callbacks: {
        label: function(context) {
            const raw = context.raw;
            const label = context.dataset.label || '';
            const val = context.parsed.y;
            if (raw && raw.is_imputed) {
                const confPercent = raw.confidence_score !== null && raw.confidence_score !== undefined
                    ? `${Math.round(raw.confidence_score * 100)}%`
                    : 'N/A';
                return [
                    ` ${label}: ${val} U`,
                    ` Status: Imputed / Estimated`,
                    ` Confidence: ${confPercent}`
                ];
            }
            return ` ${label}: ${val} U`;
        }
    }
}
```

### 4.4 Table Log Integration (`#insulin-tbody`)
In addition to the chart, visual consistency across the dashboard requires updating the Recent Doses Log table:
- Display a small purple badge `Imputed (88%)` in the timestamp cell.
- Highlight imputed rows with a subtle purple background tint (`rgba(168, 85, 247, 0.05)`).

---

## 5. Implementation Proposal (Code Diffs)

### 5.1 Endpoint URL Update in `fetchInsulinHistory`
```javascript
// Before (Line 934):
const res = await fetch(`/api/insulin/history?hours=${hours}`);

// Proposed After:
const res = await fetch(`/api/insulin/history?hours=${hours}&include_imputed=true`);
```

### 5.2 Updated `renderInsulinChart` Function
```javascript
function renderInsulinChart(doses) {
    const ctx = document.getElementById('insulinChart').getContext('2d');
    if (insulinChart) {
        insulinChart.destroy();
    }

    // Separate logged vs imputed doses
    const loggedDoses = doses.filter(d => !d.is_imputed);
    const imputedDoses = doses.filter(d => d.is_imputed);

    const rapidData = loggedDoses.filter(d => d.rapid_acting !== null).map(d => ({ x: new Date(d.timestamp), y: d.rapid_acting, is_imputed: false }));
    const longData = loggedDoses.filter(d => d.long_acting !== null).map(d => ({ x: new Date(d.timestamp), y: d.long_acting, is_imputed: false }));
    const mealData = loggedDoses.filter(d => d.meal !== null).map(d => ({ x: new Date(d.timestamp), y: d.meal, is_imputed: false }));
    const corrData = loggedDoses.filter(d => d.correction !== null).map(d => ({ x: new Date(d.timestamp), y: d.correction, is_imputed: false }));
    
    const imputedData = imputedDoses.map(d => ({
        x: new Date(d.timestamp),
        y: d.correction !== null ? d.correction : (d.rapid_acting !== null ? d.rapid_acting : 0),
        is_imputed: true,
        confidence_score: d.confidence_score
    }));

    insulinChart = new Chart(ctx, {
        type: 'bar',
        data: {
            datasets: [
                {
                    label: 'Rapid-Acting',
                    data: rapidData,
                    backgroundColor: 'rgba(239, 68, 68, 0.85)',
                    borderColor: '#ef4444',
                    borderWidth: 1,
                    barThickness: 6
                },
                {
                    label: 'Long-Acting',
                    data: longData,
                    backgroundColor: 'rgba(245, 158, 11, 0.85)',
                    borderColor: '#f59e0b',
                    borderWidth: 1,
                    barThickness: 10
                },
                {
                    label: 'Meal',
                    data: mealData,
                    backgroundColor: 'rgba(16, 185, 129, 0.85)',
                    borderColor: '#10b981',
                    borderWidth: 1,
                    barThickness: 6
                },
                {
                    label: 'Correction',
                    data: corrData,
                    backgroundColor: 'rgba(59, 130, 246, 0.85)',
                    borderColor: '#3b82f6',
                    borderWidth: 1,
                    barThickness: 6
                },
                {
                    label: 'Imputed (Estimated)',
                    data: imputedData,
                    backgroundColor: 'rgba(168, 85, 247, 0.35)',
                    borderColor: 'rgba(168, 85, 247, 0.9)',
                    borderWidth: 2,
                    borderDash: [5, 5],
                    barThickness: 8
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: true,
                    position: 'top',
                    labels: { color: '#9ca3af', font: { family: 'Inter', size: 10 } }
                },
                tooltip: {
                    backgroundColor: 'rgba(17, 18, 36, 0.95)',
                    borderColor: 'rgba(168, 85, 247, 0.4)',
                    borderWidth: 1,
                    titleColor: '#9ca3af',
                    bodyColor: '#f3f4f6',
                    callbacks: {
                        label: function(context) {
                            const raw = context.raw;
                            const label = context.dataset.label || '';
                            const val = context.parsed.y;
                            if (raw && raw.is_imputed) {
                                const confPercent = raw.confidence_score !== null && raw.confidence_score !== undefined
                                    ? `${Math.round(raw.confidence_score * 100)}%`
                                    : 'N/A';
                                return [
                                    ` ${label}: ${val} U`,
                                    ` Status: Imputed / Estimated`,
                                    ` Confidence: ${confPercent}`
                                ];
                            }
                            return ` ${label}: ${val} U`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    type: 'time',
                    time: {
                        tooltipFormat: 'MMM d, h:mm a',
                        displayFormats: {
                            hour: 'h:mm a',
                            day: 'MMM d'
                        }
                    },
                    grid: { color: 'rgba(255, 255, 255, 0.03)' },
                    ticks: { color: '#9ca3af', font: { family: 'Inter' } }
                },
                y: {
                    beginAtZero: true,
                    title: { display: true, text: 'Units', color: '#9ca3af' },
                    grid: { color: 'rgba(255, 255, 255, 0.03)' },
                    ticks: { color: '#9ca3af', font: { family: 'Inter' } }
                }
            }
        }
    });
}
```

---

## 6. Edge Cases & Verification Plan

1. **Zero Imputed Doses Returned**: When no missing doses are inferred, `imputedData` evaluates to `[]`. The chart renders normally with 4 active datasets; the 5th legend item remains clean.
2. **Missing `confidence_score`**: Handled via fallbacks (`'N/A'`), ensuring tooltips never throw JS TypeError.
3. **High Density Time Ranges**: Chart.js time scale automatically aligns x-axis timestamps for logged and imputed entries even if they occur at irregular intervals.

---
