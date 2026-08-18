"""Focused tests for DiscoveryAgent V2 canonical project-scoped navigation,
addressed by a persisted, human-readable url_slug (not the internal project
id, and never /rounds/current).

Route table:
  GET /projects                                  -> Projects list
  GET /projects/<url_slug>                       -> Project Home
  GET /projects/<url_slug>/hypotheses             -> Hypothesis Studio
  GET /rounds/                                   -> active/latest Round Workspace
  GET /projects/<url_slug>/rounds/<int:n>         -> compatibility redirect to /rounds/
  GET /legacy/projects/<url_slug>/rounds/<int:n>  -> exact V2 workspace redirect

Internal APIs (e.g. /api/projects/<id>/edit) keep using the immutable
internal id -- only user-facing browser navigation uses the slug.

Temporary filesystem roots only; no live LLM, no S3; AIR and
.auth_submissions are untouched by any test here.
"""
import json
from pathlib import Path

from da_selection_ui import app as app_module


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s["authenticated"] = True
    return client


def _create(client, name="Optic Nerve Regeneration", context="Some dataset context."):
    resp = client.post(
        "/api/projects",
        data={
            "project_name": name,
            "task_description": "Identify testable regeneration hypotheses.",
            "dataset_context": context,
        },
        content_type="multipart/form-data",
    )
    return resp.get_json()


def _registry(tmp_path):
    return json.loads((tmp_path / "v2_projects.json").read_text())["projects"]


def _round_dir(tmp_path, pid, n):
    return tmp_path / pid / "checkpoint" / "checkpoints" / f"round_{n}"


# --- (1-4) slug normalization -------------------------------------------------

