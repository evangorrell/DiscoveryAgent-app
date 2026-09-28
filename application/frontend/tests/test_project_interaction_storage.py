"""Managed-project durability and isolation for frontend research interactions."""

from __future__ import annotations

import json
from pathlib import Path

from flask import session

from da_selection_ui import app as app_module


def _client():
    client = app_module.app.test_client()
    with client.session_transaction() as state:
        state["app_mode"] = app_module.APP_MODE_SELECTION
    return client


def _create(client, name: str) -> dict:
    response = client.post("/api/projects", data={
        "project_name": name,
        "task_description": f"Investigate {name}.",
        "dataset_context": f"Dataset context for {name}.",
    })
    assert response.status_code == 200
    return response.get_json()


def _project_root(project_id: str) -> Path:
    return Path(app_module._v2_get_project(project_id)["project_root"])


def _write_for(project_id: str, label: str, event_type: str, payload: dict) -> Path:
    with app_module.app.test_request_context("/"):
        session["v2_active_project_id"] = project_id
        session["project_label"] = label
        session["app_mode"] = app_module.APP_MODE_SELECTION
        path = app_module._write_interaction_log(event_type, payload)
    assert path is not None
    return path


def _source_log_snapshot() -> tuple[bool, int | None, int | None]:
    source_log = Path(app_module.__file__).parent / ".interaction_logs" / "audit_interactions.jsonl"
    if not source_log.exists():
        return False, None, None
    stat = source_log.stat()
    return True, stat.st_size, stat.st_mtime_ns


def test_new_interaction_is_project_local_and_never_writes_package_storage(tmp_path):
    client = _client()
    created = _create(client, "Project Local Interactions")
    project_id = created["project_id"]
    source_before = _source_log_snapshot()

    path = _write_for(project_id, "Project Local Interactions", "report_chat", {
        "question": "What persisted?",
        "answer": "This project-local reply.",
        "conversation_messages": [
            {"role": "user", "content": "What persisted?"},
            {"role": "assistant", "content": "This project-local reply."},
        ],
    })

    expected = (
        _project_root(project_id) / ".discoveryagent" / "interactions"
        / "audit_interactions.jsonl"
    )
    assert path == expected
    assert expected.is_file()
    record = json.loads(expected.read_text(encoding="utf-8").strip())
    assert record["project_id"] == project_id
    assert record["event_type"] == "report_chat"
    assert _source_log_snapshot() == source_before


def test_interaction_redaction_preserves_science_and_prior_records(tmp_path):
    client = _client()
    project_id = _create(client, "Redacted Interactions")["project_id"]
    path = _write_for(project_id, "Redacted Interactions", "chat", {
        "message": "Earlier scientific question", "reply": "Earlier answer",
    })
    before = path.read_bytes()
    _write_for(project_id, "Redacted Interactions", "provider_failure", {
        "query": "Ribosomal stoichiometry", "status": "error",
        "nested": {"access_token": "fake-oauth-credential"},
        "error": "Authorization: Bearer fake-header-credential",
    })
    assert path.read_bytes().startswith(before)
    assert "fake-oauth-credential" not in path.read_text()
    assert "fake-header-credential" not in path.read_text()
    saved = json.loads(path.read_text().splitlines()[-1])
    assert saved["query"] == "Ribosomal stoichiometry"
    assert saved["status"] == "error"
    assert saved["project_id"] == project_id


def test_two_managed_projects_have_isolated_interaction_histories(monkeypatch, tmp_path):
    client = _client()
    project_a = _create(client, "Interaction Project A")["project_id"]
    project_b = _create(client, "Interaction Project B")["project_id"]

    path_a = _write_for(project_a, "Interaction Project A", "chat", {
        "message": "Only A",
        "reply": "A reply",
    })
    path_b = _write_for(project_b, "Interaction Project B", "chat", {
        "message": "Only B",
        "reply": "B reply",
    })

    assert path_a != path_b
    assert "Only A" in path_a.read_text(encoding="utf-8")
    assert "Only B" not in path_a.read_text(encoding="utf-8")
    assert "Only B" in path_b.read_text(encoding="utf-8")
    assert "Only A" not in path_b.read_text(encoding="utf-8")

    monkeypatch.setattr(app_module, "ADMIN_TOKEN", "interaction-test-token")
    client.post(f"/api/projects/{project_a}/continue")
    exported_a = client.get(
        "/api/admin/interaction_logs?format=json",
        headers={"X-Admin-Token": "interaction-test-token"},
    ).get_json()
    assert [record["message"] for record in exported_a["records"]] == ["Only A"]

    client.post(f"/api/projects/{project_b}/continue")
    exported_b = client.get(
        "/api/admin/interaction_logs?format=json",
        headers={"X-Admin-Token": "interaction-test-token"},
    ).get_json()
    assert [record["message"] for record in exported_b["records"]] == ["Only B"]


