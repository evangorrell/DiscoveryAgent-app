"""Server-owned Round Workspace chat job lifecycle regressions."""

from __future__ import annotations

from pathlib import Path
import threading

from flask import jsonify, request, session
import pytest

from da_selection_ui import app as app_module


REAL_THREAD = threading.Thread


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


def _cancel(client, project, round_index, job_id):
    return client.post(
        f"/api/round_chat_jobs/{job_id}/cancel"
        f"?project={project['url_slug']}&round={round_index}"
    )


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
            "state": {"rounds": {"4": [
                {
                    "id": "request-a4", "requestId": "request-a4",
                    "role": "user", "text": "What happened?",
                    "queued": True, "jobStatus": "queued",
                },
                {
                    "id": "request-a4:assistant", "requestId": "request-a4",
                    "role": "assistant", "text": "", "pending": True,
                    "jobStatus": "pending",
                },
            ]}},
        },
    )
    assert stale.status_code == 200
    after_stale_write = _round_state(client, project_a)["rounds"]["4"]
    durable_user = next(item for item in after_stale_write if item["role"] == "user")
    durable_answer = next(item for item in after_stale_write if item["role"] == "assistant")
    assert durable_user["jobStatus"] == "succeeded"
    assert durable_user.get("queued") is False
    assert durable_answer["jobStatus"] == "succeeded"
    assert durable_answer["text"] == "Durable Round 4 answer."


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


def test_pending_job_cancel_is_durable_and_prevents_model_start(monkeypatch):
    client = _client()
    project = _create_project(client, "Cancelled Pending Round Chat", rounds=(4,))
    calls = []
    monkeypatch.setattr(
        app_module,
        "api_round_chat",
        lambda: calls.append("called") or jsonify({"success": True, "answer": "late"}),
    )
    accepted = _submit(client, project, 4, "request-cancel-pending")
    job_id = accepted.get_json()["job_id"]

    cancelled = _cancel(client, project, 4, job_id)
    assert cancelled.status_code == 200
    assert cancelled.get_json()["status"] == "cancelled"
    ControlledThread.instances[0].run()

    assert calls == []
    assistant = _round_state(client, project)["rounds"]["4"][-1]
    assert assistant["jobStatus"] == "cancelled"
    assert assistant["pending"] is False
    assert assistant["cancelled"] is True


def test_running_job_cancel_discards_eventual_success(monkeypatch):
    client = _client()
    project = _create_project(client, "Cancelled Running Round Chat", rounds=(4,))
    model_started = threading.Event()
    release_model = threading.Event()

    def slow_round_chat():
        model_started.set()
        assert release_model.wait(5)
        return jsonify({"success": True, "answer": "MUST NOT BE PERSISTED"})

    monkeypatch.setattr(app_module, "api_round_chat", slow_round_chat)
    accepted = _submit(client, project, 4, "request-cancel-running")
    job_id = accepted.get_json()["job_id"]
    worker = REAL_THREAD(target=ControlledThread.instances[0].run)
    worker.start()
    assert model_started.wait(5)

    cancelled = _cancel(client, project, 4, job_id)
    assert cancelled.status_code == 200
    assert cancelled.get_json()["status"] == "cancelled"
    release_model.set()
    worker.join(5)
    assert not worker.is_alive()

    assistant = _round_state(client, project)["rounds"]["4"][-1]
    assert assistant["jobStatus"] == "cancelled"
    assert "MUST NOT BE PERSISTED" not in str(assistant)


def test_cancel_is_project_and_round_isolated():
    client = _client()
    project_a = _create_project(client, "Cancel Isolation A", rounds=(3, 4))
    project_b = _create_project(client, "Cancel Isolation B", rounds=(4,))
    accepted = _submit(client, project_a, 4, "request-isolated-cancel")
    job_id = accepted.get_json()["job_id"]

    assert _cancel(client, project_a, 3, job_id).status_code == 404
    assert _cancel(client, project_b, 4, job_id).status_code == 404
    with app_module.ROUND_CHAT_JOBS_LOCK:
        assert app_module.ROUND_CHAT_JOBS[job_id]["status"] == "pending"


