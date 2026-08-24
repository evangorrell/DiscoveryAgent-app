"""Missing/moved V2 project-location recovery and fail-closed routing."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from da_selection_ui import app as app_module


def _client():
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
    return client


def _create(client, project_id: str = "prj_" + "9" * 32) -> tuple[dict, Path]:
    response = client.post("/api/projects", data={
        "project_id": project_id,
        "project_name": "Moved project",
        "task_description": "Investigate a recoverable project.",
        "dataset_context": "Small disposable fixture.",
    })
    assert response.status_code == 200, response.get_data(as_text=True)
    record = app_module._v2_get_project(project_id)
    assert record is not None
    return record, Path(record["project_root"])


def _move(root: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    root.rename(destination)
    return destination


def _picker(monkeypatch, selected: Path | None):
    monkeypatch.setattr(app_module, "_v2_native_project_recovery_picker_command", lambda: ["picker"])
    monkeypatch.setattr(
        app_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0 if selected is not None else 1,
            stdout=(str(selected) + "\n") if selected is not None else "",
            stderr="",
        ),
    )


def test_missing_project_stays_listed_and_continue_requires_recovery(tmp_path):
    client = _client()
    record, root = _create(client)
    moved = _move(root, tmp_path / "elsewhere" / "moved-project")

    listing = client.get("/api/projects").get_json()["projects"]
    summary = next(item for item in listing if item["id"] == record["id"])
    assert summary["location_available"] is False
    assert summary["registered_path"] == str(root)

    response = client.post(f"/api/projects/{record['id']}/continue")
    assert response.status_code == 409
    assert response.get_json()["code"] == "project_location_unavailable"
    assert moved.is_dir()
    assert not root.exists()
    assert app_module._v2_get_project(record["id"]) is not None


def test_original_registered_path_is_revalidated_on_every_continue(tmp_path):
    client = _client()
    record, root = _create(client)
    moved = _move(root, tmp_path / "temporary" / root.name)
    assert client.post(f"/api/projects/{record['id']}/continue").status_code == 409

    moved.rename(root)
    response = client.post(f"/api/projects/{record['id']}/continue")
    assert response.status_code == 200
    assert response.get_json()["destination"].endswith(record["url_slug"])


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/projects/{slug}"),
        ("get", "/projects/{slug}/hypotheses"),
        ("get", "/projects/{slug}/skills"),
        ("get", "/rounds/?project={slug}"),
        ("post", "/api/rounds/start"),
        ("post", "/api/rounds/resume"),
        ("post", "/api/rounds/resubmit"),
        ("get", "/api/projects/{id}/skills"),
        ("get", "/api/round_workspace_artifact?round=0&path=round_report.md"),
        ("get", "/api/round_workspace_code_artifact?path=analysis.py"),
    ],
)
def test_unavailable_project_entry_and_operations_fail_closed(tmp_path, method, path):
    client = _client()
    record, root = _create(client)
    _move(root, tmp_path / "moved" / root.name)
    target = path.format(slug=record["url_slug"], id=record["id"])

    response = getattr(client, method)(target)
    if target.startswith("/projects/") or target.startswith("/rounds/"):
        assert response.status_code == 302
        assert f"recover={record['id']}" in response.headers["Location"]
    else:
        assert response.status_code == 409
        assert response.get_json()["code"] == "project_location_unavailable"


def test_locate_rebases_registry_without_moving_files_or_rewriting_history(monkeypatch, tmp_path):
    client = _client()
    record, root = _create(client)
    round_config = Path(record["checkpoint_dir"]) / "config.json"
    historical = b'{"config":{"scientific_provenance":"unchanged"}}\n'
    round_config.write_bytes(historical)
    marker = root / "code" / "analysis.py"
    marker.write_text("print('still here')\n", encoding="utf-8")
    moved = _move(root, tmp_path / "new parent" / "moved-project")
    _picker(monkeypatch, moved)

    response = client.post(f"/api/projects/{record['id']}/locate")

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["root"] == str(moved)
    assert payload["files_moved"] is False
    recovered = app_module._v2_get_project(record["id"])
    assert Path(recovered["project_root"]) == moved
    assert Path(recovered["code_dir"]) == moved / "code"
    assert Path(recovered["checkpoint_dir"]) == moved / "checkpoint" / "checkpoints" / "round_0"
    assert (moved / "code" / "analysis.py").read_text(encoding="utf-8") == "print('still here')\n"
    assert (moved / "checkpoint" / "checkpoints" / "round_0" / "config.json").read_bytes() == historical
    assert not root.exists()
    assert app_module._v2_project_destination(
        moved.parent, record["display_name"], current_root=moved,
    ) == moved
    assert app_module._v2_project_delete_root(recovered, managed=True) == moved
    runtime_paths = app_module.local_round_runner._project_paths(recovered)
    assert runtime_paths["project_root"] == moved
    assert runtime_paths["code_dir"] == moved / "code"
    resume_config = moved / "checkpoint" / "checkpoints" / "round_0" / "resume-config.json"
    resume_config.write_text(json.dumps({"config": {
        "working_directory": str(root / "code"),
        "existing_codebase": str(root / "code"),
    }}), encoding="utf-8")
    app_module.local_round_runner._refresh_resume_project_skills(recovered, resume_config)
    resumed = json.loads(resume_config.read_text(encoding="utf-8"))["config"]
    assert resumed["working_directory"] == str(moved / "code")
    assert resumed["existing_codebase"] == str(moved / "code")
    assert client.post(f"/api/projects/{record['id']}/continue").status_code == 200


def test_locate_cancel_and_identity_validation_leave_registry_unchanged(monkeypatch, tmp_path):
    client = _client()
    record, root = _create(client)
    moved = _move(root, tmp_path / "moved" / "correct")

    _picker(monkeypatch, None)
    cancelled = client.post(f"/api/projects/{record['id']}/locate")
    assert cancelled.get_json() == {"success": True, "cancelled": True}
    assert Path(app_module._v2_get_project(record["id"])["project_root"]) == root

    wrong = tmp_path / "wrong-project"
    wrong.mkdir()
    app_module.write_project_metadata(wrong, "prj_" + "8" * 32, "Wrong")
    _picker(monkeypatch, wrong)
    rejected = client.post(f"/api/projects/{record['id']}/locate")
    assert rejected.status_code == 400
    assert "different" in rejected.get_json()["error"]

    missing_metadata = tmp_path / "missing-metadata"
    missing_metadata.mkdir()
    _picker(monkeypatch, missing_metadata)
    rejected = client.post(f"/api/projects/{record['id']}/locate")
    assert rejected.status_code == 400
    assert rejected.get_json()["error"] == (
        "The selected folder is not a DiscoveryAgent project. "
        "Select the project folder itself, not its parent."
    )
    assert moved.is_dir()
    assert Path(app_module._v2_get_project(record["id"])["project_root"]) == root


def test_recovery_rejects_symlink_protected_overlapping_and_data_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module.data_library, "get_data_root", lambda: tmp_path / "library-state")
    monkeypatch.setattr(app_module.data_library, "get_projects_root", lambda: tmp_path / "project-guard")
    client = _client()
    record, root = _create(client)
    moved = _move(root, tmp_path / "moved" / "real")
    alias = tmp_path / "alias"
    alias.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        app_module._v2_validate_project_recovery_root(alias, record["id"], [record])
    with pytest.raises(ValueError, match="protected"):
        app_module._v2_validate_project_recovery_root(Path.home(), record["id"], [record])

    other_id = "prj_" + "7" * 32
    other_root = moved.parent / "other"
    other_root.mkdir()
    app_module.write_project_metadata(other_root, other_id, "Other")
    nested = other_root / "nested"
    nested.mkdir()
    app_module.write_project_metadata(nested, record["id"], "Moved")
    with pytest.raises(ValueError, match="overlaps another"):
        app_module._v2_validate_project_recovery_root(
            nested, record["id"], [record, {"id": other_id, "project_root": str(other_root)}],
        )

    source, _created = app_module.data_library.add_source(str(moved))
    assert source["id"]
    with pytest.raises(ValueError, match="Data source"):
        app_module._v2_validate_project_recovery_root(moved, record["id"], [record])


def test_edit_is_registry_backed_and_save_requires_recovery(tmp_path):
    client = _client()
    record, root = _create(client)
    moved = _move(root, tmp_path / "moved" / root.name)

    detail = client.get(f"/api/projects/{record['id']}?selection_only=1")
    assert detail.status_code == 200
    project = detail.get_json()["project"]
    assert project["display_name"] == record["display_name"]
    assert project["task_description"] == record["task_description"]
    assert project["project_location"]["root"] == str(root)
    assert project["project_location"]["available"] is False

    response = client.post(f"/api/projects/{record['id']}/edit", data={
        "project_name": "Must remain pending",
        "task_description": "Do not partially save.",
        "dataset_context": "Do not write.",
        "project_parent_dir": str(root.parent),
    })
    assert response.status_code == 409
    assert response.get_json()["code"] == "project_location_recovery_required"
    assert app_module._v2_get_project(record["id"])["display_name"] == record["display_name"]
    assert moved.is_dir()


def test_unavailable_delete_requires_explicit_registry_only_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module.data_library, "get_data_root", lambda: tmp_path / "library-state")
    monkeypatch.setattr(app_module.data_library, "get_projects_root", lambda: tmp_path / "project-guard")
    client = _client()
    record, root = _create(client)
    moved = _move(root, tmp_path / "unknown location" / root.name)
    data_root = tmp_path / "scientist-data"
    data_root.mkdir()
    source_file = data_root / "input.csv"
    source_file.write_text("a,b\n1,2\n", encoding="utf-8")
    source, _created = app_module.data_library.add_source(str(data_root))
    trash_calls = []
    monkeypatch.setattr(app_module, "_v2_move_to_trash", lambda target: trash_calls.append(Path(target)))

    first = client.delete(f"/api/projects/{record['id']}")
    assert first.status_code == 409
    assert first.get_json()["code"] == "project_unavailable_delete_confirmation_required"
    assert app_module._v2_get_project(record["id"]) is not None

    confirmed = client.delete(f"/api/projects/{record['id']}?confirm_unavailable=true")
    assert confirmed.status_code == 200
    assert confirmed.get_json()["removal_mode"] == "registry_only"
    assert confirmed.get_json()["files_untouched"] is True
    assert app_module._v2_get_project(record["id"]) is None
    assert moved.is_dir() and trash_calls == []
    assert source_file.read_text(encoding="utf-8") == "a,b\n1,2\n"
    assert [item["id"] for item in app_module.data_library.list_sources()] == [source["id"]]


def test_confirmed_unavailable_delete_revalidates_and_uses_trash_if_root_returns(monkeypatch, tmp_path):
    client = _client()
    record, root = _create(client)
    moved = _move(root, tmp_path / "temporary" / root.name)
    assert client.delete(f"/api/projects/{record['id']}").status_code == 409
    moved.rename(root)
    calls = []
    monkeypatch.setattr(app_module, "_v2_move_to_trash", lambda target: calls.append(Path(target)))

    response = client.delete(f"/api/projects/{record['id']}?confirm_unavailable=true")
    assert response.status_code == 200
    assert response.get_json()["removal_mode"] == "trash"
    assert calls == [root]


def test_local_runner_never_recreates_an_unavailable_project_root(tmp_path):
    missing = tmp_path / "missing-project"
    project = {
        "id": "prj_" + "6" * 32,
        "project_root": str(missing),
        "code_dir": str(missing / "code"),
        "checkpoint_dir": str(missing / "checkpoint" / "checkpoints" / "round_0"),
    }
    with pytest.raises(app_module.local_round_runner.RoundLaunchError) as raised:
        app_module.local_round_runner._project_paths(project)
    assert raised.value.code == "project_location_unavailable"
    assert not missing.exists()


def test_locate_and_delete_reject_a_tracked_active_round(monkeypatch, tmp_path):
    client = _client()
    record, root = _create(client)
    moved = _move(root, tmp_path / "moved-active" / root.name)
    _picker(monkeypatch, moved)

    class RunningProcess:
        def poll(self):
            return None

    key = str(root.absolute())
    app_module.local_round_runner._PROCESSES[key] = RunningProcess()
    try:
        located = client.post(f"/api/projects/{record['id']}/locate")
        deleted = client.delete(f"/api/projects/{record['id']}?confirm_unavailable=true")
    finally:
        app_module.local_round_runner._PROCESSES.pop(key, None)

    assert located.status_code == 409
    assert located.get_json()["code"] == "project_location_locked_while_running"
    assert deleted.status_code == 409
    assert deleted.get_json()["code"] == "project_delete_locked_while_running"
    assert Path(app_module._v2_get_project(record["id"])["project_root"]) == root
    assert moved.is_dir()
