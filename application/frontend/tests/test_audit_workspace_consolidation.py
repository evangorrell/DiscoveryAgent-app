"""Regression contracts for the project-scoped Audit Agent in Round Workspace."""

import json
from pathlib import Path

from da_selection_ui import app as app_module


def _client_with_project(monkeypatch, tmp_path, name="Audit Workspace"):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    client = app_module.app.test_client()
    response = client.post(
        "/api/projects",
        data={
            "project_name": name,
            "task_description": "Audit all project research.",
            "dataset_context": "A project-wide test context.",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    project_id = response.get_json()["project_id"]
    project = app_module._v2_get_project(project_id)
    assert project is not None
    rounds_root = app_module._v2_round_dirs_root_from_any_checkpoint_path(
        Path(project["checkpoint_dir"])
    )
    for index in (0, 1):
        round_dir = rounds_root / f"round_{index}"
        round_dir.mkdir(parents=True, exist_ok=True)
        (round_dir / "round_report.md").write_text(
            f"# Round {index}\n\nEvidence unique to round {index}.\n", encoding="utf-8",
        )
    return client, project


def test_round_workspace_embeds_project_scoped_audit_controls(monkeypatch, tmp_path):
    client, project = _client_with_project(monkeypatch, tmp_path)

    response = client.get(f"/rounds/?project={project['url_slug']}&round=0")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "AUDIT AGENT" in html
    assert 'aria-label="Open chat history"' in html
    assert 'aria-expanded="false"' in html
    audit_region = html[html.index('<section class="aa-page aa-embedded"'):]
    outer_header_start = html.index('<header class="rw-panel-tabs">')
    outer_header = html[outer_header_start:html.index("</header>", outer_header_start)]
    audit_controls_start = audit_region.index('<div class="aa-control-row">')
    audit_controls = audit_region[
        audit_controls_start:audit_region.index('id="main-interface"', audit_controls_start)
    ]
    assert 'aria-label="Audit Agent"' in audit_region.split(">", 1)[0]
    assert "aa-embedded-header" not in audit_region
    assert "aa-embedded-title" not in audit_controls
    assert ">Audit Agent<" not in audit_controls
    assert 'id="audit-history-toggle"' not in outer_header
    assert 'id="audit-new-chat-btn"' not in outer_header
    assert 'id="chat-export-current-btn"' not in outer_header
    assert html.count('id="audit-history-toggle"') == 1
    assert 'id="audit-history-open-toggle-slot"' in audit_region
    assert 'id="audit-history-closed-toggle-slot"' in audit_controls
    assert 'id="aa-sidebar"' in html and "hidden" in html.split('id="aa-sidebar"', 1)[1].split(">", 1)[0]
    assert 'id="audit-history-close"' not in html
    # The unrelated Project Configuration dialog has an accessible X.
    assert "&times;" not in audit_region[:audit_region.index('id="rw-project-configuration-overlay"')]
    assert 'id="audit-new-chat-btn"' in html and "<span>New chat</span>" in html
    new_chat_at = html.index('id="audit-new-chat-btn"')
    download_at = html.index('id="chat-export-current-btn"')
    assert new_chat_at < download_at
    new_chat_button = html[new_chat_at:html.index("</button>", new_chat_at)]
    assert 'aria-label="New chat"' in new_chat_button
    assert new_chat_button.index("<svg") < new_chat_button.index("<span>New chat</span>")
    download_button = html[download_at:html.index("</button>", download_at)]
    assert 'aria-label="Download conversation"' in download_button
    assert download_button.index("<svg") < download_button.index("<span>Download</span>")
    assert "Reset" not in audit_controls and "aa-embedded-reset" not in audit_controls
    assert "Guide" not in html
    assert "/api/round_chat" not in html


def test_one_selected_conversation_is_injected_for_every_round_but_not_other_projects(
    monkeypatch, tmp_path,
):
    client, first = _client_with_project(monkeypatch, tmp_path, "First Audit Project")
    state = {
        "version": 2,
        "projectId": first["id"],
        "updatedAt": 42,
        "activeChatId": "selected-chat",
        "chats": [{
            "id": "selected-chat",
            "title": "Cross-round evidence",
            "createdAt": 1,
            "updatedAt": 42,
            "messages": [
                {"role": "user", "text": "Compare all rounds."},
                {"role": "assistant", "text": "I inspected rounds 0 and 1."},
            ],
        }],
        "pendingJobs": [{
            "jobId": "a" * 32,
            "chatId": "selected-chat",
            "projectId": first["id"],
            "message": "Continue auditing",
            "createdAt": 42,
        }],
        "queuedMessages": {"selected-chat": [{"text": "Then summarize", "createdAt": 43}]},
    }
    saved = client.put(
        f"/api/conversation_state?project_id={first['id']}",
        json={"project_id": first["id"], "surface": "audit_agent", "scope": "main", "state": state},
    )
    assert saved.status_code == 200
    round_zero_response = client.get(f"/rounds/?project={first['url_slug']}&round=0")
    round_one_response = client.get(f"/rounds/?project={first['url_slug']}&round=1")
    assert round_zero_response.status_code == 200
    assert round_one_response.status_code == 200, round_one_response.get_data(as_text=True)[:1000]
    round_zero = round_zero_response.get_data(as_text=True)
    round_one = round_one_response.get_data(as_text=True)
    assert "selected-chat" in round_zero and "selected-chat" in round_one
    assert "Continue auditing" in round_zero and "Continue auditing" in round_one
    assert "Then summarize" in round_zero and "Then summarize" in round_one

    other_client, second = _client_with_project(monkeypatch, tmp_path, "Second Audit Project")
    isolated = other_client.get(
        f"/api/conversation_state?project_id={second['id']}&surface=audit_agent&scope=main"
    ).get_json()
    assert isolated["state"] is None


def test_audit_client_restores_selection_and_preserves_queue_stop_and_download_contracts():
    root = Path(app_module.app.root_path)
    js = (root / "static" / "project_audit_agent.js").read_text(encoding="utf-8")
    template = (root / "templates" / "round_workspace.html").read_text(encoding="utf-8")

    restore = js[js.index("function restoreLocalChatInterface"):js.index("function setComposerEnabled")]
    assert "storedState.activeChatId" in restore
    assert "activeAuditWorkChatId(storedActiveId)" in restore
    assert "showAuditChatLocally(workChatId, 'active')" in restore
    assert "showBlankNewChat({ discardDraft: true, resetScroll: true })" in restore
    assert "activeChatId = restoredActive.id" not in restore
    assert "setHistoryDrawerOpen" in js
    assert "historyDrawerEl.hidden = !shouldOpen" in js
    assert "setAttribute('aria-expanded', String(shouldOpen))" in js
    assert "shouldOpen ? 'Close chat history' : 'Open chat history'" in js
    assert "setHistoryDrawerOpen(historyDrawerEl && historyDrawerEl.hidden)" in js
    assert "historyCloseBtn" not in js
    assert "getElementById('audit-history-close')" not in js
    assert "exportChat(getActiveChat())" in js
    assert "filter(function(chat) { return !isUnlistedDraftChat(chat); })" in js
    assert "queueAuditMessage(requestChatId, message, viewerContext)" in js
    assert "promoteNextQueuedAuditMessage(job.chatId)" in js
    assert "cancelledAuditJobIds.has(job.jobId)" in js
    assert "cancelActiveAuditResponse" in js
    assert 'id="audit-stop-btn"' not in template
    assert template.count('class="aa-send"') == 1
    assert 'id="chat-send-btn"' in template
    assert 'aria-label="Send message"' in template
    refresh = js[js.index("function refreshSendEnabled"):
                 js.index("function updateComposerForActiveChat")]
    assert "setAttribute('aria-label', 'Stop response')" in refresh
    assert '<rect x="5.25" y="5.25"' in refresh
    submit_handler = js[js.index("function handleComposerSubmit"):
                        js.index("chatInputEl && chatInputEl.addEventListener('keydown'")]
    assert "isActiveChatPending()" in submit_handler
    assert "cancelActiveAuditResponse();" in submit_handler
    assert "sendMessage();" in submit_handler
    assert "v2-report-markdown" in js
    assert "renderReportMarkdown" in js
    assert "/api/audit_chat_figures" in js
    assert "project_audit_agent.js" in template


def test_standalone_audit_route_is_retired_and_historical_round_stays_exact(
    monkeypatch, tmp_path,
):
    client, project = _client_with_project(monkeypatch, tmp_path)

    assert client.get(f"/projects/{project['url_slug']}/audit").status_code == 404
    legacy = client.get("/audit")
    assert legacy.status_code == 302
    assert legacy.headers["Location"].endswith(
        f"/rounds/?project={project['url_slug']}"
    )
    response = client.get(f"/projects/{project['url_slug']}/rounds/0")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(
        f"/rounds/?project={project['url_slug']}&round=0"
    )


def test_project_wide_audit_inventory_contains_artifacts_from_multiple_rounds(
    monkeypatch, tmp_path,
):
    client, project = _client_with_project(monkeypatch, tmp_path)
    client.get(f"/rounds/?project={project['url_slug']}&round=0")
    generator = type("Generator", (), {"checkpoint_dir": project["checkpoint_dir"]})()

    with app_module.app.test_request_context("/"):
        inventory = app_module._build_audit_checkpoint_inventory(generator)

    assert "round_0" in inventory
    assert "round_1" in inventory
    assert inventory.count("round_report.md") >= 2
