from app.circuit_matching import circuit_match_score
from app.ingestion import persist_session_bundle, persist_track, sync_season
from app.mongo import database


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
        return [{"driverId": "tester", "driverCode": "TST"}]

    def constructors(self, year):
        return [{"constructorId": "test", "constructorName": "Test Team"}]

    def standings(self, year, kind):
        return [{"position": 1, "points": 25}]

    def circuits(self, year=None):
        return [{"circuitId": "test", "circuitName": "Test Circuit", "country": "Testland", "locality": "Test"}]

    def session_bundle(self, session_id):
        return {
            "summary": {"availability": "available", "data": {"name": "Race"}, "source": "FastF1"},
            "results": {"availability": "available", "data": [{"DriverNumber": "1", "Abbreviation": "TST", "Position": 1}], "source": "FastF1"},
            "laps": {"availability": "available", "data": [{"Driver": "TST", "LapNumber": 1, "LapTime": 90_000}], "source": "FastF1"},
        }

    @staticmethod
    def artifact_key(session_id, kind, options):
        return f"v3:{session_id}:{kind}:test"


def setup_function():
    for name in database.list_collection_names():
        database[name].delete_many({})


def test_circuit_matching_accepts_an_exact_locality_with_a_different_event_name():
    circuit = {"name": "Autodromo Enzo e Dino Ferrari", "locality": "Imola"}
    assert circuit_match_score(circuit, "Emilia Romagna Grand Prix Imola") >= 55


def test_season_and_session_data_are_normalized_into_mongodb():
    adapter = FakeAdapter()
    counts = sync_season(database, adapter, 2025)
    persist_session_bundle(database, adapter, "2025-1-R")
    assert counts["events"] == 1
    assert database.events.count_documents({"season": 2025}) == 1
    assert database.sessions.count_documents({"season": 2025}) == 1
    assert database.results.count_documents({"session_id": "2025-1-R"}) == 1
    assert database.laps.count_documents({"session_id": "2025-1-R"}) == 1


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
            return f"v3:{session_id}:{kind}:test"

        @staticmethod
        def session_artifact(*_):
            raise AssertionError("FastF1 should not load when the canonical map already exists")

    result = persist_track(database, NoLoadAdapter(), "2025-1-Q")
    assert result["data"]["points"] == [{"X": 1, "Y": 2}]
    assert result["source"] == "MongoDB canonical circuit map"
