"""Focused UI contracts for Audit Agent chat-session behavior."""

from pathlib import Path
import json
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

from flask import session
from da_selection_ui import app as app_module


def test_current_audit_chat_action_is_labeled_download():
    root = Path(app_module.app.root_path)
    html = (root / app_module.app.template_folder / "round_workspace.html").read_text(
        encoding="utf-8"
    )
    js = (root / app_module.app.static_folder / "project_audit_agent.js").read_text(
        encoding="utf-8"
    )

    button = html.split('id="chat-export-current-btn"', 1)[1].split("</button>", 1)[0]
    assert 'aria-label="Download conversation"' in button
    assert 'class="aa-embedded-download"' in button
    assert button.index("<svg") < button.index("<span>Download</span>")
    handler = js.split("chatExportCurrentBtn &&", 1)[1].split("bootRetryBtn &&", 1)[0]
    assert "exportChat(getActiveChat());" in handler


def test_audit_assistant_markdown_reuses_shared_scientific_presentation():
    root = Path(app_module.app.root_path)
    static = root / app_module.app.static_folder
    template = root / app_module.app.template_folder / "round_workspace.html"
    js = (static / "project_audit_agent.js").read_text(encoding="utf-8")
    audit_css = (static / "project_audit_agent.css").read_text(encoding="utf-8")
    shared_css = (static / "style.css").read_text(encoding="utf-8")
    html = template.read_text(encoding="utf-8")

    append_turn = js.split("function appendTurn(role, text, loading, responseTimeMs)", 1)[1].split(
        "function renderAssistantReplyIntoPendingTurn", 1
    )[0]
    assert "content.classList.add('aa-turn-body');" in append_turn
    assert "if (role === 'user')" in append_turn
    assert "content.classList.add('markdown-body');" in append_turn
    assert "content.classList.add('v2-report-markdown', 'aa-chat-markdown');" in append_turn
    assert append_turn.index("if (role === 'user')") < append_turn.index("if (loading)")
    assert "content.innerHTML = renderMarkdown(text);" in append_turn
    assert "void renderAuditAssistantMarkdown(content, text);" in append_turn
    assert "rewriteImagePaths" not in js
    shared_render = js.split("async function renderAuditAssistantMarkdown", 1)[1].split(
        "function normalizeStoredAuditChatMessages", 1
    )[0]
    assert "window.DiscoveryReportFigures.renderReportMarkdown(" in shared_render
    assert "prepareAuditFigurePresentation(text)" in shared_render
    assert "/api/audit_chat_figures" in js
    pending_reply = js.split("function renderAssistantReplyIntoPendingTurn", 1)[1].split(
        "function handleAuditJobResult", 1
    )[0]
    assert "void renderAuditAssistantMarkdown(replyContent, replyText);" in pending_reply

    assert ".aa-turn-assistant {" in audit_css and "min-width: 0;" in audit_css
    assert ".aa-turn-assistant .aa-chat-markdown {" in audit_css
    assert "font: var(--v2-report-body-font);" in audit_css
    assert "table-layout: fixed" not in audit_css
    assert ".aa-turn-user .aa-turn-body.markdown-body" in audit_css
    assert "filename='report_figures.js'" in html
    assert "dompurify@3" in html
    assert "20260818-audit-figure-drag" in html

    for selector in (
        ".v2-report-markdown h1",
        ".v2-report-markdown h2",
        ".v2-report-markdown h3",
        ".v2-report-markdown h4",
        ".v2-report-markdown p",
        ".v2-report-markdown ul",
        ".v2-report-markdown hr",
        ".v2-report-markdown table",
        ".v2-report-markdown :not(pre) > code",
        ".v2-report-markdown blockquote",
        ".v2-report-markdown a",
    ):
        assert selector in shared_css
    table_rules = shared_css[shared_css.index(".v2-report-markdown table"):]
    assert "display: block" in table_rules
    assert "overflow-x: auto" in table_rules
    assert "border-bottom: 1px solid var(--v2-border-soft)" in table_rules


