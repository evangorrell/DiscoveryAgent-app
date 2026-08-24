"""Availability contracts for exact-root registered legacy projects."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import threading
from pathlib import Path

import pytest

from da_selection_ui import app as app_module


def _client():
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
    return client


def _legacy_fixture(
    monkeypatch,
    tmp_path: Path,
    *,
    project_id: str = "legacy-project-id",
    root: Path | None = None,
    create_root: bool = True,
) -> tuple[dict, Path, Path]:
    legacy_disk = tmp_path / "legacy" / "local_disk"
    legacy_disk.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: legacy_disk)
    project_root = root or (legacy_disk / project_id)
    if create_root:
        (project_root / "code" / "data").mkdir(parents=True, exist_ok=True)
        (project_root / "checkpoint" / "checkpoints" / "round_0").mkdir(
            parents=True, exist_ok=True,
        )
        (project_root / ".discoveryagent" / "interactions").mkdir(
            parents=True, exist_ok=True,
        )
        (project_root / "code" / "data" / "README.md").write_text(
            "# Disposable legacy project\n", encoding="utf-8",
        )
    record = {
        "id": project_id,
        "url_slug": "registered-legacy-project",
        "display_name": "Registered Legacy Project",
        "project_root": str(project_root),
        "project_parent_dir": str(project_root.parent),
        "code_dir": str(project_root / "code"),
        "data_dir": str(project_root / "code" / "data"),
        "checkpoint_dir": str(
            project_root / "checkpoint" / "checkpoints" / "round_0"
        ),
        "readme_path": str(project_root / "code" / "data" / "README.md"),
        "task_description": "Exercise legacy availability safely.",
        "dataset_context": "Disposable fixture only.",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    registry = legacy_disk / "v2_projects.json"
    registry.write_text(
        json.dumps({"projects": [record]}, indent=2), encoding="utf-8",
    )
    return record, project_root, registry


def test_exact_registered_legacy_root_without_v2_marker_is_available_and_read_only(
    monkeypatch, tmp_path,
):
    record, root, registry = _legacy_fixture(monkeypatch, tmp_path)
    marker = root / ".discoveryagent" / "project.json"
    before = registry.read_bytes()
    before_mtime = registry.stat().st_mtime_ns

    availability = app_module._v2_project_availability(record)
    listing = _client().get("/api/projects").get_json()["projects"]
    summary = next(item for item in listing if item["id"] == record["id"])

    assert availability == {
        "available": True,
        "registered_path": str(root),
        "root": str(root),
        "message": None,
    }
    assert summary["location_available"] is True
    assert (root / ".discoveryagent" / "interactions").is_dir()
    assert not marker.exists()
    assert registry.read_bytes() == before
    assert registry.stat().st_mtime_ns == before_mtime


def test_legacy_continue_and_direct_routes_do_not_create_marker_or_rewrite_registry(
    monkeypatch, tmp_path,
):
    record, root, registry = _legacy_fixture(monkeypatch, tmp_path)
    round_dir = root / "checkpoint" / "checkpoints" / "round_0"
    (round_dir / "round_report.md").write_text("# Legacy report\n", encoding="utf-8")
    (root / "code" / "analysis.py").write_text("print('legacy')\n", encoding="utf-8")
    before = registry.read_bytes()
    marker = root / ".discoveryagent" / "project.json"

    def unexpected_marker(*_args, **_kwargs):
        raise AssertionError("read-only legacy access attempted to create identity metadata")

    monkeypatch.setattr(app_module, "write_project_metadata", unexpected_marker)
    client = _client()
    continued = client.post(f"/api/projects/{record['id']}/continue")
    assert continued.status_code == 200
    assert "recover=" not in continued.get_json()["destination"]

    for route in (
        f"/projects/{record['url_slug']}",
        f"/projects/{record['url_slug']}/hypotheses",
        f"/projects/{record['url_slug']}/skills",
        f"/rounds/?project={record['url_slug']}",
    ):
        response = client.get(route)
        assert response.status_code == 200, (route, response.status_code)

    skills = client.get(f"/api/projects/{record['id']}/skills")
    code = client.get("/api/round_workspace_code_artifact?path=analysis.py")
    artifact = client.get(
        "/api/round_workspace_artifact?round=0&path=round_report.md"
    )
    assert skills.status_code == 200
    assert code.status_code == 200
    assert code.get_data(as_text=True) == "print('legacy')\n"
    assert artifact.status_code == 200
    assert artifact.get_data(as_text=True) == "# Legacy report\n"
    assert registry.read_bytes() == before
    assert not marker.exists()


def test_runner_resolves_existing_legacy_root_without_recreating_or_marking_it(
    monkeypatch, tmp_path,
):
    record, root, _registry = _legacy_fixture(monkeypatch, tmp_path)
    root_identity = root.stat(follow_symlinks=False)
    readme_before = (root / "code" / "data" / "README.md").read_bytes()

    paths = app_module.local_round_runner._project_paths(record)

    after_identity = root.stat(follow_symlinks=False)
    assert paths["project_root"] == root
    assert paths["code_dir"] == root / "code"
    assert after_identity.st_dev == root_identity.st_dev
    assert after_identity.st_ino == root_identity.st_ino
    assert (root / "code" / "data" / "README.md").read_bytes() == readme_before
    assert not (root / ".discoveryagent" / "project.json").exists()


def test_managed_project_still_requires_matching_v2_identity(monkeypatch, tmp_path):
    client = _client()
    response = client.post("/api/projects", data={
        "project_id": "prj_" + "4" * 32,
        "project_name": "Managed identity",
        "task_description": "Keep managed validation strict.",
        "dataset_context": "Disposable managed fixture.",
    })
    assert response.status_code == 200
    record = app_module._v2_get_project(response.get_json()["project_id"])
    root = Path(record["project_root"])
    marker = root / ".discoveryagent" / "project.json"
    assert app_module._v2_project_availability(record)["available"] is True

    with monkeypatch.context() as scoped:
        scoped.setattr(app_module, "_v2_delete_protected_roots", lambda: (root,))
        protected = app_module._v2_project_availability(record)
    assert protected["available"] is False
    assert "protected" in protected["message"]

    marker.unlink()
    missing = app_module._v2_project_availability(record)
    assert missing["available"] is False
    assert "metadata" in missing["message"]

    app_module.write_project_metadata(root, "prj_" + "5" * 32, "Wrong identity")
    conflicting = app_module._v2_project_availability(record)
    assert conflicting["available"] is False
    assert "different identity" in conflicting["message"]


def test_unmarked_legacy_symlink_outside_root_and_missing_root_fail_closed(
    monkeypatch, tmp_path,
):
    record, root, registry = _legacy_fixture(monkeypatch, tmp_path)
    real = root.with_name("real-project")
    root.rename(real)
    root.symlink_to(real, target_is_directory=True)
    assert app_module._v2_project_availability(record)["available"] is False

    root.unlink()
    outside = tmp_path / "outside" / record["id"]
    (outside / "code" / "data").mkdir(parents=True)
    (outside / "checkpoint" / "checkpoints" / "round_0").mkdir(parents=True)
    (outside / "code" / "data" / "README.md").write_text("# Outside\n")
    record.update({
        "project_root": str(outside),
        "code_dir": str(outside / "code"),
        "data_dir": str(outside / "code" / "data"),
        "checkpoint_dir": str(outside / "checkpoint" / "checkpoints" / "round_0"),
        "readme_path": str(outside / "code" / "data" / "README.md"),
    })
    registry.write_text(json.dumps({"projects": [record]}), encoding="utf-8")
    outside_result = app_module._v2_project_availability(record)
    assert outside_result["available"] is False
    assert "outside" in outside_result["message"]

    missing = tmp_path / "legacy" / "local_disk" / "missing-project"
    record.update({"project_root": str(missing)})
    registry.write_text(json.dumps({"projects": [record]}), encoding="utf-8")
    missing_result = app_module._v2_project_availability(record)
    assert missing_result["available"] is False
    assert "could not find" in missing_result["message"]


def test_conflicting_or_unsafe_marker_on_legacy_root_fails_closed(monkeypatch, tmp_path):
    record, root, _registry = _legacy_fixture(monkeypatch, tmp_path)
    app_module.write_project_metadata(root, record["id"], "Matching")
    assert app_module._v2_project_availability(record)["available"] is True

    app_module.write_project_metadata(root, "another-project", "Conflicting")
    conflicting = app_module._v2_project_availability(record)
    assert conflicting["available"] is False
    assert "different identity" in conflicting["message"]

    marker = root / ".discoveryagent" / "project.json"
    marker.unlink()
    marker.symlink_to(root / "missing-marker-target")
    unsafe = app_module._v2_project_availability(record)
    assert unsafe["available"] is False
    assert "unsafe" in unsafe["message"]


def test_moved_unmarked_legacy_folder_is_not_accepted_by_recovery(monkeypatch, tmp_path):
    record, root, _registry = _legacy_fixture(monkeypatch, tmp_path)
    moved = tmp_path / "moved" / root.name
    moved.parent.mkdir()
    root.rename(moved)

    with pytest.raises(app_module._V2ProjectMarkerError) as error:
        app_module._v2_validate_project_recovery_root(
            moved, record["id"], [record],
        )
    assert error.value.code == "project_marker_missing"
    assert str(error.value) == (
        "The selected folder is not a DiscoveryAgent project. "
        "Select the project folder itself, not its parent."
    )


def _edit_payload(record: dict, destination_parent: Path) -> dict[str, str]:
    return {
        "project_name": str(record["display_name"]),
        "task_description": str(record["task_description"]),
        "dataset_context": str(record["dataset_context"]),
        "project_parent_dir": str(destination_parent),
        "confirm_project_relocation": "true",
    }


def test_exact_custom_legacy_root_backfills_marker_atomically_without_touching_project_data(
    monkeypatch, tmp_path,
):
    custom_root = tmp_path / "Desktop" / "optic-nerve-regeneration"
    record, root, registry = _legacy_fixture(
        monkeypatch,
        tmp_path,
        project_id="legacy-optic-fixture-123456",
        root=custom_root,
    )
    record["display_name"] = "Optic Nerve Regeneration"
    registry.write_text(json.dumps({"projects": [record]}, indent=2), encoding="utf-8")
    conversation = root / ".discoveryagent" / "interactions" / "conversation_state.json"
    audit = root / ".discoveryagent" / "interactions" / "audit_interactions.jsonl"
    conversation.write_bytes(b'{"conversation":"unchanged"}\n')
    audit.write_bytes(b'{"event":"unchanged"}\n')
    registry_before = registry.read_bytes()
    conversation_before = conversation.read_bytes()
    audit_before = audit.read_bytes()

    created = app_module._v2_backfill_exact_registered_legacy_marker(record, root)

    marker = root / ".discoveryagent" / "project.json"
    assert created is True
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "project_id": record["id"],
        "display_name": record["display_name"],
    }
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600
    assert registry.read_bytes() == registry_before
    assert conversation.read_bytes() == conversation_before
    assert audit.read_bytes() == audit_before
    assert app_module._v2_project_availability(record)["available"] is True


def test_legacy_marker_backfill_is_idempotent_and_never_rewrites_valid_marker(
    monkeypatch, tmp_path,
):
    record, root, _registry = _legacy_fixture(monkeypatch, tmp_path)
    assert app_module._v2_backfill_exact_registered_legacy_marker(record, root) is True
    marker = root / ".discoveryagent" / "project.json"
    first_bytes = marker.read_bytes()
    first_stat = marker.stat()

    assert app_module._v2_backfill_exact_registered_legacy_marker(record, root) is False
    second_stat = marker.stat()
    assert marker.read_bytes() == first_bytes
    assert second_stat.st_ino == first_stat.st_ino
    assert second_stat.st_mtime_ns == first_stat.st_mtime_ns


def test_concurrent_legacy_marker_backfill_publishes_one_complete_marker(
    monkeypatch, tmp_path,
):
    record, root, _registry = _legacy_fixture(monkeypatch, tmp_path)
    results: list[bool] = []
    failures: list[BaseException] = []

    def backfill() -> None:
        try:
            results.append(app_module._v2_backfill_exact_registered_legacy_marker(record, root))
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    threads = [threading.Thread(target=backfill) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    assert results.count(True) == 1
    assert results.count(False) == 5
    marker = root / ".discoveryagent" / "project.json"
    assert json.loads(marker.read_text(encoding="utf-8"))["project_id"] == record["id"]
    assert list(marker.parent.glob("project.json.tmp.*")) == []


@pytest.mark.parametrize("unsafe_kind", ["mismatch", "symlink"])
def test_legacy_marker_backfill_never_replaces_conflicting_or_unsafe_metadata(
    monkeypatch, tmp_path, unsafe_kind,
):
    record, root, _registry = _legacy_fixture(monkeypatch, tmp_path)
    marker = root / ".discoveryagent" / "project.json"
    if unsafe_kind == "mismatch":
        marker.write_text(
            json.dumps({"project_id": "different-project", "display_name": "Other"}),
            encoding="utf-8",
        )
        before = marker.read_bytes()
    else:
        marker.symlink_to(root / "untrusted-marker-target")
        before = os.readlink(marker)

    with pytest.raises(app_module._V2ProjectMarkerError) as error:
        app_module._v2_backfill_exact_registered_legacy_marker(record, root)

    assert error.value.code in {"project_marker_mismatch", "project_marker_unsafe"}
    if unsafe_kind == "mismatch":
        assert marker.read_bytes() == before
    else:
        assert marker.is_symlink()
        assert os.readlink(marker) == before


def test_locate_exact_registered_custom_legacy_root_backfills_and_resolves_all_contexts(
    monkeypatch, tmp_path,
):
    custom_root = tmp_path / "Desktop" / "optic-nerve-regeneration"
    record, root, registry = _legacy_fixture(
        monkeypatch,
        tmp_path,
        project_id="legacy-optic-fixture-123456",
        root=custom_root,
    )
    (root / "code" / "analysis.py").write_text("print('optic nerve')\n", encoding="utf-8")
    round_dir = root / "checkpoint" / "checkpoints" / "round_0"
    (round_dir / "round_report.md").write_text("# Optic nerve report\n", encoding="utf-8")
    registry_before = registry.read_bytes()
    monkeypatch.setattr(
        app_module,
        "_v2_native_project_recovery_picker_command",
        lambda: ["native-project-picker"],
    )
    monkeypatch.setattr(
        app_module.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, f"{root}\n", ""),
    )

    client = _client()
    response = client.post(f"/api/projects/{record['id']}/locate")

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["root"] == str(root)
    marker = json.loads(
        (root / ".discoveryagent" / "project.json").read_text(encoding="utf-8")
    )
    assert marker["project_id"] == record["id"]
    persisted = json.loads(registry.read_text(encoding="utf-8"))["projects"][0]
    assert persisted["project_root"] == str(root)
    assert registry.read_bytes() == registry_before  # exact-root repair leaves registry bytes untouched

    continued = client.post(f"/api/projects/{record['id']}/continue")
    assert continued.status_code == 200
    for route in (
        f"/projects/{record['url_slug']}",
        f"/projects/{record['url_slug']}/hypotheses",
        f"/projects/{record['url_slug']}/skills",
        f"/rounds/?project={record['url_slug']}",
    ):
        assert client.get(route).status_code == 200
    assert client.get("/api/round_workspace_code_artifact?path=analysis.py").get_data(as_text=True) == "print('optic nerve')\n"
    assert client.get(
        "/api/round_workspace_artifact?round=0&path=round_report.md"
    ).get_data(as_text=True) == "# Optic nerve report\n"
    assert app_module._v2_resolve_registered_project_context(
        project_id=record["id"],
        path_context={"code_dir": str(root / "code")},
    )["project_root"] == str(root)


@pytest.mark.parametrize(
    ("marker_kind", "expected_code", "expected_message"),
    (
        (
            "missing",
            "project_marker_missing",
            "The selected folder is not a DiscoveryAgent project. "
            "Select the project folder itself, not its parent.",
        ),
        (
            "unsafe",
            "project_marker_unsafe",
            "The selected folder contains unsafe DiscoveryAgent project metadata.",
        ),
        (
            "malformed",
            "project_marker_malformed",
            "The selected folder contains invalid DiscoveryAgent project metadata.",
        ),
        (
            "mismatch",
            "project_marker_mismatch",
            "The selected folder belongs to a different DiscoveryAgent project.",
        ),
    ),
)
def test_locate_reports_distinct_marker_error_codes_and_messages(
    monkeypatch, tmp_path, marker_kind, expected_code, expected_message,
):
    record, _root, _registry = _legacy_fixture(monkeypatch, tmp_path)
    candidate = tmp_path / f"selected-{marker_kind}"
    candidate.mkdir()
    marker = candidate / ".discoveryagent" / "project.json"
    if marker_kind == "unsafe":
        marker.parent.mkdir()
        marker.symlink_to(candidate / "untrusted-marker-target")
    elif marker_kind == "malformed":
        marker.parent.mkdir()
        marker.write_text("{not json", encoding="utf-8")
    elif marker_kind == "mismatch":
        app_module.write_project_metadata(candidate, "another-project", "Another project")
    monkeypatch.setattr(
        app_module,
        "_v2_native_project_recovery_picker_command",
        lambda: ["native-project-picker"],
    )
    monkeypatch.setattr(
        app_module.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, f"{candidate}\n", ""),
    )

    response = _client().post(f"/api/projects/{record['id']}/locate")

    assert response.status_code == 400
    assert response.get_json() == {
        "success": False,
        "code": expected_code,
        "error": expected_message,
    }


def test_markerless_legacy_relocation_persists_identity_and_survives_registry_reload(
    monkeypatch, tmp_path,
):
    record, source, registry = _legacy_fixture(monkeypatch, tmp_path)
    round_dir = source / "checkpoint" / "checkpoints" / "round_0"
    (source / "code" / "analysis.py").write_text("print('relocated')\n", encoding="utf-8")
    (round_dir / "round_report.md").write_text("# Relocated report\n", encoding="utf-8")
    (round_dir / "memory_report.md").write_text("# Relocated memory\n", encoding="utf-8")
    skill = source / ".discoveryagent" / "skills" / "relocated-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Relocated skill\n", encoding="utf-8")
    conversation = source / ".discoveryagent" / "interactions" / "conversation_state.json"
    audit = source / ".discoveryagent" / "interactions" / "audit_interactions.jsonl"
    conversation.write_text('{"surface":"audit_agent"}\n', encoding="utf-8")
    audit.write_text('{"event":"relocated"}\n', encoding="utf-8")
    destination_parent = tmp_path / "relocated"
    destination_parent.mkdir()
    client = _client()

    response = client.post(
        f"/api/projects/{record['id']}/edit",
        data=_edit_payload(record, destination_parent),
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    persisted = json.loads(registry.read_text(encoding="utf-8"))["projects"][0]
    destination = Path(persisted["project_root"])
    assert persisted["id"] == record["id"]
    assert persisted["url_slug"] == record["url_slug"]
    assert not source.exists()
    assert destination.parent == destination_parent
    assert json.loads(
        (destination / ".discoveryagent" / "project.json").read_text(encoding="utf-8")
    )["project_id"] == record["id"]
    assert app_module._v2_project_availability(persisted)["available"] is True
    assert app_module._v2_get_project(record["id"])["project_root"] == str(destination)

    for field, expected in (
        ("project_root", destination),
        ("code_dir", destination / "code"),
        ("data_dir", destination / "code" / "data"),
        ("checkpoint_dir", destination / "checkpoint" / "checkpoints" / "round_0"),
        ("readme_path", destination / "code" / "data" / "README.md"),
    ):
        resolved = app_module._v2_resolve_registered_project_context(
            project_id=record["id"], path_context={field: str(expected)},
        )
        assert resolved is not None
        assert resolved["project_root"] == str(destination)

    continued = client.post(f"/api/projects/{record['id']}/continue")
    assert continued.status_code == 200
    for route in (
        f"/projects/{record['url_slug']}",
        f"/projects/{record['url_slug']}/hypotheses",
        f"/projects/{record['url_slug']}/skills",
        f"/rounds/?project={record['url_slug']}",
        "/data",
    ):
        assert client.get(route).status_code == 200
    assert client.get(f"/api/projects/{record['id']}/skills").status_code == 200
    assert client.get("/api/round_workspace_code_artifact?path=analysis.py").get_data(as_text=True) == "print('relocated')\n"
    assert client.get(
        "/api/round_workspace_artifact?round=0&path=round_report.md"
    ).get_data(as_text=True) == "# Relocated report\n"
    assert client.get(
        "/api/round_workspace_artifact?round=0&path=memory_report.md"
    ).get_data(as_text=True) == "# Relocated memory\n"
    assert app_module._project_conversation_state_path(record["id"]) == (
        destination / ".discoveryagent" / "interactions" / "conversation_state.json"
    )
    assert app_module._project_interaction_log_path(record["id"]) == (
        destination / ".discoveryagent" / "interactions" / "audit_interactions.jsonl"
    )


def test_markerless_legacy_relocation_rolls_back_when_marker_creation_fails(
    monkeypatch, tmp_path,
):
    record, source, registry = _legacy_fixture(monkeypatch, tmp_path)
    destination_parent = tmp_path / "relocated"
    destination_parent.mkdir()
    registry_before = registry.read_bytes()
    monkeypatch.setattr(
        app_module,
        "write_project_metadata_if_absent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("marker write failed")),
    )

    response = _client().post(
        f"/api/projects/{record['id']}/edit",
        data=_edit_payload(record, destination_parent),
    )

    assert response.status_code == 500
    assert source.is_dir()
    assert not (source / ".discoveryagent" / "project.json").exists()
    assert list(destination_parent.iterdir()) == []
    assert registry.read_bytes() == registry_before


def test_markerless_legacy_relocation_rolls_back_move_and_marker_on_post_move_validation_failure(
    monkeypatch, tmp_path,
):
    record, source, registry = _legacy_fixture(monkeypatch, tmp_path)
    destination_parent = tmp_path / "relocated"
    destination_parent.mkdir()
    registry_before = registry.read_bytes()
    original_identity = app_module._v2_project_metadata_identity

    def fail_only_at_destination(target: Path) -> str:
        target = Path(target)
        if target.parent == destination_parent:
            raise app_module._V2ProjectMarkerError(
                "project_marker_malformed", "Injected post-move validation failure.",
            )
        return original_identity(target)

    monkeypatch.setattr(app_module, "_v2_project_metadata_identity", fail_only_at_destination)

    response = _client().post(
        f"/api/projects/{record['id']}/edit",
        data=_edit_payload(record, destination_parent),
    )

    assert response.status_code == 400
    assert source.is_dir()
    assert not (source / ".discoveryagent" / "project.json").exists()
    assert list(destination_parent.iterdir()) == []
    assert registry.read_bytes() == registry_before
