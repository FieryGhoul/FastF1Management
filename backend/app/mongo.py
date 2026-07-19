import uuid
from datetime import datetime, timezone
from typing import Any, Generator

from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.database import Database

from .config import get_settings


settings = get_settings()


def _create_client():
    if settings.mongodb_url.startswith("mongomock://"):
        import mongomock

        return mongomock.MongoClient(tz_aware=True)
    return MongoClient(settings.mongodb_url, tz_aware=True, connectTimeoutMS=5000)


client = _create_client()
database: Database = client[settings.mongodb_database]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_db() -> Generator[Database, None, None]:
    yield database


def init_mongo() -> None:
    client.admin.command("ping")
    database.seasons.create_index([("year", DESCENDING)], unique=True)
    database.events.create_index([("season", ASCENDING), ("round", ASCENDING)], unique=True)
    database.events.create_index([("circuit_id", ASCENDING), ("season", DESCENDING)])
    database.sessions.create_index([("event_id", ASCENDING), ("starts_at", ASCENDING)])
    database.sessions.create_index([("season", ASCENDING), ("round", ASCENDING), ("code", ASCENDING)], unique=True)
    database.drivers.create_index([("season", ASCENDING), ("driverId", ASCENDING)], unique=True)
    database.constructors.create_index([("season", ASCENDING), ("constructorId", ASCENDING)], unique=True)
    database.circuits.create_index("external_id", unique=True)
    database.circuits.create_index([("country", ASCENDING), ("name", ASCENDING)])
    database.standings.create_index([("season", ASCENDING), ("round", ASCENDING), ("kind", ASCENDING)], unique=True)
    database.results.create_index([("session_id", ASCENDING), ("Position", ASCENDING)])
    database.laps.create_index([("session_id", ASCENDING), ("Driver", ASCENDING), ("LapNumber", ASCENDING)], unique=True)
    database.strategies.create_index([("session_id", ASCENDING), ("Driver", ASCENDING), ("Stint", ASCENDING)], unique=True)
    database.weather_samples.create_index([("session_id", ASCENDING), ("Time", ASCENDING)])
    database.race_control_messages.create_index([("session_id", ASCENDING), ("Time", ASCENDING)])
    database.telemetry_laps.create_index([("session_id", ASCENDING), ("driver", ASCENDING), ("lap", ASCENDING)], unique=True)
    database.telemetry_laps.create_index([("session_id", ASCENDING), ("driver", ASCENDING), ("lap_time", ASCENDING)])
    database.artifacts.create_index([("session_id", ASCENDING), ("kind", ASCENDING)])
    database.jobs.create_index(
        [("status", ASCENDING), ("priority", DESCENDING), ("scheduled_for", ASCENDING), ("created_at", ASCENDING)]
    )
    database.jobs.create_index([("key", ASCENDING), ("created_at", DESCENDING)])
    database.dataset_status.create_index([("subject", ASCENDING), ("dataset", ASCENDING)], unique=True)
    database.admin_users.create_index("username", unique=True)
    database.admin_sessions.create_index("expires_at", expireAfterSeconds=0)


def public_document(document: dict[str, Any] | None) -> dict[str, Any] | None:
    if document is None:
        return None
    result = dict(document)
    result.pop("_id", None)
    return result


def queue_job(
    db: Database,
    kind: str,
    key: str,
    payload: dict[str, Any],
    *,
    scheduled_for: datetime | None = None,
    priority: int = 0,
) -> dict[str, Any]:
    existing = db.jobs.find_one({"key": key, "status": {"$in": ["queued", "running"]}})
    if existing:
        if priority > int(existing.get("priority", 0)):
            db.jobs.update_one(
                {"_id": existing["_id"]},
                {"$set": {"priority": priority, "updated_at": utcnow()}},
            )
            existing["priority"] = priority
        return existing
    now = utcnow()
    document = {
        "_id": str(uuid.uuid4()),
        "kind": kind,
        "key": key,
        "payload": payload,
        "status": "queued",
        "progress": 0,
        "error": None,
        "attempts": 0,
        "priority": priority,
        "scheduled_for": scheduled_for or now,
        "created_at": now,
        "updated_at": now,
    }
    db.jobs.insert_one(document)
    return document


def claim_job(db: Database) -> dict[str, Any] | None:
    now = utcnow()
    return db.jobs.find_one_and_update(
        {"status": "queued", "scheduled_for": {"$lte": now}},
        {"$set": {"status": "running", "progress": 5, "updated_at": now}, "$inc": {"attempts": 1}},
        sort=[("priority", DESCENDING), ("scheduled_for", ASCENDING), ("created_at", ASCENDING)],
        return_document=ReturnDocument.AFTER,
    )


def set_dataset_status(
    db: Database,
    subject: str,
    dataset: str,
    availability: str,
    *,
    source: str,
    reason: str | None = None,
    checksum: str | None = None,
) -> None:
    now = utcnow()
    db.dataset_status.update_one(
        {"subject": subject, "dataset": dataset},
        {"$set": {
            "subject": subject,
            "dataset": dataset,
            "availability": availability,
            "unavailable_reason": reason,
            "source": source,
            "checksum": checksum,
            "last_synced_at": now,
            "updated_at": now,
        }},
        upsert=True,
    )
