"""High-value regressions for importing an existing project in place."""

from __future__ import annotations

import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from da_selection_ui import app as app_module
import pytest

from da_selection_ui.project_import import (
    MAX_METADATA_BYTES,
    ProjectImportMetadataError,
    inspect_project_metadata,
    recognized_layout,
)


def _client():
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
    return client


def _choose(client, root: Path):
    with (
        patch.object(app_module, "_v2_native_project_import_picker_command", return_value=["picker"]),
        patch.object(
            app_module.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0, stdout=str(root), stderr=""),
        ),
    ):
        return client.post("/api/project-import/choose")


def _form(client, root: Path, **overrides):
    preview = _choose(client, root)
    assert preview.status_code == 200, preview.get_data(as_text=True)
    return _form_from_preview(root, preview.get_json(), **overrides)


def _form_from_preview(root: Path, preview: dict, **overrides):
    return {
        "project_root": str(root),
        "preview_token": preview["preview_token"],
        "project_name": "Imported Legacy Project",
        "task_description": "Identify reproducible mechanisms.",
        "dataset_context": "Existing local data and artifacts.",
        **overrides,
    }


def _legacy_root(tmp_path: Path) -> tuple[Path, dict[Path, bytes]]:
    root = tmp_path / "copied-hpc-project"
    round_zero = root / "checkpoints" / "round_0"
    code = root / "code"
    round_zero.mkdir(parents=True)
    code.mkdir()
    files = {
        root / "research.bin": b"\x00existing-research\xff",
        code / "README.md": b"# Imported Legacy Project\n\nExisting local data and artifacts.\n",
        code / "data" / "input.csv": b"gene,score\nKLF6,1\n",
        code / "analysis" / "pipeline.py": b"print(\"legacy analysis\")\n",
        code / ".claude" / "skills" / "legacy-skill" / "SKILL.md": (
            b"---\nname: Legacy Skill\ndescription: Preserved imported analysis skill.\n---\n"
        ),
        round_zero / "config.json": json.dumps({
            "config": {
                "dataset_name": "Imported Legacy Project",
                "task_description": "Identify reproducible mechanisms.",
                "working_directory": "/stale/hpc/user/project/code",
                "api_key": "must-not-import",
                "execution_model": "obsolete-model",
            }
        }).encode(),
        round_zero / "round_report.md": b"# Existing report\n",
        round_zero / "memory_report.md": b"# Existing memory\n",
    }
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return root, files


def _split_legacy_root(tmp_path: Path) -> Path:
    root = tmp_path / "archived-run-bundle"
    suffix = "study_20260213_120509"
    code = root / f"code_{suffix}"
    checkpoints = root / f"logs_{suffix}" / "checkpoints"
    round_zero = checkpoints / "round_0"
    (code / "data").mkdir(parents=True)
    (code / "reports" / "figures").mkdir(parents=True)
    round_zero.mkdir(parents=True)
    (code / "data" / "README.txt").write_text(
        "# Data overview\n\nExample perturbation dataset.\n",
        encoding="utf-8",
    )
    (code / "analysis.py").write_text("print('analysis')\n", encoding="utf-8")
    (code / "reports" / "figures" / "relay.png").write_bytes(b"figure")
    (checkpoints / "config.yaml").write_text(
        "dataset_name: Archived study\nworking_directory: /stale/hpc/code\n",
        encoding="utf-8",
    )
    (round_zero / "memory_report.md").write_text(
        "# Accumulated findings\n", encoding="utf-8",
    )
    return root


def _tree_snapshot(root: Path) -> dict[str, tuple]:
    """Capture names, kinds, modes, targets, and exact regular-file bytes."""
    snapshot: dict[str, tuple] = {}
    for path in [root, *sorted(root.rglob("*"))]:
        relative = "." if path == root else path.relative_to(root).as_posix()
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if path.is_symlink():
            snapshot[relative] = ("symlink", mode, os.readlink(path))
        elif path.is_dir():
            snapshot[relative] = ("directory", mode)
        else:
            snapshot[relative] = ("file", mode, path.read_bytes())
    return snapshot


