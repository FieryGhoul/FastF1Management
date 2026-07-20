import pytest

from app.backfill import ArchiveBackfill
from app.circuit_matching import circuit_match_score, country_variants
from app.contracts import artifact_key as contract_artifact_key
from app.ingestion import (
    migrate_telemetry_schema,
    persist_session_bundle,
    persist_telemetry,
    persist_track,
    sync_season,
)
from app.mongo import database, set_dataset_status
from app.serialization import (
    TELEMETRY_POINTS_ENCODING,
    TELEMETRY_SCHEMA_VERSION,
    compress_telemetry_points,
    merged_telemetry_points,
    telemetry_points,
)


class FakeAdapter:
    def schedule(self, year):
        return [{
            "id": f"{year}-1", "season": year, "round": 1, "name": "Test Grand Prix",
            "official_name": "Test", "country": "Testland", "location": "Test Circuit",
            "event_date": f"{year}-03-01T00:00:00+00:00", "format": "conventional",
            "f1_api_support": True,
            "sessions": [{"id": f"{year}-1-R", "name": "Race", "code": "R", "starts_at": f"{year}-03-01T14:00:00+00:00"}],
        }]

    def drivers(self, year):
        return [
            {"driverId": "tester", "driverCode": "TST"},
            {"driverId": "reserve", "givenName": "Reserve", "familyName": "Placeholder"},
        ]

    def constructors(self, year):
        return [{"constructorId": "test", "constructorName": "Test Team"}]

    def standings(self, year, kind):
        return [{"position": 1, "points": 25}]

    def circuits(self, year=None):
        return [{"circuitId": "test", "circuitName": "Test Circuit", "country": "Testland", "locality": "Test"}]

    def session_bundle(self, session_id):
        return {
            "summary": {"availability": "available", "data": {"name": "Race"}, "source": "FastF1"},
            "results": {"availability": "available", "data": [{"session_id": "upstream-42", "DriverNumber": "1", "Abbreviation": "TST", "Position": 1}], "source": "FastF1"},
            "laps": {"availability": "available", "data": [{"Driver": "tester", "DriverId": "tester", "LapNumber": 1, "LapTime": 90_000}], "source": "FastF1"},
        }

    @staticmethod
    def artifact_key(session_id, kind, options):
        return contract_artifact_key(session_id, kind, options)


def setup_function():
    for name in database.list_collection_names():
        database[name].delete_many({})


def test_circuit_matching_accepts_an_exact_locality_with_a_different_event_name():
    circuit = {"name": "Autodromo Enzo e Dino Ferrari", "locality": "Imola"}
    assert circuit_match_score(circuit, "Emilia Romagna Grand Prix Imola") >= 55


def test_abu_dhabi_country_alias_resolves_to_the_uae_circuit():
    assert "UAE" in country_variants("Abu Dhabi")


def test_season_and_session_data_are_normalized_into_mongodb():
    adapter = FakeAdapter()
    database.sessions.insert_one({
        "_id": "2025-1-R",
        "event_id": "2025-1",
        "season": 2025,
        "round": 1,
        "code": "R",
        "status": "processed",
        "last_synced_at": "kept",
    })
    database.drivers.insert_one({
        "_id": "2025:obsolete", "season": 2025, "driverId": "obsolete",
    })
    counts = sync_season(database, adapter, 2025)
    indexed_session = database.sessions.find_one({"_id": "2025-1-R"})
    assert indexed_session["status"] == "processed"
    assert indexed_session["last_synced_at"] == "kept"
    database.artifacts.insert_one({
        "_id": "v3:2025-1-R:results:obsolete",
        "session_id": "2025-1-R",
        "kind": "results",
        "payload": {"data": []},
    })
    persist_session_bundle(database, adapter, "2025-1-R")
    assert counts["events"] == 1
    assert counts["drivers"] == 2
    assert database.events.count_documents({"season": 2025}) == 1
    assert database.sessions.count_documents({"season": 2025}) == 1
    assert database.events.find_one({"_id": "2025-1"})["circuit_slug"] == "test"
    assert indexed_session["circuit_slug"] == "test"
    assert database.results.count_documents({"session_id": "2025-1-R"}) == 1
    result = database.results.find_one({"session_id": "2025-1-R"})
    assert result["source_session_id"] == "upstream-42"
    assert database.laps.count_documents({"session_id": "2025-1-R"}) == 1
    assert database.laps.find_one({"session_id": "2025-1-R"})["Driver"] == "TST"
    assert database.drivers.find_one({"_id": "2025:obsolete"}) is None
    reserve = database.drivers.find_one({"_id": "2025:reserve"})
    assert reserve["driverRole"] == "reserve"
    assert reserve["isReserve"] is True
    assert database.artifacts.count_documents({"_id": {"$regex": "^v3:"}}) == 0