def _audit_figure_project(monkeypatch, tmp_path):
    project_root = tmp_path / "Application Support" / "optic-nerve"
    code_root = project_root / "code"
    round_dir = project_root / "checkpoint" / "checkpoints" / "round_2"
    output = code_root / "outputs" / "figures" / "current figure.png"
    analysis = code_root / "analysis_output" / "nested" / "analysis.png"
    run_image = project_root / "checkpoint" / "images" / "run-image.png"
    for target, data in (
        (output, b"output-figure"),
        (analysis, b"analysis-figure"),
        (run_image, b"run-image"),
    ):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    round_dir.mkdir(parents=True, exist_ok=True)
    (round_dir / "round_report.md").write_text(
        "# Round\n\n![Run](/lustre/old/optic/images/run-image.png)\n",
        encoding="utf-8",
    )
    (round_dir / "config.json").write_text(
        json.dumps({
            "round_idx": 2,
            "config": {
                "working_directory": "/old/optic/code",
                "log_dir": "/old/optic/checkpoint",
                "checkpoint_base_dir": "/old/optic/checkpoint/checkpoints",
            },
        }),
        encoding="utf-8",
    )
    app_module.write_project_metadata(project_root, "optic-nerve-id", "Optic Nerve")
    record = {
        "id": "optic-nerve-id",
        "url_slug": "optic-nerve",
        "project_root": str(project_root),
        "code_dir": str(code_root),
        "checkpoint_dir": str(round_dir),
    }
    monkeypatch.setattr(app_module, "_active_project_record", lambda: record)
    with app_module._ROUND_WORKSPACE_CACHE_LOCK:
        app_module._ROUND_WORKSPACE_ARTIFACT_CACHE.clear()
    return record, code_root, output, analysis, run_image


def test_audit_figures_use_exact_shared_tokens_for_current_historical_and_relative_paths(
    monkeypatch, tmp_path
):
    _record, _code_root, output, analysis, run_image = _audit_figure_project(
        monkeypatch, tmp_path
    )
    markdown = (
        "## Figures\n\nOrdinary scientific context remains unchanged.\n\n"
        f"![Current output]({output})\n\n"
        "![Historical analysis](/lustre/old/optic/analysis_output/nested/analysis.png)\n\n"
        "![Relative output](outputs/figures/current figure.png)\n\n"
        "![Historical run image](/lustre/old/optic/images/run-image.png)\n"
    )
    with app_module.app.test_request_context("/"):
        display, figures = app_module._prepare_audit_chat_figures(markdown)

    assert display.startswith("## Figures\n\nOrdinary scientific context remains unchanged.")
    assert [figure["alt"] for figure in figures] == [
        "Current output",
        "Historical analysis",
        "Relative output",
        "Historical run image",
    ]
    assert all(figure["available"] for figure in figures)
    assert all("rw-report-figure:rw_fig_" in display for _figure in [figures[0]])
    assert str(output) not in display
    assert "/lustre/" not in display
    assert figures[0]["url"].startswith("/api/round_workspace_code_artifact?")
    assert figures[1]["url"].startswith("/api/round_workspace_code_artifact?")
    assert figures[2]["url"].startswith("/api/round_workspace_code_artifact?")
    assert figures[3]["url"].startswith("/api/round_workspace_artifact?")
    assert [figure["artifact"] for figure in figures[:3]] == [
        {
            "round": None,
            "sourceKind": "project-code",
            "source": "",
            "artifactPath": "outputs/figures/current figure.png",
        },
        {
            "round": None,
            "sourceKind": "project-code",
            "source": "",
            "artifactPath": "analysis_output/nested/analysis.png",
        },
        {
            "round": None,
            "sourceKind": "project-code",
            "source": "",
            "artifactPath": "outputs/figures/current figure.png",
        },
    ]
    assert figures[3]["artifact"]["round"] == 2
    assert figures[3]["artifact"]["sourceKind"] == "external"
    assert figures[3]["artifact"]["source"].startswith("src_")
    assert figures[3]["artifact"]["artifactPath"] == "images/run-image.png"
    assert not any(
        str(value).startswith(("/", "file:"))
        for figure in figures
        for value in figure["artifact"].values()
        if isinstance(value, str)
    )

    client = app_module.app.test_client()
    for figure, expected in zip(figures, (output, analysis, output, run_image)):
        parsed = urlsplit(figure["url"])
        response = client.get(parsed.path + "?" + parsed.query)
        assert response.status_code == 200, figure["url"]
        assert response.data == expected.read_bytes()