def test_bounded_metadata_prepopulation_and_conflicts(tmp_path):
    root, _files = _legacy_root(tmp_path)
    metadata = inspect_project_metadata(root)
    assert metadata["project_name"] == "Imported Legacy Project"
    assert metadata["task_description"] == "Identify reproducible mechanisms."
    assert metadata["dataset_context"] == "Existing local data and artifacts."
    serialized = json.dumps(metadata)
    assert "/stale/hpc" not in serialized
    assert "must-not-import" not in serialized
    assert "obsolete-model" not in serialized

    yaml_path = root / "checkpoints" / "config.yaml"
    yaml_path.write_text(
        "dataset_name: A conflicting name\nworking_directory: /another/stale/path\n",
        encoding="utf-8",
    )
    conflicted = inspect_project_metadata(root)
    # Conflicting explicit metadata is blank rather than guessed from the folder.
    assert conflicted["project_name"] == ""
    assert any("conflicting project names" in warning for warning in conflicted["warnings"])
    assert "/another/stale/path" not in json.dumps(conflicted)

    unknown = tmp_path / "unknown-folder"
    unknown.mkdir()
    empty = inspect_project_metadata(unknown)
    assert empty["project_name"] == "unknown-folder"
    assert empty["task_description"] == ""
    assert empty["dataset_context"] == ""


def test_split_hpc_bundle_is_recognized_and_imported_in_place(tmp_path):
    client = _client()
    root = _split_legacy_root(tmp_path)
    suffix = "study_20260213_120509"
    code = root / f"code_{suffix}"
    round_zero = root / f"logs_{suffix}" / "checkpoints" / "round_0"

    metadata = inspect_project_metadata(root)
    assert metadata["project_name"] == "Archived study"
    assert metadata["dataset_context"] == (
        "Example perturbation dataset."
    )
    assert metadata["recognized_files"] == [
        f"logs_{suffix}/checkpoints/config.yaml",
        f"code_{suffix}/data/README.txt",
    ]
    layout = recognized_layout(root)
    assert layout == {
        "code_dir": code,
        "data_dir": code / "data",
        "checkpoint_dir": round_zero,
        "readme_path": code / "data" / "README.txt",
    }

    response = client.post(
        "/api/projects/import",
        data=_form(
            client,
            root,
            project_name="Archived study",
            dataset_context=metadata["dataset_context"],
        ),
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    record = app_module._v2_get_project(response.get_json()["project_id"])
    assert Path(record["code_dir"]) == code
    assert Path(record["data_dir"]) == code / "data"
    assert Path(record["checkpoint_dir"]) == round_zero
    assert Path(record["readme_path"]) == code / "data" / "README.txt"
    assert (round_zero / "config.json").is_file()
    code_response = client.get(
        "/api/round_workspace_code_artifact",
        query_string={"path": "analysis.py"},
    )
    assert code_response.status_code == 200
    assert code_response.data == b"print('analysis')\n"



def test_split_layout_rejects_multiple_runs(tmp_path):
    root = _split_legacy_root(tmp_path)
    (root / "code_other").mkdir()
    (root / "logs_other/checkpoints/round_0").mkdir(parents=True)
    with pytest.raises(ProjectImportMetadataError, match="Multiple matching"):
        inspect_project_metadata(root)
    with pytest.raises(ProjectImportMetadataError, match="Multiple matching"):
        recognized_layout(root)


def test_canonical_layout_does_not_borrow_archived_checkpoints(tmp_path):
    root = _split_legacy_root(tmp_path)
    (root / "code").mkdir()
    layout = recognized_layout(root)
    assert layout["code_dir"] == root / "code"
    assert layout["checkpoint_dir"] is None
    assert inspect_project_metadata(root)["recognized_files"] == []


def test_split_layout_does_not_follow_symlinked_code(tmp_path):
    root = tmp_path / "bundle"
    (root / "logs_study/checkpoints/round_0").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "code_study").symlink_to(outside, target_is_directory=True)
    assert recognized_layout(root)["checkpoint_dir"] is None


def test_changed_preview_and_malformed_registry_fail_closed(tmp_path):
    client = _client()
    root, _files = _legacy_root(tmp_path)
    form = _form(client, root)
    config = root / "checkpoints" / "round_0" / "config.json"
    config.write_text(
        json.dumps({"config": {"dataset_name": "Changed after preview"}}),
        encoding="utf-8",
    )
    changed = client.post("/api/projects/import", data=form)
    assert changed.status_code == 400
    assert "changed after selection" in changed.get_json()["error"]
    assert not (root / ".discoveryagent").exists()

    layout_root = tmp_path / "layout-changed-after-preview"
    layout_root.mkdir()
    layout_form = _form(client, layout_root)
    (layout_root / "code").mkdir()
    layout_changed = client.post("/api/projects/import", data=layout_form)
    assert layout_changed.status_code == 400
    assert "changed after selection" in layout_changed.get_json()["error"]

    other = tmp_path / "other-project"
    other.mkdir()
    other_form = _form(client, other)
    registry = tmp_path / "projects.json"
    registry.write_bytes(b"malformed registry bytes")
    registry_before = registry.read_bytes()
    rejected = client.post("/api/projects/import", data=other_form)
    assert rejected.status_code == 400
    assert "registry" in rejected.get_json()["error"].lower()
    assert registry.read_bytes() == registry_before
    assert not (other / ".discoveryagent").exists()

