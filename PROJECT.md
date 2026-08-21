# Project: Multi-Bot Health & Medication Ecosystem

## Architecture
A unified, multi-bot health tracking and care-coordination platform built on FastAPI and PostgreSQL, featuring:
- **Dedicated Ingress & Isolated Dispatch**: Independent FastAPI webhook routes (`/api/telegram/webhook`, `/api/medbot/webhook`, `/api/monkebot/webhook`, `/api/biometrics/webhook`) and a multi-bot long-polling fallback runner (`MultiBotPollingManager`) with zero token/callback crosstalk.
- **Dual-Mode Interaction**: Group Chat mode with strict noise-filtering, urgent alerts, and attributed inline buttons (`editMessageText`), alongside Direct Message (DM) mode with rich Reply Keyboards, preset management, and private historical drill-downs.
- **Specialized Bot Roles**:
  1. *GlucoTrack Bot*: Telemetry, IOB decay, Lantus check-ins, urgent hypo/hyper alerts, carbohydrate estimation.
  2. *MedFlowAssist Bot* (`@medflowassist_bot`): Prescription/PRN presets, one-tap dose buttons, elapsed time history inspection.
  3. *MonkeHelperBot* (`@monkehelper_bot`): Master hub executive `/briefing` multi-domain synthesis, quiet hours (23:00–07:00), and care circle administration.
  4. *Circadian & Biometrics Bot*: Sleep stage analytics (TST, Efficiency, Deep/REM, Fragmentation), circadian phase calculation, nocturnal RHR dipping, and dynamic ISF modifier.

```
                    ┌───────────────────────────────┐
                    │      Telegram Cloud API       │
                    └───────┬───────────────┬───────┘
                            │               │
            Webhooks (POST) │               │ Long-Polling (Fallback)
                            ▼               ▼
┌───────────────────────────────────────────────────────────────────┐
│                     FastAPI / Uvicorn Server                      │
│                                                                   │
│  /api/telegram/webhook  /api/medbot/webhook                       │
│  /api/monkebot/webhook  /api/biometrics/webhook                   │
│                                                                   │
│  ┌─────────────────────── MultiBotDispatcher ───────────────────┐ │
│  │                                                              │ │
│  │  [GlucoTrack]      [MedFlowAssist]  [MonkeHelper] [Circadian]│ │
│  │  Prefix: gt:       Prefix: med:     Prefix: mh:   Prefix: bio│ │
│  │  Group/DM Filter   Attribution      Master Hub    Sleep/RHR  │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                │                                  │
│                                ▼                                  │
│                 Circadian & Clinical Services                     │
│                 (IOB, ISF, Sleep Metrics, Trends)                 │
└────────────────────────────────┬──────────────────────────────────┘
                                 │
                                 ▼
┌───────────────────────────────────────────────────────────────────┐
│                  PostgreSQL Telemetry Database                    │
│  glucose_readings   insulin_doses   food_logs   system_settings   │
│  medication_types   medication_logs health_sessions health_metrics│
└───────────────────────────────────────────────────────────────────┘
```

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Isolated Webhook Ingress | 4 distinct webhook routes for GlucoTrack, MedFlow, MonkeHelper, Biometrics | M1 | ORIGINAL_REQUEST §R1 |
| 2 | Resilient Long-Polling Fallback | Multi-bot polling daemon managing isolated threads and webhook cleanup | M1 | ORIGINAL_REQUEST §R1 |
| 3 | Callback Query Namespacing | Strict prefixing (`gt:`, `med:`, `mh:`, `bio:`) eliminating callback crosstalk | M1 | ORIGINAL_REQUEST §R1 |
| 4 | Token & Client Isolation | Cryptographically isolated Bot API clients with per-bot config settings | M1 | ORIGINAL_REQUEST §R1 |
| 5 | Group Chat Noise Filtering | Non-intrusive filtering ignoring ambient chatter while handling commands/mentions | M2 | ORIGINAL_REQUEST §R2 |
| 6 | DM Private Consultation & Keyboards | Persistent Custom Reply Keyboards and rich menus in private 1-on-1 chats | M2 | ORIGINAL_REQUEST §R2 |
| 7 | Medication Preset Management | `/addpreset [Name] [Dose] [Unit]`, `/delpreset`, `/presets` persistent in `medication_types` | M2 | ORIGINAL_REQUEST §R3 |
| 8 | One-Tap Dose Logging & Attribution | Interactive inline keyboard logging with user attribution in `medication_logs` notes | M2 | ORIGINAL_REQUEST §R3 |
| 9 | Chronological Intake Inspection | `/history` and `/summary` displaying reverse-chronological doses and elapsed time | M2 | ORIGINAL_REQUEST §R3 |
| 10 | Sleep Stage Architecture Analytics | Total sleep time, efficiency %, deep/REM ratios, fragmentation index calculation | M3 | ORIGINAL_REQUEST §R5 |
| 11 | Circadian Phase & Chronotype Analysis | Sleep Midpoint MSFsc, chronotype alignment, nocturnal RHR dipping/nadir metrics | M3 | ORIGINAL_REQUEST §R5 |
| 12 | Dynamic ISF Resistance Modifier | Circadian & sleep deficit multiplier adjusting insulin sensitivity factor | M3 | ORIGINAL_REQUEST §R5 |
| 13 | Biometrics Sync & Webhook | Scheduled sync and `/bio` bot commands reporting sleep and heart telemetry | M3 | ORIGINAL_REQUEST §R5 |
| 14 | Master Hub Cross-Domain Querying | Aggregating glucose, insulin/IOB, medication logs, and health sessions | M4 | ORIGINAL_REQUEST §R4 |
| 15 | Executive Daily Digest (`/briefing`) | Formatted executive daily briefing summarizing full patient telemetry state | M4 | ORIGINAL_REQUEST §R4 |
| 16 | Care Circle & Role Administration | Role management (Owner, Caregiver, Viewer) and access control | M4 | ORIGINAL_REQUEST §R4 |
| 17 | Quiet Hours & Urgent Hypo Bypass | Muting routine notifications during 23:00–07:00 while allowing urgent hypo alerts | M4 | ORIGINAL_REQUEST §R4 |
| 18 | E2E Test Suite Execution (Tiers 1-4) | Comprehensive multi-tier test suite validation across all 4 bots | M5-Phase1 | Acceptance Criteria |
| 19 | Adversarial Coverage Hardening (Tier 5) | White-box stress testing, race-condition deduplication, and edge case hardening | M5-Phase2 | Acceptance Criteria |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Multi-Bot Webhook & Dispatch Engine | Ingress routes, `MultiBotPollingManager`, token isolation, callback namespaces | none | DONE |
| M2 | Group/DM Architecture & MedFlowAssist | Group filtering, DM keyboards, presets, one-tap dose logging & attribution, history | M1 | DONE |
| M3 | Circadian & Biometrics Modular Service | Sleep stage breakdown, circadian phase metrics, ISF modifier, biometrics bot | M1 | DONE |
| M4 | Master Coordinator Hub (MonkeHelper) | Multi-domain `/briefing`, care circle admin, quiet hours with hypo bypass | M1, M2, M3 | DONE |
| M5 | Final Verification & Hardening | Phase 1: 100% E2E Test Pass (Tiers 1-4); Phase 2: Tier 5 Adversarial Hardening | M1, M2, M3, M4 | IN_PROGRESS |

