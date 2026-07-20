import pandas as pd
import pytest
from types import SimpleNamespace

from app import fastf1_adapter as adapter_module
from app.fastf1_adapter import FastF1Adapter, slugify


def test_slugify_is_stable():
    assert slugify("Autodromo Jose Carlos Pace") == "autodromo-jose-carlos-pace"


def test_session_id_parser():
    assert FastF1Adapter.parse_session_id("2025-12-R") == (2025, 12, "R")


def test_schedule_preserves_complete_upstream_row(monkeypatch):
    frame = pd.DataFrame([{
        "RoundNumber": 1,
        "Country": "Testland",
        "Location": "Test",
        "OfficialEventName": "Official Test Grand Prix",
        "EventDate": pd.Timestamp("2025-03-02", tz="UTC"),
        "EventName": "Test Grand Prix",
        "EventFormat": "conventional",
        "Session1": "Practice 1",
        "Session1DateUtc": pd.Timestamp("2025-02-28T10:00:00Z"),
        "F1ApiSupport": True,
        "FutureScheduleField": "kept",
    }])
    monkeypatch.setattr(adapter_module.fastf1, "get_event_schedule", lambda *_args, **_kwargs: frame)
    adapter = FastF1Adapter.__new__(FastF1Adapter)

    event = adapter.schedule(2025)[0]

    assert event["schedule_data"]["FutureScheduleField"] == "kept"
    assert event["schedule_data"]["OfficialEventName"] == "Official Test Grand Prix"


def test_artifact_keys_change_with_options():
    first = FastF1Adapter.artifact_key("2025-1-Q", "telemetry", {"drivers": "VER"})
    second = FastF1Adapter.artifact_key("2025-1-Q", "telemetry", {"drivers": "NOR"})
    assert first != second
    assert first.startswith("v4:")
    assert FastF1Adapter.bundle_key("2025-1-Q") == "v4:2025-1-Q:core-bundle"


def test_historical_results_are_normalized_for_the_frontend():
    frame = pd.DataFrame([{
        "position": 1, "grid": 2, "driverCode": "FAR", "number": 2,
        "givenName": "Nino", "familyName": "Farina", "constructorName": "Alfa Romeo",
        "points": 9.0, "status": "Finished", "totalRaceTime": pd.Timedelta(minutes=120),
        "fastestLapRank": 1,
    }])
    row = FastF1Adapter._historical_results(frame)[0]
    assert row["Abbreviation"] == "FAR"
    assert row["FullName"] == "Nino Farina"
    assert row["Time"] == 7_200_000
    assert row["fastestLapRank"] == 1


def test_historical_race_bundle_keeps_jolpica_laps_and_pit_stops():
    description = pd.DataFrame([{
        "raceName": "Test Grand Prix", "raceDate": pd.Timestamp("2017-11-26"),
        "country": "Testland", "locality": "Test", "circuitName": "Test Circuit",
    }])

    def response(frame):
        return SimpleNamespace(description=description, content=[pd.DataFrame(frame)])

    class Ergast:
        @staticmethod
        def get_race_results(**_kwargs):
            return response([{
                "position": 1, "grid": 1, "driverCode": "TST",
                "driverId": "tester", "laps": 55,
            }])

        get_qualifying_results = get_race_results
        get_sprint_results = get_race_results

        @staticmethod
        def get_lap_times(**_kwargs):
            return response([{
                "number": 1, "driverId": "tester", "position": 1,
                "time": pd.Timedelta(seconds=90),
            }])

        @staticmethod
        def get_pit_stops(**_kwargs):
            return response([{
                "driverId": "tester", "stop": 1, "lap": 20,
                "time": pd.Timestamp("2017-11-26T14:30:00Z").time(),
                "duration": pd.Timedelta(seconds=2.4),
            }])

    adapter = FastF1Adapter.__new__(FastF1Adapter)
    adapter.ergast = Ergast()
    adapter._wait_for_ergast = lambda: None

    bundle = adapter._historical_bundle(2017, 20, "R")

    assert bundle["laps"]["availability"] == "available"
    assert bundle["laps"]["data"][0]["LapTime"] == 90_000
    assert bundle["laps"]["data"][0]["IsAccurate"] is True
    assert bundle["strategy"]["availability"] == "available"
    assert bundle["strategy"]["data"][0]["Duration"] == 2_400
    assert bundle["strategy"]["data"][0]["PitTime"] == "14:30:00"


