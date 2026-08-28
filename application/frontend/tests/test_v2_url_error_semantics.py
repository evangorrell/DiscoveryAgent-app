"""Focused contracts for V2 URL resolution and the standard 404 experience."""

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


def _create_project(client, name="URL Semantics"):
    response = client.post(
        "/api/projects",
        data={
            "project_name": name,
            "task_description": "Verify exact V2 URL resolution.",
            "dataset_context": "A focused local routing fixture.",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    return response.get_json()


def _assert_standard_v2_404(response):
    assert response.status_code == 404
    assert response.headers.get("Location") is None
    html = response.get_data(as_text=True)
    assert 'class="v2-error-page"' in html
    assert 'id="error-code">404<' in html
    assert 'href="/projects">Return to Projects<' in html


@pytest.mark.parametrize(
    "path",
    (
        "/definitely-not-real",
        "/projects/definitely-not-real",
        "/projects/definitely-not-real/hypotheses",
        "/projects/definitely-not-real/audit",
        "/projects/definitely-not-real/skills",
        "/rounds/?project=definitely-not-real",
        "/projects/definitely-not-real/rounds/0",
        "/legacy/projects/definitely-not-real/rounds/0",
    ),
)
def test_unrecognized_urls_use_the_standard_v2_404(monkeypatch, tmp_path, path):
    client = _client(monkeypatch, tmp_path)

    _assert_standard_v2_404(client.get(path))


def test_bad_explicit_url_is_not_rescued_by_active_project(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project = _create_project(client)
    assert client.get(project["destination"]).status_code == 200

    for path in (
        "/projects/definitely-not-real",
        "/projects/definitely-not-real/audit",
        "/rounds/?project=definitely-not-real",
        "/rounds/?project=",
        "/rounds/?project=%20%20",
    ):
        _assert_standard_v2_404(client.get(path))

    with client.session_transaction() as flask_session:
        assert flask_session["v2_active_project_id"] == project["project_id"]
        assert flask_session["v2_active_project_slug"] == "url-semantics"


def test_valid_project_pages_and_exact_round_remain_healthy(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project = _create_project(client)
    slug = "url-semantics"

    for path in (
        project["destination"],
        f"/projects/{slug}/hypotheses",
        f"/projects/{slug}/skills",
        f"/rounds/?project={slug}",
        f"/rounds/?project={slug}&round=0",
    ):
        assert client.get(path).status_code == 200, path

    exact = client.get(f"/projects/{slug}/rounds/0")
    assert exact.status_code == 302
    assert exact.headers["Location"] == f"/rounds/?project={slug}&round=0"
    legacy = client.get(f"/legacy/projects/{slug}/rounds/0")
    assert legacy.status_code == 302
    assert legacy.headers["Location"] == f"/rounds/?project={slug}&round=0"


def test_valid_project_with_missing_explicit_round_is_standard_404(
    monkeypatch, tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    _create_project(client)

    for path in (
        "/rounds/?project=url-semantics&round=999",
        "/projects/url-semantics/rounds/999",
        "/legacy/projects/url-semantics/rounds/999",
    ):
        _assert_standard_v2_404(client.get(path))


@pytest.mark.parametrize("round_value", ("not-a-number", "-1", "1.5", "+1"))
def test_malformed_explicit_round_is_standard_404(
    monkeypatch, tmp_path, round_value,
):
    client = _client(monkeypatch, tmp_path)
    _create_project(client)

    _assert_standard_v2_404(
        client.get(f"/rounds/?project=url-semantics&round={round_value}")
    )


def test_supported_internal_id_links_canonicalize(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project = _create_project(client)
    project_id = project["project_id"]

    expected = {
        f"/projects/{project_id}": "/projects/url-semantics",
        f"/projects/{project_id}/hypotheses": "/projects/url-semantics/hypotheses",
        f"/projects/{project_id}/skills": "/projects/url-semantics/skills",
    }
    for path, location in expected.items():
        response = client.get(path)
        assert response.status_code == 302, path
        assert response.headers["Location"] == location


def test_invalid_explicit_urls_do_not_activate_or_mutate_another_project(
    monkeypatch, tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    project_a = _create_project(client, "Project Alpha")
    project_b = _create_project(client, "Project Beta")
    assert client.get(project_a["destination"]).status_code == 200

    registry_path = tmp_path / "projects.json"
    registry_before = registry_path.read_bytes()
    with client.session_transaction() as flask_session:
        session_before = dict(flask_session)

    invalid_requests = (
        ("/definitely-not-real?project=project-beta", {}),
        ("/projects/definitely-not-real?project=project-beta", {}),
        (
            "/definitely-not-real",
            {
                "headers": {
                    "X-DiscoveryAgent-Project": "project-beta",
                    "X-DiscoveryAgent-Tab": "truthful-url-test-token",
                },
            },
        ),
    )
    for path, kwargs in invalid_requests:
        _assert_standard_v2_404(client.get(path, **kwargs))
        assert registry_path.read_bytes() == registry_before
        with client.session_transaction() as flask_session:
            assert dict(flask_session) == session_before

    assert project_b["destination"] == "/projects/project-beta"


def test_home_compatibility_validates_explicit_project_before_session_fallback(
    monkeypatch, tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    project_a = _create_project(client, "Project Alpha")
    project_b = _create_project(client, "Project Beta")
    assert client.get(project_a["destination"]).status_code == 200

    _assert_standard_v2_404(client.get("/home?project=definitely-not-real"))
    explicit = client.get(f"/home?project={project_b['project_id']}")
    assert explicit.status_code == 302
    assert explicit.headers["Location"] == "/projects/project-beta"


def test_invalid_explicit_audit_context_cannot_fall_back_to_remote_session(
    monkeypatch, tmp_path,
):
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

    for path in (
        "/audit?project=definitely-not-real",
        "/audit?project=",
        "/audit?project_slug=definitely-not-real",
        "/audit?project_id=definitely-not-real",
    ):
        _assert_standard_v2_404(client.get(path))


def test_conflicting_explicit_audit_identifiers_are_not_guessed(
    monkeypatch, tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    first = _create_project(client, "Project Alpha")
    second = _create_project(client, "Project Beta")

    _assert_standard_v2_404(client.get(
        "/audit",
        query_string={
            "project_slug": "project-alpha",
            "project_id": second["project_id"],
        },
    ))
    response = client.get(
        "/audit",
        query_string={
            "project_slug": "project-alpha",
            "project_id": first["project_id"],
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/rounds/?project=project-alpha"


def test_invalid_report_view_is_a_missing_resource(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _create_project(client)

    _assert_standard_v2_404(client.get(
        "/projects/url-semantics/rounds/0/report/download?view=unknown"
    ))


def test_harmless_unknown_query_parameter_remains_tolerated(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.get("/projects?campaign=demo")

    assert response.status_code == 200


def test_intentional_root_navigation_and_retired_routes_are_unchanged(
    monkeypatch, tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    root = client.get("/")
    assert root.status_code == 302
    assert root.headers["Location"] == "/projects"

    for path in RETIRED_V1_ROUTES:
        _assert_standard_v2_404(client.get(path))


def test_unresolved_context_only_compatibility_urls_are_not_found(
    monkeypatch, tmp_path,
):
    client = _client(monkeypatch, tmp_path)

    for path in ("/home", "/audit", "/rounds/"):
        _assert_standard_v2_404(client.get(path))


def test_retired_v1_routes_remain_unregistered():
    registered = {rule.rule for rule in app_module.app.url_map.iter_rules()}

    assert not set(RETIRED_V1_ROUTES) & registered
