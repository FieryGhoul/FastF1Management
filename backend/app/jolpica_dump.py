"""Read Jolpica's official CSV database dump without holding it all in RAM.

The public Ergast-compatible API limits responses to 100 rows.  A single race
usually has around one thousand lap rows, so paging the complete archive is
both slow and unnecessarily expensive for the community API.  Jolpica also
publishes a free delayed CSV dump.  This module indexes that dump in a local
SQLite cache and exposes complete, session-shaped records to the importer.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sqlite3
import unicodedata
import urllib.request
import zipfile
from pathlib import Path
from threading import RLock
from typing import Any


TABLE_FILES = {
    "season": "formula_one_season.csv",
    "round": "formula_one_round.csv",
    "circuit": "formula_one_circuit.csv",
    "session": "formula_one_session.csv",
    "session_entry": "formula_one_sessionentry.csv",
    "round_entry": "formula_one_roundentry.csv",
    "team_driver": "formula_one_teamdriver.csv",
    "driver": "formula_one_driver.csv",
    "team": "formula_one_team.csv",
    "lap": "formula_one_lap.csv",
    "pit_stop": "formula_one_pitstop.csv",
}
DUMP_INDEX_URL = "https://api.jolpi.ca/data/dumps/download/"
HISTORICAL_DUMP_SCHEMA_VERSION = 2
SESSION_TYPE_ALIASES = {
    "S": ("S", "SR"),
    "Q": ("Q", "QB"),
    "SQ": ("SQ", "SS"),
    "SS": ("SS", "SQ"),
}


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _boolean(value: Any) -> bool:
    return str(value).lower() in {"1", "t", "true", "yes"}


def duration_milliseconds(value: Any) -> int | None:
    """Convert PostgreSQL-style duration text to integer milliseconds."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    days = 0
    if " day" in text:
        day_text, text = text.split(",", 1)
        days = int(day_text.split()[0])
        text = text.strip()
    parts = text.split(":")
    if len(parts) != 3:
        return None
    hours, minutes, seconds = parts
    total = (
        days * 86_400
        + int(hours) * 3_600
        + int(minutes) * 60
        + float(seconds)
    )
    return round(total * 1_000)