def test_restart_reads_report_conversation_from_project_local_store(monkeypatch, tmp_path):
    first_client = _client()
    created = _create(first_client, "Restartable Interactions")
    project_id = created["project_id"]
    _write_for(project_id, "Restartable Interactions", "report_chat", {
        "question": "Survive restart?",
        "answer": "Yes.",
        "conversation_messages": [
            {"role": "user", "content": "Survive restart?"},
            {"role": "assistant", "content": "Yes."},
        ],
    })

    # A new client has no in-memory generator/session history. Project
    # activation plus the project-local reader must still recover the record.
    app_module.GENERATORS.clear()
    restarted_client = _client()
    assert restarted_client.post(f"/api/projects/{project_id}/continue").status_code == 200
    monkeypatch.setattr(app_module, "ADMIN_TOKEN", "restart-test-token")
    response = restarted_client.get(
        "/api/admin/interaction_logs?format=json",
        headers={"X-Admin-Token": "restart-test-token"},
    )
    assert response.status_code == 200
    records = response.get_json()["records"]
    assert records[0]["conversation_messages"][-1] == {
        "role": "assistant",
        "content": "Yes.",
    }
    expected = _project_root(project_id) / ".discoveryagent" / "interactions" / "audit_interactions.jsonl"
    assert expected.is_file()


def test_conversation_ui_state_survives_restart_and_is_project_isolated(tmp_path):
    first_client = _client()
    project_a = _create(first_client, "Durable Chat Project A")
    project_b = _create(first_client, "Durable Chat Project B")

    first_client.post(f"/api/projects/{project_a['project_id']}/continue")
    state_a = {
        "version": 2,
        "projectId": project_a["project_id"],
        "activeChatId": "chat-a",
        "chats": [{
            "id": "chat-a",
            "title": "Only project A",
            "messages": [
                {"role": "user", "text": "conversation A"},
                {"role": "assistant", "text": "reply A"},
            ],
        }],
    }
    saved_a = first_client.put("/api/conversation_state", json={
        "project_id": project_a["project_id"],
        "surface": "audit_agent",
        "scope": "main",
        "state": state_a,
    })
    assert saved_a.status_code == 200

    first_client.post(f"/api/projects/{project_b['project_id']}/continue")
    state_b = {
        "version": 2,
        "projectId": project_b["project_id"],
        "activeChatId": "chat-b",
        "chats": [{
            "id": "chat-b",
            "title": "Only project B",
            "messages": [
                {"role": "user", "text": "conversation B"},
                {"role": "assistant", "text": "reply B"},
            ],
        }],
    }
    assert first_client.put("/api/conversation_state", json={
        "project_id": project_b["project_id"],
        "surface": "audit_agent",
        "scope": "main",
        "state": state_b,
    }).status_code == 200

    # Simulate a process/browser restart with no in-memory generator or cookie.
    app_module.GENERATORS.clear()
    restarted = _client()
    restarted.post(f"/api/projects/{project_a['project_id']}/continue")
    restored_a = restarted.get(
        f"/api/conversation_state?project_id={project_a['project_id']}&surface=audit_agent&scope=main"
    ).get_json()["state"]
    assert restored_a == state_a
    assert "conversation B" not in json.dumps(restored_a)

    restarted.post(f"/api/projects/{project_b['project_id']}/continue")
    restored_b = restarted.get(
        f"/api/conversation_state?project_id={project_b['project_id']}&surface=audit_agent&scope=main"
    ).get_json()["state"]
    assert restored_b == state_b
    assert "conversation A" not in json.dumps(restored_b)

    path_a = (
        _project_root(project_a["project_id"]) / ".discoveryagent" / "interactions"
        / "conversation_state.json"
    )
    path_b = (
        _project_root(project_b["project_id"]) / ".discoveryagent" / "interactions"
        / "conversation_state.json"
    )
    assert path_a.is_file() and path_b.is_file() and path_a != path_b


