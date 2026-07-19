import asyncio
import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import engine, get_db, init_db
from .fastf1_adapter import FastF1Adapter
from .models import AdminSession, Circuit, DerivedArtifact, Event, IngestionJob, Season
from .security import COOKIE_NAME, authenticate, create_session, ensure_admin, get_admin, require_csrf
from .services import artifact_or_job, circuit_dict, circuit_event_score, get_calendar, queue_job, sync_circuits


settings = get_settings()
adapter: FastF1Adapter | None = None
login_attempts: dict[str, list[datetime]] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    global adapter
    init_db()
    adapter = FastF1Adapter(settings.fastf1_cache)
    with next(get_db()) as db:
        ensure_admin(db)
    yield


app = FastAPI(title=settings.app_name, version="1.0.0", docs_url="/api/docs", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin], allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type", "X-CSRF-Token"],
)
def source() -> FastF1Adapter:
    if adapter is None:
        raise HTTPException(503, "Data source is starting")
    return adapter


@app.get("/api/v1/health")
def health() -> dict:
    return {"status": "ok", "service": settings.app_name, "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/v1/ready")
def ready(db: Session = Depends(get_db)) -> dict:
    db.scalar(select(func.count()).select_from(Season))
    return {"status": "ready", "database": engine.dialect.name}


@app.get("/api/v1/seasons")
def seasons() -> dict:
    current = datetime.now(timezone.utc).year
    return {"data": list(range(current, 1949, -1)), "default": current, "telemetry_from": 2018}


@app.get("/api/v1/calendar/{season}")
def calendar(season: int, db: Session = Depends(get_db), ff1: FastF1Adapter = Depends(source)) -> dict:
    if season < 1950 or season > datetime.now(timezone.utc).year + 1:
        raise HTTPException(422, "Season is outside the supported range")
    events = get_calendar(db, ff1, season)
    return {"data": events, "season": season, "source": "FastF1", "updated_at": datetime.now(timezone.utc).isoformat()}


@app.get("/api/v1/events/{season}/{round_number}")
def event(season: int, round_number: int, db: Session = Depends(get_db), ff1: FastF1Adapter = Depends(source)) -> dict:
    match = next((item for item in get_calendar(db, ff1, season) if item["round"] == round_number), None)
    if not match:
        raise HTTPException(404, "Event not found")
    return {"data": match, "availability": "available"}


@app.get("/api/v1/live")
def live(db: Session = Depends(get_db), ff1: FastF1Adapter = Depends(source)) -> dict:
    now = datetime.now(timezone.utc)
    events = get_calendar(db, ff1, now.year)
    sessions = [(event, item) for event in events for item in event["sessions"] if item.get("starts_at")]
    parsed = [(event, item, datetime.fromisoformat(item["starts_at"])) for event, item in sessions]
    upcoming = next(((event, item, starts) for event, item, starts in parsed if starts >= now), None)
    recent = next(((event, item, starts) for event, item, starts in reversed(parsed) if starts < now), None)
    active = next(((event, item, starts) for event, item, starts in parsed if starts <= now <= starts.replace(tzinfo=timezone.utc) + __import__("datetime").timedelta(hours=3)), None)
    chosen = active or upcoming
    state = "in_progress" if active else ("scheduled" if upcoming else "off_season")
    return {"state": state, "honest_live": True, "message": "FastF1 publishes detailed timing after sessions; this view never fabricates live timing.",
            "event": chosen[0] if chosen else None, "session": chosen[1] if chosen else None,
            "recent_session": recent[1] if recent else None, "checked_at": now.isoformat()}


@app.get("/api/v1/standings/{season}/{kind}")
def standings(season: int, kind: Literal["drivers", "constructors"], round_number: int | None = Query(None, alias="round"), ff1: FastF1Adapter = Depends(source)) -> dict:
    try:
        return {"data": ff1.standings(season, kind, round_number), "source": "FastF1 / Jolpica", "season": season, "round": round_number}
    except Exception as exc:
        raise HTTPException(502, f"Standings source unavailable: {exc}") from exc


@app.get("/api/v1/drivers")
def drivers(season: int = Query(default_factory=lambda: datetime.now().year), ff1: FastF1Adapter = Depends(source)) -> dict:
    return {"data": ff1.drivers(season), "season": season, "source": "FastF1 / Jolpica"}