def _letters(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return "".join(character for character in normalized.upper() if character.isalpha())


def unique_driver_codes(rows: list[Any]) -> dict[str, str]:
    """Create stable, readable and session-unique legacy driver codes."""
    output: dict[str, str] = {}
    used: dict[str, str] = {}
    for row in rows:
        reference = str(row["driver_reference"] or "")
        if not reference or reference in output:
            continue
        abbreviation = _letters(row["driver_abbreviation"])
        surname = _letters(row["driver_surname"])
        consonants = "".join(character for character in surname if character not in "AEIOU")
        reference_letters = _letters(reference)
        candidates = [
            abbreviation[:3],
            surname[:3],
            consonants[:3],
            (surname[:1] + surname[-2:]) if len(surname) >= 3 else surname,
            (surname[:2] + surname[-1:]) if len(surname) >= 3 else surname,
            reference_letters[:3],
        ]
        candidate = next(
            (
                value for value in candidates
                if len(value) == 3 and (value not in used or used[value] == reference)
            ),
            None,
        )
        if candidate is None:
            base = (surname or reference_letters or "DRV")[:2].ljust(2, "X")
            suffix = 0
            while f"{base}{suffix % 10}" in used:
                suffix += 1
            candidate = f"{base}{suffix % 10}"
        output[reference] = candidate
        used[candidate] = reference
    return output


class JolpicaDump:
    """A lazily built SQLite index over an extracted Jolpica CSV dump."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.csv_dir = self.root / "csv"
        self.database_path = self.root / "jolpica.sqlite3"
        self._prepare_lock = RLock()

    @property
    def source_available(self) -> bool:
        return all((self.csv_dir / filename).is_file() for filename in TABLE_FILES.values())

    def _fingerprint(self) -> str:
        digest = hashlib.sha256()
        for filename in TABLE_FILES.values():
            path = self.csv_dir / filename
            stat = path.stat()
            digest.update(filename.encode())
            digest.update(str(stat.st_size).encode())
            digest.update(str(stat.st_mtime_ns).encode())
        return digest.hexdigest()

    def prepare(self) -> bool:
        with self._prepare_lock:
            if not self.source_available:
                return False
            fingerprint = self._fingerprint()
            if self.database_path.is_file():
                try:
                    with sqlite3.connect(self.database_path) as connection:
                        row = connection.execute(
                            "SELECT value FROM metadata WHERE key = 'fingerprint'"
                        ).fetchone()
                    if row and row[0] == fingerprint:
                        return True
                except sqlite3.Error:
                    pass
            self._build(fingerprint)
            return True

    def download_delayed(self) -> dict[str, Any]:
        """Download, verify and safely extract Jolpica's free delayed dump."""
        with urllib.request.urlopen(DUMP_INDEX_URL, timeout=60) as response:
            metadata = json.load(response)
        details = metadata["delayed_dumps"]["csv"]
        self.root.mkdir(parents=True, exist_ok=True)
        archive = self.root / "jolpica-csv.zip"
        temporary = archive.with_suffix(".zip.tmp")
        temporary.unlink(missing_ok=True)
        with (
            urllib.request.urlopen(details["download_url"], timeout=180) as response,
            temporary.open("wb") as output,
        ):
            shutil.copyfileobj(response, output)
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        if digest.lower() != str(details["file_hash"]).lower():
            temporary.unlink(missing_ok=True)
            raise RuntimeError("Jolpica dump checksum did not match the published SHA-256 hash")
        if temporary.stat().st_size != int(details["file_size"]):
            temporary.unlink(missing_ok=True)
            raise RuntimeError("Jolpica dump size did not match the published size")
        archive.unlink(missing_ok=True)
        temporary.replace(archive)
        self.csv_dir.mkdir(parents=True, exist_ok=True)
        destination = self.csv_dir.resolve()
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                target = (destination / member.filename).resolve()
                if destination not in target.parents and target != destination:
                    raise RuntimeError("Jolpica dump contains an unsafe archive path")
            package.extractall(destination)
        (self.root / "dump-metadata.json").write_text(
            json.dumps(details, indent=2), encoding="utf-8",
        )
        self.prepare()
        return details

    def ensure(self) -> dict[str, Any]:
        # A background worker may be downloading or indexing while a session
        # request reaches the same adapter. Serialize preparation so neither
        # path observes a partially extracted or partially indexed archive.
        with self._prepare_lock:
            if not self.source_available:
                details = self.download_delayed()
                return {"downloaded": True, **details}
            self.prepare()
            return {"downloaded": False, "root": str(self.root)}

    def _build(self, fingerprint: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.database_path.with_suffix(".sqlite3.tmp")
        temporary.unlink(missing_ok=True)
        connection = sqlite3.connect(temporary)
        try:
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute("PRAGMA temp_store=MEMORY")
            for table, filename in TABLE_FILES.items():
                path = self.csv_dir / filename
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    reader = csv.reader(handle)
                    columns = next(reader)
                    quoted = ", ".join(f'"{column}" TEXT' for column in columns)
                    connection.execute(f'CREATE TABLE "{table}" ({quoted})')
                    placeholders = ", ".join("?" for _ in columns)
                    statement = f'INSERT INTO "{table}" VALUES ({placeholders})'
                    batch: list[list[str]] = []
                    for row in reader:
                        batch.append(row)
                        if len(batch) >= 5_000:
                            connection.executemany(statement, batch)
                            batch.clear()
                    if batch:
                        connection.executemany(statement, batch)

            indexes = (
                "CREATE UNIQUE INDEX season_year ON season(year)",
                "CREATE INDEX round_season_number ON round(season_id, number)",
                "CREATE INDEX session_round_type ON session(round_id, type)",
                "CREATE INDEX session_entry_session ON session_entry(session_id)",
                "CREATE INDEX round_entry_id ON round_entry(id)",
                "CREATE INDEX team_driver_id ON team_driver(id)",
                "CREATE INDEX driver_id ON driver(id)",
                "CREATE INDEX team_id ON team(id)",
                "CREATE INDEX lap_session_entry ON lap(session_entry_id)",
                "CREATE INDEX pit_stop_session_entry ON pit_stop(session_entry_id)",
            )
            for statement in indexes:
                connection.execute(statement)
            connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES ('fingerprint', ?)",
                (fingerprint,),
            )
            connection.commit()
        finally:
            connection.close()
        self.database_path.unlink(missing_ok=True)
        temporary.replace(self.database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _driver_code(row: sqlite3.Row, codes: dict[str, str]) -> str | None:
        return codes.get(str(row["driver_reference"] or ""))

    @staticmethod
    def _combined_qualifying(
        connection: sqlite3.Connection, year: int, round_number: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        """Merge split Q1/Q2/Q3 dump sessions into one final classification."""
        sessions = connection.execute(
            """
            SELECT s.*, r.name AS round_name, r.date AS round_date,
                   c.name AS circuit_name, c.country_code,
                   c.locality AS circuit_location
            FROM session s
            JOIN round r ON r.id = s.round_id
            JOIN season y ON y.id = r.season_id
            LEFT JOIN circuit c ON c.id = r.circuit_id
            WHERE y.year = ? AND r.number = ? AND s.type IN ('Q1', 'Q2', 'Q3')
            ORDER BY CAST(s.number AS INTEGER)
            """,
            (str(year), str(round_number)),
        ).fetchall()
        if not sessions:
            return None
        session_ids = [row["id"] for row in sessions]
        placeholders = ", ".join("?" for _ in session_ids)
        phase_entries = connection.execute(
            f"""
            SELECT se.*, s.type AS qualifying_segment, re.car_number,
                   d.reference AS driver_reference,
                   d.abbreviation AS driver_abbreviation,
                   d.forename AS driver_forename,
                   d.surname AS driver_surname,
                   t.name AS team_name
            FROM session_entry se
            JOIN session s ON s.id = se.session_id
            JOIN round_entry re ON re.id = se.round_entry_id
            JOIN team_driver td ON td.id = re.team_driver_id
            JOIN driver d ON d.id = td.driver_id
            LEFT JOIN team t ON t.id = td.team_id
            WHERE se.session_id IN ({placeholders})
            ORDER BY CAST(s.number AS INTEGER), CAST(se.position AS INTEGER)
            """,
            session_ids,
        ).fetchall()
        phase_laps = connection.execute(
            f"""
            SELECT l.*, s.type AS qualifying_segment,
                   d.reference AS driver_reference
            FROM lap l
            JOIN session_entry se ON se.id = l.session_entry_id
            JOIN session s ON s.id = se.session_id
            JOIN round_entry re ON re.id = se.round_entry_id
            JOIN team_driver td ON td.id = re.team_driver_id
            JOIN driver d ON d.id = td.driver_id
            WHERE se.session_id IN ({placeholders})
            """,
            session_ids,
        ).fetchall()
        by_driver: dict[str, dict[str, Any]] = {}
        for row in phase_entries:
            item = dict(row)
            reference = str(row["driver_reference"])
            segment = str(row["qualifying_segment"])
            merged = by_driver.setdefault(reference, {})
            merged.update(item)  # deeper phases appear later and own final position
            merged[segment] = duration_milliseconds(row["time"])
            merged[f"{segment}EntryRecord"] = item
        for row in phase_laps:
            reference = str(row["driver_reference"])
            segment = str(row["qualifying_segment"])
            if reference in by_driver:
                by_driver[reference][f"{segment}LapRecord"] = dict(row)
                if by_driver[reference].get(segment) is None:
                    by_driver[reference][segment] = duration_milliseconds(row["time"])
        entries = sorted(
            by_driver.values(),
            key=lambda row: (_optional_int(row.get("position")) or 10_000),
        )
        return dict(sessions[-1]), entries

    def session_bundle(self, year: int, round_number: int, code: str) -> dict[str, Any] | None:
        if not self.prepare():
            return None
        candidates = SESSION_TYPE_ALIASES.get(code, (code,))
        placeholders = ", ".join("?" for _ in candidates)
        with self._connect() as connection:
            session = connection.execute(
                f"""
                SELECT s.*, r.name AS round_name, r.date AS round_date,
                       c.name AS circuit_name, c.country_code,
                       c.locality AS circuit_location
                FROM session s
                JOIN round r ON r.id = s.round_id
                JOIN season y ON y.id = r.season_id
                LEFT JOIN circuit c ON c.id = r.circuit_id
                WHERE y.year = ? AND r.number = ? AND s.type IN ({placeholders})
                ORDER BY CASE WHEN s.type = ? THEN 0 ELSE 1 END, s.number
                LIMIT 1
                """,
                (str(year), str(round_number), *candidates, code),
            ).fetchone()
            combined_qualifying = None
            if session is None and code == "Q":
                combined_qualifying = self._combined_qualifying(
                    connection, year, round_number,
                )
            if session is None and combined_qualifying is None:
                return None
            if combined_qualifying is not None:
                session, entries = combined_qualifying
            else:
                entries = connection.execute(
                """
                SELECT se.*, re.car_number,
                       d.reference AS driver_reference,
                       d.abbreviation AS driver_abbreviation,
                       d.forename AS driver_forename,
                       d.surname AS driver_surname,
                       t.name AS team_name
                FROM session_entry se
                JOIN round_entry re ON re.id = se.round_entry_id
                JOIN team_driver td ON td.id = re.team_driver_id
                JOIN driver d ON d.id = td.driver_id
                LEFT JOIN team t ON t.id = td.team_id
                WHERE se.session_id = ?
                ORDER BY CAST(se.position AS INTEGER), CAST(re.car_number AS INTEGER)
                """,
                (session["id"],),
                ).fetchall()
            laps = [] if combined_qualifying is not None else connection.execute(
                """
                SELECT l.*, re.car_number,
                       d.reference AS driver_reference,
                       d.abbreviation AS driver_abbreviation,
                       d.surname AS driver_surname
                FROM lap l
                JOIN session_entry se ON se.id = l.session_entry_id
                JOIN round_entry re ON re.id = se.round_entry_id
                JOIN team_driver td ON td.id = re.team_driver_id
                JOIN driver d ON d.id = td.driver_id
                WHERE se.session_id = ?
                ORDER BY CAST(l.number AS INTEGER), CAST(l.position AS INTEGER)
                """,
                (session["id"],),
            ).fetchall()
            pit_stops = [] if combined_qualifying is not None else connection.execute(
                """
                SELECT p.*, l.number AS lap_number, re.car_number,
                       d.reference AS driver_reference,
                       d.abbreviation AS driver_abbreviation,
                       d.surname AS driver_surname
                FROM pit_stop p
                JOIN session_entry se ON se.id = p.session_entry_id
                JOIN round_entry re ON re.id = se.round_entry_id
                JOIN team_driver td ON td.id = re.team_driver_id
                JOIN driver d ON d.id = td.driver_id
                LEFT JOIN lap l ON l.id = p.lap_id
                WHERE se.session_id = ?
                ORDER BY CAST(p.number AS INTEGER), p.local_timestamp
                """,
                (session["id"],),
            ).fetchall()

            # Older single-session qualifying stores one fastest-lap record
            # per entry. Keep that raw row nested with the classification and
            # expose its time as Q1 instead of pretending it is a numbered lap.
            if code == "Q" and combined_qualifying is None:
                raw_laps = {str(row["session_entry_id"]): dict(row) for row in laps}
                normalized_entries = []
                for row in entries:
                    item = dict(row)
                    source_lap = raw_laps.get(str(row["id"]))
                    item["Q1"] = duration_milliseconds(row["time"])
                    if item["Q1"] is None and source_lap is not None:
                        item["Q1"] = duration_milliseconds(source_lap.get("time"))
                    if str(row["id"]) in raw_laps:
                        item["Q1LapRecord"] = raw_laps[str(row["id"])]
                    normalized_entries.append(item)
                entries = normalized_entries
                laps = []

        driver_code_map = unique_driver_codes(entries)
        result_rows = []
        for row in entries:
            item = dict(row)
            driver_code = self._driver_code(row, driver_code_map)
            item.update({
                "position": _optional_int(row["position"]),
                "grid": _optional_int(row["grid"]),
                "driverCode": driver_code,
                "driverId": row["driver_reference"],
                "givenName": row["driver_forename"],
                "familyName": row["driver_surname"],
                "driverNumber": _optional_int(row["car_number"]),
                "constructorName": row["team_name"],
                "laps": _optional_int(row["laps_completed"]),
                "points": _optional_float(row["points"]),
                "status": row["detail"],
                "totalRaceTime": duration_milliseconds(row["time"]),
                "fastestLapRank": _optional_int(row["fastest_lap_rank"]),
            })
            result_rows.append(item)

        lap_rows = []
        for row in laps:
            item = dict(row)
            driver_code = self._driver_code(row, driver_code_map)
            lap_number = _optional_int(row["number"])
            lap_time = duration_milliseconds(row["time"])
            deleted = _boolean(row["is_deleted"])
            item.update({
                "DriverId": row["driver_reference"],
                "Driver": driver_code,
                "DriverNumber": _optional_int(row["car_number"]),
                "LapNumber": lap_number,
                "LapTime": lap_time,
                "Position": _optional_int(row["position"]),
                "AverageSpeed": _optional_float(row["average_speed"]),
                "IsEntryFastestLap": _boolean(row["is_entry_fastest_lap"]),
                "IsAccurate": bool(lap_number is not None and lap_time is not None and not deleted),
                "Deleted": deleted,
                "DataSource": "Jolpica CSV database dump",
            })
            lap_rows.append(item)

        strategy_rows = []
        for row in pit_stops:
            item = dict(row)
            driver_code = self._driver_code(row, driver_code_map)
            stop_number = _optional_int(row["number"])
            item.update({
                "DriverId": row["driver_reference"],
                "Driver": driver_code,
                "DriverNumber": _optional_int(row["car_number"]),
                "Stint": stop_number,
                "StopNumber": stop_number,
                "LapNumber": _optional_int(row["lap_number"]),
                "PitTime": row["local_timestamp"] or None,
                "Duration": duration_milliseconds(row["duration"]),
                "DataSource": "Jolpica CSV database dump",
            })
            strategy_rows.append(item)

        driver_codes = [
            item.get("driverCode") or item.get("driverId")
            for item in result_rows
            if item.get("driverCode") or item.get("driverId")
        ]
        total_laps = max(
            (item["laps"] for item in result_rows if item.get("laps") is not None),
            default=None,
        )
        session_name = {
            "R": "Race", "Q": "Qualifying", "S": "Sprint",
            "SQ": "Sprint Qualifying", "SS": "Sprint Shootout",
            "FP1": "Practice 1", "FP2": "Practice 2", "FP3": "Practice 3",
        }.get(code, code)
        summary = {
            "name": session_name,
            "date": session["timestamp"] or session["round_date"],
            "event": session["round_name"],
            "country": session["country_code"],
            "location": session["circuit_location"],
            "circuit": session["circuit_name"],
            "total_laps": total_laps,
            "drivers": driver_codes,
            "scheduled_laps": _optional_int(session["scheduled_laps"]),
            "has_time_data": _boolean(session["has_time_data"]),
            "dump_session_api_id": session["api_id"],
        }
        return {
            "summary": summary,
            "results": result_rows,
            "laps": lap_rows,
            "strategy": strategy_rows,
        }

    def expected_counts(self, year: int, round_number: int, code: str) -> dict[str, int] | None:
        """Return source row counts without materializing a session payload."""
        if not self.prepare():
            return None
        candidates = SESSION_TYPE_ALIASES.get(code, (code,))
        placeholders = ", ".join("?" for _ in candidates)
        with self._connect() as connection:
            session = connection.execute(
                f"""
                SELECT s.id
                FROM session s
                JOIN round r ON r.id = s.round_id
                JOIN season y ON y.id = r.season_id
                WHERE y.year = ? AND r.number = ? AND s.type IN ({placeholders})
                ORDER BY CASE WHEN s.type = ? THEN 0 ELSE 1 END, s.number
                LIMIT 1
                """,
                (str(year), str(round_number), *candidates, code),
            ).fetchone()
            if session is None and code == "Q":
                combined = self._combined_qualifying(connection, year, round_number)
                if combined is not None:
                    return {"results": len(combined[1]), "laps": 0, "strategy": 0}
            if session is None:
                return None
            session_id = session["id"]
            results = connection.execute(
                "SELECT COUNT(*) FROM session_entry WHERE session_id = ?", (session_id,),
            ).fetchone()[0]
            laps = connection.execute(
                """
                SELECT COUNT(*) FROM lap l
                JOIN session_entry se ON se.id = l.session_entry_id
                WHERE se.session_id = ?
                """,
                (session_id,),
            ).fetchone()[0]
            strategy = connection.execute(
                "SELECT COUNT(*) FROM pit_stop WHERE session_entry_id IN (SELECT id FROM session_entry WHERE session_id = ?)",
                (session_id,),
            ).fetchone()[0]
            if code == "Q":
                laps = 0
                strategy = 0
        return {"results": results, "laps": laps, "strategy": strategy}


def dump_inventory(root: Path) -> dict[str, Any]:
    dump = JolpicaDump(root)
    ready = dump.prepare()
    if not ready:
        return {"ready": False, "root": str(root), "missing": [
            filename for filename in TABLE_FILES.values()
            if not (dump.csv_dir / filename).is_file()
        ]}
    with dump._connect() as connection:
        counts = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in TABLE_FILES
        }
    return {"ready": True, "root": str(root), "counts": counts}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build and inspect the local Jolpica CSV index")
    parser.add_argument("--root", type=Path, default=Path(".cache/jolpica-dump"))
    parser.add_argument("--download", action="store_true", help="download the official free delayed dump if missing")
    args = parser.parse_args()
    if args.download:
        JolpicaDump(args.root).ensure()
    print(json.dumps(dump_inventory(args.root), indent=2))