def test_metadata_reads_are_bounded_and_never_follow_symlinks(tmp_path):
    root = tmp_path / "legacy"
    config = root / "checkpoints" / "round_0" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_bytes(b"x" * (MAX_METADATA_BYTES + 1))
    try:
        inspect_project_metadata(root)
    except ProjectImportMetadataError as exc:
        assert "too large" in str(exc)
    else:  # pragma: no cover - safety assertion
        raise AssertionError("oversized metadata was accepted")

    config.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text('{"config": {"dataset_name": "Outside"}}', encoding="utf-8")
    config.symlink_to(outside)
    try:
        inspect_project_metadata(root)
    except ProjectImportMetadataError as exc:
        assert "symbolic link" in str(exc)
    else:  # pragma: no cover - safety assertion
        raise AssertionError("symlinked metadata was followed")


def test_picker_cancel_is_silent_and_read_only(monkeypatch, tmp_path):
    client = _client()
    root = tmp_path / "preview-then-cancel"
    root.mkdir()
    preview = _choose(client, root).get_json()
    stale_form = _form_from_preview(root, preview)
    monkeypatch.setattr(app_module, "_v2_native_project_import_picker_command", lambda: ["picker"])
    monkeypatch.setattr(
        app_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="cancelled"),
    )
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    response = client.post("/api/project-import/choose")
    assert response.status_code == 200
    assert response.get_json() == {"success": True, "cancelled": True}
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before
    assert not (tmp_path / "projects.json").exists()
    stale = client.post("/api/projects/import", data=stale_form)
    assert stale.status_code == 400
    assert stale.get_json()["code"] == "invalid_import_preview"
    assert not (root / ".discoveryagent").exists()


def test_preview_is_opaque_exact_session_bound_expiring_and_single_use(tmp_path):
    root = tmp_path / "bound-preview"
    root.mkdir()
    owner = _client()
    other_session = _client()
    preview = _choose(owner, root).get_json()
    form = _form_from_preview(root, preview)
    assert preview["preview_token"] != inspect_project_metadata(root)["metadata_token"]
    assert "metadata_token" not in preview["metadata"]

    crossed = other_session.post("/api/projects/import", data=form)
    assert crossed.status_code == 400
    assert crossed.get_json()["code"] == "invalid_import_preview"
    imported = owner.post("/api/projects/import", data=form)
    assert imported.status_code == 200
    replayed = owner.post("/api/projects/import", data=form)
    assert replayed.status_code == 400
    assert replayed.get_json()["code"] == "invalid_import_preview"

    selected = tmp_path / "selected-root"
    substituted = tmp_path / "substituted-root"
    selected.mkdir()
    substituted.mkdir()
    substituted_form = _form_from_preview(selected, _choose(owner, selected).get_json())
    substituted_form["project_root"] = str(substituted)
    rejected = owner.post("/api/projects/import", data=substituted_form)
    assert rejected.status_code == 400
    assert not (substituted / ".discoveryagent").exists()

    expiring = tmp_path / "expiring-preview"
    expiring.mkdir()
    expiring_form = _form_from_preview(expiring, _choose(owner, expiring).get_json())
    app_module._V2_PROJECT_IMPORT_PREVIEWS[expiring_form["preview_token"]]["expires_at"] = 0
    expired = owner.post("/api/projects/import", data=expiring_form)
    assert expired.status_code == 400
    assert expired.get_json()["code"] == "invalid_import_preview"

    malformed = dict(expiring_form, preview_token="client-computable")
    malformed_response = owner.post("/api/projects/import", data=malformed)
    assert malformed_response.status_code == 400
    assert not (expiring / ".discoveryagent").exists()


