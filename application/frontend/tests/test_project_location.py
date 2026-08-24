"""Focused project-location safety and relocation regressions."""

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from da_selection_ui import app as app_module


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
    return client


def _destination(parent: Path, project_id: str, name: str = "Located Project") -> Path:
    return parent / app_module._v2_project_directory_name(name)


def _create(client, **overrides):
    data = {
        "project_name": "Located Project",
        "task_description": "Investigate a testable mechanism.",
        "dataset_context": "A documented test dataset.",
        **overrides,
    }
    return client.post("/api/projects", data=data)


def test_custom_create_uses_stable_child_root_and_cancel_has_no_server_mutation(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    parent = tmp_path / "custom parent"
    parent.mkdir()
    project_id = "prj_" + "a" * 32

    # Selecting a location is browser-pending; only Create sends it here.
    assert list(parent.iterdir()) == []
    response = _create(
        client, project_id=project_id, project_parent_dir=str(parent),
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    project = app_module._v2_get_project(project_id)
    root = _destination(parent, project_id)
    assert Path(project["project_root"]) == root
    assert Path(project["code_dir"]) == root / "code"
    assert Path(project["checkpoint_dir"]).is_relative_to(root)
    assert (root / ".discoveryagent" / "project.json").is_file()


def test_custom_create_rejects_collision_symlink_and_unsafe_root(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    parent = tmp_path / "destination"
    parent.mkdir()
    project_id = "prj_" + "b" * 32
    (_destination(parent, project_id)).mkdir()
    collision = _create(client, project_id=project_id, project_parent_dir=str(parent))
    assert collision.status_code == 409
    assert (_destination(parent, project_id)).is_dir()

    linked = tmp_path / "linked"
    linked.symlink_to(parent, target_is_directory=True)
    symlinked = _create(client, project_id="prj_" + "c" * 32, project_parent_dir=str(linked))
    assert symlinked.status_code == 400
    unsafe = _create(client, project_id="prj_" + "d" * 32, project_parent_dir=str(Path.home()))
    assert unsafe.status_code == 400


def test_picker_cancel_returns_no_location_and_creates_nothing(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "_v2_native_project_location_picker_command", lambda: ["picker"])
    monkeypatch.setattr(
        app_module.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="cancelled"),
    )
    before = set(tmp_path.iterdir())
    response = client.post("/api/project-location/choose")
    assert response.status_code == 200
    assert response.get_json() == {"success": True, "cancelled": True}
    assert set(tmp_path.iterdir()) == before


def test_edit_same_volume_moves_complete_tree_and_preserves_external_data_refs(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    created = _create(client).get_json()
    project_id = created["project_id"]
    project = app_module._v2_get_project(project_id)
    source = Path(project["project_root"])
    artifacts = {
        source / "code" / "analysis.py": "generated code",
        source / "checkpoint" / "checkpoints" / "round_0" / "round_report.md": "report",
        source / "checkpoint" / "checkpoints" / "round_0" / "memory_report.md": "memory",
        source / "checkpoint" / "round.log": "log",
        source / ".discoveryagent" / "skills" / "test-skill" / "SKILL.md": "skill",
    }
    for artifact, content in artifacts.items():
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(content, encoding="utf-8")
    project["data_sources"] = [{"dataset_id": "ds_stable", "selected_paths": ["."], "excluded_paths": []}]
    with app_module._V2_PROJECTS_LOCK:
        projects, record, registry_path, managed = app_module._v2_locate_project(project_id)
        record["data_sources"] = project["data_sources"]
        app_module._v2_save_projects(projects, registry_path, managed=managed)
    parent = tmp_path / "relocated"
    parent.mkdir()

    response = client.post(f"/api/projects/{project_id}/edit", data={
        "project_name": "Located Project",
        "task_description": "Investigate a testable mechanism.",
        "dataset_context": "A documented test dataset.",
        "project_parent_dir": str(parent),
        "confirm_project_relocation": "true",
    })

    assert response.status_code == 200, response.get_data(as_text=True)
    moved = app_module._v2_get_project(project_id)
    destination = _destination(parent, project_id)
    assert not source.exists()
    for artifact, content in artifacts.items():
        assert (destination / artifact.relative_to(source)).read_text(encoding="utf-8") == content
    assert moved["data_sources"] == project["data_sources"]
    assert Path(moved["code_dir"]) == destination / "code"


def test_custom_relocation_remains_available_after_reopen_and_registry_reload(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id = "prj_" + "9" * 32
    created = _create(client, project_id=project_id)
    assert created.status_code == 200, created.get_data(as_text=True)
    before = app_module._v2_get_project(project_id)
    source = Path(before["project_root"])
    original_marker = json.loads(
        (source / ".discoveryagent" / "project.json").read_text(encoding="utf-8")
    )
    custom_parent = tmp_path / "Desktop"
    custom_parent.mkdir()
    destination = custom_parent / "located-project"

    response = client.post(f"/api/projects/{project_id}/edit", data={
        "project_name": "Located Project",
        "task_description": "Investigate a testable mechanism.",
        "dataset_context": "A documented test dataset.",
        "project_parent_dir": str(custom_parent),
        "confirm_project_relocation": "true",
    })

    assert response.status_code == 200, response.get_data(as_text=True)
    assert not source.exists()
    assert destination.is_dir()
    raw_record = json.loads(
        (tmp_path / "v2_projects.json").read_text(encoding="utf-8")
    )["projects"][0]
    assert raw_record["project_root"] == str(destination)
    assert raw_record["project_parent_dir"] == str(custom_parent)
    marker = json.loads(
        (destination / ".discoveryagent" / "project.json").read_text(encoding="utf-8")
    )
    assert marker["project_id"] == project_id
    assert marker["project_id"] == original_marker["project_id"]
    assert destination.name != project_id

    immediate = client.get(f"/api/projects/{project_id}?selection_only=1")
    assert immediate.status_code == 200
    immediate_location = immediate.get_json()["project"]["project_location"]
    assert immediate_location == {
        "available": True,
        "default_parent": str(app_module._v2_projects_root()),
        "error": None,
        "parent": str(custom_parent),
        "root": str(destination),
        "running": False,
    }

    # A fresh client forces the next request to recover the project entirely
    # from the persisted registry, matching a browser reload/app restart.
    reloaded_client = app_module.app.test_client()
    with reloaded_client.session_transaction() as session:
        session["authenticated"] = True
    reloaded = reloaded_client.get(f"/api/projects/{project_id}?selection_only=1")
    assert reloaded.status_code == 200
    assert reloaded.get_json()["project"]["project_location"]["available"] is True
    assert reloaded.get_json()["project"]["project_location"]["root"] == str(destination)

    restart_code = (
        "import json; from da_selection_ui import app as app_module; "
        f"project = app_module._v2_resolve_registered_project_context(project_id={project_id!r}); "
        "availability = app_module._v2_project_availability(project); "
        "print(json.dumps({'root': project['project_root'], "
        "'available': availability['available']}))"
    )
    restart_env = dict(os.environ)
    restart_env.update({
        "DISCOVERYAGENT_DATA_DIR": str(tmp_path),
        "DISK_PATH": str(tmp_path),
    })
    restarted = subprocess.run(
        [sys.executable, "-c", restart_code],
        cwd=Path(__file__).resolve().parents[1],
        env=restart_env,
        capture_output=True,
        text=True,
        check=True,
    )
    restarted_state = json.loads(restarted.stdout.strip().splitlines()[-1])
    assert restarted_state == {"available": True, "root": str(destination)}

    persisted = app_module._v2_get_project(project_id)
    normalized = app_module._v2_normalize_registered_project_for_resolution(
        persisted, managed=True, registry_parent=tmp_path,
    )
    assert Path(normalized["project_root"]) == destination
    assert Path(normalized["code_dir"]) == destination / "code"
    assert Path(normalized["checkpoint_dir"]).is_relative_to(destination)
    by_id = app_module._v2_resolve_registered_project_context(project_id=project_id)
    by_slug = app_module._v2_resolve_registered_project_context(
        project_slug=persisted["url_slug"],
    )
    by_path = app_module._v2_resolve_registered_project_context(
        path_context={"project_root": str(destination)},
    )
    assert {Path(item["project_root"]) for item in (by_id, by_slug, by_path)} == {destination}


def test_edit_relocation_requires_confirmation_before_any_mutation(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id = _create(client).get_json()["project_id"]
    before = app_module._v2_get_project(project_id)
    source = Path(before["project_root"])
    marker = source / "code" / "pending-confirmation.txt"
    marker.write_text("unchanged", encoding="utf-8")
    destination_parent = tmp_path / "needs-confirmation"
    destination_parent.mkdir()

    response = client.post(f"/api/projects/{project_id}/edit", data={
        "project_name": "Unsaved name",
        "task_description": "Unsaved objective.",
        "dataset_context": "Unsaved context.",
        "project_parent_dir": str(destination_parent),
    })

    assert response.status_code == 409
    assert response.get_json()["code"] == "project_relocation_confirmation_required"
    assert source.is_dir()
    assert not _destination(destination_parent, project_id, "Unsaved name").exists()
    assert marker.read_text(encoding="utf-8") == "unchanged"
    after = app_module._v2_get_project(project_id)
    assert after["display_name"] == before["display_name"]
    assert after["task_description"] == before["task_description"]


def test_unchanged_and_canonically_equivalent_locations_save_without_confirmation(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id = _create(client).get_json()["project_id"]
    source = Path(app_module._v2_get_project(project_id)["project_root"])

    response = client.post(f"/api/projects/{project_id}/edit", data={
        "project_name": "Saved without moving",
        "task_description": "Investigate a testable mechanism.",
        "dataset_context": "A documented test dataset.",
        "project_parent_dir": str(source.parent / "."),
    })

    assert response.status_code == 200, response.get_data(as_text=True)
    saved = app_module._v2_get_project(project_id)
    assert saved["display_name"] == "Saved without moving"
    assert Path(saved["project_root"]) == source
    assert source.is_dir()


def test_active_round_blocks_relocation_before_filesystem_mutation(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id = _create(client).get_json()["project_id"]
    source = Path(app_module._v2_get_project(project_id)["project_root"])
    parent = tmp_path / "blocked"
    parent.mkdir()
    monkeypatch.setattr(app_module, "_v2_project_is_running", lambda _record: True)

    response = client.post(f"/api/projects/{project_id}/edit", data={
        "project_name": "Located Project",
        "task_description": "Investigate a testable mechanism.",
        "dataset_context": "A documented test dataset.",
        "project_parent_dir": str(parent),
        "confirm_project_relocation": "true",
    })
    assert response.status_code == 409
    assert response.get_json()["code"] == "project_location_locked_while_running"
    assert source.is_dir() and not (_destination(parent, project_id)).exists()


def test_legacy_symlink_project_root_is_rejected_without_following_it(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id = _create(client).get_json()["project_id"]
    source = Path(app_module._v2_get_project(project_id)["project_root"])
    real_root = tmp_path / "legacy-real-root"
    source.rename(real_root)
    source.symlink_to(real_root, target_is_directory=True)
    destination_parent = tmp_path / "must-not-move"
    destination_parent.mkdir()

    response = client.post(f"/api/projects/{project_id}/edit", data={
        "project_name": "Located Project",
        "task_description": "Investigate a testable mechanism.",
        "dataset_context": "A documented test dataset.",
        "project_parent_dir": str(destination_parent),
        "confirm_project_relocation": "true",
    })

    assert response.status_code == 409
    assert response.get_json()["code"] == "project_location_recovery_required"
    assert source.is_symlink() and real_root.is_dir()
    assert not _destination(destination_parent, project_id).exists()


def test_registered_legacy_project_relocates_without_managed_registry_rewrite(monkeypatch, tmp_path):
    project_id = "prj_" + "f" * 32
    source = tmp_path / "legacy-workspace" / project_id
    code = source / "code"
    data = code / "data"
    round_zero = source / "checkpoint" / "checkpoints" / "round_0"
    data.mkdir(parents=True)
    round_zero.mkdir(parents=True)
    readme = data / "README.md"
    readme.write_text("# Legacy\n\nContext\n", encoding="utf-8")
    (round_zero / "config.json").write_text('{"config": {}}', encoding="utf-8")
    app_module.write_project_metadata(source, project_id, "Legacy")
    legacy_registry = tmp_path / "legacy-registry" / "v2_projects.json"
    legacy_registry.parent.mkdir()
    legacy_record = {
        "id": project_id,
        "display_name": "Legacy",
        "task_description": "Investigate a legacy project.",
        "dataset_context": "Context",
        "project_root": str(source),
        "code_dir": str(code),
        "data_dir": str(data),
        "checkpoint_dir": str(round_zero),
        "readme_path": str(readme),
        "data_sources": [],
    }
    legacy_registry.write_text(json.dumps({"projects": [legacy_record]}), encoding="utf-8")
    monkeypatch.setattr(app_module, "_v2_legacy_projects_registry_path", lambda: legacy_registry)
    client = _client(monkeypatch, tmp_path)
    destination_parent = tmp_path / "legacy-relocated"
    destination_parent.mkdir()

    response = client.post(f"/api/projects/{project_id}/edit", data={
        "project_name": "Legacy",
        "task_description": "Investigate a legacy project.",
        "dataset_context": "Context",
        "project_parent_dir": str(destination_parent),
        "confirm_project_relocation": "true",
    })

    assert response.status_code == 200, response.get_data(as_text=True)
    destination = _destination(destination_parent, project_id, "Legacy")
    assert not source.exists() and (destination / "code" / "data" / "README.md").is_file()
    persisted = json.loads(legacy_registry.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["project_root"] == str(destination)
    assert persisted["code_dir"] == str(destination / "code")


def test_cross_volume_copy_is_verified_and_rollback_removes_only_destination(monkeypatch, tmp_path):
    source = tmp_path / "source" / ("prj_" + "e" * 32)
    destination = tmp_path / "target" / source.name
    source.mkdir(parents=True)
    (source / "artifact.bin").write_bytes(b"complete project")
    destination.parent.mkdir()
    original_stat = Path.stat

    def split_devices(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        forced = 11 if path == source else 22 if path == destination.parent else result.st_dev
        return SimpleNamespace(st_dev=forced, **{
            name: getattr(result, name) for name in dir(result)
            if name.startswith("st_") and name != "st_dev"
        })

    monkeypatch.setattr(Path, "stat", split_devices)
    transaction = app_module._v2_prepare_project_relocation(source, destination)
    assert transaction["mode"] == "copy"
    assert source.is_dir()
    assert (destination / "artifact.bin").read_bytes() == b"complete project"

    app_module._v2_rollback_project_relocation(transaction)
    assert source.is_dir()
    assert not destination.exists()


def test_cross_volume_edit_commits_registry_before_removing_source(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id = _create(client).get_json()["project_id"]
    source = Path(app_module._v2_get_project(project_id)["project_root"])
    marker = source / "code" / "cross-volume.txt"
    marker.write_text("verified", encoding="utf-8")
    destination_parent = tmp_path / "other-volume"
    destination_parent.mkdir()

    def verified_copy(source_root, destination_root):
        shutil.copytree(source_root, destination_root, symlinks=True)
        assert app_module._v2_tree_manifest(source_root) == app_module._v2_tree_manifest(destination_root)
        return {"mode": "copy", "source": source_root, "destination": destination_root}

    monkeypatch.setattr(app_module, "_v2_prepare_project_relocation", verified_copy)
    response = client.post(f"/api/projects/{project_id}/edit", data={
        "project_name": "Located Project",
        "task_description": "Investigate a testable mechanism.",
        "dataset_context": "A documented test dataset.",
        "project_parent_dir": str(destination_parent),
        "confirm_project_relocation": "true",
    })

    assert response.status_code == 200, response.get_data(as_text=True)
    destination = _destination(destination_parent, project_id)
    assert not source.exists()
    assert (destination / marker.relative_to(source)).read_text(encoding="utf-8") == "verified"
    assert Path(app_module._v2_get_project(project_id)["project_root"]) == destination


def test_registry_write_failure_rolls_same_volume_move_back(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id = _create(client).get_json()["project_id"]
    source = Path(app_module._v2_get_project(project_id)["project_root"])
    readme = source / "code" / "data" / "README.md"
    metadata = source / ".discoveryagent" / "project.json"
    config = source / "checkpoint" / "checkpoints" / "round_0" / "config.json"
    originals = {path: path.read_bytes() for path in (readme, metadata, config)}
    parent = tmp_path / "rollback"
    parent.mkdir()
    original_save = app_module._v2_save_projects

    def fail_after_move(projects, registry_path=None, *, managed=True):
        if any(Path(project.get("project_root") or "") == _destination(parent, project_id, "Changed Name") for project in projects):
            raise OSError("registry unavailable")
        return original_save(projects, registry_path, managed=managed)

    monkeypatch.setattr(app_module, "_v2_save_projects", fail_after_move)
    response = client.post(f"/api/projects/{project_id}/edit", data={
        "project_name": "Changed Name",
        "task_description": "Investigate a testable mechanism.",
        "dataset_context": "Changed context that must roll back.",
        "project_parent_dir": str(parent),
        "confirm_project_relocation": "true",
        "files": (io.BytesIO(b"must be removed"), "new-input.txt"),
    }, content_type="multipart/form-data")
    assert response.status_code == 500
    assert source.is_dir()
    assert not _destination(parent, project_id, "Changed Name").exists()
    assert not (source / "code" / "data" / "new-input.txt").exists()
    assert {path: path.read_bytes() for path in originals} == originals
    persisted = json.loads((tmp_path / "v2_projects.json").read_text())["projects"][0]
    assert persisted["project_root"] == str(source)
