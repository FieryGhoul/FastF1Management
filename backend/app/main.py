import asyncio
import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import numpy as np
from fastapi import BackgroundTasks, Body, Cookie, Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database

from .circuit_matching import circuit_match_score, country_variants
from .config import get_settings
from .contracts import artifact_key
from .mongo import database, get_db, init_mongo, public_document, queue_job, utcnow
from .on_demand import OnDemandArtifactCache
from .security import COOKIE_NAME, authenticate, create_session, ensure_admin, get_admin, require_csrf
from .serialization import (
    TELEMETRY_SCHEMA_VERSION,
    merged_telemetry_points,
    telemetry_points,
)


settings = get_settings()
login_attempts: dict[str, list[datetime]] = {}
on_demand_cache = (
    OnDemandArtifactCache(
        settings.on_demand_cache,
        settings.fastf1_cache,
        max_bytes=settings.on_demand_cache_max_mb * 1024 * 1024,
    )
    if settings.on_demand_enabled
    else None
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_mongo()
    ensure_admin(database)
    yield


app = FastAPI(title=settings.app_name, version="2.0.0", docs_url="/api/docs", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _recent_session_rate(completions: list[dict[str, Any]]) -> tuple[float | None, int]:
    """Estimate active ingestion throughput without counting idle pauses.

    Completion rows arrive newest-first.  A development restart, machine
    sleep, or a deliberate pause should not turn a healthy session rate into
    a multi-day ETA, so intervals far above the recent median are excluded.
    The ten-minute floor still retains legitimately slow race sessions.
    """
    # Six completions roughly cover one Formula 1 event's practice,
    # qualifying/sprint and race mix.  Keeping the window event-sized makes
    # the displayed ETA respond to real ingestion optimizations without being
    # dominated by sessions completed under an older code path.
    timestamps = [
        row.get("last_synced_at")
        for row in completions
        if row.get("last_synced_at") is not None
    ][:6]
    intervals = [
        (timestamps[index] - timestamps[index + 1]).total_seconds()
        for index in range(len(timestamps) - 1)
    ]
    intervals = [interval for interval in intervals if interval > 0]
    if not intervals:
        return None, len(timestamps)
    cutoff = max(600.0, float(np.median(intervals)) * 5)
    active_intervals = [interval for interval in intervals if interval <= cutoff]
    if not active_intervals:
        return None, len(timestamps)
    rate = round(len(active_intervals) * 3600 / sum(active_intervals), 2)
    return rate, len(active_intervals) + 1


def session_state(db: Database, session_id: str) -> dict[str, Any] | None:
    session = db.sessions.find_one({"_id": session_id})
    if not session:
        return None
    starts = parse_datetime(session.get("starts_at"))
    if not starts:
        return None
    now = utcnow()
    duration = timedelta(hours=4 if session.get("code") == "R" else 2)
    state = None
    reason = None
    if now < starts:
        state, reason = "scheduled", f"This session starts at {starts.isoformat()}."
    elif now < starts + duration:
        state, reason = "in_progress", "The session is in progress. Downloadable timing is published after the session."
    elif now < starts + duration + timedelta(minutes=90):
        state, reason = "awaiting_data", "The session has ended and detailed FastF1 data may still be publishing."
    if state is None:
        return None
    return {
        "availability": state,
        "unavailable_reason": reason,
        "data": {
            "name": session.get("name"), "date": starts.isoformat(),
            "event": session.get("event_name"), "country": session.get("country"),
            "location": session.get("location"), "total_laps": None, "drivers": [],
        },
        "source": "MongoDB / FastF1 schedule",
        "updated_at": session.get("synced_at") or utcnow(),
    }


def find_circuit(db: Database, session: dict[str, Any]) -> dict[str, Any] | None:
    event = db.events.find_one({"_id": session.get("event_id")})
    if not event:
        return None
    if event.get("circuit_slug"):
        return db.circuits.find_one({"_id": event["circuit_slug"]})
    candidates = list(db.circuits.find({"country": {"$in": country_variants(event.get("country"))}}))
    target = f"{event.get('location', '')} {event.get('name', '')}"
    ranked = sorted(
        ((circuit_match_score(row, target), row) for row in candidates),
        key=lambda pair: pair[0], reverse=True,
    )
    return ranked[0][1] if ranked and ranked[0][0] >= 55 else None


def find_map_reference_session(db: Database, circuit: dict[str, Any]) -> dict[str, Any] | None:
    """Find the newest completed timing session that matches a circuit."""
    target = f"{circuit.get('name', '')} {circuit.get('locality', '')}"
    ranked: list[tuple[int, int, int, int, dict[str, Any]]] = []
    code_preference = {"Q": 3, "S": 2, "R": 1}
    for session in db.sessions.find(
        {"country": {"$in": country_variants(circuit.get("country"))}, "season": {"$gte": 2018}, "code": {"$in": ["Q", "S", "R"]}},
        {"_id": 1, "season": 1, "round": 1, "code": 1, "starts_at": 1, "event_name": 1, "location": 1},
    ):
        starts = parse_datetime(session.get("starts_at"))
        if not starts or starts + timedelta(hours=6) > utcnow():
            continue
        candidate = f"{session.get('event_name', '')} {session.get('location', '')}"
        score = circuit_match_score(circuit, candidate)
        ranked.append((score, int(session.get("season", 0)), int(session.get("round", 0)), code_preference.get(session.get("code"), 0), session))
    ranked.sort(key=lambda item: item[:4], reverse=True)
    return ranked[0][4] if ranked and ranked[0][0] >= 55 else None


@app.get("/api/v1/health")
def health() -> dict:
    return {"status": "ok", "service": settings.app_name, "time": utcnow().isoformat()}


@app.get("/api/v1/ready")
def ready(db: Database = Depends(get_db)) -> dict:
    db.command("ping")
    return {"status": "ready", "database": "mongodb", "database_name": settings.mongodb_database}


@app.get("/api/v1/seasons")
def seasons(db: Database = Depends(get_db)) -> dict:
    current = utcnow().year
    synced = [row["year"] for row in db.seasons.find({}, {"year": 1, "_id": 0}).sort("year", DESCENDING)]
    return {"data": list(range(current, 1949, -1)), "synced": synced, "default": current, "telemetry_from": 2018}


@app.get("/api/v1/calendar/{season}")
def calendar(season: int, db: Database = Depends(get_db)) -> dict:
    if season < 1950 or season > utcnow().year + 1:
        raise HTTPException(422, "Season is outside the supported range")
    rows = [public_document(row) for row in db.events.find({"season": season}).sort("round", ASCENDING)]
    state = db.dataset_status.find_one({"subject": str(season), "dataset": "calendar"})
    return {
        "data": rows,
        "season": season,
        "availability": "available" if rows else "awaiting_data",
        "unavailable_reason": None if rows else "This season has not been ingested yet.",
        "source": "MongoDB",
        "updated_at": state.get("last_synced_at") if state else None,
    }


@app.get("/api/v1/events/{season}/{round_number}")
def event(season: int, round_number: int, db: Database = Depends(get_db)) -> dict:
    row = db.events.find_one({"season": season, "round": round_number})
    if not row:
        raise HTTPException(404, "Event not found in MongoDB")
    return {"data": public_document(row), "availability": "available", "source": "MongoDB"}


@app.get("/api/v1/live")
def live(db: Database = Depends(get_db)) -> dict:
    now = utcnow()
    events = list(db.events.find({"season": now.year}).sort("round", ASCENDING))
    sessions = [(event, item, parse_datetime(item.get("starts_at"))) for event in events for item in event.get("sessions", [])]
    sessions = [(event, item, starts) for event, item, starts in sessions if starts]
    upcoming = next(((event, item, starts) for event, item, starts in sessions if starts >= now), None)
    recent = next(((event, item, starts) for event, item, starts in reversed(sessions) if starts < now), None)
    active = next(((event, item, starts) for event, item, starts in sessions if starts <= now <= starts + timedelta(hours=3)), None)
    chosen = active or upcoming
    return {
        "state": "in_progress" if active else "scheduled" if upcoming else "off_season",
        "honest_live": True,
        "message": "FastF1 publishes detailed timing after sessions; this view never fabricates live timing.",
        "event": public_document(chosen[0]) if chosen else None,
        "session": chosen[1] if chosen else None,
        "recent_session": recent[1] if recent else None,
        "checked_at": now.isoformat(),
        "source": "MongoDB",
    }


@app.get("/api/v1/standings/{season}/{kind}")
def standings(season: int, kind: Literal["drivers", "constructors"], round_number: int | None = Query(None, alias="round"), db: Database = Depends(get_db)) -> dict:
    query = {"season": season, "kind": kind}
    if round_number is not None:
        query["round"] = round_number
    row = db.standings.find_one(query, sort=[("synced_at", DESCENDING)])
    return {
        "data": row.get("data", []) if row else [], "source": "MongoDB",
        "season": season, "round": round_number,
        "availability": "available" if row else "awaiting_data",
        "unavailable_reason": None if row else "Standings have not been ingested for this selection.",
        "updated_at": row.get("synced_at") if row else None,
    }


@app.get("/api/v1/drivers")
def drivers(season: int = Query(default_factory=lambda: utcnow().year), db: Database = Depends(get_db)) -> dict:
    rows = [public_document(row) for row in db.drivers.find({"season": season}).sort("driverCode", ASCENDING)]
    return {"data": rows, "season": season, "source": "MongoDB", "availability": "available" if rows else "awaiting_data"}


@app.get("/api/v1/drivers/{driver_id}")
def driver(driver_id: str, season: int = Query(default_factory=lambda: utcnow().year), db: Database = Depends(get_db)) -> dict:
    row = db.drivers.find_one({"season": season, "driverId": driver_id})
    if not row:
        raise HTTPException(404, "Driver not found in MongoDB")
    return {"data": public_document(row), "season": season, "source": "MongoDB"}


@app.get("/api/v1/constructors")
def constructors(season: int = Query(default_factory=lambda: utcnow().year), db: Database = Depends(get_db)) -> dict:
    rows = [public_document(row) for row in db.constructors.find({"season": season}).sort("constructorName", ASCENDING)]
    return {"data": rows, "season": season, "source": "MongoDB", "availability": "available" if rows else "awaiting_data"}


@app.get("/api/v1/constructors/{constructor_id}")
def constructor(constructor_id: str, season: int = Query(default_factory=lambda: utcnow().year), db: Database = Depends(get_db)) -> dict:
    row = db.constructors.find_one({"season": season, "constructorId": constructor_id})
    if not row:
        raise HTTPException(404, "Constructor not found in MongoDB")
    return {"data": public_document(row), "season": season, "source": "MongoDB"}


@app.get("/api/v1/circuits")
def circuits(
    season: int | None = None,
    include_maps: bool = False,
    db: Database = Depends(get_db),
) -> dict:
    projection = None if include_maps else {"map_data": 0}
    rows = [public_document(row) for row in db.circuits.find({}, projection).sort("name", ASCENDING)]
    return {"data": rows, "source": "MongoDB / FastF1 Jolpica", "availability": "available" if rows else "awaiting_data"}


@app.get("/api/v1/circuits/{slug}")
def circuit(slug: str, db: Database = Depends(get_db)) -> dict:
    row = db.circuits.find_one({"_id": slug}, {"map_data": 0})
    if not row:
        raise HTTPException(404, "Circuit not found in MongoDB")
    events = [
        public_document(event)
        for event in db.events.find({"circuit_slug": slug}).sort([
            ("season", DESCENDING), ("round", DESCENDING),
        ])
    ]
    data = public_document(row)
    data["events"] = events
    data["event_count"] = len(events)
    data["session_count"] = sum(len(event.get("sessions", [])) for event in events)
    return {"data": data, "availability": "available", "source": "MongoDB"}


@app.get("/api/v1/circuits/{slug}/map")
def circuit_map(slug: str, db: Database = Depends(get_db)) -> dict:
    row = db.circuits.find_one({"_id": slug})
    if not row:
        raise HTTPException(404, "Circuit not found in MongoDB")
    if row.get("map_data"):
        source = row.get("map_source_attribution") or "FastF1 position data"
        return {
            "availability": "available", "data": row["map_data"],
            "source": f"MongoDB / {source}",
            "source_url": row.get("map_source_url"),
            "reference_session": row.get("map_reference_session"),
        }
    reference = find_map_reference_session(db, row)
    if not reference:
        return {"availability": "unavailable", "unavailable_reason": "No supported completed reference session is available for this circuit.", "data": None, "source": "MongoDB"}
    job = queue_job(
        db,
        "track",
        f"track:{reference['_id']}",
        {"session_id": reference["_id"]},
        priority=100,
    )
    return {
        "availability": "awaiting_data",
        "unavailable_reason": "Preparing the circuit outline from a cached FastF1 reference session.",
        "data": None,
        "source": "MongoDB",
        "status": job["status"],
        "job_id": job["_id"],
        "reference_session": reference["_id"],
    }


def stored_session_payload(
    db: Database,
    background_tasks: BackgroundTasks,
    session_id: str,
    kind: str,
) -> dict[str, Any]:
    state = session_state(db, session_id)
    if state:
        return state
    artifact = db.artifacts.find_one({"_id": artifact_key(session_id, kind, {})})
    if artifact:
        return artifact["payload"]
    year = int(session_id.split("-", 1)[0])
    if year < 2018 and kind not in {"summary", "results", "track"}:
        return {"availability": "unavailable", "unavailable_reason": "Detailed timing data is available from 2018 onward.", "data": [], "source": "MongoDB"}
    if kind == "track":
        session = db.sessions.find_one({"_id": session_id})
        circuit_row = find_circuit(db, session) if session else None
        if circuit_row and circuit_row.get("map_data"):
            return {"availability": "available", "unavailable_reason": None, "data": circuit_row["map_data"], "source": "MongoDB canonical circuit map", "updated_at": circuit_row.get("updated_at")}
    if on_demand_cache is not None:
        return on_demand_cache.get_or_schedule(
            background_tasks, session_id, kind, {},
        )
    return {"availability": "awaiting_data", "unavailable_reason": "The scheduled ingestion worker has not stored this dataset yet.", "data": [], "source": "MongoDB"}


@app.get("/api/v1/sessions/{session_id}/telemetry")
def telemetry(
    session_id: str,
    background_tasks: BackgroundTasks,
    drivers: str = "",
    laps: str = Query(default="fastest", max_length=16),
    channels: str = "all",
    stream: Literal["merged", "car", "position"] = "merged",
    db: Database = Depends(get_db),
) -> dict:
    state = session_state(db, session_id)
    if state:
        return state
    if int(session_id.split("-", 1)[0]) < 2018:
        return {"availability": "unavailable", "unavailable_reason": "Telemetry is available from 2018 onward.", "data": None, "source": "MongoDB"}
    lap_selection = laps.strip().lower()
    selected_lap = None
    if lap_selection != "fastest":
        try:
            selected_lap = int(lap_selection)
        except ValueError as exc:
            raise HTTPException(422, "Lap must be 'fastest' or a positive number") from exc
        if selected_lap < 1:
            raise HTTPException(422, "Lap must be 'fastest' or a positive number")
    requested_drivers = [value.strip().upper() for value in drivers.split(",") if value.strip()][:2]
    channel_selection = channels.strip().lower()
    return_all_channels = channel_selection in {"", "all", "*"}
    requested_channels = [] if return_all_channels else [
        value.strip() for value in channels.split(",")
        if value.strip() and len(value.strip()) <= 64
    ][:64]
    on_demand_options = {
        "drivers": ",".join(requested_drivers),
        "laps": lap_selection,
        "channels": "" if return_all_channels else ",".join(requested_channels),
        "stream": stream,
    }
    telemetry_state = db.dataset_status.find_one({
        "subject": session_id, "dataset": "telemetry",
    })
    has_stored_telemetry = db.telemetry_laps.find_one(
        {"session_id": session_id}, {"_id": 1},
    ) is not None
    if (
        not telemetry_state
        or telemetry_state.get("schema_version") != TELEMETRY_SCHEMA_VERSION
        or telemetry_state.get("availability") == "awaiting_data"
        or (
            telemetry_state.get("availability") == "available"
            and not has_stored_telemetry
        )
    ) and on_demand_cache is not None:
        return on_demand_cache.get_or_schedule(
            background_tasks,
            session_id,
            "telemetry",
            on_demand_options,
        )
    if not telemetry_state:
        return {
            "availability": "awaiting_data",
            "unavailable_reason": "Telemetry ingestion is not complete for this session.",
            "data": None,
            "source": "MongoDB",
        }
    if telemetry_state.get("schema_version") != TELEMETRY_SCHEMA_VERSION:
        return {
            "availability": "awaiting_data",
            "unavailable_reason": "Telemetry is being upgraded to the compact two-stream schema.",
            "data": None,
            "source": "MongoDB",
        }
    if telemetry_state.get("availability") != "available":
        return {
            "availability": telemetry_state.get("availability", "unavailable"),
            "unavailable_reason": telemetry_state.get("unavailable_reason"),
            "data": None,
            "source": telemetry_state.get("source", "MongoDB"),
            "updated_at": telemetry_state.get("updated_at"),
        }
    if not requested_drivers:
        fastest = db.telemetry_laps.find_one({"session_id": session_id, "lap_time": {"$ne": None}}, sort=[("lap_time", ASCENDING)])
        requested_drivers = [fastest["driver"]] if fastest else []
    traces = []
    available_channels: set[str] = set()
    for code in requested_drivers:
        query = {"session_id": session_id, "driver": code}
        document = (
            db.telemetry_laps.find_one(query, sort=[("lap_time", ASCENDING)])
            if selected_lap is None
            else db.telemetry_laps.find_one({**query, "lap": selected_lap})
        )
        if not document:
            continue
        points = (
            merged_telemetry_points(document)
            if stream == "merged"
            else telemetry_points(document, stream)
        )
        for point in points:
            available_channels.update(point.keys())
        stride = max(1, int(np.ceil(len(points) / 1500)))
        selected = []
        for point in points[::stride]:
            keys = (
                list(point.keys())
                if return_all_channels
                else list(dict.fromkeys([
                    "Distance", "Time",
                    *requested_channels,
                ]))
            )
            selected.append({key: point.get(key) for key in keys if key in point})
        count_key = "point_count" if stream == "merged" else f"{stream}_point_count"
        traces.append({
            "driver": code,
            "lap": document.get("lap"),
            "lap_time": document.get("lap_time"),
            "point_count": document.get(count_key, len(points)),
            "returned_point_count": len(selected),
            "points": selected,
        })
    if stream == "merged" and len(traces) == 2:
        reference = [(point.get("Distance"), point.get("Time")) for point in traces[0]["points"] if point.get("Distance") is not None and point.get("Time") is not None]
        if reference:
            ref_distance = np.array([item[0] for item in reference])
            ref_time = np.array([item[1] for item in reference])
            for point in traces[1]["points"]:
                if point.get("Distance") is not None and point.get("Time") is not None:
                    point["Delta"] = point["Time"] - float(np.interp(point["Distance"], ref_distance, ref_time))
    if not traces:
        if on_demand_cache is not None:
            return on_demand_cache.get_or_schedule(
                background_tasks,
                session_id,
                "telemetry",
                on_demand_options,
            )
        return {
            "availability": "unavailable",
            "unavailable_reason": (
                "No stored telemetry matches the selected driver and lap. "
                "Choose another driver, lap number, or the fastest-lap default."
            ),
            "data": None,
            "source": "MongoDB",
        }
    returned_channels = (
        sorted(available_channels)
        if return_all_channels
        else [channel for channel in requested_channels if channel in available_channels]
    )
    if stream == "merged" and len(traces) == 2:
        returned_channels.append("Delta")
    return {
        "availability": "available",
        "unavailable_reason": None,
        "data": {
            "stream": stream,
            "channels": returned_channels,
            "available_channels": sorted(available_channels),
            "traces": traces,
        },
        "source": "MongoDB",
        "updated_at": utcnow(),
    }


@app.get("/api/v1/sessions/{session_id}/drivers")
def session_drivers(
    session_id: str,
    db: Database = Depends(get_db),
) -> dict:
    """Return display names for drivers who have data in this session."""
    year = int(session_id.split("-", 1)[0])
    result_rows = list(db.results.find(
        {"session_id": session_id},
        {
            "_id": 0, "Abbreviation": 1, "FullName": 1,
            "DriverNumber": 1, "TeamName": 1,
        },
    ))
    by_code = {
        str(row["Abbreviation"]).upper(): row
        for row in result_rows if row.get("Abbreviation")
    }
    telemetry_codes = {
        str(code).upper()
        for code in db.telemetry_laps.distinct("driver", {"session_id": session_id})
        if code
    }
    codes = telemetry_codes | set(by_code)
    season_drivers = {
        str(row.get("driverCode", "")).upper(): row
        for row in db.drivers.find(
            {"season": year, "driverCode": {"$in": list(codes)}},
            {
                "_id": 0, "driverCode": 1, "givenName": 1,
                "familyName": 1, "driverNumber": 1,
            },
        )
        if row.get("driverCode")
    }
    rows = []
    for code in codes:
        result = by_code.get(code, {})
        season_driver = season_drivers.get(code, {})
        full_name = result.get("FullName") or " ".join(filter(None, [
            season_driver.get("givenName"), season_driver.get("familyName"),
        ]))
        rows.append({
            "code": code,
            "full_name": full_name or code,
            "driver_number": result.get("DriverNumber") or season_driver.get("driverNumber"),
            "team_name": result.get("TeamName"),
            "telemetry_available": code in telemetry_codes,
        })
    rows.sort(key=lambda row: (str(row["full_name"]), row["code"]))
    return {
        "availability": "available" if rows else "awaiting_data",
        "unavailable_reason": None if rows else "The session driver list is not available yet.",
        "data": rows,
        "source": "MongoDB",
    }


@app.get("/api/v1/sessions/{session_id}/{kind}")
def session_artifact(
    session_id: str,
    kind: Literal["summary", "results", "laps", "strategy", "weather", "race-control", "track"],
    background_tasks: BackgroundTasks,
    db: Database = Depends(get_db),
) -> dict:
    return stored_session_payload(db, background_tasks, session_id, kind)


@app.get("/api/v1/jobs/{job_id}")
def job(job_id: str, db: Database = Depends(get_db)) -> dict:
    row = db.jobs.find_one({"_id": job_id})
    if not row:
        raise HTTPException(404, "Job not found")
    return {"id": row["_id"], "kind": row["kind"], "status": row["status"], "progress": row["progress"], "error": row.get("error"), "updated_at": row["updated_at"]}


@app.websocket("/api/v1/updates")
async def updates(websocket: WebSocket):
    await websocket.accept()
    seen: dict[str, str] = {}
    try:
        while True:
            rows = list(database.jobs.find().sort("updated_at", DESCENDING).limit(20))
            for row in reversed(rows):
                stamp = f"{row['status']}:{row['progress']}:{row['updated_at']}"
                if seen.get(row["_id"]) != stamp:
                    event_name = "sync.completed" if row["status"] == "completed" else "sync.failed" if row["status"] == "failed" else "sync.progress"
                    await websocket.send_json({"event": event_name, "job_id": row["_id"], "status": row["status"], "progress": row["progress"]})
                    seen[row["_id"]] = stamp
            # A client normally sends nothing on this socket.  Still wait for
            # an ASGI disconnect event so Uvicorn can close the connection
            # immediately during a development reload instead of hanging in
            # "Waiting for background tasks to complete" indefinitely.
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=1)
                if message["type"] == "websocket.disconnect":
                    return
            except TimeoutError:
                pass
    except (WebSocketDisconnect, RuntimeError):
        return


@app.post("/api/v1/admin/login")
def admin_login(request: Request, response: Response, credentials: dict = Body(...), db: Database = Depends(get_db)) -> dict:
    key = request.client.host if request.client else "unknown"
    cutoff = utcnow().timestamp() - 900
    attempts = [attempt for attempt in login_attempts.get(key, []) if attempt.timestamp() > cutoff]
    if len(attempts) >= 5:
        raise HTTPException(429, "Too many login attempts. Try again later.")
    user = authenticate(db, str(credentials.get("username", "")), str(credentials.get("password", "")))
    if not user:
        attempts.append(utcnow())
        login_attempts[key] = attempts
        raise HTTPException(401, "Invalid credentials")
    login_attempts.pop(key, None)
    return {"authenticated": True, "username": user["username"], "csrf_token": create_session(db, user, response)}


@app.post("/api/v1/admin/logout", dependencies=[Depends(get_admin), Depends(require_csrf)])
def admin_logout(response: Response, raw: str | None = Cookie(default=None, alias=COOKIE_NAME), db: Database = Depends(get_db)) -> dict:
    if raw:
        db.admin_sessions.delete_one({"_id": hashlib.sha256(raw.encode()).hexdigest()})
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"authenticated": False}


