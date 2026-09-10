"""Bounded, read-only metadata extraction for importing legacy projects.

Only exact DiscoveryAgent-owned paths are inspected.  No directory walk is
performed, symlinks are never followed, and unrecognized configuration keys
are deliberately ignored.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any


MAX_METADATA_BYTES = 256 * 1024
MAX_FIELD_CHARS = 20_000
MAX_SPLIT_LAYOUT_ENTRIES = 2_048

# Earlier HPC layouts placed ``checkpoints`` directly beneath the selected
# project root.  Current local projects nest it beneath ``checkpoint``.
RECOGNIZED_JSON_CONFIGS = (
    Path("checkpoint/checkpoints/round_0/config.json"),
    Path("checkpoints/round_0/config.json"),
)
RECOGNIZED_YAML_CONFIGS = (
    Path("checkpoint/checkpoints/config.yaml"),
    Path("checkpoint/checkpoints/config.yml"),
    Path("checkpoints/config.yaml"),
    Path("checkpoints/config.yml"),
)
RECOGNIZED_CONTEXT_FILES = (
    Path("code/data/README.md"),
    Path("code/README.md"),
)
RECOGNIZED_LAYOUT_DIRS = (
    Path("code"),
    Path("code/data"),
    Path("checkpoint/checkpoints/round_0"),
    Path("checkpoints/round_0"),
)

_YAML_SCALAR = re.compile(r"^(dataset_name|task_description):[ \t]*(.*)$")


class ProjectImportMetadataError(ValueError):
    """A recognized metadata file exists but cannot be inspected safely."""


def _split_layout(root: Path) -> dict[str, Path] | None:
    """Recognize one historical ``code_<run>``/``logs_<run>`` sibling pair.

    Some archived HPC exports preserve the backend's two output roots beneath
    one bundle directory instead of renaming them to ``code`` and
    ``checkpoint``.  Inspect only immediate children and require matching,
    unique suffixes so unrelated folders are never guessed into a project.
    """
    if any(
        _recognized_directory(root, relative) is not None
        for relative in (Path("code"), Path("checkpoint"), Path("checkpoints"))
    ):
        return None
    code_roots: dict[str, Path] = {}
    log_roots: dict[str, Path] = {}
    try:
        with os.scandir(root) as entries:
            for index, entry in enumerate(entries, start=1):
                if index > MAX_SPLIT_LAYOUT_ENTRIES:
                    raise ProjectImportMetadataError(
                        "DiscoveryAgent cannot inspect a split legacy project with "
                        f"more than {MAX_SPLIT_LAYOUT_ENTRIES} top-level entries."
                    )
                name = entry.name
                if name.startswith("code_"):
                    collection, suffix = code_roots, name.removeprefix("code_")
                elif name.startswith("logs_"):
                    collection, suffix = log_roots, name.removeprefix("logs_")
                else:
                    continue
                if not suffix or not entry.is_dir(follow_symlinks=False):
                    continue
                collection[suffix] = Path(entry.path)
    except ProjectImportMetadataError:
        raise
    except OSError as exc:
        raise ProjectImportMetadataError(
            "DiscoveryAgent could not inspect the selected project's top-level layout."
        ) from exc

    pairs: list[dict[str, Path]] = []
    for suffix in sorted(code_roots.keys() & log_roots.keys()):
        code_dir = code_roots[suffix]
        logs_dir = log_roots[suffix]
        checkpoint_dir = _recognized_directory(
            root, logs_dir.relative_to(root) / "checkpoints" / "round_0"
        )
        if checkpoint_dir is None:
            continue
        pairs.append({
            "code_dir": code_dir,
            "logs_dir": logs_dir,
            "checkpoint_dir": checkpoint_dir,
        })
    if len(pairs) > 1:
        raise ProjectImportMetadataError(
            "Multiple matching code/log run pairs were found; select a folder containing one archived run."
        )
    return pairs[0] if pairs else None


def _candidate_without_symlinks(root: Path, relative: Path) -> Path | None:
    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ProjectImportMetadataError(
                f"DiscoveryAgent could not safely inspect {relative.as_posix()}."
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise ProjectImportMetadataError(
                f"DiscoveryAgent metadata cannot be read through a symbolic link: {relative.as_posix()}."
            )
    return candidate


def _read_recognized_text(root: Path, relative: Path) -> str | None:
    candidate = _candidate_without_symlinks(root, relative)
    if candidate is None:
        return None
    try:
        metadata = candidate.stat()
    except OSError as exc:
        raise ProjectImportMetadataError(
            f"DiscoveryAgent could not safely inspect {relative.as_posix()}."
        ) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ProjectImportMetadataError(
            f"Recognized DiscoveryAgent metadata is not a regular file: {relative.as_posix()}."
        )
    if metadata.st_size > MAX_METADATA_BYTES:
        raise ProjectImportMetadataError(
            f"Recognized DiscoveryAgent metadata is too large to import safely: {relative.as_posix()}."
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise ProjectImportMetadataError(
            f"DiscoveryAgent could not read {relative.as_posix()}."
        ) from exc
    if len(raw) > MAX_METADATA_BYTES:
        raise ProjectImportMetadataError(
            f"Recognized DiscoveryAgent metadata is too large to import safely: {relative.as_posix()}."
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProjectImportMetadataError(
            f"Recognized DiscoveryAgent metadata is not valid UTF-8: {relative.as_posix()}."
        ) from exc


def _recognized_directory(root: Path, relative: Path) -> Path | None:
    candidate = _candidate_without_symlinks(root, relative)
    if candidate is None:
        return None
    try:
        metadata = candidate.stat()
    except OSError as exc:
        raise ProjectImportMetadataError(
            f"DiscoveryAgent could not safely inspect {relative.as_posix()}."
        ) from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise ProjectImportMetadataError(
            f"Recognized DiscoveryAgent project layout is not a directory: {relative.as_posix()}."
        )
    return candidate


def _clean_scalar(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_FIELD_CHARS or "\x00" in normalized:
        return None
    return normalized


def _json_fields(text: str, relative: Path) -> dict[str, str]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProjectImportMetadataError(
            f"Recognized DiscoveryAgent configuration is invalid JSON: {relative.as_posix()}."
        ) from exc
    if not isinstance(payload, dict):
        return {}
    config = payload.get("config") if isinstance(payload.get("config"), dict) else payload
    return {
        field: cleaned
        for field, key in (("project_name", "dataset_name"), ("task_description", "task_description"))
        if (cleaned := _clean_scalar(config.get(key))) is not None
    }


def _yaml_value(raw: str) -> str | None:
    value = raw.strip()
    if not value or value in {"|", ">", "null", "Null", "NULL", "~"}:
        return None
    if value.startswith(("!", "&", "*", "{", "[")):
        return None
    if value.startswith('"') and value.endswith('"'):
        try:
            return _clean_scalar(json.loads(value))
        except json.JSONDecodeError:
            return None
    if value.startswith("'") and value.endswith("'"):
        return _clean_scalar(value[1:-1].replace("''", "'"))
    # Plain top-level YAML scalars are accepted only as text.  Path-like and
    # runtime keys are never considered because the allowlist above excludes
    # them before this parser is reached.
    return _clean_scalar(value)


def _yaml_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith((" ", "\t")):
            continue
        match = _YAML_SCALAR.fullmatch(line)
        if not match:
            continue
        key, raw_value = match.groups()
        cleaned = _yaml_value(raw_value)
        if cleaned is not None:
            fields["project_name" if key == "dataset_name" else key] = cleaned
    return fields


def _context_from_readme(text: str) -> str | None:
    value = text.strip()
    if value.startswith("# "):
        _heading, separator, remainder = value.partition("\n")
        if separator:
            value = remainder.strip()
    return _clean_scalar(value)


def _resolve_field(
    field: str,
    candidates: list[tuple[str, str]],
    warnings: list[str],
) -> str:
    distinct: list[tuple[str, str]] = []
    for source, value in candidates:
        if not any(existing == value for _existing_source, existing in distinct):
            distinct.append((source, value))
    if not distinct:
        return ""
    if len(distinct) == 1:
        return distinct[0][1]
    label = {
        "project_name": "project names",
        "task_description": "research objectives",
        "dataset_context": "project contexts",
    }[field]
    warnings.append(
        f"Recognized DiscoveryAgent files contain conflicting {label}; review and enter this field manually."
    )
    return ""


def inspect_project_metadata(root: Path) -> dict[str, Any]:
    """Return safe Create-form suggestions without changing ``root``."""
    root = Path(root)
    split = _split_layout(root)
    values: dict[str, list[tuple[str, str]]] = {
        "project_name": [],
        "task_description": [],
        "dataset_context": [],
    }
    recognized: list[str] = []
    metadata_digest = hashlib.sha256()

    def record_candidate(relative: Path, text: str | None) -> None:
        metadata_digest.update(relative.as_posix().encode("utf-8"))
        metadata_digest.update(b"\x00" if text is None else b"\x01" + text.encode("utf-8"))
    for relative in RECOGNIZED_JSON_CONFIGS:
        text = _read_recognized_text(root, relative)
        record_candidate(relative, text)
        if text is None:
            continue
        recognized.append(relative.as_posix())
        for field, value in _json_fields(text, relative).items():
            values[field].append((relative.as_posix(), value))
    for relative in RECOGNIZED_YAML_CONFIGS:
        text = _read_recognized_text(root, relative)
        record_candidate(relative, text)
        if text is None:
            continue
        recognized.append(relative.as_posix())
        for field, value in _yaml_fields(text).items():
            values[field].append((relative.as_posix(), value))
    for relative in RECOGNIZED_CONTEXT_FILES:
        text = _read_recognized_text(root, relative)
        record_candidate(relative, text)
        if text is None:
            continue
        recognized.append(relative.as_posix())
        context = _context_from_readme(text)
        if context is not None:
            values["dataset_context"].append((relative.as_posix(), context))
    if split is not None:
        split_code = split["code_dir"].relative_to(root)
        split_logs = split["logs_dir"].relative_to(root)
        split_json_configs = (
            split_logs / "checkpoints" / "round_0" / "config.json",
        )
        split_yaml_configs = (
            split_logs / "checkpoints" / "config.yaml",
            split_logs / "checkpoints" / "config.yml",
        )
        split_context_files = (
            split_code / "data" / "README.md",
            split_code / "data" / "README.txt",
            split_code / "README.md",
            split_code / "README.txt",
        )
        for relative in split_json_configs:
            text = _read_recognized_text(root, relative)
            record_candidate(relative, text)
            if text is None:
                continue
            recognized.append(relative.as_posix())
            for field, value in _json_fields(text, relative).items():
                values[field].append((relative.as_posix(), value))
        for relative in split_yaml_configs:
            text = _read_recognized_text(root, relative)
            record_candidate(relative, text)
            if text is None:
                continue
            recognized.append(relative.as_posix())
            for field, value in _yaml_fields(text).items():
                values[field].append((relative.as_posix(), value))
        for relative in split_context_files:
            text = _read_recognized_text(root, relative)
            record_candidate(relative, text)
            if text is None:
                continue
            recognized.append(relative.as_posix())
            context = _context_from_readme(text)
            if context is not None:
                values["dataset_context"].append((relative.as_posix(), context))
    for relative in RECOGNIZED_LAYOUT_DIRS:
        directory = _recognized_directory(root, relative)
        metadata_digest.update(relative.as_posix().encode("utf-8"))
        metadata_digest.update(b"\x00" if directory is None else b"\x02")
    if split is not None:
        for key in ("code_dir", "logs_dir", "checkpoint_dir"):
            relative = split[key].relative_to(root)
            metadata_digest.update(relative.as_posix().encode("utf-8"))
            metadata_digest.update(b"\x02")

    warnings: list[str] = []
    project_name = _resolve_field("project_name", values["project_name"], warnings)
    if not project_name and not values["project_name"]:
        project_name = root.name
    return {
        "project_name": project_name,
        "task_description": _resolve_field(
            "task_description", values["task_description"], warnings
        ),
        "dataset_context": _resolve_field(
            "dataset_context", values["dataset_context"], warnings
        ),
        "warnings": warnings,
        "recognized_files": recognized,
        "metadata_token": metadata_digest.hexdigest(),
    }


def recognized_layout(root: Path) -> dict[str, Path | None]:
    """Choose existing canonical subpaths without walking project contents."""
    root = Path(root)
    split = _split_layout(root)
    canonical_code_dir = root / "code"
    code_dir = (
        canonical_code_dir
        if _recognized_directory(root, Path("code")) is not None or split is None
        else split["code_dir"]
    )
    data_dir = code_dir / "data"
    checkpoint_candidates = (
        Path("checkpoint/checkpoints/round_0"),
        Path("checkpoints/round_0"),
    )
    checkpoint_dir = next(
        (
            candidate
            for relative in checkpoint_candidates
            if (candidate := _recognized_directory(root, relative)) is not None
        ),
        None,
    )
    if checkpoint_dir is None and split is not None:
        checkpoint_dir = split["checkpoint_dir"]
    context_files = list(RECOGNIZED_CONTEXT_FILES)
    if split is not None and code_dir == split["code_dir"]:
        split_code = split["code_dir"].relative_to(root)
        context_files = [
            split_code / "data" / "README.md",
            split_code / "data" / "README.txt",
            split_code / "README.md",
            split_code / "README.txt",
        ]
    readme_path = next(
        (
            candidate
            for relative in context_files
            if (candidate := _candidate_without_symlinks(root, relative)) is not None
        ),
        None,
    )
    return {
        "code_dir": code_dir,
        "data_dir": data_dir,
        "checkpoint_dir": checkpoint_dir,
        "readme_path": readme_path,
    }
