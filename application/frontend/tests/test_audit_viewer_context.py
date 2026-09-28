"""Focused contracts for silent, message-owned Audit viewer context."""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from flask import session

from da_selection_ui import app as app_module


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + (b"viewer-context" * 8)


def test_failed_audit_chat_is_persisted_as_error_not_assistant_answer(monkeypatch):
    import json
    client = app_module.app.test_client()
    project, _ = _create_project_with_viewer_artifacts(client)
    generator = SimpleNamespace(
        code_dir=project["code_dir"], checkpoint_dir=project["checkpoint_dir"],
        report_code_chat_conversations={}, memory_report_summary="", memory_report="",
        research_paper_path=None, citation_documents=[],
    )
    monkeypatch.setattr(app_module, "get_or_restore_generator", lambda: generator)
    monkeypatch.setattr(app_module, "_run_code_chat_with_timeout", lambda *a, **kw: {
        "error": "Provider unavailable; password=fake-error-secret",
        "error_type": "provider_error", "answer": "",
    })
    response = client.post("/api/chat", json={
        "project_id": project["id"], "mode": "audit",
        "message": "Explain the ribosomal result", "messages": [],
    })
    assert response.status_code == 200
    assert response.get_json()["success"] is False
    path = Path(project["project_root"]) / ".discoveryagent/interactions/audit_interactions.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    failures = [event for event in events if event["event_type"] == "provider_failure"]
    assert len(failures) == 1
    assert failures[0]["message"] == "Explain the ribosomal result"
    assert failures[0]["status"] == "error"
    assert failures[0]["provider"] == "claude_agent_sdk"
    assert "fake-error-secret" not in path.read_text()
    assert not any(event["event_type"] == "chat" for event in events)


