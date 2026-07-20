"""Copy one season's non-telemetry data between MongoDB deployments.

The destination URL is read from ``DESTINATION_MONGODB_URL`` so credentials do
not appear in the command line. Existing documents are replaced by ``_id`` and
unrelated destination data is left untouched.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Iterator
from typing import Any

from pymongo import MongoClient, ReplaceOne


def season_queries(season: int, include_telemetry: bool) -> list[tuple[str, dict[str, Any]]]:
    session_prefix = f"^{season}-"
    queries: list[tuple[str, dict[str, Any]]] = [
        ("seasons", {"_id": season}),
        ("events", {"season": season}),
        ("sessions", {"season": season}),
        ("drivers", {"season": season}),
        ("constructors", {"season": season}),
        ("circuits", {}),
        ("standings", {"season": season}),
        ("results", {"session_id": {"$regex": session_prefix}}),
        ("laps", {"session_id": {"$regex": session_prefix}}),
        ("strategies", {"session_id": {"$regex": session_prefix}}),
        ("weather_samples", {"session_id": {"$regex": session_prefix}}),
        ("race_control_messages", {"session_id": {"$regex": session_prefix}}),
        ("artifacts", {"session_id": {"$regex": session_prefix}}),
        (
            "dataset_status",
            {
                "$or": [
                    {"subject": str(season)},
                    {"subject": {"$regex": session_prefix}},
                ]
            },
        ),
    ]
    if include_telemetry:
        queries.append(
            ("telemetry_laps", {"session_id": {"$regex": session_prefix}})
        )
    return queries


def batches(documents: Iterator[dict[str, Any]], size: int = 250) -> Iterator[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for document in documents:
        batch.append(document)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def replacement_filter(
    collection_name: str, document: dict[str, Any]
) -> dict[str, Any]:
    if collection_name == "dataset_status":
        return {
            "subject": document["subject"],
            "dataset": document["dataset"],
        }
    return {"_id": document["_id"]}


def replacement_document(
    collection_name: str, document: dict[str, Any]
) -> dict[str, Any]:
    if collection_name == "dataset_status":
        return {key: value for key, value in document.items() if key != "_id"}
    return document


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy one race-data season to another MongoDB deployment"
    )
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--include-telemetry", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source_url = os.getenv("SOURCE_MONGODB_URL", "mongodb://localhost:27017")
    destination_url = os.getenv("DESTINATION_MONGODB_URL")
    source_database = os.getenv("SOURCE_MONGODB_DATABASE", "race_data")
    destination_database = os.getenv("DESTINATION_MONGODB_DATABASE", "race_data")

    if not args.dry_run and not destination_url:
        parser.error("DESTINATION_MONGODB_URL is required unless --dry-run is used")
    if destination_url and any(ord(character) < 32 for character in destination_url):
        parser.error(
            "DESTINATION_MONGODB_URL contains a control character. Copy the URI "
            "again and load it with: $env:DESTINATION_MONGODB_URL = "
            "(Get-Clipboard).Trim()"
        )
    if destination_url and not destination_url.startswith(
        ("mongodb://", "mongodb+srv://")
    ):
        parser.error(
            "DESTINATION_MONGODB_URL must start with mongodb:// or mongodb+srv://"
        )

    source_client = MongoClient(source_url, serverSelectionTimeoutMS=10_000)
    source_client.admin.command("ping")
    source = source_client[source_database]

    destination_client = None
    destination = None
    if not args.dry_run:
        destination_client = MongoClient(destination_url, serverSelectionTimeoutMS=10_000)
        destination_client.admin.command("ping")
        destination = destination_client[destination_database]

    total = 0
    try:
        for collection_name, query in season_queries(
            args.season, args.include_telemetry
        ):
            source_collection = source[collection_name]
            count = source_collection.count_documents(query)
            total += count
            print(f"{collection_name}: {count} documents")
            if args.dry_run or not count:
                continue
            assert destination is not None
            for batch in batches(source_collection.find(query)):
                destination[collection_name].bulk_write(
                    [
                        ReplaceOne(
                            replacement_filter(collection_name, document),
                            replacement_document(collection_name, document),
                            upsert=True,
                        )
                        for document in batch
                    ],
                    ordered=False,
                )
        print(
            f"{'Would copy' if args.dry_run else 'Copied'} {total} documents "
            f"for season {args.season}"
        )
        return 0
    finally:
        source_client.close()
        if destination_client is not None:
            destination_client.close()


if __name__ == "__main__":
    raise SystemExit(main())
