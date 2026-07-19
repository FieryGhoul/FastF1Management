import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from rapidfuzz import fuzz

from .fastf1_adapter import FastF1Adapter, slugify
from .models import Circuit, DerivedArtifact, Event, IngestionJob, RaceSession, Season


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def sync_schedule(db: Session, adapter: FastF1Adapter, year: int) -> list[dict[str, Any]]:
    events = adapter.schedule(year)
    season = db.get(Season, year) or Season(year=year)
    season.last_synced_at = datetime.now(timezone.utc)
    db.add(season)
    for item in events:
        event = db.get(Event, item["id"]) or Event(id=item["id"], season=year, round_number=item["round"], name=item["name"], country=item["country"], location=item["location"])
        event.name = item["name"]
        event.official_name = item["official_name"]
        event.country = item["country"]
        event.location = item["location"]
        event.event_date = parse_datetime(item["event_date"])
        event.format = item["format"]
        event.f1_api_support = item["f1_api_support"]
        event.raw = item
        db.add(event)
        for session_item in item["sessions"]:
            session = db.get(RaceSession, session_item["id"]) or RaceSession(
                id=session_item["id"], event_id=item["id"], name=session_item["name"], abbreviation=session_item["code"]
            )
            session.starts_at = parse_datetime(session_item["starts_at"])
            db.add(session)
    db.commit()
    return events


def get_calendar(db: Session, adapter: FastF1Adapter, year: int) -> list[dict[str, Any]]:
    season = db.get(Season, year)
    if not season:
        return sync_schedule(db, adapter, year)
    rows = db.scalars(select(Event).where(Event.season == year).order_by(Event.round_number)).all()
    return [event.raw for event in rows]


def queue_job(db: Session, kind: str, key: str, payload: dict[str, Any]) -> IngestionJob:
    existing = db.scalar(select(IngestionJob).where(
        IngestionJob.key == key, IngestionJob.status.in_(["queued", "running"])
    ).order_by(IngestionJob.created_at.desc()))
    if existing:
        return existing
    job = IngestionJob(id=str(uuid.uuid4()), kind=kind, key=key, payload=payload)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def artifact_or_job(db: Session, adapter: FastF1Adapter, session_id: str, kind: str, options: dict[str, Any]):
    year, _, _ = adapter.parse_session_id(session_id)
    session_row = db.get(RaceSession, session_id)
    if session_row and session_row.starts_at:
        starts_at = session_row.starts_at
        if starts_at.tzinfo is None:
            starts_at = starts_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        event = db.get(Event, session_row.event_id)
        duration = timedelta(hours=4 if session_row.abbreviation == "R" else 2)
        if now < starts_at:
            return "ready", _session_state_payload(
                session_row, event, "scheduled",
                f"This session starts at {starts_at.isoformat()}.",
            )
        if now < starts_at + duration:
            return "ready", _session_state_payload(
                session_row, event, "in_progress",
                "The session is in progress. FastF1 publishes downloadable timing after the session.",
            )
        if now < starts_at + duration + timedelta(minutes=90):
            return "ready", _session_state_payload(
                session_row, event, "awaiting_data",
                "The session has ended and detailed FastF1 data is still being published.",
            )
    if year < 2018 and kind not in {"summary", "results"}:
        return "ready", {"availability": "unavailable", "unavailable_reason": "Detailed timing data is available from 2018 onward.", "data": []}
    key = adapter.artifact_key(session_id, kind, options)
    artifact = db.get(DerivedArtifact, key)
    if artifact:
        return "ready", artifact.payload
    job_key = key if kind in {"track", "telemetry"} else adapter.bundle_key(session_id)
    failed = db.scalar(select(IngestionJob).where(
        IngestionJob.key == job_key, IngestionJob.status == "failed"
    ).order_by(IngestionJob.updated_at.desc()))
    if failed:
        return "failed", {"job_id": failed.id, "status": "failed", "progress": failed.progress, "error": failed.error}
    job = queue_job(db, "session", job_key, {"session_id": session_id, "artifact_kind": kind,
                                             "artifact_key": key, "options": options})
    return "queued", {"job_id": job.id, "status": job.status, "progress": job.progress, "artifact_key": key}


def _session_state_payload(session: RaceSession, event: Event | None, availability: str, reason: str) -> dict[str, Any]:
    starts_at = session.starts_at
    if starts_at and starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=timezone.utc)
    return {
        "availability": availability,
        "unavailable_reason": reason,
        "data": {
            "name": session.name,
            "date": starts_at.isoformat() if starts_at else None,
            "event": event.name if event else None,
            "country": event.country if event else None,
            "location": event.location if event else None,
            "total_laps": None,
            "drivers": [],
        },
        "source": "FastF1 schedule",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def sync_circuits(db: Session, adapter: FastF1Adapter, year: int | None = None) -> list[Circuit]:
    for item in adapter.circuits(year):
        external_id = str(item.get("circuitId") or slugify(item.get("circuitName", "circuit")))
        slug = slugify(external_id)
        circuit = db.get(Circuit, slug) or Circuit(slug=slug, external_id=external_id, name=item.get("circuitName", external_id), country=item.get("country") or "Unknown")
        circuit.name = item.get("circuitName") or circuit.name
        circuit.country = item.get("country") or circuit.country
        circuit.locality = item.get("locality")
        circuit.latitude = item.get("lat")
        circuit.longitude = item.get("long")
        circuit.source_url = item.get("circuitUrl") or circuit.source_url
        db.add(circuit)
    db.commit()
    return list(db.scalars(select(Circuit).order_by(Circuit.name)).all())


def circuit_dict(circuit: Circuit) -> dict[str, Any]:
    return {
        "slug": circuit.slug, "external_id": circuit.external_id, "name": circuit.name,
        "country": circuit.country, "locality": circuit.locality, "latitude": circuit.latitude,
        "longitude": circuit.longitude, "length_km": circuit.length_km, "race_laps": circuit.race_laps,
        "lap_record": circuit.lap_record, "first_grand_prix": circuit.first_grand_prix,
        "circuit_type": circuit.circuit_type, "source_url": circuit.source_url,
        "map_data": circuit.map_data, "updated_at": circuit.updated_at.isoformat() if circuit.updated_at else None,
    }


def circuit_event_score(circuit: Circuit, event: Event) -> float:
    if circuit.country.casefold() != event.country.casefold():
        return 0
    target = f"{circuit.name} {circuit.locality or ''}"
    return max(fuzz.token_set_ratio(target, event.location), fuzz.partial_ratio(circuit.name, event.location))


def find_circuit_for_event(db: Session, event: Event) -> Circuit | None:
    candidates = db.scalars(select(Circuit).where(Circuit.country == event.country)).all()
    ranked = sorted(((circuit_event_score(circuit, event), circuit) for circuit in candidates), reverse=True, key=lambda item: item[0])
    return ranked[0][1] if ranked and ranked[0][0] >= 55 else None