def _create_project_with_viewer_artifacts(client, name="Viewer Context Project"):
    response = client.post(
        "/api/projects",
        data={
            "project_name": name,
            "task_description": "Audit evidence across rounds.",
            "dataset_context": "A viewer-context validation project.",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    project = app_module._v2_get_project(response.get_json()["project_id"])
    assert project is not None
    rounds_root = app_module._v2_round_dirs_root_from_any_checkpoint_path(
        Path(project["checkpoint_dir"])
    )
    for index in (0, 1):
        round_dir = rounds_root / f"round_{index}"
        figure_dir = round_dir / "figures"
        figure_dir.mkdir(parents=True, exist_ok=True)
        (figure_dir / "active.png").write_bytes(PNG_BYTES + bytes([index]))
        (figure_dir / "collapsed.png").write_bytes(PNG_BYTES + bytes([index + 2]))
        (round_dir / "round_report.md").write_text(
            f"# Round {index}\n\nEvidence unique to round {index}.\n\n"
            "![Active server caption](figures/active.png)\n\n"
            "![Collapsed server caption](figures/collapsed.png)\n",
            encoding="utf-8",
        )
        (round_dir / "round_report_summary.md").write_text(
            f"# Round {index} Summary\n\nConcise evidence for round {index}.\n",
            encoding="utf-8",
        )
    client.get(f"/rounds/?project={project['url_slug']}&round=0")
    return project, rounds_root


@contextmanager
def _client_request_context(client, path="/"):
    with client.session_transaction() as stored:
        session_snapshot = dict(stored)
    with app_module.app.test_request_context(path):
        session.update(session_snapshot)
        yield


def _checkpoint_source(round_index):
    registry = app_module._round_workspace_artifact_registry(round_index)
    return next(
        source_id for source_id, source in registry.items()
        if source.get("kind") == "checkpoint"
    )


def _report_snapshot(project, source, *, round_index=0, figure_indexes=None, report_view="full"):
    return {
        "version": 1,
        "capturedAt": 1234,
        "project": project["url_slug"],
        "artifacts": [{
            "pane": 0,
            "project": project["url_slug"],
            "round": round_index,
            "sourceKind": "checkpoint",
            "source": source,
            "path": "round_report.md",
            "artifactPath": "round_report.md",
            "artifactType": "markdown",
            "reportView": report_view,
            "figures": [{"index": index} for index in (figure_indexes or [])],
        }],
    }


def test_server_canonicalizes_active_text_and_server_enumerated_figure(tmp_path):
    client = app_module.app.test_client()
    project, rounds_root = _create_project_with_viewer_artifacts(client)
    with _client_request_context(client):
        source = _checkpoint_source(0)
        resolved = app_module._canonicalize_audit_viewer_context(
            _report_snapshot(project, source, figure_indexes=[0])
        )

    assert len(resolved) == 1
    artifact = resolved[0]
    assert artifact["target"] == (rounds_root / "round_0" / "round_report.md").resolve()
    assert artifact["round"] == 0
    assert artifact["artifact_type"] == "markdown"
    assert len(artifact["figures"]) == 1
    assert artifact["figures"][0]["target"] == (
        rounds_root / "round_0" / "figures" / "active.png"
    ).resolve()
    assert artifact["figures"][0]["caption"] == "Active server caption"


def test_summary_variant_is_resolved_server_side(tmp_path):
    client = app_module.app.test_client()
    project, rounds_root = _create_project_with_viewer_artifacts(client)
    with _client_request_context(client):
        source = _checkpoint_source(0)
        resolved = app_module._canonicalize_audit_viewer_context(
            _report_snapshot(project, source, report_view="summary")
        )
    assert resolved[0]["target"] == (
        rounds_root / "round_0" / "round_report_summary.md"
    ).resolve()
    assert resolved[0]["report_view"] == "summary"


def test_invalid_stale_absolute_traversal_symlink_and_cross_project_hints_fail_closed(tmp_path):
    client = app_module.app.test_client()
    project, rounds_root = _create_project_with_viewer_artifacts(client)
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG_BYTES)
    symlink = rounds_root / "round_0" / "figures" / "escape.png"
    symlink.symlink_to(outside)

    with _client_request_context(client):
        source = _checkpoint_source(0)
        base = _report_snapshot(project, source)["artifacts"][0]
        variants = [
            {**base, "artifactPath": str((rounds_root / "round_0" / "round_report.md").resolve())},
            {**base, "artifactPath": "../round_1/round_report.md"},
            {**base, "artifactPath": "missing.md", "path": "missing.md", "reportView": None},
            {**base, "artifactPath": "figures/escape.png", "path": "figures/escape.png", "reportView": None},
            {**base, "project": "another-project"},
            {**base, "source": "src_from_another_project"},
        ]
        assert all(app_module._audit_resolve_viewer_artifact(item) is None for item in variants)
        cross_project_snapshot = _report_snapshot(project, source)
        cross_project_snapshot["project"] = "another-project"
        assert app_module._canonicalize_audit_viewer_context(cross_project_snapshot) == []


def test_invalid_reference_does_not_break_chat_and_no_artifact_means_no_viewer_section(
    monkeypatch, tmp_path,
):
    client = app_module.app.test_client()
    project, _rounds_root = _create_project_with_viewer_artifacts(client)
    generator = SimpleNamespace(
        code_dir=project["code_dir"],
        checkpoint_dir=project["checkpoint_dir"],
        report_code_chat_conversations={},
        memory_report_summary="",
        memory_report="",
        research_paper_path=None,
        citation_documents=[],
    )
    captured = []
    monkeypatch.setattr(app_module, "get_or_restore_generator", lambda: generator)
    monkeypatch.setattr(
        app_module,
        "_run_code_chat_with_timeout",
        lambda _timeout, **kwargs: captured.append(kwargs) or {"answer": "Normal audit answer."},
    )
    monkeypatch.setattr(app_module, "_write_interaction_log", lambda *_args, **_kwargs: None)

    invalid = {
        "version": 1,
        "project": project["url_slug"],
        "artifacts": [{
            "round": 0,
            "sourceKind": "checkpoint",
            "path": "round_report.md",
            "artifactPath": "../../private.txt",
        }],
    }
    response = client.post(
        "/api/chat",
        json={
            "project_id": project["id"],
            "mode": "audit",
            "message": "What does this show?",
            "messages": [],
            "viewer_context": invalid,
        },
    )
    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert "Internal active file-viewer context" not in captured[-1]["report_prompt"]

    response = client.post(
        "/api/chat",
        json={
            "project_id": project["id"],
            "mode": "audit",
            "message": "Give me a project summary.",
            "messages": [],
        },
    )
    assert response.status_code == 200
    assert "Internal active file-viewer context" not in captured[-1]["report_prompt"]


def test_prompt_keeps_full_project_inventory_and_makes_visible_figure_inspectable(
    monkeypatch, tmp_path,
):
    client = app_module.app.test_client()
    project, _rounds_root = _create_project_with_viewer_artifacts(client)
    generator = SimpleNamespace(
        code_dir=project["code_dir"],
        checkpoint_dir=project["checkpoint_dir"],
        report_code_chat_conversations={},
        memory_report_summary="",
        memory_report="",
        research_paper_path=None,
        citation_documents=[],
    )
    captured = {}
    monkeypatch.setattr(app_module, "get_or_restore_generator", lambda: generator)
    monkeypatch.setattr(
        app_module,
        "_run_code_chat_with_timeout",
        lambda _timeout, **kwargs: captured.update(kwargs) or {"answer": "Grounded answer."},
    )
    monkeypatch.setattr(app_module, "_write_interaction_log", lambda *_args, **_kwargs: None)

    with _client_request_context(client):
        source = _checkpoint_source(0)
    response = client.post(
        "/api/chat",
        json={
            "mode": "audit",
            "project_id": project["id"],
            "message": "Does this figure support the hypothesis?",
            "messages": [],
            "viewer_context": _report_snapshot(project, source, figure_indexes=[0]),
        },
    )
    assert response.status_code == 200
    prompt = captured["report_prompt"]
    assert "Internal active file-viewer context" in prompt
    assert "checkpoint/checkpoints/round_0/round_report.md" in prompt
    assert "checkpoint/checkpoints/round_0/figures/active.png" in prompt
    assert "Active server caption" in prompt
    assert "Inspect this image with the Read tool" in prompt
    assert "round_0" in prompt and "round_1" in prompt
    assert "relevance signal only; retain full-project scope" in prompt


def test_viewer_metadata_persists_with_user_message_but_is_not_current_live_state(tmp_path):
    client = app_module.app.test_client()
    project, _rounds_root = _create_project_with_viewer_artifacts(client)
    with _client_request_context(client):
        source = _checkpoint_source(0)
    viewer_context = _report_snapshot(project, source)
    state = {
        "version": 2,
        "projectId": project["id"],
        "updatedAt": 1,
        "activeChatId": "chat-one",
        "chats": [{
            "id": "chat-one",
            "title": "What does this show?",
            "createdAt": 1,
            "updatedAt": 1,
            "messages": [{
                "role": "user",
                "text": "What does this show?",
                "responseTimeMs": None,
                "viewerContext": viewer_context,
            }],
        }],
        "pendingJobs": [],
        "queuedMessages": {},
    }
    saved = client.put(
        f"/api/conversation_state?project_id={project['id']}",
        json={
            "project_id": project["id"],
            "surface": "audit_agent",
            "scope": "main",
            "state": state,
        },
    )
    assert saved.status_code == 200
    restored = client.get(
        f"/api/conversation_state?project_id={project['id']}&surface=audit_agent&scope=main"
    ).get_json()["state"]
    message = restored["chats"][0]["messages"][0]
    assert message["text"] == "What does this show?"
    assert message["viewerContext"] == viewer_context
