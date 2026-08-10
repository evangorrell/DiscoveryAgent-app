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
    get_legacy_auth_root,
    get_legacy_disk_root,
    get_legacy_interaction_log_path,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = Path(app_module.__file__).resolve().parent


def _file_snapshot(paths: list[Path]) -> dict[Path, tuple[int, int] | None]:
    snapshot = {}
    for path in paths:
        try:
            stat = path.stat()
        except FileNotFoundError:
            snapshot[path] = None
        else:
            snapshot[path] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def test_centralized_runtime_paths_resolve_beneath_data_root(tmp_path):
    data_root = tmp_path / "application-data"
    environ = {"DISCOVERYAGENT_DATA_DIR": str(data_root)}

    assert get_flask_session_dir(environ=environ) == (
        data_root / "runtime" / "flask_session"
    )
    assert get_legacy_auth_root(environ=environ) == (
        data_root / "legacy" / "auth_submissions"
    )
    assert get_legacy_disk_root(environ=environ) == (
        data_root / "legacy" / "local_disk"
    )
    assert get_legacy_interaction_log_path(environ=environ) == (
        data_root / "legacy" / "interaction_logs" / "audit_interactions.jsonl"
    )


def test_clean_startup_writes_only_to_configured_data_root(tmp_path):
    data_root = tmp_path / "application-data"
    working_directory = tmp_path / "launch-directory"
    working_directory.mkdir()
    historical_source_files = [
        PACKAGE_ROOT / ".auth_submissions" / "registrations.jsonl",
        PACKAGE_ROOT / ".auth_submissions" / "project_keys.json",
        PACKAGE_ROOT / ".auth_submissions" / "project_keys.jsonl",
        PACKAGE_ROOT / ".auth_submissions" / "project_key.hash",
        PACKAGE_ROOT / ".auth_submissions" / "project_progress.json",
        PACKAGE_ROOT / ".interaction_logs" / "audit_interactions.jsonl",
    ]
    source_before = _file_snapshot(historical_source_files)

    environment = os.environ.copy()
    environment["DISCOVERYAGENT_DATA_DIR"] = str(data_root)
    environment.pop("DISK_PATH", None)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, [
        str(REPOSITORY_ROOT),
        environment.get("PYTHONPATH", ""),
    ]))
    script = """
import json
from da_selection_ui import app as app_module

print("STORAGE_PATHS=" + json.dumps({
    "session": app_module.app.config["SESSION_FILE_DIR"],
    "registration": str(app_module.REGISTRATION_LOG_PATH),
    "project_progress": str(app_module.PROJECT_PROGRESS_FILE),
    "legacy_interactions": str(app_module.LEGACY_INTERACTION_LOG_PATH),
    "legacy_disk": str(app_module._get_disk_path()),
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

    assert paths["session"] == data_root / "runtime" / "flask_session"
    assert paths["registration"] == data_root / "legacy" / "auth_submissions" / "registrations.jsonl"
    assert paths["project_progress"] == data_root / "legacy" / "auth_submissions" / "project_progress.json"
    assert paths["legacy_interactions"] == data_root / "legacy" / "interaction_logs" / "audit_interactions.jsonl"
    assert paths["legacy_disk"] == data_root / "legacy" / "local_disk"
    assert paths["session"].is_dir()
    assert not (working_directory / ".flask_session").exists()
    assert not (working_directory / "local_disk").exists()
    assert _file_snapshot(historical_source_files) == source_before


def test_legacy_disk_default_is_durable_and_cwd_independent(monkeypatch, tmp_path):
    data_root = tmp_path / "application-data"
    first_cwd = tmp_path / "first-cwd"
    second_cwd = tmp_path / "second-cwd"
    first_cwd.mkdir()
    second_cwd.mkdir()
    monkeypatch.setenv("DISCOVERYAGENT_DATA_DIR", str(data_root))
    monkeypatch.delenv("DISK_PATH", raising=False)
    monkeypatch.setattr(app_module, "load_dotenv", None)

    monkeypatch.chdir(first_cwd)
    first = app_module._get_disk_path()
    monkeypatch.chdir(second_cwd)
    second = app_module._get_disk_path()

    assert first == second == data_root / "legacy" / "local_disk"
    assert not (first_cwd / "local_disk").exists()
    assert not (second_cwd / "local_disk").exists()


def test_explicit_legacy_disk_override_remains_supported(monkeypatch, tmp_path):
    override = tmp_path / "explicit-legacy-disk"
    monkeypatch.setenv("DISK_PATH", str(override))

    assert app_module._get_disk_path() == override.resolve()


def test_retained_auth_and_progress_fallbacks_write_to_durable_root(monkeypatch, tmp_path):
    data_root = tmp_path / "application-data"
    auth_root = get_legacy_auth_root(
        environ={"DISCOVERYAGENT_DATA_DIR": str(data_root)}
    )
    registration_log = auth_root / "registrations.jsonl"
    progress_file = auth_root / "project_progress.json"
    auth_root.mkdir(parents=True)
    monkeypatch.setattr(app_module, "DB_AVAILABLE", False)
    monkeypatch.setattr(app_module, "REGISTRATION_LOG_PATH", registration_log)
    monkeypatch.setattr(app_module, "PROJECT_PROGRESS_FILE", progress_file)
    monkeypatch.setattr(app_module, "PROJECT_PROGRESS", {})

    app_module.record_registration_submission({
        "name": "Durable Test",
        "affiliation": "Local",
        "email": "durable@example.test",
        "username": "durable-test",
    })
    progress = app_module.increment_project_submission(
        "abc123", username="durable-test", label="Durable Project"
    )

    assert registration_log.is_file()
    assert progress_file.is_file()
    assert progress["submission_count"] == 1
    assert registration_log.is_relative_to(data_root)
    assert progress_file.is_relative_to(data_root)
    assert not registration_log.is_relative_to(PACKAGE_ROOT)
    assert not progress_file.is_relative_to(PACKAGE_ROOT)
