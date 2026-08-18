from pathlib import Path

from da_selection_ui import app as app_module


def _projects(tmp_path):
    records = {}
    for slug in ("alpha", "beta"):
        root = tmp_path / slug
        code = root / "code"
        round_zero = root / "checkpoint" / "checkpoints" / "round_0"
        (code / "data").mkdir(parents=True)
        round_zero.mkdir(parents=True)
        records[slug] = {
            "id": f"id-{slug}",
            "url_slug": slug,
            "display_name": slug.title(),
            "project_root": str(root),
            "code_dir": str(code),
            "checkpoint_dir": str(round_zero),
            "task_description": f"Study {slug}.",
        }
    return records


def test_api_requests_rebind_project_and_agent_scope_per_tab(monkeypatch, tmp_path):
    records = _projects(tmp_path)
    monkeypatch.setattr(
        app_module,
        "_v2_resolve_project_by_slug_or_id",
        lambda value: (records.get(value), False),
    )
    monkeypatch.setattr(
        app_module,
        "_v2_get_project",
        lambda project_id: next(
            (record for record in records.values() if record["id"] == project_id), None
        ),
    )
    seen = []

    def status(project):
        seen.append((project["url_slug"], app_module.session.get("session_id")))
        return {
            "project_id": project["id"], "round": 0,
            "status": "not_started", "pid": None, "resumable": False,
        }

    monkeypatch.setattr(app_module.local_round_runner, "round_status", status)
    client = app_module.app.test_client()

    alpha = client.get("/api/rounds/status", headers={
        "X-DiscoveryAgent-Project": "alpha",
        "X-DiscoveryAgent-Tab": "aaaaaaaaaaaaaaaa",
    })
    beta = client.get("/api/rounds/status", headers={
        "X-DiscoveryAgent-Project": "beta",
        "X-DiscoveryAgent-Tab": "bbbbbbbbbbbbbbbb",
    })
    alpha_again = client.get("/api/rounds/status", headers={
        "X-DiscoveryAgent-Project": "alpha",
        "X-DiscoveryAgent-Tab": "aaaaaaaaaaaaaaaa",
    })

    assert [response.get_json()["project_id"] for response in (alpha, beta, alpha_again)] == [
        "id-alpha", "id-beta", "id-alpha",
    ]
    assert seen == [
        ("alpha", "tab:aaaaaaaaaaaaaaaa"),
        ("beta", "tab:bbbbbbbbbbbbbbbb"),
        ("alpha", "tab:aaaaaaaaaaaaaaaa"),
    ]


def test_generation_revisions_are_isolated_per_tab():
    tab_alpha = "tab:aaaaaaaaaaaaaaaa"
    tab_beta = "tab:bbbbbbbbbbbbbbbb"
    app_module.GENERATION_IDS.pop(tab_alpha, None)
    app_module.GENERATION_IDS.pop(tab_beta, None)
    try:
        with app_module.app.test_request_context("/"):
            app_module.session["session_id"] = tab_alpha
            app_module.session["generation_id"] = 41
            assert app_module._increment_generation_id() == 1
        with app_module.app.test_request_context("/"):
            app_module.session["session_id"] = tab_beta
            app_module.session["generation_id"] = 41
            assert app_module._increment_generation_id() == 1
        with app_module.app.test_request_context("/"):
            app_module.session["session_id"] = tab_alpha
            assert app_module._increment_generation_id() == 2
    finally:
        app_module.GENERATION_IDS.pop(tab_alpha, None)
        app_module.GENERATION_IDS.pop(tab_beta, None)


def test_round_workspace_query_context_overrides_other_tabs_session(monkeypatch, tmp_path):
    records = _projects(tmp_path)
    monkeypatch.setattr(
        app_module,
        "_v2_resolve_project_by_slug_or_id",
        lambda value: (records.get(value), False),
    )
    monkeypatch.setattr(app_module, "_v2_activate_project", lambda project: project)
    captured = {}
    monkeypatch.setattr(
        app_module,
        "render_template",
        lambda template, **context: captured.update(template=template, **context) or "OK",
    )
    client = app_module.app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["v2_active_project_slug"] = "beta"
        flask_session["v2_active_project_id"] = "id-beta"

    response = client.get("/rounds/?project=alpha")

    assert response.status_code == 200
    assert captured["project_slug"] == "alpha"
    assert captured["checkpoint_path"] == records["alpha"]["checkpoint_dir"]
    assert captured["round_workspace_url"] == "/rounds/?project=alpha"


def test_base_loads_tab_context_before_project_scripts():
    root = Path(app_module.app.root_path)
    base = (root / app_module.app.template_folder / "base.html").read_text(encoding="utf-8")
    script = (root / "static" / "project_context.js").read_text(encoding="utf-8")

    assert base.index("project_context.js") < base.index("hypothesis_activity.js")
    assert base.index("project_context.js") < base.index("{% block scripts %}")
    assert "window.sessionStorage.getItem(storageKey)" in script
    assert "X-DiscoveryAgent-Tab" in script
    assert "X-DiscoveryAgent-Project" in script
    assert "target.origin !== window.location.origin" in script


def test_round_workspace_direct_resources_include_project_context():
    root = Path(app_module.app.root_path)
    script = (root / "static" / "round_workspace.js").read_text(encoding="utf-8")

    assert "const projectQuery" in script
    assert "round_workspace_code_artifact?path=" in script
    assert "checkpoint_assets/round_" in script
    assert "?project=" in script
    assert "round_workspace_markdown?round=" in script


def test_audit_images_include_project_context():
    root = Path(app_module.app.root_path)
    script = (root / "static" / "project_audit_agent.js").read_text(encoding="utf-8")

    assert "?project=${encodeURIComponent(config.projectSlug)}" in script
    assert "/api/audit_chat_figures" in script
    assert "/api/serve_asset" not in script
