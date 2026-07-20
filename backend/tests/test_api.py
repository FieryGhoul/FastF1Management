from datetime import timedelta

from fastapi.testclient import TestClient

from app.contracts import artifact_key
from app.main import _recent_session_rate, app
from app.mongo import database, set_dataset_status, utcnow
from app.serialization import (
    TELEMETRY_POINTS_ENCODING,
    TELEMETRY_SCHEMA_VERSION,
    compress_telemetry_points,
)


def setup_function():
    for name in database.list_collection_names():
        database[name].delete_many({})


def test_health_readiness_and_season_contracts():
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        ready = client.get("/api/v1/ready")
        seasons = client.get("/api/v1/seasons")
    assert health.status_code == 200
    assert ready.json()["database"] == "mongodb"
    assert seasons.json()["telemetry_from"] == 2018
    assert 1950 in seasons.json()["data"]


def test_recent_session_rate_ignores_idle_pause():
    newest = utcnow()
    completions = [
        {"last_synced_at": newest - timedelta(seconds=offset)}
        for offset in (0, 100, 200, 3_800, 3_900)
    ]

    rate, sample_size = _recent_session_rate(completions)

    assert rate == 36.0
    assert sample_size == 4


def test_recent_session_rate_uses_latest_event_sized_window():
    newest = utcnow()
    completions = [
        {"last_synced_at": newest - timedelta(seconds=offset)}
        for offset in (0, 60, 120, 180, 240, 300, 3_900)
    ]

    rate, sample_size = _recent_session_rate(completions)

    assert rate == 60.0
    assert sample_size == 6


def test_public_calendar_reads_mongodb_without_upstream_calls():
    database.events.insert_one({"_id": "2025-1", "id": "2025-1", "season": 2025, "round": 1, "name": "Test Grand Prix", "sessions": []})
    with TestClient(app) as client:
        response = client.get("/api/v1/calendar/2025")
    assert response.status_code == 200
    assert response.json()["data"][0]["name"] == "Test Grand Prix"
    assert response.json()["source"] == "MongoDB"


def test_future_session_is_scheduled_not_failed():
    database.sessions.insert_one({
        "_id": "2099-1-FP1", "id": "2099-1-FP1", "name": "Practice 1", "code": "FP1",
        "event_name": "Future Grand Prix", "country": "Test", "location": "Test",
        "starts_at": utcnow() + timedelta(days=1),
    })
    with TestClient(app) as client:
        response = client.get("/api/v1/sessions/2099-1-FP1/summary")
    assert response.status_code == 200
    assert response.json()["availability"] == "scheduled"


def test_historical_lap_artifact_is_returned_when_jolpica_data_exists():
    payload = {
        "availability": "available",
        "unavailable_reason": None,
        "data": [{"Driver": "MSC", "LapNumber": 1, "LapTime": 92_123}],
        "source": "FastF1 Jolpica",
    }
    database.artifacts.insert_one({
        "_id": artifact_key("2000-1-R", "laps", {}),
        "session_id": "2000-1-R",
        "kind": "laps",
        "payload": payload,
    })

    with TestClient(app) as client:
        response = client.get("/api/v1/sessions/2000-1-R/laps")

    assert response.status_code == 200
    assert response.json() == payload


def test_admin_rejects_bad_credentials():
    with TestClient(app) as client:
        response = client.post("/api/v1/admin/login", json={"username": "admin", "password": "incorrect"})
    assert response.status_code == 401


