"""Project-local durable storage for frontend research interactions."""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable


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


def _resolved_path(value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return Path(value).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def _project_identities(projects: Iterable[dict]) -> dict[str, dict]:
    identities: dict[str, dict] = {}
    for project in projects:
        project_id = str(project.get("id") or "").strip()
        root = _resolved_path(project.get("project_root"))
        if not project_id or root is None:
            continue
        identities[project_id] = {
            "id": project_id,
            "root": root,
            "slug": str(project.get("url_slug") or "").strip(),
            "label": str(project.get("display_name") or "").strip(),
        }
    return identities


def _record_project_candidates(record: dict, identities: dict[str, dict]) -> set[str]:
    """Resolve only positive, exact project identity signals from one record."""
    if not isinstance(record, dict):
        return set()

    candidates: set[str] = set()

    claimed_ids = {
        str(record.get(key) or "").strip()
        for key in ("project_id", "v2_active_project_id")
        if str(record.get(key) or "").strip()
    }
    if claimed_ids:
        # An explicit unknown or conflicting id must never fall through to a
        # weaker display-name guess.
        if len(claimed_ids) != 1:
            return claimed_ids
        claimed_id = next(iter(claimed_ids))
        return {claimed_id} if claimed_id in identities else set()

    slug = str(record.get("project_slug") or "").strip()
    if slug:
        slug_matches = {
            project_id for project_id, identity in identities.items()
            if identity["slug"] == slug
        }
        if slug_matches:
            candidates.update(slug_matches)

    for key in ("project_root", "checkpoint_dir", "code_dir", "data_dir", "readme_path"):
        candidate_path = _resolved_path(record.get(key))
        if candidate_path is None:
            continue
        for project_id, identity in identities.items():
            try:
                candidate_path.relative_to(identity["root"])
            except ValueError:
                continue
            candidates.add(project_id)

    label = str(record.get("project_label") or "").strip()
    if label:
        label_matches = {
            project_id for project_id, identity in identities.items()
            if identity["label"] == label
        }
        # Display names are safe only when unique among registered managed
        # projects. Duplicate names are deliberately treated as ambiguous.
        if len(label_matches) == 1:
            candidates.update(label_matches)
        elif len(label_matches) > 1:
            candidates.update(label_matches)

    return candidates


def migrate_legacy_interactions(
    legacy_path: Path,
    projects: Iterable[dict],
    *,
    override_root: Path | None = None,
) -> dict:
    """Partition unambiguous legacy JSONL records into managed projects.

    The legacy file is never changed. Exact-line occurrence counts make the
    append idempotent while preserving genuine duplicate records and original
    ordering within each project's migrated history.
    """
    legacy_path = Path(legacy_path)
    identities = _project_identities(projects)
    result = {
        "legacy_path": str(legacy_path),
        "migrated_by_project": {},
        "already_present_by_project": {},
        "ambiguous_records": 0,
        "unmatched_records": 0,
        "invalid_records": 0,
    }
    if not legacy_path.is_file() or not identities:
        return result

    destination_counts: dict[str, Counter[str]] = {}
    for project_id, identity in identities.items():
        destination = project_interaction_log_path(
            identity["root"], project_id=project_id, override_root=override_root,
        )
        counts: Counter[str] = Counter()
        if destination.is_file():
            try:
                counts.update(
                    line.rstrip("\r\n")
                    for line in destination.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )
            except OSError:
                counts = Counter()
        destination_counts[project_id] = counts

    source_occurrences: dict[str, Counter[str]] = defaultdict(Counter)
    pending: dict[str, list[str]] = defaultdict(list)
    already: Counter[str] = Counter()
    try:
        legacy_lines = legacy_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return result

    for raw_line in legacy_lines:
        content = raw_line.rstrip("\r\n")
        if not content.strip():
            continue
        try:
            record = json.loads(content)
        except (TypeError, ValueError):
            result["invalid_records"] += 1
            continue
        candidates = _record_project_candidates(record, identities)
        if len(candidates) > 1:
            result["ambiguous_records"] += 1
            continue
        if not candidates:
            result["unmatched_records"] += 1
            continue

        project_id = next(iter(candidates))
        source_occurrences[project_id][content] += 1
        if destination_counts[project_id][content] >= source_occurrences[project_id][content]:
            already[project_id] += 1
            continue
        pending[project_id].append(content)

    for project_id, lines in pending.items():
        identity = identities[project_id]
        destination = project_interaction_log_path(
            identity["root"], project_id=project_id, override_root=override_root,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("a", encoding="utf-8") as handle:
            for line in lines:
                handle.write(line + "\n")
        result["migrated_by_project"][project_id] = len(lines)

    result["already_present_by_project"] = {
        project_id: count for project_id, count in already.items() if count
    }
    return result
