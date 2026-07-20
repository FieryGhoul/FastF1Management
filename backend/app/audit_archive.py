"""Evidence-based completeness audit for a stored Formula 1 archive."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .contracts import ARCHIVE_SCHEMA_VERSION, artifact_key, stores_persistent_telemetry
from .config import get_settings
from .jolpica_dump import HISTORICAL_DUMP_SCHEMA_VERSION, JolpicaDump
from .mongo import database, init_mongo, utcnow
from .serialization import (
    COMPACT_CAR_CHANNELS,
    COMPACT_POSITION_CHANNELS,
    TELEMETRY_POINTS_ENCODING,
    TELEMETRY_SCHEMA_VERSION,
    telemetry_points,
)


CORE_DATASETS = ("summary", "results", "laps", "strategy", "weather", "race-control")
SEASON_DATASETS = ("calendar", "drivers", "constructors", "circuits")


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def nonfinite_paths(value: Any, path: str = "$") -> Iterable[str]:
    if isinstance(value, float) and not math.isfinite(value):
        yield path
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from nonfinite_paths(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from nonfinite_paths(item, f"{path}[{index}]")


def contains_nonfinite(value: Any) -> bool:
    """Fast first pass that avoids constructing a path for every clean value."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, float):
            if not math.isfinite(item):
                return True
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return False


def lap_identity(driver: Any, lap: Any) -> tuple[str, float | str]:
    try:
        normalized_lap: float | str = float(lap)
    except (TypeError, ValueError):
        normalized_lap = str(lap)
    return str(driver), normalized_lap


