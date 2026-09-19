from da_selection_ui import app as app_module


RUN_ID = "a" * 32


def client_with_project(monkeypatch):
    project = {"id": "project-1", "url_slug": "local-project"}
    monkeypatch.setattr(app_module, "_v2_get_project", lambda project_id: project if project_id == "project-1" else None)
    monkeypatch.setattr(app_module, "_v2_project_availability", lambda _project: {
        "available": True, "registered_path": "/test/project", "root": "/test/project", "message": None,
    })
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["v2_active_project_id"] = "project-1"
    return client, project


def test_start_route_returns_monitor_redirect(monkeypatch):
    client, project = client_with_project(monkeypatch)
    monkeypatch.setattr(
        app_module.local_round_runner,
        "start_round",
        lambda actual, options=None: {"project_id": actual["id"], "round": 0, "run_id": RUN_ID, "status": "starting", "pid": 10},
    )
    response = client.post("/api/rounds/start", json={"project_id": project["id"], "project_root": "/untrusted/client/path"})
    assert response.status_code == 202
    assert response.get_json() == {
        "ok": True,
        "project_id": project["id"],
        "round": 0,
        "run_id": RUN_ID,
        "status": "starting",
        "redirect_url": "/rounds/?project=local-project&round=0&started=1",
    }


def test_start_route_forwards_only_validated_round_overrides(monkeypatch):
    client, _project = client_with_project(monkeypatch)
    seen = {}

    def record(actual, options=None):
        seen["options"] = options
        return {"project_id": actual["id"], "round": 0, "run_id": RUN_ID, "status": "starting", "pid": 10}

    monkeypatch.setattr(app_module.local_round_runner, "start_round", record)
    response = client.post("/api/rounds/start", json={
        "project_id": "project-1",
        "num_queries": 9,
        "deep_research": False,
        "web_search": True,
        "reasoning_model": "claude-sonnet-4-6",
        "execution_model": "claude-haiku-4-5-20251001",
        # Agents that need outside authorization, and a setting that is no
        # longer a choice — all dropped rather than forwarded.
        "opentargets": True,
        "sub_agents": False,
        # Neither a round setting nor something a browser may dictate.
        "working_directory": "/untrusted/client/path",
        "no_sandbox": True,
    })

    assert response.status_code == 202
    assert seen["options"]["num_queries"] == 9
    assert seen["options"]["omit_deepresearch"] is True
    assert seen["options"]["omit_singlesearch"] is False
    assert {seen["options"][field] for field in app_module.model_catalog.REASONING_MODEL_CONFIG_FIELDS} == {
        "claude-sonnet-4-6"
    }
    assert seen["options"]["execution_model"] == "claude-haiku-4-5-20251001"
    assert "working_directory" not in seen["options"]
    assert "no_sandbox" not in seen["options"]
    assert "sub_agent_architecture" not in seen["options"]


def test_options_route_contains_only_supported_per_round_settings(monkeypatch):
    client, _project = client_with_project(monkeypatch)
    monkeypatch.setattr(app_module.local_round_runner, "next_round_number", lambda _project: 0)

    payload = client.get("/api/rounds/options", query_string={"project_id": "project-1"}).get_json()

    assert payload["ok"] is True
    assert {model["id"] for model in payload["models"]} == app_module.model_catalog.known_model_ids()
    assert payload["defaults"]["reasoning_model"] == app_module.model_catalog.DEFAULT_REASONING_MODEL
    assert payload["defaults"]["execution_model"] == app_module.model_catalog.DEFAULT_EXECUTION_MODEL
    assert set(payload["defaults"]) == {
        "num_queries", "query_count", "minimum_queries", "maximum_queries",
        "deep_research", "web_search", "reasoning_model", "execution_model",
    }


def test_options_route_lets_a_retired_project_model_be_replaced_for_the_new_round(monkeypatch):
    client, project = client_with_project(monkeypatch)
    project["project_model"] = "gpt-4o"
    monkeypatch.setattr(app_module.local_round_runner, "next_round_number", lambda _project: 2)

    response = client.get("/api/rounds/options", query_string={"project_id": "project-1"})
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["defaults"]["reasoning_model"] == app_module.model_catalog.DEFAULT_REASONING_MODEL
    assert payload["defaults"]["execution_model"] == app_module.model_catalog.DEFAULT_EXECUTION_MODEL
    assert "gpt-4o" in payload["model_warning"]
    assert "Choose supported models below" in payload["model_warning"]


def test_start_route_rejects_an_out_of_range_browser_override(monkeypatch):
    client, _project = client_with_project(monkeypatch)
    launched = []
    monkeypatch.setattr(
        app_module.local_round_runner,
        "start_round", lambda *_args, **_kwargs: launched.append(True),
    )

    response = client.post("/api/rounds/start", json={"project_id": "project-1", "num_queries": 500})

    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_round_options"
    assert launched == []


def test_start_route_rejects_non_boolean_tools_and_unsupported_models(monkeypatch):
    client, _project = client_with_project(monkeypatch)
    for override in (
        {"deep_research": "false"},
        {"web_search": 1},
        {"reasoning_model": "gpt-4o"},
        {"execution_model": "claude-retired-1"},
    ):
        response = client.post("/api/rounds/start", json={"project_id": "project-1", **override})
        assert response.status_code == 400
        assert response.get_json()["code"] in {"invalid_round_options", "unsupported_project_model"}


