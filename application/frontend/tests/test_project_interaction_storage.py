"""Managed-project durability and isolation for frontend research interactions."""

from __future__ import annotations

import json
from pathlib import Path

from flask import session

from da_selection_ui import app as app_module
from da_selection_ui.interaction_storage import migrate_legacy_interactions


def _client():
    client = app_module.app.test_client()
    with client.session_transaction() as state:
        state["username"] = "interaction-storage-test"
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
        tmp_path / project_id / ".discoveryagent" / "interactions"
        / "audit_interactions.jsonl"
    )
    assert path == expected
    assert expected.is_file()
    record = json.loads(expected.read_text(encoding="utf-8").strip())
    assert record["project_id"] == project_id
    assert record["event_type"] == "report_chat"
    assert _source_log_snapshot() == source_before


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
    expected = tmp_path / project_id / ".discoveryagent" / "interactions" / "audit_interactions.jsonl"
    assert expected.is_file()


def test_conversation_ui_state_survives_restart_and_is_project_isolated(tmp_path):
    first_client = _client()
    project_a = _create(first_client, "Durable Chat Project A")
    project_b = _create(first_client, "Durable Chat Project B")

    first_client.post(f"/api/projects/{project_a['project_id']}/continue")
    state_a = {
        "version": 1,
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
        "surface": "audit_agent",
        "scope": "main",
        "state": state_a,
    })
    assert saved_a.status_code == 200

    first_client.post(f"/api/projects/{project_b['project_id']}/continue")
    state_b = {
        "version": 1,
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
        "surface": "audit_agent",
        "scope": "main",
        "state": state_b,
    }).status_code == 200

    # Simulate a process/browser restart with no in-memory generator or cookie.
    app_module.GENERATORS.clear()
    restarted = _client()
    restarted.post(f"/api/projects/{project_a['project_id']}/continue")
    restored_a = restarted.get(
        "/api/conversation_state?surface=audit_agent&scope=main"
    ).get_json()["state"]
    assert restored_a == state_a
    assert "conversation B" not in json.dumps(restored_a)

    restarted.post(f"/api/projects/{project_b['project_id']}/continue")
    restored_b = restarted.get(
        "/api/conversation_state?surface=audit_agent&scope=main"
    ).get_json()["state"]
    assert restored_b == state_b
    assert "conversation A" not in json.dumps(restored_b)

    path_a = (
        tmp_path / project_a["project_id"] / ".discoveryagent" / "interactions"
        / "conversation_state.json"
    )
    path_b = (
        tmp_path / project_b["project_id"] / ".discoveryagent" / "interactions"
        / "conversation_state.json"
    )
    assert path_a.is_file() and path_b.is_file() and path_a != path_b


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
        "version": 1,
        "activeChatId": "managed-chat",
        "chats": [{
            "id": "managed-chat",
            "title": "Managed transcript",
            "messages": [{"role": "user", "text": "survives without localStorage"}],
        }],
    }
    client.put("/api/conversation_state", json={
        "surface": "audit_agent",
        "scope": "main",
        "state": state,
    })

    response = client.get(f"/projects/{project_slug}/audit")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "durableChatState" in html
    assert "survives without localStorage" in html


def test_round_workspace_chat_state_is_round_grouped_and_restart_safe(tmp_path):
    client = _client()
    project = _create(client, "Round Chat Persistence")
    project_id = project["project_id"]
    project_slug = next(
        item["url_slug"] for item in app_module._v2_load_projects()
        if item["id"] == project_id
    )
    client.post(f"/api/projects/{project_id}/continue")
    state = {
        "rounds": {
            "0": [{"role": "user", "text": "round zero"}],
            "2": [{"role": "assistant", "text": "round two reply"}],
        }
    }
    assert client.put("/api/conversation_state", json={
        "surface": "round_workspace_chat",
        "scope": "main",
        "state": state,
    }).status_code == 200

    restarted = _client()
    restarted.post(f"/api/projects/{project_id}/continue")
    response = restarted.get(f"/rounds/?project={project_slug}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "durableChatState" in html
    assert "round zero" in html and "round two reply" in html


def test_round_chat_state_write_stays_with_explicit_project_across_navigation(tmp_path):
    client = _client()
    project_a = _create(client, "Explicit Round Chat Project A")
    project_b = _create(client, "Explicit Round Chat Project B")
    records = {item["id"]: item for item in app_module._v2_load_projects()}
    slug_a = records[project_a["project_id"]]["url_slug"]

    # Simulate navigation changing the cookie's active project while a delayed
    # Round Workspace persistence write for A is still in flight.
    client.post(f"/api/projects/{project_b['project_id']}/continue")
    state_a = {
        "rounds": {
            "4": [{"id": "user-4", "role": "user", "text": "Only project A"}],
        },
    }
    saved = client.put(
        f"/api/conversation_state?project={slug_a}",
        json={
            "surface": "round_workspace_chat",
            "scope": "main",
            "project": slug_a,
            "state": state_a,
        },
    )
    assert saved.status_code == 200

    restored = client.get(
        "/api/conversation_state"
        f"?project={slug_a}&surface=round_workspace_chat&scope=main"
    )
    assert restored.status_code == 200
    assert restored.get_json()["state"] == state_a

    project_a_state = (
        tmp_path / project_a["project_id"] / ".discoveryagent" / "interactions"
        / "conversation_state.json"
    )
    project_b_state = (
        tmp_path / project_b["project_id"] / ".discoveryagent" / "interactions"
        / "conversation_state.json"
    )
    assert project_a_state.is_file()
    assert not project_b_state.exists()


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
        tmp_path / project_id / ".discoveryagent" / "interactions"
        / "audit_interactions.jsonl"
    ).exists()