def test_admin_archive_reports_parallel_timing_runner():
    with TestClient(app) as client:
        database.sync_controls.insert_many([
            {
                "_id": "archive_backfill:2000:2026", "active": True,
                "phase": "standings", "subject": "2006:8:drivers", "updated_at": utcnow(),
            },
            {
                "_id": "timing_backfill:2018:2026", "active": True,
                "phase": "session-data", "subject": "2026-10-R", "updated_at": utcnow(),
                "position": 42, "total": 913, "counts": {"telemetry_sessions": 41},
            },
        ])
        set_dataset_status(
            database, "2025-1-R", "telemetry", "available", source="test",
            schema_version=TELEMETRY_SCHEMA_VERSION,
        )
        set_dataset_status(
            database, "2025-2-R", "telemetry", "awaiting_data", source="test",
            schema_version=TELEMETRY_SCHEMA_VERSION,
        )
        database.telemetry_laps.insert_many([
            {
                "_id": "2025-1-R:TST:1", "session_id": "2025-1-R",
                "schema_version": TELEMETRY_SCHEMA_VERSION,
                "car_points_encoding": TELEMETRY_POINTS_ENCODING,
                "position_points_encoding": TELEMETRY_POINTS_ENCODING,
            },
            {
                "_id": "2025-2-R:TST:1", "session_id": "2025-2-R",
                "schema_version": TELEMETRY_SCHEMA_VERSION,
                "car_points_encoding": TELEMETRY_POINTS_ENCODING,
                "position_points_encoding": TELEMETRY_POINTS_ENCODING,
            },
        ])
        login = client.post(
            "/api/v1/admin/login",
            json={"username": "admin", "password": "change-me"},
        )
        response = client.get("/api/v1/admin/archive")

    assert login.status_code == 200
    assert response.status_code == 200
    assert response.json()["active"] is True
    assert response.json()["timing"] == {
        "active": True,
        "phase": "session-data",
        "subject": "2026-10-R",
        "position": 42,
        "total": 913,
        "counts": {"telemetry_sessions": 41},
        "updated_at": response.json()["timing"]["updated_at"],
        "completed_at": None,
        "failures": 0,
        "recent_sessions_per_hour": None,
        "estimated_seconds_remaining": None,
        "rate_sample_size": 1,
    }
    assert response.json()["coverage"]["telemetry_sessions"] == 1
    assert response.json()["coverage"]["telemetry_laps"] == 1
    assert response.json()["coverage"]["raw_stream_laps"] == 1


