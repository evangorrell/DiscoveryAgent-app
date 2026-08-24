"""Focused tests for V2 Slice 2 refinement — project action menu editability,
the edit endpoint, README rewrite, edit file handling, and integration."""
import io
import json
from pathlib import Path

import pytest

from da_selection_ui import app as app_module


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    trash = tmp_path / ".test-trash"

    def move_to_test_trash(target, platform_name=None):
        trash.mkdir(exist_ok=True)
        destination = trash / target.name
        target.replace(destination)

    monkeypatch.setattr(app_module, "_v2_move_to_trash", move_to_test_trash)
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s["authenticated"] = True
    return client


def _create(client, name="Optic Nerve Regeneration", context="Single-cell RNA-seq of mouse RGCs.", files=None,
            objective="Identify mechanisms that promote retinal ganglion cell regeneration."):
    data = {"project_name": name, "task_description": objective, "dataset_context": context}
    if files:
        data["files"] = files
    return client.post("/api/projects", data=data, content_type="multipart/form-data").get_json()


def _project_root(project_id):
    return Path(app_module._v2_get_project(project_id)["project_root"])


def _list(client):
    return client.get("/api/projects").get_json()["projects"]


def _edit(client, pid, name, context, files=None, objective="", model=None, query_count=None):
    data = {"project_name": name, "task_description": objective, "dataset_context": context}
    if model is not None:
        data["project_model"] = model
    if query_count is not None:
        data["query_count"] = query_count
    if files:
        data["files"] = files
    return client.post(f"/api/projects/{pid}/edit", data=data, content_type="multipart/form-data")


# --- every V2 project is editable (no legacy distinction) --------------------

def test_every_v2_project_can_open_edit(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client)["project_id"]
    # listed with no editability/legacy branch fields
    row = next(p for p in _list(client) if p["id"] == pid)
    assert "editable" not in row and "is_legacy" not in row
    # detail (edit prefill) is available for it
    detail = client.get(f"/api/projects/{pid}")
    assert detail.status_code == 200
    project = detail.get_json()["project"]
    assert project["id"] == pid
    assert project["task_description"] == "Identify mechanisms that promote retinal ganglion cell regeneration."


# --- (3,4) optional fields ---------------------------------------------------

def test_edit_empty_name_gets_safe_default(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client)["project_id"]
    resp = _edit(client, pid, "   ", "ctx")
    assert resp.status_code == 200
    record = next(p for p in json.loads((tmp_path / "v2_projects.json").read_text())["projects"] if p["id"] == pid)
    assert record["display_name"] == "Untitled project"


def test_edit_empty_context_clears_readme(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client)["project_id"]
    resp = _edit(client, pid, "Name", "  ")
    assert resp.status_code == 200
    record = next(p for p in json.loads((tmp_path / "v2_projects.json").read_text())["projects"] if p["id"] == pid)
    assert record["dataset_context"] == ""
    assert record["readme_path"] is None
    assert not (_project_root(pid) / "code" / "data" / "README.md").exists()


# --- (5,6,7) metadata + README rewrite --------------------------------------

def test_edit_updates_name_context_and_readme(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, name="Old Name", context="Old context.")["project_id"]
    new_ctx = "Updated context spanning\nmultiple meaningful lines."
    new_objective = "Prioritize perturbations that causally improve regeneration."
    resp = _edit(client, pid, "New Name", new_ctx, objective=new_objective)
    assert resp.status_code == 200 and resp.get_json()["success"] is True

    record = next(p for p in json.loads((tmp_path / "v2_projects.json").read_text())["projects"] if p["id"] == pid)
    assert record["display_name"] == "New Name"
    assert record["task_description"] == new_objective
    assert record["dataset_context"] == new_ctx

    expected = f"# New Name\n\n{new_ctx}\n"
    assert (_project_root(pid) / "code" / "data" / "README.md").read_text() == expected
    assert record["readme_path"] == "code/data/README.md"
    assert app_module._v2_get_project(pid)["readme_path"] == str(
        _project_root(pid) / "code" / "data" / "README.md"
    )

    # detail endpoint reflects the update
    detail = client.get(f"/api/projects/{pid}").get_json()["project"]
    assert detail["display_name"] == "New Name"
    assert detail["task_description"] == new_objective
    assert detail["dataset_context"] == new_ctx

    cfg = json.loads((_project_root(pid) / "checkpoint" / "checkpoints" / "round_0" / "config.json").read_text())["config"]
    assert cfg["dataset_name"] == "New Name"
    assert cfg["task_description"] == new_objective


