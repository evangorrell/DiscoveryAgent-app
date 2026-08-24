"""Readable project directory leaves and normalized display-name identity."""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

import pytest

from da_selection_ui import app as app_module


def _client():
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
    return client


def _create(client, *, name="Readable Project", project_id=None, parent=None):
    data = {
        "project_name": name,
        "task_description": "Investigate a testable mechanism.",
        "dataset_context": "A documented temporary dataset.",
    }
    if project_id is not None:
        data["project_id"] = project_id
    if parent is not None:
        data["project_parent_dir"] = str(parent)
    return client.post("/api/projects", data=data)


def _edit(
    client,
    project_id,
    *,
    name,
    parent=None,
    confirm=False,
    use_default=False,
    query_count="12",
):
    data = {
        "project_name": name,
        "task_description": "Investigate a testable mechanism.",
        "dataset_context": "A documented temporary dataset.",
        "query_count": query_count,
    }
    if parent is not None:
        data["project_parent_dir"] = str(parent)
    if confirm:
        data["confirm_project_relocation"] = "true"
    if use_default:
        data["use_canonical_default_location"] = "true"
    return client.post(f"/api/projects/{project_id}/edit", data=data)


def test_default_and_custom_creation_use_readable_leaf_and_immutable_marker(tmp_path):
    client = _client()
    default_id = "prj_" + "1" * 32
    custom_id = "prj_" + "2" * 32
    custom_parent = tmp_path / "custom parent"
    custom_parent.mkdir()

    default = _create(client, name="Multiregion Alzheimers Disease", project_id=default_id)
    custom = _create(client, name="Optic_Nerve / Regeneration", project_id=custom_id, parent=custom_parent)

    assert default.status_code == custom.status_code == 200
    default_root = Path(app_module._v2_get_project(default_id)["project_root"])
    custom_root = Path(app_module._v2_get_project(custom_id)["project_root"])
    assert default_root.name == "multiregion-alzheimers-disease"
    assert custom_root == custom_parent / "optic-nerve-regeneration"
    assert "--" not in default_root.name
    assert default_id not in default_root.name
    assert custom_id not in custom_root.name
    for root, project_id, display_name in (
        (default_root, default_id, "Multiregion Alzheimers Disease"),
        (custom_root, custom_id, "Optic_Nerve / Regeneration"),
    ):
        marker = json.loads((root / ".discoveryagent" / "project.json").read_text(encoding="utf-8"))
        assert marker == {"project_id": project_id, "display_name": display_name}
    raw = json.loads((tmp_path / "v2_projects.json").read_text(encoding="utf-8"))["projects"]
    assert {record["project_root"] for record in raw} == {str(default_root), str(custom_root)}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("  My___Project  ", "my-project"),
        ("../Unsafe: Name? <test>|*", "unsafe-name-test"),
        ("Café DATA", "café-data"),
        ("東京 研究", "東京-研究"),
        ("... ___ 💥 / ", ""),
    ],
)
def test_directory_slug_normalizes_unsafe_unicode_and_empty_values(name, expected):
    assert app_module._v2_project_directory_slug(name) == expected


def test_directory_slug_is_bounded_and_directory_name_contains_no_identity_suffix():
    slug = app_module._v2_project_directory_slug("界" * 100)
    leaf = app_module._v2_project_directory_name("界" * 100)
    assert len(slug) == app_module._V2_PROJECT_DIRECTORY_SLUG_MAX_LENGTH
    assert leaf == slug
    assert "--" not in leaf
    assert "prj_" not in leaf
    assert len(leaf.encode("utf-8")) < 255


def test_create_rejects_an_invalid_client_pending_identity_without_writing():
    response = _create(_client(), name="Create Identity Guard", project_id="legacy-edit-id")

    assert response.status_code == 400
    assert response.get_json() == {
        "success": False,
        "code": "invalid_project_location",
        "error": "The pending project identity is invalid. Close and reopen Create Project.",
    }
    assert app_module._v2_load_projects() == []


def test_name_without_safe_slug_is_rejected_without_creating_a_project():
    response = _create(_client(), name="... ___ 💥 / ")

    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_project_name"
    assert app_module._v2_load_projects() == []


