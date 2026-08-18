"""Focused contracts for routing legacy Audit contexts into V2 Audit."""

import json
from pathlib import Path

from da_selection_ui import app as app_module


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    return app_module.app.test_client()


def _create(client, name="Audit Migration"):
    response = client.post(
        "/api/projects",
        data={
            "project_name": name,
            "task_description": "Verify V2 Audit migration.",
            "dataset_context": "Temporary Audit migration context.",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    return response.get_json()["project_id"]


def _record(tmp_path, project_id):
    projects = json.loads((tmp_path / "v2_projects.json").read_text())["projects"]
    return next(project for project in projects if project["id"] == project_id)


def test_managed_project_legacy_audit_context_redirects_to_canonical_v2(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _create(client)
    registry_path = tmp_path / "v2_projects.json"
    registry_before = registry_path.read_bytes()
    with client.session_transaction() as flask_session:
        flask_session["app_mode"] = app_module.APP_MODE_AUDIT

    response = client.get("/audit")

    assert response.status_code == 302
    assert response.headers["Location"] == "/projects/audit-migration/audit"
    assert response.headers["Location"] != "/app"
    assert registry_path.read_bytes() == registry_before


def test_non_audit_managed_project_also_redirects_directly_to_v2_audit(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _create(client)

    response = client.get("/audit")

    assert response.status_code == 302
    assert response.headers["Location"] == "/projects/audit-migration/audit"
    assert response.headers["Location"] != "/app"


def test_registered_legacy_project_audit_context_redirects_to_canonical_v2(monkeypatch, tmp_path):
    managed_root = tmp_path / "managed"
    legacy_root = tmp_path / "legacy"
    project_root = tmp_path / "registered-legacy"
    legacy_root.mkdir()
    record = {
        "id": "legacy-audit-id",
        "url_slug": "registered-legacy-audit",
        "display_name": "Registered Legacy Audit",
        "project_root": str(project_root),
        "code_dir": str(project_root / "code"),
        "data_dir": str(project_root / "code" / "data"),
        "checkpoint_dir": str(project_root / "checkpoint" / "checkpoints" / "round_0"),
        "readme_path": str(project_root / "code" / "data" / "README.md"),
    }
    (legacy_root / "v2_projects.json").write_text(
        json.dumps({"projects": [record]}), encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "get_data_root", lambda: managed_root)
    monkeypatch.setattr(app_module, "get_projects_root", lambda: managed_root / "projects")
    monkeypatch.setattr(
        app_module, "get_project_root", lambda project_id: managed_root / "projects" / project_id,
    )
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: legacy_root)
    client = app_module.app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["app_mode"] = app_module.APP_MODE_AUDIT
        flask_session["project_config"] = {"checkpoint_dir": record["checkpoint_dir"]}

    response = client.get("/audit")

    assert response.status_code == 302
    assert response.headers["Location"] == "/projects/registered-legacy-audit/audit"


def test_internal_id_and_path_context_canonicalize_to_slug(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id = _create(client)
    record = app_module._v2_get_project(project_id)
    with client.session_transaction() as flask_session:
        flask_session["app_mode"] = app_module.APP_MODE_AUDIT
        flask_session.pop("v2_active_project_slug", None)
        flask_session["v2_active_project_id"] = project_id
    by_id = client.get("/audit")
    assert by_id.headers["Location"] == "/projects/audit-migration/audit"

    with client.session_transaction() as flask_session:
        flask_session["app_mode"] = app_module.APP_MODE_AUDIT
        flask_session.pop("v2_active_project_slug", None)
        flask_session.pop("v2_active_project_id", None)
        flask_session["project_config"] = {
            "checkpoint_dir": str(Path(record["checkpoint_dir"]).parent / "round_8"),
        }
    by_path = client.get("/audit")
    assert by_path.headers["Location"] == "/projects/audit-migration/audit"


def test_ambiguous_audit_context_is_not_found_without_guessing(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    first = _create(client, "Audit One")
    second = _create(client, "Audit Two")
    shared_code = tmp_path / "shared-code"
    registry_path = tmp_path / "v2_projects.json"
    registry = json.loads(registry_path.read_text())
    for project in registry["projects"]:
        if project["id"] in {first, second}:
            project["code_dir"] = str(shared_code)
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    with client.session_transaction() as flask_session:
        flask_session["app_mode"] = app_module.APP_MODE_AUDIT
        flask_session.pop("v2_active_project_slug", None)
        flask_session.pop("v2_active_project_id", None)
        flask_session["project_config"] = {"code_dir": str(shared_code)}

    response = client.get("/audit")

    assert response.status_code == 404
    assert response.headers.get("Location") is None
    assert "Return to Projects" in response.get_data(as_text=True)


def test_non_audit_unresolved_request_is_not_found_never_app(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.get("/audit")

    assert response.status_code == 404
    assert response.headers.get("Location") is None
    assert "Return to Projects" in response.get_data(as_text=True)
    assert "/app" not in response.get_data(as_text=True)


def test_unresolved_valid_remote_audit_session_keeps_legacy_sync_ui(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    remote_config = {
        "checkpoint_s3_prefix": "unregistered/remote-audit",
        "checkpoint_dir": str(tmp_path / "remote" / "checkpoint"),
        "code_dir": str(tmp_path / "remote" / "code"),
    }
    monkeypatch.setattr(app_module, "_get_project_config", lambda: dict(remote_config))
    with client.session_transaction() as flask_session:
        flask_session["app_mode"] = app_module.APP_MODE_AUDIT
        flask_session["project_config"] = dict(remote_config)
        flask_session["checkpoint_s3_prefix"] = remote_config["checkpoint_s3_prefix"]

    response = client.get("/audit")

    assert response.status_code == 200
    assert b'class="audit-only"' in response.data
    assert b"/api/s3_sync_status" in response.data


def test_v2_audit_renders_only_download_and_guide_actions(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id = _create(client)
    registry_path = tmp_path / "v2_projects.json"
    registry = json.loads(registry_path.read_text())
    record = next(project for project in registry["projects"] if project["id"] == project_id)
    record["github_url"] = "https://github.com/example/discovery-project"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    project_root = tmp_path / project_id
    paper = project_root / "checkpoint" / "research_paper_20260816_120000" / "research_paper.pdf"
    paper.parent.mkdir(parents=True)
    paper.write_bytes(b"%PDF-1.4\n")

    response = client.get("/projects/audit-migration/audit")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="chat-export-current-btn"' in html
    assert '<span>Download</span>' in html
    assert 'id="audit-guide-btn"' in html
    assert '<span>Guide</span>' in html
    for resource in ("readme", "paper", "codebase", "round-artifacts"):
        assert f'id="audit-resource-{resource}"' not in html
    assert "Round Artifacts" not in html
    assert "researchPaperMarkdown" not in html
    assert "audit-paper-modal" not in html
    assert "purify.min.js" in html
    assert "report_figures.js" in html


def test_round_workspace_navigation_is_consistent_across_v2_project_pages(
    monkeypatch, tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    project_id = _create(client)
    project = _record(tmp_path, project_id)
    assert project["checkpoint_dir"]
    expected_href = 'href="/rounds/?project=audit-migration"'

    for path in (
        "/projects/audit-migration",
        "/projects/audit-migration/hypotheses",
        "/projects/audit-migration/skills",
        "/projects/audit-migration/audit",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        html = response.get_data(as_text=True)
        assert expected_href in html, path
        assert "Round Workspace</a>" in html, path
        assert 'title="No research round has started yet">Round Workspace</span>' not in html, path

    workspace = client.get("/rounds/?project=audit-migration")
    assert workspace.status_code == 200
    assert 'id="round-workspace-root"' in workspace.get_data(as_text=True)


def test_audit_keeps_round_workspace_disabled_when_project_is_not_initialized(
    monkeypatch, tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    project_id = _create(client)
    registry_path = tmp_path / "v2_projects.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    record = next(project for project in registry["projects"] if project["id"] == project_id)
    record["checkpoint_dir"] = None
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    response = client.get("/projects/audit-migration/audit")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'title="No research round has started yet">Round Workspace</span>' in html
    assert 'href="/rounds/?project=audit-migration"' not in html


def test_v2_audit_does_not_expose_resource_action_plumbing(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id = _create(client)
    registry_path = tmp_path / "v2_projects.json"
    registry = json.loads(registry_path.read_text())
    record = next(project for project in registry["projects"] if project["id"] == project_id)
    record["github_url"] = "javascript:alert(1)"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    Path(app_module._v2_get_project(project_id)["readme_path"]).unlink()

    response = client.get("/projects/audit-migration/audit")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'class="aa-page-action is-disabled"' not in html
    assert "/readme_view?project=audit-migration" not in html
    assert "/research_paper/pdf?project=audit-migration" not in html
    assert "javascript:alert(1)" not in html


def test_opening_v2_audit_does_not_persist_an_empty_chat(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id = _create(client)
    state = {
        "version": 1,
        "activeChatId": "prior-chat",
        "chats": [{
            "id": "prior-chat",
            "title": "Prior audit",
            "messages": [{"role": "user", "text": "Keep this history"}],
        }],
    }
    saved = client.put(
        "/api/conversation_state?project=audit-migration",
        json={
            "surface": "audit_agent",
            "scope": "main",
            "project": "audit-migration",
            "state": state,
        },
    )
    assert saved.status_code == 200
    state_path = (
        tmp_path / project_id / ".discoveryagent" / "interactions"
        / "conversation_state.json"
    )
    before = state_path.read_bytes()

    response = client.get("/projects/audit-migration/audit")

    assert response.status_code == 200
    assert state_path.read_bytes() == before
    restored = client.get(
        "/api/conversation_state"
        "?project=audit-migration&surface=audit_agent&scope=main"
    )
    assert restored.status_code == 200
    assert restored.get_json()["state"] == state
