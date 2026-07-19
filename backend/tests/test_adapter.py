import pandas as pd

from app.fastf1_adapter import FastF1Adapter, slugify


def test_slugify_is_stable():
    assert slugify("Autodromo Jose Carlos Pace") == "autodromo-jose-carlos-pace"


def test_session_id_parser():
    assert FastF1Adapter.parse_session_id("2025-12-R") == (2025, 12, "R")


def test_artifact_keys_change_with_options():
    first = FastF1Adapter.artifact_key("2025-1-Q", "telemetry", {"drivers": "VER"})
    second = FastF1Adapter.artifact_key("2025-1-Q", "telemetry", {"drivers": "NOR"})
    assert first != second
    assert first.startswith("v3:")
    assert FastF1Adapter.bundle_key("2025-1-Q") == "v3:2025-1-Q:core-bundle"


def test_historical_results_are_normalized_for_the_frontend():
    frame = pd.DataFrame([{
        "position": 1, "grid": 2, "driverCode": "FAR", "number": 2,
        "givenName": "Nino", "familyName": "Farina", "constructorName": "Alfa Romeo",
        "points": 9.0, "status": "Finished", "totalRaceTime": pd.Timedelta(minutes=120),
    }])
    row = FastF1Adapter._historical_results(frame)[0]
    assert row["Abbreviation"] == "FAR"
    assert row["FullName"] == "Nino Farina"
    assert row["Time"] == 7_200_000
