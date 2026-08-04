## 2026-08-04T07:21:53Z

<USER_REQUEST>
You are the Sub-Orchestrator for M2 (R2 Missing Dose Imputation Integration).
Your working directory is c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\sub_orch_m2.
Create your working directory, BRIEFING.md, SCOPE.md, and progress.md.

Scope & Mission:
Implement Requirement R2: Missing Dose Imputation Integration & Visual Indicators.
Read:
- c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\ORIGINAL_REQUEST.md
- c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\orchestrator\PROJECT.md

Key Deliverables:
- Predictive imputation model (pharmacodynamic deconvolution inverting Scheiner decay curve bounded by time-of-day ISFs) to estimate unlogged insulin correction doses based on surrounding glucose trends.
- Database schema & API integration (`is_imputed` boolean flag, `confidence_score`, `/api/insulin/history?include_imputed=true`).
- Dashboard integration on Chart.js insulinChart in templates/index.html with distinct visual indicators (dashed stroke, distinct fill, legend entry, hover tooltip).
- Ensure imputation logic executes locally without crashing.

Procedure:
Run the Iteration Loop (Explorer -> Worker -> Reviewers -> Challenger -> Forensic Auditor -> Gate):
1. Spawn Explorer(s) to analyze db.py, ml_heuristics.py, prediction.py, and templates/index.html.
2. Spawn Worker to implement R2 imputation backend and frontend chart visualization.
   MANDATORY INTEGRITY WARNING: DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task.
3. Spawn Reviewers to inspect implementation and UI rendering.
4. Spawn Challenger to test imputation calculation accuracy and local stability.
5. Spawn Forensic Auditor (teamwork_preview_auditor) for integrity verification.
6. Evaluate Gate: All pass -> Mark M2 DONE and report to parent orchestrator. Any fail -> Loop back.
</USER_REQUEST>