def audit(start: int, end: int, *, deep: bool = False) -> dict[str, Any]:
    years = list(range(start, end + 1))
    cutoff = utcnow() - timedelta(hours=3)
    sessions = []
    for row in database.sessions.find(
        {"season": {"$gte": start, "$lte": end}},
        {"_id": 1, "season": 1, "round": 1, "code": 1, "starts_at": 1},
    ):
        starts = parse_datetime(row.get("starts_at"))
        if starts and starts < cutoff:
            sessions.append(row)

    stored_years = set(database.seasons.distinct("_id", {"_id": {"$gte": start, "$lte": end}}))
    status_documents = {
        (str(row.get("subject")), str(row.get("dataset"))): row
        for row in database.dataset_status.find(
            {}, {"subject": 1, "dataset": 1, "availability": 1, "schema_version": 1},
        )
    }
    statuses = {
        key: row.get("availability") for key, row in status_documents.items()
    }
    missing_seasons = [year for year in years if year not in stored_years]
    stale_season_schemas = [
        year for year in years
        if (database.seasons.find_one({"_id": year}) or {}).get("schema_version")
        != ARCHIVE_SCHEMA_VERSION
    ]
    missing_season_datasets = [
        f"{year}:{dataset}"
        for year in years
        for dataset in SEASON_DATASETS
        if statuses.get((str(year), dataset)) != "available"
    ]
    missing_session_datasets = [
        f"{row['_id']}:{dataset}"
        for row in sessions
        for dataset in (
            *CORE_DATASETS,
            *(("telemetry",) if stores_persistent_telemetry(row["_id"], row.get("code")) else ()),
        )
        if (row["_id"], dataset) not in statuses
    ]
    unavailable_modern_session_datasets = [
        f"{row['_id']}:{dataset}"
        for row in sessions
        if int(row["season"]) >= 2018
        for dataset in CORE_DATASETS
        if (row["_id"], dataset) in statuses
        and statuses[(row["_id"], dataset)] != "available"
    ]
    session_ids = {row["_id"] for row in sessions}
    stored_artifact_ids = set(database.artifacts.distinct(
        "_id", {"session_id": {"$in": list(session_ids)}},
    ))
    missing_artifacts = [
        f"{session_id}:{dataset}"
        for session_id in sorted(session_ids)
        for dataset in CORE_DATASETS
        if artifact_key(session_id, dataset, {}) not in stored_artifact_ids
    ]
    normalized_collections = {
        "results": database.results,
        "laps": database.laps,
        "strategy": database.strategies,
        "weather": database.weather_samples,
        "race-control": database.race_control_messages,
    }
    normalized_counts = {
        kind: {
            str(row["_id"]): int(row["count"])
            for row in collection.aggregate([
                {"$match": {"session_id": {"$in": list(session_ids)}}},
                {"$group": {"_id": "$session_id", "count": {"$sum": 1}}},
            ])
        }
        for kind, collection in normalized_collections.items()
    }
    canonical_artifact_ids = [
        artifact_key(session_id, kind, {})
        for session_id in session_ids
        for kind in normalized_collections
    ]
    normalized_row_count_errors = []
    for artifact in database.artifacts.find(
        {"_id": {"$in": canonical_artifact_ids}},
        {"session_id": 1, "kind": 1, "payload.data": 1},
    ):
        data = (artifact.get("payload") or {}).get("data")
        if not isinstance(data, list):
            continue
        session_id = str(artifact.get("session_id"))
        kind = str(artifact.get("kind"))
        stored_count = normalized_counts.get(kind, {}).get(session_id, 0)
        if stored_count != len(data):
            normalized_row_count_errors.append(
                f"{session_id}:{kind}:artifact={len(data)}:stored={stored_count}"
            )
    normalized_row_count_errors.sort()
    completed_races = {
        (int(row["season"]), int(row["round"]))
        for row in sessions if row.get("code") == "R"
    }
    stored_standings = {
        (int(row["season"]), int(row["round"]), row["kind"])
        for row in database.standings.find(
            {"season": {"$gte": start, "$lte": end}, "round": {"$ne": None}},
            {"season": 1, "round": 1, "kind": 1, "data": 1},
        )
        if row.get("data")
    }
    missing_standings = [
        f"{year}:{round_number}:{kind}"
        for year, round_number in sorted(completed_races)
        for kind in ("drivers", "constructors")
        if (year, round_number, kind) not in stored_standings
    ]
    missing_maps = []
    missing_circuit_metadata = []
    for row in database.circuits.find({}, {"map_data": 1, "map_reference_session": 1}):
        points = (row.get("map_data") or {}).get("points")
        if not isinstance(points, list) or len(points) < 3 or not row.get("map_reference_session"):
            missing_maps.append(row["_id"])
    for row in database.circuits.find({}, {
        "circuit_metadata": 1,
        "length_km": 1,
        "corner_count": 1,
        "circuit_type": 1,
        "first_grand_prix": 1,
    }):
        if (
            not row.get("circuit_metadata")
            or row.get("length_km") is None
            or row.get("corner_count") is None
            or not row.get("circuit_type")
            or row.get("first_grand_prix") is None
        ):
            missing_circuit_metadata.append(row["_id"])
    circuit_ids = set(database.circuits.distinct("_id"))
    missing_event_circuit_links = sorted(
        str(row["_id"])
        for row in database.events.find(
            {"season": {"$gte": start, "$lte": end}},
            {"circuit_slug": 1},
        )
        if row.get("circuit_slug") not in circuit_ids
    )
    failures = [
        f"{row.get('phase')}:{row.get('subject')}"
        for row in database.backfill_failures.find({"run": f"archive_backfill:{start}:{end}"})
    ]

    def subject_in_range(subject: str) -> bool:
        try:
            return start <= int(subject.split("-", 1)[0]) <= end
        except (TypeError, ValueError):
            return False

    telemetry_session_ids = {
        str(subject) for subject in database.telemetry_laps.distinct("session_id")
        if subject_in_range(str(subject))
    }
    unexpected_non_race_telemetry_sessions = sorted(
        session_id for session_id in telemetry_session_ids
        if not stores_persistent_telemetry(session_id)
    )
    telemetry_scope = {"session_id": {"$in": list(telemetry_session_ids)}}
    available_telemetry = {
        subject for (subject, dataset), availability in statuses.items()
        if dataset == "telemetry"
        and availability == "available"
        and subject_in_range(subject)
        and stores_persistent_telemetry(subject)
    }
    telemetry_status_without_rows = sorted(available_telemetry - telemetry_session_ids)
    telemetry_rows_without_status = sorted(
        session_id for session_id in telemetry_session_ids
        if stores_persistent_telemetry(session_id)
        if statuses.get((session_id, "telemetry")) != "available"
    )
    telemetry_lap_count_errors = []
    telemetry_lap_identity_errors = []
    for session_id in sorted(available_telemetry):
        timing_laps = database.laps.count_documents({"session_id": session_id})
        telemetry_laps = database.telemetry_laps.count_documents({
            "session_id": session_id,
        })
        if timing_laps != telemetry_laps:
            telemetry_lap_count_errors.append(
                f"{session_id}:timing={timing_laps}:telemetry={telemetry_laps}"
            )
            continue
        timing_identities = Counter(
            lap_identity(row.get("Driver"), row.get("LapNumber"))
            for row in database.laps.find(
                {"session_id": session_id}, {"Driver": 1, "LapNumber": 1},
            )
        )
        telemetry_identities = Counter(
            lap_identity(row.get("driver"), row.get("lap"))
            for row in database.telemetry_laps.find(
                {"session_id": session_id}, {"driver": 1, "lap": 1},
            )
        )
        if timing_identities != telemetry_identities:
            telemetry_lap_identity_errors.append(session_id)
    stale_telemetry_schemas = sorted(
        session_id for session_id in session_ids
        if stores_persistent_telemetry(session_id)
        if (session_id, "telemetry") in status_documents
        and status_documents[(session_id, "telemetry")].get("schema_version")
        != TELEMETRY_SCHEMA_VERSION
    )

    formatting_errors = []
    historical_row_count_errors = []
    if deep:
        historical_dump = JolpicaDump(get_settings().fastf1_cache.parent / "jolpica-dump")
        if historical_dump.source_available:
            historical_dump.prepare()
            for session in sessions:
                if int(session["season"]) >= 2018:
                    continue
                summary_artifact = database.artifacts.find_one({
                    "_id": artifact_key(session["_id"], "summary", {}),
                }, {"payload.source": 1, "payload.schema_version": 1})
                source = ((summary_artifact or {}).get("payload") or {}).get("source")
                if source == "FastF1 Jolpica":
                    historical_row_count_errors.append(
                        f"{session['_id']}:page-limited-source"
                    )
                    continue
                if source != "Jolpica CSV database dump":
                    continue
                source_version = ((summary_artifact or {}).get("payload") or {}).get("schema_version")
                if source_version != HISTORICAL_DUMP_SCHEMA_VERSION:
                    historical_row_count_errors.append(
                        f"{session['_id']}:source-schema={source_version}"
                    )
                    continue
                expected = historical_dump.expected_counts(
                    int(session["season"]), int(session["round"]), str(session["code"]),
                )
                if expected is None:
                    continue
                for kind, collection in (
                    ("results", database.results),
                    ("laps", database.laps),
                    ("strategy", database.strategies),
                ):
                    actual = collection.count_documents({"session_id": session["_id"]})
                    if actual != expected[kind]:
                        historical_row_count_errors.append(
                            f"{session['_id']}:{kind}:expected={expected[kind]}:actual={actual}"
                        )
    sources = (
        ("seasons", database.seasons.find()),
        ("events", database.events.find()),
        ("sessions", database.sessions.find()),
        ("drivers", database.drivers.find()),
        ("constructors", database.constructors.find()),
        ("artifacts", database.artifacts.find({}, {"payload": 1})),
        ("circuits", database.circuits.find({}, {"map_data": 1})),
        ("standings", database.standings.find({}, {"data": 1})),
        ("results", database.results.find()),
        ("laps", database.laps.find()),
        ("strategies", database.strategies.find()),
        ("weather_samples", database.weather_samples.find()),
        ("race_control_messages", database.race_control_messages.find()),
    )
    for collection, documents in sources:
        for document in documents:
            if contains_nonfinite(document):
                for path in nonfinite_paths(document):
                    formatting_errors.append(f"{collection}:{document['_id']}:{path}")
    obsolete_artifacts = sorted(database.artifacts.distinct(
        "_id", {"_id": {"$regex": "^v3:"}},
    ))
    telemetry_format_errors = []
    telemetry_documents = database.telemetry_laps.find(telemetry_scope) if deep else database.telemetry_laps.find(
        telemetry_scope, {
            "session_id": 1,
            "schema_version": 1,
            "car_point_count": 1,
            "car_points_encoding": 1,
            "position_point_count": 1,
            "position_points_encoding": 1,
            "distance_normalized": 1,
        },
    )
    for document in telemetry_documents:
        if document.get("schema_version") != TELEMETRY_SCHEMA_VERSION:
            telemetry_format_errors.append(f"{document['_id']}:invalid_schema_version")
        if document.get("distance_normalized") is not True:
            telemetry_format_errors.append(f"{document['_id']}:distance_not_normalized")
        for stream in ("car", "position"):
            prefix = f"{stream}_"
            label = stream
            if document.get(f"{prefix}points_encoding") != TELEMETRY_POINTS_ENCODING:
                telemetry_format_errors.append(f"{document['_id']}:{label}:invalid_encoding")
            count = document.get(f"{prefix}point_count")
            minimum = 1
            if not isinstance(count, int) or count < minimum:
                telemetry_format_errors.append(f"{document['_id']}:{label}:invalid_point_count")
            if deep:
                points = telemetry_points(document, stream)
                if len(points) != count:
                    telemetry_format_errors.append(f"{document['_id']}:{label}:point_count_mismatch")
                if stream == "position" and points and not all(
                    "Time" in point and "Distance" in point for point in points
                ):
                    telemetry_format_errors.append(
                        f"{document['_id']}:{label}:missing_time_or_distance"
                    )
                allowed_channels = (
                    set(COMPACT_CAR_CHANNELS)
                    if stream == "car"
                    else set(COMPACT_POSITION_CHANNELS)
                )
                unexpected_channels = sorted({
                    key for point in points for key in point
                    if key not in allowed_channels
                })
                if unexpected_channels:
                    telemetry_format_errors.append(
                        f"{document['_id']}:{label}:unexpected_channels="
                        f"{','.join(unexpected_channels)}"
                    )
                if stream == "position" and points:
                    distances = [
                        float(point["Distance"])
                        for point in points
                        if isinstance(point.get("Distance"), (int, float))
                        and not isinstance(point.get("Distance"), bool)
                        and math.isfinite(float(point["Distance"]))
                    ]
                    if distances and (
                        abs(distances[0]) > 1.0
                        or max(distances) > 15_000
                    ):
                        telemetry_format_errors.append(
                            f"{document['_id']}:{label}:distance_not_lap_relative"
                        )
                if contains_nonfinite(points):
                    for path in nonfinite_paths(points):
                        formatting_errors.append(
                            f"telemetry_laps:{document['_id']}:{label}:{path}"
                        )

    problems = {
        "missing_seasons": missing_seasons,
        "stale_season_schemas": stale_season_schemas,
        "missing_season_datasets": missing_season_datasets,
        "missing_session_datasets": missing_session_datasets,
        "unavailable_modern_session_datasets": unavailable_modern_session_datasets,
        "missing_artifacts": missing_artifacts,
        "normalized_row_count_errors": normalized_row_count_errors,
        "missing_standings": missing_standings,
        "missing_maps": missing_maps,
        "missing_circuit_metadata": missing_circuit_metadata,
        "missing_event_circuit_links": missing_event_circuit_links,
        "unexpected_non_race_telemetry_sessions": unexpected_non_race_telemetry_sessions,
        "telemetry_status_without_rows": telemetry_status_without_rows,
        "telemetry_rows_without_status": telemetry_rows_without_status,
        "telemetry_lap_count_errors": telemetry_lap_count_errors,
        "telemetry_lap_identity_errors": telemetry_lap_identity_errors,
        "stale_telemetry_schemas": stale_telemetry_schemas,
        "telemetry_format_errors": telemetry_format_errors,
        "backfill_failures": failures,
        "obsolete_artifacts": obsolete_artifacts,
        "formatting_errors": formatting_errors,
        "historical_row_count_errors": historical_row_count_errors,
    }
    complete = all(not items for items in problems.values())
    return {
        "complete": complete,
        "range": {"start": start, "end": end},
        "counts": {
            "seasons": database.seasons.count_documents({"_id": {"$gte": start, "$lte": end}}),
            "completed_sessions": len(sessions),
            "circuits": database.circuits.count_documents({}),
            "maps": database.circuits.count_documents({"map_data": {"$ne": None}}),
            "telemetry_sessions": len(database.telemetry_laps.distinct(
                "session_id", {
                    **telemetry_scope,
                    "schema_version": TELEMETRY_SCHEMA_VERSION,
                },
            )),
            "telemetry_laps": database.telemetry_laps.count_documents({
                **telemetry_scope,
                "schema_version": TELEMETRY_SCHEMA_VERSION,
            }),
            "raw_stream_laps": database.telemetry_laps.count_documents({
                **telemetry_scope,
                "schema_version": TELEMETRY_SCHEMA_VERSION,
                "car_points_encoding": TELEMETRY_POINTS_ENCODING,
                "position_points_encoding": TELEMETRY_POINTS_ENCODING,
            }),
            "outdated_telemetry_laps": database.telemetry_laps.count_documents({
                **telemetry_scope,
                "schema_version": {"$ne": TELEMETRY_SCHEMA_VERSION},
            }),
        },
        "problem_counts": {key: len(items) for key, items in problems.items()},
        "problems": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit MongoDB archive completeness and formatting")
    parser.add_argument("--start", type=int, default=2000)
    parser.add_argument("--end", type=int, default=utcnow().year)
    parser.add_argument("--deep", action="store_true", help="decompress and inspect every telemetry point")
    args = parser.parse_args()
    init_mongo()
    result = audit(args.start, args.end, deep=args.deep)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