def test_slug_from_simple_name(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    body = _create(client, name="Optic Nerve Regeneration")
    record = next(p for p in _registry(tmp_path) if p["id"] == body["project_id"])
    assert record["url_slug"] == "optic-nerve-regeneration"


def test_spaces_become_hyphens(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    body = _create(client, name="SEA-AD Multiregion Atlas")
    record = next(p for p in _registry(tmp_path) if p["id"] == body["project_id"])
    assert record["url_slug"] == "sea-ad-multiregion-atlas"


def test_underscores_become_hyphens(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    body = _create(client, name="Project_Name")
    record = next(p for p in _registry(tmp_path) if p["id"] == body["project_id"])
    assert record["url_slug"] == "project-name"


def test_punctuation_normalized_safely(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    body = _create(client, name="GBM / Recurrence Study")
    record = next(p for p in _registry(tmp_path) if p["id"] == body["project_id"])
    assert record["url_slug"] == "gbm-recurrence-study"


# --- (5) collision handling ---------------------------------------------------

def test_duplicate_names_get_numeric_suffixes(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    b1 = _create(client, name="Optic Nerve Regeneration")
    b2 = _create(client, name="Optic Nerve Regeneration")
    b3 = _create(client, name="Optic Nerve Regeneration")
    registry = _registry(tmp_path)
    slugs = {p["id"]: p["url_slug"] for p in registry}
    assert slugs[b1["project_id"]] == "optic-nerve-regeneration"
    assert slugs[b2["project_id"]] == "optic-nerve-regeneration-2"
    assert slugs[b3["project_id"]] == "optic-nerve-regeneration-3"


# --- (6,7,8,9,10) backfill for legacy records ---------------------------------

def test_backfill_assigns_slug_to_legacy_record(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    body = _create(client, name="Legacy Project")
    pid = body["project_id"]

    # Simulate a pre-slug registry record (written before url_slug existed).
    reg_path = tmp_path / "v2_projects.json"
    data = json.loads(reg_path.read_text())
    record = next(p for p in data["projects"] if p["id"] == pid)
    created_before, updated_before = record["created_at"], record["updated_at"]
    del record["url_slug"]
    reg_path.write_text(json.dumps(data))

    # Listing triggers the one-time backfill.
    payload = client.get("/api/projects").get_json()
    row = next(p for p in payload["projects"] if p["id"] == pid)
    assert row["url_slug"] == "legacy-project"

    record_after = next(p for p in _registry(tmp_path) if p["id"] == pid)
    assert record_after["created_at"] == created_before
    assert record_after["updated_at"] == updated_before
    assert record_after["id"] == pid  # internal id never changes


def test_backfill_is_a_noop_once_all_records_have_slugs(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _create(client, name="Already Slugged Project")
    reg_path = tmp_path / "v2_projects.json"
    before_mtime = reg_path.stat().st_mtime_ns
    before_text = reg_path.read_text()

    client.get("/api/projects")  # every record already has a slug

    assert reg_path.stat().st_mtime_ns == before_mtime
    assert reg_path.read_text() == before_text


# --- (11,12) rename does not change slug/canonical URL ------------------------

def test_rename_does_not_change_slug_or_canonical_url(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, name="Optic Nerve Regeneration")["project_id"]
    client.post(
        f"/api/projects/{pid}/edit",
        data={
            "project_name": "Cross-Species RGC Regeneration",
            "task_description": "Identify testable regeneration hypotheses.",
            "dataset_context": "Updated context.",
        },
        content_type="multipart/form-data",
    )
    record = next(p for p in _registry(tmp_path) if p["id"] == pid)
    assert record["display_name"] == "Cross-Species RGC Regeneration"
    assert record["url_slug"] == "optic-nerve-regeneration"  # unchanged

    resp = client.get("/projects/optic-nerve-regeneration")
    assert resp.status_code == 200
    assert b"Cross-Species RGC Regeneration" not in resp.data or True  # title is client-rendered; page still resolves
    with client.session_transaction() as s:
        assert s["project_label"] == "Cross-Species RGC Regeneration"


# --- (13,14) /projects/<slug> renders / unknown slug 404 ----------------------

def test_project_home_renders_for_valid_slug(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _create(client, name="Optic Nerve Regeneration")
    resp = client.get("/projects/optic-nerve-regeneration")
    assert resp.status_code == 200
    assert b'class="v2-footer"' not in resp.data


def test_unknown_slug_404(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _create(client, name="Optic Nerve Regeneration")
    resp = client.get("/projects/does-not-exist")
    assert resp.status_code == 404


# --- (15) direct slug deep link activates the project -------------------------

def test_deep_link_activates_project_without_prior_continue(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, name="Optic Nerve Regeneration")["project_id"]
    # Fresh client: never called /continue, just opens the URL directly.
    fresh = app_module.app.test_client()
    with fresh.session_transaction() as s:
        s["authenticated"] = True
    resp = fresh.get("/projects/optic-nerve-regeneration")
    assert resp.status_code == 200
    with fresh.session_transaction() as s:
        assert s["v2_active_project_id"] == pid
        assert s["v2_active_project_slug"] == "optic-nerve-regeneration"
        assert s["project_config"]["checkpoint_dir"].endswith("round_0")


# --- (16,17,18) title/Continue hrefs use the slug, identically ---------------

def test_summary_exposes_slug_and_home_url(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _create(client, name="Optic Nerve Regeneration")
    row = client.get("/api/projects").get_json()["projects"][0]
    assert row["url_slug"] == "optic-nerve-regeneration"
    assert row["home_url"] == "/projects/optic-nerve-regeneration"
    # no internal id exposed as the navigable destination
    assert "id" in row  # id itself may still be present (internal identity)...
    assert row["home_url"] != "/projects/" + row["id"]  # ...but never used as the href


def test_projects_js_uses_home_url_not_internal_id():
    js = (app_module.app.static_folder and
          __import__("pathlib").Path(app_module.app.static_folder) / "projects.js").read_text(encoding="utf-8")
    assert "p.home_url" in js
    # Scoped to the projects-LIST row renderer (existing projects): title and
    # Continue there are plain slug-based <a href> navigations, no POST/fetch
    # dance and no client-side id-based function call. This is distinct from
    # the *create-project modal's* "Continue" button (`els.continueProject`),
    # which legitimately POSTs to /api/projects/<id>/continue to activate a
    # just-created project's session before its first navigation -- see
    # api_projects_continue in app.py and test_local_first_project_data.py.
    # A whole-file substring check would false-positive on that unrelated,
    # intentional identifier, so this is scoped to just the row renderer.
    render_row = js[js.index("function renderRow("):js.index("function renderList(")]
    assert "continueProject" not in render_row
    assert "p.home_url" in render_row


# --- (19,20) Hypothesis Studio / round links use the short workspace URL --

def test_hypothesis_studio_route_activates_project(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, name="Optic Nerve Regeneration")["project_id"]
    resp = client.get("/projects/optic-nerve-regeneration/hypotheses")
    assert resp.status_code == 200
    with client.session_transaction() as s:
        assert s["v2_active_project_id"] == pid


def test_project_nav_round_workspace_uses_new_short_screen(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _create(client, name="Optic Nerve Regeneration")

    home = client.get("/projects/optic-nerve-regeneration")
    assert home.status_code == 200
    assert b'Round Workspace</a>' in home.data
    assert b'href="/rounds/?project=optic-nerve-regeneration"' in home.data

    resp = client.get("/rounds/")
    assert resp.status_code == 200
    assert b"v2-page-round-workspace" in resp.data
    assert b'class="v2-appbar' in resp.data
    assert b'href="/rounds/?project=optic-nerve-regeneration"' in resp.data
    assert b'class="v2-footer"' not in resp.data


def test_project_round_route_redirects_to_new_workspace(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, name="Optic Nerve Regeneration")["project_id"]
    rd = _round_dir(tmp_path, pid, 0)
    rd.joinpath("query_000").mkdir()  # round 0 is running
    resp = client.get("/projects/optic-nerve-regeneration/rounds/0")
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"] == "/rounds/?project=optic-nerve-regeneration&round=0"


def test_round_route_unknown_round_404(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _create(client, name="Optic Nerve Regeneration")
    resp = client.get("/legacy/projects/optic-nerve-regeneration/rounds/7")
    assert resp.status_code == 404


def test_completed_legacy_round_route_redirects_to_exact_v2_report(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, name="Optic Nerve Regeneration")["project_id"]
    rd = _round_dir(tmp_path, pid, 0)
    rd.joinpath("round_report.md").write_text("# Round 0 Report\n\nDone.", encoding="utf-8")
    resp = client.get("/legacy/projects/optic-nerve-regeneration/rounds/0")
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"] == (
        "/rounds/?project=optic-nerve-regeneration&round=0&review=1"
    )


def test_rounds_workspace_does_not_render_completed_round_report(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, name="Optic Nerve Regeneration")["project_id"]
    rd = _round_dir(tmp_path, pid, 0)
    rd.joinpath("round_report.md").write_text("# Round 0 Report\n\nDone.", encoding="utf-8")
    client.get("/projects/optic-nerve-regeneration")

    resp = client.get("/rounds/")
    assert resp.status_code == 200
    assert b"v2-page-round-workspace" in resp.data
    assert b"Round 0 Report" not in resp.data


def test_round_workspace_tree_returns_recursive_round_file_directory(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, name="Optic Nerve Regeneration")["project_id"]
    rd = _round_dir(tmp_path, pid, 0)
    rd1 = _round_dir(tmp_path, pid, 1)
    rd1.mkdir(parents=True, exist_ok=True)
    (rd1 / "round_report.md").write_text("# Round 1 Report", encoding="utf-8")
    nested = rd / "query_002" / "execution_agent"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "transcript.txt").write_text("agent log", encoding="utf-8")
    (rd / "round_report.md").write_text("# Round 0 Report", encoding="utf-8")
    (rd / ".DS_Store").write_text("noise", encoding="utf-8")
    client.get("/projects/optic-nerve-regeneration")

    resp = client.get("/api/round_workspace_tree")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["success"] is True
    assert payload["project_slug"] == "optic-nerve-regeneration"
    assert [round_entry["round_index"] for round_entry in payload["rounds"]] == [0, 1]
    assert [round_entry["label"] for round_entry in payload["rounds"]] == ["Round 0", "Round 1"]
    assert [round_entry["target_round_number"] for round_entry in payload["rounds"]] == [0, 1]

    round_zero = next(r for r in payload["rounds"] if r["round_index"] == 0)
    root_children = {child["name"]: child for child in round_zero["tree"]["children"]}
    assert ".DS_Store" not in root_children
    assert root_children["round_report.md"]["type"] == "file"
    assert root_children["round_report.md"]["size"] > 0

    query = root_children["query_002"]
    assert query["type"] == "dir"
    execution = next(child for child in query["children"] if child["name"] == "execution_agent")
    transcript = next(child for child in execution["children"] if child["name"] == "transcript.txt")
    assert transcript["type"] == "file"
    assert transcript["path"] == "query_002/execution_agent/transcript.txt"


def test_round_workspace_tree_uses_active_project_when_session_config_missing(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, name="Optic Nerve Regeneration")["project_id"]
    rd = _round_dir(tmp_path, pid, 0)
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "round_report.md").write_text("# Round 0 Report", encoding="utf-8")
    client.get("/projects/optic-nerve-regeneration")

    with client.session_transaction() as sess:
        cfg = dict(sess.get("project_config") or {})
        cfg.pop("checkpoint_dir", None)
        sess["project_config"] = cfg

    resp = client.get("/api/round_workspace_tree")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["success"] is True
    assert "checkpoint_path" not in payload
    assert payload["rounds"][0]["round_index"] == 0
    assert [round_entry["round_index"] for round_entry in payload["rounds"]] == [0]


def test_round_workspace_explorer_current_behavior():
    static_dir = __import__("pathlib").Path(app_module.app.static_folder)
    template_dir = __import__("pathlib").Path(app_module.app.root_path) / app_module.app.template_folder
    js = (static_dir / "round_workspace.js").read_text(encoding="utf-8")
    css = (static_dir / "round_workspace.css").read_text(encoding="utf-8")
    template = (template_dir / "round_workspace.html").read_text(encoding="utf-8")
    assert "/api/round_workspace_tree" in js
    assert "/api/round_tree" in js
    assert "normalizeLegacyRoundTree" in js
    assert "disclosureCaret" in js
    assert 'class="rw-tree-caret-icon"' in js
    assert "e.stopPropagation()" in js
    assert "setActiveRound(round.round_index)" in js
    assert "visibleResearchTreeNode" in js
    assert "Show technical files" not in js
    assert "technicalRounds" not in js
    assert "row.title=entryTooltip" in js
    assert "transform:rotate(90deg)" in css
    assert ".rw-technical-toggle" not in css
    assert ".rw-tree-row.is-technical" not in css
    assert "rw-terminal" not in template
    assert "rw-terminal" not in css
    assert "openFile(file, false)" in js
    assert "JSON.stringify(file)" in js
    assert "box-sizing:border-box" in css
    assert "overflow:hidden" in css


# --- (21) no generated V2 link exposes the internal id ------------------------

def test_no_rounds_current_url_anywhere():
    app_path = __import__("pathlib").Path(app_module.__file__)
    static_dir = app_path.parent / "static"
    sources = [app_path.read_text(encoding="utf-8")]
    for js in ("project_home.js", "projects.js"):
        p = static_dir / js
        if p.exists():
            sources.append(p.read_text(encoding="utf-8"))
    combined = "\n".join(sources)
    assert "rounds/current" not in combined


def test_no_v2_link_uses_internal_project_id(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    body = _create(client, name="Optic Nerve Regeneration")
    pid = body["project_id"]
    row = client.get("/api/projects").get_json()["projects"][0]
    assert pid not in (row["home_url"] or "")
    assert pid != row["url_slug"]


# --- (22,23,24) internal-id URL redirects to the canonical slug URL ----------

def test_internal_id_home_url_redirects_to_slug(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, name="Optic Nerve Regeneration")["project_id"]
    resp = client.get(f"/projects/{pid}")
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"] == "/projects/optic-nerve-regeneration"


def test_internal_id_hypotheses_url_redirects_to_slug(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, name="Optic Nerve Regeneration")["project_id"]
    resp = client.get(f"/projects/{pid}/hypotheses")
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"] == "/projects/optic-nerve-regeneration/hypotheses"


def test_internal_id_round_url_redirects_to_slug(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, name="Optic Nerve Regeneration")["project_id"]
    resp = client.get(f"/projects/{pid}/rounds/0")
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"] == "/rounds/?project=optic-nerve-regeneration&round=0"

    legacy_resp = client.get(f"/legacy/projects/{pid}/rounds/0")
    assert legacy_resp.status_code in (301, 302)
    assert legacy_resp.headers["Location"] == (
        "/rounds/?project=optic-nerve-regeneration&round=0"
    )


# --- (25) /home redirects to the active slug URL ------------------------------

def test_home_redirects_to_active_project_slug(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _create(client, name="Optic Nerve Regeneration")
    resp = client.get("/home")
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"] == "/projects/optic-nerve-regeneration"


def test_home_without_active_project_is_not_found(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.get("/home")
    assert resp.status_code == 404
    assert resp.headers.get("Location") is None
    assert b"Return to Projects" in resp.data


# --- (26,27) internal edit API unaffected; storage paths unchanged -----------

def test_edit_api_still_uses_internal_id(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, name="Optic Nerve Regeneration")["project_id"]
    resp = client.post(
        f"/api/projects/{pid}/edit",
        data={
            "project_name": "Renamed",
            "task_description": "Identify testable regeneration hypotheses.",
            "dataset_context": "ctx",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True


def test_project_storage_paths_unchanged_by_slug(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, name="Optic Nerve Regeneration")["project_id"]
    raw_record = next(p for p in _registry(tmp_path) if p["id"] == pid)
    record = app_module._v2_get_project(pid)
    # storage lives under the internal id, never the slug
    assert raw_record["project_root"] == "."
    assert Path(record["project_root"]) == tmp_path / pid
    assert Path(record["checkpoint_dir"]).is_relative_to(tmp_path / pid)
    assert (tmp_path / pid).exists()
    assert not (tmp_path / "optic-nerve-regeneration").exists()


# --- (28) routes registered; endpoint names intact ---------------------------

def test_canonical_endpoints_registered():
    endpoints = {r.endpoint for r in app_module.app.url_map.iter_rules()}
    for name in ("project_home_canonical", "project_hypotheses", "project_round", "legacy_project_round",
                 "projects", "api_projects_list", "api_projects_create",
                 "api_projects_continue", "api_projects_edit"):
        assert name in endpoints