def test_unambiguous_legacy_records_migrate_once_with_exact_order(tmp_path):
    project_root = tmp_path / "projects" / "prj_one"
    project = {
        "id": "prj_one",
        "url_slug": "one",
        "display_name": "One Project",
        "project_root": str(project_root),
    }
    legacy = tmp_path / "legacy" / "audit_interactions.jsonl"
    legacy.parent.mkdir(parents=True)
    lines = [
        json.dumps({"event_type": "chat", "project_label": "One Project", "message": "first"}),
        json.dumps({
            "event_type": "report_chat",
            "checkpoint_dir": str(project_root / "checkpoint" / "checkpoints" / "round_0"),
            "message": "second",
        }),
    ]
    legacy.write_text("\n".join(lines) + "\n", encoding="utf-8")

    first = migrate_legacy_interactions(legacy, [project])
    destination = project_root / ".discoveryagent" / "interactions" / "audit_interactions.jsonl"
    first_content = destination.read_text(encoding="utf-8")
    second = migrate_legacy_interactions(legacy, [project])

    assert first["migrated_by_project"] == {"prj_one": 2}
    assert first_content == "\n".join(lines) + "\n"
    assert destination.read_text(encoding="utf-8") == first_content
    assert second["migrated_by_project"] == {}
    assert second["already_present_by_project"] == {"prj_one": 2}
    assert legacy.read_text(encoding="utf-8") == "\n".join(lines) + "\n"


def test_ambiguous_and_unmatched_legacy_records_are_never_guessed(tmp_path):
    projects = [
        {
            "id": "prj_a",
            "url_slug": "same",
            "display_name": "Duplicate Name",
            "project_root": str(tmp_path / "prj_a"),
        },
        {
            "id": "prj_b",
            "url_slug": "same-2",
            "display_name": "Duplicate Name",
            "project_root": str(tmp_path / "prj_b"),
        },
    ]
    legacy = tmp_path / "legacy.jsonl"
    ambiguous = json.dumps({"event_type": "chat", "project_label": "Duplicate Name"})
    unmatched = json.dumps({"event_type": "chat", "project_label": "Unknown Project"})
    conflicting_ids = json.dumps({
        "event_type": "chat",
        "project_id": "prj_a",
        "v2_active_project_id": "prj_unknown",
    })
    legacy.write_text(
        f"{ambiguous}\n{unmatched}\n{conflicting_ids}\n",
        encoding="utf-8",
    )

    result = migrate_legacy_interactions(legacy, projects)

    assert result["ambiguous_records"] == 2
    assert result["unmatched_records"] == 1
    assert result["migrated_by_project"] == {}
    assert not (tmp_path / "prj_a" / ".discoveryagent" / "interactions").exists()
    assert not (tmp_path / "prj_b" / ".discoveryagent" / "interactions").exists()
    assert legacy.read_text(encoding="utf-8") == (
        f"{ambiguous}\n{unmatched}\n{conflicting_ids}\n"
    )


def test_registered_project_discovery_triggers_idempotent_legacy_migration(tmp_path):
    client = _client()
    created = _create(client, "Discovery Migration")
    project_id = created["project_id"]
    legacy = app_module.LEGACY_INTERACTION_LOG_PATH
    legacy.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({
        "event_type": "audit_chat_restore",
        "project_label": "Discovery Migration",
        "conversation_messages": [{"role": "assistant", "content": "restored"}],
    })
    legacy.write_text(line + "\n", encoding="utf-8")
    app_module._INTERACTION_MIGRATION_STATE.update({"signature": None, "result": None})

    assert client.get("/api/projects").status_code == 200
    destination = tmp_path / project_id / ".discoveryagent" / "interactions" / "audit_interactions.jsonl"
    assert destination.read_text(encoding="utf-8") == line + "\n"
    assert client.get("/api/projects").status_code == 200
    assert destination.read_text(encoding="utf-8") == line + "\n"
    assert legacy.read_text(encoding="utf-8") == line + "\n"
