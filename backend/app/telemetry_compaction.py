"""Preview or apply the compact telemetry schema migration."""

from __future__ import annotations

import argparse
import json

from .ingestion import migrate_telemetry_schema
from .mongo import database, init_mongo
from .serialization import TELEMETRY_SCHEMA_VERSION


def compaction_query() -> dict:
    return {
        "$or": [
            {"schema_version": {"$ne": TELEMETRY_SCHEMA_VERSION}},
            {"points_compressed": {"$exists": True}},
            {"points": {"$exists": True}},
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove merged telemetry and retain compact car/distance streams",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the irreversible conversion; otherwise only report",
    )
    args = parser.parse_args()
    init_mongo()
    pending = database.telemetry_laps.count_documents(compaction_query())
    if not args.apply:
        print(json.dumps({
            "applied": False,
            "pending_telemetry_laps": pending,
            "target_schema_version": TELEMETRY_SCHEMA_VERSION,
        }, indent=2))
        return 0
    result = migrate_telemetry_schema(database, progress=print)
    print(json.dumps({"applied": True, **result}, indent=2))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
