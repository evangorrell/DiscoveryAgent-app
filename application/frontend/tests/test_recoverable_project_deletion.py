"""Safety coverage for recoverable V2 project-data and project removal."""

import inspect
import tomllib
from pathlib import Path

import pytest

from da_selection_ui import app as app_module


PROJECTS_JS = Path(app_module.__file__).resolve().parent / "static" / "projects.js"
PROJECTS_TEMPLATE = Path(app_module.__file__).resolve().parent / "templates" / "projects.html"
PROJECT_ROOT = Path(app_module.__file__).resolve().parent.parent


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
    return client


def _create(client, name="Recoverable Data"):
    response = client.post("/api/projects", data={
        "project_name": name,
        "task_description": "Test recoverable project-data removal.",
        "dataset_context": "Small synthetic test context.",
    })
    assert response.status_code == 200
    return response.get_json()["project_id"]


def _fake_send2trash(tmp_path, moved):
    trash = tmp_path / ".test-trash"

    def move(raw_target):
        target = Path(raw_target)
        trash.mkdir(exist_ok=True)
        destination = trash / target.name
        target.replace(destination)
        moved.append((target, destination))

    return move


def test_send2trash_is_invoked_for_validated_path(monkeypatch, tmp_path):
    target = tmp_path / 'data with "quotes".h5ad'
    target.write_bytes(b"tiny")
    moved = []
    monkeypatch.setattr(app_module, "send2trash", _fake_send2trash(tmp_path, moved))

    app_module._v2_move_to_trash(target)

    assert moved[0][0] == target
    assert moved[0][1].read_bytes() == b"tiny"


def test_send2trash_exception_has_no_permanent_fallback(monkeypatch, tmp_path):
    target = tmp_path / "only-copy.h5ad"
    target.write_bytes(b"scientific data")

    def fail(_target):
        raise OSError("Trash unavailable")

    monkeypatch.setattr(app_module, "send2trash", fail)
    with pytest.raises(OSError, match="Trash unavailable"):
        app_module._v2_move_to_trash(target)

    assert target.read_bytes() == b"scientific data"


def test_project_delete_moves_complete_root_to_trash(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id = _create(client)
    project_root = Path(app_module._v2_get_project(project_id)["project_root"])
    source = project_root / "code" / "data" / "source.h5ad"
    source.write_bytes(b"tiny source")
    moved = []
    monkeypatch.setattr(app_module, "send2trash", _fake_send2trash(tmp_path, moved))

    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 200
    assert moved[0][0] == project_root
    assert (moved[0][1] / "code" / "data" / "source.h5ad").read_bytes() == b"tiny source"
    assert app_module._v2_get_project(project_id) is None


def test_failed_project_trash_keeps_project_and_registry(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id = _create(client)
    project_root = Path(app_module._v2_get_project(project_id)["project_root"])

    def fail(_target):
        raise OSError("Trash is unavailable")

    monkeypatch.setattr(app_module, "send2trash", fail)
    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 500
    assert project_root.is_dir()
    assert app_module._v2_get_project(project_id) is not None


def test_file_inventory_has_no_individual_delete_surface_or_endpoint():
    client = app_module.app.test_client()
    source = PROJECTS_JS.read_text(encoding="utf-8")
    template = PROJECTS_TEMPLATE.read_text(encoding="utf-8")
    file_rule = next(rule for rule in app_module.app.url_map.iter_rules()
                     if rule.rule == "/api/projects/<project_id>/files")

    assert "v2-file-remove" not in source
    assert "removeExistingFile" not in source
    assert "window.confirm" not in source
    assert "DELETE" not in file_rule.methods
    assert not hasattr(app_module, "_v2_project_data_delete_target")
    assert client.delete("/api/projects/unknown/files?path=dataset.csv").status_code == 405
    assert "complete DiscoveryAgent project workspace" in template
    assert 'id="delete-project-confirm">Delete</button>' in template


def test_default_project_delete_path_contains_no_permanent_fallback():
    project_route = inspect.getsource(app_module.api_projects_delete)
    assert "shutil.rmtree" not in project_route
    assert "_v2_move_to_trash" in project_route
    trash_helper = inspect.getsource(app_module._v2_move_to_trash)
    assert "send2trash(str(target))" in trash_helper
    assert "subprocess" not in trash_helper
    assert "osascript" not in trash_helper
    assert "powershell" not in trash_helper.lower()
    assert "gio" not in trash_helper


def test_send2trash_is_in_reproducible_runtime_dependencies():
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "send2trash==2.1.0" in pyproject["project"]["dependencies"]
    lock = (PROJECT_ROOT / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "send2trash"' in lock
    assert 'version = "2.1.0"' in lock
