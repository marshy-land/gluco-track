# Gluco Track

Gluco Track is a Python-based background service that aggregates historical FreeStyle Libre glucose data from LibreView CSV exports and continuously syncs live readings from LibreLinkUp into a PostgreSQL database.

This repository is configured to deploy directly to **[Railway](https://railway.app)**.

## Architecture

- **`schema.sql`**: Database schema defining the `glucose_readings` table and optimization indexes.
- **`db.py`**: Handles database connection, table initialization, and high-performance bulk upserts (with deduplication).
- **`parser.py`**: Clean, metadata-agnostic parser for historical LibreView CSV exports.
- **`import_csv.py`**: Command-line importer script to backfill historical data.
- **`sync.py`**: Core synchronization client that authenticates with LibreLinkUp, generates SHA-256 `Account-Id` headers, fetches live data, and inserts new readings.
- **`main.py`**: Background worker loop that runs the synchronization cycle continuously (default: every 5 minutes).
- **`Dockerfile`**: Minimalist containerization setup for deployment.

---

## Getting Started

### 1. Requirements
Ensure you have Python 3.10+ and the required packages installed:
```bash
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Create a `.env` file in the root directory:
```env
LIBRE_LINK_UP_EMAIL="your_follower_email"
LIBRE_LINK_UP_PASSWORD="your_follower_password"
LIBRE_LINK_UP_REGION="us" # Options: us, eu, eu2, ap, co, ae
DATABASE_URL="postgresql://username:password@hostname:port/database"
```
> **Note**: For the live sync client to work, the configured email must be a **follower/caregiver** account. In your phone's FreeStyle LibreLink app, go to **Share** -> **LibreLinkUp** and invite this second email.

---

## Usage

### Run a Live Sync (Dry Run)
Check if credentials and connections are working without writing to the database:
```bash
python sync.py --dry-run
```

### Run a Live Sync (Save to Database)
Run the sync script once and write new readings:
```bash
python sync.py
```

### Import Historical CSV
Export your history from [libreview.com](https://www.libreview.com) as a CSV file, place it locally, and upload it to your database:
```bash
python import_csv.py <path_to_csv_file> --tz America/Los_Angeles
```

---

## Deployment on Railway

1. Link this repository to a **Railway** project.
2. Add a **PostgreSQL** database service in the Railway canvas.
3. Link the Postgres database to your worker service. Railway will automatically inject the `DATABASE_URL` environment variable.
4. Go to your worker service settings -> **Variables**, and add:
   - `LIBRE_LINK_UP_EMAIL`
   - `LIBRE_LINK_UP_PASSWORD`
   - `LIBRE_LINK_UP_REGION`
5. Deploy. The Dockerfile will compile, run, and continuously update your database in the background!
