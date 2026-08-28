"""Global safety fixtures for DiscoveryAgent tests."""

import os
import shutil
import tempfile
from pathlib import Path

import pytest


_TEST_PROCESS_DATA_ROOT = Path(tempfile.mkdtemp(prefix="discoveryagent-tests-"))
os.environ["DISCOVERYAGENT_DATA_DIR"] = str(_TEST_PROCESS_DATA_ROOT)


@pytest.fixture(scope="session", autouse=True)
def cleanup_process_data_root():
    """Remove import-time application state after the test process exits."""
    yield
    shutil.rmtree(_TEST_PROCESS_DATA_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def isolate_discoveryagent_storage(monkeypatch, tmp_path):
    """Prevent every test from touching the user's real application data.
    """
    test_data_root = Path(tmp_path)
    monkeypatch.setenv("DISCOVERYAGENT_DATA_DIR", str(test_data_root))
    monkeypatch.delenv("DISCOVERY_AGENT_INTERACTION_LOG_DIR", raising=False)

    # Import inside the fixture so the environment guard is installed before
    # any test request can resolve or create managed storage.
    from da_selection_ui import app as app_module

    monkeypatch.setattr(app_module, "get_data_root", lambda: test_data_root)
    monkeypatch.setattr(app_module, "get_projects_root", lambda: test_data_root / "projects")
    monkeypatch.setattr(
        app_module,
        "get_projects_registry_path",
        lambda: test_data_root / "projects.json",
    )
    monkeypatch.setattr(
        app_module,
        "get_project_root",
        lambda project_directory_name: test_data_root / "projects" / project_directory_name,
    )