def test_audit_history_follows_immutable_id_through_rename_and_relocation(tmp_path):
    client = _client()
    created = _create(client, "Lifecycle Audit Project")
    project_id = created["project_id"]
    client.post(f"/api/projects/{project_id}/continue")
    state = {
        "version": 2,
        "projectId": project_id,
        "activeChatId": "lifecycle-chat",
        "chats": [{
            "id": "lifecycle-chat",
            "title": "Immutable identity",
            "messages": [{"role": "user", "text": "Follow this project only."}],
        }],
    }
    saved = client.put("/api/conversation_state", json={
        "project_id": project_id,
        "surface": "audit_agent",
        "scope": "main",
        "state": state,
    })
    assert saved.status_code == 200

    original = app_module._v2_get_project(project_id)
    destination_parent = Path(original["project_root"]).parent
    edited = client.post(f"/api/projects/{project_id}/edit", data={
        "project_name": "Renamed Lifecycle Audit Project",
        "task_description": "Investigate Lifecycle Audit Project.",
        "dataset_context": "Dataset context for Lifecycle Audit Project.",
        "project_parent_dir": str(destination_parent),
        "use_canonical_default_location": "true",
        "confirm_project_relocation": "true",
    })
    assert edited.status_code == 200, edited.get_data(as_text=True)
    relocated = app_module._v2_get_project(project_id)
    marker = json.loads(
        (Path(relocated["project_root"]) / ".discoveryagent" / "project.json").read_text(
            encoding="utf-8",
        )
    )
    assert marker["project_id"] == project_id

    restored = client.get(
        f"/api/conversation_state?project_id={project_id}&surface=audit_agent&scope=main"
    )
    assert restored.status_code == 200
    assert restored.get_json()["state"] == state


def test_audit_state_rejects_cross_project_and_marker_mismatch(tmp_path):
    client = _client()
    first = _create(client, "Identity Guard A")
    second = _create(client, "Identity Guard B")
    first_id = first["project_id"]
    second_id = second["project_id"]

    client.post(f"/api/projects/{first_id}/continue")
    first_state = {
        "version": 2,
        "projectId": first_id,
        "activeChatId": "first-only",
        "chats": [{"id": "first-only", "messages": [{"role": "user", "text": "A"}]}],
    }
    assert client.put("/api/conversation_state", json={
        "project_id": first_id,
        "surface": "audit_agent",
        "scope": "main",
        "state": first_state,
    }).status_code == 200

    client.post(f"/api/projects/{second_id}/continue")
    cross_read = client.get(
        f"/api/conversation_state?project_id={first_id}&surface=audit_agent&scope=main"
    )
    assert cross_read.status_code == 409
    cross_write = client.put("/api/conversation_state", json={
        "project_id": second_id,
        "surface": "audit_agent",
        "scope": "main",
        "state": {**first_state, "projectId": first_id},
    })
    assert cross_write.status_code == 409

    second_root = Path(app_module._v2_get_project(second_id)["project_root"])
    marker_path = second_root / ".discoveryagent" / "project.json"
    original_marker = marker_path.read_text(encoding="utf-8")
    marker_path.write_text(
        json.dumps({"project_id": first_id, "display_name": "Wrong owner"}),
        encoding="utf-8",
    )
    try:
        unavailable = client.get(
            f"/api/conversation_state?project_id={second_id}&surface=audit_agent&scope=main"
        )
        assert unavailable.status_code == 409
        assert unavailable.get_json()["code"] == "project_identity_unavailable"
    finally:
        marker_path.write_text(original_marker, encoding="utf-8")