@app.get("/api/v1/admin/me")
def admin_me(user: dict = Depends(get_admin)) -> dict:
    return {"authenticated": True, "username": user["username"]}


@app.post("/api/v1/admin/sync", dependencies=[Depends(get_admin), Depends(require_csrf)])
def admin_sync(payload: dict = Body(...), db: Database = Depends(get_db)) -> dict:
    kind = str(payload.get("kind", "season"))
    if kind not in {"season", "session", "telemetry", "track", "circuits", "backfill"}:
        raise HTTPException(422, "Unsupported ingestion job kind")
    subject = payload.get("season") or payload.get("session_id") or f"{payload.get('start', 1950)}-{payload.get('end', utcnow().year)}"
    row = queue_job(db, kind, f"{kind}:{subject}", payload)
    return {"job_id": row["_id"], "status": row["status"]}


@app.post("/api/v1/admin/jobs/{job_id}/retry", dependencies=[Depends(get_admin), Depends(require_csrf)])
def retry_job(job_id: str, db: Database = Depends(get_db)) -> dict:
    row = db.jobs.find_one_and_update(
        {"_id": job_id}, {"$set": {"status": "queued", "progress": 0, "error": None, "scheduled_for": utcnow(), "updated_at": utcnow()}},
    )
    if not row:
        raise HTTPException(404, "Job not found")
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/v1/admin/cache", dependencies=[Depends(get_admin)])
def cache_info() -> dict:
    path = Path(settings.fastf1_cache)
    size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file()) if path.exists() else 0
    return {"path": str(path.resolve()), "size_bytes": size}