def test_track_ingestion_reuses_a_canonical_map_without_loading_fastf1():
    database.circuits.insert_one({
        "_id": "test", "name": "Test Circuit", "country": "Testland", "locality": "Test",
        "map_data": {"points": [{"X": 1, "Y": 2}], "rotation": 0},
    })
    database.events.insert_one({
        "_id": "2025-1", "country": "Testland", "location": "Test", "name": "Test Grand Prix",
    })
    database.sessions.insert_one({"_id": "2025-1-Q", "event_id": "2025-1"})

    class NoLoadAdapter:
        @staticmethod
        def artifact_key(session_id, kind, options):
            return contract_artifact_key(session_id, kind, options)

        @staticmethod
        def session_artifact(*_):
            raise AssertionError("FastF1 should not load when the canonical map already exists")

    result = persist_track(database, NoLoadAdapter(), "2025-1-Q")
    assert result["data"]["points"] == [{"X": 1, "Y": 2}]
    assert result["source"] == "MongoDB canonical circuit map"


def test_telemetry_ingestion_streams_compressed_laps():
    class StreamingAdapter:
        @staticmethod
        def iter_session_telemetry_laps(_session_id):
            for lap in range(1, 52):
                yield {
                    "session_id": "2025-1-R",
                    "driver": "TST",
                    "lap": lap,
                    "points": [{"Time": 0, "Distance": 0.0, "Speed": 100 + lap}],
                    "car_points": [{"Time": 0, "Speed": 100 + lap}],
                    "position_points": [{"Time": 0, "X": lap, "Y": lap}],
                }

        @staticmethod
        def session_telemetry_laps(_session_id):
            raise AssertionError("streaming ingestion must not materialize every lap")

    count = persist_telemetry(database, StreamingAdapter(), "2025-1-R")

    assert count == 51
    assert database.telemetry_laps.count_documents({"session_id": "2025-1-R"}) == 51
    telemetry = database.telemetry_laps.find_one({"session_id": "2025-1-R"})
    assert "points_compressed" not in telemetry
    assert telemetry["car_points_compressed"]
    assert telemetry["position_points_compressed"]
    assert telemetry["schema_version"] == TELEMETRY_SCHEMA_VERSION
    assert telemetry["distance_normalized"] is True
    assert telemetry["car_point_count"] == 1
    assert telemetry["position_point_count"] == 1
    assert telemetry_points(telemetry, "car") == [{"Time": 0, "Speed": 101}]
    assert telemetry_points(telemetry, "position") == [{"Time": 0, "Distance": 0.0}]
    assert database.dataset_status.find_one({
        "subject": "2025-1-R", "dataset": "telemetry",
    })["availability"] == "available"


def test_persistent_telemetry_rejects_non_race_sessions():
    class AdapterMustNotRun:
        @staticmethod
        def iter_session_telemetry_laps(_session_id):
            raise AssertionError("non-race telemetry must remain on demand")

    with pytest.raises(ValueError, match="restricted to race sessions"):
        persist_telemetry(database, AdapterMustNotRun(), "2026-1-Q")

    assert database.telemetry_laps.count_documents({}) == 0
    assert database.dataset_status.count_documents({"dataset": "telemetry"}) == 0


