"""Server-owned Round Workspace chat job lifecycle regressions."""

from __future__ import annotations

from pathlib import Path

from flask import jsonify, request, session
import pytest

from da_selection_ui import app as app_module


class ControlledThread:
    instances = []

    def __init__(self, target, args=(), daemon=None, **_kwargs):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.started = False
        self.__class__.instances.append(self)

    def start(self):
        self.started = True

    def run(self):
        self.target(*self.args)


@pytest.fixture(autouse=True)
def clear_round_chat_jobs(monkeypatch):
    with app_module.ROUND_CHAT_JOBS_LOCK:
        app_module.ROUND_CHAT_JOBS.clear()
    ControlledThread.instances = []
    monkeypatch.setattr(app_module.threading, "Thread", ControlledThread)
    yield
    with app_module.ROUND_CHAT_JOBS_LOCK:
        app_module.ROUND_CHAT_JOBS.clear()


def _client():
    client = app_module.app.test_client()
    with client.session_transaction() as state:
        state["username"] = "round-chat-job-test"
        state["app_mode"] = app_module.APP_MODE_SELECTION
    return client


def _create_project(client, name, rounds=(4,)):
    response = client.post("/api/projects", data={
        "project_name": name,
        "task_description": f"Investigate {name}.",
        "dataset_context": f"Dataset context for {name}.",
    })
    assert response.status_code == 200
    project_id = response.get_json()["project_id"]
    project = next(item for item in app_module._v2_load_projects() if item["id"] == project_id)
    rounds_root = app_module._v2_round_dirs_root_from_any_checkpoint_path(
        Path(project["checkpoint_dir"])
    )
    for round_index in rounds:
        (rounds_root / f"round_{round_index}").mkdir(parents=True, exist_ok=True)
    return project


def _submit(client, project, round_index, request_id, message="What happened?"):
    return client.post(
        f"/api/round_chat_jobs?project={project['url_slug']}&round={round_index}",
        json={
            "project": project["url_slug"],
            "job_id": f"job-{request_id}",
            "request_id": request_id,
            "user_message_id": request_id,
            "assistant_message_id": f"{request_id}:assistant",
            "message": message,
        },
    )


def _round_state(client, project):
    response = client.get(
        "/api/conversation_state"
        f"?project={project['url_slug']}&surface=round_workspace_chat&scope=main"
    )
    assert response.status_code == 200
    return response.get_json()["state"] or {"rounds": {}}


def test_one_job_continues_after_navigation_and_completes_in_originating_round(monkeypatch):
    client = _client()
    project_a = _create_project(client, "Background Round Chat A", rounds=(4,))
    project_b = _create_project(client, "Background Round Chat B", rounds=(3,))
    invocations = []

    def fake_round_chat():
        invocations.append({
            "project_id": session.get("v2_active_project_id"),
            "round": request.args.get("round"),
            "message": request.get_json()["message"],
        })
        return jsonify({"success": True, "answer": "Durable Round 4 answer."})

    monkeypatch.setattr(app_module, "api_round_chat", fake_round_chat)
    client.post(f"/api/projects/{project_a['id']}/continue")
    accepted = _submit(client, project_a, 4, "request-a4")
    duplicate = _submit(client, project_a, 4, "request-a4")

    assert accepted.status_code == 202
    assert duplicate.status_code == 200
    assert duplicate.get_json()["job_id"] == accepted.get_json()["job_id"]
    assert len(ControlledThread.instances) == 1
    assert invocations == []
    pending = _round_state(client, project_a)["rounds"]["4"][-1]
    assert pending["pending"] is True
    assert pending["jobStatus"] == "pending"

    # Navigation changes the browser session to B; the accepted worker retains
    # A's captured project/round ownership and does not depend on that page.
    client.post(f"/api/projects/{project_b['id']}/continue")
    ControlledThread.instances[0].run()

    assert invocations == [{
        "project_id": project_a["id"],
        "round": "4",
        "message": "What happened?",
    }]
    messages_a = _round_state(client, project_a)["rounds"]["4"]
    assert messages_a[-1]["jobStatus"] == "succeeded"
    assert messages_a[-1]["pending"] is False
    assert messages_a[-1]["text"] == "Durable Round 4 answer."
    assert _round_state(client, project_b).get("rounds", {}) == {}

    # A delayed browser persistence write from before completion cannot replace
    # the server-owned terminal answer with its stale pending placeholder.
    stale = client.put(
        f"/api/conversation_state?project={project_a['url_slug']}",
        json={
            "surface": "round_workspace_chat", "scope": "main",
            "project": project_a["url_slug"],
            "state": {"rounds": {"4": [{
                "id": "request-a4:assistant", "requestId": "request-a4",
                "role": "assistant", "text": "", "pending": True,
                "jobStatus": "pending",
            }]}},
        },
    )
    assert stale.status_code == 200
    after_stale_write = _round_state(client, project_a)["rounds"]["4"][-1]
    assert after_stale_write["jobStatus"] == "succeeded"
    assert after_stale_write["text"] == "Durable Round 4 answer."


