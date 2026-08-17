from da_selection_ui import app as app_module


def client_with_project(monkeypatch):
    project = {"id": "project-1", "url_slug": "local-project"}
    monkeypatch.setattr(app_module, "_v2_get_project", lambda project_id: project if project_id == "project-1" else None)
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["v2_active_project_id"] = "project-1"
    return client, project


def test_start_route_returns_monitor_redirect(monkeypatch):
    client, project = client_with_project(monkeypatch)
    monkeypatch.setattr(
        app_module.local_round_runner,
        "start_round",
        lambda actual, options=None: {"project_id": actual["id"], "round": 0, "status": "starting", "pid": 10},
    )
    response = client.post("/api/rounds/start", json={"project_root": "/untrusted/client/path"})
    assert response.status_code == 202
    assert response.get_json() == {
        "ok": True,
        "project_id": project["id"],
        "round": 0,
        "status": "starting",
        "redirect_url": "/rounds/?project=local-project&round=0&started=1",
    }


def test_start_route_forwards_only_recognized_round_options(monkeypatch):
    client, _project = client_with_project(monkeypatch)
    seen = {}

    def record(actual, options=None):
        seen["options"] = options
        return {"project_id": actual["id"], "round": 0, "status": "starting", "pid": 10}

    monkeypatch.setattr(app_module.local_round_runner, "start_round", record)
    response = client.post("/api/rounds/start", json={
        "num_queries": 9,
        "deep_research": False,
        # Agents that need outside authorization, and a setting that is no
        # longer a choice — all dropped rather than forwarded.
        "opentargets": True,
        "sub_agents": False,
        # Neither a round setting nor something a browser may dictate.
        "working_directory": "/untrusted/client/path",
        "no_sandbox": True,
    })

    assert response.status_code == 202
    assert seen["options"] == {
        "num_queries": 9,
        "omit_deepresearch": True,
    }


def test_options_route_relays_the_live_model_list(monkeypatch):
    client, _project = client_with_project(monkeypatch)
    monkeypatch.setattr(app_module.local_round_runner, "next_round_number", lambda _project: 0)
    monkeypatch.setattr(app_module.model_catalog, "available_models", lambda: {
        "models": [{"id": "claude-opus-9", "display_name": "Claude Opus 9"}],
        "source": "anthropic",
        "error": None,
    })

    payload = client.get("/api/rounds/options").get_json()

    assert payload["ok"] is True
    assert payload["models"] == [{"id": "claude-opus-9", "display_name": "Claude Opus 9"}]
    assert payload["models_source"] == "anthropic"
    assert payload["defaults"]["reasoning_model"]
    assert payload["defaults"]["execution_model"]


def test_options_route_says_when_the_list_is_the_builtin_fallback(monkeypatch):
    client, _project = client_with_project(monkeypatch)
    monkeypatch.setattr(app_module.local_round_runner, "next_round_number", lambda _project: 0)
    monkeypatch.setattr(app_module.model_catalog, "available_models", lambda: {
        "models": [{"id": "claude-opus-5", "display_name": "Claude Opus 5"}],
        "source": "builtin",
        "error": "offline",
    })

    payload = client.get("/api/rounds/options").get_json()

    assert payload["models_source"] == "builtin"
    # The reason the live call failed is not shown to the browser.
    assert "error" not in payload


def test_start_route_rejects_an_out_of_range_query_count(monkeypatch):
    client, _project = client_with_project(monkeypatch)
    monkeypatch.setattr(
        app_module.local_round_runner,
        "start_round",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not launch")),
    )

    response = client.post("/api/rounds/start", json={"num_queries": 500})

    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_round_options"


def test_start_route_returns_actionable_runner_error(monkeypatch):
    client, _project = client_with_project(monkeypatch)

    def fail(_project, options=None):
        raise app_module.local_round_runner.RoundLaunchError("anthropic_api_key_missing", "Set it and restart.")

    monkeypatch.setattr(app_module.local_round_runner, "start_round", fail)
    response = client.post("/api/rounds/start")
    assert response.status_code == 400
    assert response.get_json() == {"ok": False, "code": "anthropic_api_key_missing", "message": "Set it and restart."}


def test_start_and_resume_routes_return_missing_key_as_expected_validation(monkeypatch):
    client, _project = client_with_project(monkeypatch)

    def fail(_project, options=None):
        raise app_module.local_round_runner.RoundLaunchError(
            "anthropic_api_key_missing",
            app_module.local_round_runner.MISSING_KEY_MESSAGE,
        )

    monkeypatch.setattr(app_module.local_round_runner, "start_round", fail)
    monkeypatch.setattr(app_module.local_round_runner, "resume_round", fail)
    for route in ("/api/rounds/start", "/api/rounds/resume"):
        response = client.post(route)
        assert response.status_code == 400
        payload = response.get_json()
        assert payload["ok"] is False
        assert payload["code"] == "anthropic_api_key_missing"
        assert payload["message"].startswith("Anthropic API key not detected")
        assert "ANTHROPIC_API_KEY" in payload["message"]


def test_status_route_and_missing_active_project(monkeypatch):
    client, _project = client_with_project(monkeypatch)
    monkeypatch.setattr(
        app_module.local_round_runner,
        "round_status",
        lambda project: {"project_id": project["id"], "round": 0, "status": "running", "pid": 10},
    )
    assert client.get("/api/rounds/status").get_json()["status"] == "running"

    empty_client = app_module.app.test_client()
    response = empty_client.post("/api/rounds/start")
    assert response.status_code == 400
    assert response.get_json()["code"] == "no_active_project"


def test_stop_resume_and_resubmit_routes_use_active_project_only(monkeypatch):
    client, project = client_with_project(monkeypatch)
    monkeypatch.setattr(app_module.local_round_runner, "stop_round", lambda actual: {
        "project_id": actual["id"], "round": 4, "status": "interrupted", "pid": None, "resumable": True,
    })
    monkeypatch.setattr(app_module.local_round_runner, "resume_round", lambda actual: {
        "project_id": actual["id"], "round": 4, "status": "starting", "pid": 22, "resumed": True,
    })
    monkeypatch.setattr(app_module.local_round_runner, "resubmit_round", lambda actual: {
        "project_id": actual["id"], "round": 4, "status": "starting", "pid": 23, "resubmitted": True,
    })
    assert client.post("/api/rounds/stop", json={"pid": 999, "round": 99}).get_json()["status"] == "interrupted"
    assert client.post("/api/rounds/resume", json={"config_file": "/tmp/evil"}).get_json()["round"] == 4
    resubmitted = client.post("/api/rounds/resubmit", json={"round": 99})
    assert resubmitted.status_code == 202
    assert resubmitted.get_json()["resubmitted"] is True


def test_round_delete_api_is_not_registered(monkeypatch):
    client, _project = client_with_project(monkeypatch)

    assert not hasattr(app_module.local_round_runner, "delete_round")
    assert not any(rule.rule == "/api/rounds/delete" for rule in app_module.app.url_map.iter_rules())
    assert client.post("/api/rounds/delete").status_code == 404
