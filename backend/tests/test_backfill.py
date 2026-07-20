from app.backfill import ArchiveBackfill
from app.contracts import artifact_key
from app.mongo import database, set_dataset_status
from app.serialization import TELEMETRY_POINTS_ENCODING, TELEMETRY_SCHEMA_VERSION


def setup_function():
    for name in database.list_collection_names():
        database[name].delete_many({})


def test_completed_telemetry_requires_all_three_lossless_streams():
    session_id = "2025-1-R"
    set_dataset_status(
        database, session_id, "telemetry", "available", source="test",
        schema_version=TELEMETRY_SCHEMA_VERSION,
    )
    database.telemetry_laps.insert_one({
        "_id": f"{session_id}:TST:1",
        "session_id": session_id,
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "distance_normalized": True,
        "points_encoding": TELEMETRY_POINTS_ENCODING,
    })

    assert ArchiveBackfill.telemetry_recorded(session_id) is False

    database.telemetry_laps.update_one(
        {"_id": f"{session_id}:TST:1"},
        {"$set": {
            "car_points_encoding": TELEMETRY_POINTS_ENCODING,
            "position_points_encoding": TELEMETRY_POINTS_ENCODING,
        }},
    )
    assert ArchiveBackfill.telemetry_recorded(session_id) is True

    database.laps.insert_many([
        {
            "_id": f"{session_id}:TST:1", "session_id": session_id,
            "Driver": "TST", "LapNumber": 1,
        },
        {
            "_id": f"{session_id}:TST:2", "session_id": session_id,
            "Driver": "TST", "LapNumber": 2,
        },
    ])
    assert ArchiveBackfill.telemetry_recorded(session_id) is False


def test_archive_reuses_canonical_map_without_loading_session():
    database.circuits.insert_one({
        "_id": "test", "name": "Test Circuit", "country": "Testland",
        "locality": "Test", "map_data": {"points": [{"X": 1, "Y": 2}]},
    })
    database.events.insert_one({
        "_id": "2025-1", "country": "Testland", "location": "Test",
        "name": "Test Grand Prix",
    })
    database.sessions.insert_one({
        "_id": "2025-1-Q", "event_id": "2025-1", "season": 2025,
        "round": 1, "code": "Q", "starts_at": "2025-03-01T14:00:00+00:00",
    })
    for dataset in ("summary", "results", "laps", "strategy", "weather", "race-control"):
        set_dataset_status(
            database, "2025-1-Q", dataset, "available", source="test",
        )
        database.artifacts.insert_one({
            "_id": artifact_key("2025-1-Q", dataset, {}),
            "session_id": "2025-1-Q",
            "kind": dataset,
        })
    set_dataset_status(
        database,
        "2025-1-Q",
        "telemetry",
        "unavailable",
        source="test",
        reason="No published telemetry in fixture.",
        schema_version=TELEMETRY_SCHEMA_VERSION,
    )

    class NoLoadAdapter:
        @staticmethod
        def load_session(*_args, **_kwargs):
            raise AssertionError("canonical maps must not trigger another FastF1 load")

    backfill = ArchiveBackfill(2025, 2025)
    backfill.adapter = NoLoadAdapter()
    backfill.sync_session_data(list(database.sessions.find()))

    assert backfill.counts["skipped"] == 1


def test_archive_records_pre_timing_telemetry_as_explicitly_unavailable():
    database.sessions.insert_one({
        "_id": "2000-1-R", "season": 2000, "round": 1, "code": "R",
        "starts_at": "2000-03-01T14:00:00+00:00",
    })
    set_dataset_status(database, "2000-1-R", "summary", "available", source="test")

    class HistoricalAdapter:
        @staticmethod
        def session_telemetry_laps(_session_id):
            return []

    backfill = ArchiveBackfill(2000, 2000, include_telemetry=False)
    backfill.adapter = HistoricalAdapter()
    backfill.sync_session_data(list(database.sessions.find()))

    state = database.dataset_status.find_one({
        "subject": "2000-1-R", "dataset": "telemetry",
    })
    assert state["availability"] == "unavailable"
    assert state["unavailable_reason"] == "No telemetry laps were published for this session."
    assert state["schema_version"] == TELEMETRY_SCHEMA_VERSION


def test_archive_waits_for_explicit_timing_completion(monkeypatch):
    control_id = "timing_backfill:2018:2026"
    database.sync_controls.insert_one({
        "_id": control_id,
        "active": False,
        "phase": "session-data",
        "subject": "2025-18-Q",
    })
    sleeps = []

    def finish_after_wait(seconds):
        sleeps.append(seconds)
        database.sync_controls.update_one(
            {"_id": control_id},
            {"$set": {"phase": "completed", "subject": None}},
        )

    monkeypatch.setattr("app.backfill.time.sleep", finish_after_wait)

    ArchiveBackfill(2000, 2026).wait_for_modern_timing_runner()

    assert sleeps == [30]
    archive_control = database.sync_controls.find_one({
        "_id": "archive_backfill:2000:2026",
    })
    assert archive_control["phase"] == "waiting-for-modern-timing"
    assert archive_control["subject"] == "2025-18-Q"


def test_bulk_dump_replaces_older_historical_api_artifacts():
    database.sessions.insert_one({
        "_id": "2000-1-R", "season": 2000, "round": 1, "code": "R",
        "starts_at": "2000-03-01T14:00:00+00:00",
    })
    kinds = ("summary", "results", "laps", "strategy", "weather", "race-control")
    for kind in kinds:
        set_dataset_status(database, "2000-1-R", kind, "available", source="FastF1 Jolpica")
        database.artifacts.insert_one({
            "_id": artifact_key("2000-1-R", kind, {}),
            "session_id": "2000-1-R", "kind": kind,
            "payload": {"availability": "available", "data": [], "source": "FastF1 Jolpica"},
        })
    set_dataset_status(
        database, "2000-1-R", "telemetry", "unavailable", source="FastF1",
        schema_version=TELEMETRY_SCHEMA_VERSION,
    )

    class DumpAdapter:
        calls = 0

        def session_bundle(self, _session_id):
            self.calls += 1
            return {
                kind: {
                    "availability": "available" if kind == "summary" else "unavailable",
                    "data": {} if kind == "summary" else [],
                    "source": "Jolpica CSV database dump",
                }
                for kind in kinds
            }

        @staticmethod
        def artifact_key(session_id, kind, options):
            return artifact_key(session_id, kind, options)

    backfill = ArchiveBackfill(2000, 2000, include_telemetry=False)
    backfill.historical_bulk_ready = True
    backfill.adapter = DumpAdapter()
    backfill.sync_session_data(list(database.sessions.find()))

    assert backfill.adapter.calls == 1
    artifact = database.artifacts.find_one({
        "_id": artifact_key("2000-1-R", "summary", {}),
    })
    assert artifact["payload"]["source"] == "Jolpica CSV database dump"
