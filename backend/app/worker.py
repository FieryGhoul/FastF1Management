import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from .config import get_settings
from .database import SessionLocal, engine, init_db
from .fastf1_adapter import FastF1Adapter
from .models import Circuit, DerivedArtifact, Event, IngestionJob, Season
from .services import find_circuit_for_event, parse_datetime, queue_job, sync_circuits, sync_schedule


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("race-worker")
settings = get_settings()


def notify(db, event: str, job: IngestionJob) -> None:
    if engine.dialect.name == "postgresql":
        db.execute(text("SELECT pg_notify('race_updates', :payload)"), {"payload": f'{event}:{job.id}'})


def claim_job(db):
    query = select(IngestionJob).where(IngestionJob.status == "queued").order_by(IngestionJob.created_at).limit(1)
    if engine.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    job = db.scalar(query)
    if job:
        job.status = "running"
        job.progress = 5
        job.attempts += 1
        job.updated_at = datetime.now(timezone.utc)
        notify(db, "sync.started", job)
        db.commit()
    return job


def process_job(db, adapter: FastF1Adapter, job: IngestionJob) -> None:
    try:
        if job.kind == "session":
            session_id = job.payload["session_id"]
            requested_kind = job.payload["artifact_kind"]
            if requested_kind in {"telemetry", "track"}:
                bundle = {requested_kind: adapter.session_artifact(session_id, requested_kind, job.payload.get("options", {}))}
            else:
                bundle = adapter.session_bundle(session_id)
            for artifact_kind, result in bundle.items():
                key = (job.payload.get("artifact_key", job.key) if artifact_kind == requested_kind else
                       adapter.artifact_key(session_id, artifact_kind, {}))
                artifact = db.get(DerivedArtifact, key)
                if artifact:
                    artifact.payload = result
                else:
                    db.add(DerivedArtifact(key=key, kind=artifact_kind, payload=result))
            track_result = bundle.get("track")
            if track_result and track_result.get("availability") == "available":
                year, round_number, _ = adapter.parse_session_id(job.payload["session_id"])
                event = db.get(Event, f"{year}-{round_number}")
                if event:
                    circuit = find_circuit_for_event(db, event)
                    if circuit:
                        circuit.map_data = track_result["data"]
        elif job.kind == "season":
            sync_schedule(db, adapter, int(job.payload["season"]))
        elif job.kind == "circuits":
            sync_circuits(db, adapter, job.payload.get("season"))
        else:
            raise ValueError(f"Unknown job kind: {job.kind}")
        job.status = "completed"
        job.progress = 100
        job.error = None
        notify(db, "sync.completed", job)
    except Exception as exc:
        logger.exception("Job %s failed", job.id)
        job.status = "failed"
        job.error = str(exc)
        notify(db, "sync.failed", job)
    finally:
        job.updated_at = datetime.now(timezone.utc)
        db.commit()


def run_forever() -> None:
    init_db()
    adapter = FastF1Adapter(settings.fastf1_cache)
    with SessionLocal() as db:
        now = datetime.now(timezone.utc)
        logger.info("Prewarming current season schedule and circuit index")
        sync_schedule(db, adapter, now.year)
        sync_circuits(db, adapter, now.year)
        recent_sessions = sorted(
            [(parse_datetime(item.get("starts_at")), item["id"])
             for event in db.scalars(select(Event).where(Event.season == now.year)).all()
             for item in event.raw.get("sessions", []) if item.get("starts_at") and parse_datetime(item["starts_at"]) < now - timedelta(hours=3)],
            reverse=True,
        )
        if recent_sessions:
            session_id = recent_sessions[0][1]
            queue_job(db, "session", adapter.bundle_key(session_id),
                      {"session_id": session_id, "artifact_kind": "summary",
                       "artifact_key": adapter.artifact_key(session_id, "summary", {}), "options": {}})
    logger.info("FastF1 worker started; current indexes are warm")
    while True:
        with SessionLocal() as db:
            now = datetime.now(timezone.utc)
            season = db.get(Season, now.year)
            events = db.scalars(select(Event).where(Event.season == now.year)).all()
            near_event = any(
                abs((parse_datetime(item.get("starts_at")) - now).total_seconds()) <= 36 * 3600
                for event in events for item in event.raw.get("sessions", []) if item.get("starts_at")
            )
            interval = timedelta(minutes=5) if near_event else timedelta(hours=6)
            if season and season.last_synced_at:
                age = now - season.last_synced_at.replace(tzinfo=timezone.utc)
                if age >= interval:
                    queue_job(db, "season", f"season:{now.year}", {"season": now.year})
            elif not db.scalar(select(IngestionJob).where(IngestionJob.key == f"season:{now.year}", IngestionJob.status.in_(["queued", "running"]))):
                queue_job(db, "season", f"season:{now.year}", {"season": now.year})
            job = claim_job(db)
            if job:
                process_job(db, adapter, job)
        if not job:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    run_forever()
