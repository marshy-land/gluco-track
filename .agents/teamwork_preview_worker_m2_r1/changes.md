# Changes Report — Milestone M2 (Requirement R2)

## Files Modified & Created

### 1. `imputation.py` (Created)
- Implemented `detect_and_impute_missing_doses(glucose_readings, logged_doses, timezone_str, min_confidence)`.
- Applied pharmacodynamic deconvolution inverting Scheiner decay curve $F_{\text{act}}(\Delta t) = 1 - (1 - \Delta t / 240)^2$ bounded by time-of-day ISFs from `ml_heuristics.py`.
- Formulated unexplained glucose drop $\Delta G_{\text{unexplained}} = \Delta G_{\text{obs}} - \Delta G_{\text{logged\_iob}}$.
- Implemented multi-factor confidence scoring:
  $$C = 0.35 C_{\text{magnitude}} + 0.30 C_{\text{shape}} + 0.20 C_{\text{hyper}} + 0.15 C_{\text{no\_carb}}$$
- Applied thresholding gating ($C \ge 0.50$), dose clamping $[0.5 \text{ U}, 15.0 \text{ U}]$, and non-overlapping candidate selection.

### 2. `schema.sql` (Modified)
- Updated `insulin_doses` table creation schema to include `is_imputed BOOLEAN DEFAULT FALSE` and `confidence_score DOUBLE PRECISION`.

### 3. `db.py` (Modified)
- Added migration execution in `init_db()` (`ALTER TABLE insulin_doses ADD COLUMN IF NOT EXISTS ...`).
- Updated `insert_insulin_doses()` to insert `is_imputed` and `confidence_score` values.
- Updated `get_insulin_history()` to accept `include_imputed: bool = False`, select `is_imputed` and `confidence_score` columns, filter logged doses when `include_imputed=False`, and provide safe fallback for unmigrated database instances.

### 4. `app.py` (Modified)
- Updated `/api/insulin/history` endpoint signature to accept query parameter `include_imputed: bool = Query(default=False)`.
- Integrated dynamic call to `imputation.detect_and_impute_missing_doses()` when `include_imputed=True`, merging imputed doses into JSON history response.

### 5. `templates/index.html` (Modified)
- Updated `fetchInsulinHistory(hours)` to request `/api/insulin/history?hours=${hours}&include_imputed=true`.
- Added `'Imputed (Estimated)'` dataset to Chart.js `insulinChart` with:
  - `borderDash: [5, 5]`
  - `backgroundColor: 'rgba(168, 85, 247, 0.35)'`
  - `borderColor: 'rgba(168, 85, 247, 0.9)'`
  - `borderWidth: 2`
  - Custom multi-line tooltip callback displaying dose, timestamp, status (`Imputed`), and confidence percentage.
- Added visual badge `Imputed (${confidence}%)` in the dose log HTML table.

### 6. Test Suites (Created)
- `test_imputation.py`: 4 unit test cases verifying basic imputation, logged IOB subtraction, stable glucose non-triggering, and confidence threshold filtering.
- `test_app_imputation.py`: Integration test cases verifying default and `include_imputed=true` query modes on `/api/insulin/history`.
