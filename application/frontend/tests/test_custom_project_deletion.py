"""Identity-bound, recoverable deletion for managed and custom project roots."""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path

import pytest

from da_selection_ui import app as app_module


def _client():
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
    return client


def _create(client, *, project_id: str, parent: Path | None = None) -> tuple[dict, Path]:
    data = {
        "project_id": project_id,
        "project_name": f"Deletion fixture {project_id[-4:]}",
        "task_description": "Test recoverable project deletion.",
        "dataset_context": "Small temporary fixture.",
    }
    if parent is not None:
        data["project_parent_dir"] = str(parent)
    response = client.post("/api/projects", data=data)
    assert response.status_code == 200, response.get_data(as_text=True)
    record = app_module._v2_get_project(project_id)
    assert record is not None
    return record, Path(record["project_root"])


def _trash_move(trash: Path, calls: list[Path], *, before_move=None):
    def move(target: Path):
        target = Path(target)
        if before_move:
            before_move(target)
        calls.append(target)
        trash.mkdir(parents=True, exist_ok=True)
        destination = trash / target.name
        target.replace(destination)
        return {"trash_path": destination}

    return move


def test_default_and_custom_projects_move_only_the_registered_child(monkeypatch, tmp_path):
    client = _client()
    custom_parent = tmp_path / "chosen parent"
    custom_parent.mkdir()
    default_id = "prj_" + "1" * 32
    custom_id = "prj_" + "2" * 32
    _default, default_root = _create(client, project_id=default_id)
    _custom, custom_root = _create(client, project_id=custom_id, parent=custom_parent)
    sibling = custom_parent / "must-remain.txt"
    sibling.write_text("parent content", encoding="utf-8")
    calls: list[Path] = []
    trash = tmp_path / "trash"
    monkeypatch.setattr(app_module, "_v2_move_to_trash", _trash_move(trash, calls))

    assert client.delete(f"/api/projects/{default_id}").status_code == 200
    assert client.delete(f"/api/projects/{custom_id}").status_code == 200

    assert calls == [default_root, custom_root]
    assert custom_parent.is_dir() and sibling.read_text(encoding="utf-8") == "parent content"
    assert (trash / default_root.name / ".discoveryagent" / "project.json").is_file()
    assert (trash / custom_root.name / ".discoveryagent" / "project.json").is_file()
    assert app_module._v2_get_project(default_id) is None
    assert app_module._v2_get_project(custom_id) is None


def test_registry_changes_only_after_trash_and_browser_path_is_ignored(monkeypatch, tmp_path):
    client = _client()
    parent = tmp_path / "custom"
    parent.mkdir()
    project_id = "prj_" + "3" * 32
    _record, root = _create(client, project_id=project_id, parent=parent)
    injected = tmp_path / "injected"
    injected.mkdir()
    events: list[str] = []
    original_save = app_module._v2_save_projects

    def before_move(target):
        assert target == root
        assert app_module._v2_get_project(project_id) is not None
        events.append("trash")

    def recording_save(projects, registry_path=None, *, managed=True):
        if not any(project.get("id") == project_id for project in projects):
            events.append("registry")
        return original_save(projects, registry_path, managed=managed)

    monkeypatch.setattr(app_module, "_v2_move_to_trash", _trash_move(tmp_path / "trash", [], before_move=before_move))
    monkeypatch.setattr(app_module, "_v2_save_projects", recording_save)
    response = client.delete(
        f"/api/projects/{project_id}",
        json={"project_root": str(injected), "path": str(Path.home())},
    )

    assert response.status_code == 200
    assert events == ["trash", "registry"]
    assert injected.is_dir()


def test_data_library_source_and_nested_symlink_targets_are_untouched(monkeypatch, tmp_path):
    client = _client()
    monkeypatch.setattr(app_module.data_library, "get_data_root", lambda: tmp_path / "library registry")
    monkeypatch.setattr(app_module.data_library, "get_projects_root", lambda: tmp_path / "managed-project guard")
    external = tmp_path / "scientist input"
    external.mkdir()
    source_file = external / "input.csv"
    source_file.write_text("a,b\n1,2\n", encoding="utf-8")
    source, created = app_module.data_library.add_source(str(external))
    assert created
    parent = tmp_path / "projects elsewhere"
    parent.mkdir()
    project_id = "prj_" + "4" * 32
    record, root = _create(client, project_id=project_id, parent=parent)
    linked = root / "code" / "linked-input"
    linked.symlink_to(external, target_is_directory=True)
    record["data_sources"] = [{"dataset_id": source["id"], "selected_paths": ["."], "excluded_paths": []}]
    with app_module._V2_PROJECTS_LOCK:
        projects, stored, registry_path, managed = app_module._v2_locate_project(project_id)
        stored["data_sources"] = copy.deepcopy(record["data_sources"])
        app_module._v2_save_projects(projects, registry_path, managed=managed)
    monkeypatch.setattr(app_module, "_v2_move_to_trash", _trash_move(tmp_path / "trash", []))

    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 200
    assert source_file.read_text(encoding="utf-8") == "a,b\n1,2\n"
    assert app_module.data_library.list_sources()[0]["id"] == source["id"]
    assert (tmp_path / "trash" / root.name / "code" / "linked-input").is_symlink()


