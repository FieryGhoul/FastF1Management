from datetime import date, datetime, time as datetime_time, timedelta, timezone
import math
from typing import Any
import zlib

import numpy as np
import pandas as pd
from bson import BSON, Binary


def clean(value: Any) -> Any:
    if value is None or value is pd.NaT or value is pd.NA:
        return None
    # Pandas ``to_dict`` already emits native Python scalars for the large
    # majority of telemetry values.  Handle those before the more expensive
    # NumPy/Pandas checks; this is the hot path for millions of point fields.
    value_type = type(value)
    if value_type is str or value_type is int or value_type is bool:
        return value
    if value_type is float:
        return value if math.isfinite(value) else None
    if isinstance(value, (float, np.floating)) and not np.isfinite(value):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timedelta):
        return int(value.total_seconds() * 1000)
    if isinstance(value, timedelta):
        return int(value.total_seconds() * 1000)
    if isinstance(value, pd.Timestamp):
        if value.tzinfo is None:
            value = value.tz_localize("UTC")
        return value.isoformat()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, datetime_time):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [clean(v) for v in value]
    return value


def records(frame: pd.DataFrame, columns: list[str] | None = None) -> list[dict[str, Any]]:
    if columns:
        existing = [column for column in columns if column in frame.columns]
        frame = frame[existing]
    return [clean(row) for row in frame.to_dict(orient="records")]


TELEMETRY_POINTS_ENCODING = "zlib+bson-v1"
TELEMETRY_SCHEMA_VERSION = 4
COMPACT_CAR_CHANNELS = ("Time", "Speed", "RPM", "Throttle", "Brake", "Gear")
COMPACT_POSITION_CHANNELS = ("Time", "Distance")
# Level 3 keeps telemetry lossless while substantially reducing ingestion CPU
# versus zlib's level-6 default.  A representative 44 MB sample compressed
# 44% faster for only 3.4% more stored bytes, which is a better trade-off for
# the multi-year archive backfill.
TELEMETRY_COMPRESSION_LEVEL = 3


def normalize_telemetry_distance(
    points: list[dict[str, Any]],
    *,
    copy_points: bool = True,
) -> list[dict[str, Any]]:
    """Return a lap-relative merged telemetry stream.

    FastF1 computes ``Distance`` before a multi-lap driver stream is split.
    Without this normalization, lap two starts at roughly one lap length and
    later laps cannot be compared on a common x-axis.  Ingestion can opt into
    in-place normalization for a freshly-created list to avoid duplicating
    hundreds of thousands of point dictionaries; callers preserve their input
    by default.
    """
    normalized = [dict(point) for point in points] if copy_points else points
    baseline = None
    total = 0.0
    for item in normalized:
        distance = item.get("Distance")
        if (
            isinstance(distance, (int, float))
            and not isinstance(distance, bool)
            and math.isfinite(float(distance))
        ):
            value = float(distance)
            if baseline is None:
                baseline = value
            total = max(total, value - baseline)
    if baseline is None:
        return normalized
    for item in normalized:
        distance = item.get("Distance")
        if (
            isinstance(distance, (int, float))
            and not isinstance(distance, bool)
            and math.isfinite(float(distance))
        ):
            relative_distance = float(distance) - baseline
            item["Distance"] = relative_distance
            item["RelativeDistance"] = relative_distance / total if total > 0 else 0.0
    return normalized


def compress_telemetry_points(points: list[dict[str, Any]]) -> Binary:
    """Losslessly compact repeated telemetry fields before network/storage."""
    payload = BSON.encode({"points": points})
    return Binary(zlib.compress(payload, level=TELEMETRY_COMPRESSION_LEVEL))


def telemetry_points(
    document: dict[str, Any], stream: str | None = None,
) -> list[dict[str, Any]]:
    """Read both legacy plain points and losslessly compressed telemetry."""
    prefix = f"{stream}_" if stream else ""
    points = document.get(f"{prefix}points")
    if isinstance(points, list):
        return points
    payload = document.get(f"{prefix}points_compressed")
    if not payload or document.get(f"{prefix}points_encoding") != TELEMETRY_POINTS_ENCODING:
        return []
    try:
        decoded = BSON(bytes(zlib.decompress(bytes(payload)))).decode()
    except (TypeError, ValueError, zlib.error):
        return []
    return decoded.get("points", [])


def compact_car_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the compact car channels selected for durable storage."""
    compact = []
    for point in points:
        row = {}
        for channel in COMPACT_CAR_CHANNELS:
            source = "nGear" if channel == "Gear" else channel
            if source in point:
                row[channel] = point[source]
            elif channel in point:
                row[channel] = point[channel]
        if row:
            compact.append(row)
    return compact


def compact_distance_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep a lap-relative time/distance timeline for rebuilding chart traces."""
    normalized = normalize_telemetry_distance(points)
    return [
        {channel: point[channel] for channel in COMPACT_POSITION_CHANNELS if channel in point}
        for point in normalized
        if "Time" in point and "Distance" in point
    ]


def compact_merged_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select the compact chart channels from an already merged source."""
    normalized = normalize_telemetry_distance(points)
    compact = []
    for point in normalized:
        car_rows = compact_car_points([point])
        if not car_rows:
            continue
        row = car_rows[0]
        if "Distance" in point:
            row["Distance"] = point["Distance"]
        compact.append(row)
    return compact


def merged_telemetry_points(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Rebuild a compact merged trace from stored car and distance streams."""
    # Read schema-v3 documents during a rolling migration.
    legacy = telemetry_points(document)
    if legacy and (
        document.get("schema_version", 0) < 4
        or not telemetry_points(document, "car")
    ):
        return legacy
    car = telemetry_points(document, "car")
    distance = telemetry_points(document, "position")
    timed_distance = [
        (point.get("Time"), point.get("Distance"))
        for point in distance
        if isinstance(point.get("Time"), (int, float))
        and not isinstance(point.get("Time"), bool)
        and isinstance(point.get("Distance"), (int, float))
        and not isinstance(point.get("Distance"), bool)
    ]
    if not car:
        return distance
    if not timed_distance:
        return [dict(point) for point in car]
    times = np.asarray([item[0] for item in timed_distance], dtype=float)
    distances = np.asarray([item[1] for item in timed_distance], dtype=float)
    merged = []
    for point in car:
        row = dict(point)
        point_time = point.get("Time")
        if isinstance(point_time, (int, float)) and not isinstance(point_time, bool):
            row["Distance"] = float(np.interp(float(point_time), times, distances))
        merged.append(row)
    return merged