def test_start_route_returns_actionable_runner_error(monkeypatch):
    client, _project = client_with_project(monkeypatch)

    def fail(_project, options=None):
        raise app_module.local_round_runner.RoundLaunchError("anthropic_api_key_missing", "Set it and restart.")

    monkeypatch.setattr(app_module.local_round_runner, "start_round", fail)
    response = client.post("/api/rounds/start", json={"project_id": "project-1"})
    assert response.status_code == 400
    assert response.get_json() == {"ok": False, "code": "anthropic_api_key_missing", "message": "Set it and restart."}


def test_start_and_resume_routes_return_missing_key_as_expected_validation(monkeypatch):
    client, _project = client_with_project(monkeypatch)

    def fail(_project, options=None, **_identity):
        raise app_module.local_round_runner.RoundLaunchError(
            "anthropic_api_key_missing",
            app_module.local_round_runner.MISSING_KEY_MESSAGE,
        )

    monkeypatch.setattr(app_module.local_round_runner, "start_round", fail)
    monkeypatch.setattr(app_module.local_round_runner, "resume_round", fail)
    requests = {
        "/api/rounds/start": {"project_id": "project-1"},
        "/api/rounds/resume": {
            "project_id": "project-1", "round": 4, "run_id": RUN_ID,
        },
    }
    for route, identity in requests.items():
        response = client.post(route, json=identity)
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
        lambda project: {
            "project_id": project["id"], "round": 0, "run_id": RUN_ID,
            "status": "running", "pid": 10,
        },
    )
    assert client.get(
        "/api/rounds/status",
        query_string={"project_id": "project-1", "round": 0, "run_id": RUN_ID},
    ).get_json()["status"] == "running"

    empty_client = app_module.app.test_client()
    response = empty_client.post("/api/rounds/start")
    assert response.status_code == 400
    assert response.get_json()["code"] == "no_active_project"


def test_status_route_requires_and_matches_the_exact_run_identity(monkeypatch):
    client, project = client_with_project(monkeypatch)
    monkeypatch.setattr(
        app_module.local_round_runner,
        "round_status",
        lambda actual: {
            "project_id": actual["id"], "round": 0, "run_id": RUN_ID,
            "status": "running", "pid": 10,
        },
    )

    missing = client.get(
        "/api/rounds/status",
        query_string={"project_id": project["id"], "round": 0},
    )
    assert missing.status_code == 409
    assert missing.get_json()["code"] == "run_identity_mismatch"

    stale = client.get(
        "/api/rounds/status",
        query_string={
            "project_id": project["id"], "round": 0, "run_id": "b" * 32,
        },
    )
    assert stale.status_code == 409
    assert stale.get_json()["code"] == "run_identity_mismatch"


def test_stop_resume_and_resubmit_routes_validate_exact_active_run(monkeypatch):
    client, project = client_with_project(monkeypatch)
    seen = []

    def stop(actual, **identity):
        seen.append(("stop", actual["id"], identity))
        return {
            "project_id": actual["id"], "round": 4, "run_id": RUN_ID,
            "status": "interrupted", "pid": None, "resumable": True,
        }

    def resume(actual, **identity):
        seen.append(("resume", actual["id"], identity))
        return {
            "project_id": actual["id"], "round": 4, "run_id": "b" * 32,
            "status": "starting", "pid": 22, "resumed": True,
        }

    def resubmit(actual, **identity):
        seen.append(("resubmit", actual["id"], identity))
        return {
            "project_id": actual["id"], "round": 4, "run_id": "c" * 32,
            "status": "starting", "pid": 23, "resubmitted": True,
        }

    monkeypatch.setattr(app_module.local_round_runner, "stop_round", stop)
    monkeypatch.setattr(app_module.local_round_runner, "resume_round", resume)
    monkeypatch.setattr(app_module.local_round_runner, "resubmit_round", resubmit)
    identity = {"project_id": project["id"], "round": 4, "run_id": RUN_ID}
    assert client.post("/api/rounds/stop", json=identity).get_json()["status"] == "interrupted"
    assert client.post("/api/rounds/resume", json=identity).get_json()["round"] == 4
    resubmitted = client.post("/api/rounds/resubmit", json=identity)
    assert resubmitted.status_code == 202
    assert resubmitted.get_json()["resubmitted"] is True
    assert seen == [
        (action, project["id"], {"expected_round": 4, "expected_run_id": RUN_ID})
        for action in ("stop", "resume", "resubmit")
    ]


def test_mutating_round_routes_reject_missing_or_mismatched_identity(monkeypatch):
    client, _project = client_with_project(monkeypatch)
    monkeypatch.setattr(
        app_module.local_round_runner, "stop_round",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not stop")),
    )
    for payload, code in (
        ({"project_id": "other", "round": 4, "run_id": RUN_ID}, "project_identity_mismatch"),
        ({"project_id": "project-1", "round": "bad", "run_id": RUN_ID}, "round_identity_mismatch"),
        ({"project_id": "project-1", "round": 4, "run_id": "bad"}, "run_identity_mismatch"),
    ):
        response = client.post("/api/rounds/stop", json=payload)
        assert response.status_code == 409
        assert response.get_json()["code"] == code


def test_round_delete_api_is_not_registered(monkeypatch):
    client, _project = client_with_project(monkeypatch)

    assert not hasattr(app_module.local_round_runner, "delete_round")
    assert not any(rule.rule == "/api/rounds/delete" for rule in app_module.app.url_map.iter_rules())
    assert client.post("/api/rounds/delete").status_code == 404
