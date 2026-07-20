from pathlib import Path

from app.on_demand import OnDemandArtifactCache


class FakeBackgroundTasks:
    def __init__(self):
        self.tasks = []

    def add_task(self, function, *args, **kwargs):
        self.tasks.append((function, args, kwargs))

    def run(self):
        for function, args, kwargs in self.tasks:
            function(*args, **kwargs)


class FakeAdapter:
    def __init__(self):
        self.calls = []

    def session_artifact(self, session_id, kind, options):
        self.calls.append((session_id, kind, options))
        return {
            "availability": "available",
            "data": {"session_id": session_id, "kind": kind, **options},
            "source": "upstream",
        }


def test_requested_artifact_is_cached_without_a_database(tmp_path: Path):
    adapter = FakeAdapter()
    cache = OnDemandArtifactCache(
        tmp_path / "artifacts",
        tmp_path / "fastf1",
        max_bytes=1024 * 1024,
        adapter=adapter,
    )
    tasks = FakeBackgroundTasks()

    queued = cache.get_or_schedule(tasks, "2026-1-R", "laps", {})
    assert queued["status"] == "queued"
    assert adapter.calls == []

    tasks.run()
    cached = cache.get_or_schedule(
        FakeBackgroundTasks(), "2026-1-R", "laps", {},
    )
    assert cached["availability"] == "available"
    assert cached["data"]["kind"] == "laps"
    assert cached["source"] == "FastF1 on-demand cache"
    assert adapter.calls == [("2026-1-R", "laps", {})]


def test_only_the_requested_telemetry_variant_is_cached(tmp_path: Path):
    adapter = FakeAdapter()
    cache = OnDemandArtifactCache(
        tmp_path / "artifacts",
        tmp_path / "fastf1",
        max_bytes=1024 * 1024,
        adapter=adapter,
    )
    first = {"drivers": "VER", "laps": "fastest", "channels": "Speed", "stream": "merged"}
    second = {"drivers": "NOR", "laps": "2", "channels": "RPM", "stream": "car"}

    first_tasks = FakeBackgroundTasks()
    cache.get_or_schedule(first_tasks, "2026-1-R", "telemetry", first)
    first_tasks.run()

    second_tasks = FakeBackgroundTasks()
    queued = cache.get_or_schedule(
        second_tasks, "2026-1-R", "telemetry", second,
    )
    assert queued["status"] == "queued"
    second_tasks.run()

    assert adapter.calls == [
        ("2026-1-R", "telemetry", first),
        ("2026-1-R", "telemetry", second),
    ]