def test_confirmed_markerless_import_preserves_files_and_resolves_artifacts(tmp_path):
    client = _client()
    root, existing_files = _legacy_root(tmp_path)
    before = {path: content for path, content in existing_files.items()}
    response = client.post("/api/projects/import", data=_form(client, root))
    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["project_id"].startswith("prj_")
    assert payload["files_moved"] is False
    assert payload["projects_url"] == "/projects"
    listed = client.get("/api/projects").get_json()["projects"]
    assert any(project["id"] == payload["project_id"] for project in listed)

    for path, content in before.items():
        assert path.read_bytes() == content
    marker = json.loads((root / ".discoveryagent" / "project.json").read_text())
    assert marker == {
        "project_id": payload["project_id"],
        "display_name": "Imported Legacy Project",
    }
    assert stat.S_IMODE((root / ".discoveryagent").stat().st_mode) == 0o700
    assert stat.S_IMODE((root / ".discoveryagent" / "project.json").stat().st_mode) == 0o600
    assert not list((root / ".discoveryagent").glob("*.tmp.*"))

    record = app_module._v2_get_project(payload["project_id"])
    assert Path(record["project_root"]) == root
    assert Path(record["checkpoint_dir"]) == root / "checkpoints" / "round_0"
    assert record["data_sources"] == []
    assert record["reasoning_model"] == app_module.model_catalog.DEFAULT_REASONING_MODEL
    assert "/stale/hpc" not in json.dumps(record)
    assert app_module._v2_project_availability(record)["available"] is True

    assert client.get(payload["destination"]).status_code == 200
    audit = client.get(f"/audit?project={record['url_slug']}")
    assert audit.status_code == 302
    assert record["url_slug"] in audit.headers["Location"]
    workspace = client.get(
        f"/projects/{record['url_slug']}/rounds/0", follow_redirects=True,
    )
    assert workspace.status_code == 200
    assert b"AUDIT AGENT" in workspace.data
    report = client.get(
        "/api/round_workspace_artifact",
        query_string={"round": 0, "path": "round_report.md"},
    )
    assert report.data == b"# Existing report\n"
    memory = client.get(
        f"/projects/{record['url_slug']}/rounds/0/report/download",
        query_string={"type": "memory", "view": "full"},
    )
    assert memory.data == b"# Existing memory\n"
    code = client.get(
        "/api/round_workspace_code_artifact",
        query_string={"path": "analysis/pipeline.py"},
    )
    assert code.data == b"print(\"legacy analysis\")\n"
    assert (Path(record["data_dir"]) / "input.csv").read_bytes() == b"gene,score\nKLF6,1\n"
    skills = client.get(f"/api/projects/{record['id']}/skills").get_json()
    assert skills["skills"] == [{
        "id": "legacy-skill",
        "name": "Legacy Skill",
        "description": "Preserved imported analysis skill.",
    }]


def test_duplicate_and_existing_marker_states_never_overwrite_identity(tmp_path):
    client = _client()
    root, _files = _legacy_root(tmp_path)
    first = client.post("/api/projects/import", data=_form(client, root))
    assert first.status_code == 200
    marker_path = root / ".discoveryagent" / "project.json"
    marker_before = marker_path.read_bytes()
    duplicate = _choose(client, root)
    assert duplicate.status_code == 409
    assert duplicate.get_json()["code"] == "project_already_registered"
    assert marker_path.read_bytes() == marker_before

    unregistered = tmp_path / "unregistered-v2"
    unregistered.mkdir()
    app_module.write_project_metadata(unregistered, "prj_" + "9" * 32, "Unregistered")
    unregistered_before = (unregistered / ".discoveryagent" / "project.json").read_bytes()
    accepted = _choose(client, unregistered)
    assert accepted.status_code == 200
    assert accepted.get_json()["metadata"]["project_name"] == "Unregistered"
    assert (unregistered / ".discoveryagent" / "project.json").read_bytes() == unregistered_before

    legacy_identity = tmp_path / "legacy-identity"
    legacy_identity.mkdir()
    app_module.write_project_metadata(
        legacy_identity,
        "optic-nerve-regeneration-734492",
        "Optic Nerve Regeneration",
    )
    legacy_marker_before = (
        legacy_identity / ".discoveryagent" / "project.json"
    ).read_bytes()
    legacy_accepted = _choose(client, legacy_identity)
    assert legacy_accepted.status_code == 200
    assert legacy_accepted.get_json()["metadata"]["project_name"] == (
        "Optic Nerve Regeneration"
    )
    assert (
        legacy_identity / ".discoveryagent" / "project.json"
    ).read_bytes() == legacy_marker_before

    malformed = tmp_path / "malformed"
    (malformed / ".discoveryagent").mkdir(parents=True)
    malformed_marker = malformed / ".discoveryagent" / "project.json"
    malformed_marker.write_text("not-json", encoding="utf-8")
    malformed_before = malformed_marker.read_bytes()
    rejected = _choose(client, malformed)
    assert rejected.status_code == 409
    assert rejected.get_json()["code"] == "project_marker_malformed"
    assert malformed_marker.read_bytes() == malformed_before

    invalid_identity = tmp_path / "invalid-identity"
    (invalid_identity / ".discoveryagent").mkdir(parents=True)
    (invalid_identity / ".discoveryagent" / "project.json").write_text(
        json.dumps({"project_id": "legacy-id", "display_name": "Unsafe Identity"}),
        encoding="utf-8",
    )
    rejected = _choose(client, invalid_identity)
    assert rejected.status_code == 409
    assert rejected.get_json()["code"] == "project_marker_malformed"

    symlinked = tmp_path / "symlinked-marker"
    (symlinked / ".discoveryagent").mkdir(parents=True)
    outside_marker = tmp_path / "outside-project-marker.json"
    outside_marker.write_text(
        json.dumps({"project_id": "prj_" + "8" * 32, "display_name": "Outside"}),
        encoding="utf-8",
    )
    (symlinked / ".discoveryagent" / "project.json").symlink_to(outside_marker)
    rejected = _choose(client, symlinked)
    assert rejected.status_code == 409
    assert rejected.get_json()["code"] == "project_marker_unsafe"
    assert json.loads(outside_marker.read_text())["display_name"] == "Outside"