@pytest.mark.parametrize(
    ("first_name", "duplicate_name"),
    [
        ("My Project", "my project"),
        ("My Project", "My   Project"),
        ("Café Study", "Cafe\u0301 Study"),
        ("Straße Project", "STRASSE PROJECT"),
    ],
)
def test_create_rejects_normalized_duplicate_names(first_name, duplicate_name):
    client = _client()
    assert _create(client, name=first_name).status_code == 200

    duplicate = _create(client, name=duplicate_name)

    assert duplicate.status_code == 409
    assert duplicate.get_json() == {
        "success": False,
        "code": "duplicate_project_name",
        "error": f"A project named “{duplicate_name}” already exists. Choose a different project name.",
    }
    assert len(app_module._v2_load_projects()) == 1


def test_concurrent_duplicate_creation_is_serialized():
    barrier = threading.Barrier(2)
    results = []

    def create(name):
        client = _client()
        barrier.wait(timeout=5)
        response = _create(client, name=name)
        results.append((response.status_code, response.get_json()))

    first = threading.Thread(target=create, args=("Concurrent Project",))
    second = threading.Thread(target=create, args=(" concurrent   project ",))
    first.start()
    second.start()
    first.join(10)
    second.join(10)

    assert sorted(status for status, _body in results) == [200, 409]
    assert len(app_module._v2_load_projects()) == 1


def test_distinct_names_with_same_slug_and_concurrent_paths_are_rejected():
    barrier = threading.Barrier(2)
    results = []

    def create(name):
        client = _client()
        barrier.wait(timeout=5)
        response = _create(client, name=name)
        results.append((response.status_code, response.get_json()))

    first = threading.Thread(target=create, args=("Concurrent/Project",))
    second = threading.Thread(target=create, args=("Concurrent - Project",))
    first.start()
    second.start()
    first.join(10)
    second.join(10)

    assert sorted(status for status, _body in results) == [200, 409]
    rejected = next(body for status, body in results if status == 409)
    assert rejected["code"] == "duplicate_project_slug"
    records = app_module._v2_load_projects()
    assert len(records) == 1
    assert Path(records[0]["project_root"]).name == "concurrent-project"


def test_edit_unchanged_normalized_name_is_allowed_but_duplicate_rename_is_rejected():
    client = _client()
    first_id = _create(client, name="Alpha Project").get_json()["project_id"]
    second_id = _create(client, name="Beta Project").get_json()["project_id"]
    first_root = Path(app_module._v2_get_project(first_id)["project_root"])
    second_root = Path(app_module._v2_get_project(second_id)["project_root"])

    unchanged = _edit(client, first_id, name="  ALPHA   PROJECT  ")
    duplicate = _edit(client, second_id, name="alpha project")

    assert unchanged.status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.get_json()["code"] == "duplicate_project_name"
    assert Path(app_module._v2_get_project(first_id)["project_root"]) == first_root
    assert Path(app_module._v2_get_project(second_id)["project_root"]) == second_root
    assert app_module._v2_get_project(second_id)["display_name"] == "Beta Project"


def test_historical_duplicate_registry_loads_and_unrelated_save_does_not_rewrite_other_record(tmp_path):
    client = _client()
    first_id = _create(client, name="Historical One").get_json()["project_id"]
    second_id = _create(client, name="Historical Two").get_json()["project_id"]
    registry_path = tmp_path / "v2_projects.json"
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    second = next(record for record in payload["projects"] if record["id"] == second_id)
    second["display_name"] = " historical   one "
    registry_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    response = _edit(client, first_id, name="Historical One", query_count="17")

    assert response.status_code == 200
    loaded = app_module._v2_load_projects()
    assert len(loaded) == 2
    preserved = next(record for record in loaded if record["id"] == second_id)
    assert preserved["display_name"] == " historical   one "
    assert app_module._v2_get_project(first_id)["query_count"] == 17


def test_rename_alone_preserves_existing_root_and_explicit_relocation_uses_new_readable_leaf(tmp_path):
    client = _client()
    project_id = "prj_" + "b" * 32
    assert _create(client, name="Original Name", project_id=project_id).status_code == 200
    original_root = Path(app_module._v2_get_project(project_id)["project_root"])

    renamed = _edit(client, project_id, name="Renamed Display")
    assert renamed.status_code == 200
    assert Path(app_module._v2_get_project(project_id)["project_root"]) == original_root
    assert original_root.name == "original-name"

    destination_parent = tmp_path / "relocated"
    destination_parent.mkdir()
    moved = _edit(
        client, project_id, name="Renamed Display", parent=destination_parent, confirm=True,
    )
    destination = destination_parent / "renamed-display"
    assert moved.status_code == 200
    assert not original_root.exists()
    assert Path(app_module._v2_get_project(project_id)["project_root"]) == destination
    assert app_module._v2_project_metadata_identity(destination) == project_id


