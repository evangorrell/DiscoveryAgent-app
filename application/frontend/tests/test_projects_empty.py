"""Focused tests for the true zero-project Projects welcome screen."""

from pathlib import Path

from da_selection_ui import app as app_module

JS = Path(app_module.__file__).resolve().parent / "static" / "projects.js"


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
    return client


def _html(client):
    response = client.get("/projects")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _create(client, name="First Investigation"):
    response = client.post(
        "/api/projects",
        data={
            "project_name": name,
            "task_description": "Investigate a test research objective.",
            "dataset_context": "Synthetic context used only by this isolated test.",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    project_id = response.get_json()["project_id"]
    return app_module._v2_get_project(project_id)


def test_empty_registry_returns_no_projects(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.get("/api/projects").get_json()["projects"] == []


def test_zero_projects_render_only_the_welcome_page_identity(monkeypatch, tmp_path):
    html = _html(_client(monkeypatch, tmp_path))

    assert '<h1 class="v2-title v2-app-page-title v2-onboarding-title">Welcome to DiscoveryAgent!</h1>' in html
    assert html.count("<h1") == 1
    assert '<h1 class="v2-title v2-app-page-title">Projects</h1>' not in html
    assert "Start new investigations or continue work across your existing research projects." not in html
    assert 'id="pj-header"' not in html
    assert 'data-projects-empty="true"' in html


def test_welcome_content_and_single_action_follow_visual_and_keyboard_order(monkeypatch, tmp_path):
    html = _html(_client(monkeypatch, tmp_path))
    description = (
        "Give DiscoveryAgent your data and scientific context. Develop hypotheses"
    )
    sequence = [
        "Welcome to DiscoveryAgent!",
        description,
        'id="open-new-project"',
        'class="v2-onboarding-how"',
        "How It Works",
        ">01</span>",
        "Add context",
        ">02</span>",
        "Choose a direction",
        ">03</span>",
        "Run autonomous research",
    ]
    positions = [html.index(token) for token in sequence]

    assert positions == sorted(positions)
    assert html.count('id="open-new-project"') == 1
    assert 'aria-label="Create Your First Project"' in html
    assert '<span class="v2-plus" aria-hidden="true">+</span> Create Your First Project' in html
    assert 'aria-label="New Project"' not in html
    assert 'id="projects-primary-action"' not in html
    assert "v2-sticky-action-tray" not in html
    assert "Turn a dataset into an evolving research program." not in html

    for detail in (
        "Files + scientific context",
        "Generate and compare hypotheses",
        "DiscoveryAgent performs research and presents the evidence to you",
    ):
        assert detail in html


def test_unavailable_registered_project_uses_populated_identity_not_welcome(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project = _create(client, "Unavailable Investigation")
    registered_root = Path(project["project_root"])
    registered_root.rename(tmp_path / "relocated-outside-registry")

    html = _html(client)
    projects = client.get("/api/projects").get_json()["projects"]

    assert len(projects) == 1
    assert projects[0]["location_available"] is False
    assert "Welcome to DiscoveryAgent!" not in html
    assert '<h1 class="v2-title v2-app-page-title">Projects</h1>' in html
    assert "Start new investigations or continue work across your existing research projects." in html
    assert 'data-projects-empty="false"' in html
    assert 'id="projects-primary-action" hidden' in html


def test_creating_first_project_restores_populated_projects_hierarchy(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert "Welcome to DiscoveryAgent!" in _html(client)

    _create(client)
    html = _html(client)

    assert "Welcome to DiscoveryAgent!" not in html
    assert '<h1 class="v2-title v2-app-page-title">Projects</h1>' in html
    assert "Start new investigations or continue work across your existing research projects." in html
    assert html.count('id="open-new-project"') == 1
    assert 'aria-label="New Project"' in html
    assert '<span class="v2-plus" aria-hidden="true">+</span> New Project' in html
    assert "Create Your First Project" not in html
    assert 'id="projects-primary-action" hidden' in html


def test_client_uses_one_modal_binding_and_reloads_across_server_dom_modes():
    source = JS.read_text(encoding="utf-8")

    assert source.count("els.openBtn.addEventListener('click', openCreate)") == 1
    assert "const initialProjectsEmpty" in source
    assert "if (empty !== initialProjectsEmpty)" in source
    assert "window.location.reload()" in source
    assert "if (helper && els.primaryAction)" in source


def test_welcome_layout_is_compact_responsive_and_populated_rows_are_unchanged():
    css = (Path(app_module.__file__).resolve().parent / "static" / "style.css").read_text(encoding="utf-8")
    onboarding = css[css.index(".v2-projects-empty"):css.index("/* ---- Secondary button ---- */")]

    assert "body.v2 .v2-onboarding-title" in onboarding
    assert "font-size: clamp(3.6rem, 6vw, 4.75rem)" in onboarding
    assert ".v2-projects-empty { max-width: 52rem; }" in onboarding
    assert ".v2-onboarding-intro { width: 100%; text-align: center; }" in onboarding
    assert "max-width: 44rem" in onboarding
    assert "margin: 52px auto 0" in onboarding
    assert "padding-bottom: 0" in onboarding
    assert "transform: translateX" not in onboarding
    assert "@media (max-width: 760px)" in onboarding
    assert ".v2-onboarding { margin-top: 32px; }" in onboarding
    assert "grid-template-columns: 1fr" in onboarding
    assert "@media (max-height: 760px)" in onboarding
    assert ".v2-onboarding { margin-top: 28px; }" in onboarding
    assert "width: 100%" in onboarding
    assert ".v2-onboarding-cta .v2-new-project-btn" in onboarding
    assert "max-width: 100%" in onboarding
    assert "white-space: normal" in onboarding
    assert "overflow-wrap: anywhere" in onboarding

    source = JS.read_text(encoding="utf-8")
    assert "v2-project-title" in source
    assert "v2-project-continue" in source
    assert "head.appendChild(buildMenu(p))" in source


def test_footer_is_absent_from_projects(monkeypatch, tmp_path):
    html = _html(_client(monkeypatch, tmp_path))
    assert "v2-footer" not in html
    assert "Log out" not in html
    assert "oqueen@stanford.edu" not in html
