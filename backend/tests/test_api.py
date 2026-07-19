from datetime import timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.mongo import database, utcnow
from app.serialization import TELEMETRY_POINTS_ENCODING, compress_telemetry_points


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


def test_admin_rejects_bad_credentials():
    with TestClient(app) as client:
        response = client.post("/api/v1/admin/login", json={"username": "admin", "password": "incorrect"})
    assert response.status_code == 401


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
    with TestClient(app) as client:
        response = client.get("/api/v1/sessions/2025-1-Q/telemetry?drivers=TST&channels=Speed,RPM")
    assert response.status_code == 200
    trace = response.json()["data"]["traces"][0]
    assert trace["points"] == points


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