@app.get("/api/v1/drivers/{driver_id}")
def driver(driver_id: str, season: int = Query(default_factory=lambda: datetime.now().year), ff1: FastF1Adapter = Depends(source)) -> dict:
    row = next((item for item in ff1.drivers(season) if item.get("driverId") == driver_id), None)
    if not row:
        raise HTTPException(404, "Driver not found")
    return {"data": row, "season": season}


@app.get("/api/v1/constructors")
def constructors(season: int = Query(default_factory=lambda: datetime.now().year), ff1: FastF1Adapter = Depends(source)) -> dict:
    return {"data": ff1.constructors(season), "season": season, "source": "FastF1 / Jolpica"}


@app.get("/api/v1/constructors/{constructor_id}")
def constructor(constructor_id: str, season: int = Query(default_factory=lambda: datetime.now().year), ff1: FastF1Adapter = Depends(source)) -> dict:
    row = next((item for item in ff1.constructors(season) if item.get("constructorId") == constructor_id), None)
    if not row:
        raise HTTPException(404, "Constructor not found")
    return {"data": row, "season": season}


@app.get("/api/v1/circuits")
def circuits(season: int | None = None, db: Session = Depends(get_db), ff1: FastF1Adapter = Depends(source)) -> dict:
    rows = db.scalars(select(Circuit).order_by(Circuit.name)).all()
    if not rows:
        rows = sync_circuits(db, ff1, season)
    return {"data": [circuit_dict(row) for row in rows], "source": "FastF1 / Jolpica + curated SQL"}


@app.get("/api/v1/circuits/{slug}")
def circuit(slug: str, db: Session = Depends(get_db)) -> dict:
    row = db.get(Circuit, slug)
    if not row:
        raise HTTPException(404, "Circuit not found")
    return {"data": circuit_dict(row), "availability": "available"}


@app.get("/api/v1/circuits/{slug}/map")
def circuit_map(slug: str, db: Session = Depends(get_db), ff1: FastF1Adapter = Depends(source)):
    row = db.get(Circuit, slug)
    if not row:
        raise HTTPException(404, "Circuit not found")
    if row.map_data:
        return {"availability": "available", "data": row.map_data, "source": "FastF1 position data"}
    now = datetime.now(timezone.utc)
    candidates = []
    for year in (now.year, now.year - 1):
        for item in get_calendar(db, ff1, year):
            event_row = db.get(Event, item["id"])
            if not event_row or circuit_event_score(row, event_row) < 55:
                continue
            for session_item in item["sessions"]:
                starts_at = session_item.get("starts_at")
                if starts_at and session_item["code"] in {"R", "Q", "S"} and datetime.fromisoformat(starts_at) < now - __import__("datetime").timedelta(hours=3):
                    candidates.append((datetime.fromisoformat(starts_at), session_item["id"]))
    if not candidates:
        return {"availability": "unavailable", "unavailable_reason": "No completed FastF1 reference session was found for this circuit.", "data": None}
    session_id = max(candidates)[1]
    state, payload = artifact_or_job(db, ff1, session_id, "track", {})
    if state == "ready" and payload.get("data"):
        row.map_data = payload["data"]
        db.commit()
        return payload
    status_code = 202 if state == "queued" else 500
    return Response(status_code=status_code, media_type="application/json", content=__import__("json").dumps(payload))


@app.get("/api/v1/sessions/{session_id}/telemetry")
def telemetry(session_id: str, drivers: str = "", laps: str = "fastest", channels: str = "Speed,RPM,Throttle,Brake,nGear,DRS", db: Session = Depends(get_db), ff1: FastF1Adapter = Depends(source)):
    state, payload = artifact_or_job(db, ff1, session_id, "telemetry", {"drivers": drivers, "laps": laps, "channels": channels})
    return Response(status_code=202, media_type="application/json", content=__import__("json").dumps(payload)) if state == "queued" else payload


@app.get("/api/v1/sessions/{session_id}/{kind}")
def session_artifact(session_id: str, kind: Literal["summary", "results", "laps", "strategy", "weather", "race-control", "track"], db: Session = Depends(get_db), ff1: FastF1Adapter = Depends(source)):
    state, payload = artifact_or_job(db, ff1, session_id, kind, {})
    return Response(status_code=202, media_type="application/json", content=__import__("json").dumps(payload)) if state == "queued" else payload


@app.get("/api/v1/jobs/{job_id}")
def job(job_id: str, db: Session = Depends(get_db)) -> dict:
    row = db.get(IngestionJob, job_id)
    if not row:
        raise HTTPException(404, "Job not found")
    return {"id": row.id, "kind": row.kind, "status": row.status, "progress": row.progress, "error": row.error, "updated_at": row.updated_at.isoformat()}