@pytest.mark.parametrize("metadata_value", [None, {"project_id": "prj_" + "f" * 32}])
def test_custom_managed_root_requires_matching_identity_metadata(monkeypatch, tmp_path, metadata_value):
    client = _client()
    parent = tmp_path / "custom"
    parent.mkdir()
    project_id = "prj_" + "5" * 32
    _record, root = _create(client, project_id=project_id, parent=parent)
    metadata = root / ".discoveryagent" / "project.json"
    if metadata_value is None:
        metadata.unlink()
    else:
        metadata.write_text(json.dumps(metadata_value), encoding="utf-8")
    called = []
    monkeypatch.setattr(app_module, "_v2_move_to_trash", lambda target: called.append(target))

    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 409
    assert root.is_dir() and app_module._v2_get_project(project_id) is not None
    assert called == []


def test_custom_root_must_match_its_registered_parent_identity(monkeypatch, tmp_path):
    client = _client()
    parent = tmp_path / "custom"
    parent.mkdir()
    project_id = "prj_" + "0" * 32
    _record, root = _create(client, project_id=project_id, parent=parent)
    different_parent = tmp_path / "different-parent"
    different_parent.mkdir()
    with app_module._V2_PROJECTS_LOCK:
        projects, stored, registry_path, managed = app_module._v2_locate_project(project_id)
        stored["project_parent_dir"] = str(different_parent)
        app_module._v2_save_projects(projects, registry_path, managed=managed)
    called = []
    monkeypatch.setattr(app_module, "_v2_move_to_trash", lambda target: called.append(target))

    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 409
    assert "parent identity" in response.get_json()["error"]
    assert root.is_dir() and called == []


def test_symlink_root_missing_volume_active_round_and_duplicate_identity_fail_closed(monkeypatch, tmp_path):
    client = _client()
    parent = tmp_path / "custom"
    parent.mkdir()
    project_id = "prj_" + "6" * 32
    _record, root = _create(client, project_id=project_id, parent=parent)
    real = parent / "real-project"
    root.rename(real)
    root.symlink_to(real, target_is_directory=True)
    assert client.delete(f"/api/projects/{project_id}").status_code == 409
    assert root.is_symlink() and real.is_dir()
    root.unlink()
    real.rename(root)

    monkeypatch.setattr(app_module, "_v2_project_is_running", lambda _record: True)
    active = client.delete(f"/api/projects/{project_id}")
    assert active.status_code == 409
    assert active.get_json()["code"] == "project_delete_locked_while_running"
    monkeypatch.setattr(app_module, "_v2_project_is_running", lambda _record: False)

    legacy_registry = tmp_path / "legacy" / "v2_projects.json"
    legacy_registry.parent.mkdir()
    legacy_record = copy.deepcopy(app_module._v2_get_project(project_id))
    legacy_registry.write_text(json.dumps({"projects": [legacy_record]}), encoding="utf-8")
    monkeypatch.setattr(app_module, "_v2_legacy_projects_registry_path", lambda: legacy_registry)
    duplicate = client.delete(f"/api/projects/{project_id}")
    assert duplicate.status_code == 409
    assert "more than once" in duplicate.get_json()["error"]
    assert root.is_dir()


def test_missing_custom_root_and_unsupported_trash_leave_registry_unchanged(monkeypatch, tmp_path):
    client = _client()
    parent = tmp_path / "external volume"
    parent.mkdir()
    missing_id = "prj_" + "7" * 32
    _record, missing_root = _create(client, project_id=missing_id, parent=parent)
    missing_root.rename(parent / "disconnected-project")
    response = client.delete(f"/api/projects/{missing_id}")
    assert response.status_code == 409
    assert app_module._v2_get_project(missing_id) is not None

    unsupported_id = "prj_" + "8" * 32
    _record, unsupported_root = _create(client, project_id=unsupported_id, parent=parent)
    monkeypatch.setattr(
        app_module,
        "_v2_move_to_trash",
        lambda _target: (_ for _ in ()).throw(OSError("This volume has no recoverable Trash")),
    )
    response = client.delete(f"/api/projects/{unsupported_id}")
    assert response.status_code == 500
    assert unsupported_root.is_dir() and app_module._v2_get_project(unsupported_id) is not None


def test_registry_failure_restores_exact_custom_root(monkeypatch, tmp_path):
    client = _client()
    parent = tmp_path / "custom"
    parent.mkdir()
    project_id = "prj_" + "9" * 32
    _record, root = _create(client, project_id=project_id, parent=parent)
    artifact = root / "code" / "analysis.py"
    artifact.write_text("print('restored')\n", encoding="utf-8")
    original_save = app_module._v2_save_projects

    def fail_removed(projects, registry_path=None, *, managed=True):
        if not any(project.get("id") == project_id for project in projects):
            raise OSError("registry unavailable")
        return original_save(projects, registry_path, managed=managed)

    monkeypatch.setattr(app_module, "_v2_move_to_trash", _trash_move(tmp_path / "trash", []))
    monkeypatch.setattr(app_module, "_v2_save_projects", fail_removed)

    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 500
    assert "restored to its original location" in response.get_json()["error"]
    assert artifact.read_text(encoding="utf-8") == "print('restored')\n"
    assert app_module._v2_get_project(project_id) is not None