def test_telemetry_endpoint_reads_losslessly_compressed_points():
    points = [
        {"Distance": 0.0, "Time": 0, "Speed": 120, "RPM": 9000},
        {"Distance": 10.0, "Time": 100, "Speed": 130, "RPM": 9500},
    ]
    database.telemetry_laps.insert_one({
        "_id": "2025-1-Q:TST:1.0", "session_id": "2025-1-Q", "driver": "TST",
        "lap": 1.0, "lap_time": 90_000,
        "points_compressed": compress_telemetry_points(points),
        "points_encoding": TELEMETRY_POINTS_ENCODING, "point_count": len(points),
    })
    set_dataset_status(
        database,
        "2025-1-Q",
        "telemetry",
        "available",
        source="test",
        schema_version=TELEMETRY_SCHEMA_VERSION,
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/sessions/2025-1-Q/telemetry?drivers=TST&channels=Speed,RPM")
    assert response.status_code == 200
    trace = response.json()["data"]["traces"][0]
    assert trace["points"] == points


def test_telemetry_endpoint_returns_every_channel_by_default():
    points = [{
        "Distance": 0.0, "Time": 0, "Speed": 120, "RPM": 9000,
        "Brake": False, "Source": "car", "CustomChannel": 42,
    }]
    database.telemetry_laps.insert_one({
        "_id": "2026-1-Q:TST:1", "session_id": "2026-1-Q", "driver": "TST",
        "lap": 1, "lap_time": 90_000,
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "points_compressed": compress_telemetry_points(points),
        "points_encoding": TELEMETRY_POINTS_ENCODING, "point_count": len(points),
    })
    set_dataset_status(
        database, "2026-1-Q", "telemetry", "available", source="test",
        schema_version=TELEMETRY_SCHEMA_VERSION,
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/sessions/2026-1-Q/telemetry?drivers=TST")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["channels"] == sorted(points[0])
    assert data["traces"][0]["points"] == points
    assert data["traces"][0]["point_count"] == 1
    assert data["traces"][0]["returned_point_count"] == 1


def test_session_driver_roster_returns_full_names_for_telemetry_dropdown():
    database.results.insert_many([
        {
            "session_id": "2026-1-R", "Abbreviation": "TST",
            "FullName": "Test Driver", "DriverNumber": "1", "TeamName": "Test Team",
        },
        {
            "session_id": "2026-1-R", "Abbreviation": "RES",
            "FullName": "Reserve Driver", "DriverNumber": "2", "TeamName": "Test Team",
        },
    ])
    database.telemetry_laps.insert_one({
        "_id": "2026-1-R:TST:1", "session_id": "2026-1-R",
        "driver": "TST", "lap": 1,
    })

    with TestClient(app) as client:
        response = client.get("/api/v1/sessions/2026-1-R/drivers")

    assert response.status_code == 200
    assert response.json()["data"] == [
        {
            "code": "RES", "full_name": "Reserve Driver", "driver_number": "2",
            "team_name": "Test Team", "telemetry_available": False,
        },
        {
            "code": "TST", "full_name": "Test Driver", "driver_number": "1",
            "team_name": "Test Team", "telemetry_available": True,
        },
    ]


def test_available_telemetry_marker_without_rows_uses_on_demand_repair(monkeypatch):
    class RepairCache:
        calls = []

        def get_or_schedule(self, _tasks, session_id, kind, options):
            self.calls.append((session_id, kind, options))
            return {
                "availability": "awaiting_data", "status": "queued",
                "unavailable_reason": "Repairing missing telemetry rows.", "data": None,
            }

    import app.main as main_module

    cache = RepairCache()
    monkeypatch.setattr(main_module, "on_demand_cache", cache)
    set_dataset_status(
        database, "2026-10-R", "telemetry", "available", source="test",
        schema_version=TELEMETRY_SCHEMA_VERSION,
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/sessions/2026-10-R/telemetry?channels=all")

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert cache.calls == [(
        "2026-10-R", "telemetry",
        {"drivers": "", "laps": "fastest", "channels": "", "stream": "merged"},
    )]


def test_telemetry_endpoint_exposes_original_car_stream():
    merged = [{"Distance": 0.0, "Time": 0, "Speed": 120}]
    car = [{"Time": 0, "Speed": 119, "Source": "car"}]
    position = [{"Time": 0, "X": 10, "Y": 20, "Source": "pos"}]
    database.telemetry_laps.insert_one({
        "_id": "2025-1-Q:TST:1.0",
        "session_id": "2025-1-Q",
        "driver": "TST",
        "lap": 1.0,
        "lap_time": 90_000,
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "points_compressed": compress_telemetry_points(merged),
        "points_encoding": TELEMETRY_POINTS_ENCODING,
        "point_count": len(merged),
        "car_points_compressed": compress_telemetry_points(car),
        "car_points_encoding": TELEMETRY_POINTS_ENCODING,
        "car_point_count": len(car),
        "position_points_compressed": compress_telemetry_points(position),
        "position_points_encoding": TELEMETRY_POINTS_ENCODING,
        "position_point_count": len(position),
    })
    set_dataset_status(
        database,
        "2025-1-Q",
        "telemetry",
        "available",
        source="test",
        schema_version=TELEMETRY_SCHEMA_VERSION,
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/sessions/2025-1-Q/telemetry?stream=car&channels=Speed,Source",
        )

    assert response.status_code == 200
    assert response.json()["data"]["stream"] == "car"
    assert response.json()["data"]["traces"][0]["points"] == car
    assert "Source" in response.json()["data"]["available_channels"]


def test_telemetry_endpoint_selects_a_lap_and_validates_the_selector():
    for lap, speed in ((1, 120), (2, 240)):
        points = [{"Distance": 0.0, "Time": 0, "Speed": speed}]
        database.telemetry_laps.insert_one({
            "_id": f"2025-1-Q:TST:{lap}",
            "session_id": "2025-1-Q",
            "driver": "TST",
            "lap": lap,
            "lap_time": 90_000 + lap,
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "points_compressed": compress_telemetry_points(points),
            "points_encoding": TELEMETRY_POINTS_ENCODING,
            "point_count": len(points),
        })
    set_dataset_status(
        database,
        "2025-1-Q",
        "telemetry",
        "available",
        source="test",
        schema_version=TELEMETRY_SCHEMA_VERSION,
    )

    with TestClient(app) as client:
        selected = client.get(
            "/api/v1/sessions/2025-1-Q/telemetry?drivers=TST&laps=2&channels=Speed",
        )
        missing = client.get(
            "/api/v1/sessions/2025-1-Q/telemetry?drivers=TST&laps=99&channels=Speed",
        )
        invalid = client.get(
            "/api/v1/sessions/2025-1-Q/telemetry?drivers=TST&laps=two",
        )

    assert selected.status_code == 200
    assert selected.json()["data"]["traces"][0]["lap"] == 2
    assert selected.json()["data"]["traces"][0]["points"][0]["Speed"] == 240
    assert missing.status_code == 200
    assert missing.json()["availability"] == "unavailable"
    assert invalid.status_code == 422


def test_telemetry_endpoint_hides_partial_streamed_session():
    database.telemetry_laps.insert_one({
        "_id": "2025-1-Q:TST:1.0", "session_id": "2025-1-Q", "driver": "TST",
        "lap": 1.0, "lap_time": 90_000, "points_compressed": "partial",
        "points_encoding": TELEMETRY_POINTS_ENCODING, "point_count": 1,
    })

    with TestClient(app) as client:
        response = client.get("/api/v1/sessions/2025-1-Q/telemetry")

    assert response.status_code == 200
    assert response.json()["availability"] == "awaiting_data"


def test_missing_circuit_map_queues_a_priority_reference_job():
    database.circuits.insert_one({
        "_id": "test", "slug": "test", "name": "Silverstone Circuit",
        "country": "UK", "locality": "Silverstone",
    })
    database.sessions.insert_one({
        "_id": "2025-1-Q", "season": 2025, "round": 1, "code": "Q",
        "country": "United Kingdom", "location": "Silverstone", "event_name": "British Grand Prix",
        "starts_at": utcnow() - timedelta(days=1),
    })
    with TestClient(app) as client:
        response = client.get("/api/v1/circuits/test/map")
    assert response.status_code == 200
    assert response.json()["availability"] == "awaiting_data"
    assert response.json()["status"] == "queued"
    assert database.jobs.find_one({"key": "track:2025-1-Q"})["priority"] == 100


def test_circuit_index_does_not_duplicate_heavy_map_points():
    database.circuits.insert_one({
        "_id": "test", "slug": "test", "name": "Test Circuit",
        "country": "Testland", "map_data": {"points": [{"X": 1, "Y": 2}]},
    })
    with TestClient(app) as client:
        index = client.get("/api/v1/circuits")
        index_with_maps = client.get("/api/v1/circuits?include_maps=true")
        detail = client.get("/api/v1/circuits/test")
        map_response = client.get("/api/v1/circuits/test/map")

    assert "map_data" not in index.json()["data"][0]
    assert index_with_maps.json()["data"][0]["map_data"]["points"] == [{"X": 1, "Y": 2}]
    assert "map_data" not in detail.json()["data"]
    assert map_response.json()["data"]["points"] == [{"X": 1, "Y": 2}]


def test_circuit_detail_includes_linked_event_and_session_history():
    database.circuits.insert_one({
        "_id": "test", "slug": "test", "name": "Test Circuit",
        "country": "Testland",
    })
    database.events.insert_one({
        "_id": "2025-1", "id": "2025-1", "season": 2025, "round": 1,
        "name": "Test Grand Prix", "country": "Testland", "location": "Test",
        "circuit_slug": "test",
        "sessions": [
            {"id": "2025-1-Q", "name": "Qualifying", "code": "Q"},
            {"id": "2025-1-R", "name": "Race", "code": "R"},
        ],
    })

    with TestClient(app) as client:
        response = client.get("/api/v1/circuits/test")

    assert response.status_code == 200
    assert response.json()["data"]["event_count"] == 1
    assert response.json()["data"]["session_count"] == 2
    assert response.json()["data"]["events"][0]["name"] == "Test Grand Prix"
