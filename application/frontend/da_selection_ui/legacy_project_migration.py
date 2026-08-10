"""One-time migration of registered checkout-local legacy projects."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path


_PATH_FIELDS = (
    "project_root",
    "code_dir",
    "data_dir",
    "checkpoint_dir",
    "readme_path",
)


def _read_registry(path: Path) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    projects = payload.get("projects") if isinstance(payload, dict) else None
    if not isinstance(projects, list):
        return []
    return [dict(project) for project in projects if isinstance(project, dict)]


def _atomic_write_registry(path: Path, projects: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f"{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    )
    temporary.write_text(
        json.dumps({"projects": projects}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def _contained_path(root: Path, raw: object) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve(strict=False)
    try:
        relative = candidate.relative_to(root.resolve(strict=False))
    except ValueError:
        return None
    if not relative.parts:
        return None
    return candidate


def _source_project_root(record: dict, source_root: Path) -> Path | None:
    candidate = _contained_path(source_root, record.get("project_root"))
    if candidate is not None:
        return candidate
    project_id = str(record.get("id") or "")
    if not project_id or Path(project_id).name != project_id:
        return None
    return _contained_path(source_root, project_id)


def _destination_project_root(
    existing: dict | None,
    destination_root: Path,
    source_project: Path,
) -> Path:
    if existing:
        candidate = _contained_path(destination_root, existing.get("project_root"))
        if candidate is not None:
            return candidate
    return (destination_root / source_project.name).resolve(strict=False)


def _merge_record(source: dict, existing: dict | None) -> dict:
    merged = dict(source)
    if existing:
        for key, value in existing.items():
            if value not in (None, "", [], {}):
                merged[key] = value
    return merged


def _rewrite_project_paths(
    record: dict,
    source_project: Path,
    destination_project: Path,
) -> dict:
    rewritten = dict(record)
    source_resolved = source_project.resolve(strict=False)
    destination_resolved = destination_project.resolve(strict=False)
    rewritten["project_root"] = str(destination_resolved)
    for field in _PATH_FIELDS[1:]:
        raw = rewritten.get(field)
        if not isinstance(raw, str) or not raw.strip():
            continue
        value = Path(raw).expanduser()
        if not value.is_absolute():
            rewritten[field] = str(destination_resolved / value)
            continue
        try:
            relative = value.resolve(strict=False).relative_to(source_resolved)
        except ValueError:
            # External dataset paths are legitimate legacy metadata and must
            # not be silently rebased into application storage.
            continue
        rewritten[field] = str(destination_resolved / relative)
    return rewritten


def migrate_checkout_legacy_store(source_root: Path, destination_root: Path) -> dict:
    """Move registered legacy projects into durable storage and merge metadata.

    The historical registry is deliberately left untouched.  That makes a
    failed registry write recoverable: a later run can observe the moved
    directory and recreate the durable entry.  Unregistered directories are
    never inferred as projects or moved.
    """
    source_root = Path(source_root).expanduser().resolve(strict=False)
    destination_root = Path(destination_root).expanduser().resolve(strict=False)
    source_registry = source_root / "v2_projects.json"
    destination_registry = destination_root / "v2_projects.json"
    result = {"moved": [], "merged": [], "conflicts": [], "skipped": []}

    if source_root == destination_root or not source_registry.is_file():
        return result

    source_projects = _read_registry(source_registry)
    destination_projects = _read_registry(destination_registry)
    destination_by_id = {
        str(project.get("id")): project
        for project in destination_projects
        if project.get("id")
    }
    output = list(destination_projects)
    output_index = {
        str(project.get("id")): index
        for index, project in enumerate(output)
        if project.get("id")
    }

    for source_record in source_projects:
        project_id = str(source_record.get("id") or "")
        if not project_id or Path(project_id).name != project_id:
            result["skipped"].append(project_id or "<missing-id>")
            continue
        source_project = _source_project_root(source_record, source_root)
        if source_project is None or source_project.is_symlink():
            result["skipped"].append(project_id)
            continue

        existing = destination_by_id.get(project_id)
        destination_project = _destination_project_root(
            existing, destination_root, source_project
        )
        source_exists = source_project.is_dir()
        destination_exists = destination_project.is_dir()

        physical_conflict = (
            source_exists
            and destination_exists
            and source_project != destination_project
        )
        if physical_conflict:
            if existing is None:
                # An unrelated directory at the target name is not evidence
                # that it is this project.  Never overwrite or adopt it.
                result["conflicts"].append(project_id)
                continue
            result["conflicts"].append(project_id)
        elif source_exists and not destination_exists:
            destination_project.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_project), str(destination_project))
            destination_exists = True
            result["moved"].append(project_id)
        elif not source_exists and not destination_exists:
            result["skipped"].append(project_id)
            continue

        merged = _merge_record(source_record, existing)
        if physical_conflict and existing:
            # Metadata can be filled safely, but paths from the retained source
            # tree must not be made to describe the different durable tree.
            for field in _PATH_FIELDS[1:]:
                if existing.get(field) in (None, ""):
                    merged.pop(field, None)
        merged = _rewrite_project_paths(
            merged, source_project, destination_project
        )
        if project_id in output_index:
            output[output_index[project_id]] = merged
        else:
            output_index[project_id] = len(output)
            output.append(merged)
        destination_by_id[project_id] = merged
        result["merged"].append(project_id)

    if output != destination_projects:
        _atomic_write_registry(destination_registry, output)
    return result