@app.get("/api/v1/admin/archive", dependencies=[Depends(get_admin)])
def archive_info(db: Database = Depends(get_db)) -> dict:
    control = db.sync_controls.find_one({"_id": "archive_backfill:2000:2026"}) or {}
    timing_control = db.sync_controls.find_one({"_id": "timing_backfill:2018:2026"}) or {}
    updated_at = control.get("updated_at")
    timing_updated_at = timing_control.get("updated_at")
    active = bool(
        control.get("active")
        and updated_at
        and updated_at >= utcnow() - timedelta(minutes=30)
    )
    timing_active = bool(
        timing_control.get("active")
        and timing_updated_at
        and timing_updated_at >= utcnow() - timedelta(minutes=30)
    )
    verified_telemetry_sessions = db.dataset_status.distinct("subject", {
        "dataset": "telemetry",
        "availability": "available",
        "schema_version": TELEMETRY_SCHEMA_VERSION,
    })
    verified_telemetry_scope = {
        "session_id": {"$in": verified_telemetry_sessions},
        "schema_version": TELEMETRY_SCHEMA_VERSION,
    }
    timing_position = int(timing_control.get("position", 0))
    timing_total = int(timing_control.get("total", 0))
    recent_completions = list(db.dataset_status.find(
        {
            "dataset": "telemetry",
            "availability": "available",
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "last_synced_at": {"$ne": None},
        },
        {"last_synced_at": 1},
    ).sort("last_synced_at", DESCENDING).limit(20))
    recent_sessions_per_hour, rate_sample_size = _recent_session_rate(
        recent_completions,
    )
    estimated_seconds_remaining = None
    if recent_sessions_per_hour:
        remaining = max(timing_total - timing_position, 0)
        estimated_seconds_remaining = int(
            remaining * 3600 / recent_sessions_per_hour,
        )
    return {
        "active": active,
        "phase": "stalled" if control.get("active") and not active else control.get("phase", "not_started"),
        "subject": control.get("subject"),
        "position": control.get("position", 0),
        "total": control.get("total", 0),
        "counts": control.get("counts", {}),
        "updated_at": updated_at,
        "completed_at": control.get("completed_at"),
        "failures": db.backfill_failures.count_documents({"run": "archive_backfill:2000:2026"}),
        "timing": {
            "active": timing_active,
            "phase": (
                "stalled"
                if timing_control.get("active") and not timing_active
                else timing_control.get("phase", "not_started")
            ),
            "subject": timing_control.get("subject"),
            "position": timing_position,
            "total": timing_total,
            "counts": timing_control.get("counts", {}),
            "updated_at": timing_updated_at,
            "completed_at": timing_control.get("completed_at"),
            "failures": db.backfill_failures.count_documents({"run": "timing_backfill:2018:2026"}),
            "recent_sessions_per_hour": recent_sessions_per_hour,
            "estimated_seconds_remaining": estimated_seconds_remaining,
            "rate_sample_size": rate_sample_size,
        },
        "coverage": {
            "seasons": db.seasons.count_documents({"_id": {"$gte": 2000, "$lte": 2026}}),
            "maps": db.circuits.count_documents({"map_data": {"$ne": None}}),
            "circuits": db.circuits.count_documents({}),
            "telemetry_sessions": len(verified_telemetry_sessions),
            "telemetry_laps": db.telemetry_laps.count_documents(
                verified_telemetry_scope,
            ),
            "raw_stream_laps": db.telemetry_laps.count_documents({
                **verified_telemetry_scope,
                "car_points_encoding": {"$exists": True},
                "position_points_encoding": {"$exists": True},
            }),
            "outdated_telemetry_laps": db.telemetry_laps.count_documents({
                "schema_version": {"$ne": TELEMETRY_SCHEMA_VERSION},
            }),
        },
    }


@app.get("/api/v1/admin/jobs", dependencies=[Depends(get_admin)])
def admin_jobs(db: Database = Depends(get_db)) -> dict:
    rows = db.jobs.find().sort("created_at", DESCENDING).limit(50)
    return {"data": [{"id": row["_id"], "kind": row["kind"], "key": row["key"], "status": row["status"], "progress": row["progress"], "error": row.get("error"), "attempts": row.get("attempts", 0), "updated_at": row["updated_at"]} for row in rows]}


@app.put("/api/v1/admin/circuits/{slug}", dependencies=[Depends(get_admin), Depends(require_csrf)])
def update_circuit(slug: str, payload: dict = Body(...), db: Database = Depends(get_db)) -> dict:
    fields = {field: payload[field] for field in ("length_km", "race_laps", "lap_record", "first_grand_prix", "circuit_type", "locality", "source_url", "source_attribution") if field in payload}
    fields["updated_at"] = utcnow()
    result = db.circuits.update_one({"_id": slug}, {"$set": fields})
    if not result.matched_count:
        raise HTTPException(404, "Circuit not found")
    return {"data": public_document(db.circuits.find_one({"_id": slug}))}
