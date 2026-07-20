from app.audit_archive import CORE_DATASETS, SEASON_DATASETS, audit
from app.contracts import ARCHIVE_SCHEMA_VERSION, artifact_key
from app.mongo import database, set_dataset_status
from app.serialization import (
    TELEMETRY_POINTS_ENCODING,
    TELEMETRY_SCHEMA_VERSION,
    compress_telemetry_points,
)


def setup_function():
    for name in database.list_collection_names():
        database[name].delete_many({})


def test_audit_proves_a_fully_recorded_historical_season():
    database.seasons.insert_one({
        "_id": 2000, "year": 2000, "schema_version": ARCHIVE_SCHEMA_VERSION,
    })
    for dataset in SEASON_DATASETS:
        set_dataset_status(database, "2000", dataset, "available", source="test")
    database.sessions.insert_one({
        "_id": "2000-1-R", "season": 2000, "round": 1, "code": "R",
        "starts_at": "2000-03-01T14:00:00+00:00",
    })
    for dataset in CORE_DATASETS:
        availability = "available" if dataset in {"summary", "results"} else "unavailable"
        set_dataset_status(database, "2000-1-R", dataset, availability, source="test")
        database.artifacts.insert_one({
            "_id": artifact_key("2000-1-R", dataset, {}), "session_id": "2000-1-R",
            "kind": dataset, "payload": {"availability": availability, "data": []},
        })
    set_dataset_status(
        database,
        "2000-1-R",
        "telemetry",
        "unavailable",
        source="test",
        schema_version=TELEMETRY_SCHEMA_VERSION,
    )
    for kind in ("drivers", "constructors"):
        database.standings.insert_one({
            "_id": f"2000:1:{kind}", "season": 2000, "round": 1,
            "kind": kind, "data": [{"position": 1}],
        })
    database.circuits.insert_one({
        "_id": "test", "map_reference_session": "catalog:test",
        "map_data": {"points": [{"X": 0, "Y": 0}, {"X": 1, "Y": 1}, {"X": 2, "Y": 0}]},
        "circuit_metadata": {"id": "test", "length": 1.0, "turns": 3, "type": "RACE"},
        "length_km": 1.0, "corner_count": 3, "circuit_type": "Race",
        "first_grand_prix": 2000,
    })

    result = audit(2000, 2000, deep=True)

    assert result["complete"] is True
    assert all(count == 0 for count in result["problem_counts"].values())


def test_audit_rejects_an_available_telemetry_status_without_rows():
    set_dataset_status(database, "2025-1-Q", "telemetry", "available", source="test")

    result = audit(2025, 2025)

    assert result["complete"] is False
    assert result["problems"]["telemetry_status_without_rows"] == ["2025-1-Q"]


def test_audit_rejects_partial_telemetry_lap_coverage():
    database.laps.insert_many([
        {
            "_id": "2025-1-Q:TST:1", "session_id": "2025-1-Q",
            "Driver": "TST", "LapNumber": 1,
        },
        {
            "_id": "2025-1-Q:TST:2", "session_id": "2025-1-Q",
            "Driver": "TST", "LapNumber": 2,
        },
    ])
    database.telemetry_laps.insert_one({
        "_id": "2025-1-Q:TST:1", "session_id": "2025-1-Q",
        "schema_version": TELEMETRY_SCHEMA_VERSION,
    })
    set_dataset_status(
        database, "2025-1-Q", "telemetry", "available", source="test",
        schema_version=TELEMETRY_SCHEMA_VERSION,
    )

    result = audit(2025, 2025)

    assert result["problems"]["telemetry_lap_count_errors"] == [
        "2025-1-Q:timing=2:telemetry=1",
    ]


