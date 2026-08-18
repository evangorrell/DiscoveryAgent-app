"""Contracts for fully retiring obsolete generic V1 entrypoints."""

import json
from pathlib import Path

import pytest

from da_selection_ui import app as app_module


RETIRED_V1_ROUTES = (
    "/app",
    "/round_monitor",
    "/report",
    "/background_report",
    "/checkpoint_browser",
    "/activity_log",
    "/thank_you",
    "/website_guide",
)


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    return app_module.app.test_client()


def _create(client, name="Retired App Project"):
    response = client.post(
        "/api/projects",
        data={
            "project_name": name,
            "task_description": "Verify obsolete V1 URLs return V2 404s.",
            "dataset_context": "Temporary route-retirement test context.",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    return response.get_json()


def _assert_standard_v2_404(response):
    assert response.status_code == 404
    assert response.headers.get("Location") is None
    html = response.get_data(as_text=True)
    assert "404 &middot; DiscoveryAgent" in html
    assert "Return to Projects" in html
    assert 'class="v2 v2-page-error"' in html


@pytest.mark.parametrize("path", RETIRED_V1_ROUTES)
def test_obsolete_v1_route_is_standard_404_in_fresh_session(monkeypatch, tmp_path, path):
    client = _client(monkeypatch, tmp_path)

    _assert_standard_v2_404(client.get(path))


@pytest.mark.parametrize("path", RETIRED_V1_ROUTES)
def test_obsolete_v1_route_does_not_resolve_active_project_context(
    monkeypatch, tmp_path, path,
):
    client = _client(monkeypatch, tmp_path)
    project = _create(client)
    registry = tmp_path / "v2_projects.json"
    registry_before = registry.read_bytes()
    registry_mtime = registry.stat().st_mtime_ns

    def unexpected_context_resolution(*_args, **_kwargs):
        raise AssertionError(f"{path} inspected or activated project context")

    monkeypatch.setattr(
        app_module, "_v2_resolve_project_by_slug_or_id", unexpected_context_resolution,
    )
    monkeypatch.setattr(
        app_module, "_v2_compatibility_registered_project", unexpected_context_resolution,
    )
    monkeypatch.setattr(app_module, "_v2_activate_v2_project", unexpected_context_resolution)
    monkeypatch.setattr(app_module, "_v2_save_projects", unexpected_context_resolution)

    response = client.get(
        path,
        query_string={
            "project": "retired-app-project",
            "round": "0",
            "type": "round",
        },
    )

    _assert_standard_v2_404(response)
    assert registry.read_bytes() == registry_before
    assert registry.stat().st_mtime_ns == registry_mtime
    assert project["destination"] == "/projects/retired-app-project"


@pytest.mark.parametrize("path", RETIRED_V1_ROUTES)
def test_obsolete_v1_route_ignores_audit_remote_and_legacy_session_state(
    monkeypatch, tmp_path, path,
):
    managed_root = tmp_path / "managed"
    legacy_root = tmp_path / "legacy"
    project_root = tmp_path / "registered-legacy"
    legacy_root.mkdir()
    record = {
        "id": "legacy-route-id",
        "url_slug": "registered-legacy-route",
        "display_name": "Registered Legacy Route",
        "project_root": str(project_root),
        "code_dir": str(project_root / "code"),
        "data_dir": str(project_root / "code" / "data"),
        "checkpoint_dir": str(project_root / "checkpoint"),
        "readme_path": str(project_root / "code" / "data" / "README.md"),
    }
    registry = legacy_root / "v2_projects.json"
    registry.write_text(json.dumps({"projects": [record]}), encoding="utf-8")
    before = registry.read_bytes()
    monkeypatch.setattr(app_module, "get_data_root", lambda: managed_root)
    monkeypatch.setattr(app_module, "get_projects_root", lambda: managed_root / "projects")
    monkeypatch.setattr(
        app_module,
        "get_project_root",
        lambda project_id: managed_root / "projects" / project_id,
    )
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: legacy_root)
    client = app_module.app.test_client()
    with client.session_transaction() as state:
        state["app_mode"] = app_module.APP_MODE_AUDIT
        state["v2_active_project_slug"] = record["url_slug"]
        state["v2_active_project_id"] = record["id"]
        state["checkpoint_s3_prefix"] = "remote/legacy-route"
        state["project_config"] = {
            "checkpoint_dir": record["checkpoint_dir"],
            "checkpoint_s3_prefix": "remote/legacy-route",
        }

    _assert_standard_v2_404(client.get(path, query_string={"round": "7"}))
    assert registry.read_bytes() == before


def test_obsolete_v1_routes_are_unregistered_but_historical_assets_remain():
    root = Path(app_module.app.root_path)
    routes = {rule.rule for rule in app_module.app.url_map.iter_rules()}

    assert set(RETIRED_V1_ROUTES).isdisjoint(routes)
    assert "/checkpoint_assets/<path:filename>" in routes
    assert "/background_report/assets/<path:filename>" in routes
    assert "/report_assets/<root>/<path:filename>" in routes
    assert not (root / "templates" / "index.html").exists()
    assert not (root / "templates" / "activity_log.html").exists()
    assert not (root / "templates" / "website_guide.html").exists()
    assert not (root / "templates" / "report.html").exists()
    assert not (root / "templates" / "background_report.html").exists()
    assert not (root / "static" / "activity_log.js").exists()
    assert not (root / "static" / "script.js").exists()


def test_active_frontend_navigation_does_not_target_obsolete_v1_routes():
    root = Path(app_module.app.root_path)
    for directory, pattern in ((root / "templates", "*.html"), (root / "static", "*.js")):
        for path in directory.glob(pattern):
            text = path.read_text(encoding="utf-8")
            for route in RETIRED_V1_ROUTES:
                assert f'"{route}"' not in text, path
                assert f"'{route}'" not in text, path


def test_current_v2_project_routes_and_exact_round_navigation_remain_healthy(
    monkeypatch, tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    project = _create(client)
    slug = "retired-app-project"

    assert client.get("/projects").status_code == 200
    assert client.get(f"/projects/{slug}").status_code == 200
    assert client.get(f"/projects/{slug}/hypotheses").status_code == 200
    assert client.get(f"/projects/{slug}/skills").status_code == 200
    assert client.get(f"/projects/{slug}/audit").status_code == 200
    assert client.get(f"/rounds/?project={slug}&round=0").status_code == 200
    exact = client.get(f"/projects/{slug}/rounds/0")
    assert exact.status_code == 302
    assert exact.headers["Location"] == f"/rounds/?project={slug}&round=0"
    assert project["destination"] == f"/projects/{slug}"