def test_audit_figure_resolution_fails_closed_for_unsafe_references(
    monkeypatch, tmp_path
):
    _record, code_root, _output, _analysis, _run_image = _audit_figure_project(
        monkeypatch, tmp_path
    )
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    symlink = code_root / "analysis_output" / "escaped.png"
    symlink.symlink_to(outside)
    other_project = tmp_path / "other-project" / "code" / "outputs" / "cross.png"
    other_project.parent.mkdir(parents=True)
    other_project.write_bytes(b"cross")
    markdown = (
        "![Missing](/lustre/old/optic/outputs/missing.png)\n\n"
        "![Traversal](/lustre/old/optic/analysis_output/../outside.png)\n\n"
        f"![Symlink]({symlink})\n\n"
        f"![Cross project]({other_project})\n\n"
        "![Unsupported](/lustre/old/optic/outputs/data.txt)\n\n"
        "![Arbitrary absolute](/etc/private.png)\n"
    )
    with app_module.app.test_request_context("/"):
        display, figures = app_module._prepare_audit_chat_figures(markdown)

    assert len(figures) == 6
    assert all(figure["available"] is False for figure in figures)
    assert all(figure["url"] is None for figure in figures)
    assert all("artifact" not in figure for figure in figures)
    assert "/lustre/" not in display
    assert str(symlink) not in display
    assert str(other_project) not in display
    assert "/etc/private.png" not in display


