"""Small on-demand loader for single-service deployments.

Session artifacts deliberately remain in the ephemeral filesystem cache.
Small calendar metadata imports are written to MongoDB so the public archive
can still fill itself when a separately deployed worker is delayed.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks
from fastapi.encoders import jsonable_encoder
from pymongo.database import Database

from .fastf1_adapter import FastF1Adapter
from .ingestion import sync_season


class OnDemandArtifactCache:
    def __init__(
        self,
        cache_dir: Path,
        fastf1_cache_dir: Path,
        *,
        max_bytes: int,
        adapter: FastF1Adapter | None = None,
    ) -> None:
        self.cache_dir = cache_dir.resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        fastf1_cache_dir.mkdir(parents=True, exist_ok=True)
        self.adapter = adapter or FastF1Adapter(fastf1_cache_dir)
        self.max_bytes = max(0, max_bytes)
        self._state_lock = threading.Lock()
        self._adapter_lock = threading.Lock()
        self._states: dict[str, dict[str, Any]] = {}
        self._active_session: str | None = None

    @staticmethod
    def _key(session_id: str, kind: str, options: dict[str, Any]) -> str:
        value = json.dumps(
            {"session_id": session_id, "kind": kind, "options": options},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _read(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            os.utime(path, None)
            return payload
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
            return None

    def _write(self, key: str, payload: dict[str, Any]) -> None:
        path = self._path(key)
        temporary = path.with_suffix(".tmp")
        encoded = jsonable_encoder(payload)
        temporary.write_text(
            json.dumps(encoded, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, path)
        self._prune()

    def _prune(self) -> None:
        if self.max_bytes <= 0:
            return
        files = sorted(
            (path for path in self.cache_dir.glob("*.json") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
        )
        total = sum(path.stat().st_size for path in files)
        for path in files:
            if total <= self.max_bytes:
                break
            try:
                size = path.stat().st_size
                path.unlink()
                total -= size
            except FileNotFoundError:
                continue

    def sync_calendar(self, db: Database, season: int) -> dict[str, int]:
        """Persist one lightweight season schedule using the shared adapter."""
        with self._adapter_lock:
            return sync_season(db, self.adapter, season)

    @staticmethod
    def _pending(kind: str, status: str) -> dict[str, Any]:
        return {
            "availability": "awaiting_data",
            "unavailable_reason": "Fetching this requested dataset from FastF1.",
            "data": None if kind == "telemetry" else [],
            "source": "FastF1 on-demand cache",
            "status": status,
        }

    def get_or_schedule(
        self,
        background_tasks: BackgroundTasks,
        session_id: str,
        kind: str,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        key = self._key(session_id, kind, options)
        cached = self._read(key)
        if cached is not None:
            return cached

        with self._state_lock:
            state = self._states.get(key)
            if state and state["status"] in {"queued", "running"}:
                return self._pending(kind, state["status"])
            if state and state["status"] == "failed":
                return {
                    "availability": "unavailable",
                    "unavailable_reason": state["error"],
                    "data": None if kind == "telemetry" else [],
                    "source": "FastF1 on-demand cache",
                    "status": "failed",
                }
            self._states[key] = {"status": "queued", "error": None}

        background_tasks.add_task(self._load, key, session_id, kind, options)
        return self._pending(kind, "queued")

    def _load(
        self,
        key: str,
        session_id: str,
        kind: str,
        options: dict[str, Any],
    ) -> None:
        with self._state_lock:
            self._states[key] = {"status": "running", "error": None}
        try:
            # FastF1 owns process-global cache configuration and its session
            # objects are memory-heavy. Serial execution avoids duplicate
            # downloads and keeps a free instance's peak memory predictable.
            with self._adapter_lock:
                if (
                    self._active_session is not None
                    and self._active_session != session_id
                ):
                    try:
                        self.adapter.prune_session_cache(self._active_session)
                    except Exception:
                        # A derived response remains useful even if FastF1's
                        # disposable staging files cannot be pruned.
                        pass
                self._active_session = session_id
                payload = self.adapter.session_artifact(session_id, kind, options)
            payload["source"] = "FastF1 on-demand cache"
            self._write(key, payload)
            with self._state_lock:
                self._states[key] = {"status": "completed", "error": None}
        except Exception as exc:
            with self._state_lock:
                self._states[key] = {"status": "failed", "error": str(exc)}
