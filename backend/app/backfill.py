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

from .circuit_catalog import sync_catalog_maps, sync_f1db_maps, sync_f1db_metadata
from .config import get_settings
from .contracts import ARCHIVE_SCHEMA_VERSION, artifact_key
from .fastf1_adapter import FastF1Adapter
from .ingestion import (
    find_circuit_for_session,
    persist_session_bundle,
    persist_telemetry,
    persist_track,
    sync_season,
    sync_standings,
)
from .jolpica_dump import HISTORICAL_DUMP_SCHEMA_VERSION
from .mongo import database, init_mongo, set_dataset_status, utcnow
from .serialization import TELEMETRY_POINTS_ENCODING, TELEMETRY_SCHEMA_VERSION


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
        self.historical_bulk_ready = False
        self.prune_fastf1_cache = False
        self.counts = {
            "seasons": 0,
            "standings": 0,
            "sessions": 0,
            "tracks": 0,
            "catalog_maps": 0,
            "svg_maps": 0,
            "circuit_metadata": 0,
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

    @staticmethod
    def telemetry_recorded(session_id: str) -> bool:
        state = database.dataset_status.find_one({
            "subject": session_id, "dataset": "telemetry",
        })
        if not state or state.get("schema_version") != TELEMETRY_SCHEMA_VERSION:
            return False
        if state.get("availability") == "unavailable":
            return True
        if state.get("availability") != "available":
            return False
        stored_laps = database.telemetry_laps.count_documents({
            "session_id": session_id,
        })
        expected_laps = database.laps.count_documents({
            "session_id": session_id,
        })
        return (
            stored_laps > 0
            and (expected_laps == 0 or stored_laps == expected_laps)
            and database.telemetry_laps.count_documents({
                "session_id": session_id,
                "$or": [
                    {"schema_version": {"$ne": TELEMETRY_SCHEMA_VERSION}},
                    {"distance_normalized": {"$ne": True}},
                    {"points_encoding": {"$ne": TELEMETRY_POINTS_ENCODING}},
                    {"car_points_encoding": {"$ne": TELEMETRY_POINTS_ENCODING}},
                    {"position_points_encoding": {"$ne": TELEMETRY_POINTS_ENCODING}},
                ],
            }) == 0
        )

    def sync_seasons(self) -> None:
        self.checkpoint("seasons")
        for year in range(self.start, self.end + 1):
            season_document = database.seasons.find_one({"_id": year})
            complete = all(
                self.dataset_recorded(str(year), dataset, require_available=True)
                for dataset in ("calendar", "drivers", "constructors", "circuits")
            )
            if (
                complete
                and season_document
                and season_document.get("schema_version") == ARCHIVE_SCHEMA_VERSION
            ):
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
                status_subject = f"{year}-{round_number}"
                document = database.standings.find_one({
                    "season": year, "round": round_number, "kind": kind,
                })
                if (
                    document
                    and document.get("data")
                    and self.dataset_recorded(
                        status_subject, f"{kind}_standings", require_available=True,
                    )
                ):
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

    def sync_circuit_maps(self) -> None:
        self.checkpoint("circuit-catalog")
        metadata = self.retry(
            "circuit-metadata", "all", lambda: sync_f1db_metadata(database),
        )
        if metadata is not None:
            self.counts["circuit_metadata"] += metadata["matched"]
            logger.info("circuit metadata stored=%s", metadata)
        result = self.retry("circuit-catalog", "all", lambda: sync_catalog_maps(database))
        if result is not None:
            self.counts["catalog_maps"] += result["matched"]
            logger.info("circuit catalog stored=%s", result)
        result = self.retry("circuit-svg", "all", lambda: sync_f1db_maps(database))
        if result is not None:
            self.counts["svg_maps"] += result["matched"]
            logger.info("circuit SVG archive stored=%s", result)

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
            expected_artifacts = [
                artifact_key(session_id, dataset, {})
                for dataset in ("summary", "results", "laps", "strategy", "weather", "race-control")
            ]
            current_artifacts = database.artifacts.count_documents({
                "_id": {"$in": expected_artifacts},
            }) == len(expected_artifacts)
            if year < 2018 and self.historical_bulk_ready:
                # Replace page-limited historical API artifacts from older
                # runs with the verified complete bulk-dump representation.
                current_artifacts = current_artifacts and database.artifacts.count_documents({
                    "_id": {"$in": expected_artifacts},
                    "payload.source": "Jolpica CSV database dump",
                    "payload.schema_version": HISTORICAL_DUMP_SCHEMA_VERSION,
                }) == len(expected_artifacts)
            if year >= 2018:
                needs_core = any(
                    not self.dataset_recorded(session_id, dataset, require_available=True)
                    for dataset in ("summary", "results", "laps", "strategy", "weather", "race-control")
                ) or not current_artifacts
            else:
                # Detailed timing is genuinely unavailable before 2018, but
                # every dataset must still have an explicit stored status.
                needs_core = (
                    not self.dataset_recorded(session_id, "summary", require_available=True)
                    or any(
                        not self.dataset_recorded(session_id, dataset)
                        for dataset in ("results", "laps", "strategy", "weather", "race-control")
                    )
                ) or not current_artifacts
            circuit = find_circuit_for_session(database, session_id) if year >= 2018 else None
            needs_track = (
                year >= 2018
                and row.get("code") in {"Q", "S", "R"}
                and not (circuit and circuit.get("map_data"))
                and not self.dataset_recorded(session_id, "track", require_available=True)
            )
            needs_telemetry = (
                # The dedicated timing runner owns expensive modern streams,
                # but pre-2018 sessions still need an explicit schema-versioned
                # unavailable record.  Otherwise a --without-telemetry archive
                # can never pass the completeness audit.
                (year < 2018 or self.include_telemetry)
                and not self.telemetry_recorded(session_id)
            )
            if not any((needs_core, needs_track, needs_telemetry)):
                if self.prune_fastf1_cache and year >= 2018 and self.telemetry_recorded(session_id):
                    try:
                        pruned = self.adapter.prune_session_cache(session_id)
                        if pruned["files"]:
                            logger.info("cache session=%s pruned=%s", session_id, pruned)
                    except Exception as exc:
                        logger.warning("cache session=%s prune failed: %s", session_id, exc)
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

            if self.prune_fastf1_cache and self.telemetry_recorded(session_id):
                try:
                    pruned = self.adapter.prune_session_cache(session_id)
                    if pruned["files"]:
                        logger.info("cache session=%s pruned=%s", session_id, pruned)
                except Exception as exc:
                    logger.warning("cache session=%s prune failed: %s", session_id, exc)

    def wait_for_modern_timing_runner(self) -> None:
        """Avoid duplicate FastF1 loads while the timing runner owns the range.

        Once a timing control exists, only its explicit ``completed`` phase
        releases the full archive runner.  A stale heartbeat may mean the
        machine slept or the dedicated process needs restarting; treating it
        as an expired lease lets both runners consume the same upstream quota
        and overwrite the same session cache concurrently.
        """
        control_id = f"timing_backfill:{max(2018, self.start)}:{self.end}"
        while True:
            control = database.sync_controls.find_one({"_id": control_id})
            if not control or control.get("phase") == "completed":
                return
            self.checkpoint("waiting-for-modern-timing", control.get("subject"))
            time.sleep(30)

    def purge_obsolete_artifacts(self) -> None:
        """Remove superseded v3 payloads only after v4 coverage is loaded."""
        self.checkpoint("artifact-cleanup")
        result = database.artifacts.delete_many({"_id": {"$regex": "^v3:"}})
        logger.info("removed %d superseded v3 artifacts", result.deleted_count)

    def run(self) -> int:
        init_mongo()
        database.backfill_failures.create_index(
            [("run", 1), ("phase", 1), ("subject", 1)], unique=True
        )
        self.checkpoint("starting")
        if self.start < 2018:
            dump = self.retry(
                "historical-dump", "all", self.adapter.prepare_historical_dump,
            )
            if dump is not None:
                self.historical_bulk_ready = True
                logger.info("historical Jolpica dump ready=%s", dump)
        self.sync_seasons()
        self.sync_circuit_maps()
        self.sync_round_standings()
        sessions = self.completed_sessions()
        historical = [row for row in sessions if int(row["season"]) < 2018]
        modern = [row for row in sessions if int(row["season"]) >= 2018]
        self.sync_session_data(historical)
        self.wait_for_modern_timing_runner()
        self.sync_session_data(modern)
        self.purge_obsolete_artifacts()
        unresolved = database.backfill_failures.count_documents({"run": self.control_id})
        # Completion is based on the stored database, not merely reaching the
        # end of the loop.  The deep audit decompresses every telemetry lap and
        # verifies all expected season/session artifacts, standings and maps.
        from .audit_archive import audit

        coverage = audit(self.start, self.end, deep=True)
        status = (
            "completed"
            if unresolved == 0 and coverage["complete"]
            else "completed_with_errors"
            if unresolved
            else "completed_with_gaps"
        )
        database.sync_controls.update_one(
            {"_id": self.control_id},
            {"$set": {
                "active": False,
                "phase": status,
                "subject": None,
                "counts": self.counts,
                "unresolved_failures": unresolved,
                "audit_complete": coverage["complete"],
                "audit_counts": coverage["counts"],
                "audit_problem_counts": coverage["problem_counts"],
                "completed_at": utcnow(),
                "updated_at": utcnow(),
            }},
        )
        logger.info("archive backfill %s counts=%s unresolved_failures=%d", status, self.counts, unresolved)
        return 0 if unresolved == 0 and coverage["complete"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Store all supported Formula 1 datasets for a season range")
    parser.add_argument("--start", type=int, default=2015)
    parser.add_argument("--end", type=int, default=utcnow().year)
    parser.add_argument("--without-telemetry", action="store_true")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--until-complete", action="store_true",
        help="rerun incomplete audited passes until the archive is complete",
    )
    parser.add_argument("--retry-delay", type=int, default=60)
    args = parser.parse_args()
    if args.start < 1950 or args.end < args.start:
        parser.error("season range must begin in 1950 or later and end at or after start")
    while True:
        result = ArchiveBackfill(
            args.start, args.end,
            include_telemetry=not args.without_telemetry,
            retries=max(1, args.retries),
        ).run()
        if result == 0 or not args.until_complete:
            return result
        delay = max(5, args.retry_delay)
        logger.warning("archive audit is incomplete; starting another resumable pass in %ds", delay)
        time.sleep(delay)


if __name__ == "__main__":
    raise SystemExit(main())
