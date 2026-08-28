"""Readable project directory leaves and normalized display-name identity."""

from __future__ import annotations

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


def test_default_creation_uses_readable_leaf_and_immutable_marker(tmp_path):
    client = _client()
    default_id = "prj_" + "1" * 32

    default = _create(client, name="Multiregion Alzheimers Disease", project_id=default_id)

    assert default.status_code == 200
    default_root = Path(app_module._v2_get_project(default_id)["project_root"])
    assert default_root.name == "multiregion-alzheimers-disease"
    assert "--" not in default_root.name
    assert default_id not in default_root.name
    marker = json.loads((default_root / ".discoveryagent" / "project.json").read_text(encoding="utf-8"))
    assert marker == {"project_id": default_id, "display_name": "Multiregion Alzheimers Disease"}
    raw = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))["projects"]
    assert {record["project_root"] for record in raw} == {str(default_root)}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("  My___Project  ", "my-project"),
        ("../Unsafe: Name? <test>|*", "unsafe-name-test"),
        ("Café DATA", "café-data"),
        ("東京 研究", "東京-研究"),
        ("... ___ 💥 / ", "project"),
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


def test_name_without_alphanumeric_slug_uses_safe_fallback():
    response = _create(_client(), name="... ___ 💥 / ")

    assert response.status_code == 200
    record = app_module._v2_get_project(response.get_json()["project_id"])
    assert Path(record["project_root"]).name == "project"


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
    registry_path = tmp_path / "projects.json"
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