def test_imported_remove_reregister_restart_and_artifacts_preserve_exact_tree(
    monkeypatch, tmp_path,
):
    client = _client()
    root, _files = _legacy_root(tmp_path)
    imported = client.post("/api/projects/import", data=_form(client, root))
    assert imported.status_code == 200, imported.get_data(as_text=True)
    project_id = imported.get_json()["project_id"]
    record = app_module._v2_get_project(project_id)
    assert record is not None and record["imported_in_place"] is True

    round_zero = root / "checkpoints" / "round_0"
    (round_zero / "selected_hypothesis.md").write_text(
        "# Synthetic selected hypothesis\n", encoding="utf-8",
    )
    (round_zero / "generated_hypotheses.json").write_text(
        json.dumps({"hypotheses": [{"hypothesis": "Synthetic selected hypothesis"}]}),
        encoding="utf-8",
    )
    interactions = root / ".discoveryagent" / "interactions"
    interactions.mkdir()
    (interactions / "audit_interactions.jsonl").write_text(
        json.dumps({"event_type": "synthetic_audit", "message": "Synthetic audit message"}) + "\n",
        encoding="utf-8",
    )
    (interactions / "conversation_state.json").write_text(
        json.dumps({"version": 1, "surfaces": {}}), encoding="utf-8",
    )
    before_remove = _tree_snapshot(root)
    marker_before = (root / ".discoveryagent" / "project.json").read_bytes()
    registry = tmp_path / "projects.json"
    registry_before = registry.read_bytes()

    monkeypatch.setattr(
        app_module,
        "_v2_move_to_trash",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("imported project removal must never call Trash")
        ),
    )
    original_save = app_module._v2_save_projects

    def fail_after_registry_publication(*args, **kwargs):
        original_save(*args, **kwargs)
        raise OSError("simulated removal registry failure")

    monkeypatch.setattr(app_module, "_v2_save_projects", fail_after_registry_publication)
    failed_remove = client.delete(f"/api/projects/{project_id}")
    assert failed_remove.status_code == 500
    assert registry.read_bytes() == registry_before
    assert app_module._v2_get_project(project_id) is not None
    assert _tree_snapshot(root) == before_remove
    monkeypatch.setattr(app_module, "_v2_save_projects", original_save)

    removed = client.delete(f"/api/projects/{project_id}")
    assert removed.status_code == 200, removed.get_data(as_text=True)
    assert removed.get_json()["removal_mode"] == "registry_only"
    assert removed.get_json()["files_untouched"] is True
    assert app_module._v2_get_project(project_id) is None
    assert _tree_snapshot(root) == before_remove

    preview_response = _choose(client, root)
    assert preview_response.status_code == 200, preview_response.get_data(as_text=True)
    preview = preview_response.get_json()
    assert preview["metadata"]["project_name"] == "Imported Legacy Project"
    reregistered = client.post(
        "/api/projects/import", data=_form_from_preview(root, preview),
    )
    assert reregistered.status_code == 200, reregistered.get_data(as_text=True)
    reregistered_payload = reregistered.get_json()
    assert reregistered_payload["project_id"] == project_id
    assert reregistered_payload["projects_url"] == "/projects"
    listed_after_reregistration = client.get("/api/projects").get_json()["projects"]
    assert any(project["id"] == project_id for project in listed_after_reregistration)
    assert (root / ".discoveryagent" / "project.json").read_bytes() == marker_before
    assert _tree_snapshot(root) == before_remove

    app_module._v2_clear_project_runtime_caches()
    restarted = _client()
    listed = restarted.get("/api/projects").get_json()["projects"]
    summary = next(project for project in listed if project["id"] == project_id)
    assert summary["imported_in_place"] is True
    record = app_module._v2_get_project(project_id)
    continued = restarted.post(f"/api/projects/{project_id}/continue")
    assert continued.status_code == 200
    assert restarted.get(continued.get_json()["destination"]).status_code == 200
    report = restarted.get(
        "/api/round_workspace_artifact",
        query_string={"round": 0, "path": "round_report.md"},
    )
    assert report.data == b"# Existing report\n"
    memory = restarted.get(
        f"/projects/{record['url_slug']}/rounds/0/report/download",
        query_string={"type": "memory", "view": "full"},
    )
    assert memory.data == b"# Existing memory\n"
    code = restarted.get(
        "/api/round_workspace_code_artifact",
        query_string={"path": "analysis/pipeline.py"},
    )
    assert code.data == b'print("legacy analysis")\n'
    assert (Path(record["data_dir"]) / "input.csv").read_bytes() == b"gene,score\nKLF6,1\n"
    skills = restarted.get(f"/api/projects/{project_id}/skills").get_json()
    assert [skill["id"] for skill in skills["skills"]] == ["legacy-skill"]
    assert b"Synthetic audit message" in (
        root / ".discoveryagent" / "interactions" / "audit_interactions.jsonl"
    ).read_bytes()


