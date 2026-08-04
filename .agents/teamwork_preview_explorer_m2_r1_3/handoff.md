# Handoff Report: Visual Indicators for Imputed Insulin Doses

**Explorer**: Explorer 3 (Milestone M2 — Missing Dose Imputation Integration)  
**Working Directory**: `c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\teamwork_preview_explorer_m2_r1_3`  
**Date**: 2026-08-04  

---

## 1. Observation

1. **File Locations**:
   - `templates/index.html` contains all HTML, CSS, and inline JavaScript logic for the dashboard frontend.
   - Canvas element `#insulinChart` is located at line 464 inside a glassmorphic container:
     `<canvas id="insulinChart"></canvas>`.
   - Global chart variable `let insulinChart = null;` defined at line 598.

2. **Current API Call Logic**:
   - `fetchInsulinHistory(hours)` at line 934 calls:
     `const res = await fetch('/api/insulin/history?hours=${hours}');`
   - It currently omits `include_imputed=true`.

3. **Current `insulinChart` Rendering Logic**:
   - `renderInsulinChart(doses)` (lines 966–1055) initializes a Chart.js `bar` chart with 4 datasets:
     - `'Rapid-Acting'` (`rgba(239, 68, 68, 0.85)`)
     - `'Long-Acting'` (`rgba(245, 158, 11, 0.85)`)
     - `'Meal'` (`rgba(16, 185, 129, 0.85)`)
     - `'Correction'` (`rgba(59, 130, 246, 0.85)`)
   - Standard tooltips only return simple single-line strings: `label: (context) => ...`.

4. **Required Interface Contracts**:
   - `PROJECT.md` §2 and `SCOPE.md` require:
     - Endpoint parameter `include_imputed=true` when fetching insulin history.
     - Imputed bars MUST have `borderDash: [5, 5]`, distinct semi-transparent color (`rgba(168, 85, 247, 0.35)`), distinct legend entry (`'Imputed (Estimated)'`), and custom tooltip callback displaying dose, timestamp, imputation flag, and confidence score.

---

## 2. Logic Chain

1. **Observation**: `templates/index.html:934` calls `/api/insulin/history?hours=${hours}` without `include_imputed=true`.
   **Inference**: The backend endpoint defaults `include_imputed=false` unless `include_imputed=true` is explicitly requested in the query string. Therefore, updating line 934 to `fetch('/api/insulin/history?hours=${hours}&include_imputed=true')` is necessary to receive imputed doses from the M2 imputation engine.

2. **Observation**: `templates/index.html:966-1055` maps all items into 4 logged dose categories without checking `d.is_imputed`.
   **Inference**: To separate logged doses from imputed doses, `doses` must be partitioned into `loggedDoses` (`!d.is_imputed`) and `imputedDoses` (`d.is_imputed === true`).

3. **Observation**: Adding a 5th dataset labeled `'Imputed (Estimated)'` to `insulinChart` with `borderDash: [5, 5]`, `backgroundColor: 'rgba(168, 85, 247, 0.35)'`, `borderColor: 'rgba(168, 85, 247, 0.9)'`, and `borderWidth: 2`.
   **Inference**: This directly satisfies the acceptance criteria for a distinct stroke style (`borderDash`), distinct fill/opacity/color, and an automatic legend entry for imputed doses.

4. **Observation**: `tooltip.callbacks.label` in Chart.js v4 accepts a function returning an array of strings.
   **Inference**: Returning `[` ${label}: ${val} U`, ` Status: Imputed / Estimated`, ` Confidence: ${confPercent}`]` inside `tooltip.callbacks.label` formats multi-line tooltips displaying dose, status, and confidence score whenever an imputed bar is hovered.

---

## 3. Caveats

- **No Source Code Edits Applied**: Per read-only investigation rules, no edits were made directly to `templates/index.html`. All changes are provided as exact drop-in code snippets in `analysis.md` for the implementer worker agent.
- **Backend Dependency**: The visual indicators rely on the backend endpoint returning JSON items containing `is_imputed: boolean` and `confidence_score: float`. If the backend sends missing keys, default fallbacks (`is_imputed: false`, `confidence_score: 'N/A'`) in the JS code prevent UI exceptions.

---

## 4. Conclusion

The visual indicator design for imputed insulin doses on `insulinChart` in `templates/index.html` is fully designed and documented.
Key changes required in `templates/index.html`:
1. Modify line 934 to append `&include_imputed=true`.
2. Replace `renderInsulinChart` (lines 966–1055) with the 5-dataset design containing `borderDash: [5, 5]`, purple translucent fill, legend label `'Imputed (Estimated)'`, and multi-line tooltip callback.
3. Optionally update `#insulin-tbody` row rendering to display `Imputed` badges in the dose table.

---

## 5. Verification Method

1. **Static Inspection**:
   - Inspect `templates/index.html` around line 934 to verify `include_imputed=true` query parameter.
   - Inspect `renderInsulinChart` in `templates/index.html` to confirm `borderDash: [5, 5]`, `backgroundColor: 'rgba(168, 85, 247, 0.35)'`, and legend/tooltip callbacks.

2. **Browser Execution & Visual Testing**:
   - Start FastAPI server (`uvicorn app:app --port 8000`).
   - Open `http://localhost:8000` in browser.
   - Verify `insulinChart` displays dashed purple bars for imputed entries.
   - Hover over an imputed bar and confirm tooltip displays dose, timestamp, `Status: Imputed / Estimated`, and confidence percentage.
   - Verify legend displays `'Imputed (Estimated)'` entry.

3. **Automated E2E Test Suite**:
   - Run E2E tests: `pytest e2e_tests/` (or project test runner).

---
