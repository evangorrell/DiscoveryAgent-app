"""Final managed-root naming and relocation regressions."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from da_selection_ui import app as app_module


def _client():
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
    return client


def _create(client, name="Located Project", project_id=None):
    data = {
        "project_name": name,
        "task_description": "Investigate a testable mechanism.",
        "dataset_context": "A documented test dataset.",
    }
    if project_id:
        data["project_id"] = project_id
    return client.post("/api/projects", data=data)


def test_managed_location_helper_and_external_location_rejection(tmp_path):
    client = _client()
    html = client.get('/projects').get_data(as_text=True)
    assert 'Managed project locations must be inside your DiscoveryAgent Projects folder.' in html
    assert 'aria-describedby="np-managed-location-help"' in html
    external = tmp_path / 'Desktop'
    external.mkdir()
    rejected = client.post('/api/projects', data={
        'project_name': 'External', 'task_description': 'Research',
        'dataset_context': 'Data', 'project_parent_dir': str(external),
    })
    assert rejected.status_code == 400
    assert 'DiscoveryAgent Projects folder' in rejected.get_json()['error']
    assert not list(external.iterdir())
    assert _create(client).status_code == 200


def _imported_record(tmp_path: Path, *, project_id: str, name: str) -> tuple[dict, Path]:
    root = tmp_path / "external-user-folder"
    data = root / "code" / "data"
    round_zero = root / "checkpoint" / "checkpoints" / "round_0"
    data.mkdir(parents=True)
    round_zero.mkdir(parents=True)
    readme = data / "README.md"
    readme.write_text(f"# {name}\n\nSynthetic context.\n", encoding="utf-8")
    config = round_zero / "config.json"
    config.write_text(json.dumps({"config": {
        "working_directory": str(root / "code"),
        "task_description": "Investigate imported data.",
        "dataset_name": name,
    }}), encoding="utf-8")
    app_module.write_project_metadata(root, project_id, name)
    record = {
        "id": project_id,
        "url_slug": app_module._v2_slugify(name),
        "display_name": name,
        "task_description": "Investigate imported data.",
        "dataset_context": "Synthetic context.",
        "project_root": str(root),
        "project_parent_dir": str(root.parent),
        "code_dir": str(root / "code"),
        "data_dir": str(data),
        "checkpoint_dir": str(round_zero),
        "readme_path": str(readme),
        "data_sources": [],
        "imported_in_place": True,
    }
    with app_module._V2_PROJECTS_LOCK:
        app_module._v2_save_projects([record])
    return record, root


def test_directory_slug_normalizes_unicode_unsafe_characters_and_empty_fallback():
    assert app_module._v2_project_directory_slug("  RGC__Study / α?  ") == "rgc-study-α"
    assert app_module._v2_project_directory_slug("Straße") == "strasse"
    assert app_module._v2_project_directory_slug("../../Unsafe\\Path") == "unsafe-path"
    assert app_module._v2_project_directory_slug("💡 / ?") == "project"


def test_create_uses_unsuffixed_name_slug_and_preserves_immutable_id(tmp_path):
    client = _client()
    project_id = "prj_" + "a" * 32
    response = _create(client, "Readable Project", project_id)

    assert response.status_code == 200, response.get_data(as_text=True)
    record = app_module._v2_get_project(project_id)
    root = Path(record["project_root"])
    assert root == tmp_path / "projects" / "readable-project"
    assert project_id not in root.name
    assert "--" not in root.name
    marker = json.loads((root / ".discoveryagent" / "project.json").read_text())
    assert marker["project_id"] == project_id
    assert record["id"] == project_id


def test_create_rejects_normalized_slug_collision_without_suffixing(tmp_path):
    client = _client()
    first = _create(client, "Alpha Study", "prj_" + "b" * 32)
    second = _create(client, "Alpha_Study", "prj_" + "c" * 32)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.get_json()["code"] == "duplicate_project_slug"
    assert sorted(path.name for path in (tmp_path / "projects").iterdir()) == ["alpha-study"]


def test_existing_filesystem_slug_collision_is_never_overwritten(tmp_path):
    occupied = tmp_path / "projects" / "occupied"
    occupied.mkdir(parents=True)
    sentinel = occupied / "sentinel.bin"
    sentinel.write_bytes(b"untouched")

    response = _create(_client(), "Occupied", "prj_" + "d" * 32)

    assert response.status_code == 409
    assert sentinel.read_bytes() == b"untouched"
    assert app_module._v2_get_project("prj_" + "d" * 32) is None


def test_display_name_edit_renames_managed_folder_and_preserves_id(tmp_path):
    client = _client()
    project_id = "prj_" + "e" * 32
    assert _create(client, "Stable Folder", project_id).status_code == 200
    before = app_module._v2_get_project(project_id)
    root = Path(before["project_root"])

    response = client.post(f"/api/projects/{project_id}/edit", data={
        "project_name": "Renamed Display",
        "task_description": "Investigate a testable mechanism.",
        "dataset_context": "A documented test dataset.",
        "project_parent_dir": str(root.parent),
    })

    assert response.status_code == 200, response.get_data(as_text=True)
    after = app_module._v2_get_project(project_id)
    renamed = root.parent / "renamed-display"
    assert after["display_name"] == "Renamed Display"
    assert Path(after["project_root"]) == renamed
    assert not root.exists()
    assert renamed.is_dir()
    marker = json.loads((renamed / ".discoveryagent" / "project.json").read_text())
    assert marker == {"project_id": project_id, "display_name": "Renamed Display"}


def test_imported_project_moves_only_to_canonical_unsuffixed_managed_root(tmp_path):
    client = _client()
    project_id = "prj_" + "f" * 32
    record, source = _imported_record(tmp_path, project_id=project_id, name="Imported Project")
    marker_before = (source / ".discoveryagent" / "project.json").read_bytes()

    response = client.post(f"/api/projects/{project_id}/edit", data={
        "project_name": "Imported Project",
        "task_description": record["task_description"],
        "dataset_context": record["dataset_context"],
        "project_parent_dir": str(tmp_path / "projects"),
        "use_canonical_default_location": "true",
        "confirm_project_relocation": "true",
    })

    assert response.status_code == 200, response.get_data(as_text=True)
    destination = tmp_path / "projects" / "imported-project"
    assert not source.exists() and destination.is_dir()
    assert project_id not in destination.name
    moved = app_module._v2_get_project(project_id)
    assert moved["id"] == project_id
    assert moved["imported_in_place"] is False
    assert Path(moved["project_root"]) == destination
    assert (destination / ".discoveryagent" / "project.json").read_bytes() == marker_before


def test_canonical_relocation_registry_failure_restores_everything(monkeypatch, tmp_path):
    client = _client()
    project_id = "prj_" + "1" * 32
    record, source = _imported_record(tmp_path, project_id=project_id, name="Rollback Project")
    tree_before = app_module._v2_tree_manifest(source)
    registry_before = copy.deepcopy(app_module._v2_load_projects())
    original_save = app_module._v2_save_projects

    def fail_moved(projects):
        if any(Path(item.get("project_root") or "").name == "rollback-project" for item in projects):
            raise OSError("registry unavailable")
        return original_save(projects)

    monkeypatch.setattr(app_module, "_v2_save_projects", fail_moved)
    response = client.post(f"/api/projects/{project_id}/edit", data={
        "project_name": "Rollback Project",
        "task_description": record["task_description"],
        "dataset_context": record["dataset_context"],
        "project_parent_dir": str(tmp_path / "projects"),
        "use_canonical_default_location": "true",
        "confirm_project_relocation": "true",
    })

    assert response.status_code == 500
    assert source.is_dir()
    assert app_module._v2_tree_manifest(source) == tree_before
    assert not (tmp_path / "projects" / "rollback-project").exists()
    assert app_module._v2_load_projects() == registry_before


def test_arbitrary_custom_managed_location_is_rejected(tmp_path):
    custom = tmp_path / "custom"
    custom.mkdir()
    response = _client().post("/api/projects", data={
        "project_id": "prj_" + "2" * 32,
        "project_name": "No Custom Root",
        "task_description": "Investigate a testable mechanism.",
        "dataset_context": "A documented test dataset.",
        "project_parent_dir": str(custom),
    })
    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_project_location"
    assert list(custom.iterdir()) == []
