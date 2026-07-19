import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database

from .circuit_matching import circuit_match_score, country_variants
from .fastf1_adapter import FastF1Adapter, slugify
from .mongo import set_dataset_status, utcnow
from .serialization import TELEMETRY_POINTS_ENCODING, compress_telemetry_points


def _checksum(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _replace_many(collection, documents: list[dict[str, Any]]) -> None:
    for item in documents:
        collection.replace_one({"_id": item["_id"]}, item, upsert=True)


def sync_circuits(db: Database, adapter: FastF1Adapter, season: int | None = None) -> int:
    now = utcnow()
    documents = []
    for item in adapter.circuits(season):
        external_id = str(item.get("circuitId") or slugify(item.get("circuitName", "circuit")))
        existing = db.circuits.find_one({"external_id": external_id}) or {}
        documents.append({
            "_id": slugify(external_id),
            "slug": slugify(external_id),
            "external_id": external_id,
            "name": item.get("circuitName") or external_id,
            "country": item.get("country") or "Unknown",
            "locality": item.get("locality"),
            "latitude": item.get("lat"),
            "longitude": item.get("long"),
            "length_km": existing.get("length_km"),
            "race_laps": existing.get("race_laps"),
            "lap_record": existing.get("lap_record"),
            "first_grand_prix": existing.get("first_grand_prix"),
            "circuit_type": existing.get("circuit_type"),
            "source_url": item.get("circuitUrl") or existing.get("source_url"),
            "source_attribution": existing.get("source_attribution"),
            "map_data": existing.get("map_data"),
            "map_reference_session": existing.get("map_reference_session"),
            "updated_at": now,
        })
    _replace_many(db.circuits, documents)
    set_dataset_status(db, str(season or "all"), "circuits", "available", source="FastF1 Jolpica", checksum=_checksum(documents))
    return len(documents)


def sync_season(db: Database, adapter: FastF1Adapter, year: int) -> dict[str, int]:
    now = utcnow()
    events = adapter.schedule(year)
    event_documents = []
    session_documents = []
    for event in events:
        event_document = {"_id": event["id"], **event, "synced_at": now}
        event_documents.append(event_document)
        for session in event.get("sessions", []):
            session_documents.append({
                "_id": session["id"],
                **session,
                "event_id": event["id"],
                "season": year,
                "round": event["round"],
                "event_name": event["name"],
                "country": event["country"],
                "location": event["location"],
                "status": "scheduled",
                "synced_at": now,
            })
    _replace_many(db.events, event_documents)
    _replace_many(db.sessions, session_documents)
    db.seasons.replace_one(
        {"_id": year},
        {"_id": year, "year": year, "event_count": len(events), "last_synced_at": now, "source": "FastF1"},
        upsert=True,
    )
    counts = {"events": len(event_documents), "sessions": len(session_documents)}

    for entity, fetcher, identifier in (
        ("drivers", adapter.drivers, "driverId"),
        ("constructors", adapter.constructors, "constructorId"),
    ):
        try:
            rows = fetcher(year)
            documents = [{"_id": f"{year}:{row.get(identifier)}", "season": year, **row, "synced_at": now} for row in rows]
            _replace_many(db[entity], documents)
            counts[entity] = len(documents)
            set_dataset_status(db, str(year), entity, "available", source="FastF1 Jolpica", checksum=_checksum(rows))
        except Exception as exc:
            set_dataset_status(db, str(year), entity, "unavailable", source="FastF1 Jolpica", reason=str(exc))

    for kind in ("drivers", "constructors"):
        try:
            rows = adapter.standings(year, kind)
            document = {
                "_id": f"{year}:latest:{kind}", "season": year, "round": None,
                "kind": kind, "data": rows, "synced_at": now,
            }
            db.standings.replace_one({"_id": document["_id"]}, document, upsert=True)
            counts[f"{kind}_standings"] = len(rows)
            set_dataset_status(db, str(year), f"{kind}_standings", "available", source="FastF1 Jolpica", checksum=_checksum(rows))
        except Exception as exc:
            set_dataset_status(db, str(year), f"{kind}_standings", "unavailable", source="FastF1 Jolpica", reason=str(exc))

    try:
        counts["circuits"] = sync_circuits(db, adapter, year)
    except Exception as exc:
        set_dataset_status(db, str(year), "circuits", "unavailable", source="FastF1 Jolpica", reason=str(exc))
    set_dataset_status(db, str(year), "calendar", "available", source="FastF1", checksum=_checksum(events))
    return counts


def sync_standings(db: Database, adapter: FastF1Adapter, year: int, round_number: int, kind: str) -> int:
    rows = adapter.standings(year, kind, round_number)
    now = utcnow()
    document = {
        "_id": f"{year}:{round_number}:{kind}",
        "season": year,
        "round": round_number,
        "kind": kind,
        "data": rows,
        "synced_at": now,
    }
    db.standings.replace_one({"_id": document["_id"]}, document, upsert=True)
    set_dataset_status(
        db, f"{year}-{round_number}", f"{kind}_standings",
        "available" if rows else "unavailable", source="FastF1 Jolpica",
        reason=None if rows else "No standings were published for this round.", checksum=_checksum(rows),
    )
    return len(rows)


def _normalized_id(session_id: str, row: dict[str, Any], index: int, *fields: str) -> str:
    parts = [str(row.get(field)) for field in fields if row.get(field) is not None]
    return ":".join([session_id, *parts, str(index)])


def persist_session_bundle(db: Database, adapter: FastF1Adapter, session_id: str) -> dict[str, int]:
    bundle = adapter.session_bundle(session_id)
    session_document = db.sessions.find_one({"_id": session_id}) or {}
    summary = bundle.get("summary", {}).get("data")
    if isinstance(summary, dict):
        summary["name"] = summary.get("name") or session_document.get("name")
        summary["date"] = summary.get("date") or session_document.get("starts_at")
        summary["event"] = summary.get("event") or session_document.get("event_name")
        summary["country"] = summary.get("country") or session_document.get("country")
        summary["location"] = summary.get("location") or session_document.get("location")
    counts: dict[str, int] = {}
    collection_by_kind = {
        "results": (db.results, ("DriverNumber", "Abbreviation")),
        "laps": (db.laps, ("Driver", "LapNumber")),
        "strategy": (db.strategies, ("Driver", "Stint")),
        "weather": (db.weather_samples, ("Time",)),
        "race-control": (db.race_control_messages, ("Time", "Message")),
    }
    for kind, envelope in bundle.items():
        artifact_id = adapter.artifact_key(session_id, kind, {})
        artifact = {
            "_id": artifact_id,
            "session_id": session_id,
            "kind": kind,
            "options": {},
            "payload": envelope,
            "updated_at": utcnow(),
        }
        db.artifacts.replace_one({"_id": artifact_id}, artifact, upsert=True)
        data = envelope.get("data")
        if kind in collection_by_kind and isinstance(data, list):
            collection, fields = collection_by_kind[kind]
            collection.delete_many({"session_id": session_id})
            documents = [
                {"_id": _normalized_id(session_id, row, index, *fields), "session_id": session_id, **row}
                for index, row in enumerate(data)
            ]
            if documents:
                collection.insert_many(documents, ordered=False)
            counts[kind] = len(documents)
        set_dataset_status(
            db, session_id, kind, envelope.get("availability", "unavailable"),
            source=envelope.get("source", "FastF1"), reason=envelope.get("unavailable_reason"),
            checksum=_checksum(data),
        )
    db.sessions.update_one({"_id": session_id}, {"$set": {"status": "processed", "last_synced_at": utcnow()}})
    return counts


def persist_track(db: Database, adapter: FastF1Adapter, session_id: str) -> dict[str, Any]:
    circuit = find_circuit_for_session(db, session_id)
    if circuit and circuit.get("map_data"):
        envelope = {
            "availability": "available",
            "unavailable_reason": None,
            "data": circuit["map_data"],
            "source": "MongoDB canonical circuit map",
            "updated_at": circuit.get("updated_at"),
        }
    else:
        envelope = adapter.session_artifact(session_id, "track", {})
    artifact_id = adapter.artifact_key(session_id, "track", {})
    db.artifacts.replace_one(
        {"_id": artifact_id},
        {"_id": artifact_id, "session_id": session_id, "kind": "track", "options": {}, "payload": envelope, "updated_at": utcnow()},
        upsert=True,
    )
    if envelope.get("availability") == "available" and envelope.get("data"):
        if circuit:
            db.circuits.update_one(
                {"_id": circuit["_id"]},
                {"$set": {"map_data": envelope["data"], "map_reference_session": session_id, "updated_at": utcnow()}},
            )
    set_dataset_status(
        db, session_id, "track", envelope.get("availability", "unavailable"),
        source=envelope.get("source", "FastF1"), reason=envelope.get("unavailable_reason"),
        checksum=_checksum(envelope.get("data")),
    )
    return envelope


def persist_telemetry(db: Database, adapter: FastF1Adapter, session_id: str) -> int:
    documents = adapter.session_telemetry_laps(session_id)
    db.telemetry_laps.delete_many({"session_id": session_id})
    checksum_rows = []
    for item in documents:
        item["_id"] = f"{session_id}:{item.get('driver')}:{item.get('lap')}"
        item["updated_at"] = utcnow()
        points = item.pop("points", [])
        item["points_compressed"] = compress_telemetry_points(points)
        item["points_encoding"] = TELEMETRY_POINTS_ENCODING
        item["point_count"] = len(points)
        checksum_rows.append({"_id": item["_id"], "point_count": len(points)})
    if documents:
        db.telemetry_laps.insert_many(documents, ordered=False)
    availability = "available" if documents else "unavailable"
    reason = None if documents else "No telemetry laps were published for this session."
    set_dataset_status(db, session_id, "telemetry", availability, source="FastF1", reason=reason, checksum=_checksum(checksum_rows))
    return len(documents)


def find_circuit_for_session(db: Database, session_id: str) -> dict[str, Any] | None:
    session = db.sessions.find_one({"_id": session_id})
    if not session:
        return None
    event = db.events.find_one({"_id": session["event_id"]})
    if not event:
        return None
    candidates = list(db.circuits.find({"country": {"$in": country_variants(event.get("country"))}}))
    target = f"{event.get('location', '')} {event.get('name', '')}"
    ranked = sorted(
        ((circuit_match_score(c, target), c) for c in candidates),
        key=lambda pair: pair[0], reverse=True,
    )
    return ranked[0][1] if ranked and ranked[0][0] >= 55 else None
