"""Read the frontend's existing browser-tab presence lease."""

from __future__ import annotations

import json
import re
import tempfile
import time
from enum import Enum
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit


BROWSER_PRESENCE_MAX_AGE_SECONDS = 30.0
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{16,80}")


class BrowserPresence(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"


def browser_presence_path(
    frontend_url: str,
    *,
    temp_directory: str | None = None,
) -> Path | None:
    try:
        port = urlsplit(frontend_url).port
    except ValueError:
        return None
    if port is None:
        return None
    root = tempfile.gettempdir() if temp_directory is None else temp_directory
    return Path(root) / f"discoveryagent-ui-browser-{port}.json"


def detect_browser_presence(
    frontend_url: str,
    *,
    now: Callable[[], float] = time.time,
    temp_directory: str | None = None,
) -> BrowserPresence:
    """Return ACTIVE only for a valid lease fresh under the frontend contract."""
    path = browser_presence_path(frontend_url, temp_directory=temp_directory)
    if path is None:
        return BrowserPresence.UNKNOWN
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return BrowserPresence.INACTIVE
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return BrowserPresence.UNKNOWN
    if not isinstance(payload, dict):
        return BrowserPresence.UNKNOWN
    current = now()
    valid_lease_seen = False
    for token, seen_at in payload.items():
        if (
            not isinstance(token, str)
            or _TOKEN_PATTERN.fullmatch(token) is None
            or not isinstance(seen_at, (int, float))
            or isinstance(seen_at, bool)
        ):
            return BrowserPresence.UNKNOWN
        valid_lease_seen = True
        age = current - float(seen_at)
        if 0.0 <= age <= BROWSER_PRESENCE_MAX_AGE_SECONDS:
            return BrowserPresence.ACTIVE
    return BrowserPresence.INACTIVE if valid_lease_seen or not payload else BrowserPresence.UNKNOWN