def test_registered_identity_directs_to_locate_and_locate_stays_strict(monkeypatch, tmp_path):
    project_id = "prj_" + "7" * 32
    missing = tmp_path / "missing-original"
    selected = tmp_path / "moved-project"
    selected.mkdir()
    app_module.write_project_metadata(selected, project_id, "Moved")
    app_module._v2_save_projects([{
        "id": project_id,
        "url_slug": "moved",
        "display_name": "Moved",
        "project_root": str(missing),
        "code_dir": "code",
        "data_dir": "code/data",
        "checkpoint_dir": "checkpoint/checkpoints/round_0",
    }])
    client = _client()
    monkeypatch.setattr(app_module, "_v2_native_project_import_picker_command", lambda: ["picker"])
    monkeypatch.setattr(
        app_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=str(selected), stderr=""),
    )
    response = client.post("/api/project-import/choose")
    assert response.status_code == 409
    assert response.get_json()["code"] == "project_requires_locate"

    markerless = tmp_path / "arbitrary-markerless"
    markerless.mkdir()
    monkeypatch.setattr(app_module, "_v2_native_project_recovery_picker_command", lambda: ["picker"])
    monkeypatch.setattr(
        app_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=str(markerless), stderr=""),
    )
    located = client.post(f"/api/projects/{project_id}/locate")
    assert located.status_code == 400
    assert located.get_json()["code"] == "project_marker_missing"


def test_registered_unavailable_and_copied_identity_states_are_distinct(tmp_path):
    unavailable = tmp_path / "registered-but-unavailable"
    unavailable.mkdir()
    unavailable_id = "prj_" + "5" * 32
    available = tmp_path / "available-original"
    available.mkdir()
    available_id = "prj_" + "6" * 32
    app_module.write_project_metadata(available, available_id, "Available")
    app_module._v2_save_projects([
        {
            "id": unavailable_id,
            "url_slug": "unavailable",
            "display_name": "Unavailable",
            "project_root": str(unavailable),
        },
        {
            "id": available_id,
            "url_slug": "available",
            "display_name": "Available",
            "project_root": str(available),
        },
    ])
    client = _client()
    exact = _choose(client, unavailable)
    assert exact.status_code == 409
    assert exact.get_json()["code"] == "project_registered_unavailable"

    copied = tmp_path / "copied-identity"
    copied.mkdir()
    app_module.write_project_metadata(copied, available_id, "Copied")
    mismatch = _choose(client, copied)
    assert mismatch.status_code == 409
    assert mismatch.get_json()["code"] == "project_marker_mismatch"


