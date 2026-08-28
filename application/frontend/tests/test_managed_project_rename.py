"""Transactional managed-folder rename regressions for Edit Project."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import unicodedata
from pathlib import Path

import pytest

from da_selection_ui import app as app_module


def _client():
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
    return client


def _create(client, name: str, digit: str = "a") -> dict:
    project_id = "prj_" + digit * 32
    response = client.post("/api/projects", data={
        "project_id": project_id,
        "project_name": name,
        "task_description": "Investigate a synthetic mechanism.",
        "dataset_context": "Synthetic project context.",
    })
    assert response.status_code == 200, response.get_data(as_text=True)
    return app_module._v2_get_project(project_id)


def _edit_payload(record: dict, name: str) -> dict:
    settings = app_module._v2_project_configuration(record)
    return {
        "project_name": name,
        "task_description": app_module._v2_project_task_description(record),
        "dataset_context": record.get("dataset_context") or "",
        "project_parent_dir": str(Path(record["project_root"]).parent),
        "reasoning_model": settings["reasoning_model"],
        "execution_model": settings["execution_model"],
        "query_count": str(settings["query_count"]),
        "deep_research": str(settings["deep_research"]).lower(),
        "web_search": str(settings["web_search"]).lower(),
        "data_sources": json.dumps(record.get("data_sources") or []),
    }


def _edit(client, record: dict, name: str):
    return client.post(
        f"/api/projects/{record['id']}/edit",
        data=_edit_payload(record, name),
    )


def _file_hashes(root: Path, *, excluded: set[str] | None = None) -> dict[str, str]:
    excluded = excluded or set()
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.relative_to(root).as_posix() not in excluded
    }


def _populate_resolvable_artifacts(record: dict) -> dict[str, str]:
    root = Path(record["project_root"])
    round_dir = root / "checkpoint" / "checkpoints" / "round_0"
    query = round_dir / "query_001"
    query.mkdir()
    artifacts = {
        round_dir / "selected_hypothesis.md": "Synthetic selected hypothesis\n",
        round_dir / "round_report.md": "# Synthetic round report\n\n![Synthetic figure](figures/synthetic.png)\n",
        round_dir / "round_report_summary.md": "Synthetic round summary\n",
        round_dir / "memory_report.md": "# Synthetic memory report\n",
        round_dir / "memory_report_summary.md": "Synthetic memory summary\n",
        query / "query_001.md": "**Query**: Synthetic query input\n",
        query / "report.md": "Synthetic query report\n",
        query / "execution.log": "Synthetic execution result\n",
        root / "code" / "analysis.py": "print('synthetic execution result')\n",
        root / "code" / "data" / "synthetic.csv": "sample,value\nA,1\nB,2\n",
        root / "code" / "figures" / "synthetic.png": "synthetic figure bytes\n",
        root / "code" / ".claude" / "skills" / "synthetic-skill" / "SKILL.md": (
            "---\nname: Synthetic Skill\ndescription: Synthetic skill snapshot.\n---\n"
        ),
        root / "Audit" / "conversation.jsonl": '{"message":"Synthetic audit message"}\n',
    }
    for path, content in artifacts.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in artifacts
    }


def _assert_original_project_usable(record: dict, original_root: Path, marker: bytes, registry: bytes):
    assert original_root.is_dir()
    assert (original_root / ".discoveryagent" / "project.json").read_bytes() == marker
    assert app_module._v2_projects_registry_path().read_bytes() == registry
    restored = app_module._v2_get_project(record["id"])
    assert Path(restored["project_root"]) == original_root
    assert app_module._v2_project_availability(restored)["available"] is True


def test_managed_name_edit_renames_directory_and_rebases_every_local_surface(monkeypatch):
    client = _client()
    before = _create(client, "Managed Original", "1")
    old_root = Path(before["project_root"])
    old_inode = old_root.stat().st_ino
    url_slug = before["url_slug"]
    artifact_hashes = _populate_resolvable_artifacts(before)
    readme_before = Path(before["readme_path"]).read_bytes()
    config_before = Path(before["checkpoint_dir"], "config.json").read_bytes()
    audit_state = {
        "version": 2,
        "projectId": before["id"],
        "updatedAt": 42,
        "activeChatId": "synthetic-chat",
        "chats": [{
            "id": "synthetic-chat",
            "title": "Synthetic audit",
            "createdAt": 1,
            "updatedAt": 42,
            "messages": [
                {"role": "user", "text": "Synthetic audit message"},
                {"role": "assistant", "text": "Synthetic audit response"},
            ],
        }],
        "pendingJobs": [],
        "queuedMessages": {},
    }
    saved_audit = client.put(
        f"/api/conversation_state?project_id={before['id']}",
        json={"project_id": before["id"], "surface": "audit_agent", "scope": "main", "state": audit_state},
    )
    assert saved_audit.status_code == 200

    monkeypatch.setattr(
        app_module.shutil,
        "copytree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("rename must not copy")),
    )
    response = _edit(client, before, "Axon Regeneration Study")

    assert response.status_code == 200, response.get_data(as_text=True)
    after = app_module._v2_get_project(before["id"])
    new_root = Path(after["project_root"])
    assert new_root == old_root.parent / "axon-regeneration-study"
    assert response.get_json()["project_root"] == str(new_root)
    assert not old_root.exists()
    assert new_root.stat().st_ino == old_inode
    assert after["id"] == before["id"]
    assert after["url_slug"] == url_slug
    assert after["display_name"] == "Axon Regeneration Study"
    for field, relative in {
        "code_dir": "code",
        "data_dir": "code/data",
        "checkpoint_dir": "checkpoint/checkpoints/round_0",
        "readme_path": "code/data/README.md",
    }.items():
        assert Path(after[field]) == new_root / relative
    marker = json.loads((new_root / ".discoveryagent" / "project.json").read_text())
    assert marker == {
        "project_id": before["id"],
        "display_name": "Axon Regeneration Study",
    }
    assert {
        path: hashlib.sha256((new_root / path).read_bytes()).hexdigest()
        for path in artifact_hashes
    } == artifact_hashes
    assert Path(after["readme_path"]).read_bytes() == readme_before
    assert Path(after["checkpoint_dir"], "config.json").read_bytes() == config_before

    home = client.get(f"/projects/{url_slug}")
    assert home.status_code == 200
    assert client.get(f"/rounds/?project={url_slug}&round=0").status_code == 200
    assert client.get(f"/projects/{url_slug}/skills").status_code == 200
    assert client.get(f"/api/projects/{before['id']}/skills").status_code == 200
    workspace_tree = client.get("/api/round_workspace_tree")
    assert workspace_tree.status_code == 200
    assert workspace_tree.get_json()["code_tree"]["status"] == "present"

    for relative, expected in {
        "analysis.py": b"print('synthetic execution result')\n",
        "data/synthetic.csv": b"sample,value\nA,1\nB,2\n",
        "figures/synthetic.png": b"synthetic figure bytes\n",
    }.items():
        inline = client.get(
            "/api/round_workspace_code_artifact", query_string={"path": relative},
        )
        download = client.get(
            "/api/round_workspace_code_artifact",
            query_string={"path": relative, "download": "1"},
        )
        assert inline.status_code == download.status_code == 200
        assert inline.data == download.data == expected
        assert download.headers["Content-Disposition"].startswith("attachment;")

    report = client.get(f"/projects/{url_slug}/rounds/0/report/download")
    memory = client.get(f"/projects/{url_slug}/rounds/0/report/download?type=memory")
    assert report.status_code == 200 and b"Synthetic round report" in report.data
    assert memory.status_code == 200 and b"Synthetic memory report" in memory.data
    audit = client.get(
        f"/api/conversation_state?project_id={before['id']}&surface=audit_agent&scope=main"
    )
    assert audit.status_code == 200
    assert audit.get_json()["state"]["chats"][0]["messages"][0]["text"] == "Synthetic audit message"

    restarted = _client()
    assert restarted.get(f"/projects/{url_slug}").status_code == 200
    detail = restarted.get(f"/api/projects/{before['id']}").get_json()["project"]
    assert detail["display_name"] == "Axon Regeneration Study"
    assert detail["project_location"]["root"] == str(new_root)


def test_name_normalizing_to_current_basename_updates_metadata_without_directory_move(monkeypatch):
    client = _client()
    before = _create(client, "Alpha Study", "2")
    root = Path(before["project_root"])
    inode = root.stat().st_ino
    monkeypatch.setattr(
        app_module,
        "_v2_prepare_project_relocation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no rename expected")),
    )

    response = _edit(client, before, "ALPHA__STUDY")

    assert response.status_code == 200
    after = app_module._v2_get_project(before["id"])
    assert Path(after["project_root"]) == root
    assert root.stat().st_ino == inode
    assert after["display_name"] == "ALPHA__STUDY"
    marker = json.loads((root / ".discoveryagent" / "project.json").read_text())
    assert marker["project_id"] == before["id"]
    assert marker["display_name"] == "ALPHA__STUDY"


@pytest.mark.parametrize(("legacy_leaf", "new_name", "expected_leaf"), [
    ("Case-Study", "CASE STUDY", "case-study"),
    (unicodedata.normalize("NFD", "Étude"), "Étude", "étude"),
])
def test_case_and_unicode_equivalent_managed_renames_are_safe(
    legacy_leaf, new_name, expected_leaf,
):
    client = _client()
    before = _create(client, "Original", "3")
    original_root = Path(before["project_root"])
    legacy_root = original_root.parent / legacy_leaf
    os.replace(original_root, legacy_root)
    with app_module._V2_PROJECTS_LOCK:
        projects = app_module._v2_load_projects()
        projects[0] = app_module._v2_rebased_project_record(
            projects[0], original_root, legacy_root,
        )
        app_module._v2_save_projects(projects)
    before = app_module._v2_get_project(before["id"])
    inode = legacy_root.stat().st_ino

    response = _edit(client, before, new_name)

    assert response.status_code == 200, response.get_data(as_text=True)
    after = app_module._v2_get_project(before["id"])
    destination = Path(after["project_root"])
    assert destination.name == expected_leaf
    assert destination.stat().st_ino == inode
    assert not any(".renaming-" in child.name for child in destination.parent.iterdir())


def test_destination_collision_is_rejected_without_suffix_or_mutation():
    client = _client()
    before = _create(client, "Collision Source", "4")
    root = Path(before["project_root"])
    marker_before = (root / ".discoveryagent" / "project.json").read_bytes()
    registry_before = app_module._v2_projects_registry_path().read_bytes()
    occupied = root.parent / "occupied-target"
    occupied.mkdir()
    sentinel = occupied / "sentinel.bin"
    sentinel.write_bytes(b"occupied")

    response = _edit(client, before, "Occupied Target")

    assert response.status_code == 409
    assert response.get_json()["code"] == "duplicate_project_slug"
    _assert_original_project_usable(before, root, marker_before, registry_before)
    assert sentinel.read_bytes() == b"occupied"
    assert not list(root.parent.glob("occupied-target--*"))


def test_running_round_blocks_managed_folder_rename(monkeypatch):
    client = _client()
    before = _create(client, "Running Source", "5")
    root = Path(before["project_root"])
    registry_before = app_module._v2_projects_registry_path().read_bytes()
    marker_before = (root / ".discoveryagent" / "project.json").read_bytes()
    monkeypatch.setattr(app_module, "_v2_project_is_running", lambda _record: True)

    response = _edit(client, before, "Blocked Rename")

    assert response.status_code == 409
    assert response.get_json()["code"] == "project_location_locked_while_running"
    _assert_original_project_usable(before, root, marker_before, registry_before)


def test_unsafe_symlink_root_is_rejected_before_rename():
    client = _client()
    before = _create(client, "Unsafe Source", "6")
    registered_root = Path(before["project_root"])
    real_root = registered_root.parent / "real-project"
    os.replace(registered_root, real_root)
    registered_root.symlink_to(real_root, target_is_directory=True)
    tree_before = _file_hashes(real_root)

    response = _edit(client, before, "Unsafe Rename")

    assert response.status_code == 409
    assert response.get_json()["code"] == "project_location_recovery_required"
    assert registered_root.is_symlink()
    assert _file_hashes(real_root) == tree_before


@pytest.mark.parametrize("failure", [PermissionError("permission denied"), OSError("rename failed")])
def test_filesystem_rename_failures_leave_original_project_usable(monkeypatch, failure):
    client = _client()
    before = _create(client, "Filesystem Source", "7")
    root = Path(before["project_root"])
    marker_before = (root / ".discoveryagent" / "project.json").read_bytes()
    registry_before = app_module._v2_projects_registry_path().read_bytes()
    monkeypatch.setattr(
        app_module,
        "_v2_prepare_project_relocation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )

    response = _edit(client, before, "Filesystem Destination")

    assert response.status_code == 500
    _assert_original_project_usable(before, root, marker_before, registry_before)
    assert not (root.parent / "filesystem-destination").exists()


def test_managed_root_outside_canonical_parent_is_rejected_without_mutation():
    client = _client()
    before = _create(client, "Unsafe Parent Source", "e")
    original_root = Path(before["project_root"])
    outside_root = app_module._v2_data_root() / "synthetic-outside" / original_root.name
    outside_root.parent.mkdir()
    os.replace(original_root, outside_root)
    with app_module._V2_PROJECTS_LOCK:
        projects = app_module._v2_load_projects()
        projects[0] = app_module._v2_rebased_project_record(
            projects[0], original_root, outside_root,
        )
        app_module._v2_save_projects(projects)
    before = app_module._v2_get_project(before["id"])
    marker_before = (outside_root / ".discoveryagent" / "project.json").read_bytes()
    registry_before = app_module._v2_projects_registry_path().read_bytes()

    response = _edit(client, before, "Unsafe Parent Destination")

    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_project_settings"
    _assert_original_project_usable(before, outside_root, marker_before, registry_before)
    assert not (outside_root.parent / "unsafe-parent-destination").exists()


def test_marker_failure_after_rename_rolls_back_folder_marker_and_registry(monkeypatch):
    client = _client()
    before = _create(client, "Marker Source", "8")
    root = Path(before["project_root"])
    marker_before = (root / ".discoveryagent" / "project.json").read_bytes()
    registry_before = app_module._v2_projects_registry_path().read_bytes()
    original_write = app_module.write_project_metadata

    def fail_after_marker_write(project_root, project_id, display_name):
        original_write(project_root, project_id, display_name)
        raise OSError("marker publication failed")

    monkeypatch.setattr(app_module, "write_project_metadata", fail_after_marker_write)
    response = _edit(client, before, "Marker Destination")

    assert response.status_code == 500
    _assert_original_project_usable(before, root, marker_before, registry_before)
    assert not (root.parent / "marker-destination").exists()


def test_registry_failure_after_publication_rolls_back_exact_state(monkeypatch):
    client = _client()
    before = _create(client, "Registry Source", "9")
    root = Path(before["project_root"])
    tree_before = _file_hashes(root)
    marker_before = (root / ".discoveryagent" / "project.json").read_bytes()
    registry_before = app_module._v2_projects_registry_path().read_bytes()
    original_save = app_module._v2_save_projects

    def publish_then_fail(projects):
        original_save(projects)
        if any(Path(item.get("project_root") or "").name == "registry-destination" for item in projects):
            raise OSError("registry fsync failed")

    monkeypatch.setattr(app_module, "_v2_save_projects", publish_then_fail)
    response = _edit(client, before, "Registry Destination")

    assert response.status_code == 500
    _assert_original_project_usable(before, root, marker_before, registry_before)
    assert _file_hashes(root) == tree_before
    assert not (root.parent / "registry-destination").exists()


def test_post_rename_validation_failure_rolls_back_everything(monkeypatch):
    client = _client()
    before = _create(client, "Validation Source", "b")
    root = Path(before["project_root"])
    marker_before = (root / ".discoveryagent" / "project.json").read_bytes()
    registry_before = app_module._v2_projects_registry_path().read_bytes()
    original_availability = app_module._v2_project_availability

    def fail_destination(record):
        if Path(record.get("project_root") or "").name == "validation-destination":
            return {"available": False, "message": "synthetic validation failure"}
        return original_availability(record)

    monkeypatch.setattr(app_module, "_v2_project_availability", fail_destination)
    response = _edit(client, before, "Validation Destination")

    assert response.status_code == 500
    _assert_original_project_usable(before, root, marker_before, registry_before)
    assert not (root.parent / "validation-destination").exists()


def test_rollback_failure_is_reported_explicitly(monkeypatch):
    client = _client()
    before = _create(client, "Rollback Source", "f")
    original_write = app_module.write_project_metadata

    def fail_after_marker_write(project_root, project_id, display_name):
        original_write(project_root, project_id, display_name)
        raise OSError("marker publication failed")

    monkeypatch.setattr(app_module, "write_project_metadata", fail_after_marker_write)
    monkeypatch.setattr(
        app_module,
        "_v2_rollback_project_relocation",
        lambda _transaction: (_ for _ in ()).throw(OSError("rollback denied")),
    )

    response = _edit(client, before, "Rollback Destination")

    assert response.status_code == 500
    payload = response.get_json()
    assert payload["code"] == "project_edit_rollback_failed"
    assert "folder rollback failed: rollback denied" in payload["error"]


def test_concurrent_managed_renames_are_serialized(monkeypatch):
    first_client = _client()
    before = _create(first_client, "Concurrent Source", "c")
    root = Path(before["project_root"])
    inode = root.stat().st_ino
    entered = threading.Event()
    release = threading.Event()
    original_prepare = app_module._v2_prepare_project_relocation
    calls = 0

    def controlled_prepare(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(timeout=5)
        return original_prepare(*args, **kwargs)

    monkeypatch.setattr(app_module, "_v2_prepare_project_relocation", controlled_prepare)
    results = {}

    def rename(label, name):
        results[label] = _edit(_client(), before, name)

    first = threading.Thread(target=rename, args=("first", "Concurrent One"))
    second = threading.Thread(target=rename, args=("second", "Concurrent Two"))
    first.start()
    assert entered.wait(timeout=5)
    second.start()
    release.set()
    first.join(timeout=10)
    second.join(timeout=10)

    assert results["first"].status_code == 200
    assert results["second"].status_code == 200
    after = app_module._v2_get_project(before["id"])
    destination = Path(after["project_root"])
    assert after["display_name"] == "Concurrent Two"
    assert destination.name == "concurrent-two"
    assert destination.stat().st_ino == inode
    assert len(app_module._v2_load_projects()) == 1
    assert not any(".renaming-" in child.name for child in destination.parent.iterdir())


def test_imported_display_name_edit_is_registry_only_and_preserves_external_tree():
    client = _client()
    project_id = "prj_" + "d" * 32
    external = app_module._v2_data_root() / "external" / "user-owned-folder"
    data = external / "code" / "data"
    round_zero = external / "checkpoint" / "checkpoints" / "round_0"
    data.mkdir(parents=True)
    round_zero.mkdir(parents=True)
    readme = data / "README.md"
    readme.write_text("# Imported Original\n\nSynthetic project context.\n", encoding="utf-8")
    config = round_zero / "config.json"
    config.write_text('{"config":{"working_directory":"synthetic"}}', encoding="utf-8")
    app_module.write_project_metadata(external, project_id, "Imported Original")
    record = {
        "id": project_id,
        "url_slug": "imported-original",
        "display_name": "Imported Original",
        "task_description": "Investigate a synthetic mechanism.",
        "dataset_context": "Synthetic project context.",
        "project_root": str(external),
        "project_parent_dir": str(external.parent),
        "code_dir": str(external / "code"),
        "data_dir": str(data),
        "checkpoint_dir": str(round_zero),
        "readme_path": str(readme),
        "data_sources": [],
        "imported_in_place": True,
    }
    app_module._v2_save_projects([record])
    before_files = _file_hashes(external)
    root_stat = external.stat()
    marker = external / ".discoveryagent" / "project.json"
    marker_stat = marker.stat()
    marker_bytes = marker.read_bytes()

    response = _edit(client, record, "Imported Display Rename")

    assert response.status_code == 200, response.get_data(as_text=True)
    after = app_module._v2_get_project(project_id)
    assert after["display_name"] == "Imported Display Rename"
    assert after["project_root"] == str(external)
    assert after["url_slug"] == "imported-original"
    assert after["id"] == project_id
    assert after["imported_in_place"] is True
    assert external.stat().st_ino == root_stat.st_ino
    assert marker.stat().st_ino == marker_stat.st_ino
    assert marker.read_bytes() == marker_bytes
    assert _file_hashes(external) == before_files
