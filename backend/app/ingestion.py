import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from pymongo import UpdateOne
from pymongo.database import Database

from .circuit_matching import circuit_match_score, country_variants
from .contracts import ARCHIVE_SCHEMA_VERSION, stores_persistent_telemetry
from .fastf1_adapter import FastF1Adapter, slugify
from .mongo import set_dataset_status, utcnow
from .serialization import (
    TELEMETRY_POINTS_ENCODING,
    TELEMETRY_SCHEMA_VERSION,
    compact_car_points,
    compact_distance_points,
    compress_telemetry_points,
    telemetry_points,
)


def _checksum(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _lap_identity(driver: Any, lap: Any) -> tuple[str, float | str]:
    try:
        normalized_lap: float | str = float(lap)
    except (TypeError, ValueError):
        normalized_lap = str(lap)
    return str(driver), normalized_lap


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
            "map_source_url": existing.get("map_source_url"),
            "map_source_attribution": existing.get("map_source_attribution"),
            "map_catalog_id": existing.get("map_catalog_id"),
            "map_catalog_name": existing.get("map_catalog_name"),
            "map_match_score": existing.get("map_match_score"),
            "updated_at": now,
        })
    _replace_many(db.circuits, documents)
    set_dataset_status(db, str(season or "all"), "circuits", "available", source="FastF1 Jolpica", checksum=_checksum(documents))
    return len(documents)


def link_event_circuits(db: Database, year: int | None = None) -> dict[str, Any]:
    """Persist the canonical circuit identity used by every map view."""
    query = {"season": year} if year is not None else {}
    circuits = list(db.circuits.find({}, {
        "name": 1, "country": 1, "locality": 1,
    }))
    matched = 0
    unmatched = []
    for event in db.events.find(query, {
        "season": 1, "country": 1, "location": 1, "name": 1,
    }):
        accepted_countries = country_variants(event.get("country"))
        candidates = [
            circuit for circuit in circuits
            if circuit.get("country") in accepted_countries
        ]
        target = f"{event.get('location', '')} {event.get('name', '')}"
        ranked = sorted(
            ((circuit_match_score(circuit, target), circuit) for circuit in candidates),
            key=lambda pair: pair[0],
            reverse=True,
        )
        if not ranked or (len(candidates) > 1 and ranked[0][0] < 55):
            unmatched.append(str(event["_id"]))
            continue
        circuit_slug = str(ranked[0][1]["_id"])
        db.events.update_one(
            {"_id": event["_id"]}, {"$set": {"circuit_slug": circuit_slug}},
        )
        db.sessions.update_many(
            {"event_id": event["_id"]}, {"$set": {"circuit_slug": circuit_slug}},
        )
        matched += 1
    return {"matched": matched, "unmatched": unmatched}