def test_active_job_accepts_followups_as_one_durable_fifo_queue(monkeypatch):
    client = _client()
    project = _create_project(client, "Queued Round Chat", rounds=(4,))
    invocations = []

    def fake_round_chat():
        invocations.append(request.get_json()["request_id"])
        return jsonify({"success": True, "answer": f"Answer {len(invocations)}"})

    monkeypatch.setattr(app_module, "api_round_chat", fake_round_chat)
    first = _submit(client, project, 4, "request-a", "Question A")
    second = _submit(client, project, 4, "request-b", "Question B")
    third = _submit(client, project, 4, "request-c", "Question C")

    assert first.get_json()["status"] == "pending"
    assert second.get_json()["status"] == "queued"
    assert third.get_json()["status"] == "queued"
    assert len(ControlledThread.instances) == 1
    with app_module.ROUND_CHAT_JOBS_LOCK:
        assert [app_module.ROUND_CHAT_JOBS[f"job-request-{letter}"]["status"] for letter in "abc"] == [
            "pending", "queued", "queued",
        ]

    durable = _round_state(client, project)["rounds"]["4"]
    assert [item["text"] for item in durable if item["role"] == "user"] == [
        "Question A", "Question B", "Question C",
    ]
    assert [item.get("queued") for item in durable if item["role"] == "user"] == [
        False, True, True,
    ]

    ControlledThread.instances[0].run()
    assert invocations == ["request-a"]
    assert len(ControlledThread.instances) == 2
    ControlledThread.instances[1].run()
    assert invocations == ["request-a", "request-b"]
    assert len(ControlledThread.instances) == 3
    ControlledThread.instances[2].run()
    assert invocations == ["request-a", "request-b", "request-c"]

    durable = _round_state(client, project)["rounds"]["4"]
    assert [(item["role"], item["text"]) for item in durable] == [
        ("user", "Question A"), ("assistant", "Answer 1"),
        ("user", "Question B"), ("assistant", "Answer 2"),
        ("user", "Question C"), ("assistant", "Answer 3"),
    ]


def test_queued_job_receives_only_completed_prior_exchanges(monkeypatch):
    client = _client()
    project = _create_project(client, "Queued Context", rounds=(4,))
    observed = []

    def fake_round_chat():
        request_id = request.get_json()["request_id"]
        observed.append((
            request_id,
            app_module._round_chat_prior_messages(project["id"], 4, request_id),
        ))
        return jsonify({"success": True, "answer": f"Answer for {request_id}"})

    monkeypatch.setattr(app_module, "api_round_chat", fake_round_chat)
    _submit(client, project, 4, "request-a", "Question A")
    _submit(client, project, 4, "request-b", "Question B")
    _submit(client, project, 4, "request-c", "Question C")

    ControlledThread.instances[0].run()
    ControlledThread.instances[1].run()
    ControlledThread.instances[2].run()

    assert observed[0] == ("request-a", [])
    assert observed[1][0] == "request-b"
    assert observed[1][1] == [
        {"role": "user", "content": "Question A"},
        {"role": "assistant", "content": "Answer for request-a"},
    ]
    assert observed[2][0] == "request-c"
    assert observed[2][1][-2:] == [
        {"role": "user", "content": "Question B"},
        {"role": "assistant", "content": "Answer for request-b"},
    ]


def test_cancelled_active_job_releases_fifo_queue_without_late_overwrite(monkeypatch):
    client = _client()
    project = _create_project(client, "Cancel Advances Queue", rounds=(4,))
    calls = []
    monkeypatch.setattr(
        app_module, "api_round_chat",
        lambda: calls.append(request.get_json()["request_id"])
        or jsonify({"success": True, "answer": "Next answer"}),
    )
    first = _submit(client, project, 4, "request-a", "Question A")
    _submit(client, project, 4, "request-b", "Question B")
    _submit(client, project, 4, "request-c", "Question C")

    cancelled = _cancel(client, project, 4, first.get_json()["job_id"])
    assert cancelled.get_json()["status"] == "cancelled"
    assert len(ControlledThread.instances) == 2
    with app_module.ROUND_CHAT_JOBS_LOCK:
        assert app_module.ROUND_CHAT_JOBS["job-request-b"]["status"] == "pending"
        assert app_module.ROUND_CHAT_JOBS["job-request-c"]["status"] == "queued"
        assert all("paused" not in job for job in app_module.ROUND_CHAT_JOBS.values())
    ControlledThread.instances[0].run()  # The cancelled pending worker is inert.
    ControlledThread.instances[1].run()
    assert len(ControlledThread.instances) == 3
    ControlledThread.instances[2].run()

    assert calls == ["request-b", "request-c"]
    messages = _round_state(client, project)["rounds"]["4"]
    assert "Next answer" in str(messages)
    assert "Question B" in str(messages)
    assert "Question C" in str(messages)
    assert messages[1]["jobStatus"] == "cancelled"