def test_audit_figure_endpoint_is_project_scoped_and_preserves_raw_chat_text(
    monkeypatch, tmp_path
):
    record, _code_root, output, _analysis, _run_image = _audit_figure_project(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(
        app_module,
        "_managed_interaction_project",
        lambda key=None: record if key == record["id"] else None,
    )
    raw = f"Before\n\n![Current output]({output})\n\nAfter"
    client = app_module.app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["v2_active_project_slug"] = "optic-nerve"
        flask_session["v2_active_project_id"] = record["id"]
    response = client.post(
        f"/api/audit_chat_figures?project_id={record['id']}",
        json={"project_id": record["id"], "markdown": raw},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["display_text"] != raw
    assert raw == f"Before\n\n![Current output]({output})\n\nAfter"
    assert payload["figures"][0]["available"] is True
    assert payload["figures"][0]["artifact"] == {
        "round": None,
        "sourceKind": "project-code",
        "source": "",
        "artifactPath": "outputs/figures/current figure.png",
    }
    mismatch = client.post(
        "/api/audit_chat_figures?project_id=other-project",
        json={"project_id": "other-project", "markdown": raw},
    )
    assert mismatch.status_code == 409
    assert mismatch.get_json()["code"] == "project_identity_mismatch"


def test_stored_audit_assistant_messages_are_reprocessed_with_shared_figures():
    js = (Path(app_module.app.static_folder) / "project_audit_agent.js").read_text(
        encoding="utf-8"
    )
    history = js.split("function renderStoredChatHistory(entries)", 1)[1].split(
        "function isActiveChatPending", 1
    )[0]
    append = js.split("function appendTurn(role, text, loading, responseTimeMs)", 1)[1].split(
        "function renderAssistantReplyIntoPendingTurn", 1
    )[0]
    assert "appendTurn(entry.role, entry.text, false, entry.responseTimeMs);" in history
    assert "void renderAuditAssistantMarkdown(content, text);" in append
    assert "chatHistory" not in js.split("async function renderAuditAssistantMarkdown", 1)[1].split(
        "function normalizeStoredAuditChatMessages", 1
    )[0]


def test_deleting_active_audit_chat_opens_fresh_chat_instead_of_recent_chat():
    js = (Path(app_module.app.static_folder) / "project_audit_agent.js").read_text(encoding="utf-8")

    delete_branch = js.split("if (deletingActiveChat) {", 1)[1].split("} else {", 1)[0]
    assert "showBlankNewChat({ discardDraft: true, resetScroll: true });" in delete_branch
    assert "persistAuditChatState({ touchActive: false, skipSync: true });" in delete_branch
    assert "createEmptyChat" not in delete_branch
    assert "activeChatId = chatSessions[0].id;" not in delete_branch
    assert "restoreAuditConversationOnServer" not in delete_branch
    assert "resetAuditConversationOnServer" not in delete_branch


def test_stopping_audit_response_records_assistant_message():
    js = (Path(app_module.app.static_folder) / "project_audit_agent.js").read_text(encoding="utf-8")

    stop_handler = js.split("async function cancelActiveAuditResponse() {", 1)[1].split(
        "function autoSizeInput()", 1
    )[0]
    assert "cancelledAuditJobIds.add(job.jobId);" in stop_handler
    assert "appendAssistantMessageToChat(job.chatId, 'Response stopped.', null);" in stop_handler
    assert stop_handler.index("appendAssistantMessageToChat") < stop_handler.index("renderStoredChatHistory")
    assert "promoteNextQueuedAuditMessage(job.chatId);" in stop_handler
    assert stop_handler.index("promoteNextQueuedAuditMessage(job.chatId);") < stop_handler.index(
        "await fetch(`/api/audit_chat_jobs/"
    )
    assert "pausedAuditQueues" not in stop_handler


def test_audit_cancellation_never_pauses_and_refresh_advances_orphaned_queue():
    static = Path(app_module.app.static_folder)
    js = (static / "project_audit_agent.js").read_text(encoding="utf-8")
    css = (static / "project_audit_agent.css").read_text(encoding="utf-8")
    resume = js.split("function resumePendingAuditJobs()", 1)[1].split(
        "function getActiveAuditJob", 1
    )[0]
    assert "Object.keys(queuedAuditMessages)" in resume
    assert "promoteNextQueuedAuditMessage(chatId);" in resume
    assert "Queue paused because you interrupted" not in js
    assert "pausedQueues" not in js and "pausedAuditQueues" not in js
    assert "aa-queue-paused" not in css and "aa-queue-resume" not in css


def test_background_audit_jobs_use_isolated_conversations_and_reject_late_cancelled_results():
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    audit_branch = source.split("if _is_audit_only_mode() or _is_audit_mode() or mode == 'audit':", 1)[1].split(
        "elif mode == 'code':", 1
    )[0]
    terminal_guard = source.split("timing_payload = _response_time_payload(request_started_at)", 1)[1].split(
        "if source == 'agent'", 1
    )[0]
    assert "isolated_audit_job = bool(audit_job_id and request_messages is not None)" in audit_branch
    assert "conversation = CodeChatConversation()" in audit_branch
    assert "_full_chat_messages_for_logging(conversation.messages)" in audit_branch
    assert "_is_audit_chat_job_cancel_requested(audit_job_id)" in terminal_guard
    guard_index = source.index("if active_report_key and audit_job_id and _is_audit_chat_job_cancel_requested")
    assert guard_index < source.index("# Write to local durable interaction log", guard_index)


def test_cancelled_audit_worker_does_not_block_or_overwrite_next_job(monkeypatch):
    first_started = threading.Event()
    release_first = threading.Event()
    second_finished = threading.Event()
    first_job_id = "a" * 32
    second_job_id = "b" * 32

    def fake_chat_view():
        from flask import g, jsonify

        if g.audit_chat_job_id == first_job_id:
            first_started.set()
            assert release_first.wait(timeout=5)
            return jsonify({"success": True, "reply": "late answer A"})
        second_finished.set()
        return jsonify({"success": True, "reply": "answer B"})

    monkeypatch.setattr(app_module, "api_chat", SimpleNamespace(__wrapped__=fake_chat_view))
    now = time.time()
    perf_started_at = time.perf_counter()
    base_job = {
        "session_id": "audit-queue-test-session",
        "project_id": "prj_auditqueuetest",
        "status": "pending",
        "request": {
            "message": "question",
            "mode": "audit",
            "messages": [],
            "project_id": "prj_auditqueuetest",
        },
        "session": {
            "session_id": "audit-queue-test-session",
            "v2_active_project_id": "prj_auditqueuetest",
        },
        "created_at": now,
        "updated_at": now,
        "perf_started_at": perf_started_at,
        "result": None,
        "status_code": None,
        "cancel_requested": False,
    }

    monkeypatch.setattr(
        app_module,
        "_managed_interaction_project",
        lambda project_id=None: {"id": project_id} if project_id == "prj_auditqueuetest" else None,
    )
    with app_module.AUDIT_CHAT_JOBS_LOCK:
        prior_jobs = dict(app_module.AUDIT_CHAT_JOBS)
        app_module.AUDIT_CHAT_JOBS.clear()
        app_module.AUDIT_CHAT_JOBS[first_job_id] = {**base_job, "id": first_job_id}
        app_module.AUDIT_CHAT_JOBS[second_job_id] = {**base_job, "id": second_job_id}

    first_worker = threading.Thread(target=app_module._run_audit_chat_job, args=(first_job_id,))
    second_worker = threading.Thread(target=app_module._run_audit_chat_job, args=(second_job_id,))
    try:
        first_worker.start()
        assert first_started.wait(timeout=5)
        with app_module.AUDIT_CHAT_JOBS_LOCK:
            first_job = app_module.AUDIT_CHAT_JOBS[first_job_id]
            first_job["cancel_requested"] = True
            first_job["status"] = "cancelled"

        second_worker.start()
        assert second_finished.wait(timeout=2), "B must finish before blocked A exits"
        second_worker.join(timeout=2)
        assert not second_worker.is_alive()
        with app_module.AUDIT_CHAT_JOBS_LOCK:
            assert app_module.AUDIT_CHAT_JOBS[second_job_id]["status"] == "succeeded"
            assert app_module.AUDIT_CHAT_JOBS[second_job_id]["result"]["reply"] == "answer B"

        release_first.set()
        first_worker.join(timeout=2)
        assert not first_worker.is_alive()
        with app_module.AUDIT_CHAT_JOBS_LOCK:
            first_job = app_module.AUDIT_CHAT_JOBS[first_job_id]
            assert first_job["status"] == "cancelled"
            assert first_job["result"]["cancelled"] is True
            assert "late answer A" not in str(first_job["result"])
    finally:
        release_first.set()
        first_worker.join(timeout=2)
        second_worker.join(timeout=2)
        with app_module.AUDIT_CHAT_JOBS_LOCK:
            app_module.AUDIT_CHAT_JOBS.clear()
            app_module.AUDIT_CHAT_JOBS.update(prior_jobs)


def test_project_audit_chat_ignores_ambiguous_legacy_browser_storage_without_deleting_it():
    static = Path(app_module.app.static_folder)
    js = (static / "project_audit_agent.js").read_text(encoding="utf-8")
    html = (
        Path(app_module.app.root_path) / app_module.app.template_folder / "round_workspace.html"
    ).read_text(encoding="utf-8")

    assert "projectId:" in html
    assert "projectSlug:" in html
    assert "function readAuditChatStateAtKey(storageKey)" in js
    assert "function legacyAuditChatStorageKeys()" not in js
    assert "key.endsWith(projectSuffix)" not in js
    assert "legacyChatStorageScopes" not in html
    assert "parsed.projectId !== projectId" in js
    loader = js.split("function readAuditChatStateFromStorage()", 1)[1].split(
        "function writeDurableAuditChatState", 1
    )[0]
    assert "return candidates[0].state;" in loader
    assert "localStorage.setItem" not in loader
    assert "queueDurableAuditChatState" not in loader
    assert "localStorage.removeItem(storageKey)" not in js


def test_project_audit_chat_preserves_history_but_restores_only_active_work():
    js = (Path(app_module.app.static_folder) / "project_audit_agent.js").read_text(
        encoding="utf-8"
    )

    loader = js.split("function readAuditChatStateFromStorage()", 1)[1].split(
        "function writeDurableAuditChatState", 1
    )[0]
    restore = js.split("function restoreLocalChatInterface()", 1)[1].split(
        "function setComposerEnabled", 1
    )[0]
    assert "Number(b.state.updatedAt)" in loader
    assert "return candidates[0].state;" in loader
    assert "queueDurableAuditChatState" not in loader
    assert "localStorage.setItem" not in loader
    assert "chatSessions = storedState && Array.isArray(storedState.chats)" in restore
    assert "storedState.activeChatId" in restore
    assert "activeAuditWorkChatId(storedActiveId)" in restore
    assert "showAuditChatLocally(workChatId, 'active')" in restore
    assert "showBlankNewChat({ discardDraft: true, resetScroll: true })" in restore
    assert "activeChatId = restoredActive.id;" not in restore
    assert "const freshChat = createEmptyChat();" not in restore
    assert ".filter(function(chat) { return !isUnlistedDraftChat(chat); })" in js


def test_project_audit_blank_entry_is_transient_until_first_message():
    js = (Path(app_module.app.static_folder) / "project_audit_agent.js").read_text(
        encoding="utf-8"
    )
    context_js = (Path(app_module.app.static_folder) / "project_context.js").read_text(
        encoding="utf-8"
    )

    persist = js.split("function persistAuditChatState(options)", 1)[1].split(
        "function renderChatList", 1
    )[0]
    send = js.split("function sendMessage()", 1)[1].split(
        "newChatBtn &&", 1
    )[0]

    assert ".filter(function(chat) { return !isUnlistedDraftChat(chat); })" in persist
    assert "const persistedActiveChatId = chats.some" in persist
    assert "activeChatId: persistedActiveChatId" in persist
    assert "if (!activeChatId)" in send
    assert "ensureActiveChat();" in send
    assert "appendUserMessageToChat(requestChatId, message, viewerContext);" in send
    assert send.index("ensureActiveChat();") < send.index("appendUserMessageToChat")
    assert send.index("appendUserMessageToChat") < send.index("persistAuditChatState();")
    assert js.count("chat = createEmptyChat();") == 1
    assert "discoveryAgentContinuousProject" not in context_js
    assert "discoveryAgentAuditFreshEntry" not in context_js
    assert "auditFreshEntry" not in js


def test_project_audit_history_is_openable_and_new_chat_returns_to_blank():
    js = (Path(app_module.app.static_folder) / "project_audit_agent.js").read_text(
        encoding="utf-8"
    )

    activate = js.split("async function activateChat(chatId, options)", 1)[1].split(
        "async function switchChat", 1
    )[0]
    new_chat = js.split("function startNewChat()", 1)[1].split(
        "async function restoreAuditConversationOnServer", 1
    )[0]
    assert "activeChatId = chat.id;" in activate
    assert "renderStoredChatHistory(chatHistory);" in activate
    assert "await restoreAuditConversationOnServer(chatHistory);" in activate
    assert "showBlankNewChat({ discardDraft: true, resetScroll: true });" in new_chat
    assert "createEmptyChat" not in new_chat
    assert "persistAuditChatState" not in new_chat
    assert "resetAuditConversationOnServer" not in new_chat


def test_project_audit_chat_flushes_to_explicit_project_on_pagehide():
    js = (Path(app_module.app.static_folder) / "project_audit_agent.js").read_text(
        encoding="utf-8"
    )

    assert "?project_id=${encodeURIComponent(projectId)}" in js
    assert "project_id: projectId" in js
    assert "window.addEventListener('pagehide'" in js
    assert "writeDurableAuditChatState(snapshot, true)" in js
    assert "keepalive: keepalive === true" in js