def test_data_registry_and_protected_root_checks_fail_closed(monkeypatch, tmp_path):
    client = _client()
    root = tmp_path / "candidate"
    root.mkdir()
    registry = tmp_path / "data_library.json"
    outside = tmp_path / "outside-data-registry.json"
    outside.write_text(json.dumps({"schema_version": 1, "datasets": []}), encoding="utf-8")
    registry.symlink_to(outside)
    assert _choose(client, root).status_code == 400

    registry.unlink()
    with monkeypatch.context() as scoped:
        scoped.setattr(app_module, "_V2_IMPORT_REGISTRY_MAX_BYTES", 8)
        registry.write_bytes(b"123456789")
        assert _choose(client, root).status_code == 400

    missing_nested_source = root / "future-data"
    registry.write_text(json.dumps({
        "schema_version": 1,
        "datasets": [{
            "id": "ds_future",
            "display_name": "Future",
            "source_path": str(missing_nested_source),
            "canonical_path": str(missing_nested_source),
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }],
    }), encoding="utf-8")
    overlap = _choose(client, root)
    assert overlap.status_code == 400
    assert "Data source" in overlap.get_json()["error"]

    registry.unlink()
    protected = root / "nested-application-storage"
    monkeypatch.setattr(app_module, "_v2_delete_protected_roots", lambda: (protected,))
    broad = _choose(client, root)
    assert broad.status_code == 400
    assert "protected" in broad.get_json()["error"]


def test_rollback_keeps_replaced_created_paths(tmp_path):
    created_file = tmp_path / "created.txt"
    created_file.write_text("first", encoding="utf-8")
    first_file_stat = created_file.lstat()
    created_file.unlink()
    created_file.write_text("replacement", encoding="utf-8")
    app_module._v2_import_remove_created_file(
        created_file, (first_file_stat.st_dev, first_file_stat.st_ino),
    )
    assert created_file.read_text(encoding="utf-8") == "replacement"

    created_dir = tmp_path / "created-dir"
    created_dir.mkdir()
    first_dir_stat = created_dir.lstat()
    created_dir.rmdir()
    created_dir.mkdir()
    app_module._v2_import_rollback(
        [], [(created_dir, (first_dir_stat.st_dev, first_dir_stat.st_ino))],
    )
    assert created_dir.is_dir()


def test_failed_publication_restores_exact_registry_and_keeps_racing_marker(monkeypatch, tmp_path):
    existing_root = tmp_path / "existing"
    existing_root.mkdir()
    existing_id = "prj_" + "4" * 32
    app_module.write_project_metadata(existing_root, existing_id, "Existing")
    app_module._v2_save_projects([{
        "id": existing_id,
        "url_slug": "existing",
        "display_name": "Existing",
        "project_root": str(existing_root),
    }])
    registry = tmp_path / "projects.json"
    registry_before = registry.read_bytes()
    candidate = tmp_path / "registry-rollback"
    candidate.mkdir()
    client = _client()
    form = _form(client, candidate)
    original_save = app_module._v2_save_projects

    def fail_after_publication(*args, **kwargs):
        original_save(*args, **kwargs)
        raise OSError("fail after registry publication")

    monkeypatch.setattr(app_module, "_v2_save_projects", fail_after_publication)
    failed = client.post("/api/projects/import", data=form)
    assert failed.status_code == 500
    assert registry.read_bytes() == registry_before
    assert not (candidate / ".discoveryagent").exists()
    monkeypatch.setattr(app_module, "_v2_save_projects", original_save)

    racing_root = tmp_path / "racing-marker"
    racing_root.mkdir()
    racing_form = _form(client, racing_root)
    conflict_id = "prj_" + "3" * 32
    original_marker_writer = app_module.write_project_metadata_if_absent

    def publish_conflict(project_root, *_args, **_kwargs):
        app_module.write_project_metadata(project_root, conflict_id, "Racing identity")
        return original_marker_writer(
            project_root, "prj_" + "2" * 32, "Ignored", return_identity=True,
        )

    monkeypatch.setattr(app_module, "write_project_metadata_if_absent", publish_conflict)
    raced = client.post("/api/projects/import", data=racing_form)
    assert raced.status_code == 500
    marker = json.loads(
        (racing_root / ".discoveryagent" / "project.json").read_text(encoding="utf-8")
    )
    assert marker["project_id"] == conflict_id
    assert registry.read_bytes() == registry_before


