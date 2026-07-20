from datetime import datetime, time, timedelta, timezone

import numpy as np
import pandas as pd

from app.serialization import (
    clean,
    compact_car_points,
    compact_distance_points,
    compress_telemetry_points,
    merged_telemetry_points,
    normalize_telemetry_distance,
    records,
    TELEMETRY_POINTS_ENCODING,
    TELEMETRY_SCHEMA_VERSION,
)


def test_clean_converts_pandas_and_numpy_values():
    result = clean({
        "missing": np.nan,
        "timestamp": pd.Timestamp("2025-07-06T14:00:00Z"),
        "duration": pd.Timedelta(seconds=91.234),
        "integer": np.int64(7),
    })
    assert result == {
        "missing": None,
        "timestamp": "2025-07-06T14:00:00+00:00",
        "duration": 91234,
        "integer": 7,
    }


def test_records_only_includes_existing_columns():
    frame = pd.DataFrame([{"Driver": "VER", "LapTime": pd.Timedelta(seconds=80), "Ignored": 1}])
    assert records(frame, ["Driver", "LapTime", "Missing"]) == [{"Driver": "VER", "LapTime": 80000}]


def test_clean_removes_all_nonfinite_numbers():
    assert clean({"nan": float("nan"), "positive": float("inf"), "negative": float("-inf"), "pandas": pd.NA}) == {
        "nan": None, "positive": None, "negative": None, "pandas": None,
    }


def test_clean_preserves_native_telemetry_scalars():
    assert clean({
        "driver": "VER",
        "gear": 8,
        "brake": False,
        "speed": 325.5,
    }) == {
        "driver": "VER",
        "gear": 8,
        "brake": False,
        "speed": 325.5,
    }


def test_naive_datetime_is_serialized_as_utc():
    assert clean(datetime(2025, 1, 1)) == "2025-01-01T00:00:00+00:00"
    assert clean(timedelta(seconds=1.5)) == 1500
    assert clean(time(14, 5, 6)) == "14:05:06"


def test_telemetry_distance_is_normalized_per_lap():
    points = [
        {"Distance": 12_000.0, "RelativeDistance": 0.5, "Speed": 200},
        {"Distance": 12_500.0, "RelativeDistance": 0.6, "Speed": 220},
        {"Distance": 13_000.0, "RelativeDistance": 0.7, "Speed": 210},
    ]

    normalized = normalize_telemetry_distance(points)

    assert [point["Distance"] for point in normalized] == [0.0, 500.0, 1000.0]
    assert [point["RelativeDistance"] for point in normalized] == [0.0, 0.5, 1.0]
    assert points[0]["Distance"] == 12_000.0


def test_telemetry_distance_can_normalize_owned_points_in_place():
    points = [{"Distance": 500.0}, {"Distance": 750.0}]

    normalized = normalize_telemetry_distance(points, copy_points=False)

    assert normalized is points
    assert points == [
        {"Distance": 0.0, "RelativeDistance": 0.0},
        {"Distance": 250.0, "RelativeDistance": 1.0},
    ]


def test_compact_telemetry_keeps_only_selected_channels_and_rebuilds_distance():
    source = [
        {"Time": 0, "Distance": 5000.0, "Speed": 100, "RPM": 9000,
         "Throttle": 50, "Brake": False, "nGear": 4, "DRS": 1, "X": 10},
        {"Time": 1000, "Distance": 5250.0, "Speed": 110, "RPM": 9500,
         "Throttle": 60, "Brake": True, "nGear": 5, "DRS": 0, "X": 20},
    ]
    car = compact_car_points(source)
    position = compact_distance_points(source)
    document = {
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "car_points_encoding": TELEMETRY_POINTS_ENCODING,
        "car_points_compressed": compress_telemetry_points(car),
        "position_points_encoding": TELEMETRY_POINTS_ENCODING,
        "position_points_compressed": compress_telemetry_points(position),
    }

    assert set(car[0]) == {"Time", "Speed", "RPM", "Throttle", "Brake", "Gear"}
    assert car[0]["Gear"] == 4
    assert position == [
        {"Time": 0, "Distance": 0.0},
        {"Time": 1000, "Distance": 250.0},
    ]
    assert merged_telemetry_points(document)[1]["Distance"] == 250.0