def test_edit_updates_updated_at(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client)["project_id"]
    before = next(p for p in _list(client) if p["id"] == pid)
    assert before["show_updated"] is False  # brand new -> "Created recently"

    reg = json.loads((tmp_path / "v2_projects.json").read_text())["projects"][0]
    created = reg["created_at"]

    _edit(client, pid, "Renamed", "New context.")
    record = next(p for p in json.loads((tmp_path / "v2_projects.json").read_text())["projects"] if p["id"] == pid)
    assert record["updated_at"] != created

    after = next(p for p in _list(client) if p["id"] == pid)
    assert after["show_updated"] is True  # now "Updated ..."


# --- (9) cancel = no server mutation ----------------------------------------

def test_detail_fetch_does_not_mutate(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, name="Stable", context="Stable context.")["project_id"]
    reg_before = (tmp_path / "v2_projects.json").read_text()
    client.get(f"/api/projects/{pid}")  # opening edit (prefill) only
    assert (tmp_path / "v2_projects.json").read_text() == reg_before


def test_legacy_missing_model_reads_as_default_without_mutating(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client)["project_id"]
    registry_path = tmp_path / "v2_projects.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    for key in ("project_model", "reasoning_model", "execution_model", "hypothesis_model"):
        registry["projects"][0].pop(key, None)
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    before = registry_path.read_text(encoding="utf-8")

    detail = client.get(f"/api/projects/{pid}").get_json()["project"]

    assert detail["reasoning_model"] == app_module.model_catalog.DEFAULT_REASONING_MODEL
    assert detail["execution_model"] == app_module.model_catalog.DEFAULT_EXECUTION_MODEL
    assert detail["project_models_valid"] is True
    assert registry_path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("legacy_value", [None, "", "automatic"])
def test_legacy_automatic_query_count_reads_as_twelve_and_migrates_only_on_save(
    monkeypatch, tmp_path, legacy_value,
):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client)["project_id"]
    registry_path = tmp_path / "v2_projects.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    record = registry["projects"][0]
    if legacy_value is None:
        record.pop("query_count", None)
    else:
        record["query_count"] = legacy_value
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    before = registry_path.read_text(encoding="utf-8")

    detail = client.get(f"/api/projects/{pid}").get_json()["project"]

    assert detail["query_count"] == 12
    assert registry_path.read_text(encoding="utf-8") == before

    response = _edit(
        client, pid, "Name", "Context", objective="Objective", query_count="12",
    )

    assert response.status_code == 200
    saved = app_module._v2_get_project(pid)
    assert saved["query_count"] == 12
    assert "num_queries" not in saved
    config = json.loads(
        (Path(saved["checkpoint_dir"]) / "config.json").read_text(encoding="utf-8")
    )["config"]
    assert config["num_queries"] == 5


