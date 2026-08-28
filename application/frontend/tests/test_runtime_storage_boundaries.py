"""Installer-facing boundaries for frontend mutable runtime state."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from da_selection_ui import app as app_module
from da_selection_ui.project_storage import (
    get_flask_session_dir,
    get_project_root,
    get_projects_registry_path,
    get_projects_root,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_centralized_runtime_paths_resolve_beneath_data_root(tmp_path):
    data_root = tmp_path / "application-data"
    environ = {"DISCOVERYAGENT_DATA_DIR": str(data_root)}

    assert get_flask_session_dir(environ=environ) == data_root / "runtime" / "flask_session"
    assert get_projects_registry_path(environ=environ) == data_root / "projects.json"
    assert get_projects_root(environ=environ) == data_root / "projects"
    assert get_project_root("normalized-name", environ=environ) == (
        data_root / "projects" / "normalized-name"
    )


def test_clean_startup_publishes_only_projects_json_as_project_registry(tmp_path):
    data_root = tmp_path / "application-data"
    working_directory = tmp_path / "launch-directory"
    working_directory.mkdir()
    environment = os.environ.copy()
    environment["DISCOVERYAGENT_DATA_DIR"] = str(data_root)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, [
        str(REPOSITORY_ROOT),
        str(REPOSITORY_ROOT / ".venv" / "lib" / "python3.11" / "site-packages"),
        environment.get("PYTHONPATH", ""),
    ]))
    script = """
import json
from da_selection_ui import app as app_module

app_module._v2_load_projects()
print("STORAGE_PATHS=" + json.dumps({
    "session": app_module.app.config["SESSION_FILE_DIR"],
    "registry": str(app_module._v2_projects_registry_path()),
    "projects": str(app_module._v2_projects_root()),
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=working_directory,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    payload_line = next(
        line for line in completed.stdout.splitlines()
        if line.startswith("STORAGE_PATHS=")
    )
    paths = {
        key: Path(value).resolve()
        for key, value in json.loads(payload_line.split("=", 1)[1]).items()
    }

    assert paths == {
        "session": data_root / "runtime" / "flask_session",
        "registry": data_root / "projects.json",
        "projects": data_root / "projects",
    }
    assert json.loads(paths["registry"].read_text(encoding="utf-8")) == {"projects": []}
    assert not (data_root / "v2_projects.json").exists()
    assert not (data_root / "legacy").exists()
    assert not (working_directory / "local_disk").exists()


def test_configured_data_root_is_cwd_independent(monkeypatch, tmp_path):
    data_root = tmp_path / "application-data"
    first_cwd = tmp_path / "first-cwd"
    second_cwd = tmp_path / "second-cwd"
    first_cwd.mkdir()
    second_cwd.mkdir()
    monkeypatch.setenv("DISCOVERYAGENT_DATA_DIR", str(data_root))
    monkeypatch.setattr(app_module, "get_data_root", lambda: data_root)

    monkeypatch.chdir(first_cwd)
    first = app_module._get_disk_path()
    monkeypatch.chdir(second_cwd)
    second = app_module._get_disk_path()

    assert first == second == data_root
    assert not (first_cwd / "local_disk").exists()
    assert not (second_cwd / "local_disk").exists()


def test_unrelated_disk_path_environment_does_not_change_storage(monkeypatch, tmp_path):
    data_root = tmp_path / "application-data"
    monkeypatch.setenv("DISCOVERYAGENT_DATA_DIR", str(data_root))
    monkeypatch.setenv("DISK_PATH", str(tmp_path / "must-not-be-used"))
    monkeypatch.setattr(app_module, "get_data_root", lambda: data_root)

    assert app_module._get_disk_path() == data_root
    assert get_projects_registry_path(environ={
        "DISCOVERYAGENT_DATA_DIR": str(data_root),
        "DISK_PATH": str(tmp_path / "must-not-be-used"),
    }) == data_root / "projects.json"
