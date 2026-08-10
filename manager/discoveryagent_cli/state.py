"""Schema-v1 state model for an installed DiscoveryAgent instance."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .errors import StateError
from .paths import validate_release_id


STATE_SCHEMA_VERSION = 1


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise StateError(f"{label} must be a JSON object")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    missing = expected.difference(value)
    extra = set(value).difference(expected)
    if missing:
        raise StateError(f"{label} is missing fields: {', '.join(sorted(missing))}")
    if extra:
        raise StateError(f"{label} contains unsupported fields: {', '.join(sorted(extra))}")


def _string(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise StateError(f"{label} must be a non-empty string")
    return value


def _absolute_path(value: Any, label: str, *, allow_empty: bool = False) -> str:
    raw = _string(value, label, allow_empty=allow_empty)
    if allow_empty and not raw:
        return raw
    if not Path(raw).is_absolute():
        raise StateError(f"{label} must be an absolute path")
    return raw


@dataclass(frozen=True)
class InstalledReleaseState:
    path: str

    @classmethod
    def from_dict(cls, value: Any, release_id: str) -> "InstalledReleaseState":
        payload = _mapping(value, f"installed_releases.{release_id}")
        _exact_keys(payload, {"path"}, f"installed_releases.{release_id}")
        path = _string(payload["path"], f"installed_releases.{release_id}.path")
        pure = PurePosixPath(path)
        expected = PurePosixPath("releases") / release_id
        if pure.is_absolute() or ".." in pure.parts or pure != expected:
            raise StateError(
                f"installed_releases.{release_id}.path must be {expected.as_posix()}"
            )
        return cls(path=path)

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path}


@dataclass(frozen=True)
class RuntimeState:
    python_version: str
    python_path: str
    uv_version: str
    uv_path: str

    @classmethod
    def from_dict(cls, value: Any) -> "RuntimeState":
        payload = _mapping(value, "runtime")
        expected = {"python_version", "python_path", "uv_version", "uv_path"}
        _exact_keys(payload, expected, "runtime")
        return cls(
            python_version=_string(payload["python_version"], "runtime.python_version"),
            python_path=_absolute_path(payload["python_path"], "runtime.python_path"),
            uv_version=_string(payload["uv_version"], "runtime.uv_version"),
            uv_path=_absolute_path(payload["uv_path"], "runtime.uv_path"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "python_version": self.python_version,
            "python_path": self.python_path,
            "uv_version": self.uv_version,
            "uv_path": self.uv_path,
        }


@dataclass(frozen=True)
class CliState:
    path: str
    path_change_added: bool
    modified_profile: str | None

    @classmethod
    def from_dict(cls, value: Any) -> "CliState":
        payload = _mapping(value, "cli")
        _exact_keys(payload, {"path", "path_change_added", "modified_profile"}, "cli")
        path = _absolute_path(payload["path"], "cli.path", allow_empty=True)
        path_change_added = payload["path_change_added"]
        if not isinstance(path_change_added, bool):
            raise StateError("cli.path_change_added must be a boolean")
        modified_profile = payload["modified_profile"]
        if modified_profile is not None:
            modified_profile = _absolute_path(modified_profile, "cli.modified_profile")
        return cls(path, path_change_added, modified_profile)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "path_change_added": self.path_change_added,
            "modified_profile": self.modified_profile,
        }


@dataclass(frozen=True)
class ManagerState:
    schema_version: int
    active_release_id: str
    installed_releases: dict[str, InstalledReleaseState]
    manager_version: str
    runtime: RuntimeState
    data_root: str
    platform: str
    architecture: str
    cli: CliState

    @classmethod
    def from_dict(cls, value: Any) -> "ManagerState":
        payload = _mapping(value, "state")
        expected = {
            "schema_version",
            "active_release_id",
            "installed_releases",
            "manager_version",
            "runtime",
            "data_root",
            "platform",
            "architecture",
            "cli",
        }
        _exact_keys(payload, expected, "state")
        schema_version = payload["schema_version"]
        if schema_version != STATE_SCHEMA_VERSION:
            raise StateError(
                f"Unsupported state schema version {schema_version!r}; "
                f"expected {STATE_SCHEMA_VERSION}"
            )
        active_release_id = validate_release_id(
            _string(payload["active_release_id"], "active_release_id")
        )
        release_payload = _mapping(payload["installed_releases"], "installed_releases")
        installed_releases: dict[str, InstalledReleaseState] = {}
        for release_id, release_state in release_payload.items():
            validate_release_id(release_id)
            installed_releases[release_id] = InstalledReleaseState.from_dict(
                release_state, release_id
            )
        return cls(
            schema_version=schema_version,
            active_release_id=active_release_id,
            installed_releases=installed_releases,
            manager_version=_string(payload["manager_version"], "manager_version"),
            runtime=RuntimeState.from_dict(payload["runtime"]),
            data_root=_absolute_path(payload["data_root"], "data_root"),
            platform=_string(payload["platform"], "platform"),
            architecture=_string(payload["architecture"], "architecture"),
            cli=CliState.from_dict(payload["cli"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "active_release_id": self.active_release_id,
            "installed_releases": {
                release_id: release.to_dict()
                for release_id, release in sorted(self.installed_releases.items())
            },
            "manager_version": self.manager_version,
            "runtime": self.runtime.to_dict(),
            "data_root": self.data_root,
            "platform": self.platform,
            "architecture": self.architecture,
            "cli": self.cli.to_dict(),
        }


def load_state(path: Path) -> ManagerState:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise StateError(f"Installation state is missing: {path}") from exc
    except OSError as exc:
        raise StateError(f"Could not read installation state {path}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StateError(
            f"Installation state is not valid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    return ManagerState.from_dict(payload)


def write_state_atomic(path: Path, state: ManagerState) -> None:
    """Validate and atomically replace state.json without persisting secrets."""
    payload = state.to_dict()
    ManagerState.from_dict(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
