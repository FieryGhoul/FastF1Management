"""Resumable full-archive ingestion for a bounded range of Formula 1 seasons.

Run from the backend directory with::

    python -m app.backfill --start 2015 --end 2026

The normal scheduler intentionally trickles historical work into the queue.  This
command is for an operator-requested archive fill and processes every supported
dataset while checkpointing progress in ``sync_controls``.
"""

from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

from .config import get_settings
from .fastf1_adapter import FastF1Adapter
from .ingestion import persist_session_bundle, persist_telemetry, persist_track, sync_season, sync_standings
from .mongo import database, init_mongo, set_dataset_status, utcnow


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("race-backfill")
T = TypeVar("T")


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


class ArchiveBackfill:
    def __init__(self, start: int, end: int, *, include_telemetry: bool = True, retries: int = 3):
        self.start = start
        self.end = end
        self.include_telemetry = include_telemetry
        self.retries = retries
        self.control_id = f"archive_backfill:{start}:{end}"
        self.adapter = FastF1Adapter(get_settings().fastf1_cache)
        self.counts = {
            "seasons": 0,
            "standings": 0,
            "sessions": 0,
            "tracks": 0,
            "telemetry_sessions": 0,
            "telemetry_laps": 0,
            "skipped": 0,
            "failed": 0,
        }

    def checkpoint(self, phase: str, subject: str | None = None, **extra: Any) -> None:
        database.sync_controls.update_one(
            {"_id": self.control_id},
            {"$set": {
                "active": True,
                "start": self.start,
                "end": self.end,
                "include_telemetry": self.include_telemetry,
                "phase": phase,
                "subject": subject,
                "counts": self.counts,
                "updated_at": utcnow(),
                **extra,
            }, "$setOnInsert": {"started_at": utcnow()}},
            upsert=True,
        )

    def record_failure(self, phase: str, subject: str, exc: Exception) -> None:
        self.counts["failed"] += 1
        database.backfill_failures.update_one(
            {"run": self.control_id, "phase": phase, "subject": subject},
            {"$set": {
                "run": self.control_id,
                "phase": phase,
                "subject": subject,
                "error": str(exc),
                "updated_at": utcnow(),
            }, "$inc": {"occurrences": 1}, "$setOnInsert": {"created_at": utcnow()}},
            upsert=True,
        )
        logger.error("phase=%s subject=%s failed: %s", phase, subject, exc)

    def retry(self, phase: str, subject: str, operation: Callable[[], T]) -> T | None:
        attempt = 1
        quota_waits = 0
        while attempt <= self.retries:
            try:
                result = operation()
                database.backfill_failures.delete_one(
                    {"run": self.control_id, "phase": phase, "subject": subject}
                )
                return result
            except Exception as exc:
                message = str(exc).lower()
                if any(marker in message for marker in ("space quota", "storage limit", "disk space")):
                    logger.critical(
                        "phase=%s subject=%s stopped because MongoDB storage is full: %s",
                        phase, subject, exc,
                    )
                    raise
                if ("500 calls/h" in message or "too many requests" in message) and quota_waits < 12:
                    quota_waits += 1
                    logger.warning(
                        "phase=%s subject=%s upstream quota reached; waiting 5 minutes (%d/12)",
                        phase, subject, quota_waits,
                    )
                    time.sleep(300)
                    continue
                if attempt == self.retries:
                    self.record_failure(phase, subject, exc)
                    return None
                delay = min(30, 2 ** attempt)
                logger.warning(
                    "phase=%s subject=%s attempt=%d/%d failed; retrying in %ds: %s",
                    phase, subject, attempt, self.retries, delay, exc,
                )
                time.sleep(delay)
                attempt += 1
        return None

    @staticmethod
    def dataset_recorded(subject: str, dataset: str, *, require_available: bool = False) -> bool:
        state = database.dataset_status.find_one({"subject": subject, "dataset": dataset})
        if not state:
            return False
        return not require_available or state.get("availability") == "available"

    def sync_seasons(self) -> None:
        self.checkpoint("seasons")
        for year in range(self.start, self.end + 1):
            complete = all(
                self.dataset_recorded(str(year), dataset, require_available=True)
                for dataset in ("calendar", "drivers", "constructors", "circuits")
            )
            if complete and database.seasons.find_one({"_id": year}):
                self.counts["skipped"] += 1
                continue
            self.checkpoint("seasons", str(year))
            result = self.retry("seasons", str(year), lambda year=year: sync_season(database, self.adapter, year))
            if result is not None:
                self.counts["seasons"] += 1
                logger.info("season=%d stored=%s", year, result)

    def completed_sessions(self) -> list[dict[str, Any]]:
        cutoff = utcnow() - timedelta(hours=3)
        sessions = []
        for row in database.sessions.find(
            {"season": {"$gte": self.start, "$lte": self.end}},
            {"_id": 1, "season": 1, "round": 1, "code": 1, "starts_at": 1},
        ).sort([("season", 1), ("round", 1), ("starts_at", 1)]):
            starts = parse_datetime(row.get("starts_at"))
            if starts and starts < cutoff:
                sessions.append(row)
        return sessions

    def sync_round_standings(self) -> None:
        self.checkpoint("standings")
        completed_races = {
            (int(row["season"]), int(row["round"]))
            for row in self.completed_sessions()
            if row.get("code") == "R"
        }
        for year, round_number in sorted(completed_races):
            for kind in ("drivers", "constructors"):
                subject = f"{year}:{round_number}:{kind}"
                if database.standings.find_one({"season": year, "round": round_number, "kind": kind}):
                    self.counts["skipped"] += 1
                    continue
                self.checkpoint("standings", subject)
                result = self.retry(
                    "standings", subject,
                    lambda year=year, round_number=round_number, kind=kind: sync_standings(
                        database, self.adapter, year, round_number, kind
                    ),
                )
                if result is not None:
                    self.counts["standings"] += 1

    def sync_session_data(self, sessions: list[dict[str, Any]]) -> None:
        """Store all session datasets while reusing one fully loaded session.

        FastF1's combined timing/car/position load is the expensive operation.
        Keeping core, track and telemetry adjacent also keeps the session in the
        adapter's small in-memory LRU and avoids parsing the same cache files
        three separate times.
        """
        self.checkpoint("session-data", total=len(sessions))
        for index, row in enumerate(sessions, 1):
            session_id = row["_id"]
            year = int(row["season"])
            needs_core = not self.dataset_recorded(session_id, "summary", require_available=True)
            needs_track = (
                year >= 2018
                and row.get("code") in {"Q", "S", "R"}
                and not self.dataset_recorded(session_id, "track", require_available=True)
            )
            needs_telemetry = (
                self.include_telemetry
                and year >= 2018
                and not self.dataset_recorded(session_id, "telemetry")
            )
            if not any((needs_core, needs_track, needs_telemetry)):
                self.counts["skipped"] += 1
                continue

            self.checkpoint("session-data", session_id, position=index, total=len(sessions))

            # Preload every required channel once.  Historical sessions use
            # Jolpica classifications and must not be sent through FastF1.
            if year >= 2018:
                loaded = self.retry(
                    "session-load", session_id,
                    lambda session_id=session_id: self.adapter.load_session(
                        session_id, telemetry=needs_track or needs_telemetry
                    ),
                )
                if loaded is None:
                    continue

            if needs_core:
                result = self.retry(
                    "sessions", session_id,
                    lambda session_id=session_id: persist_session_bundle(database, self.adapter, session_id),
                )
                if result is not None:
                    self.counts["sessions"] += 1
                    logger.info("session=%s stored=%s", session_id, result)

            if needs_track:
                result = self.retry(
                    "tracks", session_id,
                    lambda session_id=session_id: persist_track(database, self.adapter, session_id),
                )
                if result is not None:
                    self.counts["tracks"] += 1

            if needs_telemetry:
                result = self.retry(
                    "telemetry", session_id,
                    lambda session_id=session_id: persist_telemetry(database, self.adapter, session_id),
                )
                if result is not None:
                    self.counts["telemetry_sessions"] += 1
                    self.counts["telemetry_laps"] += result
                    logger.info("telemetry session=%s laps=%d", session_id, result)

    def run(self) -> int:
        init_mongo()
        database.backfill_failures.create_index(
            [("run", 1), ("phase", 1), ("subject", 1)], unique=True
        )
        self.checkpoint("starting")
        self.sync_seasons()
        self.sync_round_standings()
        sessions = self.completed_sessions()
        self.sync_session_data(sessions)
        unresolved = database.backfill_failures.count_documents({"run": self.control_id})
        status = "completed" if unresolved == 0 else "completed_with_errors"
        database.sync_controls.update_one(
            {"_id": self.control_id},
            {"$set": {
                "active": False,
                "phase": status,
                "subject": None,
                "counts": self.counts,
                "unresolved_failures": unresolved,
                "completed_at": utcnow(),
                "updated_at": utcnow(),
            }},
        )
        logger.info("archive backfill %s counts=%s unresolved_failures=%d", status, self.counts, unresolved)
        return 0 if unresolved == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Store all supported Formula 1 datasets for a season range")
    parser.add_argument("--start", type=int, default=2015)
    parser.add_argument("--end", type=int, default=utcnow().year)
    parser.add_argument("--without-telemetry", action="store_true")
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()
    if args.start < 1950 or args.end < args.start:
        parser.error("season range must begin in 1950 or later and end at or after start")
    return ArchiveBackfill(
        args.start, args.end,
        include_telemetry=not args.without_telemetry,
        retries=max(1, args.retries),
    ).run()


if __name__ == "__main__":
    raise SystemExit(main())
