import re
import shutil
import time
from collections import OrderedDict
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fastf1
import numpy as np
import pandas as pd
from fastf1.ergast import Ergast

from .contracts import ARTIFACT_VERSION, artifact_key
from .jolpica_dump import HISTORICAL_DUMP_SCHEMA_VERSION, JolpicaDump
from .serialization import clean, records


SESSION_CODES = {
    "Practice 1": "FP1", "Practice 2": "FP2", "Practice 3": "FP3",
    "Qualifying": "Q", "Sprint Qualifying": "SQ", "Sprint Shootout": "SS",
    "Sprint": "S", "Race": "R",
}


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


class FastF1Adapter:
    def __init__(self, cache_dir: Path):
        cache_dir.mkdir(parents=True, exist_ok=True)
        fastf1.Cache.enable_cache(str(cache_dir))
        self.cache_dir = cache_dir.resolve()
        self.ergast = Ergast(result_type="pandas", auto_cast=True, limit=1000)
        self.historical_dump = JolpicaDump(cache_dir.parent / "jolpica-dump")
        self._loaded_sessions: OrderedDict[str, tuple[Any, bool]] = OrderedDict()
        self._last_ergast_request = 0.0

    def prune_session_cache(self, session_id: str) -> dict[str, int]:
        """Remove one verified session's staging cache using its exact API path."""
        cached = self._loaded_sessions.get(session_id)
        session = cached[0] if cached is not None else None
        if session is None:
            year, round_number, code = self.parse_session_id(session_id)
            session = fastf1.get_session(year, round_number, code)
        api_path = str(getattr(session, "api_path", "")).strip("/")
        parts = Path(api_path).parts
        if parts and parts[0].lower() == "static":
            parts = parts[1:]
        target = self.cache_dir.joinpath(*parts).resolve()
        if not parts or self.cache_dir not in target.parents:
            raise RuntimeError(f"Refusing to prune unsafe FastF1 cache path: {target}")
        files = 0
        size = 0
        if target.is_dir():
            for path in target.rglob("*"):
                if path.is_file():
                    files += 1
                    size += path.stat().st_size
            shutil.rmtree(target)
        self._loaded_sessions.pop(session_id, None)
        return {"files": files, "bytes": size}

    def _wait_for_ergast(self) -> None:
        """Keep Jolpica usage below its documented 500-request/hour cap."""
        interval = 8.0
        elapsed = time.monotonic() - getattr(self, "_last_ergast_request", 0.0)
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_ergast_request = time.monotonic()

    def prepare_historical_dump(self) -> dict[str, Any]:
        """Ensure the bulk historical source exists before an archive run."""
        return self.historical_dump.ensure()

    def _paged_ergast(self, endpoint, **kwargs) -> list[Any]:
        """Fetch every API page when the bulk dump is unavailable."""
        pages = []
        offset = 0
        while True:
            self._wait_for_ergast()
            response = endpoint(limit=100, offset=offset, **kwargs)
            pages.append(response)
            total = getattr(response, "total_results", None)
            if total is None:
                break
            offset += 100
            if offset >= int(total):
                break
        return pages

    def schedule(self, year: int) -> list[dict[str, Any]]:
        frame = fastf1.get_event_schedule(year, include_testing=False)
        output: list[dict[str, Any]] = []
        for _, row in frame.iterrows():
            schedule_data = clean(row.to_dict())
            round_number = int(row["RoundNumber"])
            sessions = []
            for index in range(1, 6):
                name = clean(row.get(f"Session{index}"))
                starts = clean(row.get(f"Session{index}DateUtc"))
                if name:
                    sessions.append({
                        "id": f"{year}-{round_number}-{SESSION_CODES.get(name, slugify(name))}",
                        "name": name,
                        "code": SESSION_CODES.get(name, name[:3].upper()),
                        "starts_at": starts,
                    })
            event_date = clean(row.get("EventDate"))
            output.append({
                "id": f"{year}-{round_number}",
                "season": year,
                "round": round_number,
                "name": clean(row.get("EventName")),
                "official_name": clean(row.get("OfficialEventName")),
                "country": clean(row.get("Country")),
                "location": clean(row.get("Location")),
                "event_date": event_date,
                "format": clean(row.get("EventFormat")),
                "f1_api_support": bool(row.get("F1ApiSupport", False)),
                "schedule_data": schedule_data,
                "sessions": sessions,
            })
        return output

    def standings(self, year: int, kind: str, round_number: int | None = None) -> list[dict[str, Any]]:
        self._wait_for_ergast()
        kwargs = {"season": year, "round": round_number}
        response = (self.ergast.get_driver_standings(**kwargs) if kind == "drivers"
                    else self.ergast.get_constructor_standings(**kwargs))
        if not response.content:
            return []
        return records(response.content[0])

    def circuits(self, year: int | None = None) -> list[dict[str, Any]]:
        self._wait_for_ergast()
        response = self.ergast.get_circuits(season=year)
        return records(response)

    def drivers(self, year: int) -> list[dict[str, Any]]:
        self._wait_for_ergast()
        response = self.ergast.get_driver_info(season=year)
        return records(response)

    def constructors(self, year: int) -> list[dict[str, Any]]:
        self._wait_for_ergast()
        response = self.ergast.get_constructor_info(season=year)
        return records(response)

    @staticmethod
    def parse_session_id(session_id: str) -> tuple[int, int, str]:
        parts = session_id.split("-", 2)
        if len(parts) != 3:
            raise ValueError("Session id must look like 2026-1-R")
        return int(parts[0]), int(parts[1]), parts[2]

    def load_session(self, session_id: str, *, telemetry: bool = False):
        cached = self._loaded_sessions.get(session_id)
        if cached is not None and (cached[1] or not telemetry):
            self._loaded_sessions.move_to_end(session_id)
            return cached[0]
        year, round_number, code = self.parse_session_id(session_id)
        session = fastf1.get_session(year, round_number, code)
        session.load(telemetry=telemetry)
        self._loaded_sessions[session_id] = (session, telemetry)
        # Core artifacts and telemetry are persisted adjacently, so only the
        # current session needs to stay resident.  A race session can retain
        # more than 1 GB of timing frames on a long archive run.
        while len(self._loaded_sessions) > 1:
            self._loaded_sessions.popitem(last=False)
        return session

    def session_bundle(self, session_id: str) -> dict[str, dict[str, Any]]:
        year, round_number, code = self.parse_session_id(session_id)
        if year < 2018:
            return self._historical_bundle(year, round_number, code)

        kinds = [
            "summary", "results", "laps", "strategy", "weather", "race-control"
        ]
        session = self.load_session(session_id, telemetry=False)
        handlers = {
            "summary": self._summary, "results": self._results, "laps": self._laps,
            "strategy": self._strategy, "weather": self._weather,
            "race-control": self._race_control,
        }
        updated_at = datetime.now(timezone.utc).isoformat()
        bundle = {}
        for kind in kinds:
            try:
                data = handlers[kind](session, {})
                bundle[kind] = {
                    "availability": "available", "unavailable_reason": None,
                    "data": data, "source": "FastF1", "updated_at": updated_at,
                }
            except Exception as exc:
                bundle[kind] = self._unavailable(kind, exc, updated_at)
        return bundle

    def _historical_bundle(self, year: int, round_number: int, code: str) -> dict[str, dict[str, Any]]:
        """Serve pre-timing-era results through Jolpica without loading a FastF1 session."""
        updated_at = datetime.now(timezone.utc).isoformat()
        dump = getattr(self, "historical_dump", None)
        dump_data = dump.session_bundle(year, round_number, code) if dump else None
        if dump_data is not None:
            source = "Jolpica CSV database dump"
            bundle = {
                "summary": {
                    "availability": "available", "unavailable_reason": None,
                    "data": dump_data["summary"], "source": source,
                    "updated_at": updated_at,
                    "schema_version": HISTORICAL_DUMP_SCHEMA_VERSION,
                },
                "results": {
                    "availability": "available" if dump_data["results"] else "unavailable",
                    "unavailable_reason": None if dump_data["results"] else
                        "No historical classification is available for this session.",
                    "data": self._historical_results(pd.DataFrame(dump_data["results"])),
                    "source": source, "updated_at": updated_at,
                    "schema_version": HISTORICAL_DUMP_SCHEMA_VERSION,
                },
                "laps": {
                    "availability": "available" if dump_data["laps"] else "unavailable",
                    "unavailable_reason": None if dump_data["laps"] else
                        "No historical lap timing is published for this session.",
                    "data": dump_data["laps"], "source": source,
                    "updated_at": updated_at,
                    "schema_version": HISTORICAL_DUMP_SCHEMA_VERSION,
                },
                "strategy": {
                    "availability": "available" if dump_data["strategy"] else "unavailable",
                    "unavailable_reason": None if dump_data["strategy"] else
                        "Historical pit-stop timing is not published for this session.",
                    "data": dump_data["strategy"], "source": source,
                    "updated_at": updated_at,
                    "schema_version": HISTORICAL_DUMP_SCHEMA_VERSION,
                },
            }
            for kind in ("weather", "race-control"):
                bundle[kind] = {
                    "availability": "unavailable",
                    "unavailable_reason": "Detailed timing data is available from 2018 onward.",
                    "data": [], "source": source, "updated_at": updated_at,
                    "schema_version": HISTORICAL_DUMP_SCHEMA_VERSION,
                }
            return bundle

        if dump is not None and dump.source_available:
            # The verified dump is authoritative for the historical range.
            # A missing session means Jolpica publishes no classification for
            # it (typically practice), so record that explicitly without
            # falling through to another rate-limited API request.
            source = "Jolpica CSV database dump"
            name = {
                "R": "Race", "Q": "Qualifying", "S": "Sprint",
                "SQ": "Sprint Qualifying", "SS": "Sprint Shootout",
                "FP1": "Practice 1", "FP2": "Practice 2", "FP3": "Practice 3",
            }.get(code, code)
            bundle = {
                "summary": {
                    "availability": "available", "unavailable_reason": None,
                    "data": {
                        "name": name, "date": None, "event": f"Round {round_number}",
                        "country": None, "location": None, "total_laps": None,
                        "drivers": [],
                    },
                    "source": source, "updated_at": updated_at,
                    "schema_version": HISTORICAL_DUMP_SCHEMA_VERSION,
                }
            }
            reasons = {
                "results": "No historical classification is published for this session.",
                "laps": "No historical lap timing is published for this session.",
                "strategy": "Historical pit-stop timing is not published for this session.",
                "weather": "Detailed timing data is available from 2018 onward.",
                "race-control": "Detailed timing data is available from 2018 onward.",
            }
            for kind, reason in reasons.items():
                bundle[kind] = {
                    "availability": "unavailable", "unavailable_reason": reason,
                    "data": [], "source": source, "updated_at": updated_at,
                    "schema_version": HISTORICAL_DUMP_SCHEMA_VERSION,
                }
            return bundle

        endpoint = {
            "R": self.ergast.get_race_results,
            "Q": self.ergast.get_qualifying_results,
            "S": self.ergast.get_sprint_results,
        }.get(code)
        if endpoint:
            self._wait_for_ergast()
        response = endpoint(season=year, round=round_number) if endpoint else None
        lap_response = None
        pit_response = None
        if code == "R":
            lap_response = self._paged_ergast(
                self.ergast.get_lap_times, season=year, round=round_number,
            )
            if year >= 2011:
                pit_response = self._paged_ergast(
                    self.ergast.get_pit_stops, season=year, round=round_number,
                )
        description = (
            response.description.iloc[0].to_dict()
            if response is not None and not response.description.empty else {}
        )
        frame = (
            response.content[0]
            if response is not None and response.content else pd.DataFrame()
        )
        name = {
            "R": "Race", "Q": "Qualifying", "S": "Sprint",
            "SQ": "Sprint Qualifying", "SS": "Sprint Shootout",
            "FP1": "Practice 1", "FP2": "Practice 2", "FP3": "Practice 3",
        }.get(code, code)
        driver_codes = frame.get("driverCode", pd.Series(dtype=object)).dropna().tolist()
        if not driver_codes and "number" in frame:
            driver_codes = frame["number"].dropna().astype(str).tolist()
        total_laps = clean(frame["laps"].max()) if "laps" in frame and not frame.empty else None
        summary = clean({
            "name": name,
            "date": description.get("raceDate"),
            "event": description.get("raceName") or f"Round {round_number}",
            "country": description.get("country"),
            "location": description.get("locality"),
            "circuit": description.get("circuitName"),
            "total_laps": total_laps,
            "drivers": driver_codes,
        })
        source = "FastF1 Jolpica"
        bundle = {
            "summary": {
                "availability": "available", "unavailable_reason": None,
                "data": summary, "source": source, "updated_at": updated_at,
            }
        }
        for kind in ("weather", "race-control"):
            bundle[kind] = {
                "availability": "unavailable",
                "unavailable_reason": "Detailed timing data is available from 2018 onward.",
                "data": [], "source": source, "updated_at": updated_at,
            }
        historical_laps = self._historical_laps(lap_response)
        bundle["laps"] = {
            "availability": "available" if historical_laps else "unavailable",
            "unavailable_reason": None if historical_laps else
                "No historical race lap timing is published for this session.",
            "data": historical_laps,
            "source": source,
            "updated_at": updated_at,
        }
        pit_stops = self._historical_pit_stops(pit_response)
        bundle["strategy"] = {
            "availability": "available" if pit_stops else "unavailable",
            "unavailable_reason": None if pit_stops else
                "Historical pit-stop timing is not published for this session.",
            "data": pit_stops,
            "source": source,
            "updated_at": updated_at,
        }
        if endpoint is None:
            bundle["results"] = {
                "availability": "unavailable",
                "unavailable_reason": "Historical practice-session classifications are not provided by Jolpica.",
                "data": [], "source": source, "updated_at": updated_at,
            }
        elif frame.empty:
            bundle["results"] = {
                "availability": "unavailable",
                "unavailable_reason": "No historical classification is available for this session.",
                "data": [], "source": source, "updated_at": updated_at,
            }
        else:
            bundle["results"] = {
                "availability": "available", "unavailable_reason": None,
                "data": self._historical_results(frame), "source": source,
                "updated_at": updated_at,
            }
        return bundle

    @staticmethod
    def _historical_laps(response) -> list[dict[str, Any]]:
        rows = []
        responses = response if isinstance(response, list) else [response]
        for page in responses:
            for frame in page.content if page is not None else []:
                for _, row in frame.iterrows():
                    item = clean(row.to_dict())
                    driver_id = item.get("driverId")
                    item.update({
                        "DriverId": driver_id,
                        "Driver": driver_id,
                        "LapNumber": item.get("number"),
                        "LapTime": item.get("time"),
                        "Position": item.get("position"),
                        "IsAccurate": True,
                        "Deleted": False,
                        "DataSource": "Jolpica race laps",
                    })
                    rows.append(item)
        return rows

    @staticmethod
    def _historical_pit_stops(response) -> list[dict[str, Any]]:
        rows = []
        responses = response if isinstance(response, list) else [response]
        for page in responses:
            for frame in page.content if page is not None else []:
                for _, row in frame.iterrows():
                    item = clean(row.to_dict())
                    driver_id = item.get("driverId")
                    stop = item.get("stop")
                    item.update({
                        "DriverId": driver_id,
                        "Driver": driver_id,
                        "Stint": stop,
                        "StopNumber": stop,
                        "LapNumber": item.get("lap"),
                        "PitTime": item.get("time"),
                        "Duration": item.get("duration"),
                        "DataSource": "Jolpica pit stops",
                    })
                    rows.append(item)
        return rows

    @staticmethod
    def _historical_results(frame: pd.DataFrame) -> list[dict[str, Any]]:
        rows = []
        for _, row in frame.iterrows():
            result = clean(row.to_dict())
            result.update(clean({
                "Position": row.get("position"),
                "GridPosition": row.get("grid"),
                "Abbreviation": row.get("driverCode"),
                "FullName": " ".join(filter(None, [row.get("givenName"), row.get("familyName")])),
                "DriverNumber": row.get("driverNumber", row.get("number")),
                "TeamName": row.get("constructorName"),
                "TeamColor": None,
                "Points": row.get("points"),
                "Status": row.get("status"),
                "Time": row.get("totalRaceTime"),
                "Q1": row.get("Q1"),
                "Q2": row.get("Q2"),
                "Q3": row.get("Q3"),
            }))
            rows.append(result)
        return rows

    @staticmethod
    def _unavailable(kind: str, exc: Exception, updated_at: str) -> dict[str, Any]:
        message = str(exc)
        if "has not been loaded" in message:
            message = f"{kind.replace('-', ' ').title()} data is not available for this session."
        return {
            "availability": "unavailable", "unavailable_reason": message,
            "data": [], "source": "FastF1", "updated_at": updated_at,
        }

    def session_artifact(self, session_id: str, kind: str, options: dict[str, Any]) -> dict[str, Any]:
        year, _, _ = self.parse_session_id(session_id)
        if year < 2018 and kind not in {"summary", "results"}:
            return {"availability": "unavailable", "unavailable_reason": "Detailed timing data is available from 2018 onward.", "data": []}
        session = self.load_session(session_id, telemetry=kind in {"track", "telemetry"})
        handlers = {
            "summary": self._summary,
            "results": self._results,
            "laps": self._laps,
            "strategy": self._strategy,
            "weather": self._weather,
            "race-control": self._race_control,
            "track": self._track,
            "telemetry": self._telemetry,
        }
        data = handlers[kind](session, options)
        return {"availability": "available", "unavailable_reason": None, "data": data,
                "source": "FastF1", "updated_at": datetime.now(timezone.utc).isoformat()}

    @staticmethod
    def _session_time_values(stream) -> np.ndarray | None:
        """Return a reusable sorted time index for a telemetry stream."""
        if "SessionTime" not in getattr(stream, "columns", ()):
            return None
        values = stream["SessionTime"].to_numpy(dtype="timedelta64[ns]")
        if len(values) > 1 and np.any(values[1:] < values[:-1]):
            return None
        return values

    @staticmethod
    def _slice_published_lap_points(stream, lap, session_times: np.ndarray | None):
        """Slice one lap without copying the complete driver stream.

        FastF1's standard slicer copies the full stream for every lap and,
        when edge interpolation is requested, repeatedly re-merges it.  The
        archive stores every *published* sample, so synthetic boundary rows
        are unnecessary.  A binary search gives the exact same values as
        ``slice_by_lap(..., interpolate_edges=False)`` while work stays
        proportional to the selected lap instead of the complete session.
        """
        start = lap.get("LapStartTime")
        end = lap.get("Time")
        if session_times is None or pd.isna(start) or pd.isna(end):
            return stream.slice_by_lap(lap, interpolate_edges=False)
        start_value = pd.Timedelta(start).to_timedelta64()
        end_value = pd.Timedelta(end).to_timedelta64()
        left = int(np.searchsorted(session_times, start_value, side="left"))
        right = int(np.searchsorted(session_times, end_value, side="right"))
        result = stream.iloc[left:right].copy()
        if "Time" in result.columns:
            result.loc[:, "Time"] = result["SessionTime"] - pd.Timedelta(start)
        return result

    def iter_session_telemetry_laps(self, session_id: str) -> Iterator[dict[str, Any]]:
        """Yield lossless combined car/position telemetry one lap at a time."""
        year, _, _ = self.parse_session_id(session_id)
        if year < 2018:
            return []
        session = self.load_session(session_id, telemetry=True)
        # FastF1 3.8 converts ``require`` to a set internally.  Pandas 2.3+
        # rejects sets as indexers, so iterlaps(require=...) fails before the
        # first lap is yielded.  Indexed access still returns FastF1 ``Lap``
        # objects and is compatible with all supported pandas 2.x releases.
        extraction_errors = []

        def document(lap, telemetry, car_data, position_data) -> dict[str, Any]:
            # ``records`` has already cleaned every telemetry field.  Running
            # ``clean`` over this complete document again recursively walked
            # hundreds of thousands of point values a second time.  Clean
            # only the lap metadata here and reuse the lossless point rows.
            return {
                "session_id": session_id,
                "driver": clean(lap.get("Driver")),
                "driver_number": clean(lap.get("DriverNumber")),
                "lap": clean(lap.get("LapNumber")),
                "lap_time": clean(lap.get("LapTime")),
                "sector_1": clean(lap.get("Sector1Time")),
                "sector_2": clean(lap.get("Sector2Time")),
                "sector_3": clean(lap.get("Sector3Time")),
                "compound": clean(lap.get("Compound")),
                "is_accurate": clean(lap.get("IsAccurate")),
                # Persist every channel FastF1 publishes.  The API can
                # select display channels later without losing archive
                # fidelity or newly added upstream fields.
                "points": records(telemetry, list(telemetry.columns)),
                "car_points": records(car_data, list(car_data.columns)),
                "position_points": records(position_data, list(position_data.columns)),
            }

        # Merging car and position telemetry also computes DriverAhead and
        # distance channels. Doing that once per lap repeats the same costly
        # pandas work hundreds of times. Merge once per driver, then take
        # exact lap slices from the lossless driver-level streams.
        if hasattr(session.laps, "pick_drivers") and "Driver" in session.laps.columns:
            drivers = session.laps["Driver"].dropna().unique().tolist()
            for driver in drivers:
                driver_laps = session.laps.pick_drivers(driver)
                try:
                    merged = driver_laps.get_telemetry()
                    car_stream = driver_laps.get_car_data()
                    position_stream = driver_laps.get_pos_data()
                    merged_times = self._session_time_values(merged)
                    car_times = self._session_time_values(car_stream)
                    position_times = self._session_time_values(position_stream)
                except Exception as exc:
                    extraction_errors.append(f"{driver} all laps: {exc}")
                    continue
                for index in range(len(driver_laps)):
                    lap = driver_laps.iloc[index]
                    if pd.isna(lap.get("LapNumber")):
                        continue
                    try:
                        telemetry = self._slice_published_lap_points(
                            merged, lap, merged_times,
                        )
                        if "Distance" not in telemetry.columns:
                            telemetry = telemetry.add_distance()
                        if telemetry.empty:
                            continue
                        car_data = self._slice_published_lap_points(
                            car_stream, lap, car_times,
                        )
                        position_data = self._slice_published_lap_points(
                            position_stream, lap, position_times,
                        )
                        yield document(lap, telemetry, car_data, position_data)
                    except Exception as exc:
                        extraction_errors.append(
                            f"{driver} lap {lap.get('LapNumber')}: {exc}"
                        )
        else:
            # Small test doubles and older compatible FastF1-like adapters do
            # not expose the Laps bulk helpers. Keep the exact per-lap path as
            # a compatibility fallback.
            for index in range(len(session.laps)):
                lap = session.laps.iloc[index]
                if pd.isna(lap.get("Driver")) or pd.isna(lap.get("LapNumber")):
                    continue
                try:
                    telemetry = lap.get_telemetry()
                    if "Distance" not in telemetry.columns:
                        telemetry = telemetry.add_distance()
                    if telemetry.empty:
                        continue
                    yield document(
                        lap, telemetry, lap.get_car_data(), lap.get_pos_data(),
                    )
                except Exception as exc:
                    extraction_errors.append(
                        f"{lap.get('Driver')} lap {lap.get('LapNumber')}: {exc}"
                    )
        if extraction_errors:
            preview = "; ".join(extraction_errors[:3])
            raise RuntimeError(
                f"Telemetry extraction failed for {len(extraction_errors)} laps; {preview}"
            )

    def session_telemetry_laps(self, session_id: str) -> list[dict[str, Any]]:
        """Compatibility helper for callers that explicitly need a materialized list."""
        return list(self.iter_session_telemetry_laps(session_id))

    def _summary(self, session, _: dict[str, Any]) -> dict[str, Any]:
        try:
            total_laps = session.total_laps
        except Exception:
            total_laps = None
        try:
            drivers = list(session.drivers)
        except Exception:
            drivers = []
        return clean({
            "name": session.name, "date": session.date, "event": session.event.get("EventName"),
            "country": session.event.get("Country"), "location": session.event.get("Location"),
            "total_laps": total_laps, "drivers": drivers,
        })

    def _results(self, session, _: dict[str, Any]) -> list[dict[str, Any]]:
        return records(session.results)

    def _laps(self, session, _: dict[str, Any]) -> list[dict[str, Any]]:
        frame = session.laps.sort_values(["LapNumber", "Driver"])
        return records(frame)

    def _strategy(self, session, _: dict[str, Any]) -> list[dict[str, Any]]:
        if session.laps.empty:
            return []
        grouped = session.laps.groupby(["Driver", "Stint"], dropna=True).agg(
            compound=("Compound", "first"), start_lap=("LapNumber", "min"),
            end_lap=("LapNumber", "max"), laps=("LapNumber", "count"),
        ).reset_index()
        return records(grouped)

    def _weather(self, session, _: dict[str, Any]) -> list[dict[str, Any]]:
        return records(session.weather_data)

    def _race_control(self, session, _: dict[str, Any]) -> list[dict[str, Any]]:
        return records(session.race_control_messages)

    def _track(self, session, _: dict[str, Any]) -> dict[str, Any]:
        fastest = session.laps.pick_fastest()
        telemetry = fastest.get_telemetry()
        info = session.get_circuit_info()
        points = records(telemetry.iloc[::max(1, len(telemetry) // 800)], ["X", "Y", "Distance", "Speed"])
        return {"points": points, "rotation": clean(info.rotation) if info else 0,
                "corners": records(info.corners) if info else [],
                "marshal_lights": records(info.marshal_lights) if info else [],
                "marshal_sectors": records(info.marshal_sectors) if info else []}

    def _telemetry(self, session, options: dict[str, Any]) -> dict[str, Any]:
        drivers = [d.strip().upper() for d in options.get("drivers", "").split(",") if d.strip()][:2]
        if not drivers:
            drivers = list(session.results["Abbreviation"].dropna().head(1))
        requested = [
            c.strip()
            for c in options.get(
                "channels", "Speed,RPM,Throttle,Brake,nGear,DRS",
            ).split(",")
            if c.strip()
        ][:24]
        stream = options.get("stream", "merged")
        traces = []
        available_channels: set[str] = set()
        for driver in drivers:
            laps = session.laps.pick_drivers(driver)
            lap_choice = options.get("laps", "fastest")
            lap = laps.pick_fastest() if lap_choice == "fastest" else laps[laps["LapNumber"] == int(lap_choice)].iloc[0]
            if stream == "car":
                telemetry = lap.get_car_data()
            elif stream == "position":
                telemetry = lap.get_pos_data()
            else:
                telemetry = lap.get_telemetry()
            if stream == "merged" and "Distance" not in telemetry.columns:
                telemetry = telemetry.add_distance()
            available_channels.update(str(column) for column in telemetry.columns)
            allowed = [column for column in requested if column in telemetry.columns]
            stride = max(1, int(np.ceil(len(telemetry) / 1500)))
            columns = list(dict.fromkeys([
                column
                for column in (
                    "Distance", "Time", "SessionTime", "X", "Y", "Z", *allowed,
                )
                if column in telemetry.columns
            ]))
            traces.append({"driver": driver, "lap": clean(lap.get("LapNumber")),
                           "lap_time": clean(lap.get("LapTime")),
                           "points": records(telemetry.iloc[::stride], columns)})
        returned_channels = [channel for channel in requested if channel in available_channels]
        if stream == "merged" and len(traces) == 2:
            reference = traces[0]["points"]
            comparison = traces[1]["points"]
            ref_distance = np.array([point["Distance"] for point in reference if point.get("Distance") is not None])
            ref_time = np.array([point["Time"] for point in reference if point.get("Distance") is not None])
            if len(ref_distance):
                for point in comparison:
                    if point.get("Distance") is not None and point.get("Time") is not None:
                        point["Delta"] = point["Time"] - float(np.interp(point["Distance"], ref_distance, ref_time))
                traces[1]["comparison_note"] = f"Positive delta means {traces[1]['driver']} is behind {traces[0]['driver']}."
            returned_channels.append("Delta")
        return {
            "stream": stream,
            "channels": returned_channels,
            "available_channels": sorted(available_channels),
            "traces": traces,
        }

    @staticmethod
    def artifact_key(session_id: str, kind: str, options: dict[str, Any]) -> str:
        return artifact_key(session_id, kind, options)

    @staticmethod
    def bundle_key(session_id: str) -> str:
        return f"{ARTIFACT_VERSION}:{session_id}:core-bundle"
