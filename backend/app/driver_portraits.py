"""Current-season full-length portraits from Formula 1's public driver page."""

from __future__ import annotations

import html
import re
import threading
import time
from urllib.request import Request, urlopen


OFFICIAL_DRIVERS_URL = "https://www.formula1.com/en/drivers"
_CACHE_SECONDS = 6 * 60 * 60
_cache_lock = threading.Lock()
_cached_at = 0.0
_cached_rows: list[dict[str, str | int]] = []
_PORTRAIT_PATTERN = re.compile(
    r'<img src="(?P<image>https://media\.formula1\.com/image/upload/'
    r'[^\"]*?/common/f1/(?P<season>\d{4})/[^\"]+?right\.webp)"'
    r' alt="(?P<name>[^\"]+)" role="presentation"'
)


def parse_official_driver_portraits(page: str) -> list[dict[str, str | int]]:
    """Extract one full-length `right` asset for each driver."""
    rows: list[dict[str, str | int]] = []
    seen: set[str] = set()
    for match in _PORTRAIT_PATTERN.finditer(page):
        full_name = html.unescape(match.group("name")).strip()
        key = " ".join(full_name.casefold().split())
        if not full_name or key in seen:
            continue
        seen.add(key)
        rows.append({
            "full_name": full_name,
            "image_url": html.unescape(match.group("image")),
            "season": int(match.group("season")),
            "source_url": OFFICIAL_DRIVERS_URL,
        })
    return rows


def get_official_driver_portraits() -> list[dict[str, str | int]]:
    """Fetch and cache the official current grid's full-length assets."""
    global _cached_at, _cached_rows
    now = time.monotonic()
    with _cache_lock:
        if _cached_rows and now - _cached_at < _CACHE_SECONDS:
            return list(_cached_rows)
        request = Request(
            OFFICIAL_DRIVERS_URL,
            headers={
                "User-Agent": "RaceDataManagement/1.0 (driver portrait index)",
                "Accept": "text/html",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed trusted URL
                page = response.read().decode("utf-8")
            rows = parse_official_driver_portraits(page)
            if not rows:
                raise RuntimeError("Formula 1 driver page contained no portrait assets")
            _cached_rows = rows
            _cached_at = now
        except Exception:
            if not _cached_rows:
                raise
        return list(_cached_rows)
