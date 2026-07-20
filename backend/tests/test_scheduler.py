from datetime import timedelta

from app.mongo import claim_job, database, queue_job, recover_stale_jobs, utcnow
from app.scheduler import current_session_due, schedule_historical_backfill, schedule_once


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


def test_stale_running_job_is_recovered_after_worker_restart():
    job = queue_job(database, "season", "season:2025", {"season": 2025})
    database.jobs.update_one(
        {"_id": job["_id"]},
        {"$set": {"status": "running", "updated_at": utcnow() - timedelta(hours=1)}},
    )

    assert recover_stale_jobs(database) == 1
    recovered = database.jobs.find_one({"_id": job["_id"]})
    assert recovered["status"] == "queued"
    assert recovered["progress"] == 0
    assert "previous worker stopped" in recovered["error"]


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


def test_queue_backfill_pauses_while_dedicated_archive_runner_is_active():
    database.sync_controls.insert_one({
        "_id": "archive_backfill:2000:2026", "active": True, "updated_at": utcnow(),
    })
    counts = {"season": 0, "session": 0, "track": 0, "telemetry": 0, "backfill": 0}

    schedule_historical_backfill(counts)

    assert counts == {"season": 0, "session": 0, "track": 0, "telemetry": 0, "backfill": 0}
    assert database.jobs.count_documents({}) == 0


def test_stale_archive_checkpoint_does_not_pause_normal_backfill_forever():
    database.sync_controls.insert_one({
        "_id": "archive_backfill:2000:2026", "active": True,
        "updated_at": utcnow() - timedelta(hours=1),
    })
    database.sync_controls.insert_one({
        "_id": "historical_backfill", "active": True, "start": 2000,
        "end": 2001, "include_telemetry": True,
    })
    counts = {"season": 0, "session": 0, "track": 0, "telemetry": 0, "backfill": 0}

    schedule_historical_backfill(counts)

    assert counts["backfill"] == 2
    assert database.jobs.count_documents({"kind": "season", "status": "queued"}) == 2


def test_current_year_race_telemetry_is_queued_ahead_of_archive_work(monkeypatch):
    import app.scheduler as scheduler_module

    year = utcnow().year
    monkeypatch.setattr(scheduler_module.settings, "telemetry_backfill_enabled", True)
    monkeypatch.setattr(scheduler_module.settings, "historical_backfill_enabled", False)
    database.sessions.insert_one({
        "_id": f"{year}-1-R", "season": year, "round": 1, "code": "R",
        "event_id": f"{year}-1",
        "starts_at": utcnow() - timedelta(days=3),
    })
    database.dataset_status.insert_one({
        "subject": str(year), "dataset": "calendar", "availability": "available",
        "last_synced_at": utcnow(),
    })

    schedule_once()

    job = database.jobs.find_one({"key": f"telemetry:{year}-1-R"})
    assert job["priority"] == 100


def test_historical_backfill_queues_races_but_not_other_sessions():
    database.sync_controls.insert_one({
        "_id": "historical_backfill", "active": True, "start": 2025,
        "end": 2025, "include_telemetry": False,
    })
    database.seasons.insert_one({"_id": 2025, "year": 2025})
    database.sessions.insert_many([
        {
            "_id": "2025-1-R", "season": 2025, "round": 1, "code": "R",
            "event_id": "2025-1", "starts_at": utcnow() - timedelta(days=10),
        },
        {
            "_id": "2025-1-Q", "season": 2025, "round": 1, "code": "Q",
            "event_id": "2025-1", "starts_at": utcnow() - timedelta(days=10),
        },
    ])
    counts = {"season": 0, "session": 0, "track": 0, "telemetry": 0, "backfill": 0}

    schedule_historical_backfill(counts)

    assert database.jobs.find_one({"key": "session:2025-1-R"})["priority"] == 20
    assert database.jobs.find_one({"key": "session:2025-1-Q"}) is None