def test_legacy_explicit_query_override_is_preserved_and_migrated_on_save(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client)["project_id"]
    registry_path = tmp_path / "v2_projects.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    record = registry["projects"][0]
    record.pop("query_count", None)
    record["num_queries"] = 17
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    before = registry_path.read_text(encoding="utf-8")

    detail = client.get(f"/api/projects/{pid}").get_json()["project"]

    assert detail["query_count"] == 17
    assert registry_path.read_text(encoding="utf-8") == before

    response = _edit(
        client, pid, "Name", "Context", objective="Objective", query_count="17",
    )

    assert response.status_code == 200
    saved = app_module._v2_get_project(pid)
    assert saved["query_count"] == 17
    assert "num_queries" not in saved
    config = json.loads(
        (Path(saved["checkpoint_dir"]) / "config.json").read_text(encoding="utf-8")
    )["config"]
    assert config["num_queries"] == 5


def test_edit_saves_one_project_model_without_rewriting_interrupted_snapshot(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client)["project_id"]
    project = app_module._v2_get_project(pid)
    config_path = Path(project["checkpoint_dir"]) / "config.json"
    interrupted = json.loads(config_path.read_text(encoding="utf-8"))
    old_reasoning = interrupted["config"]["hypothesis_model"]
    old_execution = interrupted["config"]["execution_model"]
    (Path(project["checkpoint_dir"]) / "query_000").mkdir()
    before = config_path.read_text(encoding="utf-8")

    response = _edit(
        client, pid, "Updated", "Updated context.",
        objective="Updated objective.", model="claude-sonnet-4-6", query_count="29",
    )

    assert response.status_code == 200
    saved = app_module._v2_get_project(pid)
    assert saved["reasoning_model"] == "claude-sonnet-4-6"
    assert saved["execution_model"] == "claude-sonnet-4-6"
    assert saved["query_count"] == 29
    assert config_path.read_text(encoding="utf-8") == before
    after_config = json.loads(config_path.read_text(encoding="utf-8"))["config"]
    before_config = json.loads(before)["config"]
    assert after_config["hypothesis_model"] == old_reasoning
    assert after_config["execution_model"] == old_execution
    assert {
        after_config[field] for field in app_module.model_catalog.PROJECT_MODEL_CONFIG_FIELDS
    } == {
        before_config[field] for field in app_module.model_catalog.PROJECT_MODEL_CONFIG_FIELDS
    }
    assert after_config["num_queries"] == before_config["num_queries"] == 5


def test_edit_does_not_rewrite_a_completed_round_query_snapshot(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client)["project_id"]
    project = app_module._v2_get_project(pid)
    config_path = Path(project["checkpoint_dir"]) / "config.json"
    (Path(project["checkpoint_dir"]) / "round_report.md").write_text(
        "# Completed round\n", encoding="utf-8",
    )
    before = config_path.read_text(encoding="utf-8")

    response = _edit(
        client, pid, "Updated", "Updated context.",
        objective="Updated objective.", query_count="41",
    )

    assert response.status_code == 200
    assert app_module._v2_get_project(pid)["query_count"] == 41
    assert config_path.read_text(encoding="utf-8") == before


def test_retired_saved_model_is_recoverable_but_cannot_be_silently_kept(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client)["project_id"]
    registry_path = tmp_path / "v2_projects.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["projects"][0]["project_model"] = "claude-retired-1"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    detail = client.get(f"/api/projects/{pid}").get_json()["project"]
    # Explicit separate values take precedence; remove them to exercise the
    # older unified-only migration contract.
    registry["projects"][0].pop("reasoning_model", None)
    registry["projects"][0].pop("execution_model", None)
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    detail = client.get(f"/api/projects/{pid}").get_json()["project"]
    assert detail["project_models_valid"] is False
    assert detail["reasoning_model"] == app_module.model_catalog.DEFAULT_REASONING_MODEL
    assert "supported replacement" in detail["project_models_error"]

    refused = _edit(client, pid, "Name", "Context", objective="Objective")
    assert refused.status_code == 400
    assert refused.get_json()["code"] == "unsupported_project_model"
    replaced = _edit(
        client, pid, "Name", "Context", objective="Objective",
        model="claude-opus-4-6",
    )
    assert replaced.status_code == 200
    saved = app_module._v2_get_project(pid)
    assert saved["reasoning_model"] == "claude-opus-4-6"
    assert saved["execution_model"] == "claude-opus-4-6"
    assert "project_model" not in saved


