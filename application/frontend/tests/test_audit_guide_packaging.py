"""Regression coverage for the packaged Audit Agent guide."""

import os
from pathlib import Path
import tomllib

from da_selection_ui import app as app_module
from da_selection_ui import guide_resources


ROOT = Path(__file__).resolve().parents[1]
PACKAGED_GUIDE = ROOT / "da_selection_ui" / "guides" / "AUDIT_UI.md"


def _authenticated_client():
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
    return client


def _create_project(client):
    response = client.post(
        "/api/projects",
        data={
            "project_name": "Packaged Audit Guide",
            "task_description": "Verify packaged Audit Agent guide rendering.",
            "dataset_context": "Temporary test context.",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    project_id = response.get_json()["project_id"]
    projects = client.get("/api/projects").get_json()["projects"]
    return next(project for project in projects if project["id"] == project_id)


def test_wheel_configuration_explicitly_includes_packaged_audit_guide():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    force_include = config["tool"]["hatch"]["build"]["targets"]["wheel"][
        "force-include"
    ]

    assert force_include["da_selection_ui/guides/AUDIT_UI.md"] == (
        "da_selection_ui/guides/AUDIT_UI.md"
    )
    assert PACKAGED_GUIDE.is_file()


def test_packaged_resource_load_is_independent_of_repository_cwd(tmp_path):
    original_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        guide = guide_resources.read_audit_guide_markdown()
    finally:
        os.chdir(original_cwd)

    assert guide is not None
    assert "# Audit UI Guide" in guide
    assert "read-only review workspace" in guide
    assert "checkpoints browser" not in guide.lower()
    assert "Ask the Audit Agent to inspect supporting context" in guide
    assert "Ask the Audit Agent to summarize the README" in guide


def test_project_audit_route_renders_packaged_guide(monkeypatch, tmp_path):
    client = _authenticated_client()
    project = _create_project(client)
    original_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        response = client.get(f"/projects/{project['url_slug']}/audit")
    finally:
        os.chdir(original_cwd)

    assert response.status_code == 200
    assert b'id="audit-guide-btn"' in response.data
    assert b'id="audit-guide-modal"' in response.data
    assert b"Audit UI Guide" in response.data
    assert b"read-only review workspace" in response.data


def test_project_resolvable_legacy_audit_redirects_to_v2_packaged_guide():
    client = _authenticated_client()
    project = _create_project(client)
    with client.session_transaction() as session:
        session["app_mode"] = app_module.APP_MODE_AUDIT

    redirect_response = client.get("/audit")
    assert redirect_response.status_code == 302
    assert redirect_response.headers["Location"] == f"/projects/{project['url_slug']}/audit"

    response = client.get(redirect_response.headers["Location"])

    assert response.status_code == 200
    assert b'id="audit-guide-btn"' in response.data
    assert b'id="audit-guide-modal"' in response.data
    assert b"Audit UI Guide" in response.data
    assert b"read-only review workspace" in response.data


def test_v2_and_residual_legacy_audit_use_the_central_packaged_guide_helper():
    source = (ROOT / "da_selection_ui" / "app.py").read_text(encoding="utf-8")

    assert source.count("audit_guide_html = _load_audit_guide_html()") == 2
    assert "parent.parent / 'guides' / 'AUDIT_UI.md'" not in source
    assert 'parent.parent / "guides" / "AUDIT_UI.md"' not in source


def test_missing_packaged_guide_is_safe_for_loader_and_route(monkeypatch):
    def missing_resource(_package):
        raise FileNotFoundError("guide omitted")

    monkeypatch.setattr(guide_resources.resources, "files", missing_resource)
    assert guide_resources.read_audit_guide_markdown() is None

    monkeypatch.setattr(app_module, "read_audit_guide_markdown", lambda: None)
    client = _authenticated_client()
    project = _create_project(client)
    response = client.get(f"/projects/{project['url_slug']}/audit")

    assert response.status_code == 200
    assert b'id="audit-guide-btn"' not in response.data
    assert b'id="audit-guide-modal"' not in response.data
