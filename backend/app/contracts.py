"""Versioned persistence contracts shared by ingestion and read paths."""

from __future__ import annotations

import hashlib
from typing import Any


ARTIFACT_VERSION = "v4"
ARCHIVE_SCHEMA_VERSION = 4


def artifact_key(session_id: str, kind: str, options: dict[str, Any]) -> str:
    suffix = hashlib.sha1(
        repr(sorted(options.items())).encode(), usedforsecurity=False,
    ).hexdigest()[:12]
    return f"{ARTIFACT_VERSION}:{session_id}:{kind}:{suffix}"
