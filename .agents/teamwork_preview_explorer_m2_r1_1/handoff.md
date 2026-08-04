# Handoff Report: Backend Pharmacodynamic Deconvolution Model for Missing Insulin Dose Imputation

## 1. Observation

Direct code inspection of the Gluco Track workspace revealed the following specific components and line numbers:

1. **Scheiner Parabolic IOB Model in `prediction.py` (lines 87–132)**:
   - Function `calculate_iob(doses, current_time=None, action_duration_mins=240)` computes Insulin-on-Board (IOB) using Scheiner's parabolic equation:
     ```python
     iob_fraction = (1.0 - (elapsed_mins / action_duration_mins)) ** 2
     total_iob += rapid_dose * iob_fraction
     ``` (line 128–129).
2. **ISF & Time-of-Day Profiles in `ml_heuristics.py`**:
   - `DEFAULT_ISFS` (lines 11–17): Default ISF dictionary `{"morning": 50.0, "afternoon": 50.0, "evening": 50.0, "night": 50.0, "global": 50.0}`.
   - `get_time_of_day_bucket(dt, timezone_str)` (lines 42–58): Bucket boundaries (morning: 4–11h, afternoon: 11–17h, evening: 17–22h, night: 22–4h).
   - `calculate_personalized_isf(hours_back=720, timezone_str)` (lines 60–166): Computes empirical ISF for pure 4-hour correction events:
     ```python
     empirical_isf = (val_start - val_end) / total_rapid
     ``` (line 136).
   - `load_heuristics_params()` (lines 19–32): Reads parameters from `heuristics_params.json`.
3. **Database Layer in `db.py` & `schema.sql`**:
   - `schema.sql` (lines 22–33): `insulin_doses` table currently stores columns `id`, `timestamp`, `rapid_acting`, `long_acting`, `meal`, `correction`, `user_change`, `device`, `serial_number`, `created_at`. It lacks flags for `is_imputed` or `confidence_score`.
   - `db.py`: `get_insulin_history(limit_hours)` (lines 217–233) and `insert_insulin_doses(doses)` (lines 166–215).
4. **Project Requirements in `PROJECT.md` & `SCOPE.md`**:
   - `PROJECT.md` (lines 40–55): Defines `/api/insulin/history?include_imputed=true` response schema with `is_imputed: boolean` and `confidence_score: float`.
   - `PROJECT.md` (line 76): Specifies `imputation.py` as owned by M2 subagents.

---

## 2. Logic Chain

1. **Premise 1**: In `prediction.py` (lines 87–132), the forward insulin action model is defined by Scheiner's parabolic decay $IOB(t) = U \cdot (1 - \Delta t / 240)^2$. The cumulative fraction of insulin action exerted after time $\Delta t$ is $F_{\text{act}}(\Delta t) = 1 - (1 - \Delta t / 240)^2$.
2. **Premise 2**: In `ml_heuristics.py` (lines 42–58 & 60–166), time-of-day ISF lookup and calculation provide the patient's sensitivity factor $ISF(t_{\text{start}})$ in mg/dL per Unit of rapid-acting insulin.
3. **Reasoning Step 1 (Deconvolution Inversion)**: Any observed blood glucose drop $\Delta G_{\text{obs}} = G(t_{\text{start}}) - G(t_{\text{nadir}})$ that cannot be accounted for by the expected drop from already logged insulin IOB ($\Delta G_{\text{logged\_iob}} = ISF \cdot [IOB(t_{\text{start}}) - IOB(t_{\text{nadir}})]$) represents an unexplained drop $\Delta G_{\text{unexplained}} = \Delta G_{\text{obs}} - \Delta G_{\text{logged\_iob}}$.
4. **Reasoning Step 2 (Dose Imputation Formulation)**: Inverting the Scheiner cumulative action fraction allows calculating the exact unlogged dose:
   $$U_{\text{imputed}} = \frac{\Delta G_{\text{unexplained}}}{ISF(t_{\text{start}}) \cdot [F_{\text{act}}(t_{\text{nadir}} - t_d) - F_{\text{act}}(t_{\text{start}} - t_d)]}$$
   For full 3–4 hour drop windows, $F_{\text{act}} \approx 1.0$, simplifying to $U_{\text{imputed}} = \frac{\Delta G_{\text{unexplained}}}{ISF(t_{\text{start}})}$.
5. **Reasoning Step 3 (Confidence Scoring & Safety Controls)**: To prevent false positives from sensor noise or unlogged meals, a multi-factor confidence score $C \in [0.0, 1.0]$ combines drop magnitude ($C_{\text{magnitude}}$), curve monotonicity ($C_{\text{shape}}$), starting hyperglycemia ($C_{\text{hyper}}$), and absence of nearby logged meals ($C_{\text{no\_carb}}$). Imputations are gated at $C \ge 0.50$ and bounded between $0.5$ U and $15.0$ U.
6. **Reasoning Step 4 (Persistence & Interface Contract)**: Adding `is_imputed` and `confidence_score` columns to `insulin_doses` in `schema.sql`/`db.py` enables `imputation.py` to persist and retrieve imputed doses, satisfying the contract specified in `PROJECT.md` lines 40–55.

---

## 3. Caveats

1. **Unlogged Carb Intake**: If a user eats an unlogged meal followed by an unlogged correction dose, the initial carb rise and subsequent insulin drop may partially cancel out, leading to an underestimation of the missing correction dose.
2. **Basal Rate Drift**: The model assumes stable basal coverage during the drop window. Significant basal rate mismatches could introduce slight bias into the estimated ISF or imputed dose size.
3. **Data Granularity**: Assumes continuous glucose readings spaced $\le 15$ minutes apart (standard CGM output from Abbott FreeStyle Libre).

---

## 4. Conclusion

The backend pharmacodynamic deconvolution model for missing dose imputation is fully specified and ready for implementation by M2 Implementers. 
- The design leverages existing utilities in `prediction.py` (`calculate_iob`) and `ml_heuristics.py` (`get_time_of_day_bucket`, `load_heuristics_params`).
- The new module `imputation.py` will encapsulate sliding-window drop detection, IOB subtraction, Scheiner curve inversion, and multi-factor confidence scoring ($C$).
- Database schema modifications to `insulin_doses` (`is_imputed`, `confidence_score`) and API updates to `/api/insulin/history` fulfill all specifications outlined in `PROJECT.md` and `SCOPE.md`.

All technical details and complete proposed Python code contracts are documented in `analysis.md`.

---

## 5. Verification Method

To independently verify this design and its future implementation:

1. **Inspect Analysis Report**:
   - Read `c:\Users\tugha\Documents\antigravity\noble-galileo\.agents\teamwork_preview_explorer_m2_r1_1\analysis.md` for mathematical proofs and code specifications.
2. **Execute Imputation Unit Tests (post-implementation)**:
   - Run `pytest` or `python -m unittest` on new test files created for `imputation.py`.
3. **Validate Database Schema**:
   - Query PostgreSQL database table `insulin_doses` to confirm presence of `is_imputed` (BOOLEAN) and `confidence_score` (DOUBLE PRECISION) columns.
4. **Invalidation Conditions**:
   - If an unexplained drop of $50 \text{ mg/dL}$ with no logged IOB produces an imputed dose outside of $50 / ISF \pm 0.5 \text{ U}$, or if confidence score $C < 0.50$ for a perfect monotonic drop from $220 \text{ mg/dL} \to 120 \text{ mg/dL}$, the implementation fails verification.