def test_interrupted_core_ingestion_cannot_leave_a_completed_marker():
    set_dataset_status(
        database, "2025-1-R", "results", "available", source="old",
    )

    class InterruptedAdapter:
        @staticmethod
        def session_bundle(_session_id):
            return {
                "results": {
                    "availability": "available",
                    "data": [{"DriverNumber": "1", "Abbreviation": "TST"}],
                    "source": "FastF1",
                },
            }

        @staticmethod
        def artifact_key(*_args):
            raise RuntimeError("process interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        persist_session_bundle(database, InterruptedAdapter(), "2025-1-R")

    state = database.dataset_status.find_one({
        "subject": "2025-1-R", "dataset": "results",
    })
    assert state["availability"] == "awaiting_data"


def test_interrupted_telemetry_ingestion_cannot_leave_a_completed_marker():
    class InterruptedAdapter:
        @staticmethod
        def iter_session_telemetry_laps(_session_id):
            yield {
                "session_id": "2025-1-R",
                "driver": "TST",
                "lap": 1,
                "points": [{"Time": 0, "Distance": 0.0}],
                "car_points": [{"Time": 0, "Speed": 100}],
                "position_points": [{"Time": 0, "X": 1, "Y": 2}],
            }
            raise RuntimeError("process interrupted")

    set_dataset_status(
        database, "2025-1-R", "telemetry", "available", source="old",
        schema_version=TELEMETRY_SCHEMA_VERSION,
    )

    with pytest.raises(RuntimeError, match="interrupted"):
        persist_telemetry(database, InterruptedAdapter(), "2025-1-R")

    state = database.dataset_status.find_one({
        "subject": "2025-1-R", "dataset": "telemetry",
    })
    assert state["availability"] == "awaiting_data"
    assert ArchiveBackfill.telemetry_recorded("2025-1-R") is False


def test_telemetry_ingestion_rejects_a_partial_lap_set():
    database.laps.insert_many([
        {
            "_id": "2025-1-R:TST:1", "session_id": "2025-1-R",
            "Driver": "TST", "LapNumber": 1,
        },
        {
            "_id": "2025-1-R:TST:2", "session_id": "2025-1-R",
            "Driver": "TST", "LapNumber": 2,
        },
    ])

    class PartialAdapter:
        @staticmethod
        def iter_session_telemetry_laps(_session_id):
            yield {
                "session_id": "2025-1-R",
                "driver": "TST",
                "lap": 1,
                "points": [{"Time": 0, "Distance": 0.0}],
                "car_points": [{"Time": 0, "Speed": 100}],
                "position_points": [{"Time": 0, "X": 1, "Y": 2}],
            }

    with pytest.raises(RuntimeError, match="expected 2 timing laps, stored 1"):
        persist_telemetry(database, PartialAdapter(), "2025-1-R")

    assert database.dataset_status.find_one({
        "subject": "2025-1-R", "dataset": "telemetry",
    })["availability"] == "awaiting_data"


def test_telemetry_ingestion_rejects_wrong_driver_lap_identities():
    database.laps.insert_many([
        {
            "_id": "2025-1-R:TST:1", "session_id": "2025-1-R",
            "Driver": "TST", "LapNumber": 1,
        },
        {
            "_id": "2025-1-R:TST:2", "session_id": "2025-1-R",
            "Driver": "TST", "LapNumber": 2,
        },
    ])

    class WrongLapAdapter:
        @staticmethod
        def iter_session_telemetry_laps(_session_id):
            for lap in (1, 3):
                yield {
                    "session_id": "2025-1-R",
                    "driver": "TST",
                    "lap": lap,
                    "points": [{"Time": 0, "Distance": 0.0}],
                    "car_points": [{"Time": 0, "Speed": 100}],
                    "position_points": [{"Time": 0, "X": 1, "Y": 2}],
                }

    with pytest.raises(RuntimeError, match="identity mismatch"):
        persist_telemetry(database, WrongLapAdapter(), "2025-1-R")

    assert database.dataset_status.find_one({
        "subject": "2025-1-R", "dataset": "telemetry",
    })["availability"] == "awaiting_data"


def test_telemetry_migration_compacts_streams_and_removes_merged_blob():
    merged = [
        {"Time": 0, "Distance": 5_000.0, "RelativeDistance": 0.4},
        {"Time": 1_000, "Distance": 5_500.0, "RelativeDistance": 0.5},
    ]
    raw_car = [{"Time": 0, "Speed": 200}]
    database.telemetry_laps.insert_one({
        "_id": "2025-1-R:TST:2",
        "session_id": "2025-1-R",
        "schema_version": 2,
        "point_count": 2,
        "points_encoding": TELEMETRY_POINTS_ENCODING,
        "points_compressed": compress_telemetry_points(merged),
        "car_point_count": 1,
        "car_points_encoding": TELEMETRY_POINTS_ENCODING,
        "car_points_compressed": compress_telemetry_points(raw_car),
    })
    set_dataset_status(
        database, "2025-1-R", "telemetry", "available", source="test",
        schema_version=2,
    )

    result = migrate_telemetry_schema(database)
    document = database.telemetry_laps.find_one({"_id": "2025-1-R:TST:2"})

    assert result == {"migrated": 1, "failed": 0, "sessions": 1}
    assert "points_compressed" not in document
    assert [point["Distance"] for point in telemetry_points(document, "position")] == [0.0, 500.0]
    assert telemetry_points(document, "car") == raw_car
    assert [point["Distance"] for point in merged_telemetry_points(document)] == [0.0]
    assert document["schema_version"] == TELEMETRY_SCHEMA_VERSION
    assert database.dataset_status.find_one({
        "subject": "2025-1-R", "dataset": "telemetry",
    })["schema_version"] == TELEMETRY_SCHEMA_VERSION
