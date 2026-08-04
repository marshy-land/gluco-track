# Scope: M2 (R2 Missing Dose Imputation Integration & Visual Indicators)

## Overview
Implement Requirement R2: Missing Dose Imputation Integration & Visual Indicators.

## Key Deliverables
1. Predictive Imputation Model:
   - Pharmacodynamic deconvolution inverting Scheiner decay curve bounded by time-of-day ISFs.
   - Estimates unlogged insulin correction doses based on surrounding glucose trends.
2. Database Schema & API Integration:
   - Schema updates: `is_imputed` boolean flag, `confidence_score` float column in insulin logs table.
   - API Endpoint: `/api/insulin/history?include_imputed=true` returning imputed entries marked appropriately.
3. Dashboard Frontend Integration:
   - Visual indicators on `Chart.js` `insulinChart` in `templates/index.html`:
     - Dashed stroke (`borderDash`)
     - Distinct fill styling
     - Legend entry distinguishing imputed from logged doses
     - Hover tooltip indicating imputation details and confidence score.
4. Stability & Execution:
   - Ensure imputation logic executes locally without crashing.

## Affected Files
- `db.py`
- `ml_heuristics.py`
- `prediction.py`
- `templates/index.html`

## Execution Plan
- Iteration 1:
  - Explorers investigate existing codebase (`db.py`, `ml_heuristics.py`, `prediction.py`, `templates/index.html`).
  - Worker implements imputation algorithm, DB schema changes, API parameters, and UI chart rendering.
  - Reviewers perform code and visual/UI inspection.
  - Challenger tests accuracy, edge cases, API parameters, and stability.
  - Forensic Auditor performs integrity verification.
  - Gate evaluation.
