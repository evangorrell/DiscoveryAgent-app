"""Data Library ownership, containment, project, and runtime regressions."""

from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from da_selection_ui import app as app_module
from da_selection_ui import data_library
from da_selection_ui import local_round_runner as runner


def configure_roots(monkeypatch, tmp_path):
    root = tmp_path / "app-data"
    projects = root / "projects"
    projects.mkdir(parents=True)
    monkeypatch.setattr(data_library, "get_data_root", lambda: root)
    monkeypatch.setattr(data_library, "get_projects_root", lambda: projects)
    monkeypatch.setattr(app_module, "get_data_root", lambda: root)
    monkeypatch.setattr(app_module, "get_projects_root", lambda: projects)
    monkeypatch.setattr(app_module, "get_project_root", lambda project_id: projects / project_id)
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: root / "legacy")
    return root, projects


def source_folder(tmp_path, name="lab-input"):
    root = tmp_path / name
    (root / "nested").mkdir(parents=True)
    (root / "counts.csv").write_text("gene,count\nATF3,4\n", encoding="utf-8")
    (root / "nested" / "metadata.json").write_text('{"cohort":"A"}', encoding="utf-8")
    return root


def snapshot(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def authenticated_client():
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
    return client


def fake_picker_command(result_path, *_args, **_kwargs):
    return ["picker", str(result_path)]


def picker_result_process(payload, *, returncode=0, stderr=""):
    def run(command, *_args, **_kwargs):
        if payload is not None:
            Path(command[-1]).write_text(payload, encoding="utf-8")
        return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)

    return run


def create_project(client, name, selection=None):
    return client.post("/api/projects", data={
        "project_name": name,
        "task_description": "Find a robust relationship.",
        "dataset_context": "User-supplied expression counts and metadata.",
        "data_sources": json.dumps(selection or []),
    })