def test_cancel_with_empty_queue_then_submit_starts_next_without_pause(monkeypatch):
    client = _client()
    project = _create_project(client, "Cancel Then Send", rounds=(4,))
    model_started = threading.Event()
    release_model = threading.Event()
    calls = []

    def controlled_round_chat():
        request_id = request.get_json()["request_id"]
        calls.append(request_id)
        if request_id == "request-a":
            model_started.set()
            assert release_model.wait(5)
            return jsonify({"success": True, "answer": "LATE A"})
        return jsonify({"success": True, "answer": "Answer B"})

    monkeypatch.setattr(app_module, "api_round_chat", controlled_round_chat)
    first = _submit(client, project, 4, "request-a", "Question A")
    worker = REAL_THREAD(target=ControlledThread.instances[0].run)
    worker.start()
    assert model_started.wait(5)

    cancelled = _cancel(client, project, 4, first.get_json()["job_id"])
    assert cancelled.get_json()["status"] == "cancelled"
    assert cancelled.get_json()["next_job_id"] is None
    second = _submit(client, project, 4, "request-b", "Question B")

    assert second.get_json()["status"] == "pending"
    assert len(ControlledThread.instances) == 2
    with app_module.ROUND_CHAT_JOBS_LOCK:
        assert app_module.ROUND_CHAT_JOBS["job-request-b"]["status"] == "pending"
        assert all("paused" not in job for job in app_module.ROUND_CHAT_JOBS.values())
    ControlledThread.instances[1].run()
    assert worker.is_alive()
    release_model.set()
    worker.join(5)
    assert not worker.is_alive()
    assert calls == ["request-a", "request-b"]
    assert "LATE A" not in str(_round_state(client, project))
    assert "Answer B" in str(_round_state(client, project))


def test_failed_job_releases_next_and_queue_lanes_are_isolated(monkeypatch):
    client = _client()
    project_a = _create_project(client, "Queue Isolation A", rounds=(3, 4))
    project_b = _create_project(client, "Queue Isolation B", rounds=(4,))
    calls = []

    def fake_round_chat():
        request_id = request.get_json()["request_id"]
        calls.append((session.get("v2_active_project_id"), request.args["round"], request_id))
        if request_id == "request-a-fail":
            return jsonify({"success": False, "error": "controlled failure"}), 500
        return jsonify({"success": True, "answer": request_id})

    monkeypatch.setattr(app_module, "api_round_chat", fake_round_chat)
    _submit(client, project_a, 4, "request-a-fail")
    queued = _submit(client, project_a, 4, "request-a-next")
    other_round = _submit(client, project_a, 3, "request-round-3")
    other_project = _submit(client, project_b, 4, "request-project-b")

    assert queued.get_json()["status"] == "queued"
    assert other_round.get_json()["status"] == "pending"
    assert other_project.get_json()["status"] == "pending"
    assert len(ControlledThread.instances) == 3

    ControlledThread.instances[0].run()
    assert len(ControlledThread.instances) == 4
    ControlledThread.instances[3].run()
    assert calls[0][2] == "request-a-fail"
    assert calls[1][2] == "request-a-next"


