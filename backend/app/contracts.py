"""Versioned persistence contracts shared by ingestion and read paths."""

from __future__ import annotations

import hashlib
from typing import Any


ARTIFACT_VERSION = "v4"
ARCHIVE_SCHEMA_VERSION = 4
PERSISTENT_TELEMETRY_SESSION_CODES = frozenset({"R"})


def stores_persistent_telemetry(
    session_id: str,
    code: str | None = None,
) -> bool:
    """Return whether this session belongs in durable telemetry storage."""
    session_code = code or session_id.rsplit("-", 1)[-1]
    return str(session_code).upper() in PERSISTENT_TELEMETRY_SESSION_CODES


def artifact_key(session_id: str, kind: str, options: dict[str, Any]) -> str:
    suffix = hashlib.sha1(
        repr(sorted(options.items())).encode(), usedforsecurity=False,
    ).hexdigest()[:12]
    return f"{ARTIFACT_VERSION}:{session_id}:{kind}:{suffix}"