@app.websocket("/api/v1/updates")
async def updates(websocket: WebSocket):
    await websocket.accept()
    seen: dict[str, str] = {}
    try:
        while True:
            with next(get_db()) as db:
                rows = db.scalars(select(IngestionJob).order_by(IngestionJob.updated_at.desc()).limit(20)).all()
                for row in reversed(rows):
                    stamp = f"{row.status}:{row.progress}:{row.updated_at}"
                    if seen.get(row.id) != stamp:
                        event = "sync.completed" if row.status == "completed" else "sync.failed" if row.status == "failed" else "sync.progress"
                        await websocket.send_json({"event": event, "job_id": row.id, "status": row.status, "progress": row.progress})
                        seen[row.id] = stamp
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        return


@app.post("/api/v1/admin/login")
def admin_login(request: Request, response: Response, credentials: dict = Body(...), db: Session = Depends(get_db)) -> dict:
    key = request.client.host if request.client else "unknown"
    cutoff = datetime.now(timezone.utc).timestamp() - 900
    attempts = [attempt for attempt in login_attempts.get(key, []) if attempt.timestamp() > cutoff]
    if len(attempts) >= 5:
        raise HTTPException(429, "Too many login attempts. Try again later.")
    user = authenticate(db, str(credentials.get("username", "")), str(credentials.get("password", "")))
    if not user:
        attempts.append(datetime.now(timezone.utc))
        login_attempts[key] = attempts
        raise HTTPException(401, "Invalid credentials")
    login_attempts.pop(key, None)
    return {"authenticated": True, "username": user.username, "csrf_token": create_session(db, user, response)}


@app.post("/api/v1/admin/logout", dependencies=[Depends(get_admin), Depends(require_csrf)])
def admin_logout(response: Response, raw: str | None = __import__("fastapi").Cookie(default=None, alias=COOKIE_NAME), db: Session = Depends(get_db)) -> dict:
    if raw:
        db.execute(delete(AdminSession).where(AdminSession.id == hashlib.sha256(raw.encode()).hexdigest()))
        db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"authenticated": False}


@app.get("/api/v1/admin/me")
def admin_me(user=Depends(get_admin)) -> dict:
    return {"authenticated": True, "username": user.username}


@app.post("/api/v1/admin/sync", dependencies=[Depends(get_admin), Depends(require_csrf)])
def admin_sync(payload: dict = Body(...), db: Session = Depends(get_db)) -> dict:
    kind = payload.get("kind", "season")
    key = f"{kind}:{payload.get('season') or payload.get('session_id') or 'all'}"
    row = queue_job(db, kind, key, payload)
    return {"job_id": row.id, "status": row.status}


@app.post("/api/v1/admin/jobs/{job_id}/retry", dependencies=[Depends(get_admin), Depends(require_csrf)])
def retry_job(job_id: str, db: Session = Depends(get_db)) -> dict:
    row = db.get(IngestionJob, job_id)
    if not row:
        raise HTTPException(404, "Job not found")
    row.status, row.progress, row.error = "queued", 0, None
    db.commit()
    return {"job_id": row.id, "status": row.status}


@app.get("/api/v1/admin/cache", dependencies=[Depends(get_admin)])
def cache_info(ff1: FastF1Adapter = Depends(source)) -> dict:
    path, size = __import__("fastf1").Cache.get_cache_info()
    return {"path": path, "size_bytes": size}


@app.get("/api/v1/admin/jobs", dependencies=[Depends(get_admin)])
def admin_jobs(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(50)).all()
    return {"data": [{"id": row.id, "kind": row.kind, "key": row.key, "status": row.status,
                      "progress": row.progress, "error": row.error, "attempts": row.attempts,
                      "updated_at": row.updated_at.isoformat()} for row in rows]}


@app.put("/api/v1/admin/circuits/{slug}", dependencies=[Depends(get_admin), Depends(require_csrf)])
def update_circuit(slug: str, payload: dict = Body(...), db: Session = Depends(get_db)) -> dict:
    row = db.get(Circuit, slug)
    if not row:
        raise HTTPException(404, "Circuit not found")
    for field in ("length_km", "race_laps", "lap_record", "first_grand_prix", "circuit_type", "locality", "source_url"):
        if field in payload:
            setattr(row, field, payload[field])
    db.commit()
    return {"data": circuit_dict(row)}
