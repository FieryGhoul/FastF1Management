from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app.serialization import clean, records


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


def test_naive_datetime_is_serialized_as_utc():
    assert clean(datetime(2025, 1, 1)) == "2025-01-01T00:00:00+00:00"
    assert clean(timedelta(seconds=1.5)) == 1500