def test_audit_rejects_wrong_telemetry_lap_identities():
    database.laps.insert_many([
        {
            "_id": "2025-1-Q:TST:1", "session_id": "2025-1-Q",
            "Driver": "TST", "LapNumber": 1,
        },
        {
            "_id": "2025-1-Q:TST:2", "session_id": "2025-1-Q",
            "Driver": "TST", "LapNumber": 2,
        },
    ])
    database.telemetry_laps.insert_many([
        {
            "_id": "2025-1-Q:TST:1", "session_id": "2025-1-Q",
            "driver": "TST", "lap": 1,
            "schema_version": TELEMETRY_SCHEMA_VERSION,
        },
        {
            "_id": "2025-1-Q:TST:3", "session_id": "2025-1-Q",
            "driver": "TST", "lap": 3,
            "schema_version": TELEMETRY_SCHEMA_VERSION,
        },
    ])
    set_dataset_status(
        database, "2025-1-Q", "telemetry", "available", source="test",
        schema_version=TELEMETRY_SCHEMA_VERSION,
    )

    result = audit(2025, 2025)

    assert result["problems"]["telemetry_lap_identity_errors"] == ["2025-1-Q"]


def test_audit_rejects_obsolete_v3_artifacts():
    database.artifacts.insert_one({
        "_id": "v3:2025-1-Q:results:obsolete",
        "session_id": "2025-1-Q",
        "kind": "results",
    })

    result = audit(2025, 2025)

    assert result["problems"]["obsolete_artifacts"] == [
        "v3:2025-1-Q:results:obsolete",
    ]


def test_audit_rejects_an_explicitly_unavailable_modern_core_dataset():
    database.sessions.insert_one({
        "_id": "2025-1-Q", "season": 2025, "round": 1, "code": "Q",
        "starts_at": "2025-03-01T14:00:00+00:00",
    })
    set_dataset_status(
        database, "2025-1-Q", "results", "unavailable", source="test",
        reason="Unexpected upstream gap.",
    )

    result = audit(2025, 2025)

    assert result["problems"]["unavailable_modern_session_datasets"] == [
        "2025-1-Q:results",
    ]


def test_audit_matches_canonical_artifact_rows_to_normalized_storage():
    database.sessions.insert_one({
        "_id": "2025-1-Q", "season": 2025, "round": 1, "code": "Q",
        "starts_at": "2025-03-01T14:00:00+00:00",
    })
    database.artifacts.insert_one({
        "_id": artifact_key("2025-1-Q", "results", {}),
        "session_id": "2025-1-Q",
        "kind": "results",
        "payload": {"availability": "available", "data": [{}, {}]},
    })
    database.results.insert_one({
        "_id": "2025-1-Q:TST", "session_id": "2025-1-Q",
    })

    result = audit(2025, 2025)

    assert result["problems"]["normalized_row_count_errors"] == [
        "2025-1-Q:results:artifact=2:stored=1",
    ]


def test_audit_rejects_an_event_without_a_canonical_circuit_link():
    database.events.insert_one({
        "_id": "2025-1", "season": 2025, "round": 1,
        "name": "Test Grand Prix",
    })

    result = audit(2025, 2025)

    assert result["problems"]["missing_event_circuit_links"] == ["2025-1"]


def test_audit_checks_formatting_in_normalized_collections():
    database.laps.insert_one({
        "_id": "2025-1-Q:TST:1", "session_id": "2025-1-Q",
        "Driver": "TST", "LapNumber": 1, "SpeedST": float("nan"),
    })

    result = audit(2025, 2025)

    assert "laps:2025-1-Q:TST:1:$.SpeedST" in result["problems"]["formatting_errors"]


def test_deep_audit_rejects_session_cumulative_telemetry_distance():
    points = [
        {"Time": 0, "Distance": 5_000.0},
        {"Time": 1_000, "Distance": 5_500.0},
    ]
    database.telemetry_laps.insert_one({
        "_id": "2025-1-Q:TST:2",
        "session_id": "2025-1-Q",
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "distance_normalized": True,
        "point_count": 2,
        "points_encoding": TELEMETRY_POINTS_ENCODING,
        "points_compressed": compress_telemetry_points(points),
        "car_point_count": 0,
        "car_points_encoding": TELEMETRY_POINTS_ENCODING,
        "car_points_compressed": compress_telemetry_points([]),
        "position_point_count": 0,
        "position_points_encoding": TELEMETRY_POINTS_ENCODING,
        "position_points_compressed": compress_telemetry_points([]),
    })

    result = audit(2025, 2025, deep=True)

    assert (
        "2025-1-Q:TST:2:merged:distance_not_lap_relative"
        in result["problems"]["telemetry_format_errors"]
    )
