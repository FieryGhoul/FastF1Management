from app.mongo import database, set_dataset_status
from app.telemetry_retention import prune_non_race_telemetry


def setup_function():
    for name in database.list_collection_names():
        database[name].delete_many({})


def test_retention_preview_and_apply_keep_only_race_telemetry():
    database.telemetry_laps.insert_many([
        {"_id": "2026-1-R:TST:1", "session_id": "2026-1-R"},
        {"_id": "2026-1-Q:TST:1", "session_id": "2026-1-Q"},
        {"_id": "2026-1-FP1:TST:1", "session_id": "2026-1-FP1"},
    ])
    for session_id in ("2026-1-R", "2026-1-Q", "2026-1-FP1"):
        set_dataset_status(
            database, session_id, "telemetry", "available", source="test",
        )

    preview = prune_non_race_telemetry(database)
    assert preview == {
        "applied": False,
        "non_race_sessions": 2,
        "telemetry_laps": 2,
        "telemetry_statuses": 2,
    }
    assert database.telemetry_laps.count_documents({}) == 3

    applied = prune_non_race_telemetry(database, apply=True)
    assert applied["deleted_telemetry_laps"] == 2
    assert applied["deleted_telemetry_statuses"] == 2
    assert database.telemetry_laps.distinct("session_id") == ["2026-1-R"]
    assert database.dataset_status.distinct(
        "subject", {"dataset": "telemetry"},
    ) == ["2026-1-R"]