def test_edit_legacy_identity_uses_default_slug_without_create_identity_validation(tmp_path):
    client = _client()
    original_id = "prj_" + "9" * 32
    legacy_id = "legacy-optic-project"
    custom_parent = tmp_path / "custom-location"
    custom_parent.mkdir()
    created = _create(
        client, name="Optic Nerve Regeneration", project_id=original_id, parent=custom_parent,
    )
    assert created.status_code == 200
    source = Path(app_module._v2_get_project(original_id)["project_root"])

    with app_module._V2_PROJECTS_LOCK:
        projects, record, registry_path, managed = app_module._v2_locate_project(original_id)
        record["id"] = legacy_id
        app_module._v2_save_projects(projects, registry_path, managed=managed)
    app_module.write_project_metadata(source, legacy_id, "Optic Nerve Regeneration")

    default_parent = app_module._v2_projects_root()
    destination = default_parent / "optic-nerve-regeneration"
    pending = _edit(
        client,
        legacy_id,
        name="Optic Nerve Regeneration",
        parent=default_parent,
        use_default=True,
        query_count="17",
    )

    assert pending.status_code == 409
    assert pending.get_json()["code"] == "project_relocation_confirmation_required"
    assert "Create Project" not in pending.get_data(as_text=True)
    assert source.is_dir()
    assert not destination.exists()
    unchanged = app_module._v2_get_project(legacy_id)
    assert unchanged["id"] == legacy_id
    assert unchanged.get("query_count") != 17

    saved = _edit(
        client,
        legacy_id,
        name="Optic Nerve Regeneration",
        parent=default_parent,
        confirm=True,
        use_default=True,
        query_count="17",
    )

    assert saved.status_code == 200, saved.get_data(as_text=True)
    assert not source.exists()
    assert destination.is_dir()
    relocated = app_module._v2_get_project(legacy_id)
    assert relocated["id"] == legacy_id
    assert relocated["project_root"] == str(destination)
    assert relocated["query_count"] == 17
    assert app_module._v2_project_metadata_identity(destination) == legacy_id


def test_use_default_canonicalizes_an_opaque_root_already_under_managed_parent():
    client = _client()
    project_id = "prj_" + "8" * 32
    assert _create(client, name="Managed Legacy", project_id=project_id).status_code == 200
    readable_root = Path(app_module._v2_get_project(project_id)["project_root"])
    opaque_root = readable_root.parent / project_id
    readable_root.rename(opaque_root)
    with app_module._V2_PROJECTS_LOCK:
        projects, record, registry_path, managed = app_module._v2_locate_project(project_id)
        index = projects.index(record)
        projects[index] = app_module._v2_rebased_project_record(record, readable_root, opaque_root)
        projects[index]["project_parent_dir"] = str(opaque_root.parent)
        app_module._v2_save_projects(projects, registry_path, managed=managed)

    pending = _edit(
        client,
        project_id,
        name="Managed Legacy",
        parent=app_module._v2_projects_root(),
        use_default=True,
    )
    assert pending.status_code == 409
    assert pending.get_json()["code"] == "project_relocation_confirmation_required"
    assert opaque_root.is_dir() and not readable_root.exists()

    saved = _edit(
        client,
        project_id,
        name="Managed Legacy",
        parent=app_module._v2_projects_root(),
        use_default=True,
        confirm=True,
    )
    assert saved.status_code == 200
    assert not opaque_root.exists() and readable_root.is_dir()
    assert Path(app_module._v2_get_project(project_id)["project_root"]) == readable_root


def test_use_default_at_exact_canonical_root_does_not_schedule_relocation():
    client = _client()
    project_id = "prj_" + "7" * 32
    assert _create(client, name="Already Default", project_id=project_id).status_code == 200
    root = Path(app_module._v2_get_project(project_id)["project_root"])

    saved = _edit(
        client,
        project_id,
        name="Already Default",
        parent=app_module._v2_projects_root(),
        use_default=True,
        query_count="19",
    )

    assert saved.status_code == 200
    assert root.is_dir()
    assert Path(app_module._v2_get_project(project_id)["project_root"]) == root
    assert app_module._v2_get_project(project_id)["query_count"] == 19