def test_duplicate_queued_submission_does_not_duplicate_work(monkeypatch):
    client = _client()
    project = _create_project(client, "Queued Refresh", rounds=(4,))
    monkeypatch.setattr(
        app_module, "api_round_chat",
        lambda: jsonify({"success": True, "answer": "ok"}),
    )
    _submit(client, project, 4, "request-a")
    queued = _submit(client, project, 4, "request-b")
    duplicate = _submit(client, project, 4, "request-b")

    assert queued.get_json()["status"] == "queued"
    assert duplicate.get_json()["job_id"] == queued.get_json()["job_id"]
    assert duplicate.get_json()["status"] == "queued"
    assert len(ControlledThread.instances) == 1
    with app_module.ROUND_CHAT_JOBS_LOCK:
        assert len(app_module.ROUND_CHAT_JOBS) == 2


def test_running_cancel_starts_next_before_old_worker_exits(monkeypatch):
    client = _client()
    project = _create_project(client, "Immediate Cancel Queue", rounds=(4,))
    model_started = threading.Event()
    release_model = threading.Event()
    calls = []
    observed_context = {}

    def controlled_round_chat():
        request_id = request.get_json()["request_id"]
        calls.append(request_id)
        if request_id == "request-a":
            model_started.set()
            assert release_model.wait(5)
            return jsonify({"success": True, "answer": "LATE A"})
        observed_context[request_id] = app_module._round_chat_prior_messages(
            project["id"], 4, request_id,
        )
        return jsonify({"success": True, "answer": "Answer B"})

    monkeypatch.setattr(app_module, "api_round_chat", controlled_round_chat)
    first = _submit(client, project, 4, "request-a", "Question A")
    _submit(client, project, 4, "request-b", "Question B")
    worker = REAL_THREAD(target=ControlledThread.instances[0].run)
    worker.start()
    assert model_started.wait(5)

    cancelled = _cancel(client, project, 4, first.get_json()["job_id"]).get_json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["next_job_id"] == "job-request-b"
    assert worker.is_alive()
    assert len(ControlledThread.instances) == 2
    with app_module.ROUND_CHAT_JOBS_LOCK:
        assert app_module.ROUND_CHAT_JOBS["job-request-a"]["worker_active"] is True
        assert app_module.ROUND_CHAT_JOBS["job-request-a"]["lane_owner"] is False
        assert app_module.ROUND_CHAT_JOBS["job-request-b"]["status"] == "pending"
        assert app_module.ROUND_CHAT_JOBS["job-request-b"]["lane_owner"] is True
    refreshed = client.get(
        f"/api/round_chat_jobs/job-request-b?project={project['url_slug']}&round=4"
    )
    assert refreshed.status_code == 200
    assert refreshed.get_json()["status"] == "pending"

    ControlledThread.instances[1].run()
    assert worker.is_alive()
    assert observed_context["request-b"] == []
    assert calls == ["request-a", "request-b"]
    assert "Answer B" in str(_round_state(client, project))

    release_model.set()
    worker.join(5)
    assert not worker.is_alive()
    assert calls == ["request-a", "request-b"]
    messages = _round_state(client, project)["rounds"]["4"]
    assert "LATE A" not in str(messages)
    assert "Answer B" in str(messages)


def test_running_cancel_promotes_b_and_preserves_c_fifo(monkeypatch):
    client = _client()
    project = _create_project(client, "Running Cancel FIFO", rounds=(4,))
    model_started = threading.Event()
    release_model = threading.Event()
    calls = []

    def controlled_round_chat():
        request_id = request.get_json()["request_id"]
        calls.append(request_id)
        if request_id == "request-a":
            model_started.set()
            assert release_model.wait(5)
            return jsonify({"success": True, "answer": "LATE A"})
        return jsonify({"success": True, "answer": "Answer B"})

    monkeypatch.setattr(app_module, "api_round_chat", controlled_round_chat)
    first = _submit(client, project, 4, "request-a", "Question A")
    _submit(client, project, 4, "request-b", "Question B")
    _submit(client, project, 4, "request-c", "Question C")
    worker = REAL_THREAD(target=ControlledThread.instances[0].run)
    worker.start()
    assert model_started.wait(5)

    cancelled = _cancel(client, project, 4, first.get_json()["job_id"]).get_json()
    assert cancelled["next_job_id"] == "job-request-b"
    assert len(ControlledThread.instances) == 2
    with app_module.ROUND_CHAT_JOBS_LOCK:
        assert app_module.ROUND_CHAT_JOBS["job-request-b"]["status"] == "pending"
        assert app_module.ROUND_CHAT_JOBS["job-request-c"]["status"] == "queued"
        assert all("paused" not in job for job in app_module.ROUND_CHAT_JOBS.values())

    ControlledThread.instances[1].run()
    assert worker.is_alive()
    assert len(ControlledThread.instances) == 3
    with app_module.ROUND_CHAT_JOBS_LOCK:
        assert app_module.ROUND_CHAT_JOBS["job-request-c"]["status"] == "pending"
    ControlledThread.instances[2].run()
    assert worker.is_alive()

    release_model.set()
    worker.join(5)
    assert not worker.is_alive()
    assert calls == ["request-a", "request-b", "request-c"]
    messages = _round_state(client, project)["rounds"]["4"]
    assert "LATE A" not in str(messages)
    assert "Answer B" in str(messages)