## Interface Contracts
### Ingress ↔ Bot Handlers
- `handle_telegram_update(update: dict, bot_type: str) -> dict`: Ingress routes parse update payload and dispatch to target bot handler.
- Return format: `{"status": "ok" | "ignored" | "error", "action": str, "details": dict}`

### Callback Query Format
- Format: `<namespace>:<action>:<params>`
- GlucoTrack: `gt:meal:<carbs>`, `gt:lantus:taken`, `gt:corr:<units>`
- MedFlowAssist: `med:log:<preset_id>:<dose>`, `med:del:<preset_id>`
- MonkeHelper: `mh:briefing:refresh`, `mh:quiet:toggle`, `mh:role:set:<role>`
- Biometrics: `bio:sync:now`, `bio:sleep:detail`

### Database Contracts
- `medication_types`: `(id SERIAL PRIMARY KEY, name VARCHAR UNIQUE LOWER, default_dose NUMERIC, dose_unit VARCHAR, is_active BOOLEAN, created_at TIMESTAMPTZ)`
- `medication_logs`: `(id SERIAL PRIMARY KEY, medication_id INT FK, timestamp TIMESTAMPTZ, dose_taken NUMERIC, notes TEXT, created_at TIMESTAMPTZ)`
- `health_sessions`: `(id SERIAL PRIMARY KEY, session_id VARCHAR UNIQUE, start_time TIMESTAMPTZ, end_time TIMESTAMPTZ, session_type VARCHAR, session_name VARCHAR, duration_minutes NUMERIC, created_at TIMESTAMPTZ)`
- `health_metrics`: `(id SERIAL PRIMARY KEY, timestamp TIMESTAMPTZ, metric_type VARCHAR, value NUMERIC, created_at TIMESTAMPTZ)`

### MonkeHelper Unified Aggregation Contract
- `get_unified_daily_briefing(user_id: Optional[str] = None) -> dict`:
  - `cgm`: `{ "current_glucose": float, "trend": str, "tir_percent": float, "mean_glucose": float, "last_reading_time": str }`
  - `insulin`: `{ "iob": float, "last_lantus": dict, "lantus_due": bool, "recent_boluses": list }`
  - `medications`: `{ "recent_intakes": list, "active_presets_count": int, "last_dose_elapsed": str }`
  - `circadian`: `{ "sleep_duration_hrs": float, "sleep_efficiency": float, "deep_rem_ratio": float, "isf_modifier": float, "chronotype": str }`
  - `alerts`: `{ "urgent_active": bool, "quiet_hours_active": bool }`

## Code Layout
- `app.py`: FastAPI routes, lifecycles, and webhook endpoints (`/api/telegram/webhook`, `/api/medbot/webhook`, `/api/monkebot/webhook`, `/api/biometrics/webhook`).
- `telegram_bot.py`: GlucoTrack bot handler, vision meal recognition, Lantus tracking.
- `med_bot.py`: MedFlowAssist bot handler, preset management, one-tap inline keyboard dose logging, chronological history.
- `monke_bot.py`: MonkeHelperBot master coordinator handler, executive briefing synthesis, quiet hours, care circle roles.
- `circadian_analysis.py`: Sleep stage breakdown, circadian phase metrics, nocturnal RHR analysis, dynamic ISF modifier.
- `biometrics_bot.py`: Circadian & Biometrics bot handler and `/bio` commands.
- `bot_dispatcher.py` / `multi_bot_manager.py`: Isolated Bot API client instances, webhook routing, long-polling background thread supervisor.
- `db.py`: Database queries and data access layer.
- `schema.sql`: PostgreSQL table definitions and indexes.
- `e2e_tests/`: E2E test suite (Tiers 1-5).
