from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd


def clean(value: Any) -> Any:
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, (float, np.floating)) and np.isnan(value):
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

