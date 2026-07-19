from fastapi.testclient import TestClient

from app.main import app


def test_health_and_season_contracts():
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        seasons = client.get("/api/v1/seasons")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert seasons.status_code == 200
    assert seasons.json()["telemetry_from"] == 2018
    assert 1950 in seasons.json()["data"]


def test_invalid_season_is_rejected_without_network_call():
    with TestClient(app) as client:
        response = client.get("/api/v1/calendar/1949")
    assert response.status_code == 422


def test_admin_rejects_bad_credentials():
    with TestClient(app) as client:
        response = client.post("/api/v1/admin/login", json={"username": "admin", "password": "incorrect"})
    assert response.status_code == 401

