# Race Data

A Formula 1 data platform built with React, TypeScript, FastAPI, MongoDB and FastF1 3.8. The active application contains no SQL datastore or SQL migration layer.

## Architecture

```text
cron scheduler -> MongoDB job queue -> FastF1 worker -> MongoDB
                                                    -> persistent FastF1 cache

React frontend -> FastAPI read API -> MongoDB
```

The API process never calls FastF1 or Jolpica. It returns data already stored in MongoDB, including an explicit availability state and source timestamp. The scheduler creates idempotent jobs, and one or more workers claim jobs atomically through MongoDB.

## Stored data

MongoDB collections cover seasons, events, sessions, drivers, constructors, circuits, standings, results, laps, strategies, weather, race-control messages, per-lap telemetry, derived artifacts, dataset freshness, ingestion jobs, admin users and admin sessions.

Telemetry is stored as one document per driver lap. Browser responses select the requested lap, downsample each trace to at most 1,500 points and calculate two-driver delta from stored samples. Canonical circuit maps are stored once on the circuit and reused for historical sessions that do not have position data.

## Run locally

MongoDB must be running locally on port 27017. Python 3.12 and a supported Node LTS release are recommended.

Create `backend/.env` if your MongoDB connection differs from the default:

```env
MONGODB_URL=mongodb://localhost:27017
MONGODB_DATABASE=race_data
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me
```

Start the API:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Start the ingestion worker in a second terminal:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m app.worker
```

Start the scheduler in a third terminal:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m app.scheduler
```

Start the frontend in a fourth terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`; API documentation is at `http://localhost:8000/api/docs`.

## Run with Docker

Copy `.env.example` to `.env`, replace the passwords, then run:

```powershell
docker compose up --build
```

Open `http://localhost:8080`. The stack contains MongoDB, API, worker, scheduler and frontend services, with persistent `mongo_data` and `fastf1_cache` volumes.

## Scheduler policy

- Current schedule: every six hours, or every five minutes within 36 hours of a session.
- Completed current-season sessions: core results/laps/weather/race-control refresh every six hours.
- Circuit outlines: queued from completed race, qualifying or sprint reference sessions.
- Detailed session ingestion starts at least 30 minutes after the expected end.
- Failed jobs retry twice with exponential delay before becoming operator-visible failures.
- Historical season backfill is controlled by `HISTORICAL_BACKFILL_ENABLED` or the Operations page.
- Telemetry backfill is controlled by `TELEMETRY_BACKFILL_ENABLED` because a full 2018-present archive is large and slow.

FastF1 does not provide supported raw real-time analysis. The Live interface reports scheduled, in-progress and awaiting-data states honestly and displays detailed data only after it has been stored.

## Verification

```powershell
cd backend
pytest -q

cd ..\frontend
npm run lint
npm run build
```

Tests use an in-memory MongoDB-compatible test double and do not require live upstream requests.

## Legacy application

The original Oracle/Tkinter application remains preserved under `legacy/oracle-tkinter/`. It is not imported or used by the active runtime.
