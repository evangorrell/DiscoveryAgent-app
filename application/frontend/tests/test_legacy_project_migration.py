"""Regression coverage for the checkout-local to durable legacy transition."""

from __future__ import annotations

import json
from pathlib import Path

from da_selection_ui.legacy_project_migration import migrate_checkout_legacy_store
from da_selection_ui import app as app_module


def _write_registry(root: Path, projects: list[dict]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "v2_projects.json"
    path.write_text(json.dumps({"projects": projects}, indent=2), encoding="utf-8")
    return path


def _legacy_record(root: Path, project_id: str, display_name: str) -> dict:
    project = root / project_id
    return {
        "id": project_id,
        "display_name": display_name,
        "project_root": str(project),
        "code_dir": str(project / "code"),
        "data_dir": str(project / "code" / "data"),
        "checkpoint_dir": str(project / "checkpoint" / "checkpoints" / "round_4"),
        "readme_path": str(project / "code" / "README.md"),
        "task_description": "Preserve this metadata",
    }


def test_registered_project_moves_with_history_and_unregistered_stub_stays(tmp_path):
    source = tmp_path / "checkout" / "local_disk"
    durable = tmp_path / "application-data" / "legacy" / "local_disk"
    record = _legacy_record(source, "optic-nerve-regeneration-734492", "Optic Nerve Regeneration")
    source_registry = _write_registry(source, [record])
    source_registry_before = source_registry.read_text(encoding="utf-8")

    project = Path(record["project_root"])
    (project / "code").mkdir(parents=True)
    (project / "code" / "README.md").write_text("research files", encoding="utf-8")
    history = project / "checkpoint" / "checkpoints" / "round_4" / "selected_hypothesis.md"
    history.parent.mkdir(parents=True)
    history.write_text("selected hypothesis", encoding="utf-8")
    interactions = project / ".interaction_logs" / "audit_interactions.jsonl"
    interactions.parent.mkdir()
    interactions.write_text('{"event":"preserved"}\n', encoding="utf-8")
    unregistered = source / "gbm-targets-3b06bc"
    unregistered.mkdir()
    (unregistered / "README.md").write_text("stub", encoding="utf-8")

    first = migrate_checkout_legacy_store(source, durable)

    destination = durable / project.name
    assert first["moved"] == [record["id"]]
    assert not project.exists()
    assert (destination / "code" / "README.md").read_text() == "research files"
    assert (destination / history.relative_to(project)).read_text() == "selected hypothesis"
    assert (destination / interactions.relative_to(project)).read_text().strip() == '{"event":"preserved"}'
    assert unregistered.is_dir()
    assert source_registry.read_text(encoding="utf-8") == source_registry_before

    migrated = json.loads((durable / "v2_projects.json").read_text())["projects"][0]
    assert migrated["display_name"] == "Optic Nerve Regeneration"
    assert migrated["task_description"] == "Preserve this metadata"
    for field in ("project_root", "code_dir", "data_dir", "checkpoint_dir", "readme_path"):
        assert str(destination) in migrated[field]

    registry_before = (durable / "v2_projects.json").read_text(encoding="utf-8")
    second = migrate_checkout_legacy_store(source, durable)
    assert second["moved"] == []
    assert (durable / "v2_projects.json").read_text(encoding="utf-8") == registry_before


def test_existing_durable_record_wins_nonempty_metadata_without_overwrite(tmp_path):
    source = tmp_path / "checkout" / "local_disk"
    durable = tmp_path / "durable" / "local_disk"
    source_record = _legacy_record(source, "same-id", "Historical Name")
    _write_registry(source, [source_record])
    source_project = Path(source_record["project_root"])
    source_project.mkdir(parents=True)
    (source_project / "source-only.txt").write_text("source", encoding="utf-8")

    destination_project = durable / "same-id"
    destination_project.mkdir(parents=True)
    (destination_project / "destination-only.txt").write_text("destination", encoding="utf-8")
    destination_record = {
        "id": "same-id",
        "display_name": "Durable Name",
        "project_root": str(destination_project),
        "task_description": "",
    }
    _write_registry(durable, [destination_record])

    result = migrate_checkout_legacy_store(source, durable)

    assert result["conflicts"] == ["same-id"]
    assert source_project.is_dir()
    assert (destination_project / "destination-only.txt").read_text() == "destination"
    assert not (destination_project / "source-only.txt").exists()
    merged = json.loads((durable / "v2_projects.json").read_text())["projects"][0]
    assert merged["display_name"] == "Durable Name"
    assert merged["task_description"] == "Preserve this metadata"
    assert "code_dir" not in merged


def test_v2_project_loader_discovers_migrated_legacy_project(tmp_path, monkeypatch):
    source = tmp_path / "old-checkout" / "local_disk"
    durable = tmp_path / "data" / "legacy" / "local_disk"
    record = _legacy_record(source, "old-project", "Old Project")
    _write_registry(source, [record])
    Path(record["project_root"]).mkdir(parents=True)

    monkeypatch.delenv("DISK_PATH", raising=False)
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: durable)
    monkeypatch.setattr(
        app_module,
        "get_historical_checkout_legacy_disk_root",
        lambda: source,
    )

    projects = app_module._v2_load_projects()

    migrated = next(project for project in projects if project["id"] == "old-project")
    assert migrated["display_name"] == "Old Project"
    assert Path(migrated["project_root"]) == durable / "old-project"
