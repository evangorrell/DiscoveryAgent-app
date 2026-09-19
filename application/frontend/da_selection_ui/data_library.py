"""App-wide references to scientist-owned, read-only input data.

The registry contains metadata only.  It never creates entries in a project,
copies source data, or treats a source as an output location.  A source may be
either one regular file or one directory tree.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .project_storage import get_data_root, get_projects_root


SCHEMA_VERSION = 1
TREE_PAGE_LIMIT = 200
_REGISTRY_LOCK = threading.RLock()


class DataLibraryError(ValueError):
    """A structured, user-actionable Data Library failure."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def registry_lock():
    return _REGISTRY_LOCK


def registry_path(data_root: Path | None = None) -> Path:
    return Path(data_root or get_data_root()) / "data_library.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_key(path: Path) -> str:
    value = unicodedata.normalize("NFC", os.path.normpath(str(path)))
    return value.casefold() if sys.platform == "darwin" else value


def _validate_record(value: Any) -> tuple[dict[str, Any], bool]:
    if not isinstance(value, dict):
        raise DataLibraryError("data_library_corrupt", "The Data Library registry contains an invalid source record.", 500)
    required = ("id", "source_path", "canonical_path", "created_at", "updated_at")
    if any(not isinstance(value.get(key), str) or not value[key].strip() for key in required):
        raise DataLibraryError("data_library_corrupt", "The Data Library registry contains an incomplete source record.", 500)
    if not value["id"].startswith("ds_"):
        raise DataLibraryError("data_library_corrupt", "The Data Library registry contains an invalid dataset ID.", 500)
    if not Path(value["source_path"]).is_absolute() or not Path(value["canonical_path"]).is_absolute():
        raise DataLibraryError("data_library_corrupt", "The Data Library registry contains a non-absolute source path.", 500)

    display_name = value.get("display_name")
    repaired = not isinstance(display_name, str) or not display_name.strip()
    if repaired:
        # A short-lived batch-registration regression could persist an otherwise
        # valid source without its presentation-only name.  Recover that field
        # from the already registered canonical identity; never replace the ID
        # or touch the referenced directory.
        display_name = Path(value["canonical_path"]).name or Path(value["source_path"]).name
    display_name = " ".join(str(display_name).split()).strip()
    if not display_name:
        raise DataLibraryError("data_library_corrupt", "The Data Library registry contains an incomplete source record.", 500)
    record = {key: value[key] for key in required}
    record["display_name"] = display_name[:160]
    # Retain the stable on-disk field order used by newly registered records.
    record = {
        "id": record["id"],
        "display_name": record["display_name"],
        "source_path": record["source_path"],
        "canonical_path": record["canonical_path"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
    }
    return record, repaired


def _load_unlocked(path: Path | None = None) -> dict[str, Any]:
    target = Path(path or registry_path())
    if not target.exists():
        return {"schema_version": SCHEMA_VERSION, "datasets": []}
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DataLibraryError(
            "data_library_corrupt",
            "The Data Library registry could not be read. It was left unchanged.",
            500,
        ) from exc
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION or not isinstance(value.get("datasets"), list):
        raise DataLibraryError("data_library_corrupt", "The Data Library registry has an unsupported structure. It was left unchanged.", 500)
    validated = [_validate_record(item) for item in value["datasets"]]
    records = [record for record, _repaired in validated]
    ids = [record["id"] for record in records]
    if len(ids) != len(set(ids)):
        raise DataLibraryError("data_library_corrupt", "The Data Library registry contains duplicate dataset IDs.", 500)
    payload = {"schema_version": SCHEMA_VERSION, "datasets": records}
    if any(repaired for _record, repaired in validated):
        _save_unlocked(payload, target)
    return payload


def load_registry(path: Path | None = None) -> dict[str, Any]:
    with _REGISTRY_LOCK:
        return _load_unlocked(path)


def _save_unlocked(payload: dict[str, Any], path: Path | None = None) -> None:
    target = Path(path or registry_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)


def _validate_source(raw: str) -> tuple[Path, Path]:
    text = str(raw or "").strip()
    if not text:
        raise DataLibraryError("data_source_required", "Choose a data file or folder.")
    source = Path(text).expanduser()
    if not source.is_absolute():
        raise DataLibraryError("data_source_not_absolute", "Choose a file or folder using its full absolute path.")
    try:
        if source.is_symlink():
            raise DataLibraryError("data_source_symlink", "Choose the source itself rather than a symbolic link.")
        canonical = source.resolve(strict=True)
    except OSError as exc:
        raise DataLibraryError("data_source_unavailable", f"The data source is unavailable: {source}.", 409) from exc
    if not canonical.is_dir() and not canonical.is_file():
        raise DataLibraryError("data_source_not_supported", f"The selected path is not a regular file or folder: {source}.")
    if canonical.is_dir() and (canonical == Path(canonical.anchor) or canonical == Path.home().resolve()):
        raise DataLibraryError("data_source_too_broad", "Choose a specific data folder rather than your home folder or disk.")
    data_root = get_data_root().resolve()
    projects_root = get_projects_root().resolve()
    if canonical == data_root or canonical.is_relative_to(data_root) or canonical == projects_root or canonical.is_relative_to(projects_root):
        raise DataLibraryError(
            "project_data_not_allowed",
            "Project-owned files cannot be registered as shared input data. Choose a source outside DiscoveryAgent projects.",
        )
    required_access = os.R_OK | (os.X_OK if canonical.is_dir() else 0)
    if not os.access(canonical, required_access):
        raise DataLibraryError("data_source_unreadable", f"DiscoveryAgent cannot read {source}.", 403)
    return source.absolute(), canonical


def _source_is_usable(record: dict[str, Any]) -> bool:
    source = Path(record["source_path"])
    try:
        if source.is_symlink():
            return False
        canonical = source.resolve(strict=True)
        return (
            _canonical_key(canonical) == _canonical_key(Path(record["canonical_path"]))
            and (canonical.is_dir() or canonical.is_file())
            and os.access(canonical, os.R_OK | (os.X_OK if canonical.is_dir() else 0))
        )
    except OSError:
        return False


def invalid_source_ids(path: Path | None = None) -> list[str]:
    """Return invalid registered roots without inspecting their children.

    Callers that reconcile project metadata hold ``registry_lock()`` across
    this check and the corresponding registry mutation, so add/remove/edit
    operations cannot race the decision.
    """
    with _REGISTRY_LOCK:
        return [
            record["id"]
            for record in _load_unlocked(path)["datasets"]
            if not _source_is_usable(record)
        ]


def list_sources(path: Path | None = None) -> list[dict[str, Any]]:
    return [dict(record) for record in load_registry(path)["datasets"]]


def add_source(raw_path: str, display_name: str | None = None, *, path: Path | None = None) -> tuple[dict[str, Any], bool]:
    source, canonical = _validate_source(raw_path)
    with _REGISTRY_LOCK:
        payload = _load_unlocked(path)
        key = _canonical_key(canonical)
        for record in payload["datasets"]:
            if _canonical_key(Path(record["canonical_path"])) == key:
                return dict(record), False
        now = _now()
        name = " ".join(str(display_name or source.name).split()).strip() or source.name
        record = {
            "id": f"ds_{uuid.uuid4().hex}",
            "display_name": name[:160],
            "source_path": str(source),
            "canonical_path": str(canonical),
            "created_at": now,
            "updated_at": now,
        }
        payload["datasets"].append(record)
        _save_unlocked(payload, path)
        return dict(record), True


def add_sources(raw_paths: list[str] | tuple[str, ...], *, path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """Register one native-picker batch with a single atomic registry write.

    A bad or previously registered selection does not discard valid peers.  No
    source contents are enumerated or copied: validation records only the
    canonical identity of each selected top-level file or folder.
    """
    if not isinstance(raw_paths, (list, tuple)) or not raw_paths:
        raise DataLibraryError("data_source_required", "Choose one or more data files or folders.")

    added: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    with _REGISTRY_LOCK:
        payload = _load_unlocked(path)
        known = {
            _canonical_key(Path(record["canonical_path"])): record
            for record in payload["datasets"]
        }
        for index, raw_path in enumerate(raw_paths):
            raw_text = raw_path if isinstance(raw_path, str) else ""
            try:
                source, canonical = _validate_source(raw_text)
            except DataLibraryError as exc:
                rejected.append({
                    "selection_index": index,
                    "path": raw_text,
                    "code": exc.code,
                    "error": exc.message,
                })
                continue

            key = _canonical_key(canonical)
            existing = known.get(key)
            if existing is not None:
                skipped.append({
                    "selection_index": index,
                    "path": str(source),
                    "reason": "already_registered",
                    "source": dict(existing),
                })
                continue

            now = _now()
            record = {
                "id": f"ds_{uuid.uuid4().hex}",
                "display_name": (" ".join(source.name.split()).strip() or source.name)[:160],
                "source_path": str(source),
                "canonical_path": str(canonical),
                "created_at": now,
                "updated_at": now,
            }
            payload["datasets"].append(record)
            known[key] = record
            added.append(dict(record))

        if added:
            _save_unlocked(payload, path)

    return {"added": added, "skipped": skipped, "rejected": rejected}


def remove_sources(dataset_ids: set[str] | list[str] | tuple[str, ...], *, path: Path | None = None) -> list[dict[str, Any]]:
    """Forget a set of top-level references in one atomic registry write."""
    requested = set(dataset_ids)
    if not requested:
        return []
    with _REGISTRY_LOCK:
        payload = _load_unlocked(path)
        removed = [record for record in payload["datasets"] if record["id"] in requested]
        if removed:
            payload["datasets"] = [record for record in payload["datasets"] if record["id"] not in requested]
            _save_unlocked(payload, path)
        return removed


def remove_source(dataset_id: str, *, path: Path | None = None) -> dict[str, Any]:
    removed = remove_sources({dataset_id}, path=path)
    if not removed:
        raise DataLibraryError("dataset_not_found", "That Data Library source no longer exists.", 404)
    return removed[0]


def normalize_relative_path(value: Any) -> str:
    text = str(value if value is not None else ".").replace("\\", "/").strip()
    if text in ("", "."):
        return "."
    pure = PurePosixPath(text)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise DataLibraryError("invalid_data_path", "A selected data path is not a safe source-relative path.")
    return pure.as_posix()


def normalize_selection(value: Any) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise DataLibraryError("invalid_data_selection", "The selected input data is not valid JSON.") from exc
    if not isinstance(value, list):
        raise DataLibraryError("invalid_data_selection", "The selected input data must be a list.")
    merged: dict[str, dict[str, set[str]]] = {}
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("dataset_id"), str) or not item["dataset_id"].startswith("ds_"):
            raise DataLibraryError("invalid_data_selection", "The selected input data contains an invalid dataset ID.")
        bucket = merged.setdefault(item["dataset_id"], {"selected": set(), "excluded": set()})
        selected = item.get("selected_paths", ["."])
        excluded = item.get("excluded_paths", [])
        if not isinstance(selected, list) or not isinstance(excluded, list):
            raise DataLibraryError("invalid_data_selection", "Selected and excluded paths must be lists.")
        bucket["selected"].update(normalize_relative_path(entry) for entry in selected)
        bucket["excluded"].update(normalize_relative_path(entry) for entry in excluded)

    normalized: list[dict[str, Any]] = []
    for dataset_id in sorted(merged):
        selected = sorted(merged[dataset_id]["selected"], key=lambda p: (p.count("/"), p.casefold(), p))
        compact: list[str] = []
        for candidate in selected:
            if any(parent == "." or candidate == parent or candidate.startswith(parent + "/") for parent in compact):
                continue
            compact.append(candidate)
        excluded = sorted(
            (entry for entry in merged[dataset_id]["excluded"] if any(parent == "." or entry.startswith(parent + "/") for parent in compact)),
            key=lambda p: (p.count("/"), p.casefold(), p),
        )
        compact_excluded: list[str] = []
        for candidate in excluded:
            if candidate in compact or any(candidate.startswith(parent + "/") for parent in compact_excluded):
                continue
            compact_excluded.append(candidate)
        if compact:
            normalized.append({"dataset_id": dataset_id, "selected_paths": compact, "excluded_paths": compact_excluded})
    return normalized


def _record_map(path: Path | None = None) -> dict[str, dict[str, Any]]:
    return {record["id"]: record for record in load_registry(path)["datasets"]}


def resolve_relative(record: dict[str, Any], relative: str, *, require_directory: bool | None = None) -> Path:
    relative = normalize_relative_path(relative)
    source = Path(record["source_path"])
    try:
        root = source.resolve(strict=True)
        if _canonical_key(root) != _canonical_key(Path(record["canonical_path"])):
            raise DataLibraryError(
                "data_source_identity_changed",
                f"The folder for {record['display_name']} no longer has its registered identity. Add the source again before use.",
                409,
            )
        target_literal = root if relative == "." else root.joinpath(*PurePosixPath(relative).parts)
        # Library tree traversal never follows a nested symlink.
        current = root
        for part in (() if relative == "." else PurePosixPath(relative).parts):
            current = current / part
            if current.is_symlink():
                raise DataLibraryError("data_symlink_not_allowed", "Symbolic links inside a Data Library source are not followed.", 403)
        target = target_literal.resolve(strict=True)
        target.relative_to(root)
    except DataLibraryError:
        raise
    except (OSError, ValueError) as exc:
        raise DataLibraryError("data_path_unavailable", f"The selected data path is unavailable: {record['display_name']}/{relative}.", 409) from exc
    if require_directory is True and not target.is_dir():
        raise DataLibraryError("data_path_not_directory", "Only folders can be expanded.")
    if not os.access(target, os.R_OK | (os.X_OK if target.is_dir() else 0)):
        raise DataLibraryError("data_path_unreadable", f"DiscoveryAgent cannot read {record['display_name']}/{relative}.", 403)
    return target


def list_children(dataset_id: str, relative: Any = ".", *, cursor: int = 0, limit: int = TREE_PAGE_LIMIT, path: Path | None = None) -> dict[str, Any]:
    records = _record_map(path)
    record = records.get(dataset_id)
    if record is None:
        raise DataLibraryError("dataset_not_found", "That Data Library source no longer exists.", 404)
    folder = resolve_relative(record, normalize_relative_path(relative), require_directory=True)
    cursor = max(0, int(cursor or 0))
    limit = max(1, min(int(limit or TREE_PAGE_LIMIT), TREE_PAGE_LIMIT))
    try:
        entries = [
            entry for entry in os.scandir(folder)
            if entry.name not in {".DS_Store", ".AppleDouble"} and not entry.name.startswith("._")
        ]
    except PermissionError as exc:
        raise DataLibraryError("data_path_unreadable", "DiscoveryAgent does not have permission to list that folder.", 403) from exc
    entries.sort(key=lambda entry: (not entry.is_dir(follow_symlinks=False), unicodedata.normalize("NFC", entry.name).casefold(), entry.name))
    page = entries[cursor:cursor + limit]
    base = normalize_relative_path(relative)
    children = []
    for entry in page:
        child_rel = entry.name if base == "." else f"{base}/{entry.name}"
        symlink = entry.is_symlink()
        is_directory = bool(not symlink and entry.is_dir(follow_symlinks=False))
        size_bytes = None
        if not symlink and not is_directory:
            try:
                size_bytes = entry.stat(follow_symlinks=False).st_size
            except OSError:
                # A file can disappear or become unreadable between scandir and
                # stat. Keep the rest of the immediate-child page available.
                size_bytes = None
        children.append({
            "name": entry.name,
            "path": child_rel,
            "kind": "symlink" if symlink else ("directory" if is_directory else "file"),
            "expandable": is_directory,
            "size_bytes": size_bytes,
        })
    next_cursor = cursor + len(page)
    return {"dataset_id": dataset_id, "path": base, "children": children, "next_cursor": next_cursor if next_cursor < len(entries) else None, "total": len(entries)}


def validate_selection(value: Any, *, path: Path | None = None, require_available: bool = False) -> list[dict[str, Any]]:
    selection = normalize_selection(value)
    if not selection:
        return []
    records = _record_map(path)
    for item in selection:
        record = records.get(item["dataset_id"])
        if record is None:
            raise DataLibraryError("dataset_not_found", "A selected Data Library source no longer exists.", 409)
        if require_available:
            for relative in item["selected_paths"]:
                resolve_relative(record, relative)
    return selection


def resolve_selection(value: Any, *, path: Path | None = None) -> tuple[list[str], list[dict[str, Any]]]:
    selection = validate_selection(value, path=path, require_available=True)
    if not selection:
        return [], []
    records = _record_map(path)
    roots: list[str] = []
    manifest: list[dict[str, Any]] = []
    for item in selection:
        record = records[item["dataset_id"]]
        resolved = [str(resolve_relative(record, relative)) for relative in item["selected_paths"]]
        for absolute in resolved:
            if "," in absolute:
                raise DataLibraryError(
                    "data_path_unsupported",
                    "The current execution runtime cannot safely pass a selected input path containing a comma.",
                )
            if absolute not in roots:
                roots.append(absolute)
        manifest.append({
            "dataset_id": item["dataset_id"],
            "display_name": record["display_name"],
            "selected_paths": list(item["selected_paths"]),
            "excluded_paths": list(item["excluded_paths"]),
            "resolved_read_paths": resolved,
            "read_only": True,
            "exclusions_hard_enforced": not bool(item["excluded_paths"]),
        })
    return roots, manifest
