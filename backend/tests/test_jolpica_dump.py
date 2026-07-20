import csv
from pathlib import Path

from app.jolpica_dump import (
    JolpicaDump,
    TABLE_FILES,
    duration_milliseconds,
    unique_driver_codes,
)


HEADERS = {
    "season": ["id", "api_id", "championship_system_id", "wikipedia", "year"],
    "round": ["id", "api_id", "circuit_id", "date", "is_cancelled", "name", "number", "race_number", "season_id", "wikipedia"],
    "circuit": ["id", "altitude", "api_id", "country", "country_code", "latitude", "locality", "longitude", "name", "reference", "wikipedia"],
    "session": ["id", "api_id", "has_time_data", "is_cancelled", "number", "point_system_id", "round_id", "scheduled_laps", "timestamp", "timezone", "type"],
    "session_entry": ["id", "api_id", "detail", "fastest_lap_rank", "grid", "is_classified", "is_eligible_for_points", "laps_completed", "points", "position", "round_entry_id", "session_id", "status", "time"],
    "round_entry": ["id", "api_id", "car_number", "round_id", "team_driver_id"],
    "team_driver": ["id", "api_id", "driver_id", "role", "season_id", "team_id"],
    "driver": ["id", "abbreviation", "api_id", "country_code", "date_of_birth", "forename", "nationality", "permanent_car_number", "reference", "surname", "wikipedia"],
    "team": ["id", "api_id", "base_team_id", "country_code", "name", "nationality", "primary_color", "reference", "wikipedia"],
    "lap": ["id", "api_id", "average_speed", "is_deleted", "is_entry_fastest_lap", "number", "position", "session_entry_id", "time"],
    "pit_stop": ["id", "api_id", "duration", "lap_id", "local_timestamp", "number", "session_entry_id"],
}


def write_table(root: Path, table: str, rows: list[list[str]]) -> None:
    path = root / "csv" / TABLE_FILES[table]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS[table])
        writer.writerows(rows)


def test_dump_session_bundle_returns_every_lap_and_pit_row(tmp_path):
    rows = {
        "season": [["1", "season", "1", "", "2000"]],
        "round": [["10", "round", "5", "2000-03-12", "f", "Australian Grand Prix", "1", "1", "1", ""]],
        "circuit": [["5", "10", "circuit", "Australia", "AUS", "", "Melbourne", "", "Albert Park", "albert_park", ""]],
        "session": [["20", "session", "t", "f", "1", "1", "10", "58", "2000-03-12 03:00:00+00:00", "Australia/Melbourne", "R"]],
        "session_entry": [["30", "entry", "Finished", "1", "1", "t", "t", "58", "10", "1", "40", "20", "0", "01:31:00.123"]],
        "round_entry": [["40", "round-entry", "3", "10", "50"]],
        "team_driver": [["50", "team-driver", "60", "", "1", "70"]],
        "driver": [["60", "MSC", "driver", "DEU", "1969-01-03", "Michael", "German", "", "michael_schumacher", "Schumacher", ""]],
        "team": [["70", "team", "", "ITA", "Ferrari", "Italian", "", "ferrari", ""]],
        "lap": [
            ["80", "lap-1", "", "f", "f", "1", "1", "30", "00:01:32.100"],
            ["81", "lap-2", "", "f", "t", "2", "1", "30", "00:01:31.500"],
        ],
        "pit_stop": [["90", "pit", "00:00:24.036", "81", "14:20:10", "1", "30"]],
    }
    for table in TABLE_FILES:
        write_table(tmp_path, table, rows[table])

    bundle = JolpicaDump(tmp_path).session_bundle(2000, 1, "R")

    assert bundle is not None
    assert bundle["summary"]["event"] == "Australian Grand Prix"
    assert bundle["results"][0]["driverCode"] == "MSC"
    assert [lap["LapNumber"] for lap in bundle["laps"]] == [1, 2]
    assert bundle["laps"][1]["LapTime"] == 91_500
    assert bundle["strategy"][0]["LapNumber"] == 2
    assert bundle["strategy"][0]["Duration"] == 24_036
    assert JolpicaDump(tmp_path).expected_counts(2000, 1, "R") == {
        "results": 1, "laps": 2, "strategy": 1,
    }


def test_duration_milliseconds():
    assert duration_milliseconds("00:01:30.250") == 90_250
    assert duration_milliseconds("1 day, 00:00:00") == 86_400_000
    assert duration_milliseconds("") is None


def test_generated_legacy_driver_codes_are_unique():
    rows = [
        {"driver_reference": "panis", "driver_abbreviation": "", "driver_surname": "Panis"},
        {"driver_reference": "pantano", "driver_abbreviation": "", "driver_surname": "Pantano"},
    ]

    assert unique_driver_codes(rows) == {"panis": "PAN", "pantano": "PNT"}
