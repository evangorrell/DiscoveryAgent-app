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

    Older endpoint tests patch only ``_get_disk_path`` and expect their project
    registry directly beneath ``tmp_path``. New project creation no longer uses
    that legacy helper, so protect the entire suite at the centralized storage
    boundary while retaining those established temporary-path expectations.
    """
    test_data_root = Path(tmp_path)
    monkeypatch.setenv("DISCOVERYAGENT_DATA_DIR", str(test_data_root))
    monkeypatch.delenv("DISCOVERY_AGENT_INTERACTION_LOG_DIR", raising=False)

    # Import inside the fixture so the environment guard is installed before
    # any test request can resolve or create managed storage.
    from da_selection_ui import app as app_module

    monkeypatch.setattr(app_module, "get_data_root", lambda: test_data_root)
    monkeypatch.setattr(app_module, "get_projects_root", lambda: test_data_root)
    monkeypatch.setattr(
        app_module,
        "get_project_root",
        lambda project_id: test_data_root / project_id,
    )
    # A test that lists projects must never discover and migrate the real
    # checkout-local research store into its temporary data root.
    monkeypatch.setattr(
        app_module,
        "get_historical_checkout_legacy_disk_root",
        lambda: test_data_root / "historical-checkout-local-disk",
    )
    # Legacy interaction migration is also a storage input. Tests must never
    # read the developer checkout's real research log or copy it into fixtures.
    monkeypatch.setattr(
        app_module,
        "LEGACY_INTERACTION_LOG_PATH",
        test_data_root / "legacy" / "interaction_logs" / "audit_interactions.jsonl",
    )
    app_module._INTERACTION_MIGRATION_STATE.update({
        "signature": None,
        "result": None,
    })