def test_reset_disowns_active_worker_clears_queue_and_does_not_advance_it(monkeypatch):
    client = _client()
    project = _create_project(client, "Reset Active Queue", rounds=(4,))
    model_started = threading.Event()
    release_model = threading.Event()
    calls = []

    def controlled_round_chat():
        request_id = request.get_json()["request_id"]
        calls.append(request_id)
        if request_id == "request-a":
            model_started.set()
            assert release_model.wait(5)
            return jsonify({"success": True, "answer": "LATE RESET A"})
        return jsonify({"success": True, "answer": "Fresh answer"})

    monkeypatch.setattr(app_module, "api_round_chat", controlled_round_chat)
    _submit(client, project, 4, "request-a", "Question A")
    _submit(client, project, 4, "request-b", "Question B")
    worker = REAL_THREAD(target=ControlledThread.instances[0].run)
    worker.start()
    assert model_started.wait(5)

    reset = client.post(
        f"/api/round_chat_jobs/reset?project={project['url_slug']}&round=4"
    )
    assert reset.status_code == 200
    assert reset.get_json()["cancelled_job_ids"] == [
        "job-request-a", "job-request-b",
    ]
    assert worker.is_alive()
    assert _round_state(client, project).get("rounds", {}).get("4") is None
    with app_module.ROUND_CHAT_JOBS_LOCK:
        assert app_module.ROUND_CHAT_JOBS["job-request-a"]["status"] == "cancelled"
        assert app_module.ROUND_CHAT_JOBS["job-request-a"]["lane_owner"] is False
        assert "job-request-b" not in app_module.ROUND_CHAT_JOBS

    fresh = _submit(client, project, 4, "request-fresh", "Fresh question")
    assert fresh.get_json()["status"] == "pending"
    assert len(ControlledThread.instances) == 2
    ControlledThread.instances[1].run()
    assert worker.is_alive()
    assert "Fresh answer" in str(_round_state(client, project))

    release_model.set()
    worker.join(5)
    assert not worker.is_alive()
    final_state = _round_state(client, project)
    assert "LATE RESET A" not in str(final_state)
    assert "Question B" not in str(final_state)


def test_reset_is_project_and_round_isolated(monkeypatch):
    client = _client()
    project_a = _create_project(client, "Reset Isolation A", rounds=(3, 4))
    project_b = _create_project(client, "Reset Isolation B", rounds=(4,))
    monkeypatch.setattr(
        app_module, "api_round_chat",
        lambda: jsonify({"success": True, "answer": "answer"}),
    )
    accepted = _submit(client, project_a, 4, "request-a4")

    other_round = client.post(
        f"/api/round_chat_jobs/reset?project={project_a['url_slug']}&round=3"
    )
    other_project = client.post(
        f"/api/round_chat_jobs/reset?project={project_b['url_slug']}&round=4"
    )

    assert other_round.status_code == 200
    assert other_project.status_code == 200
    with app_module.ROUND_CHAT_JOBS_LOCK:
        assert app_module.ROUND_CHAT_JOBS[accepted.get_json()["job_id"]]["status"] == "pending"
        assert app_module.ROUND_CHAT_JOBS[accepted.get_json()["job_id"]]["lane_owner"] is True
    assert "request-a4" in str(_round_state(client, project_a))
