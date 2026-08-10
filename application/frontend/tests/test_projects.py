"""Focused tests for V2 Slice 2 — Projects registry, listing, creation,
activation, and Project Home integration. Temporary filesystem roots only;
no live LLM, no S3."""
import io
import json
from pathlib import Path

from da_selection_ui import app as app_module


def _client(monkeypatch, tmp_path):
    """Authenticated client with a clean DISK_PATH and NO legacy project."""
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    trash = tmp_path / ".test-trash"

    def move_to_test_trash(target, platform_name=None):
        trash.mkdir(exist_ok=True)
        target.replace(trash / target.name)

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
    return client.post("/api/projects", data=data, content_type="multipart/form-data")


def _list(client):
    resp = client.get("/api/projects")
    assert resp.status_code == 200, resp.data
    payload = resp.get_json()
    assert payload["success"] is True
    return payload["projects"]


# --- (1,2) page + real-only list --------------------------------------------

def test_projects_page_renders(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.get("/projects")
    assert resp.status_code == 200
    assert b"Projects" in resp.data
    assert b"New Project" in resp.data


def test_list_returns_only_real_projects_no_demo(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    projects = _list(client)
    assert projects == []  # no demo fixtures, no legacy in a clean session
    names = {p["display_name"] for p in projects}
    assert "Glioblastoma Cell-State Plasticity" not in names


# --- (3,4,10,11,12) creation persistence + README ---------------------------

def test_create_persists_metadata_and_readme(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    ctx = "These are single-cell RNA-seq profiles collected at 0, 3 and 7 days after injury."
    resp = _create(client, name="Optic Nerve Regeneration", context=ctx)
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    # canonical slug-based Project Home destination, not the generic /app or Hypothesis Studio
    assert body["success"] is True and body["destination"] == "/projects/optic-nerve-regeneration"
    pid = body["project_id"]

    # registry persisted outside git, under DISK_PATH
    registry = tmp_path / "v2_projects.json"
    assert registry.exists()
    record = next(p for p in json.loads(registry.read_text())["projects"] if p["id"] == pid)
    assert record["display_name"] == "Optic Nerve Regeneration"
    assert record["task_description"] == "Identify mechanisms that promote retinal ganglion cell regeneration."
    assert record["dataset_context"] == ctx
    assert record["data_dir"] == "code/data"
    resolved = app_module._v2_get_project(pid)
    assert resolved["data_dir"] == str(tmp_path / pid / "code" / "data")

    # README.md written at the canonical dataset root with exact Project Context.
    readme = tmp_path / pid / "code" / "data" / "README.md"
    assert readme.exists()
    text = readme.read_text()
    assert text.startswith("# Optic Nerve Regeneration\n\n")
    assert ctx in text
    assert record["readme_path"] == "code/data/README.md"
    assert resolved["readme_path"] == str(readme)

    # minimal valid config.json exists at round_0; no readme_path override key
    # (select_hypothesis.py's loader never reads config['readme_path'] --
    # the app passes it at runtime via the constructor argument instead, from
    # project_cfg['readme_path'] / the registry's own readme_path field).
    cfg = tmp_path / pid / "checkpoint" / "checkpoints" / "round_0" / "config.json"
    assert cfg.exists()
    config = json.loads(cfg.read_text())["config"]
    assert config["dataset_name"] == "Optic Nerve Regeneration"
    assert config["task_description"] == "Identify mechanisms that promote retinal ganglion cell regeneration."
    assert "readme_path" not in config
    assert config["working_directory"] == str(tmp_path / pid / "code")

    # list uses display_name + a short overview excerpt from dataset_context
    proj = next(p for p in _list(client) if p["id"] == pid)
    assert proj["display_name"] == "Optic Nerve Regeneration"
    assert proj["overview"] and proj["overview"].startswith("These are single-cell")


# --- (8,9) optional fields ---------------------------------------------------

# Project Name, Research Objective, and Project Context are required at creation.

def test_empty_name_rejected(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = _create(client, name="   ", context="ctx")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["success"] is False and "project name" in body["error"].lower()
    assert not (tmp_path / "v2_projects.json").exists()


def test_all_required_fields_rejected_when_blank(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    # Each required field, blanked in isolation, is rejected.
    assert _create(client, name="   ").status_code == 400
    assert _create(client, objective="   ").status_code == 400
    assert _create(client, context="   ").status_code == 400
    # A fully blank submission names all three required fields.
    resp = _create(client, name="  ", objective="  ", context="  ")
    assert resp.status_code == 400
    err = resp.get_json()["error"].lower()
    assert "project name" in err and "research objective" in err and "project context" in err
    # Nothing was persisted by any of the rejected attempts.
    assert not (tmp_path / "v2_projects.json").exists()


def test_empty_context_rejected(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = _create(client, name="Proj", context="  ")
    assert resp.status_code == 400
    assert "project context" in resp.get_json()["error"].lower()
    assert not (tmp_path / "v2_projects.json").exists()


def test_empty_objective_rejected_at_create(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = _create(client, objective="  ")
    assert resp.status_code == 400
    assert "research objective" in resp.get_json()["error"].lower()


# --- (13,14) uploads + traversal safety -------------------------------------

def test_multiple_files_saved(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    files = [
        (io.BytesIO(b"a,b\n1,2\n"), "sample_metadata.csv"),
        (io.BytesIO(b"x\ty\n"), "differential_expression_d3.tsv"),
    ]
    resp = _create(client, files=files)
    pid = resp.get_json()["project_id"]
    data = tmp_path / pid / "code" / "data"
    assert (data / "sample_metadata.csv").exists()
    assert (data / "differential_expression_d3.tsv").exists()


def test_path_traversal_filename_contained(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    files = [(io.BytesIO(b"evil"), "../../../etc/pwn.txt")]
    resp = _create(client, files=files)
    pid = resp.get_json()["project_id"]
    data = tmp_path / pid / "code" / "data"
    # sanitized to a basename inside the hardcoded dataset dir; nothing escapes the root
    assert (data / "pwn.txt").exists()
    assert not (tmp_path / "etc" / "pwn.txt").exists()
    assert not (tmp_path.parent / "etc" / "pwn.txt").exists()
    # every uploaded file stays within code/data/
    for p in data.iterdir():
        assert p.resolve().parent == data.resolve()


def test_duplicate_upload_names_are_stored_once(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    files = [
        (io.BytesIO(b"first"), "data.csv"),
        (io.BytesIO(b"second"), "data.csv"),
    ]
    resp = _create(client, files=files)
    pid = resp.get_json()["project_id"]
    data = tmp_path / pid / "code" / "data"
    assert (data / "data.csv").exists()
    assert (data / "data.csv").read_text() == "first"
    assert not (data / "data_1.csv").exists()


def test_repeated_file_endpoint_upload_keeps_existing_file(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client).get_json()["project_id"]
    endpoint = f"/api/projects/{pid}/files"
    relative_path = "ProcessedData/counts.csv"

    first = client.post(endpoint, data={
        "relative_path": relative_path,
        "file": (io.BytesIO(b"first"), "counts.csv"),
    }, content_type="multipart/form-data")
    repeated = client.post(endpoint, data={
        "relative_path": relative_path,
        "file": (io.BytesIO(b"second"), "counts.csv"),
    }, content_type="multipart/form-data")

    assert first.status_code == 200
    assert first.get_json()["already_exists"] is False
    assert repeated.status_code == 200
    assert repeated.get_json()["already_exists"] is True
    data = tmp_path / pid / "code" / "data" / "ProcessedData"
    assert (data / "counts.csv").read_bytes() == b"first"
    assert list(data.iterdir()) == [data / "counts.csv"]


def test_single_file_upload_preserves_folder_relative_path(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client).get_json()["project_id"]

    resp = client.post(f"/api/projects/{pid}/files", data={
        "relative_path": "ProcessedData/tran2019/counts.csv",
        "file": (io.BytesIO(b"gene,count\nA,1\n"), "counts.csv"),
    }, content_type="multipart/form-data")

    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body["success"] is True
    assert body["file"]["name"] == "ProcessedData/tran2019/counts.csv"
    data = tmp_path / pid / "code" / "data"
    assert (data / "ProcessedData" / "tran2019" / "counts.csv").read_text() == "gene,count\nA,1\n"

    detail = client.get(f"/api/projects/{pid}").get_json()["project"]
    assert {f["name"] for f in detail["files"]} == {"ProcessedData/tran2019/counts.csv"}


def test_folder_upload_lists_every_file_with_nested_relative_paths(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client).get_json()["project_id"]
    uploads = [
        ("ProcessedData/samples.csv", "samples.csv", b"sample,group\nA,control\n"),
        ("ProcessedData/tran2019/counts.csv", "counts.csv", b"gene,count\nA,1\n"),
        ("ProcessedData/tran2019/metadata/labels.tsv", "labels.tsv", b"id\tlabel\n1\tRGC\n"),
    ]

    for relative_path, filename, content in uploads:
        response = client.post(f"/api/projects/{pid}/files", data={
            "relative_path": relative_path,
            "file": (io.BytesIO(content), filename),
        }, content_type="multipart/form-data")
        assert response.status_code == 200

    detail = client.get(f"/api/projects/{pid}").get_json()["project"]
    assert [item["name"] for item in detail["files"]] == [
        "ProcessedData/samples.csv",
        "ProcessedData/tran2019/counts.csv",
        "ProcessedData/tran2019/metadata/labels.tsv",
    ]


def test_single_file_upload_rejects_relative_path_traversal(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client).get_json()["project_id"]

    resp = client.post(f"/api/projects/{pid}/files", data={
        "relative_path": "ProcessedData/../evil.txt",
        "file": (io.BytesIO(b"evil"), "evil.txt"),
    }, content_type="multipart/form-data")

    assert resp.status_code == 400
    assert not (tmp_path / pid / "code" / "data" / "evil.txt").exists()
    assert not (tmp_path / pid / "code" / "evil.txt").exists()


# --- (15) distinct dirs for same name ---------------------------------------

def test_same_name_creates_distinct_projects(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    p1 = _create(client, name="Repeat Project", context="c1").get_json()["project_id"]
    p2 = _create(client, name="Repeat Project", context="c2").get_json()["project_id"]
    assert p1 != p2
    assert (tmp_path / p1).exists() and (tmp_path / p2).exists()
    ids = {p["id"] for p in _list(client)}
    assert p1 in ids and p2 in ids


# --- (16) failed creation leaves no half-project ----------------------------

def test_failed_creation_cleans_up(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def boom(_projects):
        raise RuntimeError("registry write failed")
    monkeypatch.setattr(app_module, "_v2_save_projects", boom)

    resp = _create(client, name="Doomed Project", context="ctx")
    assert resp.status_code == 500
    # no project directory and no registry entry survive
    dirs = [p for p in tmp_path.iterdir() if p.is_dir() and p.name.startswith("doomed-project")]
    assert dirs == []
    assert not (tmp_path / "v2_projects.json").exists() or \
        all(p["display_name"] != "Doomed Project"
            for p in json.loads((tmp_path / "v2_projects.json").read_text()).get("projects", []))


# --- deletion -----------------------------------------------------------------

def test_delete_project_removes_registry_entry_and_project_root(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    delete_id = _create(client, name="Delete Me").get_json()["project_id"]
    keep_id = _create(client, name="Keep Me").get_json()["project_id"]
    delete_root = tmp_path / delete_id
    keep_root = tmp_path / keep_id
    assert delete_root.exists() and keep_root.exists()

    resp = client.delete(f"/api/projects/{delete_id}")

    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body["success"] is True
    assert body["destination"] == "/projects"
    assert not delete_root.exists()
    assert (tmp_path / ".test-trash" / delete_id / "code" / "data" / "README.md").is_file()
    assert keep_root.exists()
    remaining = {p["id"] for p in json.loads((tmp_path / "v2_projects.json").read_text())["projects"]}
    assert delete_id not in remaining
    assert keep_id in remaining


def test_delete_active_project_clears_active_session_but_keeps_auth(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, name="Active Delete").get_json()["project_id"]
    with client.session_transaction() as s:
        assert s["v2_active_project_id"] == pid

    resp = client.delete(f"/api/projects/{pid}")

    assert resp.status_code == 200, resp.data
    with client.session_transaction() as s:
        assert s["authenticated"] is True
        assert "v2_active_project_id" not in s
        assert "v2_active_project_slug" not in s
        assert "project_config" not in s


def test_delete_unknown_project_returns_404(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.delete("/api/projects/missing-project")
    assert resp.status_code == 404


# --- (5,6,7) status / rounds / updated --------------------------------------

def _round0(tmp_path, pid):
    return tmp_path / pid / "checkpoint" / "checkpoints" / "round_0"


def test_new_project_status_not_started(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client).get_json()["project_id"]
    proj = next(p for p in _list(client) if p["id"] == pid)
    assert proj["status_label"] == "Not Started"
    assert proj["round_metadata"] == "New project"
    assert proj["is_new"] is True


def test_completed_round_status_and_count(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client).get_json()["project_id"]
    rd = _round0(tmp_path, pid)
    (rd / "round_report.md").write_text("# Round Report\n\n## Round Report\n\nDone.")
    (rd / "memory_report.md").write_text("# Mem\n\n## Accumulated Report\n\nUnderstanding.")
    proj = next(p for p in _list(client) if p["id"] == pid)
    assert proj["status_label"] == "Round 0 Complete"
    assert proj["status_tone"] == "review"
    assert proj["round_metadata"] == "1 round"
    assert proj["is_new"] is False
    assert proj["updated_at"] is not None


def test_running_round_status(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client).get_json()["project_id"]
    (_round0(tmp_path, pid) / "query_000").mkdir()
    proj = next(p for p in _list(client) if p["id"] == pid)
    assert proj["status_label"] == "In Progress"
    assert proj["status_tone"] == "active"


def test_interrupted_round_status_is_not_reported_as_not_started(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client).get_json()["project_id"]
    state_file = _round0(tmp_path, pid).parent.parent / "run_state.json"
    state_file.write_text(
        json.dumps({"round": 0, "status": "interrupted"}),
        encoding="utf-8",
    )

    proj = next(p for p in _list(client) if p["id"] == pid)

    assert proj["status_label"] == "Stopped"
    assert proj["status_tone"] == "neutral"


# --- (17,18,19) continue activation + destination ---------------------------

def test_continue_activates_and_routes(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client).get_json()["project_id"]
    # fresh session (no active project) then continue
    resp = client.post(f"/api/projects/{pid}/continue")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    # Continue always lands on the canonical Project Home (slug-based); it no
    # longer bypasses Project Home based on project state.
    assert body["destination"] == "/projects/optic-nerve-regeneration"
    with client.session_transaction() as s:
        assert s["project_config"]["checkpoint_dir"].endswith("round_0")
        assert s["project_label"] == "Optic Nerve Regeneration"
        assert s["v2_active_project_id"] == pid
        assert s["v2_active_project_slug"] == "optic-nerve-regeneration"


def test_continue_running_round_still_routes_to_project_home(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client).get_json()["project_id"]
    (_round0(tmp_path, pid) / "query_000").mkdir()
    body = client.post(f"/api/projects/{pid}/continue").get_json()
    # Even with a round actively running, Continue still lands on Project
    # Home -- it is the orientation layer, not a state-aware bypass.
    assert body["destination"] == "/projects/optic-nerve-regeneration"


def test_continue_unknown_project_404(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.post("/api/projects/does-not-exist/continue")
    assert resp.status_code == 404


# --- (20,21,22) Project Home integration ------------------------------------

def test_back_to_projects_points_to_projects_route(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _create(client)
    # /home is a compatibility redirect; it lands on the canonical
    # /projects/<slug> Project Home once a V2 project is active.
    html = client.get("/home", follow_redirects=True).get_data(as_text=True)
    assert 'href="/projects"' in html
    assert "Back to Projects" in html


def test_project_home_uses_display_name_and_research_objective(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    objective = "Identify glioblastoma cell-state transitions that predict recurrence."
    pid = _create(client, name="Glioblastoma Plasticity",
                  context="Single-cell atlas of glioblastoma cell states.",
                  objective=objective).get_json()["project_id"]
    client.post(f"/api/projects/{pid}/continue")
    state = client.get("/api/project_state").get_json()
    assert state["project"]["title"] == "Glioblastoma Plasticity"
    # Dataset Context must NOT become the research goal; Research Objective does.
    assert state["project"]["research_goal"] == objective


def test_project_objective_endpoint_persists_canonical_task_description(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    # Objective is required at creation now; the endpoint under test still lets a
    # scientist revise it afterward and persists the canonical value.
    pid = _create(client).get_json()["project_id"]
    client.post(f"/api/projects/{pid}/continue")
    objective = "Identify causal perturbations that improve regeneration."

    resp = client.post("/api/project_objective", json={"task_description": f"  {objective}  "})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["task_description"] == objective

    record = next(p for p in json.loads((tmp_path / "v2_projects.json").read_text())["projects"] if p["id"] == pid)
    assert record["task_description"] == objective
    cfg = json.loads((tmp_path / pid / "checkpoint" / "checkpoints" / "round_0" / "config.json").read_text())["config"]
    assert cfg["task_description"] == objective


# --- (24) V1 + slice routes intact ------------------------------------------

def test_routes_intact():
    endpoints = {r.endpoint for r in app_module.app.url_map.iter_rules()}
    for n in ("home", "app_interface", "round_monitor", "unified_report",
              "project_home", "api_project_state",
              "projects", "api_projects_list", "api_projects_create",
              "api_projects_continue", "api_projects_upload_file"):
        assert n in endpoints


def test_projects_available_without_auth():
    resp = app_module.app.test_client().get("/api/projects")
    assert resp.status_code == 200
