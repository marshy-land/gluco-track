# Gluco Track Manager (Skill copy)
Source: C:\Users\tugha\.gemini\config\skills\gluco-track\SKILL.md

---
name: gluco-track
description: >-
  Manages Gluco Track integrations, parses historical LibreView glucose CSV
  exports, and syncs live readings from LibreLinkUp to a PostgreSQL database.
---

# Gluco Track Manager

## Overview
The `gluco-track` skill manages the LibreView and LibreLinkUp sync system. It enables importing historical LibreView CSV data and setting up continuous background syncing of live glucose readings into a PostgreSQL database (typically hosted on Railway).

## Dependencies
This skill requires the following Python libraries installed in the environment:
- `requests`
- `pytz`
- `psycopg2-binary` (or `psycopg2`)
- `python-dotenv`

## Quick Start
To get started with Gluco Track:

1. **Verify Credentials**: Ensure `LIBRE_LINK_UP_EMAIL` and `LIBRE_LINK_UP_PASSWORD` are set in the `.env` file (local to the workspace or in your user home directory `~/.env`).
2. **Dry Run Live Sync**: Test your credentials and regional connections by running:
   ```bash
   uv run sync.py --dry-run
   ```
3. **Import Historical CSV**: Backfill historical readings by running:
   ```bash
   uv run import_csv.py /path/to/libreview_export.csv --tz America/Los_Angeles
   ```
4. **Deploy background worker**: Use Railway to run the Dockerfile continuously for live updates.

## Utility Scripts

### 1. Database Setup (`schema.sql`)
Initializes the `glucose_readings` table with index optimization and unique constraint to prevent duplicate data points.

### 2. Historical Importer (`import_csv.py`)
Parses a LibreView CSV export. Automatically detects the header location and maps columns. Converts device local times to UTC.
```bash
python import_csv.py <path_to_csv> [--tz <timezone>]
```

### 3. Live Syncer (`sync.py`)
Queries Abbott's LibreLinkUp API. Retrieves both the continuous graph data (past 12 hours) and the latest scan reading, parses them into UTC, and pushes them to the DB.
```bash
python sync.py [--dry-run]
```

### 4. Background Sync Daemon (`main.py`)
Runs a continuous loop calling `sync.py` at a fixed interval (default is 5 minutes). Used for deployment in the cloud (Railway).

## Rate Limiting
The daemon polls every 5 minutes by default (`SYNC_INTERVAL_SECONDS=300`). Do not reduce this below 60 seconds, as Abbott may temporarily rate-limit or block accounts showing aggressive polling patterns.

## Common Mistakes
1. **Wrong Region**: Abbott routes logins regionally. If auth fails but credentials are correct, double check `LIBRE_LINK_UP_REGION` (supported: `us`, `eu`, `eu2`, `ap`, `co`, `ae`).
2. **Missing Follower Invitation**: Ensure the LibreLinkUp email is invited to follow the primary patient account in the FreeStyle LibreLink app. The script will fail if `/llu/connections` returns no connections.
3. **Mismatched Timezone on CSV Import**: If the device timestamp in the CSV is not in UTC, make sure to specify the matching `--tz` offset parameter, otherwise UTC times will be shifted.
