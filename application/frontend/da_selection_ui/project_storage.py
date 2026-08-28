"""Platform-aware local storage for DiscoveryAgent projects."""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path


DATA_DIR_ENV = "DISCOVERYAGENT_DATA_DIR"
PROJECTS_REGISTRY_FILENAME = "projects.json"


def get_data_root(*, platform: str | None = None, environ=None, home: Path | None = None) -> Path:
    """Return the application data directory without creating it."""
    env = os.environ if environ is None else environ
    override = env.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()

    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else Path(home)
    if platform == "darwin":
        return home / "Library" / "Application Support" / "DiscoveryAgent"
    if platform.startswith("linux"):
        xdg_data_home = env.get("XDG_DATA_HOME")
        base = Path(xdg_data_home).expanduser() if xdg_data_home else home / ".local" / "share"
        return base / "discoveryagent"
    if platform == "win32":
        local_app_data = env.get("LOCALAPPDATA")
        base = Path(local_app_data).expanduser() if local_app_data else home / "AppData" / "Local"
        return base / "DiscoveryAgent"
    return home / ".local" / "share" / "discoveryagent"


def get_projects_root(**kwargs) -> Path:
    return get_data_root(**kwargs) / "projects"


def get_projects_registry_path(**kwargs) -> Path:
    """Return the one authoritative project registry without creating it."""
    return get_data_root(**kwargs) / PROJECTS_REGISTRY_FILENAME


def get_runtime_root(**kwargs) -> Path:
    """Return the root for durable application runtime state."""
    return get_data_root(**kwargs) / "runtime"


def get_flask_session_dir(**kwargs) -> Path:
    """Return the filesystem-session directory without creating it."""
    return get_runtime_root(**kwargs) / "flask_session"


def get_project_root(project_directory_name: str, **kwargs) -> Path:
    """Return a canonical managed root from its stable display-name slug."""
    leaf = str(project_directory_name or "").strip()
    if not leaf or leaf in {".", ".."} or Path(leaf).name != leaf:
        raise ValueError("Invalid project directory name")
    return get_projects_root(**kwargs) / leaf


def new_project_id() -> str:
    """Generate an opaque durable identity; this value is not a credential."""
    return f"prj_{uuid.uuid4().hex}"


def write_project_metadata(project_root: Path, project_id: str, display_name: str) -> Path:
    metadata_path = Path(project_root) / ".discoveryagent" / "project.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"project_id": project_id, "display_name": display_name}
    temporary = metadata_path.with_name(f"{metadata_path.name}.tmp.{uuid.uuid4().hex[:8]}")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(metadata_path)
    return metadata_path


def write_project_metadata_if_absent(
    project_root: Path, project_id: str, display_name: str, *, return_identity: bool = False,
) -> Path | tuple[Path, tuple[int, int]]:
    """Atomically create identity metadata without replacing any existing entry."""
    metadata_path = Path(project_root) / ".discoveryagent" / "project.json"
    metadata_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(
        {"project_id": project_id, "display_name": display_name},
        indent=2,
        ensure_ascii=False,
    ).encode("utf-8")
    temporary = metadata_path.with_name(
        f"{metadata_path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    )
    descriptor = None
    identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            metadata = os.fstat(handle.fileno())
            identity = (metadata.st_dev, metadata.st_ino)
        # A same-directory hard link publishes the complete file atomically and
        # fails rather than replacing an entry created by another process.
        os.link(temporary, metadata_path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    if return_identity:
        if identity is None:  # pragma: no cover - a successful write always sets it
            raise OSError("Could not establish marker ownership.")
        return metadata_path, identity
    return metadata_path
