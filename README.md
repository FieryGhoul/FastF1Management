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

Race-session telemetry is stored durably as one document per driver lap with
two compressed compact streams: car data (`Time`, `Speed`, `RPM`, `Throttle`,
`Brake`, `Gear`) and a lap-relative `Time`/`Distance` timeline. The redundant
merged stream and all other telemetry channels are discarded; chart traces are
rebuilt from the two compact streams when requested. Practice, qualifying and
sprint telemetry is loaded into the bounded on-demand cache and is not written
to MongoDB. Browser responses select the requested lap, downsample
each trace to at most 1,500 points and calculate two-driver delta from stored
samples. Use `stream=merged`, `stream=car` or `stream=position` on the telemetry
API to select the rebuilt trace, compact car stream, or time/distance stream.
Canonical circuit maps are stored once on the
circuit and reused for historical sessions that do not have position data.

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

Open `http://localhost:8080`. The stack contains MongoDB, API, a resumable
coordinated 2000–2026 archive and 2018–2026 raw-timing loaders, worker,
scheduler and frontend services, with
persistent `mongo_data` and `fastf1_cache` volumes. On a new database, the
archive loader must pass its deep completion audit before Compose starts the
normal worker and scheduler.

## Deploy on Render

The repository includes a [`render.yaml`](render.yaml) Blueprint for the
public Nginx frontend, private FastAPI service, ingestion worker and scheduler.
Production MongoDB runs on MongoDB Atlas; it is intentionally not provisioned
inside Render. Full historical loaders are opt-in because they are long-running
and billed separately.

See [`docs/render.md`](docs/render.md) for Atlas setup, Blueprint deployment,
data migration, historical backfill and production verification.

## Scheduler policy

- Current schedule: every six hours, or every five minutes within 36 hours of a session.
- Completed current-season sessions: core results/laps/weather/race-control refresh every six hours.
- Circuit outlines: queued from completed race, qualifying or sprint reference sessions.
- Detailed session ingestion starts at least 30 minutes after the expected end.
- Failed jobs retry twice with exponential delay before becoming operator-visible failures.
- Historical season backfill is controlled by `HISTORICAL_BACKFILL_ENABLED` or the Operations page.
- Race-only telemetry backfill is controlled by `TELEMETRY_BACKFILL_ENABLED` because raw timing archives are large and slow.

For an operator-requested, resumable archive load that should run independently
of the normal queue, stop the worker and scheduler and run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.backfill --start 2000 --end 2026 --until-complete
```

In a second terminal, start the independent FastF1 timing and telemetry loader.
The archive loader coordinates with it and will not process the same modern
sessions concurrently:

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.timing_backfill --start 2018 --end 2026 --until-complete
```

This stores schedules, participants, round-by-round standings, published
classifications, historical race laps and available pit stops for 2000 onward.
FastF1 weather, race control and full session timing are stored for completed
sessions from 2018 onward; durable car/position telemetry is restricted to race
sessions. Progress and retryable failures are checkpointed in MongoDB so the
command can be safely rerun. The dedicated timing runner removes only a
session's exact FastF1 staging-cache directory after its schema-versioned raw
streams have been verified in MongoDB; this keeps the complete archive from
duplicating tens of gigabytes on disk.

Preview and then apply the race-only retention policy to an existing database:

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.telemetry_retention
.\.venv\Scripts\python.exe -m app.telemetry_retention --apply
```

Preview and then compact existing race telemetry into the two-stream schema:

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.telemetry_compaction
.\.venv\Scripts\python.exe -m app.telemetry_compaction --apply
```

For historical timing, the archive command automatically downloads Jolpica's
official free delayed CSV database dump, verifies its published SHA-256 hash,
and builds a local SQLite index under `.cache/jolpica-dump`. This avoids the
public API's 100-row pagination limit and stores every published lap and pit
stop. If the dump is temporarily unavailable, complete paginated API fetching
remains available as a fallback. The Docker cache volume covers all of
`/cache`, so both the FastF1 cache and verified Jolpica dump survive container
restarts.

To prepare or inspect that source explicitly:

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.jolpica_dump --download
```

Missing pre-2018 position outlines are filled from the MIT-licensed
`bacinger/f1-circuits` GeoJSON and F1DB SVG catalogs. Verify full database
coverage and normalized numeric formatting with:

```powershell
.\.venv\Scripts\python.exe -m app.audit_archive --start 2000 --end 2026 --deep
```

Circuit outlines use the MIT-licensed `bacinger/f1-circuits` and `f1db/f1db`
catalogs. Full circuit metadata (length, turns, type, direction and layouts) is
stored from F1DB alongside each canonical map.

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