def test_unsafe_roots_and_failed_writes_rollback_only_import_state(monkeypatch, tmp_path):
    client = _client()
    root = tmp_path / "rollback-project"
    root.mkdir()
    sentinel = root / "keep.txt"
    sentinel.write_bytes(b"keep-exactly")
    original_save = app_module._v2_save_projects
    original_marker_writer = app_module.write_project_metadata_if_absent

    def fail_registry(*args, **kwargs):
        original_save(*args, **kwargs)
        raise OSError("simulated registry failure after publication")

    monkeypatch.setattr(app_module, "_v2_save_projects", fail_registry)
    failed = client.post("/api/projects/import", data=_form(client, root))
    assert failed.status_code == 500
    assert sentinel.read_bytes() == b"keep-exactly"
    assert not (root / ".discoveryagent").exists()
    assert not (root / "code").exists()
    assert not (root / "checkpoint").exists()
    assert not (tmp_path / "projects.json").exists()

    monkeypatch.setattr(app_module, "_v2_save_projects", original_save)

    marker_failure_root = tmp_path / "marker-failure-project"
    marker_failure_root.mkdir()
    marker_sentinel = marker_failure_root / "keep.bin"
    marker_sentinel.write_bytes(b"preserve marker failure fixture")
    monkeypatch.setattr(
        app_module, "write_project_metadata_if_absent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("simulated marker failure")),
    )
    failed_marker = client.post(
        "/api/projects/import",
        data=_form(client, marker_failure_root, project_name="Marker Failure"),
    )
    assert failed_marker.status_code == 500
    assert marker_sentinel.read_bytes() == b"preserve marker failure fixture"
    assert not (marker_failure_root / ".discoveryagent").exists()
    assert not (marker_failure_root / "code").exists()
    assert not (marker_failure_root / "checkpoint").exists()
    monkeypatch.setattr(app_module, "write_project_metadata_if_absent", original_marker_writer)

    real = tmp_path / "real-project"
    real.mkdir()
    linked = tmp_path / "linked-project"
    linked.symlink_to(real, target_is_directory=True)
    rejected = _choose(client, linked)
    assert rejected.status_code == 400
    assert not (real / ".discoveryagent").exists()

    real_access = os.access
    monkeypatch.setattr(app_module.os, "access", lambda path, mode: False if Path(path) == real else real_access(path, mode))
    unreadable = _choose(client, real)
    assert unreadable.status_code == 400


def test_concurrent_repeated_submission_registers_once(tmp_path):
    root = tmp_path / "concurrent-project"
    root.mkdir()
    clients = [_client(), _client()]
    forms = [_form(client, root) for client in clients]

    def submit(index):
        return clients[index].post("/api/projects/import", data=forms[index]).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(submit, range(2)))
    assert statuses == [200, 409]
    registry = json.loads((tmp_path / "projects.json").read_text())
    assert len(registry["projects"]) == 1
    marker = json.loads((root / ".discoveryagent" / "project.json").read_text())
    assert marker["project_id"] == registry["projects"][0]["id"]


def test_valid_imported_continuation_launch_preserves_root_and_identity(
    monkeypatch, tmp_path,
):
    client = _client()
    root, _files = _legacy_root(tmp_path)
    imported = client.post("/api/projects/import", data=_form(client, root))
    assert imported.status_code == 200
    project_id = imported.get_json()["project_id"]
    project = app_module._v2_get_project(project_id)
    assert project is not None and project["imported_in_place"] is True

    source = root / "checkpoints" / "round_0"
    source.joinpath("generated_hypotheses.json").write_text(json.dumps({
        "hypotheses": ["**Hypothesis**: Synthetic continuation."],
    }), encoding="utf-8")
    selection = source / "selected_hypothesis.md"
    selection.write_text("**Hypothesis**: Synthetic continuation.", encoding="utf-8")
    report_mtime = source.joinpath("round_report.md").stat().st_mtime
    os.utime(selection, (report_mtime + 30, report_mtime + 30))

    before = _tree_snapshot(root)
    marker_before = root.joinpath(".discoveryagent", "project.json").read_bytes()
    captured = {}

    def launch(active_project):
        captured["project"] = dict(active_project)
        return {
            "project_id": active_project["id"],
            "round": 1,
            "run_id": "a" * 32,
            "status": "starting",
        }

    monkeypatch.setattr(app_module.local_round_runner, "start_round", launch)
    client.get(f"/projects/{project['url_slug']}/home")
    response = client.post("/api/rounds/start", json={"project_id": project_id})

    assert response.status_code == 202
    assert response.get_json()["round"] == 1
    assert captured["project"]["id"] == project_id
    assert captured["project"]["project_root"] == str(root.resolve())
    assert app_module._v2_get_project(project_id)["project_root"] == str(root.resolve())
    assert root.joinpath(".discoveryagent", "project.json").read_bytes() == marker_before
    assert _tree_snapshot(root) == before
