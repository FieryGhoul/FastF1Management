# Race Data

A deployable Formula 1 data platform built with React, TypeScript, FastAPI, PostgreSQL and [FastF1](https://docs.fastf1.dev/). It covers calendars, near-live session availability, historical standings, drivers, constructors, circuits and queued lap-level telemetry analysis.

The interface intentionally distinguishes scheduled or in-progress sessions from downloadable detailed timing. FastF1 normally makes timing and telemetry available after a session; this project does not present delayed data as raw live timing.

## Stack

- React 19, TypeScript, Vite, TanStack Query and Apache ECharts
- FastAPI, SQLAlchemy, Alembic and FastF1 3.8
- PostgreSQL 16 in deployment; SQLite is the zero-configuration local fallback
- Dedicated ingestion worker with persistent FastF1 cache
- WebSocket job updates and protected operator tools
- Nginx and Docker Compose

## Run locally

Python 3.12 is recommended. The current code also passes its test suite on Python 3.14.

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

In a second terminal, start the worker:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m app.worker
```

In a third terminal, start the frontend:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. API documentation is available at `http://localhost:8000/api/docs`.

Local development defaults to `admin` / `change-me`. Set `ADMIN_USERNAME` and `ADMIN_PASSWORD` before using the Operations page outside local development.

## Run with Docker

Copy `.env.example` to `.env`, replace every example secret, and set `COOKIE_SECURE=true` behind HTTPS. Then run:

```powershell
docker compose up --build
```

Open `http://localhost:8080`. The Compose stack runs PostgreSQL, migrations, API, ingestion worker, and the static frontend. PostgreSQL data and the FastF1 cache use named persistent volumes.

## Data behavior

- Calendar endpoints load lightweight FastF1 schedules on first request and persist normalized event/session indexes.
- Session detail and telemetry endpoints return `202 Accepted` when an artifact is missing. The worker loads and caches it, while `/api/v1/updates` reports progress.
- Worker startup prewarms the current schedule, circuit index, and most recently completed session. One core session load now materializes results, laps, strategy, weather, and race-control artifacts together.
- Heavy car/position telemetry is loaded separately only for track maps or telemetry charts. Circuit pages automatically select a completed reference session, persist the real outline and markers, and reuse them on later visits.
- Historical information uses FastF1's Jolpica-compatible API. Results may be incomplete for early seasons.
- Detailed timing, telemetry and position data are intentionally limited to 2018 onward.
- Telemetry endpoints accept at most two drivers and return at most 1,500 points per trace.
- High-frequency FastF1 data remains in the persistent filesystem cache; PostgreSQL stores normalized domain records, jobs, curated facts and derived summaries.

## Verification

```powershell
cd backend
pytest -q

cd ..\frontend
npm run build
```

An online smoke check can be performed by starting the API and opening `/api/v1/calendar/<current-year>`. Unit tests do not depend on live upstream services.

## Legacy application

The original Oracle 21c, Tkinter and embedded Flask academic application is preserved unchanged in [`legacy/oracle-tkinter`](legacy/oracle-tkinter/README.md). It is not used by the new runtime.

## Important limitations

- FastF1's supported live timing client records a stream for later processing; it does not provide supported in-session real-time analysis.
- Circuit length, first Grand Prix, lap record and related facts are curated through the private Operations workflow and include source URLs.
- Upcoming weather forecasts, video, news, fantasy features and editing FastF1-authoritative race results are outside this project.