def test_destination_collision_rejects_without_merging_or_writing_registry(tmp_path):
    client = _client()
    project_id = "prj_" + "c" * 32
    parent = tmp_path / "collision parent"
    parent.mkdir()
    destination = parent / "collision-project"
    destination.mkdir()
    sentinel = destination / "untouched.txt"
    sentinel.write_text("keep", encoding="utf-8")

    response = _create(client, name="Collision Project", project_id=project_id, parent=parent)

    assert response.status_code == 409
    assert response.get_json()["code"] == "duplicate_project_slug"
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert app_module._v2_get_project(project_id) is None


def test_case_and_unicode_equivalent_existing_destination_is_not_adopted(tmp_path):
    client = _client()
    parent = tmp_path / "equivalent collision"
    parent.mkdir()
    existing = parent / "CAFÉ-STUDY"
    existing.mkdir()
    sentinel = existing / "untouched.txt"
    sentinel.write_text("keep", encoding="utf-8")

    response = _create(client, name="Cafe\u0301 Study", parent=parent)

    assert response.status_code == 409
    assert response.get_json()["code"] == "duplicate_project_slug"
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert app_module._v2_load_projects() == []


def test_explicit_relocation_rejects_a_distinct_name_with_colliding_slug(tmp_path):
    client = _client()
    target_parent = tmp_path / "relocation target"
    target_parent.mkdir()
    first = _create(client, name="Alpha/Beta", parent=target_parent)
    second = _create(client, name="Second Project")
    second_id = second.get_json()["project_id"]
    second_root = Path(app_module._v2_get_project(second_id)["project_root"])

    response = _edit(
        client, second_id, name="Alpha - Beta", parent=target_parent, confirm=True,
    )

    assert first.status_code == second.status_code == 200
    assert response.status_code == 409
    assert response.get_json()["code"] == "duplicate_project_slug"
    assert second_root.is_dir()
    assert Path(app_module._v2_get_project(second_id)["project_root"]) == second_root


def test_marker_and_registered_root_not_basename_drive_resolution_and_deletion(tmp_path, monkeypatch):
    client = _client()
    project_id = "prj_" + "d" * 32
    _create(client, name="Identity Marker", project_id=project_id)
    record = app_module._v2_get_project(project_id)
    root = Path(record["project_root"])
    assert root.name != project_id
    normalized = app_module._v2_normalize_registered_project_for_resolution(
        copy.deepcopy(record), managed=True, registry_parent=tmp_path,
    )
    assert Path(normalized["project_root"]) == root
    assert app_module._v2_project_delete_root(record, managed=True) == root


def test_existing_opaque_root_remains_until_explicit_relocation(tmp_path):
    client = _client()
    project_id = "prj_" + "e" * 32
    _create(client, name="Opaque Existing", project_id=project_id)
    record = app_module._v2_get_project(project_id)
    readable_root = Path(record["project_root"])
    opaque_root = readable_root.parent / project_id
    readable_root.rename(opaque_root)
    with app_module._V2_PROJECTS_LOCK:
        projects, stored, registry_path, managed = app_module._v2_locate_project(project_id)
        projects[projects.index(stored)] = app_module._v2_rebased_project_record(
            stored, readable_root, opaque_root,
        )
        projects[projects.index(next(item for item in projects if item["id"] == project_id))]["project_parent_dir"] = str(opaque_root.parent)
        app_module._v2_save_projects(projects, registry_path, managed=managed)

    response = _edit(client, project_id, name="Opaque Renamed")

    assert response.status_code == 200
    assert opaque_root.is_dir()
    assert Path(app_module._v2_get_project(project_id)["project_root"]) == opaque_root


def test_existing_slug_short_id_root_remains_unchanged_on_edit():
    client = _client()
    project_id = "prj_" + "f" * 32
    _create(client, name="Suffix Existing", project_id=project_id)
    record = app_module._v2_get_project(project_id)
    original_root = Path(record["project_root"])
    suffix_root = original_root.parent / "suffix-existing--prj_ffffffff"
    original_root.rename(suffix_root)
    with app_module._V2_PROJECTS_LOCK:
        projects, stored, registry_path, managed = app_module._v2_locate_project(project_id)
        index = next(i for i, item in enumerate(projects) if item["id"] == project_id)
        projects[index] = app_module._v2_rebased_project_record(stored, original_root, suffix_root)
        projects[index]["project_parent_dir"] = str(suffix_root.parent)
        app_module._v2_save_projects(projects, registry_path, managed=managed)

    response = _edit(client, project_id, name="Suffix Renamed")

    assert response.status_code == 200
    assert suffix_root.is_dir()
    assert Path(app_module._v2_get_project(project_id)["project_root"]) == suffix_root