# --- (10,11,12) files during edit -------------------------------------------

def test_edit_adds_new_files_keeps_existing(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, files=[(io.BytesIO(b"orig"), "existing.csv")])["project_id"]
    data = _project_root(pid) / "code" / "data"
    assert (data / "existing.csv").exists()

    _edit(client, pid, "Name", "ctx", files=[(io.BytesIO(b"new"), "added.tsv")])
    assert (data / "existing.csv").read_text() == "orig"  # untouched
    assert (data / "added.tsv").exists()

    # detail lists both source files, excludes README.txt
    files = {f["name"] for f in client.get(f"/api/projects/{pid}").get_json()["project"]["files"]}
    assert files == {"existing.csv", "added.tsv"}
    assert "README.txt" not in files


def test_legacy_project_files_exclude_discoveryagent_outputs(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, files=[(io.BytesIO(b"input"), "dataset.csv")])["project_id"]
    projects = app_module._v2_load_projects()
    project = next(p for p in projects if p["id"] == pid)
    code_dir = Path(project["code_dir"])

    # Reproduce a legacy record whose uploads and generated artifacts shared
    # the working directory instead of using a dedicated data_dir.
    source = code_dir / "study_metadata.tsv"
    source.write_text("sample\tgroup\n", encoding="utf-8")
    generated = code_dir / "analysis_output" / "volcano.png"
    generated.parent.mkdir(parents=True)
    generated.write_bytes(b"generated")
    generated_2 = code_dir / "outputs" / "summary.csv"
    generated_2.parent.mkdir(parents=True)
    generated_2.write_text("generated", encoding="utf-8")
    project["data_dir"] = None
    app_module._v2_save_projects(projects)

    files = {
        f["name"]
        for f in client.get(f"/api/projects/{pid}").get_json()["project"]["files"]
    }
    assert "study_metadata.tsv" in files
    assert "analysis_output/volcano.png" not in files
    assert "outputs/summary.csv" not in files


def test_edit_duplicate_new_filename_is_not_stored_again(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, files=[(io.BytesIO(b"orig"), "data.csv")])["project_id"]
    data = _project_root(pid) / "code" / "data"
    _edit(client, pid, "Name", "ctx", files=[(io.BytesIO(b"newdup"), "data.csv")])
    assert (data / "data.csv").read_text() == "orig"  # not overwritten
    assert not (data / "data_1.csv").exists()


def test_edit_traversal_contained(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client)["project_id"]
    _edit(client, pid, "Name", "ctx", files=[(io.BytesIO(b"x"), "../../evil.txt")])
    data = _project_root(pid) / "code" / "data"
    assert (data / "evil.txt").exists()
    assert not (tmp_path / "evil.txt").exists()


# --- (13,14) integration -----------------------------------------------------

def test_project_home_reflects_updated_name(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, name="Before Name")["project_id"]
    client.post(f"/api/projects/{pid}/continue")  # activate
    _edit(client, pid, "After Name", "ctx")
    state = client.get("/api/project_state").get_json()
    assert state["project"]["title"] == "After Name"


def test_projects_overview_reflects_updated_context(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, context="Original overview text about the dataset.")["project_id"]
    _edit(client, pid, "Name", "Completely new overview describing updated data content.")
    row = next(p for p in _list(client) if p["id"] == pid)
    assert row["overview"].startswith("Completely new overview")


def test_edit_unknown_project_404(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert _edit(client, "nope-123456", "n", "c").status_code == 404


def test_edit_routes_registered():
    endpoints = {r.endpoint for r in app_module.app.url_map.iter_rules()}
    assert "api_projects_detail" in endpoints
    assert "api_projects_edit" in endpoints