def test_registry_is_metadata_only_atomic_and_ids_are_stable(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    before = snapshot(source)

    added, created = data_library.add_source(str(source))
    duplicate, duplicate_created = data_library.add_source(str(source / "."))

    assert created is True and duplicate_created is False
    assert added["id"] == duplicate["id"] and added["id"].startswith("ds_")
    assert snapshot(source) == before
    registry = json.loads(data_library.registry_path().read_text(encoding="utf-8"))
    assert registry["schema_version"] == 1
    assert len(registry["datasets"]) == 1
    assert "size_bytes" not in registry["datasets"][0]
    assert not list(data_library.registry_path().parent.glob("data_library.json.tmp.*"))


def test_missing_registry_is_empty_but_malformed_registry_is_not_overwritten(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    assert data_library.load_registry() == {"schema_version": 1, "datasets": []}
    path = data_library.registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not-json", encoding="utf-8")

    with pytest.raises(data_library.DataLibraryError) as error:
        data_library.add_source(str(source_folder(tmp_path)))

    assert error.value.code == "data_library_corrupt"
    assert path.read_text(encoding="utf-8") == "not-json"


def test_missing_legacy_display_name_is_atomically_repaired_without_changing_identity_or_data(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path, 'Cohort, “Δelta”')
    registered, _ = data_library.add_source(str(source))
    selection = [{
        "dataset_id": registered["id"],
        "selected_paths": ["."],
        "excluded_paths": [],
    }]
    client = authenticated_client()
    project_id = create_project(client, "Legacy registry reference", selection).get_json()["project_id"]
    before_data = snapshot(source)
    registry_file = data_library.registry_path()
    raw = json.loads(registry_file.read_text(encoding="utf-8"))
    del raw["datasets"][0]["display_name"]
    registry_file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    repaired = data_library.load_registry()["datasets"][0]
    persisted = json.loads(registry_file.read_text(encoding="utf-8"))["datasets"][0]

    assert repaired["display_name"] == source.name
    assert persisted["display_name"] == source.name
    assert repaired["id"] == persisted["id"] == registered["id"]
    assert data_library.validate_selection(selection) == selection
    assert app_module._v2_get_project(project_id)["data_sources"] == selection
    assert snapshot(source) == before_data
    assert not list(registry_file.parent.glob("data_library.json.tmp.*"))


def test_concurrent_registration_serializes_and_deduplicates(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: data_library.add_source(str(source)), range(24)))
    assert len({record["id"] for record, _created in results}) == 1
    assert sum(created for _record, created in results) == 1
    assert len(data_library.load_registry()["datasets"]) == 1


def test_disconnected_source_is_invalid_and_resolution_keeps_structured_error(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    registered, _ = data_library.add_source(str(source))
    disconnected = tmp_path / "disconnected"
    source.rename(disconnected)

    assert data_library.invalid_source_ids() == [registered["id"]]
    assert "available" not in data_library.list_sources()[0]
    assert "availability" not in data_library.list_sources()[0]
    with pytest.raises(data_library.DataLibraryError) as error:
        data_library.resolve_selection([{"dataset_id": registered["id"], "selected_paths": ["."], "excluded_paths": []}])
    assert error.value.code == "data_path_unavailable"


def test_remove_sources_forgets_multiple_references_in_one_registry_write(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    first, _ = data_library.add_source(str(source_folder(tmp_path, "first")))
    second, _ = data_library.add_source(str(source_folder(tmp_path, "second")))

    removed = data_library.remove_sources({first["id"], second["id"]})

    assert {record["id"] for record in removed} == {first["id"], second["id"]}
    assert data_library.list_sources() == []


def test_tree_is_lazy_sorted_bounded_and_never_reads_contents(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    (source / ".DS_Store").write_text("metadata", encoding="utf-8")
    (source / "._counts.csv").write_text("metadata", encoding="utf-8")
    (source / ".AppleDouble").mkdir()
    for index in range(205):
        (source / f"file-{index:03}.txt").write_text("secret", encoding="utf-8")
    record, _ = data_library.add_source(str(source))

    first = data_library.list_children(record["id"])
    second = data_library.list_children(record["id"], cursor=first["next_cursor"])

    assert len(first["children"]) == data_library.TREE_PAGE_LIMIT
    assert first["children"][0]["kind"] == "directory"
    assert second["next_cursor"] is None
    assert set(first["children"][1]) == {"name", "path", "kind", "expandable", "size_bytes"}
    assert not ({".DS_Store", "._counts.csv", ".AppleDouble"} & {
        child["name"] for child in first["children"] + second["children"]
    })


def test_tree_reports_only_live_immediate_file_sizes_and_survives_one_stat_failure(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    (source / "bytes.bin").write_bytes(b"x" * 842)
    (source / "kilobytes.bin").write_bytes(b"x" * 2458)
    record, _ = data_library.add_source(str(source))

    original_scandir = data_library.os.scandir

    class EntryProxy:
        def __init__(self, entry):
            self._entry = entry
            self.name = entry.name

        def __getattr__(self, name):
            return getattr(self._entry, name)

        def stat(self, *, follow_symlinks=True):
            if self.name == "counts.csv":
                raise PermissionError("simulated stat race")
            return self._entry.stat(follow_symlinks=follow_symlinks)

    def flaky_scandir(path):
        with original_scandir(path) as entries:
            return [EntryProxy(entry) for entry in entries]

    monkeypatch.setattr(data_library.os, "scandir", flaky_scandir)
    root_page = data_library.list_children(record["id"])
    by_name = {item["name"]: item for item in root_page["children"]}

    assert by_name["nested"]["kind"] == "directory"
    assert by_name["nested"]["size_bytes"] is None
    assert by_name["bytes.bin"]["size_bytes"] == 842
    assert by_name["kilobytes.bin"]["size_bytes"] == 2458
    assert by_name["counts.csv"]["size_bytes"] is None
    nested_page = data_library.list_children(record["id"], "nested")
    assert nested_page["children"][0]["size_bytes"] == len(b'{"cohort":"A"}')

    persisted = data_library.registry_path().read_text(encoding="utf-8")
    assert "size_bytes" not in persisted


def test_tree_returns_structured_error_if_folder_disappears_during_listing(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    record, _ = data_library.add_source(str(source))
    original_scandir = data_library.os.scandir

    def missing_on_list(path):
        if Path(path) == source.resolve():
            raise FileNotFoundError("source disappeared after resolution")
        return original_scandir(path)

    monkeypatch.setattr(data_library.os, "scandir", missing_on_list)
    response = app_module.app.test_client().get(
        f"/api/data-library/sources/{record['id']}/tree"
    )
    assert response.status_code == 409
    assert response.get_json()["code"] == "data_path_unavailable"


@pytest.mark.parametrize("relative", ["../outside", "/tmp", "nested/../../outside", "%2e%2e/outside"])
def test_tree_rejects_traversal_and_absolute_injection(monkeypatch, tmp_path, relative):
    configure_roots(monkeypatch, tmp_path)
    record, _ = data_library.add_source(str(source_folder(tmp_path)))
    with pytest.raises(data_library.DataLibraryError):
        data_library.list_children(record["id"], relative)


def test_tree_does_not_follow_nested_symlinks(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("private", encoding="utf-8")
    (source / "escape").symlink_to(outside, target_is_directory=True)
    record, _ = data_library.add_source(str(source))

    listed = data_library.list_children(record["id"])
    escape = next(item for item in listed["children"] if item["name"] == "escape")
    assert escape == {"name": "escape", "path": "escape", "kind": "symlink", "expandable": False, "size_bytes": None}
    with pytest.raises(data_library.DataLibraryError) as error:
        data_library.list_children(record["id"], "escape")
    assert error.value.code == "data_symlink_not_allowed"


def test_project_selection_is_normalized_and_individual_file_is_narrow_root(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    record, _ = data_library.add_source(str(source))
    selection = data_library.normalize_selection([
        {"dataset_id": record["id"], "selected_paths": ["nested/metadata.json", "nested/metadata.json"]},
    ])
    roots, manifest = data_library.resolve_selection(selection)

    assert selection == [{"dataset_id": record["id"], "selected_paths": ["nested/metadata.json"], "excluded_paths": []}]
    assert roots == [str((source / "nested" / "metadata.json").resolve())]
    assert manifest[0]["read_only"] is True
    assert manifest[0]["exclusions_hard_enforced"] is True


def test_individual_file_is_registered_and_resolved_without_copying(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = tmp_path / "standalone measurements.csv"
    source.write_text("sample,value\nA,7\n", encoding="utf-8")
    before = source.read_bytes()
    client = authenticated_client()

    added = client.post("/api/data-library/sources", json={"path": str(source)})
    payload = added.get_json()["source"]
    selection = [{"dataset_id": payload["id"], "selected_paths": ["."], "excluded_paths": []}]
    roots, manifest = data_library.resolve_selection(selection)
    created = create_project(client, "Standalone File", selection)

    assert added.status_code == 201
    assert payload["kind"] == "file"
    assert payload["size_bytes"] == len(before)
    assert roots == [str(source.resolve())]
    assert manifest[0]["resolved_read_paths"] == [str(source.resolve())]
    project = app_module._v2_get_project(created.get_json()["project_id"])
    assert not (Path(project["data_dir"]) / source.name).exists()
    assert source.read_bytes() == before


def test_create_keeps_code_data_and_references_library_without_copy(monkeypatch, tmp_path):
    _root, _projects = configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    before = snapshot(source)
    registered, _ = data_library.add_source(str(source))
    selection = [{"dataset_id": registered["id"], "selected_paths": ["counts.csv"], "excluded_paths": []}]
    client = authenticated_client()

    response = create_project(client, "Library Project", selection)

    assert response.status_code == 200, response.get_data(as_text=True)
    project = app_module._v2_get_project(response.get_json()["project_id"])
    project_data = Path(project["code_dir"]) / "data"
    assert project_data.is_dir() and not project_data.is_symlink()
    assert project["data_dir"] == str(project_data)
    assert project["external_data"] is False
    assert project["data_sources"] == selection
    assert not (project_data / "counts.csv").exists()
    assert snapshot(source) == before


def test_two_projects_share_input_but_keep_project_outputs_separate(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    registered, _ = data_library.add_source(str(source))
    selection = [{"dataset_id": registered["id"], "selected_paths": ["."], "excluded_paths": []}]
    client = authenticated_client()
    first = create_project(client, "First", selection).get_json()["project_id"]
    second = create_project(client, "Second", selection).get_json()["project_id"]
    first_record = app_module._v2_get_project(first)
    second_record = app_module._v2_get_project(second)

    (Path(first_record["code_dir"]) / "data" / "derived.csv").write_text("first", encoding="utf-8")
    (Path(second_record["code_dir"]) / "data" / "derived.csv").write_text("second", encoding="utf-8")

    assert (Path(first_record["code_dir"]) / "data" / "derived.csv").read_text() == "first"
    assert (Path(second_record["code_dir"]) / "data" / "derived.csv").read_text() == "second"
    assert not (source / "derived.csv").exists()


def test_deselecting_library_input_never_alters_project_data_or_source(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    source_before = snapshot(source)
    registered, _ = data_library.add_source(str(source))
    selection = [{"dataset_id": registered["id"], "selected_paths": ["."], "excluded_paths": []}]
    client = authenticated_client()
    created = create_project(client, "Deselect", selection).get_json()
    project = app_module._v2_get_project(created["project_id"])
    produced = Path(project["code_dir"]) / "data" / "produced.csv"
    produced.write_text("result", encoding="utf-8")

    response = client.post(f"/api/projects/{project['id']}/edit", data={
        "project_name": project["display_name"],
        "task_description": project["task_description"],
        "dataset_context": project["dataset_context"],
        "data_sources": "[]",
    })

    assert response.status_code == 200
    assert produced.read_text(encoding="utf-8") == "result"
    assert snapshot(source) == source_before


def test_project_only_source_detachment_preserves_library_other_projects_and_round_snapshots(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    removed_source = source_folder(tmp_path, "shared-atlas")
    retained_source = source_folder(tmp_path, "retained-atlas")
    removed_before = snapshot(removed_source)
    retained_before = snapshot(retained_source)
    removed, _ = data_library.add_source(str(removed_source))
    retained, _ = data_library.add_source(str(retained_source))
    removed_selection = {
        "dataset_id": removed["id"],
        "selected_paths": [".", "nested/metadata.json"],
        "excluded_paths": ["nested/private"],
    }
    retained_selection = {
        "dataset_id": retained["id"],
        "selected_paths": ["counts.csv"],
        "excluded_paths": [],
    }
    client = authenticated_client()
    edited_id = create_project(
        client, "Edited project", [removed_selection, retained_selection]
    ).get_json()["project_id"]
    other_id = create_project(client, "Other project", [removed_selection]).get_json()["project_id"]
    edited = app_module._v2_get_project(edited_id)
    initial_selection = edited["data_sources"]
    persisted_removed = next(item for item in initial_selection if item["dataset_id"] == removed["id"])
    persisted_retained = next(item for item in initial_selection if item["dataset_id"] == retained["id"])
    produced = Path(edited["code_dir"]) / "data" / "produced.csv"
    produced.write_text("project-owned", encoding="utf-8")
    config_path = Path(edited["checkpoint_dir"]) / "config.json"
    (config_path.parent / "memory_report.md").write_text("completed", encoding="utf-8")
    config_before = config_path.read_bytes()

    # Opening and cancelling Edit is read-only: no edit POST occurs.
    detail = client.get(f"/api/projects/{edited_id}?selection_only=1")
    assert detail.status_code == 200
    assert app_module._v2_get_project(edited_id)["data_sources"] == initial_selection

    response = client.post(f"/api/projects/{edited_id}/edit", data={
        "project_name": edited["display_name"],
        "task_description": edited["task_description"],
        "dataset_context": edited["dataset_context"],
        "data_sources": json.dumps([persisted_retained]),
    })

    assert response.status_code == 200
    assert app_module._v2_get_project(edited_id)["data_sources"] == [persisted_retained]
    assert app_module._v2_get_project(other_id)["data_sources"] == [persisted_removed]
    assert {item["id"] for item in data_library.list_sources()} == {removed["id"], retained["id"]}
    assert snapshot(removed_source) == removed_before
    assert snapshot(retained_source) == retained_before
    assert produced.read_text(encoding="utf-8") == "project-owned"
    assert config_path.read_bytes() == config_before


def test_create_uses_only_sources_remaining_in_pending_submission(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    omitted_source = source_folder(tmp_path, "omitted-at-create")
    kept_source = source_folder(tmp_path, "kept-at-create")
    omitted, _ = data_library.add_source(str(omitted_source))
    kept, _ = data_library.add_source(str(kept_source))
    kept_selection = [{
        "dataset_id": kept["id"],
        "selected_paths": ["nested"],
        "excluded_paths": [],
    }]

    created = create_project(authenticated_client(), "Pending Create", kept_selection)

    assert created.status_code == 200
    project = app_module._v2_get_project(created.get_json()["project_id"])
    assert project["data_sources"] == kept_selection
    assert all(item["dataset_id"] != omitted["id"] for item in project["data_sources"])
    assert {item["id"] for item in data_library.list_sources()} == {omitted["id"], kept["id"]}


def test_removing_project_does_not_touch_library_source(monkeypatch, tmp_path):
    _root, _projects = configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    before = snapshot(source)
    registered, _ = data_library.add_source(str(source))
    client = authenticated_client()
    project_id = create_project(client, "Disposable", [{"dataset_id": registered["id"], "selected_paths": ["."], "excluded_paths": []}]).get_json()["project_id"]
    trash = tmp_path / "trash"
    trash.mkdir()
    monkeypatch.setattr(app_module, "_v2_move_to_trash", lambda target: target.replace(trash / target.name))

    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 200
    assert snapshot(source) == before
    assert data_library.list_sources()[0]["id"] == registered["id"]


def test_source_removal_detaches_projects_but_touches_neither_domain(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    source_before = snapshot(source)
    registered, _ = data_library.add_source(str(source))
    client = authenticated_client()
    project_id = create_project(client, "Detach", [{"dataset_id": registered["id"], "selected_paths": ["."], "excluded_paths": []}]).get_json()["project_id"]
    project = app_module._v2_get_project(project_id)
    produced = Path(project["code_dir"]) / "data" / "produced.txt"
    produced.write_text("keep", encoding="utf-8")

    preflight = client.delete(f"/api/data-library/sources/{registered['id']}", json={})
    removed = client.delete(f"/api/data-library/sources/{registered['id']}", json={"detach_projects": True})

    assert preflight.status_code == 409
    assert preflight.get_json()["code"] == "dataset_detach_confirmation_required"
    assert removed.status_code == 200
    assert produced.read_text(encoding="utf-8") == "keep"
    assert snapshot(source) == source_before
    assert app_module._v2_get_project(project_id)["data_sources"] == []


def test_source_removal_is_blocked_while_referencing_project_runs(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    registered, _ = data_library.add_source(str(source))
    client = authenticated_client()
    project_id = create_project(client, "Running", [{"dataset_id": registered["id"], "selected_paths": ["."], "excluded_paths": []}]).get_json()["project_id"]
    monkeypatch.setattr(app_module, "_v2_project_is_running", lambda project: project.get("id") == project_id)

    response = client.delete(f"/api/data-library/sources/{registered['id']}", json={"detach_projects": True})

    assert response.status_code == 409
    assert response.get_json()["code"] == "dataset_in_use_by_running_project"
    assert data_library.list_sources()[0]["id"] == registered["id"]
    assert app_module._v2_get_project(project_id)["data_sources"][0]["dataset_id"] == registered["id"]


def test_missing_root_read_preserves_reference_and_selection_and_recovers(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    registered, _ = data_library.add_source(str(source))
    selection = [{"dataset_id": registered["id"], "selected_paths": ["."], "excluded_paths": []}]
    client = authenticated_client()
    project_id = create_project(client, "Missing Input", selection).get_json()["project_id"]
    project = app_module._v2_get_project(project_id)
    produced = Path(project["code_dir"]) / "data" / "produced.csv"
    produced.write_text("project output", encoding="utf-8")
    config_path = Path(project["checkpoint_dir"]) / "config.json"
    config_before = config_path.read_bytes()
    moved = tmp_path / "moved-input"
    source.rename(moved)
    moved_before = snapshot(moved)
    registry_before = data_library.registry_path().read_bytes()
    projects_before = app_module._v2_load_projects()

    response = client.get("/api/data-library")

    assert response.status_code == 200
    assert response.get_json()["sources"][0]["id"] == registered["id"]
    assert response.get_json()["sources"][0]["available"] is False
    assert "preserved" in response.get_json()["sources"][0]["availability_error"]
    assert data_library.registry_path().read_bytes() == registry_before
    assert app_module._v2_load_projects() == projects_before
    assert app_module._v2_get_project(project_id)["data_sources"] == selection
    assert config_path.read_bytes() == config_before
    assert produced.read_text(encoding="utf-8") == "project output"
    assert snapshot(moved) == moved_before
    moved.rename(source)
    recovered = client.get("/api/data-library").get_json()["sources"][0]
    assert recovered["id"] == registered["id"]
    assert recovered["available"] is True
    assert app_module._v2_get_project(project_id)["data_sources"] == selection


def test_missing_nested_child_does_not_remove_valid_top_level_source(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    registered, _ = data_library.add_source(str(source))
    selection = [{"dataset_id": registered["id"], "selected_paths": ["nested"], "excluded_paths": []}]
    client = authenticated_client()
    project_id = create_project(client, "Nested Input", selection).get_json()["project_id"]
    (source / "nested" / "metadata.json").unlink()
    source_after_child_removal = snapshot(source)

    response = client.get("/api/data-library")

    assert [item["id"] for item in response.get_json()["sources"]] == [registered["id"]]
    assert app_module._v2_get_project(project_id)["data_sources"] == selection
    assert snapshot(source) == source_after_child_removal


def test_temporary_access_denial_does_not_detach_source(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    registered, _ = data_library.add_source(str(source))
    selection = [{"dataset_id": registered["id"], "selected_paths": ["."], "excluded_paths": []}]
    client = authenticated_client()
    project_id = create_project(client, "Access Denied", selection).get_json()["project_id"]
    before = data_library.registry_path().read_bytes()
    original_access = os.access
    with monkeypatch.context() as denied:
        denied.setattr(os, "access", lambda path, *args, **kwargs: False if Path(path) == source else original_access(path, *args, **kwargs))
        assert client.get("/api/data-library").get_json()["sources"][0]["available"] is False
        assert app_module._v2_get_project(project_id)["data_sources"] == selection
        assert data_library.registry_path().read_bytes() == before
    assert client.get("/api/data-library").get_json()["sources"][0]["available"] is True


def test_project_detail_retains_saved_selection_when_registry_read_fails(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    registered, _ = data_library.add_source(str(source_folder(tmp_path)))
    selection = [{"dataset_id": registered["id"], "selected_paths": ["."], "excluded_paths": []}]
    client = authenticated_client()
    project_id = create_project(client, "Read Failure", selection).get_json()["project_id"]

    def unavailable_registry():
        raise data_library.DataLibraryError("data_library_unavailable", "Cannot read registry")

    monkeypatch.setattr(data_library, "list_sources", unavailable_registry)
    response = client.get(f"/api/projects/{project_id}?selection_only=1")
    assert response.status_code == 200
    payload = response.get_json()["project"]["data_sources"]
    assert payload["selection"] == selection
    assert payload["error"]["code"] == "data_library_unavailable"
    assert app_module._v2_get_project(project_id)["data_sources"] == selection


def test_unavailable_source_payload_survives_permission_denied_metadata_probe(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    registered, _ = data_library.add_source(str(source))
    original_stat = Path.stat

    def denied_stat(path, *args, **kwargs):
        if path == source:
            raise PermissionError("Temporarily denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied_stat)
    response = authenticated_client().get("/api/data-library")
    assert response.status_code == 200
    payload = response.get_json()["sources"][0]
    assert payload["id"] == registered["id"]
    assert payload["available"] is False


def test_metadata_edit_preserves_existing_unavailable_selection_but_new_attachment_is_rejected(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    registered, _ = data_library.add_source(str(source))
    selection = [{"dataset_id": registered["id"], "selected_paths": ["."], "excluded_paths": []}]
    client = authenticated_client()
    attached_id = create_project(client, "Attached", selection).get_json()["project_id"]
    unattached_id = create_project(client, "Unattached").get_json()["project_id"]
    source.rename(tmp_path / "disconnected")
    for project_id, baseline, expected in [(attached_id, selection, 200), (unattached_id, [], 409)]:
        project = app_module._v2_get_project(project_id)
        response = client.post(f"/api/projects/{project_id}/edit", data={
            "project_name": project["display_name"], "task_description": "Updated objective",
            "dataset_context": project["dataset_context"],
            "data_sources": json.dumps(selection), "data_sources_before": json.dumps(baseline),
        })
        assert response.status_code == expected, response.get_json()
        assert app_module._v2_get_project(project_id)["data_sources"] == baseline


def test_stale_edit_selection_is_rejected_before_renaming_or_writing_project(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    registered, _ = data_library.add_source(str(source_folder(tmp_path)))
    selection = [{"dataset_id": registered["id"], "selected_paths": ["."], "excluded_paths": []}]
    client = authenticated_client()
    project_id = create_project(client, "Current", selection).get_json()["project_id"]
    project = app_module._v2_get_project(project_id)
    project_root = Path(project["project_root"])
    before = snapshot(project_root)
    response = client.post(f"/api/projects/{project_id}/edit", data={
        "project_name": "Stale Rename", "task_description": project["task_description"],
        "dataset_context": project["dataset_context"],
        "data_sources": "[]", "data_sources_before": "[]",
    })
    assert response.status_code == 409
    assert response.get_json()["code"] == "project_data_selection_conflict"
    assert app_module._v2_get_project(project_id) == project
    assert snapshot(project_root) == before


def test_concurrent_missing_source_reads_preserve_references(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    registered, _ = data_library.add_source(str(source))
    selection = [{"dataset_id": registered["id"], "selected_paths": ["."], "excluded_paths": []}]
    project_id = create_project(authenticated_client(), "Concurrent", selection).get_json()["project_id"]
    source.rename(tmp_path / "disconnected-volume")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: app_module._data_library_reconcile_missing_sources(), range(16)))

    assert all([item["id"] for item in result] == [registered["id"]] for result in results)
    assert data_library.list_sources()[0]["id"] == registered["id"]
    assert app_module._v2_get_project(project_id)["data_sources"] == selection


def test_missing_source_reads_never_attempt_detach_or_project_writes(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    registered, _ = data_library.add_source(str(source))
    selection = [{"dataset_id": registered["id"], "selected_paths": ["."], "excluded_paths": []}]
    project_id = create_project(authenticated_client(), "Rollback", selection).get_json()["project_id"]
    source.rename(tmp_path / "temporarily-disconnected")
    monkeypatch.setattr(data_library, "remove_sources", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("write failed")))
    monkeypatch.setattr(app_module, "_v2_save_projects", lambda *_args, **_kwargs: pytest.fail("read must not write projects"))

    assert app_module._data_library_reconcile_missing_sources()[0]["id"] == registered["id"]

    assert app_module._v2_get_project(project_id)["data_sources"] == selection
    assert data_library.list_sources()[0]["id"] == registered["id"]


def test_editing_current_selection_does_not_rewrite_completed_round(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    registered, _ = data_library.add_source(str(source))
    client = authenticated_client()
    project_id = create_project(client, "Completed", []).get_json()["project_id"]
    project = app_module._v2_get_project(project_id)
    round0 = Path(project["checkpoint_dir"])
    (round0 / "memory_report.md").write_text("completed", encoding="utf-8")
    config_path = round0 / "config.json"
    before = config_path.read_bytes()

    response = client.post(f"/api/projects/{project_id}/edit", data={
        "project_name": project["display_name"], "task_description": project["task_description"],
        "dataset_context": project["dataset_context"],
        "data_sources": json.dumps([{"dataset_id": registered["id"], "selected_paths": ["."], "excluded_paths": []}]),
    })

    assert response.status_code == 200
    assert config_path.read_bytes() == before


def test_runtime_adds_library_roots_without_replacing_workspace_or_direct_data(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    registered, _ = data_library.add_source(str(source))
    project = {
        "id": "p", "code_dir": str(tmp_path / "project" / "code"),
        "data_sources": [{"dataset_id": registered["id"], "selected_paths": ["counts.csv"], "excluded_paths": []}],
    }
    config = {"working_directory": project["code_dir"], "allowed_read_paths": "/legacy/direct-data"}
    manifest_path = tmp_path / "round_0" / "input_data_manifest.md"

    runner._apply_data_library_access(project, config, manifest_path=manifest_path)

    assert config["working_directory"] == project["code_dir"]
    assert config["allowed_read_paths"].split(",") == ["/legacy/direct-data", str((source / "counts.csv").resolve())]
    assert config["data_library_selection"] == project["data_sources"]
    assert config["hint_txt_path"] == str(manifest_path)
    assert "Writable project workspace" in manifest_path.read_text(encoding="utf-8")
    assert "read-only" in manifest_path.read_text(encoding="utf-8").lower()


def runtime_project(tmp_path, selection):
    root = tmp_path / "runtime-project"
    code = root / "code"
    (code / "data").mkdir(parents=True)
    readme = code / "data" / "README.md"
    readme.write_text("Project context", encoding="utf-8")
    checkpoints = root / "checkpoint" / "checkpoints"
    round0 = checkpoints / "round_0"
    round0.mkdir(parents=True)
    return {
        "id": "runtime-project", "url_slug": "runtime-project",
        "display_name": "Runtime Project", "task_description": "Test inputs.",
        "project_root": str(root), "code_dir": str(code), "data_dir": str(code / "data"),
        "checkpoint_dir": str(round0), "readme_path": str(readme), "data_sources": selection,
    }, checkpoints


def test_fresh_and_continued_rounds_receive_current_library_selection(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    registered, _ = data_library.add_source(str(source))
    selection = [{"dataset_id": registered["id"], "selected_paths": ["nested"], "excluded_paths": []}]
    project, checkpoints = runtime_project(tmp_path, selection)
    round0 = checkpoints / "round_0"
    paths0 = {
        "code_dir": Path(project["code_dir"]), "log_dir": checkpoints.parent,
        "checkpoints_dir": checkpoints, "config_file": round0 / "config.json", "target_dir": round0,
    }
    (round0 / "config.json").write_text('{"config": {}}', encoding="utf-8")

    runner._hydrate_round_zero_config(project, paths0)
    fresh = json.loads((round0 / "config.json").read_text(encoding="utf-8"))["config"]

    round1 = checkpoints / "round_1"
    round1.mkdir()
    selected_hypothesis = round0 / "selected_hypothesis.md"
    selected_hypothesis.write_text("# Hypothesis", encoding="utf-8")
    paths1 = {
        "code_dir": Path(project["code_dir"]), "log_dir": checkpoints.parent,
        "checkpoints_dir": checkpoints, "config_file": round0 / "config.json", "target_dir": round1,
        "selected_hypothesis_file": selected_hypothesis,
    }
    runner._hydrate_continuation_config(project, paths1)
    continued = json.loads((round1 / "config.json").read_text(encoding="utf-8"))["config"]

    expected = str((source / "nested").resolve())
    assert expected in fresh["allowed_read_paths"].split(",")
    assert expected in continued["allowed_read_paths"].split(",")
    assert fresh["working_directory"] == continued["working_directory"] == project["code_dir"]
    assert fresh["data_library_selection"] == continued["data_library_selection"] == selection


def test_resume_keeps_captured_selection_and_fails_safely_while_source_is_unavailable(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    original = source_folder(tmp_path, "original-input")
    registered, _ = data_library.add_source(str(original))
    saved_selection = [{"dataset_id": registered["id"], "selected_paths": ["counts.csv"], "excluded_paths": []}]
    project, checkpoints = runtime_project(tmp_path, [])
    config_file = checkpoints / "round_0" / "config.json"
    config_file.write_text(json.dumps({"config": {
        "working_directory": project["code_dir"],
        "data_library_selection": saved_selection,
        "data_library_resolved_paths": [str((original / "counts.csv").resolve())],
        "allowed_read_paths": str((original / "counts.csv").resolve()),
    }}), encoding="utf-8")
    before = config_file.read_bytes()
    original.rename(tmp_path / "moved-input")
    app_module._data_library_reconcile_missing_sources()

    with pytest.raises(runner.RoundLaunchError) as error:
        runner._refresh_resume_project_skills(project, config_file)

    assert error.value.code == "data_path_unavailable"
    assert data_library.list_sources()[0]["id"] == registered["id"]
    assert config_file.read_bytes() == before


def test_navigation_and_project_selector_use_one_unified_data_section():
    package = Path(__file__).resolve().parents[1] / "da_selection_ui"
    base = (package / "templates/base.html").read_text(encoding="utf-8")
    projects = (package / "templates/projects.html").read_text(encoding="utf-8")
    data_page = (package / "templates/data_library.html").read_text(encoding="utf-8")
    script = (package / "static/projects.js").read_text(encoding="utf-8")

    assert '<h1 class="v2-title v2-app-page-title">Projects</h1>' in projects and 'id="pj-list"' in projects
    assert 'Start new investigations or continue work across your existing research projects.' in projects
    assert 'class="v2-app-page-header v2-projects-header"' in projects
    assert 'class="v2-app-page-description v2-projects-description"' in projects
    assert 'id="projects-primary-action" hidden' in projects
    assert 'id="data-library-box"' not in projects
    assert base.index(">Projects</a>") < base.index(">Data</a>")
    assert 'class="v2-workspace-sidebar-list v2-global-nav"' in base
    assert "global_nav_active == 'projects'" in base
    assert "global_nav_active == 'data'" in base
    assert 'id="data-library-title">Data</h1>' in data_page and 'class="v2-data-library-box v2-data-tree-container"' in data_page
    assert 'id="data-library-primary-action" hidden' in data_page
    assert 'class="v2-btn v2-btn-primary v2-data-library-add-btn" id="data-library-add"' in data_page
    assert "Add files or folders once, then choose which projects can access them. Data stays in its original location." in data_page
    assert 'class="v2-dropzone v2-data-empty-state v2-data-library-empty"' in data_page
    assert 'aria-label="No data added. Click to add data."' in data_page
    assert "No data added" in data_page
    assert "Click to add data" in data_page
    assert "Choose a user-supplied input folder" not in data_page
    assert "DiscoveryAgent stores only its location" not in data_page
    assert projects.count('id="np-data-field"') == 1
    assert "Select the data this project will have access to." in projects
    assert 'id="np-select-data"' in projects
    assert 'id="np-select-data" hidden' in projects
    assert 'class="v2-project-data-card v2-data-tree-container v2-data-surface-project"' in projects
    assert "Select data" in projects
    assert 'id="np-data-library-add-data"' in projects
    assert '<a class="v2-data-action" id="np-data-library-add-data"' in projects
    assert "href=\"{{ url_for('discoveryagent_data', open_data_picker='1', project_draft='1') }}\"" in projects
    assert "Click anywhere to select data" not in projects
    assert 'aria-label="No data selected. Click to select data."' in projects
    assert "Click to select data" in projects
    assert "No data available" not in projects
    assert "Add data to DiscoveryAgent" not in projects
    assert "to use it in a project." not in projects
    assert "url_for('discoveryagent_data', open_data_picker='1', project_draft='1')" in projects
    assert "INPUT DATA" not in projects and "Open Data Library" not in projects
    assert "Open Data Folder" not in projects and "Copy path" not in projects
    assert "data_sources', JSON.stringify(selectedDataSources)" in script
    assert "checkbox.indeterminate = state.indeterminate" in script
    assert "/api/data-folder/inspect" not in script
    assert "draggable" not in projects.lower()


def test_project_detail_returns_selection_kinds_for_the_unified_hierarchy(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    registered, _ = data_library.add_source(str(source))
    selection = [{
        "dataset_id": registered["id"],
        "selected_paths": ["counts.csv", "nested"],
        "excluded_paths": [],
    }]
    client = authenticated_client()
    project_id = create_project(client, "Hierarchy", selection).get_json()["project_id"]

    payload = client.get(f"/api/projects/{project_id}?selection_only=1").get_json()["project"]

    source_payload = payload["data_sources"]["sources"][0]
    assert "files" not in payload and "data_folder" not in payload
    assert "source_path" not in source_payload
    assert source_payload["display_name"] == source.name
    assert source_payload["path_kinds"] == {
        "counts.csv": "file",
        "nested": "directory",
    }


def test_edit_selection_persists_for_a_fresh_client(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    registered, _ = data_library.add_source(str(source))
    client = authenticated_client()
    project_id = create_project(client, "Persisted", []).get_json()["project_id"]
    project = app_module._v2_get_project(project_id)
    selection = [{
        "dataset_id": registered["id"],
        "selected_paths": ["nested/metadata.json"],
        "excluded_paths": [],
    }]

    saved = client.post(f"/api/projects/{project_id}/edit", data={
        "project_name": project["display_name"],
        "task_description": project["task_description"],
        "dataset_context": project["dataset_context"],
        "data_sources": json.dumps(selection),
    })
    reopened = authenticated_client().get(f"/api/projects/{project_id}")

    assert saved.status_code == 200
    assert reopened.get_json()["project"]["data_sources"]["selection"] == selection


def test_edit_selection_change_is_blocked_during_active_round(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    registered, _ = data_library.add_source(str(source))
    client = authenticated_client()
    project_id = create_project(client, "Running Edit", []).get_json()["project_id"]
    project = app_module._v2_get_project(project_id)
    monkeypatch.setattr(app_module, "_v2_project_is_running", lambda record: record.get("id") == project_id)

    response = client.post(f"/api/projects/{project_id}/edit", data={
        "project_name": project["display_name"],
        "task_description": project["task_description"],
        "dataset_context": project["dataset_context"],
        "data_sources": json.dumps([{
            "dataset_id": registered["id"],
            "selected_paths": ["."],
            "excluded_paths": [],
        }]),
    })

    assert response.status_code == 409
    assert response.get_json()["code"] == "project_data_locked_while_running"
    assert app_module._v2_get_project(project_id)["data_sources"] == []


def test_data_route_renders_dedicated_page_and_api_exposes_lazy_tree(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    client = authenticated_client()

    page = client.get("/data")
    projects_page = client.get("/projects")
    added = client.post("/api/data-library/sources", json={"path": str(source), "display_name": "Shared Atlas"})
    listed = client.get("/api/data-library")
    dataset_id = added.get_json()["source"]["id"]
    tree = client.get(f"/api/data-library/sources/{dataset_id}/tree", query_string={"path": ".", "limit": 1})

    assert page.status_code == 200
    assert b'id="data-library-title">Data</h1>' in page.data
    header_start = page.data.index(b'<header class="v2-app-page-header v2-data-library-header">')
    description_start = page.data.index(b"Add files or folders once, then choose which projects can access them. Data stays in its original location.", header_start)
    header_end = page.data.index(b"</header>", description_start)
    content_start = page.data.index(b'id="data-library-content"', header_end)
    box_start = page.data.index(b'id="data-library-box"', content_start)
    action_start = page.data.index(b'id="data-library-primary-action"', box_start)
    section_end = page.data.index(b"</section>", action_start)
    assert header_start < description_start < header_end < content_start < box_start < action_start < section_end
    assert b'class="v2-data-library-header-row"' not in page.data
    assert b'class="v2-app-page-action v2-sticky-action-tray" id="data-library-primary-action" hidden' in page.data
    assert b'class="v2-btn v2-btn-primary v2-data-library-add-btn" id="data-library-add"' in page.data
    assert b'id="data-library-return-project-draft"' in page.data
    assert b'href="/projects?resume_project_draft=1" hidden>Back to project draft</a>' in page.data
    assert page.data.count(b'id="data-library-add"') == 1
    assert b'id="data-library-box"' in page.data
    assert b'class="v2-dropzone v2-data-empty-state v2-data-library-empty"' in page.data
    assert b'aria-label="No data added. Click to add data."' in page.data
    assert b'No data added' in page.data
    assert b'Click to add data' in page.data
    assert b'aria-current="page">Data</a>' in page.data
    assert projects_page.status_code == 200
    select_data = projects_page.data[projects_page.data.index(b'id="np-select-data"') - 100:
                                     projects_page.data.index(b'id="np-select-data"') + 260]
    assert b'<button ' in select_data and b'Select Data' in select_data
    assert b'Click anywhere to select data' not in projects_page.data
    assert b'aria-label="No data selected. Click to select data."' in projects_page.data
    assert b'Click to select data' in projects_page.data
    modal_add = projects_page.data[projects_page.data.index(b'id="np-data-library-add-data"') - 100:
                                   projects_page.data.index(b'id="np-data-library-add-data"') + 260]
    assert b'<a class="v2-data-action"' in modal_add
    assert b'href="/data?open_data_picker=1&amp;project_draft=1"' in modal_add
    assert b'Add Data' in modal_add and b'&#8599;' in modal_add
    assert b'id="data-library-box"' not in projects_page.data
    assert b'aria-current="page">Projects</a>' in projects_page.data
    assert page.data.count(b'class="v2-workspace-sidebar-list v2-global-nav"') == 1
    assert projects_page.data.count(b'class="v2-workspace-sidebar-list v2-global-nav"') == 1
    assert added.status_code == 201
    assert listed.get_json()["sources"][0]["display_name"] == "Shared Atlas"
    assert listed.get_json()["sources"][0]["available"] is True
    assert listed.get_json()["sources"][0]["availability_error"] is None
    assert "availability" not in listed.get_json()["sources"][0]
    assert tree.status_code == 200
    assert len(tree.get_json()["children"]) == 1
    assert tree.get_json()["next_cursor"] == 1


def test_relink_routes_and_mutators_are_removed(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    registered, _ = data_library.add_source(str(source_folder(tmp_path)))
    client = authenticated_client()

    relink = client.post(f"/api/data-library/sources/{registered['id']}/relink")
    patch = client.patch(f"/api/data-library/sources/{registered['id']}", json={"path": str(tmp_path)})

    assert relink.status_code == 404
    assert patch.status_code == 405
    assert not hasattr(data_library, "update_source")


def test_native_data_library_picker_uses_bundled_launchservices_app(tmp_path):
    result_path = tmp_path / "result.json"
    command = app_module._v2_data_library_picker_command(result_path, "darwin")
    bundle, executable = app_module._v2_data_library_picker_helper_paths()

    assert command == [
        "/usr/bin/open", "-W", "-n", str(bundle), "--args", str(result_path),
    ]
    assert bundle.name == "DiscoveryAgentFolderPicker.app"
    assert executable.is_file() and executable.stat().st_mode & 0o111
    assert "/usr/bin/osascript" not in command


def test_native_picker_json_parser_preserves_path_punctuation_and_unicode():
    paths = [
        '/Volumes/Lab data/ATF3, cohort "A"',
        "/Users/scientist/Δεδομένα/line\nbreak",
    ]

    result = app_module._v2_parse_data_library_picker_result(json.dumps({"status": "selected", "paths": paths}))

    assert result == {"status": "selected", "paths": paths}


def test_native_picker_registers_multiple_references_once_without_scanning_or_copying(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    sources = [
        source_folder(tmp_path, "first input"),
        source_folder(tmp_path, "second,input"),
        source_folder(tmp_path, "Δεδομένα"),
    ]
    before = {str(source): snapshot(source) for source in sources}
    client = authenticated_client()
    monkeypatch.setattr(app_module, "_v2_data_library_picker_command", fake_picker_command)
    save_calls = []
    original_save = data_library._save_unlocked
    monkeypatch.setattr(data_library, "_save_unlocked", lambda payload, path=None: (save_calls.append(payload), original_save(payload, path))[1])

    monkeypatch.setattr(app_module.subprocess, "run", picker_result_process(
        json.dumps({"status": "selected", "paths": [str(source) for source in sources]})
    ))
    response = client.post("/api/data-library/choose")

    assert response.status_code == 201
    payload = response.get_json()
    assert [item["source_path"] for item in payload["added"]] == [str(source) for source in sources]
    assert payload["skipped"] == [] and payload["rejected"] == []
    assert "source" not in payload
    assert len(save_calls) == 1
    assert [item["source_path"] for item in data_library.list_sources()] == [str(source) for source in sources]
    assert {str(source): snapshot(source) for source in sources} == before
    assert "files" not in payload


def test_native_picker_single_selection_retains_compatible_response(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    client = authenticated_client()
    monkeypatch.setattr(app_module, "_v2_data_library_picker_command", fake_picker_command)
    monkeypatch.setattr(app_module.subprocess, "run", picker_result_process(
        json.dumps({"status": "selected", "paths": [str(source)]})
    ))
    response = client.post("/api/data-library/choose")

    assert response.status_code == 201
    assert response.get_json()["source"]["source_path"] == str(source)


def test_native_picker_skips_existing_and_repeated_folders_but_adds_new_peers(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    existing = source_folder(tmp_path, "existing")
    new = source_folder(tmp_path, "new folder")
    existing_record, _ = data_library.add_source(str(existing))
    client = authenticated_client()
    monkeypatch.setattr(app_module, "_v2_data_library_picker_command", fake_picker_command)
    monkeypatch.setattr(app_module.subprocess, "run", picker_result_process(json.dumps({
            "status": "selected",
            "paths": [str(existing), str(existing / "."), str(new), str(new)],
        })))
    response = client.post("/api/data-library/choose")

    payload = response.get_json()
    assert response.status_code == 201
    assert [item["source_path"] for item in payload["added"]] == [str(new)]
    assert len(payload["skipped"]) == 3
    assert all(item["reason"] == "already_registered" for item in payload["skipped"])
    assert payload["skipped"][0]["source"]["id"] == existing_record["id"]
    assert len(data_library.list_sources()) == 2


def test_all_duplicate_picker_batch_performs_no_registry_write(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    source = source_folder(tmp_path)
    registered, _ = data_library.add_source(str(source))
    before = data_library.registry_path().read_bytes()
    save_calls = []
    monkeypatch.setattr(data_library, "_save_unlocked", lambda *_args, **_kwargs: save_calls.append(True))

    result = data_library.add_sources([str(source), str(source / ".")])

    assert result["added"] == [] and result["rejected"] == []
    assert [item["source"]["id"] for item in result["skipped"]] == [registered["id"], registered["id"]]
    assert save_calls == []
    assert data_library.registry_path().read_bytes() == before


def test_native_picker_reports_invalid_peer_without_discarding_valid_sources(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    valid = source_folder(tmp_path, "valid")
    valid_file = tmp_path / "single-file.csv"
    valid_file.write_text("x", encoding="utf-8")
    unavailable = tmp_path / "missing.csv"
    client = authenticated_client()
    monkeypatch.setattr(app_module, "_v2_data_library_picker_command", fake_picker_command)
    monkeypatch.setattr(app_module.subprocess, "run", picker_result_process(
        json.dumps({"status": "selected", "paths": [str(valid), str(valid_file), str(unavailable)]})
    ))
    response = client.post("/api/data-library/choose")

    payload = response.get_json()
    assert response.status_code == 201
    assert [item["source_path"] for item in payload["added"]] == [str(valid), str(valid_file)]
    assert [item["kind"] for item in payload["added"]] == ["directory", "file"]
    assert payload["rejected"] == [{
        "selection_index": 2,
        "path": str(unavailable),
        "code": "data_source_unavailable",
        "error": f"The data source is unavailable: {unavailable}.",
    }]
    assert len(data_library.list_sources()) == 2


def test_native_picker_cancel_is_distinct_and_never_mutates_registry(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    existing, _ = data_library.add_source(str(source_folder(tmp_path)))
    before = data_library.registry_path().read_bytes()
    client = authenticated_client()
    monkeypatch.setattr(app_module, "_v2_data_library_picker_command", fake_picker_command)
    monkeypatch.setattr(app_module.subprocess, "run", picker_result_process(
        json.dumps({"status": "cancelled", "paths": []})
    ))
    response = client.post("/api/data-library/choose")

    assert response.status_code == 200
    assert response.get_json() == {
        "success": True, "cancelled": True,
        "added": [], "skipped": [], "rejected": [],
    }
    assert data_library.registry_path().read_bytes() == before
    assert data_library.list_sources()[0]["id"] == existing["id"]


@pytest.mark.parametrize(("result", "expected_code"), [
    (None, "native_picker_failed"),
    ("", "native_picker_failed"),
    ("not-json", "native_picker_invalid_result"),
    ('{"status":"selected","paths":[]}', "native_picker_invalid_result"),
])
def test_native_picker_incomplete_result_is_an_error_not_a_cancel(
    monkeypatch, tmp_path, result, expected_code,
):
    configure_roots(monkeypatch, tmp_path)
    existing, _ = data_library.add_source(str(source_folder(tmp_path)))
    before = data_library.registry_path().read_bytes()
    client = authenticated_client()
    monkeypatch.setattr(app_module, "_v2_data_library_picker_command", fake_picker_command)
    monkeypatch.setattr(app_module.subprocess, "run", picker_result_process(result))
    response = client.post("/api/data-library/choose")

    assert response.status_code == 500
    assert response.get_json()["code"] == expected_code
    assert response.get_json()["success"] is False
    assert data_library.registry_path().read_bytes() == before
    assert data_library.list_sources()[0]["id"] == existing["id"]


def test_concurrent_native_picker_request_is_rejected_without_launch(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    client = authenticated_client()
    monkeypatch.setattr(
        app_module.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("a concurrent request must not launch another picker"),
    )

    assert app_module._V2_DATA_LIBRARY_PICKER_LOCK.acquire(blocking=False)
    try:
        response = client.post("/api/data-library/choose")
    finally:
        app_module._V2_DATA_LIBRARY_PICKER_LOCK.release()

    assert response.status_code == 409
    assert response.get_json()["code"] == "native_picker_busy"
    assert response.get_json()["success"] is False


def test_native_picker_launch_failure_releases_lock_and_permits_retry(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    client = authenticated_client()
    monkeypatch.setattr(app_module, "_v2_data_library_picker_command", fake_picker_command)
    calls = []

    def run(command, *_args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise OSError("launch failed")
        return picker_result_process(json.dumps({"status": "cancelled", "paths": []}))(
            command
        )

    monkeypatch.setattr(app_module.subprocess, "run", run)
    failed = client.post("/api/data-library/choose")
    retried = client.post("/api/data-library/choose")

    assert failed.status_code == 500
    assert failed.get_json()["code"] == "native_picker_launch_failed"
    assert retried.status_code == 200
    assert retried.get_json()["cancelled"] is True
    assert len(calls) == 2
    assert all(call["timeout"] == app_module._V2_DATA_LIBRARY_PICKER_TIMEOUT_SECONDS for call in calls)
    assert app_module._V2_DATA_LIBRARY_PICKER_TIMEOUT_SECONDS >= 60 * 60


def test_native_picker_timeout_is_distinct_and_releases_lock(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    client = authenticated_client()
    monkeypatch.setattr(app_module, "_v2_data_library_picker_command", fake_picker_command)
    stopped = []
    monkeypatch.setattr(app_module, "_v2_stop_data_library_picker_helper", lambda result: stopped.append(result))
    monkeypatch.setattr(
        app_module.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(["picker"], app_module._V2_DATA_LIBRARY_PICKER_TIMEOUT_SECONDS)
        ),
    )

    response = client.post("/api/data-library/choose")

    assert response.status_code == 504
    assert response.get_json()["code"] == "native_picker_timeout"
    assert len(stopped) == 1 and stopped[0].name == "result.json"
    assert app_module._V2_DATA_LIBRARY_PICKER_LOCK.acquire(blocking=False)
    app_module._V2_DATA_LIBRARY_PICKER_LOCK.release()


def test_native_picker_readiness_failure_is_clear_and_never_registers_data(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    client = authenticated_client()
    monkeypatch.setattr(app_module, "_v2_data_library_picker_command", fake_picker_command)
    monkeypatch.setattr(app_module.subprocess, "run", picker_result_process(json.dumps({
            "status": "error",
            "code": "picker_not_ready",
            "message": "internal native detail",
        }), returncode=1))
    response = client.post("/api/data-library/choose")

    assert response.status_code == 500
    assert response.get_json() == {
        "success": False,
        "code": "native_picker_not_ready",
        "error": "The data chooser could not become ready for input. No data sources were added.",
    }
    assert data_library.list_sources() == []


def test_native_picker_process_failure_is_not_reported_as_cancel(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    client = authenticated_client()
    monkeypatch.setattr(app_module, "_v2_data_library_picker_command", fake_picker_command)

    class Completed:
        returncode = 1
        stdout = ""
        stderr = "native failure"

    monkeypatch.setattr(app_module.subprocess, "run", lambda *args, **kwargs: Completed())
    response = client.post("/api/data-library/choose")

    assert response.status_code == 500
    assert response.get_json()["code"] == "native_picker_failed"
    assert response.get_json()["success"] is False
    assert data_library.list_sources() == []


def test_batch_registry_write_failure_leaves_previous_registry_unchanged(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    existing, _ = data_library.add_source(str(source_folder(tmp_path, "existing")))
    before = data_library.registry_path().read_bytes()
    additions = [source_folder(tmp_path, "one"), source_folder(tmp_path, "two")]
    monkeypatch.setattr(data_library, "_save_unlocked", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        data_library.add_sources([str(source) for source in additions])

    assert data_library.registry_path().read_bytes() == before
    assert data_library.load_registry()["datasets"][0]["id"] == existing["id"]


def test_concurrent_multi_folder_batches_remain_serialized_and_deduplicated(monkeypatch, tmp_path):
    configure_roots(monkeypatch, tmp_path)
    paths = [str(source_folder(tmp_path, "one")), str(source_folder(tmp_path, "two"))]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: data_library.add_sources(paths), range(16)))

    assert len(data_library.list_sources()) == 2
    assert sum(len(result["added"]) for result in results) == 2
    assert sum(len(result["skipped"]) for result in results) == 30
    assert all(not result["rejected"] for result in results)