def test_verified_dump_records_missing_historical_practice_without_api_call():
    class Dump:
        source_available = True

        @staticmethod
        def session_bundle(*_args):
            return None

    adapter = FastF1Adapter.__new__(FastF1Adapter)
    adapter.historical_dump = Dump()

    bundle = adapter._historical_bundle(2000, 1, "FP1")

    assert bundle["summary"]["source"] == "Jolpica CSV database dump"
    assert bundle["results"]["availability"] == "unavailable"


def test_session_cache_pruning_is_confined_to_exact_api_path(tmp_path):
    target = tmp_path / "2026" / "2026-05-03_Miami_Grand_Prix" / "2026-05-01_Practice_1"
    sibling = target.parent / "2026-05-02_Qualifying"
    target.mkdir(parents=True)
    sibling.mkdir()
    (target / "car_data.ff1pkl").write_bytes(b"telemetry")
    (sibling / "keep.ff1pkl").write_bytes(b"keep")

    adapter = FastF1Adapter.__new__(FastF1Adapter)
    adapter.cache_dir = tmp_path.resolve()
    adapter._loaded_sessions = {
        "2026-4-FP1": (SimpleNamespace(
            api_path="/static/2026/2026-05-03_Miami_Grand_Prix/2026-05-01_Practice_1/"
        ), True)
    }

    result = adapter.prune_session_cache("2026-4-FP1")

    assert result == {"files": 1, "bytes": 9}
    assert not target.exists()
    assert (sibling / "keep.ff1pkl").is_file()


def test_telemetry_lap_errors_are_not_silently_dropped():
    class BrokenLap(dict):
        def get_telemetry(self):
            raise ValueError("broken telemetry")

    class FakeILoc:
        def __getitem__(self, _index):
            return BrokenLap(Driver="TST", LapNumber=1)

    class FakeLaps:
        iloc = FakeILoc()

        def __len__(self):
            return 1

    class FakeSession:
        laps = FakeLaps()

    adapter = FastF1Adapter.__new__(FastF1Adapter)
    adapter.load_session = lambda *_args, **_kwargs: FakeSession()

    with pytest.raises(RuntimeError, match="Telemetry extraction failed for 1 laps"):
        adapter.session_telemetry_laps("2025-1-Q")


def test_telemetry_preserves_every_published_channel():
    class Lap(dict):
        def get_telemetry(self):
            return pd.DataFrame([{
                "Time": pd.Timedelta(seconds=1),
                "Distance": 100.0,
                "Speed": 250,
                "DriverAhead": "44",
                "DistanceToDriverAhead": 12.5,
                "RelativeDistance": 0.25,
                "FutureChannel": "kept",
            }])

        def get_car_data(self):
            return pd.DataFrame([{
                "Time": pd.Timedelta(seconds=1), "Speed": 250,
                "RawCarChannel": "kept",
            }])

        def get_pos_data(self):
            return pd.DataFrame([{
                "Time": pd.Timedelta(seconds=1), "X": 10,
                "RawPositionChannel": "kept",
            }])

    class FakeILoc:
        def __getitem__(self, _index):
            return Lap(Driver="TST", DriverNumber="1", LapNumber=1)

    class FakeLaps:
        iloc = FakeILoc()

        def __len__(self):
            return 1

    class FakeSession:
        laps = FakeLaps()

    adapter = FastF1Adapter.__new__(FastF1Adapter)
    adapter.load_session = lambda *_args, **_kwargs: FakeSession()

    telemetry = adapter.session_telemetry_laps("2025-1-Q")[0]
    points = telemetry["points"]
    assert points[0]["DriverAhead"] == "44"
    assert points[0]["DistanceToDriverAhead"] == 12.5
    assert points[0]["RelativeDistance"] == 0.25
    assert points[0]["FutureChannel"] == "kept"
    assert telemetry["car_points"][0]["RawCarChannel"] == "kept"
    assert telemetry["position_points"][0]["RawPositionChannel"] == "kept"