def sync_season(db: Database, adapter: FastF1Adapter, year: int) -> dict[str, int]:
    now = utcnow()
    events = adapter.schedule(year)
    event_documents = []
    session_documents = []
    for event in events:
        event_document = {"_id": event["id"], **event, "synced_at": now}
        event_documents.append(event_document)
        for session in event.get("sessions", []):
            existing_session = db.sessions.find_one({"_id": session["id"]}) or {}
            session_documents.append({
                **existing_session,
                "_id": session["id"],
                **session,
                "event_id": event["id"],
                "season": year,
                "round": event["round"],
                "event_name": event["name"],
                "country": event["country"],
                "location": event["location"],
                "status": existing_session.get("status", "scheduled"),
                "synced_at": now,
            })
    _replace_many(db.events, event_documents)
    _replace_many(db.sessions, session_documents)
    db.seasons.replace_one(
        {"_id": year},
        {
            "_id": year,
            "year": year,
            "event_count": len(events),
            "schema_version": ARCHIVE_SCHEMA_VERSION,
            "last_synced_at": now,
            "source": "FastF1",
        },
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
            current_ids = [document["_id"] for document in documents]
            db[entity].delete_many({
                "season": year,
                "_id": {"$nin": current_ids},
            })
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
        links = link_event_circuits(db, year)
        counts["circuit_links"] = links["matched"]
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
    driver_codes = {
        str(row.get("driverId")): row.get("driverCode") or str(row.get("driverId"))
        for row in db.drivers.find(
            {"season": session_document.get("season")},
            {"driverId": 1, "driverCode": 1},
        )
        if row.get("driverId")
    }
    for envelope in bundle.values():
        if not isinstance(envelope.get("data"), list):
            continue
        for row in envelope["data"]:
            driver_id = row.get("DriverId") or row.get("driverId")
            if driver_id and (not row.get("Driver") or row.get("Driver") == driver_id):
                row["Driver"] = driver_codes.get(str(driver_id), str(driver_id))
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
        # Clear any previous completion marker before replacing the artifact
        # or its normalized rows. A terminated process must leave this dataset
        # resumable instead of allowing partial rows to look complete.
        set_dataset_status(
            db,
            session_id,
            kind,
            "awaiting_data",
            source=envelope.get("source", "FastF1"),
            reason="Dataset ingestion is in progress.",
            schema_version=envelope.get("schema_version"),
        )
        artifact_id = adapter.artifact_key(session_id, kind, {})
        artifact = {
            "_id": artifact_id,
            "session_id": session_id,
            "kind": kind,
            "options": {},
            "payload": envelope,
            "updated_at": utcnow(),
        }
        db.artifacts.delete_many({
            "session_id": session_id,
            "kind": kind,
            "_id": {"$ne": artifact_id},
        })
        db.artifacts.replace_one({"_id": artifact_id}, artifact, upsert=True)
        data = envelope.get("data")
        if kind in collection_by_kind and isinstance(data, list):
            collection, fields = collection_by_kind[kind]
            # Match the canonical key as well as its deterministic document
            # prefix. The prefix also cleans rows written by older importers
            # where an upstream ``session_id`` accidentally replaced ours.
            collection.delete_many({"$or": [
                {"session_id": session_id},
                {"_id": {"$regex": f"^{re.escape(session_id)}:"}},
            ]})
            documents = []
            for index, row in enumerate(data):
                normalized = dict(row)
                upstream_session_id = normalized.get("session_id")
                if upstream_session_id not in (None, session_id):
                    normalized["source_session_id"] = upstream_session_id
                upstream_document_id = normalized.pop("_id", None)
                if upstream_document_id is not None:
                    normalized["source_document_id"] = upstream_document_id
                normalized["session_id"] = session_id
                normalized["_id"] = _normalized_id(session_id, row, index, *fields)
                documents.append(normalized)
            if documents:
                collection.insert_many(documents, ordered=False)
            stored_count = collection.count_documents({"session_id": session_id})
            if stored_count != len(documents):
                raise RuntimeError(
                    f"{kind} row count mismatch for {session_id}: "
                    f"expected {len(documents)}, stored {stored_count}"
                )
            counts[kind] = len(documents)
        set_dataset_status(
            db, session_id, kind, envelope.get("availability", "unavailable"),
            source=envelope.get("source", "FastF1"), reason=envelope.get("unavailable_reason"),
            checksum=_checksum(data), schema_version=envelope.get("schema_version"),
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
    if not stores_persistent_telemetry(session_id):
        raise ValueError(
            f"Persistent telemetry is restricted to race sessions: {session_id}",
        )
    # Invalidate the previous completion marker before replacing any rows.
    # If the process is interrupted between batches, the next run must rebuild
    # the session instead of accepting a partial set of schema-current rows.
    set_dataset_status(
        db,
        session_id,
        "telemetry",
        "awaiting_data",
        source="FastF1",
        reason="Telemetry ingestion is in progress.",
        schema_version=TELEMETRY_SCHEMA_VERSION,
    )
    db.telemetry_laps.delete_many({"session_id": session_id})
    checksum_rows = []
    stored_lap_identities = []
    batch = []
    count = 0
    iterator = (
        adapter.iter_session_telemetry_laps(session_id)
        if hasattr(adapter, "iter_session_telemetry_laps")
        else iter(adapter.session_telemetry_laps(session_id))
    )
    for item in iterator:
        stored_lap_identities.append(
            _lap_identity(item.get("driver"), item.get("lap")),
        )
        item["_id"] = f"{session_id}:{item.get('driver')}:{item.get('lap')}"
        item["updated_at"] = utcnow()
        merged_points = item.pop("points", [])
        car_points = compact_car_points(item.pop("car_points", []) or merged_points)
        position_source = item.pop("position_points", [])
        # The adapter now supplies this directly from distance-enriched car
        # samples, avoiding FastF1's expensive merged/position calculations.
        # The fallback keeps compatible adapters and interrupted old jobs safe.
        position_points = compact_distance_points(position_source)
        if not position_points:
            position_points = compact_distance_points(merged_points)
        counts = {}
        for stream, points in (("car", car_points), ("position", position_points)):
            prefix = f"{stream}_"
            item[f"{prefix}points_compressed"] = compress_telemetry_points(points)
            item[f"{prefix}points_encoding"] = TELEMETRY_POINTS_ENCODING
            item[f"{prefix}point_count"] = len(points)
            counts[f"{prefix}point_count"] = len(points)
        item["schema_version"] = TELEMETRY_SCHEMA_VERSION
        item["distance_normalized"] = True
        checksum_rows.append({"_id": item["_id"], **counts})
        batch.append(item)
        count += 1
        if len(batch) >= 50:
            db.telemetry_laps.insert_many(batch, ordered=False)
            batch = []
    if batch:
        db.telemetry_laps.insert_many(batch, ordered=False)
    expected_laps = db.laps.count_documents({"session_id": session_id})
    if expected_laps and count != expected_laps:
        raise RuntimeError(
            f"Telemetry lap count mismatch for {session_id}: "
            f"expected {expected_laps} timing laps, stored {count} telemetry laps"
        )
    if expected_laps:
        expected_lap_identities = Counter(
            _lap_identity(row.get("Driver"), row.get("LapNumber"))
            for row in db.laps.find(
                {"session_id": session_id}, {"Driver": 1, "LapNumber": 1},
            )
        )
        if expected_lap_identities != Counter(stored_lap_identities):
            raise RuntimeError(
                f"Telemetry lap identity mismatch for {session_id}; "
                "stored driver/lap pairs do not match the timing archive"
            )
    availability = "available" if count else "unavailable"
    reason = None if count else "No telemetry laps were published for this session."
    set_dataset_status(
        db,
        session_id,
        "telemetry",
        availability,
        source="FastF1",
        reason=reason,
        checksum=_checksum(checksum_rows),
        schema_version=TELEMETRY_SCHEMA_VERSION,
    )
    return count


def migrate_telemetry_schema(
    db: Database,
    *,
    progress: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """Convert older telemetry to compact car plus time/distance streams."""
    migrated = 0
    failed = 0
    sessions: set[str] = set()
    query = {
        "$or": [
            {"schema_version": {"$ne": TELEMETRY_SCHEMA_VERSION}},
            {"points_compressed": {"$exists": True}},
            {"points": {"$exists": True}},
        ]
    }
    projection = {
        "session_id": 1,
        "schema_version": 1,
        "points": 1, "points_encoding": 1, "points_compressed": 1,
        "car_points": 1, "car_points_encoding": 1, "car_points_compressed": 1,
        "position_points": 1, "position_points_encoding": 1,
        "position_points_compressed": 1,
    }
    total = db.telemetry_laps.count_documents(query)
    updates: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def flush_updates() -> None:
        nonlocal updates
        if not updates:
            return
        try:
            db.telemetry_laps.bulk_write(
                [UpdateOne(selector, update) for selector, update in updates],
                ordered=False,
            )
        except TypeError:
            # mongomock can lag PyMongo's UpdateOne signature. Keep tests and
            # local mock workflows compatible without slowing real MongoDB.
            for selector, update in updates:
                db.telemetry_laps.update_one(selector, update)
        updates = []

    for document in db.telemetry_laps.find(query, projection):
        merged = telemetry_points(document)
        car = telemetry_points(document, "car") or merged
        compact_car = compact_car_points(car)
        compact_position = compact_distance_points(merged)
        if not compact_car or not compact_position:
            failed += 1
            continue
        updates.append((
            {"_id": document["_id"]}, {
                "$set": {
                    "car_points_compressed": compress_telemetry_points(compact_car),
                    "car_points_encoding": TELEMETRY_POINTS_ENCODING,
                    "car_point_count": len(compact_car),
                    "position_points_compressed": compress_telemetry_points(compact_position),
                    "position_points_encoding": TELEMETRY_POINTS_ENCODING,
                    "position_point_count": len(compact_position),
                    "schema_version": TELEMETRY_SCHEMA_VERSION,
                    "distance_normalized": True,
                    "updated_at": utcnow(),
                },
                "$unset": {
                    "points": "", "points_compressed": "", "points_encoding": "",
                    "point_count": "", "car_points": "", "position_points": "",
                },
            },
        ))
        sessions.add(str(document.get("session_id")))
        migrated += 1
        if len(updates) >= 100:
            flush_updates()
        if progress and (migrated + failed) % 250 == 0:
            progress(
                f"telemetry laps: {migrated + failed}/{total}; "
                f"compacted: {migrated}; failed: {failed}",
            )
    flush_updates()
    if progress and total:
        progress(
            f"telemetry laps: {migrated + failed}/{total}; "
            f"compacted: {migrated}; failed: {failed}",
        )

    for session_id in sessions:
        if db.telemetry_laps.count_documents({
            "session_id": session_id,
            "$or": [
                {"schema_version": {"$ne": TELEMETRY_SCHEMA_VERSION}},
                {"points_compressed": {"$exists": True}},
                {"points": {"$exists": True}},
            ],
        }) == 0:
            db.dataset_status.update_one(
                {"subject": session_id, "dataset": "telemetry"},
                {"$set": {
                    "schema_version": TELEMETRY_SCHEMA_VERSION,
                    "updated_at": utcnow(),
                }},
            )
    db.dataset_status.update_many(
        {"dataset": "telemetry", "availability": "unavailable"},
        {"$set": {
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "updated_at": utcnow(),
        }},
    )
    return {"migrated": migrated, "failed": failed, "sessions": len(sessions)}


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
