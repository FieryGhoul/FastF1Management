from app.mongo import database, set_dataset_status
from app.timing_backfill import CORE_DATASETS, ModernTimingBackfill
from app.contracts import artifact_key
from app.serialization import TELEMETRY_SCHEMA_VERSION


def setup_function():
    for name in database.list_collection_names():
        database[name].delete_many({})


def test_timing_coverage_requires_every_core_dataset_and_telemetry_status():
    session = {
        "_id": "2025-1-R",
        "season": 2025,
        "round": 1,
        "code": "R",
    }
    database.sessions.insert_one(session)
    for dataset in CORE_DATASETS:
        set_dataset_status(database, session["_id"], dataset, "available", source="test")
        database.artifacts.insert_one({
            "_id": artifact_key(session["_id"], dataset, {}),
            "session_id": session["_id"],
            "kind": dataset,
        })

    assert ModernTimingBackfill.coverage_gaps([session]) == ["2025-1-R:telemetry"]

    set_dataset_status(
        database,
        session["_id"],
        "telemetry",
        "unavailable",
        source="test",
        reason="No published timing data.",
        schema_version=TELEMETRY_SCHEMA_VERSION,
    )
    assert ModernTimingBackfill.coverage_gaps([session]) == []

    set_dataset_status(
        database,
        session["_id"],
        "telemetry",
        "awaiting_data",
        source="test",
        reason="Interrupted import.",
        schema_version=TELEMETRY_SCHEMA_VERSION,
    )
    assert ModernTimingBackfill.coverage_gaps([session]) == ["2025-1-R:telemetry"]


def test_timing_coverage_rejects_unavailable_core_dataset():
    session = {
        "_id": "2025-1-Q",
        "season": 2025,
        "round": 1,
        "code": "Q",
    }
    for dataset in CORE_DATASETS:
        set_dataset_status(database, session["_id"], dataset, "available", source="test")
        database.artifacts.insert_one({
            "_id": artifact_key(session["_id"], dataset, {}),
            "session_id": session["_id"],
            "kind": dataset,
        })
    set_dataset_status(database, session["_id"], "results", "unavailable", source="test")
    set_dataset_status(
        database,
        session["_id"],
        "telemetry",
        "unavailable",
        source="test",
        reason="No published timing data.",
        schema_version=TELEMETRY_SCHEMA_VERSION,
    )

    assert ModernTimingBackfill.coverage_gaps([session]) == ["2025-1-Q:results"]


def test_timing_coverage_does_not_require_qualifying_telemetry():
    session = {
        "_id": "2025-1-Q", "season": 2025, "round": 1, "code": "Q",
    }
    for dataset in CORE_DATASETS:
        set_dataset_status(database, session["_id"], dataset, "available", source="test")
        database.artifacts.insert_one({
            "_id": artifact_key(session["_id"], dataset, {}),
            "session_id": session["_id"], "kind": dataset,
        })

    assert ModernTimingBackfill.coverage_gaps([session]) == []
