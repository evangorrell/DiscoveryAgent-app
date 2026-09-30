"""Active-release metadata resolution and future-launch validation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import LaunchValidationError, ReleaseError, StateError
from .paths import InstallationPaths, release_environment_python
from .state import ManagerState
from .release_version import validate_release_version


_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseError(f"{label} must be a JSON object")
    return value


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReleaseError(f"{label} must be a non-empty string")
    return value


@dataclass(frozen=True)
class ReleaseMetadata:
    schema_version: int
    application_schema_version: int
    release_id: str
    release_type: str
    required_cpython_version: str
    frontend_commit: str
    frontend_tree: str
    backend_commit: str
    package_revision: int = 0
    release_version: str | None = None

    @property
    def installation_id(self) -> str:
        return f"{self.release_id}-pkg{self.package_revision}" if self.package_revision else self.release_id

    @classmethod
    def from_dict(cls, value: Any) -> "ReleaseMetadata":
        payload = _mapping(value, "release.json")
        frontend = _mapping(payload.get("frontend"), "release.json frontend")
        backend = _mapping(payload.get("backend"), "release.json backend")
        schema_version = payload.get("schema_version")
        application_schema_version = payload.get("application_schema_version")
        if schema_version != 1:
            raise ReleaseError(f"Unsupported release schema version: {schema_version!r}")
        if not isinstance(application_schema_version, int) or application_schema_version < 1:
            raise ReleaseError("application_schema_version must be a positive integer")
        frontend_commit = _required_string(frontend.get("commit"), "frontend.commit")
        frontend_tree = _required_string(frontend.get("tree"), "frontend.tree")
        backend_commit = _required_string(backend.get("commit"), "backend.commit")
        if not _COMMIT_PATTERN.fullmatch(frontend_commit):
            raise ReleaseError("frontend.commit must be a full lowercase Git SHA")
        if not _COMMIT_PATTERN.fullmatch(frontend_tree):
            raise ReleaseError("frontend.tree must be a full lowercase Git SHA")
        if not _COMMIT_PATTERN.fullmatch(backend_commit):
            raise ReleaseError("backend.commit must be a full lowercase Git SHA")
        revision = payload.get("package_revision", 0)
        if type(revision) is not int or revision < 0:
            raise ReleaseError("package_revision must be a non-negative integer")
        version = None
        if 'release_version' in payload:
            try:
                version = validate_release_version(payload['release_version'])
            except ValueError as exc:
                raise ReleaseError(str(exc)) from exc
        return cls(
            schema_version=schema_version,
            application_schema_version=application_schema_version,
            release_id=_required_string(payload.get("release_id"), "release_id"),
            release_type=_required_string(payload.get("release_type"), "release_type"),
            required_cpython_version=_required_string(
                payload.get("required_cpython_version"), "required_cpython_version"
            ),
            frontend_commit=frontend_commit,
            frontend_tree=frontend_tree,
            backend_commit=backend_commit,
            package_revision=revision,
            release_version=version,
        )


@dataclass(frozen=True)
class ActiveRelease:
    root: Path
    metadata: ReleaseMetadata

    @property
    def frontend_root(self) -> Path:
        return self.root / "application" / "frontend"

    @property
    def backend_root(self) -> Path:
        return self.root / "application" / "backend"


@dataclass(frozen=True)
class ValidatedLaunch:
    state: ManagerState
    release: ActiveRelease
    frontend_python: Path
    backend_python: Path
    data_root: Path


def load_active_release(
    paths: InstallationPaths,
    state: ManagerState,
    *,
    require_layout: bool = False,
) -> ActiveRelease:
    release_id = state.active_release_id
    if release_id not in state.installed_releases:
        raise StateError(
            f"Active release {release_id!r} is not listed in installed_releases"
        )
    release_root = paths.release_root(release_id)
    metadata_path = release_root / "release.json"
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReleaseError(f"Active release metadata is missing: {metadata_path}") from exc
    except json.JSONDecodeError as exc:
        raise ReleaseError(
            f"Active release metadata is malformed JSON at line {exc.lineno}"
        ) from exc
    except OSError as exc:
        raise ReleaseError(f"Could not read active release metadata: {exc}") from exc
    metadata = ReleaseMetadata.from_dict(payload)
    if metadata.installation_id != release_id:
        raise ReleaseError(
            f"Active release ID mismatch: state selects {release_id!r}, "
            f"release.json declares {metadata.release_id!r}"
        )
    release = ActiveRelease(release_root, metadata)
    if require_layout:
        _validate_release_layout(release)
    return release


def _require_directory(path: Path, label: str) -> None:
    if not path.is_dir():
        raise LaunchValidationError(f"{label} is missing: {path}")


def _require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise LaunchValidationError(f"{label} is missing: {path}")


def _validate_release_layout(release: ActiveRelease) -> None:
    _require_file(release.root / "complete", "Active release completion marker")
    _require_directory(release.frontend_root, "Frontend application snapshot")
    _require_directory(release.backend_root, "Backend application snapshot")


def validate_launch_installation(
    paths: InstallationPaths,
    state: ManagerState,
    *,
    platform_name: str | None = None,
) -> ValidatedLaunch:
    """Validate everything Phase 2A.3 will need, without starting a process."""
    release = load_active_release(paths, state, require_layout=True)
    if state.runtime.python_version != release.metadata.required_cpython_version:
        raise LaunchValidationError(
            "Runtime Python version does not match the active release: "
            f"state has {state.runtime.python_version}, release requires "
            f"{release.metadata.required_cpython_version}"
        )
    frontend_python = release_environment_python(
        release.root, "frontend", platform_name
    )
    backend_python = release_environment_python(
        release.root, "backend", platform_name
    )
    _require_file(frontend_python, "Frontend environment Python")
    _require_file(backend_python, "Backend environment Python")
    _require_file(
        release.frontend_root / "da_selection_ui" / "__main__.py",
        "Frontend production module entry point",
    )
    _require_file(
        release.frontend_root / "da_selection_ui" / "production_server.py",
        "Frontend production server",
    )
    _require_file(
        release.backend_root / "scripts" / "run_hb_sherlock.py",
        "Backend round launcher",
    )

    data_root = Path(state.data_root).expanduser()
    try:
        data_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise LaunchValidationError(
            f"DiscoveryAgent data root cannot be created: {data_root}: {exc}"
        ) from exc
    if not data_root.is_dir():
        raise LaunchValidationError(
            f"DiscoveryAgent data root is not a directory: {data_root}"
        )
    return ValidatedLaunch(
        state=state,
        release=release,
        frontend_python=frontend_python,
        backend_python=backend_python,
        data_root=data_root,
    )
