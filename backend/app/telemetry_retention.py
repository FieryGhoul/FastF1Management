"""Inspect or apply the race-only durable telemetry retention policy."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterator
from typing import Any

from pymongo.database import Database

from .contracts import stores_persistent_telemetry
from .mongo import database, init_mongo


def _batches(values: list[str], size: int) -> Iterator[list[str]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def prune_non_race_telemetry(
    db: Database,
    *,
    apply: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    session_ids = sorted(
        str(session_id)
        for session_id in db.telemetry_laps.distinct("session_id")
        if session_id and not stores_persistent_telemetry(str(session_id))
    )
    row_count = db.telemetry_laps.count_documents({
        "session_id": {"$in": session_ids},
    }) if session_ids else 0
    status_subjects = sorted(
        str(subject)
        for subject in db.dataset_status.distinct("subject", {"dataset": "telemetry"})
        if subject and not stores_persistent_telemetry(str(subject))
    )
    result: dict[str, Any] = {
        "applied": apply,
        "non_race_sessions": len(session_ids),
        "telemetry_laps": row_count,
        "telemetry_statuses": len(status_subjects),
    }
    if apply:
        deleted_rows = 0
        for index, session_id in enumerate(session_ids, 1):
            deleted_rows += db.telemetry_laps.delete_many({
                "session_id": session_id,
            }).deleted_count
            if progress and (index == len(session_ids) or index % 10 == 0):
                progress(
                    f"telemetry sessions: {index}/{len(session_ids)}; "
                    f"deleted laps: {deleted_rows}",
                )
        deleted_statuses = 0
        status_batches = list(_batches(status_subjects, 100))
        for index, subjects in enumerate(status_batches, 1):
            deleted_statuses += db.dataset_status.delete_many({
                "subject": {"$in": subjects},
                "dataset": "telemetry",
            }).deleted_count
            if progress:
                progress(
                    f"status batches: {index}/{len(status_batches)}; "
                    f"deleted statuses: {deleted_statuses}",
                )
        result.update({
            "deleted_telemetry_laps": deleted_rows,
            "deleted_telemetry_statuses": deleted_statuses,
        })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove durably stored telemetry for non-race sessions",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform deletion; without this flag the command only reports",
    )
    args = parser.parse_args()
    init_mongo()
    print(json.dumps(prune_non_race_telemetry(
        database,
        apply=args.apply,
        progress=print if args.apply else None,
    ), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
