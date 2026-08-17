"""Which Claude models this installation can offer, asked of Anthropic directly.

Hardcoding a model list means the app goes stale the day Anthropic ships a new
model. The Models API (``GET /v1/models``) returns what the caller's key can
actually reach, newest first, so the picker rolls forward on its own. The
built-in list below is only a floor: it keeps the app usable without a key or
network, and it guarantees that a model a project is already pinned to always
validates even when the live call fails.
"""

from __future__ import annotations

import threading
import time
from typing import Any

CACHE_TTL_SECONDS = 3600
LIST_TIMEOUT_SECONDS = 10.0

# Known-good models, used when the live call cannot be made. Anything here is
# also permanently accepted as a saved choice, so a project pinned to one of
# these keeps working even during an Anthropic outage.
FALLBACK_MODELS: tuple[dict[str, str], ...] = (
    {"id": "claude-opus-5", "display_name": "Claude Opus 5"},
    {"id": "claude-sonnet-5", "display_name": "Claude Sonnet 5"},
    {"id": "claude-opus-4-8", "display_name": "Claude Opus 4.8"},
    {"id": "claude-sonnet-4-6", "display_name": "Claude Sonnet 4.6"},
    {"id": "claude-opus-4-6", "display_name": "Claude Opus 4.6"},
    {"id": "claude-haiku-4-5-20251001", "display_name": "Claude Haiku 4.5"},
    {"id": "claude-sonnet-4-5-20250929", "display_name": "Claude Sonnet 4.5"},
)

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"fetched_at": 0.0, "models": None}


def _fetch_from_anthropic() -> list[dict[str, str]]:
    """Ask Anthropic which models this key can use. Raises on any failure."""
    import anthropic

    client = anthropic.Anthropic(timeout=LIST_TIMEOUT_SECONDS, max_retries=1)
    listed: list[dict[str, str]] = []
    # The API returns newest first; keep that order so the newest model is the
    # first thing a scientist sees.
    for model in client.models.list(limit=50):
        model_id = str(getattr(model, "id", "") or "").strip()
        if not model_id:
            continue
        listed.append({
            "id": model_id,
            "display_name": str(getattr(model, "display_name", "") or model_id),
        })
    if not listed:
        raise RuntimeError("Anthropic returned no models")
    return listed


def available_models(*, refresh: bool = False, now: float | None = None) -> dict[str, Any]:
    """Return the models this installation can offer, with where the list came from.

    ``source`` is ``"anthropic"`` for a live list and ``"builtin"`` when the
    call failed, so the UI can say which one a scientist is looking at.
    """
    current = time.monotonic() if now is None else now
    with _LOCK:
        cached = _CACHE["models"]
        if not refresh and cached and current - _CACHE["fetched_at"] < CACHE_TTL_SECONDS:
            return dict(cached)

    try:
        payload = {"models": _fetch_from_anthropic(), "source": "anthropic", "error": None}
    except Exception as exc:  # offline, no key, API error — the floor still works
        return {
            "models": [dict(entry) for entry in FALLBACK_MODELS],
            "source": "builtin",
            "error": str(exc),
        }

    with _LOCK:
        _CACHE["models"] = payload
        _CACHE["fetched_at"] = current
    return dict(payload)


def known_model_ids() -> set[str]:
    """Every model id this installation accepts as a saved choice.

    The union of the live list and the built-in floor: a live list that has
    dropped a model must not invalidate a project already pinned to it.
    """
    ids = {entry["id"] for entry in FALLBACK_MODELS}
    ids.update(entry["id"] for entry in available_models()["models"])
    return ids


def reset_cache() -> None:
    """Forget the cached list. For tests and for an explicit refresh."""
    with _LOCK:
        _CACHE["models"] = None
        _CACHE["fetched_at"] = 0.0
