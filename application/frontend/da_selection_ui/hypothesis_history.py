"""Durable, round-scoped Hypothesis Studio conversation history (V2).

The permanent product object for each Hypothesis Studio round is the *complete
chronological conversation* through which the hypothesis directing that round
was chosen. Generated sets, revisions, regenerations and the final selection
are rich events inside that one ordered conversation.

This module owns a small, append-only, atomically-written event log per round:

    <round checkpoint dir>/hypothesis_studio_history.json

It is *additional* durable chronology, not a replacement for the authoritative
operational artifacts (generated_hypotheses.json and selected_hypothesis.md),
which remain unchanged. Events store *snapshots*, never pointers to mutable
current files, so later regeneration can never rewrite history. No LLM calls
happen here. A missing file is a clean empty history.
"""

import datetime
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Optional

HISTORY_FILENAME = "hypothesis_studio_history.json"
HISTORY_VERSION = 1

# Event type vocabulary (kept small and product-meaningful).
TYPE_USER_MESSAGE = "user_message"
TYPE_ASSISTANT_MESSAGE = "assistant_message"
TYPE_HYPOTHESIS_SET_GENERATED = "hypothesis_set_generated"
TYPE_HYPOTHESIS_REVISED = "hypothesis_revised"
TYPE_HYPOTHESIS_REGENERATED = "hypothesis_regenerated"
TYPE_HYPOTHESIS_REMOVED = "hypothesis_removed"
TYPE_HYPOTHESIS_SELECTED = "hypothesis_selected"

PHASE_HYPOTHESIS_DISCUSSION = "hypothesis_discussion"

_MESSAGE_TYPES = {TYPE_USER_MESSAGE, TYPE_ASSISTANT_MESSAGE}

# Serializes read-modify-write of any round's history file across sessions.
# Per-session mutation is already serialized by app.py's session operation
# lock; this guards the rarer cross-session case on the same round file.
_HISTORY_LOCK = threading.Lock()


def empty_history() -> dict:
    return {"version": HISTORY_VERSION, "events": []}


def _history_path(round_dir) -> Path:
    return Path(round_dir) / HISTORY_FILENAME


def load_history(round_dir) -> dict:
    """Load the durable history for a round; missing/corrupt => empty."""
    if not round_dir:
        return empty_history()
    path = _history_path(round_dir)
    if not path.exists():
        return empty_history()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_history()
    if not isinstance(payload, dict):
        return empty_history()
    events = payload.get("events")
    if not isinstance(events, list):
        return empty_history()
    clean = [e for e in events if isinstance(e, dict) and e.get("type")]
    return {"version": payload.get("version", HISTORY_VERSION), "events": clean}


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, path)


def append_events(round_dir, new_events: list) -> dict:
    """Append events to a round's history under the module lock.

    Each event dict is stamped with a stable id, a monotonic sequence number
    and a timestamp. Deterministic ordering is preserved (append order).
    Returns the resulting history. Best-effort snapshots only — callers pass
    fully-materialized content, never references to mutable files.
    """
    if not new_events:
        return load_history(round_dir)
    path = _history_path(round_dir)
    with _HISTORY_LOCK:
        history = load_history(round_dir)
        events = history["events"]
        next_seq = (events[-1]["sequence"] + 1) if events and isinstance(events[-1].get("sequence"), int) else len(events) + 1
        now = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
        for ev in new_events:
            stamped = dict(ev)
            stamped.setdefault("id", uuid.uuid4().hex)
            stamped["sequence"] = next_seq
            stamped.setdefault("timestamp", now)
            events.append(stamped)
            next_seq += 1
        payload = {"version": HISTORY_VERSION, "events": events}
        _write_atomic(path, payload)
        return payload


# ---------------------------------------------------------------------------
# Event builders (pure; app.py assembles snapshots and calls append_events)
# ---------------------------------------------------------------------------

def user_message_event(content: str, phase: str) -> dict:
    return {"type": TYPE_USER_MESSAGE, "phase": phase, "content": content or ""}


def assistant_message_event(content: str, phase: str, status: Optional[str] = None) -> dict:
    ev = {"type": TYPE_ASSISTANT_MESSAGE, "phase": phase, "content": content or ""}
    if status:
        ev["status"] = status
    return ev


def hypothesis_set_generated_event(hypotheses: list, generation_id=None) -> dict:
    ev = {
        "type": TYPE_HYPOTHESIS_SET_GENERATED,
        "phase": PHASE_HYPOTHESIS_DISCUSSION,
        "hypotheses": hypotheses or [],
    }
    if generation_id is not None:
        ev["generation_id"] = generation_id
    return ev


def hypothesis_revised_event(hypothesis_index: int, before: Optional[dict], after: dict) -> dict:
    return {
        "type": TYPE_HYPOTHESIS_REVISED,
        "phase": PHASE_HYPOTHESIS_DISCUSSION,
        "hypothesis_index": hypothesis_index,
        "before": before,
        "after": after,
    }


def hypothesis_regenerated_event(hypothesis_index: int, before: Optional[dict], after: dict) -> dict:
    return {
        "type": TYPE_HYPOTHESIS_REGENERATED,
        "phase": PHASE_HYPOTHESIS_DISCUSSION,
        "hypothesis_index": hypothesis_index,
        "before": before,
        "after": after,
    }


def hypothesis_removed_event(hypothesis_index: int, removed: Optional[dict], remaining: list) -> dict:
    """A scientist removed one candidate. `remaining` snapshots the re-indexed
    surviving set so later projection never has to reconstruct index shifts."""
    return {
        "type": TYPE_HYPOTHESIS_REMOVED,
        "phase": PHASE_HYPOTHESIS_DISCUSSION,
        "hypothesis_index": hypothesis_index,
        "removed": removed,
        "hypotheses": remaining or [],
    }


def hypothesis_selected_event(hypothesis: dict) -> dict:
    return {
        "type": TYPE_HYPOTHESIS_SELECTED,
        "phase": PHASE_HYPOTHESIS_DISCUSSION,
        "hypothesis": hypothesis or {},
    }
