"""Contracts for retiring the standalone Checkpoint Browser into V2."""

import json
from pathlib import Path

from da_selection_ui import app as app_module


def _project_record(tmp_path, *, project_id="managed-id", slug="managed-project"):
    project_root = tmp_path / project_id
    code_dir = project_root / "code"
    data_dir = code_dir / "data"
    checkpoint_dir = project_root / "checkpoint"
    data_dir.mkdir(parents=True)
    checkpoint_dir.mkdir(parents=True)
    readme = data_dir / "README.md"
    readme.write_text("# Project", encoding="utf-8")
    app_module.write_project_metadata(project_root, project_id, slug.replace("-", " ").title())
    return {
        "id": project_id,
        "url_slug": slug,
        "display_name": slug.replace("-", " ").title(),
        "project_root": str(project_root),
        "code_dir": str(code_dir),
        "data_dir": str(data_dir),
        "checkpoint_dir": str(checkpoint_dir),
        "readme_path": str(readme),
    }


def _write_registry(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"projects": records}), encoding="utf-8")


def _managed_client(monkeypatch, tmp_path, records):
    registry = tmp_path / "v2_projects.json"
    _write_registry(registry, records)
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    return app_module.app.test_client(), registry


def test_v2_audit_has_no_round_artifacts_or_checkpoint_browser_action(
    monkeypatch, tmp_path,
):
    record = _project_record(tmp_path)
    client, _registry = _managed_client(monkeypatch, tmp_path, [record])

    response = client.get("/rounds/?project=managed-project")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "AUDIT AGENT" in html
    assert 'id="audit-resource-round-artifacts"' not in html
    assert ">Round Artifacts<" not in html
    assert "/checkpoint_browser" not in html


def test_unresolved_remote_audit_has_no_checkpoint_browser_shortcut(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    client = app_module.app.test_client()
    remote = {
        "checkpoint_s3_prefix": "remote/checkpoints",
        "checkpoint_dir": str(tmp_path / "remote" / "checkpoint"),
        "code_dir": str(tmp_path / "remote" / "code"),
    }
    with client.session_transaction() as flask_session:
        flask_session["app_mode"] = app_module.APP_MODE_AUDIT
        flask_session["project_config"] = dict(remote)
        flask_session["checkpoint_s3_prefix"] = remote["checkpoint_s3_prefix"]

    response = client.get("/audit")

    assert response.status_code == 200
    assert b"/checkpoint_browser" not in response.data


def test_legacy_template_and_tree_file_apis_are_retired():
    root = Path(app_module.app.root_path)
    source = (root / "app.py").read_text(encoding="utf-8")
    rules = {rule.rule for rule in app_module.app.url_map.iter_rules()}

    assert not (root / "templates" / "checkpoint_browser.html").exists()
    assert "render_template('checkpoint_browser.html'" not in source
    assert 'render_template("checkpoint_browser.html"' not in source
    assert "/api/checkpoint_tree" not in rules
    assert "/api/checkpoint_file" not in rules
    client = app_module.app.test_client()
    assert client.get("/api/checkpoint_tree").status_code == 404
    assert client.get("/api/checkpoint_file").status_code == 404


def test_active_checkpoint_assets_and_audit_investigation_remain_available():
    root = Path(app_module.app.root_path)
    source = (root / "app.py").read_text(encoding="utf-8")
    rules = {rule.rule for rule in app_module.app.url_map.iter_rules()}
    guide = root.parent / "guides" / "CHECKPOINT_STRUCTURE.md"

    assert "/checkpoint_assets/<path:filename>" in rules
    assert "def _get_checkpoint_base()" in source
    assert "def _round_workspace_visibility(" in source
    assert "def _build_audit_checkpoint_inventory(" in source
    assert "working_dir = code_dir_path.parent" in source
    assert "CodeChatConversation" in source
    assert guide.is_file()
