"""Versioned persistence contracts shared by ingestion and read paths."""

from __future__ import annotations

import hashlib
from typing import Any


ARTIFACT_VERSION = "v4"
ARCHIVE_SCHEMA_VERSION = 4
PERSISTENT_TELEMETRY_SESSION_CODES = frozenset({"R"})
DRIVER_PROFILE_FIELDS = (
    "driverCode",
    "driverNumber",
    "driverUrl",
    "dateOfBirth",
    "driverNationality",
)
RESERVE_DRIVER_ROLES = frozenset({
    "reserve", "reserve_driver", "test", "test_driver", "development",
    "development_driver",
})
RACE_DRIVER_ROLES = frozenset({"race", "race_driver", "main", "regular"})


def is_public_driver_profile(row: dict[str, Any]) -> bool:
    """Return whether a driver has the metadata supplied for a race driver."""
    return any(row.get(field) not in (None, "") for field in DRIVER_PROFILE_FIELDS)


def is_reserve_driver(row: dict[str, Any]) -> bool:
    """Classify explicit reserves, falling back to name-only source records."""
    explicit_reserve = row.get("isReserve")
    if isinstance(explicit_reserve, bool):
        return explicit_reserve

    explicit_role = (
        str(row.get("driverRole") or row.get("role") or "")
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )
    if explicit_role in RESERVE_DRIVER_ROLES:
        return True
    if explicit_role in RACE_DRIVER_ROLES:
        return False
    return not is_public_driver_profile(row)


def driver_role(row: dict[str, Any]) -> str:
    """Return the stable public role used by driver directory clients."""
    return "reserve" if is_reserve_driver(row) else "race"


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
