## 2026-08-04T00:23:00Z
You are Worker 1 for Milestone M2 (R2 Missing Dose Imputation Integration & Visual Indicators).
Your working directory is c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\teamwork_preview_worker_m2_r1.
Create your working directory, BRIEFING.md, and progress.md.

Task:
Implement the complete backend missing dose imputation model and frontend chart visualization for Requirement R2.

Read:
- c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\ORIGINAL_REQUEST.md
- c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\orchestrator\PROJECT.md
- c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\sub_orch_m2\SCOPE.md
- c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\sub_orch_m2\explorer_synthesis.md
- c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\teamwork_preview_explorer_m2_r1_1\handoff.md
- c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\teamwork_preview_explorer_m2_r1_2\handoff.md
- c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\teamwork_preview_explorer_m2_r1_3\handoff.md

Implementation Instructions:
1. **Backend Imputation Model**:
   - Create/update `imputation.py` implementing pharmacodynamic deconvolution inverting the Scheiner decay curve bounded by time-of-day ISFs to estimate unlogged insulin correction doses from surrounding glucose drops.
   - Include multi-factor confidence scoring logic (`confidence_score`) and thresholding ($C \ge 0.50$).
2. **Database & Schema Updates**:
   - Update `schema.sql` and `db.py` `init_db()` to safely migration-add `is_imputed BOOLEAN DEFAULT FALSE` and `confidence_score DOUBLE PRECISION` to `insulin_doses`.
   - Update database helper functions in `db.py` (`insert_insulin_doses`, `get_insulin_history`).
3. **API Endpoint Integration**:
   - Update `/api/insulin/history` in `app.py` to support `include_imputed=true` query parameter, integrating `imputation.py` to return imputed doses alongside logged doses cleanly.
4. **Frontend Chart Visualization**:
   - Update `templates/index.html` to fetch `/api/insulin/history` with `include_imputed=true`.
   - Render imputed doses on Chart.js `insulinChart` with:
     - Dashed stroke (`borderDash: [5, 5]`)
     - Distinct fill styling
     - Legend entry distinguishing imputed from logged doses
     - Hover tooltip callback showing dose, timestamp, status (`Imputed`), and confidence score.
5. **Testing & Verification**:
   - Run local test suite / pytest / python server checks to verify imputation logic executes locally without crashing.
   - Document verification commands and test results in your handoff report (`handoff.md` and `changes.md`).
