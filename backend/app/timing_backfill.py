"""Parallel 2018+ FastF1 timing and telemetry archive runner.

This runner is safe to use beside :mod:`app.backfill` while that process is
fetching Jolpica standings and pre-2018 classifications.  The full runner waits
for this checkpoint before entering its own modern-session phase.
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import Any

from .backfill import ArchiveBackfill
from .contracts import artifact_key
from .ingestion import migrate_telemetry_schema
from .mongo import database, init_mongo, utcnow
from .serialization import TELEMETRY_SCHEMA_VERSION


logger = logging.getLogger("race-timing-backfill")
CORE_DATASETS = ("summary", "results", "laps", "strategy", "weather", "race-control")


class ModernTimingBackfill(ArchiveBackfill):
    def __init__(self, start: int, end: int, *, retries: int = 3):
        super().__init__(max(2018, start), end, include_telemetry=True, retries=retries)
        self.control_id = f"timing_backfill:{self.start}:{self.end}"
        self.prune_fastf1_cache = True

    def wait_for_season_index(self) -> None:
        expected = self.end - self.start + 1
        while database.seasons.count_documents({
            "_id": {"$gte": self.start, "$lte": self.end},
        }) < expected:
            self.checkpoint("waiting-for-season-index")
            time.sleep(10)

    @staticmethod
    def coverage_gaps(sessions: list[dict[str, Any]]) -> list[str]:
        session_ids = [row["_id"] for row in sessions]
        statuses = {
            (row.get("subject"), row.get("dataset")): row
            for row in database.dataset_status.find(
                {"subject": {"$in": session_ids}},
                {"subject": 1, "dataset": 1, "availability": 1, "schema_version": 1},
            )
        }
        artifact_ids = set(database.artifacts.distinct(
            "_id", {"session_id": {"$in": session_ids}},
        ))
        gaps = []
        for session_id in session_ids:
            for dataset in CORE_DATASETS:
                state = statuses.get((session_id, dataset)) or {}
                if state.get("availability") != "available":
                    gaps.append(f"{session_id}:{dataset}")
                elif artifact_key(session_id, dataset, {}) not in artifact_ids:
                    gaps.append(f"{session_id}:{dataset}:artifact")
            telemetry_state = statuses.get((session_id, "telemetry")) or {}
            if (
                telemetry_state.get("availability") not in {"available", "unavailable"}
                or telemetry_state.get("schema_version") != TELEMETRY_SCHEMA_VERSION
                or not ModernTimingBackfill.telemetry_recorded(session_id)
            ):
                gaps.append(f"{session_id}:telemetry")
        return gaps

    def run_timing(self) -> int:
        init_mongo()
        migration = migrate_telemetry_schema(database)
        if migration["migrated"] or migration["failed"]:
            logger.info("telemetry schema migration=%s", migration)
        database.backfill_failures.create_index(
            [("run", 1), ("phase", 1), ("subject", 1)], unique=True,
        )
        self.checkpoint("starting")
        self.wait_for_season_index()
        sessions = self.completed_sessions()
        sessions.sort(
            key=lambda row: (int(row["season"]), int(row["round"]), str(row.get("starts_at", ""))),
            reverse=True,
        )
        self.sync_session_data(sessions)
        gaps = self.coverage_gaps(sessions)
        unresolved = database.backfill_failures.count_documents({"run": self.control_id})
        complete = not gaps and unresolved == 0
        database.sync_controls.update_one(
            {"_id": self.control_id},
            {"$set": {
                "active": False,
                "phase": "completed" if complete else "completed_with_gaps",
                "subject": None,
                "counts": self.counts,
                "coverage_gaps": len(gaps),
                "unresolved_failures": unresolved,
                "completed_at": utcnow(),
                "updated_at": utcnow(),
            }},
        )
        logger.info(
            "modern timing backfill complete=%s gaps=%d failures=%d counts=%s",
            complete, len(gaps), unresolved, self.counts,
        )
        return 0 if complete else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Store all 2018+ FastF1 timing and telemetry")
    parser.add_argument("--start", type=int, default=2018)
    parser.add_argument("--end", type=int, default=utcnow().year)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--until-complete", action="store_true")
    parser.add_argument("--retry-delay", type=int, default=60)
    args = parser.parse_args()
    if args.end < max(2018, args.start):
        parser.error("timing range must include at least one season from 2018 onward")
    while True:
        runner = ModernTimingBackfill(args.start, args.end, retries=max(1, args.retries))
        result = runner.run_timing()
        if result == 0 or not args.until_complete:
            return result
        delay = max(5, args.retry_delay)
        # Keep the ownership lease active while waiting to retry.  The full
        # archive runner uses this lease to avoid writing the same FastF1
        # session/cache concurrently.
        runner.checkpoint("retry-wait", retry_in_seconds=delay)
        logger.warning("timing coverage is incomplete; retrying in %ds", delay)
        time.sleep(delay)


if __name__ == "__main__":
    raise SystemExit(main())
