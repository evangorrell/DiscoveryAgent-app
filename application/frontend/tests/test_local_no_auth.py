"""Local-only application entry and removed-access-flow contracts."""

import json

from da_selection_ui import app as app_module


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    return app_module.app.test_client()


def test_fresh_session_opens_existing_projects_screen(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.get("/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/projects"

    projects = client.get(response.headers["Location"])
    assert projects.status_code == 200
    assert b'id="projects-root"' in projects.data
    assert b"New Project" in projects.data


def test_fresh_session_can_create_list_and_open_project(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    created = client.post("/api/projects", data={
        "project_name": "Local Project",
        "task_description": "Test a local research objective.",
        "dataset_context": "Local dataset context.",
    }, content_type="multipart/form-data")
    assert created.status_code == 200
    payload = created.get_json()
    listed = client.get("/api/projects").get_json()["projects"]
    assert any(project["id"] == payload["project_id"] for project in listed)
    assert client.get(payload["destination"]).status_code == 200


def test_pages_have_no_access_flow_or_global_footer(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    forbidden = (
        b"Signed in as", b"Log out", b"oqueen@stanford.edu",
        b'class="v2-footer"', b"support-footer", b"account-footer",
        b"Project Key Login", b"Register Username",
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for path in ("/", "/projects"):
        response = client.get(path, follow_redirects=True)
        assert response.status_code == 200, path
        assert all(text not in response.data for text in forbidden), path


def test_projects_page_has_no_settings_ui(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert b"Settings" not in client.get("/projects").data


def test_no_route_redirects_to_removed_login(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    for rule in app_module.app.url_map.iter_rules():
        if "<" in rule.rule or "GET" not in rule.methods or rule.rule.startswith("/static/"):
            continue
        response = client.get(rule.rule)
        assert response.headers.get("Location") != "/login", rule.rule


def test_browser_presence_prevents_duplicate_window_on_restart(monkeypatch, tmp_path):
    presence = tmp_path / "presence.json"
    monkeypatch.setattr(app_module, "_browser_presence_path", lambda _port: presence)
    monkeypatch.setattr(app_module.time, "time", lambda: 1000.0)
    presence.write_text(json.dumps({"current-tab-token": 995.0}), encoding="utf-8")
    opened = []
    monkeypatch.setattr(app_module.webbrowser, "open", opened.append)

    assert app_module._open_browser_if_no_app_tab("http://127.0.0.1:8000/", 8000) is False
    assert opened == []


def test_browser_opens_when_no_current_app_tab(monkeypatch, tmp_path):
    presence = tmp_path / "presence.json"
    monkeypatch.setattr(app_module, "_browser_presence_path", lambda _port: presence)
    monkeypatch.setattr(app_module.time, "time", lambda: 1000.0)
    presence.write_text(json.dumps({"closed-or-stale-tab": 900.0}), encoding="utf-8")
    opened = []
    monkeypatch.setattr(app_module.webbrowser, "open", opened.append)

    assert app_module._open_browser_if_no_app_tab("http://127.0.0.1:8000/", 8000) is True
    assert opened == ["http://127.0.0.1:8000/"]
    assert not presence.exists()


def test_browser_presence_endpoint_tracks_and_closes_each_tab(monkeypatch, tmp_path):
    presence = tmp_path / "presence.json"
    monkeypatch.setattr(app_module, "_browser_presence_path", lambda _port: presence)
    monkeypatch.setattr(app_module.time, "time", lambda: 1000.0)
    client = app_module.app.test_client()
    token = "browser_tab_token_1234"

    assert client.post('/api/browser-presence', json={"token": token}).status_code == 204
    assert json.loads(presence.read_text(encoding="utf-8")) == {token: 1000.0}
    assert client.post('/api/browser-presence', json={"token": token, "action": "close"}).status_code == 204
    assert not presence.exists()


def test_v2_pages_install_browser_presence_heartbeat(monkeypatch, tmp_path):
    page = _client(monkeypatch, tmp_path).get('/projects').get_data(as_text=True)
    assert 'browser_presence.js' in page
