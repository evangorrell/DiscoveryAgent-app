"""Project-local durable storage for frontend research interactions."""

from __future__ import annotations

import json
import os
from pathlib import Path


INTERACTION_LOG_RELATIVE_PATH = (
    Path(".discoveryagent") / "interactions" / "audit_interactions.jsonl"
)
CONVERSATION_STATE_RELATIVE_PATH = (
    Path(".discoveryagent") / "interactions" / "conversation_state.json"
)


def project_interaction_log_path(
    project_root: Path,
    *,
    project_id: str,
    override_root: Path | None = None,
) -> Path:
    """Return one project's interaction log without creating anything.

    ``override_root`` is intentionally namespaced by project id so test/dev
    overrides retain the same isolation guarantee as managed production data.
    """
    if override_root is not None:
        return Path(override_root).expanduser() / project_id / "audit_interactions.jsonl"
    return Path(project_root) / INTERACTION_LOG_RELATIVE_PATH


def project_conversation_state_path(
    project_root: Path,
    *,
    project_id: str,
    override_root: Path | None = None,
) -> Path:
    """Return one project's durable UI-conversation snapshot path.

    The interaction JSONL remains the append-only audit/export record. This
    compact state file is the restart/reconstruction authority for chat UI
    state that cannot be faithfully rebuilt from individual exchange events
    (multiple Audit Agent threads, titles, queues, and round chat groupings).
    """
    if override_root is not None:
        return Path(override_root).expanduser() / project_id / "conversation_state.json"
    return Path(project_root) / CONVERSATION_STATE_RELATIVE_PATH


def read_conversation_state(path: Path) -> dict:
    """Read a conversation-state document; missing/corrupt means empty."""
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {"version": 1, "surfaces": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("surfaces"), dict):
        return {"version": 1, "surfaces": {}}
    return {"version": 1, "surfaces": payload["surfaces"]}


def write_conversation_state(path: Path, payload: dict) -> None:
    """Atomically replace one project's compact conversation-state document."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def append_jsonl_line(path: Path, line: str) -> None:
    """Append one already-serialized JSON object, creating storage lazily."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line.rstrip("\r\n") + "\n")
