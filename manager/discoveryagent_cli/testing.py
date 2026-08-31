"""Internal fixture builder for manager tests and development only."""

from __future__ import annotations

import json
from pathlib import Path

from . import MANAGER_VERSION
from .paths import InstallationPaths, release_environment_python
from .state import (
    CliState,
    InstalledReleaseState,
    ManagerState,
    RuntimeState,
    STATE_SCHEMA_VERSION,
    write_state_atomic,
)


DEFAULT_FRONTEND_COMMIT = "f4eef14d4d449199db2c3b5f4d856a5e13f5b946"
DEFAULT_FRONTEND_TREE = "a18e73b7f416e3c54c961ca327030bd616560251"
DEFAULT_BACKEND_COMMIT = "d547cedabc0d983b518d1a1c9e66df21ea249560"
DEFAULT_RELEASE_ID = f"dev-{DEFAULT_FRONTEND_TREE[:12]}-{DEFAULT_BACKEND_COMMIT[:12]}"


def fixture_release_metadata(release_id: str = DEFAULT_RELEASE_ID) -> dict:
    return {
        "schema_version": 1,
        "release_id": release_id,
        "release_type": "development",
        "required_cpython_version": "3.11.11",
        "application_schema_version": 1,
        "frontend": {
            "commit": DEFAULT_FRONTEND_COMMIT,
            "tree": DEFAULT_FRONTEND_TREE,
        },
        "backend": {"commit": DEFAULT_BACKEND_COMMIT},
    }


def create_fake_installation(
    root: Path,
    *,
    release_id: str = DEFAULT_RELEASE_ID,
    platform_name: str = "darwin",
) -> tuple[InstallationPaths, ManagerState]:
    """Construct a non-runnable but structurally valid installed-layout fixture."""
    paths = InstallationPaths(root.resolve())
    release_root = paths.release_root(release_id)
    frontend = release_root / "application" / "frontend"
    backend = release_root / "application" / "backend"
    (frontend / "da_selection_ui").mkdir(parents=True)
    (backend / "scripts").mkdir(parents=True)
    (frontend / "da_selection_ui" / "__main__.py").write_text("", encoding="utf-8")
    (frontend / "da_selection_ui" / "production_server.py").write_text("", encoding="utf-8")
    (backend / "scripts" / "run_hb_sherlock.py").write_text("", encoding="utf-8")
    (release_root / "release.json").write_text(
        json.dumps(fixture_release_metadata(release_id), indent=2) + "\n",
        encoding="utf-8",
    )
    (release_root / "complete").write_text("complete\n", encoding="utf-8")
    for environment_name in ("frontend", "backend"):
        python = release_environment_python(
            release_root, environment_name, platform_name
        )
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("fixture interpreter\n", encoding="utf-8")

    runtime_python = paths.runtime_root / "python" / "python"
    runtime_uv = paths.runtime_root / "uv" / "uv"
    runtime_python.parent.mkdir(parents=True)
    runtime_uv.parent.mkdir(parents=True)
    runtime_python.write_text("fixture runtime\n", encoding="utf-8")
    runtime_uv.write_text("fixture uv\n", encoding="utf-8")
    paths.manager_root.mkdir(parents=True)
    paths.cache_root.mkdir(parents=True)
    data_root = root / "fixture-data"

    state = ManagerState(
        schema_version=STATE_SCHEMA_VERSION,
        active_release_id=release_id,
        installed_releases={
            release_id: InstalledReleaseState(path=f"releases/{release_id}")
        },
        manager_version=MANAGER_VERSION,
        runtime=RuntimeState(
            python_version="3.11.11",
            python_path=str(runtime_python),
            uv_version="fixture",
            uv_path=str(runtime_uv),
        ),
        data_root=str(data_root),
        platform=platform_name,
        architecture="fixture-architecture",
        cli=CliState(
            path=str(paths.manager_root / "discoveryagent"),
            path_change_added=False,
            modified_profile=None,
        ),
    )
    write_state_atomic(paths.state_file, state)
    return paths, state