def test_audit_jobs_cannot_be_polled_cancelled_or_reused_from_another_project(
    monkeypatch,
):
    client = _client()
    first_id = _create(client, "Job Identity A")["project_id"]
    second_id = _create(client, "Job Identity B")["project_id"]
    job_id = "c" * 32

    class DeferredThread:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def start(self):
            return None

    monkeypatch.setattr(app_module.threading, "Thread", DeferredThread)
    with app_module.AUDIT_CHAT_JOBS_LOCK:
        previous_jobs = dict(app_module.AUDIT_CHAT_JOBS)
        app_module.AUDIT_CHAT_JOBS.clear()
    try:
        client.post(f"/api/projects/{first_id}/continue")
        with client.session_transaction() as state:
            state["session_id"] = "project-job-isolation-session"
        submitted = client.post("/api/audit_chat_jobs", json={
            "project_id": first_id,
            "job_id": job_id,
            "message": "Only project A may receive this.",
            "messages": [],
        })
        assert submitted.status_code == 200
        assert submitted.get_json()["project_id"] == first_id

        client.post(f"/api/projects/{second_id}/continue")
        status = client.get(
            f"/api/audit_chat_jobs/{job_id}?project_id={second_id}"
        )
        assert status.status_code == 404
        cancel = client.post(
            f"/api/audit_chat_jobs/{job_id}/cancel",
            json={"project_id": second_id},
        )
        assert cancel.status_code == 404
        collision = client.post("/api/audit_chat_jobs", json={
            "project_id": second_id,
            "job_id": job_id,
            "message": "Do not adopt A's job.",
            "messages": [],
        })
        assert collision.status_code == 409
        assert collision.get_json()["code"] == "audit_job_identity_conflict"
        with app_module.AUDIT_CHAT_JOBS_LOCK:
            assert app_module.AUDIT_CHAT_JOBS[job_id]["project_id"] == first_id
    finally:
        with app_module.AUDIT_CHAT_JOBS_LOCK:
            app_module.AUDIT_CHAT_JOBS.clear()
            app_module.AUDIT_CHAT_JOBS.update(previous_jobs)


def test_audit_page_bootstraps_from_managed_state_not_browser_storage(tmp_path):
    client = _client()
    project = _create(client, "Server Rendered Audit State")
    project_id = project["project_id"]
    project_slug = next(
        item["url_slug"] for item in app_module._v2_load_projects()
        if item["id"] == project_id
    )
    client.post(f"/api/projects/{project_id}/continue")
    state = {
        "version": 2,
        "projectId": project_id,
        "activeChatId": "managed-chat",
        "chats": [{
            "id": "managed-chat",
            "title": "Managed transcript",
            "messages": [{"role": "user", "text": "survives without localStorage"}],
        }],
    }
    client.put("/api/conversation_state", json={
        "project_id": project_id,
        "surface": "audit_agent",
        "scope": "main",
        "state": state,
    })

    response = client.get(f"/rounds/?project={project_slug}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "durableChatState" in html
    assert "survives without localStorage" in html


def test_report_discussions_are_isolated_by_round_scope(tmp_path):
    client = _client()
    project_id = _create(client, "Round Scoped Report Chats")["project_id"]
    client.post(f"/api/projects/{project_id}/continue")
    round_zero = {
        "reportType": "round",
        "messages": [{"role": "user", "content": "round zero report question"}],
    }
    round_one = {
        "reportType": "round",
        "messages": [{"role": "user", "content": "round one report question"}],
    }
    for scope, state in (("round:0", round_zero), ("round:1", round_one)):
        assert client.put("/api/conversation_state", json={
            "surface": "report_chat",
            "scope": scope,
            "state": state,
        }).status_code == 200

    restored_zero = client.get(
        "/api/conversation_state?surface=report_chat&scope=round:0"
    ).get_json()["state"]
    restored_one = client.get(
        "/api/conversation_state?surface=report_chat&scope=round:1"
    ).get_json()["state"]
    assert restored_zero == round_zero
    assert restored_one == round_one
    assert "round one" not in json.dumps(restored_zero)
    assert "round zero" not in json.dumps(restored_one)


def test_explicit_dev_override_remains_project_namespaced(monkeypatch, tmp_path):
    client = _client()
    created = _create(client, "Override Interactions")
    project_id = created["project_id"]
    override_root = tmp_path / "override-store"
    monkeypatch.setenv(app_module.INTERACTION_LOG_OVERRIDE_ENV, str(override_root))

    path = _write_for(project_id, "Override Interactions", "chat", {
        "message": "isolated override",
        "reply": "still project scoped",
    })

    assert path == override_root / project_id / "audit_interactions.jsonl"
    assert path.is_file()
    assert not (
        _project_root(project_id) / ".discoveryagent" / "interactions"
        / "audit_interactions.jsonl"
    ).exists()
