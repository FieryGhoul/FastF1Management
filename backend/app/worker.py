import json
import logging
import time
from datetime import datetime, timedelta, timezone

from .config import get_settings
from .fastf1_adapter import FastF1Adapter
from .ingestion import persist_session_bundle, persist_telemetry, persist_track, sync_circuits, sync_season, sync_standings
from .mongo import claim_job, database, init_mongo, queue_job, utcnow


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("race-worker")
settings = get_settings()


def log_event(event: str, job: dict, **detail) -> None:
    logger.info(json.dumps({"event": event, "job_id": job["_id"], "kind": job["kind"], **detail}, default=str))


def process_job(adapter: FastF1Adapter, job: dict) -> None:
    job_id = job["_id"]
    payload = job.get("payload", {})
    try:
        database.jobs.update_one({"_id": job_id}, {"$set": {"progress": 20, "updated_at": utcnow()}})
        if job["kind"] == "season":
            season = int(payload["season"])
            result = sync_season(database, adapter, season)
            now = utcnow()
            for event in database.events.find({"season": season}, {"round": 1, "sessions": 1}):
                race = next((item for item in event.get("sessions", []) if item.get("code") == "R"), None)
                race_start = datetime.fromisoformat(race["starts_at"]) if race and race.get("starts_at") else None
                if race_start and race_start.tzinfo is None:
                    race_start = race_start.replace(tzinfo=timezone.utc)
                if race_start and race_start < now:
                    for standings_kind in ("drivers", "constructors"):
                        queue_job(
                            database, "standings", f"standings:{season}:{event['round']}:{standings_kind}",
                            {"season": season, "round": event["round"], "standings_kind": standings_kind},
                        )
        elif job["kind"] == "circuits":
            result = {"circuits": sync_circuits(database, adapter, payload.get("season"))}
        elif job["kind"] == "session":
            result = persist_session_bundle(database, adapter, payload["session_id"])
        elif job["kind"] == "track":
            result = persist_track(database, adapter, payload["session_id"])
        elif job["kind"] == "telemetry":
            result = {"telemetry_laps": persist_telemetry(database, adapter, payload["session_id"])}
        elif job["kind"] == "standings":
            result = {"rows": sync_standings(
                database, adapter, int(payload["season"]), int(payload["round"]), payload["standings_kind"],
            )}
        elif job["kind"] == "backfill":
            start = max(1950, int(payload.get("start", 1950)))
            end = int(payload.get("end", utcnow().year))
            for year in range(end, start - 1, -1):
                queue_job(database, "season", f"season:{year}", {"season": year})
            database.sync_controls.replace_one(
                {"_id": "historical_backfill"},
                {"_id": "historical_backfill", "active": True, "start": start, "end": end,
                 "include_telemetry": bool(payload.get("include_telemetry", False)), "updated_at": utcnow()},
                upsert=True,
            )
            result = {"queued_seasons": end - start + 1}
        else:
            raise ValueError(f"Unknown job kind: {job['kind']}")
        database.jobs.update_one(
            {"_id": job_id},
            {"$set": {"status": "completed", "progress": 100, "error": None, "result": result, "updated_at": utcnow()}},
        )
        log_event("sync.completed", job, result=result)
    except Exception as exc:
        attempts = int(job.get("attempts", 1))
        transient = attempts < 3
        update = {
            "status": "queued" if transient else "failed",
            "progress": 0 if transient else int(job.get("progress", 5)),
            "error": str(exc),
            "updated_at": utcnow(),
        }
        if transient:
            update["scheduled_for"] = utcnow() + timedelta(minutes=2 ** attempts)
        database.jobs.update_one({"_id": job_id}, {"$set": update})
        log_event("sync.retry_scheduled" if transient else "sync.failed", job, error=str(exc), attempts=attempts)
        logger.exception("Ingestion failed")


def run_forever() -> None:
    init_mongo()
    adapter = FastF1Adapter(settings.fastf1_cache)
    logger.info(json.dumps({"event": "worker.started", "database": settings.mongodb_database}))
    while True:
        job = claim_job(database)
        if job:
            log_event("sync.started", job)
            process_job(adapter, job)
        else:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    run_forever()
