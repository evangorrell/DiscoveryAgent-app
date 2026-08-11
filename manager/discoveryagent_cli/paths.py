"""Canonical paths for one installed DiscoveryAgent instance."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .errors import StateError


INTERNAL_INSTALL_ROOT_ENV = "DISCOVERYAGENT_INTERNAL_INSTALL_ROOT"
_RELEASE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class InstallationPaths:
    """Filesystem contract rooted at one explicit installation directory."""

    root: Path

    @property
    def state_file(self) -> Path:
        return self.root / "state.json"

    @property
    def manager_root(self) -> Path:
        return self.root / "manager"

    @property
    def runtime_root(self) -> Path:
        return self.root / "runtime"

    @property
    def releases_root(self) -> Path:
        return self.root / "releases"

    @property
    def cache_root(self) -> Path:
        return self.root / "cache"

    @property
    def frontend_runtime_file(self) -> Path:
        return self.runtime_root / "frontend.json"

    @property
    def frontend_launch_lock(self) -> Path:
        return self.runtime_root / "frontend.launch.lock"

    @property
    def frontend_log_file(self) -> Path:
        return self.runtime_root / "logs" / "frontend.log"

    @property
    def frontend_cwd(self) -> Path:
        return self.runtime_root / "frontend-cwd"

    def release_root(self, release_id: str) -> Path:
        validate_release_id(release_id)
        return self.releases_root / release_id


def validate_release_id(release_id: str) -> str:
    if not isinstance(release_id, str) or not _RELEASE_ID_PATTERN.fullmatch(release_id):
        raise StateError(f"Invalid active release ID: {release_id!r}")
    return release_id


def resolve_installation_root(
    explicit: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    module_file: str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve the install root without consulting developer repositories.

    Normal installed execution derives the root from ``manager/``. Tests and
    development tools can use an explicit value or the deliberately internal
    environment variable.
    """
    env = os.environ if environ is None else environ
    selected = explicit or env.get(INTERNAL_INSTALL_ROOT_ENV)
    if selected:
        return Path(selected).expanduser().resolve()
    source = Path(module_file or __file__).resolve()
    return source.parents[2]


def environment_python(environment_root: Path, platform_name: str | None = None) -> Path:
    """Return the platform-specific interpreter path for an application env."""
    platform_name = sys.platform if platform_name is None else platform_name
    if platform_name == "win32":
        return environment_root / "Scripts" / "python.exe"
    return environment_root / "bin" / "python"


def release_environment_python(
    release_root: Path,
    environment_name: str,
    platform_name: str | None = None,
) -> Path:
    if environment_name not in {"frontend", "backend"}:
        raise ValueError(f"Unknown application environment: {environment_name}")
    return environment_python(
        release_root / "envs" / environment_name,
        platform_name,
    )
