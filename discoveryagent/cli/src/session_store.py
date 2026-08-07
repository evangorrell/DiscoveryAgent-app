"""
Session metadata helpers for the interactive DiscoveryAgent CLI.

Sessions are lightweight wrappers around Claude's native conversation persistence
plus DiscoveryAgent-owned MCP checkpoint state. Each session may span multiple
CLI runs, with each run writing logs/checkpoints to its own folder.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional


SESSION_VERSION = 2
SNAPSHOT_PREFIX = "snapshot_"


def _now_iso() -> str:
    return datetime.now().isoformat()


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    tmp_path.replace(path)


def _session_file_from_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    if candidate.is_dir():
        return candidate / "session.json"
    return candidate


def _update_symlink(link_path: Path, target: Path) -> None:
    try:
        if link_path.is_symlink() or link_path.exists():
            link_path.unlink()
        link_path.symlink_to(target)
    except OSError:
        # Symlink creation is best-effort only.
        return


def get_sessions_root(checkpoint_dir: str | Path) -> Path:
    root = Path(checkpoint_dir).expanduser().resolve() / "interactive_sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_session_dir(session_metadata_or_path: dict | str | Path) -> Path:
    if isinstance(session_metadata_or_path, dict):
        return Path(session_metadata_or_path["session_dir"]).expanduser().resolve()
    session_file = _session_file_from_path(session_metadata_or_path)
    if session_file.name == "session.json":
        return session_file.parent.resolve()
    return session_file.resolve()


def get_state_root(session_metadata_or_path: dict | str | Path) -> Path:
    state_root = get_session_dir(session_metadata_or_path) / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    return state_root


def get_snapshots_root(session_metadata_or_path: dict | str | Path) -> Path:
    snapshots_root = get_state_root(session_metadata_or_path) / "snapshots"
    snapshots_root.mkdir(parents=True, exist_ok=True)
    return snapshots_root


def get_queries_root(session_metadata_or_path: dict | str | Path) -> Path:
    queries_root = get_state_root(session_metadata_or_path) / "queries"
    queries_root.mkdir(parents=True, exist_ok=True)
    return queries_root


def get_report_path(session_metadata_or_path: dict | str | Path) -> Path:
    return get_state_root(session_metadata_or_path) / "report.md"


def build_snapshot_path(
    session_metadata_or_path: dict | str | Path,
    snapshot_id: str,
) -> Path:
    return get_snapshots_root(session_metadata_or_path) / snapshot_id


def extract_snapshot_id(snapshot_path: str | Path) -> Optional[str]:
    path = Path(snapshot_path).expanduser().resolve()
    if path.name.startswith(SNAPSHOT_PREFIX):
        return path.name
    parent = path.parent
    if parent.name.startswith(SNAPSHOT_PREFIX):
        return parent.name
    return None


def resolve_head_snapshot_path(session_metadata_or_path: dict | str | Path) -> Optional[Path]:
    metadata = (
        session_metadata_or_path
        if isinstance(session_metadata_or_path, dict)
        else load_session_metadata(session_metadata_or_path)
    )

    head_snapshot_id = metadata.get("head_snapshot_id")
    if head_snapshot_id:
        candidate = build_snapshot_path(metadata, head_snapshot_id)
        if candidate.exists():
            return candidate

    latest_link = get_state_root(metadata) / "latest"
    if latest_link.exists():
        try:
            return latest_link.resolve()
        except OSError:
            pass

    legacy_path = metadata.get("last_interactive_checkpoint")
    if legacy_path:
        candidate = Path(legacy_path).expanduser().resolve()
        if candidate.exists():
            return candidate

    return None


def _ensure_session_layout(session_dir: Path) -> None:
    (session_dir / "runs").mkdir(parents=True, exist_ok=True)
    get_state_root(session_dir)
    get_snapshots_root(session_dir)
    get_queries_root(session_dir)


def load_session_metadata(path: str | Path) -> dict:
    session_file = _session_file_from_path(path)
    with open(session_file, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data


def get_latest_session_metadata(checkpoint_dir: str | Path) -> Optional[dict]:
    sessions_root = get_sessions_root(checkpoint_dir)
    latest_link = sessions_root / "latest"
    if latest_link.exists():
        try:
            return load_session_metadata(latest_link.resolve())
        except Exception:
            pass

    candidates = []
    for session_dir in sessions_root.iterdir():
        session_file = session_dir / "session.json"
        if session_dir.is_dir() and session_file.exists():
            try:
                meta = load_session_metadata(session_file)
                candidates.append((meta.get("updated_at", ""), meta))
            except Exception:
                continue

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def resolve_session_metadata(checkpoint_dir: str | Path, selector: str) -> dict:
    selector = selector.strip()
    if not selector:
        raise ValueError("Session selector cannot be empty")
    if selector == "latest":
        latest = get_latest_session_metadata(checkpoint_dir)
        if latest is None:
            raise FileNotFoundError("No interactive sessions found")
        return latest

    selector_path = Path(selector).expanduser()
    if selector_path.exists():
        return load_session_metadata(selector_path)

    sessions_root = get_sessions_root(checkpoint_dir)
    exact = sessions_root / selector
    if exact.exists():
        return load_session_metadata(exact)

    matches = []
    for session_dir in sessions_root.iterdir():
        if session_dir.is_dir() and session_dir.name.startswith(selector):
            session_file = session_dir / "session.json"
            if session_file.exists():
                matches.append(session_file)

    if not matches:
        raise FileNotFoundError(f"No interactive session matched: {selector}")
    if len(matches) > 1:
        match_names = ", ".join(sorted(path.parent.name for path in matches))
        raise ValueError(
            f"Session selector '{selector}' is ambiguous. Matches: {match_names}"
        )
    return load_session_metadata(matches[0])


def create_session_metadata(
    checkpoint_dir: str | Path,
    code_dir: str | Path,
    analysis_name: str,
    display_name: Optional[str] = None,
) -> dict:
    sessions_root = get_sessions_root(checkpoint_dir)
    session_id = str(uuid.uuid4())
    session_dir = sessions_root / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    _ensure_session_layout(session_dir)

    timestamp = _now_iso()
    metadata = {
        "session_version": SESSION_VERSION,
        "session_id": session_id,
        "claude_session_id": session_id,
        "display_name": display_name,
        "analysis_name": analysis_name,
        "checkpoint_dir": str(Path(checkpoint_dir).expanduser().resolve()),
        "code_dir": str(Path(code_dir).expanduser().resolve()),
        "session_dir": str(session_dir.resolve()),
        "state_dir": str(get_state_root(session_dir)),
        "created_at": timestamp,
        "updated_at": timestamp,
        "last_run_dir": None,
        "head_snapshot_id": None,
        "last_interactive_checkpoint": None,
        "last_memory_report": None,
        "runs": [],
    }
    _atomic_write_json(session_dir / "session.json", metadata)
    _update_symlink(sessions_root / "latest", session_dir)
    return metadata


def validate_session_scope(
    metadata: dict,
    checkpoint_dir: str | Path,
    code_dir: str | Path,
) -> None:
    expected_checkpoint = str(Path(checkpoint_dir).expanduser().resolve())
    expected_code_dir = str(Path(code_dir).expanduser().resolve())

    actual_checkpoint = metadata.get("checkpoint_dir")
    actual_code_dir = metadata.get("code_dir")

    if actual_checkpoint != expected_checkpoint:
        raise ValueError(
            "Session checkpoint directory mismatch. "
            f"Expected {expected_checkpoint}, found {actual_checkpoint}."
        )
    if actual_code_dir != expected_code_dir:
        raise ValueError(
            "Session code directory mismatch. "
            f"Expected {expected_code_dir}, found {actual_code_dir}."
        )


def register_session_run(
    metadata: dict,
    log_dir: str | Path,
    resumed_from_checkpoint: Optional[str] = None,
) -> dict:
    session_file = _session_file_from_path(Path(metadata["session_dir"]) / "session.json")
    current = load_session_metadata(session_file)
    _ensure_session_layout(Path(current["session_dir"]).resolve())

    run_dir = Path(log_dir).expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    session_dir = Path(current["session_dir"]).resolve()
    timestamp = _now_iso()
    current["updated_at"] = timestamp
    current["last_run_dir"] = str(run_dir)
    current.setdefault("runs", []).append(
        {
            "run_id": run_dir.name,
            "log_dir": str(run_dir),
            "started_at": timestamp,
            "resumed_from_checkpoint": resumed_from_checkpoint,
        }
    )
    _atomic_write_json(session_file, current)
    _update_symlink(session_dir / "latest_run", run_dir)
    _update_symlink(get_sessions_root(current["checkpoint_dir"]) / "latest", session_dir)
    return current


def update_session_progress(
    session_metadata_path: str | Path,
    *,
    last_run_dir: Optional[str | Path] = None,
    head_snapshot_id: Optional[str] = None,
    last_interactive_checkpoint: Optional[str | Path] = None,
    last_memory_report: Optional[str | Path] = None,
) -> dict:
    session_file = _session_file_from_path(session_metadata_path)
    current = load_session_metadata(session_file)
    current["updated_at"] = _now_iso()
    _ensure_session_layout(Path(current["session_dir"]).resolve())

    if last_run_dir is not None:
        current["last_run_dir"] = str(Path(last_run_dir).expanduser().resolve())
    if head_snapshot_id is not None:
        current["head_snapshot_id"] = head_snapshot_id
    if last_interactive_checkpoint is not None:
        resolved_checkpoint = str(Path(last_interactive_checkpoint).expanduser().resolve())
        current["last_interactive_checkpoint"] = resolved_checkpoint
        extracted_snapshot_id = extract_snapshot_id(resolved_checkpoint)
        if extracted_snapshot_id is not None:
            current["head_snapshot_id"] = extracted_snapshot_id
    if last_memory_report is not None:
        current["last_memory_report"] = str(
            Path(last_memory_report).expanduser().resolve()
        )

    _atomic_write_json(session_file, current)

    session_dir = Path(current["session_dir"]).resolve()
    if current.get("last_run_dir"):
        _update_symlink(session_dir / "latest_run", Path(current["last_run_dir"]))
    _update_symlink(get_sessions_root(current["checkpoint_dir"]) / "latest", session_dir)
    return current


def build_default_run_log_dir(metadata: dict) -> Path:
    session_dir = Path(metadata["session_dir"]).resolve()
    runs_dir = session_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_name = datetime.now().strftime("%Y%m%d_%H%M%S")
    return runs_dir / run_name
