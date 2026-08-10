"""Platform-aware local storage for DiscoveryAgent projects."""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path


DATA_DIR_ENV = "DISCOVERYAGENT_DATA_DIR"


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


def get_runtime_root(**kwargs) -> Path:
    """Return the root for durable application runtime state."""
    return get_data_root(**kwargs) / "runtime"


def get_flask_session_dir(**kwargs) -> Path:
    """Return the filesystem-session directory without creating it."""
    return get_runtime_root(**kwargs) / "flask_session"


def get_legacy_root(**kwargs) -> Path:
    """Return the root for retained pre-managed-project compatibility data."""
    return get_data_root(**kwargs) / "legacy"


def get_legacy_disk_root(**kwargs) -> Path:
    """Return the durable replacement for the historical ``./local_disk``."""
    return get_legacy_root(**kwargs) / "local_disk"


def get_historical_checkout_legacy_disk_root() -> Path:
    """Return the pre-cleanup checkout-local legacy store without creating it.

    The location is derived from the installed module rather than the process
    working directory.  In the editable/source installation that needs this
    one-time compatibility bridge, ``project_storage.py`` lives beneath the
    checkout that formerly owned ``local_disk``.
    """
    return Path(__file__).resolve().parents[1] / "local_disk"


def get_legacy_auth_root(**kwargs) -> Path:
    """Return the durable root for retained registration/key/progress state."""
    return get_legacy_root(**kwargs) / "auth_submissions"


def get_legacy_interaction_log_path(**kwargs) -> Path:
    """Return the read-only source used by the optional legacy-log migration."""
    return get_legacy_root(**kwargs) / "interaction_logs" / "audit_interactions.jsonl"


def get_project_root(project_id: str, **kwargs) -> Path:
    if not project_id or Path(project_id).name != project_id:
        raise ValueError("Invalid project id")
    return get_projects_root(**kwargs) / project_id


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
