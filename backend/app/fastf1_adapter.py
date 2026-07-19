import hashlib
import re
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fastf1
import numpy as np
import pandas as pd
from fastf1.ergast import Ergast

from .serialization import clean, records


SESSION_CODES = {
    "Practice 1": "FP1", "Practice 2": "FP2", "Practice 3": "FP3",
    "Qualifying": "Q", "Sprint Qualifying": "SQ", "Sprint Shootout": "SS",
    "Sprint": "S", "Race": "R",
}
ARTIFACT_VERSION = "v3"


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


class FastF1Adapter:
    def __init__(self, cache_dir: Path):
        cache_dir.mkdir(parents=True, exist_ok=True)
        fastf1.Cache.enable_cache(str(cache_dir))
        self.ergast = Ergast(result_type="pandas", auto_cast=True, limit=1000)
        self._loaded_sessions: OrderedDict[str, tuple[Any, bool]] = OrderedDict()

    def schedule(self, year: int) -> list[dict[str, Any]]:
        frame = fastf1.get_event_schedule(year, include_testing=False)
        output: list[dict[str, Any]] = []
        for _, row in frame.iterrows():
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
                "sessions": sessions,
            })
        return output

    def standings(self, year: int, kind: str, round_number: int | None = None) -> list[dict[str, Any]]:
        kwargs = {"season": year, "round": round_number}
        response = (self.ergast.get_driver_standings(**kwargs) if kind == "drivers"
                    else self.ergast.get_constructor_standings(**kwargs))
        if not response.content:
            return []
        return records(response.content[0])

    def circuits(self, year: int | None = None) -> list[dict[str, Any]]:
        response = self.ergast.get_circuits(season=year)
        return records(response)

    def drivers(self, year: int) -> list[dict[str, Any]]:
        response = self.ergast.get_driver_info(season=year)
        return records(response)

    def constructors(self, year: int) -> list[dict[str, Any]]:
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
        while len(self._loaded_sessions) > 2:
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
        endpoint = {
            "R": self.ergast.get_race_results,
            "Q": self.ergast.get_qualifying_results,
            "S": self.ergast.get_sprint_results,
        }.get(code)
        response = endpoint(season=year, round=round_number) if endpoint else None
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
    def _historical_results(frame: pd.DataFrame) -> list[dict[str, Any]]:
        rows = []
        for _, row in frame.iterrows():
            rows.append(clean({
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
        columns = ["Position", "GridPosition", "Abbreviation", "FullName", "DriverNumber", "TeamName",
                   "TeamColor", "Points", "Status", "Time", "Q1", "Q2", "Q3"]
        return records(session.results, columns)

    def _laps(self, session, _: dict[str, Any]) -> list[dict[str, Any]]:
        columns = ["Driver", "DriverNumber", "Team", "Position", "LapNumber", "LapTime",
                   "Sector1Time", "Sector2Time", "Sector3Time", "Compound", "TyreLife", "Stint",
                   "PitInTime", "PitOutTime", "TrackStatus", "IsAccurate", "Deleted", "DeletedReason",
                   "SpeedI1", "SpeedI2", "SpeedFL", "SpeedST"]
        frame = session.laps.sort_values(["LapNumber", "Driver"]).head(4000)
        return records(frame, columns)

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
        requested = [c.strip() for c in options.get("channels", "Speed,RPM,Throttle,Brake,nGear,DRS").split(",")]
        allowed = [c for c in requested if c in {"Speed", "RPM", "Throttle", "Brake", "nGear", "DRS"}]
        traces = []
        for driver in drivers:
            laps = session.laps.pick_drivers(driver)
            lap_choice = options.get("laps", "fastest")
            lap = laps.pick_fastest() if lap_choice == "fastest" else laps[laps["LapNumber"] == int(lap_choice)].iloc[0]
            telemetry = lap.get_telemetry().add_distance()
            stride = max(1, int(np.ceil(len(telemetry) / 1500)))
            columns = ["Distance", "Time", "X", "Y", *allowed]
            traces.append({"driver": driver, "lap": clean(lap.get("LapNumber")),
                           "lap_time": clean(lap.get("LapTime")),
                           "points": records(telemetry.iloc[::stride], columns)})
        if len(traces) == 2:
            reference = traces[0]["points"]
            comparison = traces[1]["points"]
            ref_distance = np.array([point["Distance"] for point in reference if point.get("Distance") is not None])
            ref_time = np.array([point["Time"] for point in reference if point.get("Distance") is not None])
            if len(ref_distance):
                for point in comparison:
                    if point.get("Distance") is not None and point.get("Time") is not None:
                        point["Delta"] = point["Time"] - float(np.interp(point["Distance"], ref_distance, ref_time))
                traces[1]["comparison_note"] = f"Positive delta means {traces[1]['driver']} is behind {traces[0]['driver']}."
        return {"channels": [*allowed, "Delta"] if len(traces) == 2 else allowed, "traces": traces}

    @staticmethod
    def artifact_key(session_id: str, kind: str, options: dict[str, Any]) -> str:
        suffix = hashlib.sha1(repr(sorted(options.items())).encode(), usedforsecurity=False).hexdigest()[:12]
        return f"{ARTIFACT_VERSION}:{session_id}:{kind}:{suffix}"

    @staticmethod
    def bundle_key(session_id: str) -> str:
        return f"{ARTIFACT_VERSION}:{session_id}:core-bundle"