def test_rollback_collision_never_overwrites_new_path_occupant(monkeypatch, tmp_path):
    client = _client()
    parent = tmp_path / "custom"
    parent.mkdir()
    project_id = "prj_" + "a" * 32
    _record, root = _create(client, project_id=project_id, parent=parent)
    original_save = app_module._v2_save_projects

    def move_then_collide(target):
        destination = tmp_path / "trash" / target.name
        destination.parent.mkdir()
        target.replace(destination)
        target.mkdir()
        (target / "unrelated.txt").write_text("do not overwrite", encoding="utf-8")
        return {"trash_path": destination}

    def fail_removed(projects, registry_path=None, *, managed=True):
        if not any(project.get("id") == project_id for project in projects):
            raise OSError("registry unavailable")
        return original_save(projects, registry_path, managed=managed)

    monkeypatch.setattr(app_module, "_v2_move_to_trash", move_then_collide)
    monkeypatch.setattr(app_module, "_v2_save_projects", fail_removed)
    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 500
    assert "rollback was incomplete" in response.get_json()["error"]
    assert (root / "unrelated.txt").read_text(encoding="utf-8") == "do not overwrite"
    assert (tmp_path / "trash" / root.name / ".discoveryagent" / "project.json").is_file()
    assert app_module._v2_get_project(project_id) is not None


def test_overlapping_project_data_and_protected_roots_are_rejected(monkeypatch, tmp_path):
    project_id = "prj_" + "b" * 32
    target = tmp_path / "container" / project_id
    target.mkdir(parents=True)
    app_module.write_project_metadata(target, project_id, "Unsafe")
    other = target / ("prj_" + "c" * 32)
    other.mkdir()
    app_module.write_project_metadata(other, other.name, "Nested")
    record = {"id": project_id, "project_root": str(target)}
    record["project_parent_dir"] = str(target.parent)
    with pytest.raises(ValueError, match="overlaps another"):
        app_module._v2_project_delete_root(
            record,
            managed=True,
            registered_projects=[record, {"id": other.name, "project_root": str(other)}],
        )

    monkeypatch.setattr(
        app_module.data_library,
        "list_sources",
        lambda: [{"canonical_path": str(target), "source_path": str(target)}],
    )
    with pytest.raises(ValueError, match="Data source"):
        app_module._v2_project_delete_root(record, managed=True, registered_projects=[record])

    monkeypatch.setattr(app_module, "_v2_delete_protected_roots", lambda: (target,))
    with pytest.raises(ValueError, match="protected"):
        app_module._v2_project_delete_root(record, managed=True, registered_projects=[record])


def test_repeated_concurrent_delete_is_serialized_and_moves_once(monkeypatch, tmp_path):
    creator = _client()
    project_id = "prj_" + "d" * 32
    _record, root = _create(creator, project_id=project_id)
    entered = threading.Event()
    release = threading.Event()
    calls: list[Path] = []

    def blocking_move(target):
        calls.append(Path(target))
        destination = tmp_path / "trash" / Path(target).name
        destination.parent.mkdir()
        Path(target).replace(destination)
        entered.set()
        assert release.wait(5)
        return {"trash_path": destination}

    monkeypatch.setattr(app_module, "_v2_move_to_trash", blocking_move)
    statuses: list[int] = []

    def request_delete():
        statuses.append(_client().delete(f"/api/projects/{project_id}").status_code)

    first = threading.Thread(target=request_delete)
    second = threading.Thread(target=request_delete)
    first.start()
    assert entered.wait(5)
    second.start()
    release.set()
    first.join(5)
    second.join(5)

    assert sorted(statuses) == [200, 404]
    assert calls == [root]


def test_legacy_custom_root_requires_marker_but_in_place_legacy_behavior_remains(monkeypatch, tmp_path):
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: legacy_root)
    project_id = "legacy-project"
    in_place = legacy_root / project_id
    in_place.mkdir()
    assert app_module._v2_project_delete_root(
        {"id": project_id, "project_root": str(in_place)},
        managed=False,
        registered_projects=[],
    ) == in_place

    custom = tmp_path / "elsewhere" / project_id
    custom.mkdir(parents=True)
    with pytest.raises(ValueError, match="metadata"):
        app_module._v2_project_delete_root(
            {"id": project_id, "project_root": str(custom), "project_parent_dir": str(custom.parent)},
            managed=False,
            registered_projects=[],
        )
    app_module.write_project_metadata(custom, project_id, "Legacy")
    assert app_module._v2_project_delete_root(
        {"id": project_id, "project_root": str(custom), "project_parent_dir": str(custom.parent)},
        managed=False,
        registered_projects=[],
    ) == custom
