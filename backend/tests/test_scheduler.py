from datetime import timedelta

from app.mongo import claim_job, database, queue_job, utcnow
from app.scheduler import current_session_due


def setup_function():
    for name in database.list_collection_names():
        database[name].delete_many({})


def test_job_queue_is_idempotent_while_work_is_pending():
    first = queue_job(database, "season", "season:2025", {"season": 2025})
    second = queue_job(database, "season", "season:2025", {"season": 2025})
    assert first["_id"] == second["_id"]
    assert database.jobs.count_documents({"key": "season:2025"}) == 1


def test_existing_job_can_be_promoted_and_is_claimed_first():
    queued = queue_job(database, "season", "season:2025", {"season": 2025})
    queue_job(database, "track", "track:2025-1-Q", {"session_id": "2025-1-Q"})
    promoted = queue_job(
        database, "season", "season:2025", {"season": 2025}, priority=100,
    )
    assert promoted["_id"] == queued["_id"]
    assert claim_job(database)["_id"] == queued["_id"]


def test_finalized_session_is_not_downloaded_forever():
    now = utcnow()
    session = {"_id": "2025-1-R", "code": "R", "starts_at": now - timedelta(days=3)}
    database.dataset_status.insert_one({
        "subject": session["_id"], "dataset": "summary", "availability": "available",
        "last_synced_at": now - timedelta(days=2),
    })
    assert current_session_due(session, now) is False


def test_recent_session_gets_a_finalization_refresh():
    now = utcnow()
    session = {"_id": "2026-1-Q", "code": "Q", "starts_at": now - timedelta(hours=8)}
    database.dataset_status.insert_one({
        "subject": session["_id"], "dataset": "summary", "availability": "available",
        "last_synced_at": now - timedelta(hours=3),
    })
    assert current_session_due(session, now) is True
