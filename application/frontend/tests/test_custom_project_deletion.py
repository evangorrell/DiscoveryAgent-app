"""Canonical managed deletion and rollback safety."""

from __future__ import annotations

import json
from pathlib import Path

from da_selection_ui import app as app_module


def _client():
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
    return client


def _create(client, project_id: str, name: str = "Deletion Fixture") -> tuple[dict, Path]:
    response = client.post("/api/projects", data={
        "project_id": project_id,
        "project_name": name,
        "task_description": "Test recoverable project deletion.",
        "dataset_context": "Small temporary fixture.",
    })
    assert response.status_code == 200, response.get_data(as_text=True)
    record = app_module._v2_get_project(project_id)
    return record, Path(record["project_root"])


def test_managed_delete_moves_only_canonical_registered_root_and_removes_record(monkeypatch, tmp_path):
    client = _client()
    project_id = "prj_" + "3" * 32
    record, root = _create(client, project_id)
    artifact = root / "code" / "artifact.bin"
    artifact.write_bytes(b"managed research artifact")
    trash = tmp_path / "trash-copy"

    def move(target):
        target.rename(trash)
        return {"trash_path": trash}

    monkeypatch.setattr(app_module, "_v2_move_to_trash", move)
    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["removal_mode"] == "trash"
    assert not root.exists()
    assert (trash / artifact.relative_to(root)).read_bytes() == b"managed research artifact"
    assert app_module._v2_get_project(project_id) is None
    assert json.loads((tmp_path / "projects.json").read_text()) == {"projects": []}
    assert record["imported_in_place"] is False


def test_managed_delete_requires_matching_marker_and_keeps_registry(tmp_path):
    client = _client()
    project_id = "prj_" + "4" * 32
    _record, root = _create(client, project_id)
    marker = root / ".discoveryagent" / "project.json"
    before = (tmp_path / "projects.json").read_bytes()
    marker.unlink()

    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 409
    assert response.get_json()["code"] == "project_unavailable_delete_confirmation_required"
    assert root.is_dir()
    assert (tmp_path / "projects.json").read_bytes() == before


def test_managed_delete_registry_failure_restores_exact_root(monkeypatch, tmp_path):
    client = _client()
    project_id = "prj_" + "5" * 32
    _record, root = _create(client, project_id)
    artifact = root / "code" / "rollback.bin"
    artifact.write_bytes(b"restore exactly")
    registry_before = (tmp_path / "projects.json").read_bytes()
    trash = tmp_path / "temporary-trash"
    original_save = app_module._v2_save_projects

    def move(target):
        target.rename(trash)
        return {"trash_path": trash}

    def restore(receipt):
        Path(receipt["trash_path"]).rename(Path(receipt["original_path"]))

    def fail_empty(projects):
        if not projects:
            raise OSError("registry unavailable")
        return original_save(projects)

    monkeypatch.setattr(app_module, "_v2_move_to_trash", move)
    monkeypatch.setattr(app_module, "_v2_restore_trashed_project", restore)
    monkeypatch.setattr(app_module, "_v2_save_projects", fail_empty)
    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 500
    assert root.is_dir()
    assert artifact.read_bytes() == b"restore exactly"
    assert not trash.exists()
    assert (tmp_path / "projects.json").read_bytes() == registry_before


def test_managed_delete_rejects_root_outside_canonical_projects(tmp_path):
    project_id = "prj_" + "6" * 32
    root = tmp_path / "external" / "unsafe"
    root.mkdir(parents=True)
    app_module.write_project_metadata(root, project_id, "Unsafe")
    record = {
        "id": project_id,
        "project_root": str(root),
        "project_parent_dir": str(root.parent),
        "imported_in_place": False,
    }

    try:
        app_module._v2_project_delete_root(record, registered_projects=[record])
    except ValueError as exc:
        assert "DiscoveryAgent Projects folder" in str(exc)
    else:  # pragma: no cover - fail closed assertion
        raise AssertionError("noncanonical managed root was accepted")


def test_duplicate_identity_in_single_registry_fails_closed(tmp_path):
    project_id = "prj_" + "7" * 32
    first = {"id": project_id, "project_root": str(tmp_path / "one")}
    second = {"id": project_id, "project_root": str(tmp_path / "two")}
    app_module._v2_save_projects([first, second])

    response = _client().delete(f"/api/projects/{project_id}")

    assert response.status_code == 409
    assert response.get_json()["code"] == "unsafe_project_delete"
    assert len(json.loads((tmp_path / "projects.json").read_text())["projects"]) == 2