def test_results_and_laps_preserve_every_published_column():
    class Session:
        results = pd.DataFrame([{
            "Position": 1,
            "Abbreviation": "TST",
            "DriverNumber": "1",
            "BroadcastName": "T TEST",
            "ClassifiedPosition": "1",
            "FutureResultField": "kept",
        }])
        laps = pd.DataFrame([{
            "Driver": "TST",
            "DriverNumber": "1",
            "LapNumber": 1,
            "LapTime": pd.Timedelta(seconds=90),
            "FreshTyre": True,
            "FastF1Generated": False,
            "FutureLapField": "kept",
        }])

    adapter = FastF1Adapter.__new__(FastF1Adapter)
    result = adapter._results(Session(), {})[0]
    lap = adapter._laps(Session(), {})[0]

    assert result["BroadcastName"] == "T TEST"
    assert result["ClassifiedPosition"] == "1"
    assert result["FutureResultField"] == "kept"
    assert lap["FreshTyre"] is True
    assert lap["FastF1Generated"] is False
    assert lap["FutureLapField"] == "kept"


def test_indexed_telemetry_slice_keeps_exact_published_lap_samples():
    stream = pd.DataFrame({
        "SessionTime": pd.to_timedelta([0, 1, 2, 3], unit="s"),
        "Time": pd.to_timedelta([0, 1, 2, 3], unit="s"),
        "X": [10, 20, 30, 40],
        "FutureChannel": ["a", "b", "c", "d"],
    })
    lap = {
        "LapStartTime": pd.Timedelta(seconds=1),
        "Time": pd.Timedelta(seconds=2),
    }

    values = FastF1Adapter._session_time_values(stream)
    result = FastF1Adapter._slice_published_lap_points(stream, lap, values)

    assert result["X"].tolist() == [20, 30]
    assert result["FutureChannel"].tolist() == ["b", "c"]
    assert result["Time"].tolist() == [
        pd.Timedelta(0),
        pd.Timedelta(seconds=1),
    ]


def test_telemetry_merges_once_per_driver_before_slicing_laps():
    calls = {"merged": 0, "car": 0, "position": 0}

    class Stream(pd.DataFrame):
        @property
        def _constructor(self):
            return Stream

        def slice_by_lap(self, _lap, **_kwargs):
            return self

    merged = Stream([{"Time": pd.Timedelta(0), "Distance": 0.0, "Speed": 200}])
    car = Stream([{"Time": pd.Timedelta(0), "Speed": 199}])
    position = Stream([{"Time": pd.Timedelta(0), "X": 1, "Y": 2}])
    laps = [
        {"Driver": "TST", "DriverNumber": "1", "LapNumber": 1},
        {"Driver": "TST", "DriverNumber": "1", "LapNumber": 2},
    ]

    class ILoc:
        def __getitem__(self, index):
            return laps[index]

    class DriverLaps:
        iloc = ILoc()

        def __len__(self):
            return len(laps)

        @staticmethod
        def get_telemetry():
            calls["merged"] += 1
            return merged

        @staticmethod
        def get_car_data():
            calls["car"] += 1
            return car

        @staticmethod
        def get_pos_data():
            calls["position"] += 1
            return position

    class BulkLaps:
        columns = ["Driver"]

        @staticmethod
        def pick_drivers(_driver):
            return DriverLaps()

        @staticmethod
        def __getitem__(_key):
            return pd.Series(["TST", "TST"])

    class Session:
        laps = BulkLaps()

    adapter = FastF1Adapter.__new__(FastF1Adapter)
    adapter.load_session = lambda *_args, **_kwargs: Session()

    documents = adapter.session_telemetry_laps("2025-1-Q")

    assert len(documents) == 2
    assert calls == {"merged": 1, "car": 1, "position": 1}
