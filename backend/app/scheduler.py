import json
import logging
import time
from datetime import datetime, timedelta, timezone

from .config import get_settings
from .ingestion import find_circuit_for_session
from .mongo import database, init_mongo, queue_job, utcnow


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("race-scheduler")
settings = get_settings()


def parse_datetime(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def dataset_due(subject: str, dataset: str, interval: timedelta | None = None) -> bool:
    state = database.dataset_status.find_one({"subject": subject, "dataset": dataset})
    if not state:
        return True
    if interval is None:
        return False
    synced = parse_datetime(state.get("last_synced_at"))
    return not synced or utcnow() - synced >= interval


def current_session_due(session: dict, now: datetime) -> bool:
    state = database.dataset_status.find_one({"subject": session["_id"], "dataset": "summary"})
    if not state:
        return True
    starts = parse_datetime(session.get("starts_at"))
    if not starts:
        return False
    duration = timedelta(hours=4 if session.get("code") == "R" else 2)
    finalization_window = starts + duration + timedelta(hours=24)
    if now >= finalization_window:
        return False
    synced = parse_datetime(state.get("last_synced_at"))
    return not synced or now - synced >= timedelta(hours=2)


def schedule_historical_backfill(counts: dict[str, int]) -> None:
    control = database.sync_controls.find_one({"_id": "historical_backfill", "active": True})
    enabled = settings.historical_backfill_enabled or bool(control)
    if not enabled:
        return
    start = int(control.get("start", settings.historical_backfill_start) if control else settings.historical_backfill_start)
    end = int(control.get("end", utcnow().year - 1) if control else utcnow().year - 1)
    include_telemetry = bool(control.get("include_telemetry", settings.telemetry_backfill_enabled) if control else settings.telemetry_backfill_enabled)
    missing_years = [year for year in range(end, max(1949, start - 1), -1) if not database.seasons.find_one({"_id": year})]
    for year in missing_years[:2]:
        queue_job(database, "season", f"season:{year}", {"season": year})
        counts["backfill"] += 1

    core_queued = 0
    track_queued = 0
    telemetry_queued = 0
    track_events: set[str] = set()
    for year in range(end, max(1949, start - 1), -1):
        for session in database.sessions.find({"season": year}).sort([("round", -1), ("starts_at", -1)]):
            starts = parse_datetime(session.get("starts_at"))
            if not starts or starts >= utcnow() - timedelta(hours=3):
                continue
            session_id = session["_id"]
            if core_queued < 10 and dataset_due(session_id, "summary"):
                queue_job(database, "session", f"session:{session_id}", {"session_id": session_id})
                core_queued += 1
            circuit = find_circuit_for_session(database, session_id) if year >= 2018 else None
            event_id = str(session.get("event_id", ""))
            if (year >= 2018 and session.get("code") == "R" and track_queued < 2
                    and event_id not in track_events and not (circuit and circuit.get("map_data"))
                    and dataset_due(session_id, "track")):
                queue_job(database, "track", f"track:{session_id}", {"session_id": session_id})
                track_queued += 1
                track_events.add(event_id)
            if include_telemetry and year >= 2018 and telemetry_queued < 1 and dataset_due(session_id, "telemetry"):
                queue_job(database, "telemetry", f"telemetry:{session_id}", {"session_id": session_id})
                telemetry_queued += 1
            if core_queued >= 10 and track_queued >= 2 and (telemetry_queued >= 1 or not include_telemetry):
                break
        if core_queued >= 10 and track_queued >= 2 and (telemetry_queued >= 1 or not include_telemetry):
            break
    counts["session"] += core_queued
    counts["track"] += track_queued
    counts["telemetry"] += telemetry_queued

    if control and not missing_years and core_queued == 0 and track_queued == 0 and telemetry_queued == 0:
        database.sync_controls.update_one({"_id": "historical_backfill"}, {"$set": {"active": False, "completed_at": utcnow()}})


def schedule_once() -> dict[str, int]:
    now = utcnow()
    counts = {"season": 0, "session": 0, "track": 0, "telemetry": 0, "backfill": 0}
    sessions = list(database.sessions.find({"season": now.year}, {"starts_at": 1, "code": 1, "season": 1, "event_id": 1}))
    near_event = any(
        starts and abs((starts - now).total_seconds()) <= 36 * 3600
        for starts in (parse_datetime(row.get("starts_at")) for row in sessions)
    )
    calendar_interval = timedelta(minutes=5) if near_event else timedelta(hours=6)
    if dataset_due(str(now.year), "calendar", calendar_interval):
        queue_job(database, "season", f"season:{now.year}", {"season": now.year})
        counts["season"] += 1

    track_events: set[str] = set()
    for session in sessions:
        starts = parse_datetime(session.get("starts_at"))
        if not starts:
            continue
        duration = timedelta(hours=4 if session.get("code") == "R" else 2)
        if now < starts + duration + timedelta(minutes=30):
            continue
        session_id = session["_id"]
        if current_session_due(session, now):
            queue_job(database, "session", f"session:{session_id}", {"session_id": session_id})
            counts["session"] += 1
        circuit = find_circuit_for_session(database, session_id)
        event_id = str(session.get("event_id", ""))
        if (int(session.get("season", 0)) >= 2018 and session.get("code") in {"R", "Q", "S"}
                and event_id not in track_events and not (circuit and circuit.get("map_data"))
                and dataset_due(session_id, "track")):
            queue_job(database, "track", f"track:{session_id}", {"session_id": session_id})
            counts["track"] += 1
            track_events.add(event_id)
        if settings.telemetry_backfill_enabled and counts["telemetry"] == 0 and int(session.get("season", 0)) >= 2018 and dataset_due(session_id, "telemetry"):
            queue_job(database, "telemetry", f"telemetry:{session_id}", {"session_id": session_id})
            counts["telemetry"] += 1

    schedule_historical_backfill(counts)
    return counts


def run_forever() -> None:
    init_mongo()
    logger.info(json.dumps({"event": "scheduler.started", "database": settings.mongodb_database}))
    while True:
        try:
            counts = schedule_once()
            if any(counts.values()):
                logger.info(json.dumps({"event": "scheduler.queued", **counts}))
        except Exception:
            logger.exception("Scheduler cycle failed")
        time.sleep(settings.scheduler_poll_seconds)


if __name__ == "__main__":
    run_forever()