def test_two_rounds_keep_independent_pending_and_completed_transcripts(monkeypatch):
    client = _client()
    project = _create_project(client, "Independent Round Chats", rounds=(3, 4))
    calls = []

    def fake_round_chat():
        round_index = request.args["round"]
        calls.append(round_index)
        return jsonify({"success": True, "answer": f"Answer for Round {round_index}."})

    monkeypatch.setattr(app_module, "api_round_chat", fake_round_chat)
    assert _submit(client, project, 4, "request-r4").status_code == 202
    assert _submit(client, project, 3, "request-r3").status_code == 202
    assert len(ControlledThread.instances) == 2

    ControlledThread.instances[0].run()
    state = _round_state(client, project)["rounds"]
    assert state["4"][-1]["text"] == "Answer for Round 4."
    assert state["3"][-1]["pending"] is True
    assert "Answer for Round 4." not in str(state["3"])

    ControlledThread.instances[1].run()
    state = _round_state(client, project)["rounds"]
    assert state["3"][-1]["text"] == "Answer for Round 3."
    assert calls == ["4", "3"]


def test_backend_restart_marks_pending_job_failed_without_resubmission(monkeypatch):
    client = _client()
    project = _create_project(client, "Restarted Round Chat", rounds=(4,))
    calls = []
    monkeypatch.setattr(
        app_module, "api_round_chat",
        lambda: calls.append("called") or jsonify({"success": True, "answer": "unexpected"}),
    )
    accepted = _submit(client, project, 4, "request-restart")
    job_id = accepted.get_json()["job_id"]
    with app_module.ROUND_CHAT_JOBS_LOCK:
        app_module.ROUND_CHAT_JOBS.clear()

    status = client.get(
        f"/api/round_chat_jobs/{job_id}?project={project['url_slug']}&round=4"
    )
    assert status.status_code == 200
    assert status.get_json()["status"] == "failed"
    assert calls == []
    failed = _round_state(client, project)["rounds"]["4"][-1]
    assert failed["failed"] is True
    assert failed["retryable"] is True
    assert "server process restarted" in failed["text"]

    duplicate = _submit(client, project, 4, "request-restart")
    assert duplicate.get_json()["status"] == "failed"
    assert len(ControlledThread.instances) == 1
    assert calls == []


def test_genuine_job_failure_is_durable_and_requires_explicit_new_request(monkeypatch):
    client = _client()
    project = _create_project(client, "Failed Round Chat", rounds=(4,))
    calls = []

    def failing_round_chat():
        calls.append(request.get_json()["request_id"])
        return jsonify({"success": False, "error": "Controlled model failure"}), 500

    monkeypatch.setattr(app_module, "api_round_chat", failing_round_chat)
    _submit(client, project, 4, "request-fail")
    ControlledThread.instances[0].run()
    failed = _round_state(client, project)["rounds"]["4"][-1]
    assert failed["jobStatus"] == "failed"
    assert failed["pending"] is False
    assert failed["retryable"] is True
    assert "Controlled model failure" in failed["text"]

    # Reposting the same request id is an idempotent lookup, not a retry.
    same = _submit(client, project, 4, "request-fail")
    assert same.get_json()["status"] == "failed"
    assert calls == ["request-fail"]
    assert len(ControlledThread.instances) == 1

    # An explicit retry is a new user submission and therefore a new request id.
    _submit(client, project, 4, "request-fail-retry")
    assert len(ControlledThread.instances) == 2
