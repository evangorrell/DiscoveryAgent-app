import json
from pathlib import Path

from da_selection_ui import app as app_module


def test_round_workspace_nav_remains_available_for_pristine_project(tmp_path):
    checkpoint_root = tmp_path / "checkpoint"
    (checkpoint_root / "checkpoints").mkdir(parents=True)
    project = {"checkpoint_dir": str(checkpoint_root / "checkpoints" / "round_0")}

    with app_module.app.test_request_context():
        assert app_module._v2_round_workspace_nav_url(project, "example") == "/rounds/?project=example"


def test_workspace_frontend_has_independent_round_and_split_controls():
    static = Path(app_module.app.static_folder)
    template = Path(app_module.app.root_path) / app_module.app.template_folder / "round_workspace.html"
    js = (static / "round_workspace.js").read_text(encoding="utf-8")
    css = (static / "round_workspace.css").read_text(encoding="utf-8")
    html = template.read_text(encoding="utf-8")

    assert "rw-round-caret" in js and "e.stopPropagation()" in js
    assert "row.style.paddingLeft = '8px'" in js
    assert ".rw-file-tree{margin-left:14px;border-left:1px" in (static / "round_workspace.css").read_text(encoding="utf-8")
    assert ".rw-tree-children{margin-left:14px;border-left:1px" in (static / "round_workspace.css").read_text(encoding="utf-8")
    assert ".rw-tree-disclosure-slot{display:inline-flex;align-items:center;justify-content:center" in css
    assert "setActiveRound(round.round_index)" in js
    assert "select.addEventListener('click', () => {open ? state.expanded.delete(key) : state.expanded.add(key)" in js
    assert "if(state.chats.has(state.activeRound))renderChat();refreshActivity()" in js
    assert "else if(renderActivityQueryStream.lastData)renderActivity(" in js
    assert "pane.tabs.push(file)" in js and "pane.activeTab" in js
    assert "['top','bottom']" in js and "['left','top']" in js
    assert "rw-drop-preview" in html and "data-drop=" not in html
    assert "rw-main-divider" in html and "rw-panel-collapse" in html
    assert "collapseThreshold=12.5" in js and "Math.max(25" in js
    assert "state.panelCollapsed=true" in js and "state.panelCollapsed=false" in js
    assert "window.addEventListener('pointermove',move)" in js
    assert 'data-panel-tab="activity"' in html
    assert "rw-chat-role" in js and "'YOU'" in js and "'ROUND CHAT'" in js
    assert "rw-chat-composer-surface" in html
    assert "data-chat-mode" not in html
    assert 'class="rw-chat-reset"' in html and "rw-chat-toolbar" not in html
    assert 'class="rw-chat-actions"' in html
    assert ".rw-chat-actions{display:flex;flex:0 0 auto;justify-content:flex-end" in (static / "round_workspace.css").read_text(encoding="utf-8")
    assert "fetch('/api/round_chat_jobs?round='" in js
    assert "activeRoundStorageKey" in js and "sessionStorage.setItem" in js
    assert "const reviewEntry = entryParams.get('review') === '1'" in js
    assert "const startedEntry = entryParams.get('started') === '1'" in js
    assert "activeRound: startedEntry ? startedRound" in js
    assert "panelTab: startedEntry || reviewEntry ? 'activity'" in js
    assert "panelWidth: startedEntry || reviewEntry ? 25" in js
    assert "panelCollapsed: startedEntry || reviewEntry ? false" in js
    assert "const restoredViewer = startedEntry ? {panes:[]" in js
    assert "round.round_index > latest" in js
    assert "panelTabStorageKey" in js and "applyPanelTab()" in js
    assert "panelCollapsedStorageKey" in js and "String(state.panelCollapsed)" in js
    assert "round.status==='completed'" in js
    assert "what DiscoveryAgent did" in js and "generated figures, or reports" in js
    assert "rw-chat-typing" in js and "Round Chat is responding" in js
    assert "keepalive:true" in js and "pollRoundChatJob" in js
    assert "renderChatQueue" in js and "rw-chat-queued-item" in js and "processRoundMessage" in js
    assert "const distanceFromBottom=els.messages.scrollHeight-els.messages.scrollTop-els.messages.clientHeight" in js
    assert "const shouldStickToBottom=options.forceBottom===true||distanceFromBottom<=48" in js
    assert "els.messages.scrollTop=shouldStickToBottom?els.messages.scrollHeight:previousScrollTop" in js
    assert "renderChat({forceBottom:true})" in js
    assert "Remove queued message" in js and "chat.splice(index,1)" in js
    assert "chat.filter(m=>!m.queued)" in js
    assert 'id="rw-chat-queue"' in html
    assert "updateChatSubmitButton" in js and "Round Chat response pending" in js
    assert "chatStorageKey" in js and "restoreChats()" in js and "persistChats()" in js
    chat_state = (static / "round_workspace_chat_state.js").read_text(encoding="utf-8")
    assert "ChatState.recoverMessages(messages,roundIndex)" in js
    assert "return normalized;" in chat_state
    assert "m.role==='assistant'&&m.pending?{pending:true}" in js
    assert "pausedQueueStorageKey" in js and "persistPausedQueues" in js
    assert "state.pausedQueues.delete(round)" in js
    assert "rw-queue-resume" in js
    assert "resumeInterruptedChats" not in js
    assert "interrupted by page navigation" not in chat_state
    assert "resumePendingRoundChatJobs()" in js
    process_body = js[js.index("async function processRoundMessage"):js.index("els.form.addEventListener")]
    assert "job_id:pending.jobId" in process_body
    assert "request_id:userMessage.id" in process_body
    assert "pollRoundChatJob(round,pending)" in process_body
    assert "function activeViewerFile()" in js
    assert "viewer_file:userMessage.viewerFile||null" in process_body
    assert "viewerFile:activeViewerFile()" in js
    assert ".rw-chat-queue{margin-bottom:14px}" in (static / "round_workspace.css").read_text(encoding="utf-8")
    assert "viewerStorageKey" in js and "restoreViewer()" in js
    assert "const viewerIdentityChanged=hydrateRestoredViewerSources()" in js
    assert "if(viewerIdentityChanged)renderPanes()" in js
    assert "rw-pane-divider" in js and "Resize file panes" in js
    assert "Math.max(25,Math.min(75,value))" in js
    assert "paneRatio:state.paneRatio" in js
    assert "pointermove',move" in js and "ArrowLeft" in js and "ArrowRight" in js


def test_interrupted_precheckpoint_round_does_not_show_first_round_overlay(monkeypatch, tmp_path):
    round_zero = tmp_path / "project" / "checkpoint" / "checkpoints" / "round_0"
    round_zero.mkdir(parents=True)
    (round_zero / "round_activity.jsonl").write_text(
        json.dumps({"event_type": "round_started"}) + "\n", encoding="utf-8",
    )
    project = {
        "id": "project-1", "url_slug": "project-1",
        "checkpoint_dir": str(round_zero),
    }
    monkeypatch.setattr(
        app_module, "_v2_resolve_project_by_slug_or_id",
        lambda slug: (project, False) if slug == "project-1" else (None, False),
    )
    monkeypatch.setattr(app_module, "_v2_activate_project", lambda actual: actual)
    captured = {}
    monkeypatch.setattr(
        app_module, "render_template",
        lambda template, **context: captured.update(template=template, **context) or "OK",
    )
    client = app_module.app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["v2_active_project_slug"] = "project-1"

    response = client.get("/rounds/")

    assert response.status_code == 200
    assert captured["template"] == "round_workspace.html"
    assert captured["pre_round_overlay"] is False
    assert captured["pinned_round"] == 0


def test_round_chat_uses_selected_round_and_file_nav_inventory():
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    route = source[source.index("def api_round_chat():"):source.index("@app.route", source.index("def api_round_chat():") + 1)]

    assert "round_index, round_dir, round_error = _round_activity_dir_for_request()" in route
    assert "_get_latest_round_dir_for_session()" not in route
    assert "_round_chat_file_nav_inventory(" in route
    assert "request_id = str(data.get('request_id')" in route
    assert "'request_id': request_id or None" in route
    assert "data.get('viewer_file')" in route
    assert "file_nav_inventory" in route


def test_activity_frontend_has_only_status_and_query_progress_sections():
    static = Path(app_module.app.static_folder)
    template = Path(app_module.app.root_path) / app_module.app.template_folder / "round_workspace.html"
    js = (static / "round_workspace.js").read_text(encoding="utf-8")
    html = template.read_text(encoding="utf-8")
    activity_js = js[
        js.index("function activityStatusClass"):
        js.index("function chatGreetingForRound")
    ]

    assert "rw-activity-status" not in html
    assert "renderActivitySummary(data,items,requestedRound)" in activity_js
    assert "renderActivityQueryStream(items,requestedRound)" in activity_js
    assert "els.activity.replaceChildren(" in activity_js
    for forbidden in (
        "Current Activity", "System Events", "Execution History",
        "Recent Activity", "Milestones",
    ):
        assert forbidden not in activity_js
    assert "QUERY PROGRESS" in activity_js
    assert "rw-activity-milestone" in activity_js
    assert "function activityMilestoneType(row)" in activity_js
    assert "function activityDataWithRoundFallback(data,requestedRound)" in activity_js
    assert "data.round_status||round.status||'unavailable'" in activity_js
    assert "}).filter(Boolean)" in activity_js
    assert "rw-activity-caret" in activity_js
    assert "aria-expanded" in activity_js
    assert "state.activityExpanded" in activity_js
    assert "const calls=Array.isArray(row.subagent_calls)?row.subagent_calls:[]" in activity_js
    assert "const hasCalls=item.details.length>0" in activity_js
    assert "'Round '+requestedRound+' failed'" in activity_js
    assert "'Please resubmit this round and try again.'" in activity_js
    assert "rw-stop-run','Stop Run'" in activity_js
    assert "rw-resume-round','Resume Round'" in activity_js
    assert "rw-resubmit-round','Resubmit Round'" in activity_js
    assert "function confirmRoundAction(" in activity_js
    assert "Delete Round" not in activity_js
    assert "Delete round?" not in activity_js
    assert "/api/rounds/delete" not in activity_js
    assert "Stop this round?" in activity_js
    assert "Keep Running" in activity_js
    assert "api('/api/rounds/stop',{})" in activity_js
    assert "api('/api/rounds/resume',{})" in activity_js
    assert "api('/api/rounds/resubmit',{})" in activity_js
    assert "data.execution_status=execution.status" in activity_js
    assert "Round interrupted" in activity_js
    assert "rawStatus==='interrupted'?'stopped':rawStatus" in js
    assert "section.dataset.status=round.status||'unknown'" in js
    assert "rw-round-status-icon" not in js
    assert "rw-activity-caret-placeholder" in activity_js
    assert "'Sub-agent call'" in activity_js
    assert "detail.agent_role" in activity_js
    assert "detail.task_summary" in activity_js
    assert "rw-activity-query-summary" in activity_js
    assert "const requestToken=++state.activityRequestToken" in activity_js
    assert "requestToken!==state.activityRequestToken" in activity_js
    assert "if(data.round_index!=null&&Number(data.round_index)!==requestedRound)return" in activity_js
    assert "state.activityExpanded.has(expansionKey)" in activity_js
    assert "activityExpandedStorageKey" in js
    assert "activityExpanded: restoreActivityExpanded()" in js
    assert "persistActivityExpanded();renderActivityQueryStream.lastData" in activity_js
    assert js.count("setInterval(") == 1
    assert "refreshActivity();loadExplorer({silent:true})" in js
    assert "function completedQueryCount(count){return count+' '+(count===1?'query':'queries')+' completed';}" in activity_js
    assert "completedQueryCount(queries.length)" in activity_js
    assert "completedQueryCount(completedCount)" in activity_js
    assert "finalCount+' of '+finalCount" not in activity_js
    css = (static / "round_workspace.css").read_text(encoding="utf-8")
    assert ".rw-activity-subagents{display:flex" in css
    assert ".rw-activity-state-icon.active,.rw-activity-query-indicator.active{animation:rw-activity-pulse" in css
    assert "@media (prefers-reduced-motion:reduce)" in css
    assert ".rw-stop-run{border-color:#b85b5b;background:#a24949;color:#fff}" in css
    assert "rw-delete-round" not in css
    assert "window.V2Dialog.confirm" in activity_js
    assert ".rw-round-status.failed,.rw-round-status.interrupted" in css
    assert "round_workspace.js', v=" in html
    assert "round_workspace.css', v=" in html
    for raw_internal in ("tool_calls", "model_messages", "terminal", "raw_prompt"):
        assert raw_internal not in activity_js


def test_completed_activity_summary_uses_compact_report_button():
    css = (Path(app_module.app.static_folder) / "round_workspace.css").read_text(encoding="utf-8")

    assert ".rw-activity-summary .rw-open-round-report{align-self:flex-start;width:fit-content;min-width:136px" in css


def test_completed_activity_metadata_matches_query_summary_text():
    css = (Path(app_module.app.static_folder) / "round_workspace.css").read_text(encoding="utf-8")

    shared_text_style = "color:var(--v2-ink-soft);font-size:.76rem;line-height:1.42"
    assert ".rw-activity-summary-body" in css
    assert shared_text_style in css[css.index(".rw-activity-summary-body"):]
    assert shared_text_style in css[css.index(".rw-activity-query-summary"):]


def test_activity_timeline_interleaves_curated_milestones_and_collapses_report_noise(
    monkeypatch, tmp_path
):
    round_dir = tmp_path / "round_3"
    query_one = round_dir / "query_001"
    query_two = round_dir / "query_002"
    query_one.mkdir(parents=True)
    query_two.mkdir()
    (query_one / "query_001.md").write_text("# Query 1", encoding="utf-8")
    (query_two / "query_002.md").write_text("# Query 2", encoding="utf-8")
    query_items = {
        1: {
            "kind": "query", "timestamp": "2026-01-01T10:00:00Z",
            "query_id": "query_001", "status": "completed",
            "summary": "First query", "director_activity": [{"label": "Data Analysis"}],
            "source": "test",
        },
        2: {
            "kind": "query", "timestamp": "2026-01-01T11:00:00Z",
            "query_id": "query_002", "status": "running",
            "summary": "Second query", "director_activity": [],
            "source": "test",
        },
    }
    monkeypatch.setattr(app_module, "_list_query_dirs", lambda _round: [query_one, query_two])
    monkeypatch.setattr(
        app_module,
        "_build_discovery_query_item",
        lambda _qd, num, *_args, **_kwargs: dict(query_items[num]),
    )
    monkeypatch.setattr(app_module, "_scan_log_for_query_timing", lambda _text: {})
    monkeypatch.setattr(app_module, "_group_round_activity_jsonl_by_query", lambda _round: {})

    timeline = app_module._build_discovery_activity_timeline(
        round_dir,
        None,
        None,
        [
            {"timestamp": "2026-01-01T09:00:00Z", "message": "Round started.", "query": None},
            {"timestamp": "2026-01-01T10:30:00Z", "message": "Session reset.", "query": None},
            {"timestamp": "2026-01-01T11:30:00Z", "message": "Report writing started.", "query": None},
            {"timestamp": "2026-01-01T12:00:00Z", "message": "Round report saved.", "query": None},
            {"timestamp": "2026-01-01T12:30:00Z", "message": "Round report saved.", "query": None},
            {"timestamp": "2026-01-01T13:00:00Z", "message": "Memory report saved.", "query": None},
            {"timestamp": "2026-01-01T13:30:00Z", "message": "Checkpoint updated.", "query": None},
        ],
    )

    assert [
        item.get("query_id") or item.get("milestone_type") for item in timeline
    ] == [
        "round_started", "query_001", "query_002",
        "report_writing_started", "round_report_saved", "memory_report_saved",
    ]
    assert sum(item.get("milestone_type") == "round_report_saved" for item in timeline) == 1
    milestone_rows = [item for item in timeline if item["kind"] == "milestone"]
    assert all(item["director_activity"] == [] for item in milestone_rows)
    assert timeline[1]["director_activity"] == [{"label": "Data Analysis"}]
    assert all(item.get("summary") != "Checkpoint updated." for item in timeline)


def test_native_start_and_resume_events_are_distinct_and_timestamp_ordered(
    monkeypatch, tmp_path
):
    round_dir = tmp_path / "round_4"
    query_two = round_dir / "query_002"
    query_three = round_dir / "query_003"
    query_two.mkdir(parents=True)
    query_three.mkdir()
    (query_two / "query_002.md").write_text("# Query 2", encoding="utf-8")
    (query_three / "query_003.md").write_text("# Query 3", encoding="utf-8")
    (round_dir / "round_activity.jsonl").write_text("\n".join([
        json.dumps({"timestamp": "2026-01-01T09:00:00Z", "event_type": "round_started"}),
        json.dumps({"timestamp": "2026-01-01T10:30:00Z", "event_type": "round_resumed"}),
    ]), encoding="utf-8")
    query_items = {
        2: {"kind": "query", "timestamp": "2026-01-01T10:00:00Z", "query_id": "query_002",
            "status": "completed", "summary": "Second query", "director_activity": [], "source": "test"},
        3: {"kind": "query", "timestamp": "2026-01-01T11:00:00Z", "query_id": "query_003",
            "status": "running", "summary": "Third query", "director_activity": [], "source": "test"},
    }
    monkeypatch.setattr(app_module, "_list_query_dirs", lambda _round: [query_two, query_three])
    monkeypatch.setattr(
        app_module, "_build_discovery_query_item",
        lambda _qd, num, *_args, **_kwargs: dict(query_items[num]),
    )
    monkeypatch.setattr(app_module, "_scan_log_for_query_timing", lambda _text: {})
    monkeypatch.setattr(app_module, "_group_round_activity_jsonl_by_query", lambda _round: {})

    events = app_module._build_round_activity_jsonl_events(round_dir)
    timeline = app_module._build_discovery_activity_timeline(round_dir, None, None, events)

    assert [item.get("query_id") or item.get("milestone_type") for item in timeline] == [
        "round_started", "query_002", "round_resumed", "query_003",
    ]
    assert [item["summary"] for item in timeline if item["kind"] == "milestone"] == [
        "Round started.", "Round resumed.",
    ]


def test_resume_event_stays_before_in_progress_query_until_query_completes(
    monkeypatch, tmp_path
):
    round_dir = tmp_path / "round_4"
    query_one = round_dir / "query_001"
    query_two = round_dir / "query_002"
    query_one.mkdir(parents=True)
    query_two.mkdir()
    (query_one / "query_001.md").write_text("# Query 1", encoding="utf-8")
    (query_two / "query_002.md").write_text("# Query 2", encoding="utf-8")
    (round_dir / "round_activity.jsonl").write_text("\n".join([
        json.dumps({"timestamp": "2026-01-01T09:00:00Z", "event_type": "round_started"}),
        json.dumps({"timestamp": "2026-01-01T10:30:00Z", "event_type": "round_resumed"}),
    ]), encoding="utf-8")
    query_items = {
        1: {"kind": "query", "timestamp": "2026-01-01T10:00:00Z", "query_id": "query_001",
            "status": "completed", "summary": "First query", "director_activity": [], "source": "test"},
        # Query 2 began before the interruption, so its running timestamp is
        # older than the later resume event.
        2: {"kind": "query", "timestamp": "2026-01-01T10:15:00Z", "query_id": "query_002",
            "status": "running", "summary": "Second query", "director_activity": [], "source": "test"},
    }
    monkeypatch.setattr(app_module, "_list_query_dirs", lambda _round: [query_one, query_two])
    monkeypatch.setattr(
        app_module, "_build_discovery_query_item",
        lambda _qd, num, *_args, **_kwargs: dict(query_items[num]),
    )
    monkeypatch.setattr(app_module, "_scan_log_for_query_timing", lambda _text: {})
    monkeypatch.setattr(app_module, "_group_round_activity_jsonl_by_query", lambda _round: {})

    events = app_module._build_round_activity_jsonl_events(round_dir)

    running_timeline = app_module._build_discovery_activity_timeline(
        round_dir, None, None, events,
    )
    expected_order = ["round_started", "query_001", "round_resumed", "query_002"]
    assert [
        item.get("query_id") or item.get("milestone_type") for item in running_timeline
    ] == expected_order

    query_items[2]["status"] = "completed"
    query_items[2]["timestamp"] = "2026-01-01T11:00:00Z"
    completed_timeline = app_module._build_discovery_activity_timeline(
        round_dir, None, None, events,
    )
    assert [
        item.get("query_id") or item.get("milestone_type") for item in completed_timeline
    ] == expected_order


def test_internal_session_reset_does_not_replace_native_round_resume(tmp_path):
    round_dir = tmp_path / "round_4"
    round_dir.mkdir()
    (round_dir / "round_activity.jsonl").write_text(
        json.dumps({"timestamp": "2026-01-01T10:30:00Z", "event_type": "round_resumed"}),
        encoding="utf-8",
    )
    log_text = (
        "2026-01-01 10:31:00 - INFO - [ACTION] session_reset - director_workflow\n"
        "Reinitialized Claude Code session for fresh context\n"
    )

    events = app_module._extract_round_activity_events(log_text, round_dir)

    assert "Round resumed." in [event["message"] for event in events]
    assert "Session reset." not in [event["message"] for event in events]


def test_repeated_legacy_round_started_event_is_normalized_as_resume(tmp_path):
    round_dir = tmp_path / "round_4"
    round_dir.mkdir()
    query_dir = round_dir / "query_001"
    query_dir.mkdir()
    (query_dir / "checkpoint.json").write_text(json.dumps({
        "checkpoint_timestamp": "2026-01-01T09:30:00Z",
    }), encoding="utf-8")
    (round_dir / "round_activity.jsonl").write_text("\n".join([
        json.dumps({"timestamp": "2026-01-01T09:00:00Z", "event_type": "round_started"}),
        json.dumps({"timestamp": "2026-01-01T10:00:00Z", "event_type": "round_started"}),
    ]), encoding="utf-8")

    assert [event["message"] for event in app_module._build_round_activity_jsonl_events(round_dir)] == [
        "Round started.", "Round resumed.",
    ]


def test_unproven_repeated_legacy_start_is_not_synthesized_as_resume(tmp_path):
    round_dir = tmp_path / "round_4"
    round_dir.mkdir()
    activity = round_dir / "round_activity.jsonl"
    activity.write_text("\n".join([
        json.dumps({"timestamp": "2026-01-01T09:00:00Z", "event_type": "round_started"}),
        json.dumps({"timestamp": "2026-01-01T10:00:00Z", "event_type": "round_started"}),
    ]), encoding="utf-8")
    original = activity.read_text(encoding="utf-8")

    first_poll = app_module._build_round_activity_jsonl_events(round_dir)
    browser_refresh = app_module._build_round_activity_jsonl_events(round_dir)

    assert [event["message"] for event in first_poll] == ["Round started."]
    assert browser_refresh == first_poll
    assert activity.read_text(encoding="utf-8") == original


def test_backend_emits_native_resume_lifecycle_event():
    backend = (
        Path(app_module.__file__).resolve().parents[2]
        / "discoveryagent" / "AIR" / "hypothesis_brancher.py"
    ).read_text(encoding="utf-8")

    assert '"round_resumed" if mid_round_resume_state is not None else "round_started"' in backend


def test_activity_endpoint_reports_planned_count_and_eligible_reports(monkeypatch, tmp_path):
    rounds = tmp_path / "checkpoints"
    round_dir = rounds / "round_3"
    round_dir.mkdir(parents=True)
    (round_dir / "config.json").write_text(
        json.dumps({"round_idx": 3, "config": {"num_queries": 12}}),
        encoding="utf-8",
    )
    for number in range(1, 13):
        query_dir = round_dir / f"query_{number:03d}"
        query_dir.mkdir()
        (query_dir / f"query_{number:03d}.md").write_text(
            f"# Query {number}\n\nInvestigating result {number}.", encoding="utf-8"
        )
        tag = "web_search" if number in {8, 10} else "execution_agent"
        (query_dir / "checkpoint.json").write_text(json.dumps({
            "workflow_state": {
                "results": [{
                    "query_num": number,
                    "query_raw": f"<{tag}>Investigate result {number}.</{tag}>",
                    "query_parsed": f"Investigate result {number}.",
                    "result": f"Completed result {number}.",
                    "timestamp": f"2026-01-01T10:{number:02d}:00",
                }],
            },
        }), encoding="utf-8")
    (round_dir / "round_report.md").write_text("# Report", encoding="utf-8")
    (round_dir / "memory_report.md").write_text("# Memory", encoding="utf-8")
    monkeypatch.setattr(
        app_module, "_round_workspace_checkpoint_dir_for_session", lambda: rounds
    )
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True

    response = client.get("/api/round_activity", query_string={"round": 3})
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["round_index"] == 3
    assert payload["round_status"] == "completed"
    assert payload["planned_query_count"] == 12
    assert payload["created_query_count"] == 12
    assert payload["has_round_report"] is True
    assert payload["has_memory_report"] is True
    assert [row["query_id"] for row in payload["discovery_activity"] if row["kind"] == "query"] == [
        f"query_{number:03d}" for number in range(1, 13)
    ]
    query_rows = [row for row in payload["discovery_activity"] if row["kind"] == "query"]
    assert len(query_rows) == 12
    assert all(len(row["subagent_calls"]) == 1 for row in query_rows)
    assert query_rows[7]["subagent_calls"][0]["agent_role"] == "Web Search Agent"
    assert query_rows[0]["subagent_calls"][0]["task_summary"] == "Investigate result 1."


def test_legacy_activity_excludes_bootstrap_and_abandoned_numeric_dirs(tmp_path):
    round_dir = tmp_path / "round_0"
    round_dir.mkdir()

    bootstrap = round_dir / "query_000"
    bootstrap.mkdir()
    (bootstrap / "checkpoint.json").write_text(json.dumps({
        "checkpoint_type": "hypothesis_generation",
        "query_num": 0,
        "hypothesis_output": {"hypotheses": []},
        "workflow_state": {"results": []},
    }), encoding="utf-8")
    (bootstrap / "query_summary.txt").write_text(
        "Query artifacts created; objective not yet summarized.", encoding="utf-8"
    )

    for number in range(1, 11):
        query_dir = round_dir / f"query_{number:03d}"
        query_dir.mkdir()
        (query_dir / f"query_{number:03d}.md").write_text(
            f"# Query {number}\n\nScientific result {number}.", encoding="utf-8"
        )
        (query_dir / "query_summary.txt").write_text(
            f"Exploring whether condition {number} influences the measured outcome.", encoding="utf-8"
        )
        (query_dir / "checkpoint.json").write_text(json.dumps({
            "query_num": number,
            "workflow_state": {"results": [{
                "query_num": number,
                "query_raw": f"<execution_agent>Test objective {number}.</execution_agent>",
                "query_parsed": f"Test objective {number}.",
                "result": "completed",
                "timestamp": f"2026-01-01T10:{number:02d}:00Z",
            }]},
        }), encoding="utf-8")

    abandoned = round_dir / "query_011"
    abandoned.mkdir()
    (abandoned / "query_summary.txt").write_text(
        "Query artifacts created; objective not yet summarized.", encoding="utf-8"
    )
    (abandoned / "execution_agent").mkdir()
    (abandoned / "execution_agent" / "messages.jsonl").write_text("", encoding="utf-8")
    (round_dir / "round_report.md").write_text("# Round report", encoding="utf-8")

    timeline = app_module._build_discovery_activity_timeline(round_dir, None, None, [])
    queries = [row for row in timeline if row["kind"] == "query"]

    assert [row["query_id"] for row in queries] == [
        f"query_{number:03d}" for number in range(1, 11)
    ]
    assert [row["query_summary"] for row in queries] == [
        f"Exploring whether condition {number} influences the measured outcome."
        for number in range(1, 11)
    ]
    assert len({row["query_id"] for row in queries}) == 10
    assert all(row["subagent_calls"] for row in queries)


def test_modern_status_only_query_remains_visible(tmp_path):
    round_dir = tmp_path / "round_4"
    query_dir = round_dir / "query_012"
    query_dir.mkdir(parents=True)
    (query_dir / "query_status.json").write_text(json.dumps({
        "query_num": 12,
        "status": "running",
        "objective": "Test the current scientific objective.",
        "summary": "Query is running; results have not been summarized yet.",
        "started_at": "2026-01-01T12:00:00Z",
    }), encoding="utf-8")

    queries = [
        row for row in app_module._build_discovery_activity_timeline(
            round_dir, None, None, [],
        ) if row["kind"] == "query"
    ]

    assert [row["query_id"] for row in queries] == ["query_012"]
    assert queries[0]["status"] == "running"


def test_activity_unknown_planned_count_is_not_fabricated(monkeypatch, tmp_path):
    rounds = tmp_path / "checkpoints"
    query_dir = rounds / "round_1" / "query_001"
    query_dir.mkdir(parents=True)
    (query_dir / "query_001.md").write_text("# Query 1\n\nTesting an observation.", encoding="utf-8")
    monkeypatch.setattr(
        app_module, "_round_workspace_checkpoint_dir_for_session", lambda: rounds
    )
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True

    payload = client.get(
        "/api/round_activity", query_string={"round": 1}
    ).get_json()

    assert payload["round_status"] == "running"
    assert payload["planned_query_count"] is None
    assert payload["created_query_count"] == 1


def test_normalized_activity_uses_operational_steps_not_objective(tmp_path):
    query_dir = tmp_path / "query_001"
    query_dir.mkdir()
    (query_dir / "query_status.json").write_text(json.dumps({
        "query_num": 1,
        "status": "completed",
        "current_step": "completed",
        "objective": "Whether the available evidence supports the proposed relationship.",
        "summary": "A concise outcome.",
        "started_at": "2026-01-01T10:00:00Z",
        "completed_at": "2026-01-01T10:10:00Z",
        "artifacts_written": [],
    }), encoding="utf-8")
    groups = {"query_001": [
        {"event_type": "query_step", "message": "executing_code", "timestamp": "2026-01-01T10:01:00"},
        {"event_type": "query_step", "message": "analyzing_results", "timestamp": "2026-01-01T10:05:00"},
        {"event_type": "query_step", "message": "writing_query_report", "timestamp": "2026-01-01T10:08:00"},
    ]}

    item = app_module._build_discovery_query_item(
        query_dir, 1, {}, groups, False, tmp_path / "round_report.md",
        False, None,
    )

    assert item["objective"].startswith("Whether")
    assert item["activity_category"] == "Report writing"
    assert item["activity_summary"] == "Documented the query findings and supporting evidence."
    assert not item["activity_summary"].startswith("Whether")
    assert [step["kind"] for step in item["steps"]] == [
        "computational_analysis", "result_validation", "report_writing",
    ]
    assert [step["sequence"] for step in item["steps"]] == [1, 2, 3]


def test_repeated_low_level_events_group_and_running_step_wins(tmp_path):
    query_dir = tmp_path / "query_002"
    query_dir.mkdir()
    (query_dir / "query_status.json").write_text(json.dumps({
        "query_num": 2, "status": "running", "current_step": "analyzing_results",
        "objective": "Evaluate the current objective.", "summary": "Still running.",
        "started_at": "2026-01-01T10:00:00", "updated_at": "2026-01-01T10:04:00",
        "artifacts_written": [],
    }), encoding="utf-8")
    groups = {"query_002": [
        {"event_type": "query_step", "message": "running_literature_check"},
        {"event_type": "query_step", "message": "running_literature_check"},
        {"event_type": "query_step", "message": "executing_code"},
    ]}

    item = app_module._build_discovery_query_item(
        query_dir, 2, {}, groups, False, tmp_path / "round_report.md",
        False, None,
    )

    assert [step["kind"] for step in item["steps"]] == [
        "literature_search", "computational_analysis", "result_validation",
    ]
    assert item["activity_category"] == "Result validation"
    assert item["steps"][-1]["status"] == "running"


def test_controlled_helper_call_is_genuine_subagent_without_raw_fields(tmp_path):
    log_text = (
        "2026-01-01 10:00:00 - INFO - [ACTION] helper_agent_called - run\n"
        "tag=execution_agent\nagent_type=InternalExecutionClass\nquery_num=3\nquery_chars=120\n"
    )
    calls = app_module._helper_calls_by_query(log_text)

    assert list(calls) == ["query_003"]
    assert calls["query_003"][0]["label"] == "Execution Agent"
    serialized = json.dumps(calls)
    assert "InternalExecutionClass" not in serialized
    assert "query_chars" not in serialized


def test_persisted_checkpoint_exposes_genuine_completed_subagent_call(tmp_path):
    query_dir = tmp_path / "query_003"
    query_dir.mkdir()
    (query_dir / "checkpoint.json").write_text(json.dumps({
        "workflow_state": {
            "results": [{
                "query_num": 3,
                "query_raw": (
                    "**Thought**: delegate this work\n"
                    "<execution_agent>\n"
                    "Test whether the observed relationship survives a matched control.\n"
                    "Use /private/research/input.csv for the analysis.\n"
                    "</execution_agent>"
                ),
                "query_parsed": (
                    "Test whether the observed relationship survives a matched control.\n"
                    "Use /private/research/input.csv for the analysis."
                ),
                "result": "Completed analysis output that must never reach the activity API.",
                "timestamp": "2026-01-01T10:10:00",
            }],
        },
    }), encoding="utf-8")

    calls = app_module._persisted_subagent_calls(
        query_dir, 3, "2026-01-01T10:00:00",
    )

    assert calls == [{
        "id": calls[0]["id"],
        "agent_role": "Execution Agent",
        "task_summary": "Test whether the observed relationship survives a matched control.",
        "status": "completed",
        "sequence": 1,
        "started_at": "2026-01-01T10:00:00Z",
        "completed_at": "2026-01-01T10:10:00Z",
    }]
    serialized = json.dumps(calls)
    assert "Completed analysis output" not in serialized
    assert "/private/" not in serialized


def test_verbose_delegated_task_is_separate_and_safely_reduced(tmp_path):
    query_dir = tmp_path / "query_004"
    query_dir.mkdir()
    (query_dir / "query_status.json").write_text(json.dumps({
        "query_num": 4,
        "status": "completed",
        "objective": "Determine whether the proposed relationship is supported.",
        "summary": "Concise legacy query summary.",
        "started_at": "2026-01-01T10:00:00Z",
        "completed_at": "2026-01-01T10:10:00Z",
    }), encoding="utf-8")
    raw_task = (
        "**Evaluate the remaining evidence.**\n\n"
        "## Context:\nPrior material and internal directives.\n\n"
        "**Files:** /private/project/input.csv\n"
        "```python\nprint('internal code')\n```\n"
        "1. Load the private input.\n"
        "2. Execute the detailed implementation.\n"
        "[truncated]"
    )
    (query_dir / "checkpoint.json").write_text(json.dumps({
        "workflow_state": {"results": [{
            "query_num": 4,
            "query_raw": f"<execution_agent>{raw_task}</execution_agent>",
            "query_parsed": raw_task,
            "result": "Private result payload.",
            "timestamp": "2026-01-01T10:10:00Z",
        }]},
    }), encoding="utf-8")

    item = app_module._build_discovery_query_item(
        query_dir, 4, {}, {}, False, tmp_path / "round_report.md", False, None,
    )

    assert item["query_summary"] == "Concise legacy query summary."
    assert item["query_objective"] == "Determine whether the proposed relationship is supported."
    assert item["subagent_calls"][0]["task_summary"] == "Evaluate the remaining evidence."
    response_shape = json.dumps(item)
    assert "/private/" not in response_shape
    assert "internal code" not in response_shape
    assert "detailed implementation" not in response_shape
    assert "[truncated]" not in response_shape
    assert raw_task not in response_shape


def test_verbose_objective_is_sanitized_before_api_serialization(tmp_path):
    query_dir = tmp_path / "query_005"
    query_dir.mkdir()
    verbose_objective = (
        "Evaluate the proposed relationship.\n\n"
        "**Context:** private prior material\n"
        "**Files:** /private/project/input.csv\n"
        "1. Run the implementation.\n"
        "[truncated]"
    )
    (query_dir / "query_status.json").write_text(json.dumps({
        "query_num": 5,
        "status": "completed",
        "objective": verbose_objective,
        "summary": "Concise legacy query summary.",
    }), encoding="utf-8")

    item = app_module._build_discovery_query_item(
        query_dir, 5, {}, {}, False, tmp_path / "round_report.md", False, None,
    )

    assert item["query_objective"] == "Evaluate the proposed relationship."
    serialized = json.dumps(item)
    assert verbose_objective not in serialized
    assert "/private/" not in serialized
    assert "[truncated]" not in serialized


def test_activity_query_summary_cache_remains_authoritative(tmp_path):
    query_dir = tmp_path / "query_001"
    query_dir.mkdir()
    cached = "Historical concise scientific summary remains fully authoritative."
    (query_dir / "query_summary.txt").write_text(cached, encoding="utf-8")

    summary = app_module._build_query_timeline_summary(
        query_dir,
        {"summary": "A different native summary with enough meaningful words."},
        [],
        "completed",
    )

    assert summary == cached


def test_activity_accepts_concise_native_query_summary(tmp_path):
    query_dir = tmp_path / "query_001"
    query_dir.mkdir()
    native = "Ribosomal stoichiometry differs between resilient retinal ganglion cell subtypes."

    summary = app_module._build_query_timeline_summary(
        query_dir, {"summary": native}, [], "completed",
    )

    assert summary == native


def test_activity_extracts_plain_summary_from_native_markdown_report(tmp_path):
    query_dir = tmp_path / "query_001"
    query_dir.mkdir()
    report = (
        "# Ribosomal Protein Stoichiometry Analysis\n"
        "## Comprehensive Findings Report\n\n"
        "**Analysis Date:** 2026-08-06\n"
        "**Query:** Test whether ribosomal stoichiometry differs across resilient RGC subtypes.\n"
        "**Status:** Fully completed.\n\n"
        "## Overview\n"
        "The analysis found reproducible subtype-associated differences. [truncated]"
    )

    summary = app_module._build_query_timeline_summary(
        query_dir, {"summary": report}, [], "completed",
    )

    assert summary == "Test whether ribosomal stoichiometry differs across resilient RGC subtypes."
    assert "#" not in summary
    assert "[truncated]" not in summary


def test_activity_extracts_first_prose_when_report_has_no_labeled_summary(tmp_path):
    query_dir = tmp_path / "query_001"
    query_dir.mkdir()
    report = (
        "# Findings Report\n\n"
        "**Analysis Date:** 2026-08-06\n\n"
        "## Overview\n"
        "The analysis found reproducible subtype-associated differences across the tested cells.\n"
        "[truncated]"
    )

    summary = app_module._build_query_timeline_summary(
        query_dir, {"summary": report}, [], "completed",
    )

    assert summary == (
        "The analysis found reproducible subtype-associated differences across the tested cells."
    )


def test_activity_report_garbage_falls_back_to_completed(tmp_path):
    query_dir = tmp_path / "query_001"
    query_dir.mkdir()

    summary = app_module._build_query_timeline_summary(
        query_dir,
        {"summary": "# Report\n## Comprehensive Findings Report\n[truncated]"},
        [],
        "completed",
    )

    assert summary == "Query completed."


def test_native_query_task_wins_over_project_metadata_in_result_report(tmp_path):
    query_dir = tmp_path / "query_002"
    query_dir.mkdir()
    native_task = (
        "Please execute the following analyses continuing prior work.\n\n"
        "## Context from Previous Analysis\nPrior context.\n\n"
        "## Analysis 1: Identify Key Discriminating Ribosomal Proteins\nDetails.\n"
        "## Analysis 2: Leave-One-Out RRS Analysis\nDetails.\n"
    )
    (query_dir / "checkpoint.json").write_text(json.dumps({
        "workflow_state": {"results": [{
            "query_num": 2, "query_parsed": native_task,
            "query_raw": f"<execution_agent>{native_task}</execution_agent>",
            "result": "# Report\n**Project:** Optic Nerve Regeneration\n[truncated]",
        }]},
    }), encoding="utf-8")
    info = {
        "summary": "# Report\n**Project:** Optic Nerve Regeneration — Integrated Datasets\n[truncated]",
        "objective": native_task,
    }

    summary = app_module._build_query_timeline_summary(
        query_dir, info, [], "completed",
    )

    assert summary == (
        "Identify Key Discriminating Ribosomal Proteins; Leave-One-Out RRS Analysis"
    )
    assert "Project:" not in summary


def test_report_extraction_rejects_project_and_other_context_metadata(tmp_path):
    query_dir = tmp_path / "query_002"
    query_dir.mkdir()
    report = (
        "# Report\n"
        "**Project:** Optic Nerve Regeneration\n"
        "**Dataset:** RGC Atlas\n"
        "**Files:** input.h5ad\n"
        "**Background:** Prior work\n"
        "The requested analysis compares ribosomal profiles across resilient subtypes.\n"
        "[truncated]"
    )

    summary = app_module._build_query_timeline_summary(
        query_dir, {"summary": report}, [], "completed",
    )

    assert summary == "The requested analysis compares ribosomal profiles across resilient subtypes."


def test_execution_agent_summary_uses_numbered_scientific_tasks_not_generic_lead(tmp_path):
    query_dir = tmp_path / "query_002"
    query_dir.mkdir()
    task = (
        "Please execute the following three analyses continuing prior work.\n\n"
        "## Context\nFiles and prior results.\n"
        "## Analysis 1: Identify Key Discriminating Ribosomal Proteins\n"
        "## Analysis 2: Test Leave-One-Out RRS Contributions\n"
        "## Analysis 3: Compare Zebrafish 7DPI Stoichiometric Profiles\n"
    )
    _write_persisted_calls(query_dir, [
        _persisted_call(2, "execution_agent", task, "inv-1", "2026-01-01T10:00:00Z"),
    ])

    calls = app_module._persisted_subagent_calls(query_dir, 2)

    assert len(calls) == 1
    assert calls[0]["agent_role"] == "Execution Agent"
    assert calls[0]["task_summary"].startswith("Identify Key Discriminating Ribosomal Proteins;")
    assert "Please execute the following" not in calls[0]["task_summary"]
    assert "Context" not in calls[0]["task_summary"]


def test_query_what_prefers_helper_request_over_director_budget_planning(tmp_path):
    query_dir = tmp_path / "query_003"
    query_dir.mkdir()
    task = (
        "Please execute the following two analyses.\n\n"
        "## Analysis 1: Regulatory Support for Candidate Genes\n"
        "## Analysis 2: Intervention Response — Signature Predicts Response\n"
    )
    raw = (
        "**Interpretation**: Prior results established the candidate signal.\n\n"
        "**Thought**: Given that I have 7 queries left, the most efficient approach "
        "is to run one analysis and then finalize the report.\n\n"
        f"<execution_agent>{task}</execution_agent>"
    )
    record = _persisted_call(
        3, "execution_agent", task, "inv-1", "2026-01-01T10:00:00Z",
    )
    record["query_raw"] = raw
    _write_persisted_calls(query_dir, [record])
    info = {
        "summary": "# Report\n**Project:** Metadata must not win.\n[truncated]",
        "objective": task,
    }

    what = app_module._build_query_timeline_summary(query_dir, info, [], "completed")
    calls = app_module._persisted_subagent_calls(query_dir, 3)

    assert what == (
        "Regulatory Support for Candidate Genes; Intervention Response — Signature Predicts Response"
    )
    assert calls[0]["task_summary"] == (
        "Assess Regulatory support for Candidate Genes; "
        "analyze Intervention Response to assess signature predicts response."
    )
    assert calls[0]["task_summary"] != what
    assert "Please execute the following" not in calls[0]["task_summary"]
    assert "queries left" not in what
    assert "most efficient" not in what


def test_scientist_query_request_retains_entities_and_final_synthesis_goal():
    request = (
        "Please compare Cohort A and Cohort B in Dataset North to test whether "
        "GeneX and GeneY track the measured intervention response.\n\n"
        "## Files\n/private/run/input.csv"
    )
    synthesis = (
        "Please create a final synthesis assessing whether Pathway Z remains "
        "supported across Dataset East and Dataset West and identify uncertainties."
    )

    assert app_module._scientist_facing_query_request(request) == (
        "Compare Cohort A and Cohort B in Dataset North to test whether GeneX and "
        "GeneY track the measured intervention response."
    )
    assert app_module._scientist_facing_query_request(synthesis) == (
        "Create a final synthesis assessing whether Pathway Z remains supported "
        "across Dataset East and Dataset West and identify uncertainties."
    )


def test_recovered_format_retry_is_hidden_but_native_events_are_unchanged(tmp_path):
    round_dir = tmp_path / "round_2"
    query_dir = round_dir / "query_003"
    query_dir.mkdir(parents=True)
    (query_dir / "query_status.json").write_text(json.dumps({
        "query_num": 3, "status": "completed",
        "objective": "Compare the two experimental cohorts.",
        "summary": "Compare the two experimental cohorts.",
        "started_at": "2026-01-01T10:00:00Z",
        "completed_at": "2026-01-01T10:05:00Z",
    }), encoding="utf-8")
    events = [{
        "timestamp": "2026-01-01T10:01:00Z", "query": None,
        "message": "Error encountered: Tool invocation did not match expected format "
                   "(attempt 1/3). Asking the agent to try again.",
    }]
    original = json.loads(json.dumps(events))

    timeline = app_module._build_discovery_activity_timeline(
        round_dir, None, None, events,
    )

    assert not any(row.get("milestone_type") == "round_error" for row in timeline)
    assert events == original


def test_provider_failure_is_hidden_only_when_fallback_succeeds(tmp_path):
    round_dir = tmp_path / "round_2"
    round_dir.mkdir()
    events = [
        {"timestamp": "2026-01-01T10:00:00Z", "query": None,
         "message": "Error encountered: Retrieval provider A failed."},
        {"timestamp": "2026-01-01T10:01:00Z", "query": None,
         "message": "Literature fallback provider completed successfully."},
    ]

    timeline = app_module._build_discovery_activity_timeline(
        round_dir, None, None, events,
    )

    assert not any(row.get("milestone_type") == "round_error" for row in timeline)


def test_blocking_query_and_dependency_errors_remain_visible(tmp_path):
    round_dir = tmp_path / "round_2"
    query_dir = round_dir / "query_003"
    query_dir.mkdir(parents=True)
    (query_dir / "query_status.json").write_text(json.dumps({
        "query_num": 3, "status": "failed",
        "objective": "Compare the experimental cohorts.",
        "summary": "Comparison failed.",
        "started_at": "2026-01-01T10:00:00Z",
        "completed_at": "2026-01-01T10:02:00Z",
    }), encoding="utf-8")
    events = [
        {"timestamp": "2026-01-01T10:01:00Z", "query": None,
         "message": "Error encountered: Parser retry attempt 1/3 failed."},
        {"timestamp": "2026-01-01T10:02:00Z", "query": None,
         "message": "Error encountered: Required retrieval dependency is unavailable; user intervention required."},
    ]

    timeline = app_module._build_discovery_activity_timeline(
        round_dir, None, None, events,
    )
    errors = [row for row in timeline if row.get("milestone_type") == "round_error"]

    assert len(errors) == 2
    assert any("retry" in row["summary"].lower() for row in errors)
    assert any("user intervention" in row["summary"].lower() for row in errors)


def test_separate_invocations_keep_distinct_how_summaries(tmp_path):
    query_dir = tmp_path / "query_004"
    query_dir.mkdir()
    _write_persisted_calls(query_dir, [
        _persisted_call(
            4, "execution_agent", "Analyze the regulatory evidence for the candidates.",
            "inv-1", "2026-01-01T10:00:00Z",
        ),
        _persisted_call(
            4, "deep_research", "Identify published intervention evidence for the mechanism.",
            "inv-2", "2026-01-01T10:01:00Z",
        ),
    ])

    calls = app_module._persisted_subagent_calls(query_dir, 4)

    assert [call["task_summary"] for call in calls] == [
        "Analyze the regulatory evidence for the candidates.",
        "Identify published intervention evidence for the mechanism.",
    ]
    assert len({call["task_summary"] for call in calls}) == 2


def test_delegated_task_explicit_title_and_malformed_fallback(tmp_path):
    concise = app_module._delegated_task_summary("**Review the current evidence.**")
    assert concise == "Review the current evidence."

    fallback = app_module._delegated_task_summary(
        "## Requirements:\n1. Follow these instructions.\n```text\n/private/data\n```",
        fallback_summary="Concise normalized query summary.",
    )
    assert fallback == "Concise normalized query summary."


def test_delegated_task_summary_prioritizes_scientific_goal_over_file_loading():
    summary = app_module._delegated_task_summary(
        "Quick analysis to resolve two outlier subtypes from the resilience hypothesis analysis. "
        "Load `outputs/resilience_analysis/step4_rrs_per_subtype.csv` and "
        "`outputs/resilience_analysis/step2_subtype_enrichment_ratios.csv`."
    )

    assert summary == "Resolve two outlier subtypes from the resilience hypothesis analysis."
    assert "outputs/" not in summary


def test_delegated_task_summary_reduces_output_path_to_filename():
    summary = app_module._delegated_task_summary(
        "Append the outlier analysis findings to the existing "
        "FINAL_HYPOTHESIS_SYNTHESIS.md file at "
        "`outputs/resilience_analysis/FINAL_HYPOTHESIS_SYNTHESIS.md`."
    )

    assert summary == "Add the outlier analysis findings to FINAL_HYPOTHESIS_SYNTHESIS.md."
    assert "outputs/" not in summary


def test_delegated_task_summary_keeps_action_that_references_output_path():
    summary = app_module._delegated_task_summary(
        "Continue the AlphaONS resilience hypothesis analysis. "
        "Build on the output files already saved at "
        "/runs/optic_nerve/outputs/resilience_analysis/.\n\n"
        "**Context from prior step**:\n"
        "- 39 RGC subtypes were classified by resilience."
    )

    assert summary == "Continue the AlphaONS resilience hypothesis analysis."


def test_delegated_task_summary_stops_before_inline_context():
    summary = app_module._delegated_task_summary(
        "Complete the AlphaONS resilience hypothesis analysis with two remaining tasks. "
        "Context: Prior analysis established significantly higher readiness scores."
    )

    assert summary == (
        "Complete the AlphaONS resilience hypothesis analysis with two remaining tasks."
    )


def test_activity_frontend_keeps_summary_objective_and_task_fields_separate():
    js = (
        Path(app_module.app.static_folder) / "round_workspace.js"
    ).read_text(encoding="utf-8")
    activity_js = js[
        js.index("function normalizeActivity"):
        js.index("function renderActivityQueryStream")
    ]

    assert "let summary=row.query_summary||row.summary||''" in activity_js
    assert "unfinished&&roundStatus==='interrupted'" in activity_js
    assert "status='interrupted';summary='Query interrupted.'" in activity_js
    assert "unfinished&&roundStatus==='failed'" in activity_js
    assert "status='failed';summary='Query failed.'" in activity_js
    assert "item.status==='interrupted'?'■'" in activity_js
    assert "const objective=row.query_objective||row.objective||summary" in activity_js
    assert "rw-activity-query-summary',item.summary" in activity_js
    assert "'Research objective'" not in activity_js
    assert "rw-activity-objective',item.objective" not in activity_js
    assert "detail.task_summary" in activity_js
    assert "rw-activity-query-summary',detail.task_summary" not in activity_js


def _persisted_call(
    query_num,
    tag,
    task,
    invocation_id,
    timestamp,
    *,
    status="completed",
):
    return {
        "query_num": query_num,
        "query_raw": f"<{tag}>{task}</{tag}>",
        "query_parsed": task,
        "result": "Safe result marker." if status == "completed" else None,
        "error": "Safe failure marker." if status == "failed" else None,
        "status": status,
        "invocation_id": invocation_id,
        "started_at": timestamp,
        "timestamp": timestamp,
    }


def _write_persisted_calls(query_dir, calls):
    (query_dir / "checkpoint.json").write_text(json.dumps({
        "workflow_state": {"results": calls},
    }), encoding="utf-8")


def test_multiple_different_agent_roles_preserve_execution_order(tmp_path):
    query_dir = tmp_path / "query_004"
    query_dir.mkdir()
    _write_persisted_calls(query_dir, [
        _persisted_call(4, "web_search", "Retrieve relevant evidence.", "inv-1", "2026-01-01T10:00:00Z"),
        _persisted_call(4, "execution_agent", "Analyze the available material.", "inv-2", "2026-01-01T10:01:00Z"),
    ])

    calls = app_module._persisted_subagent_calls(query_dir, 4)

    assert [call["agent_role"] for call in calls] == ["Web Search Agent", "Execution Agent"]
    assert [call["sequence"] for call in calls] == [1, 2]


def test_same_agent_role_called_twice_remains_two_calls(tmp_path):
    query_dir = tmp_path / "query_004"
    query_dir.mkdir()
    _write_persisted_calls(query_dir, [
        _persisted_call(4, "execution_agent", "Perform the initial analysis.", "inv-1", "2026-01-01T10:00:00Z"),
        _persisted_call(4, "execution_agent", "Repeat the analysis with revised inputs.", "inv-2", "2026-01-01T10:01:00Z"),
    ])

    calls = app_module._persisted_subagent_calls(query_dir, 4)

    assert len(calls) == 2
    assert [call["agent_role"] for call in calls] == ["Execution Agent", "Execution Agent"]
    assert calls[0]["id"] != calls[1]["id"]


def test_identical_task_summaries_with_distinct_invocations_have_distinct_ids(tmp_path):
    query_dir = tmp_path / "query_004"
    query_dir.mkdir()
    task = "Evaluate the current evidence."
    _write_persisted_calls(query_dir, [
        _persisted_call(4, "execution_agent", task, "inv-1", "2026-01-01T10:00:00Z"),
        _persisted_call(4, "execution_agent", task, "inv-2", "2026-01-01T10:01:00Z"),
    ])

    calls = app_module._persisted_subagent_calls(query_dir, 4)

    assert [call["task_summary"] for call in calls] == [task, task]
    assert len({call["id"] for call in calls}) == 2


def test_failed_call_and_successful_retry_remain_distinct(tmp_path):
    query_dir = tmp_path / "query_004"
    query_dir.mkdir()
    _write_persisted_calls(query_dir, [
        _persisted_call(4, "execution_agent", "Evaluate the material.", "inv-failed", "2026-01-01T10:00:00Z", status="failed"),
        _persisted_call(4, "execution_agent", "Retry the evaluation.", "inv-retry", "2026-01-01T10:01:00Z"),
    ])

    calls = app_module._persisted_subagent_calls(query_dir, 4)

    assert [call["status"] for call in calls] == ["failed", "completed"]
    assert [call["sequence"] for call in calls] == [1, 2]
    assert calls[0]["id"] != calls[1]["id"]


def test_three_sequential_calls_preserve_order(tmp_path):
    query_dir = tmp_path / "query_004"
    query_dir.mkdir()
    records = [
        _persisted_call(4, "web_search", "Retrieve evidence.", "inv-1", "2026-01-01T10:00:00Z"),
        _persisted_call(4, "execution_agent", "Analyze evidence.", "inv-2", "2026-01-01T10:01:00Z"),
        _persisted_call(4, "deep_research", "Check the conclusion.", "inv-3", "2026-01-01T10:02:00Z"),
    ]
    _write_persisted_calls(query_dir, records)

    calls = app_module._persisted_subagent_calls(query_dir, 4)

    assert [call["sequence"] for call in calls] == [1, 2, 3]
    assert [call["task_summary"] for call in calls] == [
        "Retrieve evidence.", "Analyze evidence.", "Check the conclusion.",
    ]


def test_actual_invocation_request_wins_over_parent_like_display_metadata(tmp_path):
    query_dir = tmp_path / "query_004"
    query_dir.mkdir()
    record = _persisted_call(
        4, "execution_agent", "Analyze matched RNA and ATAC profiles.", "inv-1",
        "2026-01-01T10:00:00Z",
    )
    record["description"] = "Parent query text that must not replace the delegated request."
    _write_persisted_calls(query_dir, [record])

    calls = app_module._persisted_subagent_calls(query_dir, 4)

    assert calls[0]["agent_role"] == "Execution Agent"
    assert calls[0]["task_summary"] == "Analyze matched RNA and ATAC profiles."


def test_execution_agent_summary_uses_delegated_task_sections(tmp_path):
    query_dir = tmp_path / "query_004"
    query_dir.mkdir()
    parent_query = (
        "Investigate regulatory-transcriptional relationships across the regional "
        "pathology gradient using the Multiome data."
    )
    delegated = (
        parent_query + "\n\n"
        "1. **Analyze matched RNA and ATAC profiles**: Compare molecular layers.\n"
        "2. **Regional and donor comparison**: Preserve paired donor structure.\n"
        "Files: /managed/project/code/data/input.h5ad\n"
    )
    _write_persisted_calls(query_dir, [
        _persisted_call(
            4, "execution_agent", delegated, "inv-1", "2026-01-01T10:00:00Z",
        ),
    ])

    call = app_module._persisted_subagent_calls(query_dir, 4)[0]

    assert call["task_summary"] == (
        "Analyze matched RNA and ATAC profiles; compare regional and donor profiles."
    )
    assert call["task_summary"] != parent_query
    assert "/managed/" not in call["task_summary"]


def test_parent_and_execution_agent_summaries_differ_with_step_sections(tmp_path):
    # Director prompts open by restating the parent objective before the real
    # instructions; the nested sub-agent row must describe the delegated step
    # sections, not repeat that restated lead.
    query_dir = tmp_path / "query_001"
    query_dir.mkdir()
    parent_objective = (
        "Enumerate all supertypes within the astrocyte, oligodendrocyte, and "
        "microglia-PVM subclasses in both MTG and DLPFC regions."
    )
    delegated = (
        parent_objective + "\n\n"
        "**Step 1: Load the MTG and DLPFC AnnData files**\n"
        "Use /managed/project/data/sea_ad.h5ad for all steps.\n\n"
        "**Step 2: Enumerate supertypes per glial subclass**\n"
        "Count cells per supertype in each region.\n\n"
        "**Step 3: Compare supertype composition across regions**\n"
        "Report shared and region-specific supertypes.\n"
    )
    (query_dir / "query_status.json").write_text(json.dumps({
        "query_num": 1,
        "status": "completed",
        "objective": parent_objective,
        "started_at": "2026-01-01T10:00:00Z",
        "completed_at": "2026-01-01T10:10:00Z",
    }), encoding="utf-8")
    _write_persisted_calls(query_dir, [
        _persisted_call(1, "execution_agent", delegated, "inv-1", "2026-01-01T10:05:00Z"),
    ])

    item = app_module._build_discovery_query_item(
        query_dir, 1, {}, {}, False, tmp_path / "round_report.md", False, None,
    )
    call = item["subagent_calls"][0]

    assert item["query_summary"].startswith("Enumerate all supertypes")
    assert call["task_summary"] == (
        "Enumerate supertypes per glial subclass; "
        "compare supertype composition across regions."
    )
    assert call["task_summary"] != item["query_summary"]
    assert "Load the MTG" not in call["task_summary"]
    assert "/managed/" not in call["task_summary"]


def test_deep_research_summary_uses_question_sections(tmp_path):
    query_dir = tmp_path / "query_005"
    query_dir.mkdir()
    delegated = (
        "Please conduct a focused literature search on the SEA-AD project to identify:\n\n"
        "**QUESTION 1: Published SEA-AD methods**\n"
        "Cover cohort design and pipelines.\n\n"
        "**QUESTION 2: Cross-regional Alzheimer's findings**\n"
        "Cover MTG versus DLPFC differences.\n"
    )
    _write_persisted_calls(query_dir, [
        _persisted_call(5, "deep_research", delegated, "inv-1", "2026-01-01T10:00:00Z"),
    ])

    call = app_module._persisted_subagent_calls(query_dir, 5)[0]

    assert call["agent_role"] == "Deep Research Agent"
    assert call["task_summary"] == (
        "Search the literature on the SEA-AD project for published SEA-AD "
        "methods, and cross-regional Alzheimer's findings."
    )
    assert not call["task_summary"].startswith("Please")


def test_subagent_summary_strips_role_boilerplate_and_paths(tmp_path):
    query_dir = tmp_path / "query_006"
    query_dir.mkdir()
    delegated = (
        "You are an execution agent.\n"
        "Please analyze ribosomal stoichiometry across supertypes using "
        "/managed/project/data/sea_ad.h5ad and report effect sizes.\n"
    )
    _write_persisted_calls(query_dir, [
        _persisted_call(6, "execution_agent", delegated, "inv-1", "2026-01-01T10:00:00Z"),
    ])

    call = app_module._persisted_subagent_calls(query_dir, 6)[0]

    assert call["task_summary"] == (
        "Analyze ribosomal stoichiometry across supertypes using sea_ad.h5ad "
        "and report effect sizes."
    )
    assert "/managed/" not in call["task_summary"]
    assert "You are" not in call["task_summary"]
    assert not call["task_summary"].startswith("Please")


def test_parent_summary_is_last_resort_for_empty_invocation_body(tmp_path):
    query_dir = tmp_path / "query_007"
    query_dir.mkdir()
    (query_dir / "checkpoint.json").write_text(json.dumps({
        "workflow_state": {"results": [{
            "query_num": 7,
            "query_raw": "<execution_agent>\n</execution_agent>",
            "query_parsed": "",
            "result": "done",
            "timestamp": "2026-01-01T10:10:00Z",
        }]},
    }), encoding="utf-8")
    parent = "Compare glial supertype composition between MTG and DLPFC regions."

    with_fallback = app_module._persisted_subagent_calls(
        query_dir, 7, fallback_summary=parent,
    )
    without_fallback = app_module._persisted_subagent_calls(query_dir, 7)

    assert with_fallback[0]["task_summary"] == parent
    assert without_fallback[0]["task_summary"] == "Execution Agent completed."


def test_parent_fallback_never_overrides_distinct_delegated_task(tmp_path):
    query_dir = tmp_path / "query_008"
    query_dir.mkdir()
    _write_persisted_calls(query_dir, [
        _persisted_call(
            8, "execution_agent",
            "Quantify supertype abundance shifts along the pathology gradient.",
            "inv-1", "2026-01-01T10:00:00Z",
        ),
    ])

    calls = app_module._persisted_subagent_calls(
        query_dir, 8,
        fallback_summary="Parent objective that must not replace the delegated task.",
    )

    assert calls[0]["task_summary"] == (
        "Quantify supertype abundance shifts along the pathology gradient."
    )


def test_running_query_keeps_running_summary_while_subagent_shows_task(tmp_path):
    round_dir = tmp_path / "round_2"
    query_dir = round_dir / "query_002"
    query_dir.mkdir(parents=True)
    (query_dir / "query_status.json").write_text(json.dumps({
        "query_num": 2,
        "status": "running",
        "objective": "Enumerate all supertypes within the glial subclasses in MTG and DLPFC.",
        "summary": "Query is running; results have not been summarized yet.",
        "started_at": "2026-01-01T12:00:00Z",
    }), encoding="utf-8")
    _write_persisted_calls(query_dir, [
        _persisted_call(
            2, "execution_agent",
            "Enumerate all supertypes within the glial subclasses in MTG and DLPFC.\n\n"
            "**Step 1: Enumerate supertypes per subclass**\nCount cells.\n\n"
            "**Step 2: Compare regional composition**\nSummarize differences.\n",
            "inv-1", "2026-01-01T12:01:00Z", status="running",
        ),
    ])

    item = app_module._build_discovery_query_item(
        query_dir, 2, {}, {}, False, round_dir / "round_report.md", False, None,
    )

    assert item["status"] == "running"
    assert item["query_summary"] == (
        "Query is running; results have not been summarized yet."
    )
    assert item["subagent_calls"][0]["status"] == "running"
    assert item["subagent_calls"][0]["task_summary"] == (
        "Enumerate supertypes per subclass; compare regional composition."
    )


def test_subagent_summary_pipeline_makes_no_llm_calls():
    import ast
    import inspect
    import re as re_module
    import textwrap

    called_names = set()
    for fn in (
        app_module._persisted_subagent_calls,
        app_module._subagent_activity_task_summary,
        app_module._invocation_action_summary,
        app_module._delegated_task_summary,
        app_module._plain_delegated_task_text,
    ):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                called_names.add(
                    func.attr if isinstance(func, ast.Attribute)
                    else getattr(func, "id", "")
                )

    assert not any(
        re_module.search(r"llm|openai|anthropic|claude|chat|invoke|post|request", name)
        for name in called_names
    )


def test_deep_research_summary_uses_literature_invocation_topics(tmp_path):
    query_dir = tmp_path / "query_003"
    query_dir.mkdir()
    delegated = (
        "Please conduct a focused literature search on the SEA-AD project to identify:\n\n"
        "1. **What has already been published**: Review methods and findings.\n"
        "2. **Unexplored territories**: Find cross-regional gaps.\n"
        "3. **Region-specific responses**: Compare MTG and DLPFC.\n"
        "4. **Methodological gaps**: Identify missing analyses.\n"
    )
    _write_persisted_calls(query_dir, [
        _persisted_call(
            3, "deep_research", delegated, "inv-1", "2026-01-01T10:00:00Z",
        ),
    ])

    call = app_module._persisted_subagent_calls(query_dir, 3)[0]

    assert call["task_summary"] == (
        "Search the literature on the SEA-AD project for published findings, "
        "unexplored directions, region-specific responses, and methodological gaps."
    )
    assert not call["task_summary"].startswith("Please")


def test_subagent_markdown_uses_labeled_task_without_rendering_report(tmp_path):
    query_dir = tmp_path / "query_004"
    query_dir.mkdir()
    report_like_task = (
        "# Differential Expression Analysis\n"
        "## Results\n"
        "**Task:** Compare ribosomal protein expression across resilient and susceptible RGC subtypes.\n"
        "We performed a comprehensive analysis of all cells. [truncated]"
    )
    _write_persisted_calls(query_dir, [
        _persisted_call(4, "execution_agent", report_like_task, "inv-1", "2026-01-01T10:00:00Z"),
    ])

    calls = app_module._persisted_subagent_calls(query_dir, 4)

    assert calls[0]["task_summary"] == (
        "Compare ribosomal protein expression across resilient and susceptible RGC subtypes."
    )
    assert "#" not in calls[0]["task_summary"]
    assert "[truncated]" not in calls[0]["task_summary"]


def test_subagent_markdown_falls_back_to_first_substantive_prose(tmp_path):
    query_dir = tmp_path / "query_004"
    query_dir.mkdir()
    report_like_task = (
        "# Literature Review\n"
        "**Analysis Date:** 2026-01-01\n"
        "## Overview\n"
        "Identify evidence linking ribosomal stoichiometry to neuronal resilience.\n"
        "[truncated]"
    )
    _write_persisted_calls(query_dir, [
        _persisted_call(4, "web_search", report_like_task, "inv-1", "2026-01-01T10:00:00Z"),
    ])

    calls = app_module._persisted_subagent_calls(query_dir, 4)

    assert calls[0]["agent_role"] == "Web Search Agent"
    assert calls[0]["task_summary"] == (
        "Identify evidence linking ribosomal stoichiometry to neuronal resilience."
    )


def test_subagent_calls_are_normalized_independently_and_keep_identity(tmp_path):
    query_dir = tmp_path / "query_004"
    query_dir.mkdir()
    _write_persisted_calls(query_dir, [
        _persisted_call(
            4, "web_search", "**Task:** Identify evidence linking RP ratios to resilience.\n[truncated]",
            "inv-1", "2026-01-01T10:00:00Z",
        ),
        _persisted_call(
            4, "execution_agent", "**Objective:** Test subtype-specific differences in RP ratios.\n[truncated]",
            "inv-2", "2026-01-01T10:01:00Z",
        ),
    ])

    calls = app_module._persisted_subagent_calls(query_dir, 4)

    assert [call["agent_role"] for call in calls] == ["Web Search Agent", "Execution Agent"]
    assert [call["task_summary"] for call in calls] == [
        "Identify evidence linking RP ratios to resilience.",
        "Test subtype-specific differences in RP ratios.",
    ]
    assert len({call["id"] for call in calls}) == 2


def test_unusable_subagent_task_gets_clean_agent_fallback(tmp_path):
    query_dir = tmp_path / "query_004"
    query_dir.mkdir()
    _write_persisted_calls(query_dir, [
        _persisted_call(
            4, "execution_agent", "# Report\n## Results\n[truncated]",
            "inv-1", "2026-01-01T10:00:00Z",
        ),
    ])

    calls = app_module._persisted_subagent_calls(query_dir, 4)

    assert calls[0]["agent_role"] == "Execution Agent"
    assert calls[0]["task_summary"] == "Execution Agent completed."


def test_subagent_activity_never_uses_scientific_result_as_task_summary(tmp_path):
    query_dir = tmp_path / "query_004"
    query_dir.mkdir()
    record = _persisted_call(
        4, "execution_agent", "# Report\n## Results\n[truncated]", "inv-1",
        "2026-01-01T10:00:00Z",
    )
    record["result"] = "A scientific conclusion that must not become the Activity task."
    _write_persisted_calls(query_dir, [record])

    calls = app_module._persisted_subagent_calls(query_dir, 4)

    assert calls[0]["task_summary"] == "Execution Agent completed."
    assert record["result"] not in json.dumps(calls)


def test_polling_appends_call_without_duplicating_first(tmp_path):
    query_dir = tmp_path / "query_004"
    query_dir.mkdir()
    first = _persisted_call(4, "execution_agent", "Perform the initial analysis.", "inv-1", "2026-01-01T10:00:00Z")
    second = _persisted_call(4, "execution_agent", "Perform the follow-up analysis.", "inv-2", "2026-01-01T10:01:00Z")
    _write_persisted_calls(query_dir, [first])
    initial = app_module._persisted_subagent_calls(query_dir, 4)
    _write_persisted_calls(query_dir, [first, second])

    updated = app_module._persisted_subagent_calls(query_dir, 4)

    assert len(initial) == 1 and len(updated) == 2
    assert updated[0]["id"] == initial[0]["id"]
    assert [call["sequence"] for call in updated] == [1, 2]


def test_exact_duplicate_persistence_record_is_deduplicated(tmp_path):
    query_dir = tmp_path / "query_004"
    query_dir.mkdir()
    record = _persisted_call(
        4, "execution_agent", "Evaluate the current evidence.", "inv-1",
        "2026-01-01T10:00:00Z",
    )
    _write_persisted_calls(query_dir, [record, dict(record)])

    calls = app_module._persisted_subagent_calls(query_dir, 4)

    assert len(calls) == 1


def test_exact_duplicate_without_authoritative_id_is_deduplicated(tmp_path):
    query_dir = tmp_path / "query_004"
    query_dir.mkdir()
    record = _persisted_call(
        4, "execution_agent", "Evaluate the current evidence.", "inv-1",
        "2026-01-01T10:00:00Z",
    )
    record.pop("invocation_id")
    _write_persisted_calls(query_dir, [record, dict(record)])

    calls = app_module._persisted_subagent_calls(query_dir, 4)

    assert len(calls) == 1


def test_stable_call_ids_do_not_change_across_identical_polls(tmp_path):
    query_dir = tmp_path / "query_004"
    query_dir.mkdir()
    records = [
        _persisted_call(4, "execution_agent", "Perform the initial analysis.", "inv-1", "2026-01-01T10:00:00Z"),
        _persisted_call(4, "execution_agent", "Perform the follow-up analysis.", "inv-2", "2026-01-01T10:01:00Z"),
    ]
    _write_persisted_calls(query_dir, records)

    first_poll = app_module._persisted_subagent_calls(query_dir, 4)
    second_poll = app_module._persisted_subagent_calls(query_dir, 4)

    assert [call["id"] for call in first_poll] == [call["id"] for call in second_poll]


def test_frontend_caret_and_expansion_render_every_call_with_stable_identity():
    js = (
        Path(app_module.app.static_folder) / "round_workspace.js"
    ).read_text(encoding="utf-8")
    activity_js = js[
        js.index("function normalizeActivity"):
        js.index("function renderActivityQueryStream")
    ]

    assert "const hasCalls=item.details.length>0" in activity_js
    assert "item.details.forEach(detail=>" in activity_js
    assert "if(detail.id)call.dataset.callId=detail.id" in activity_js
    assert "state.activityExpanded.has(expansionKey)" in activity_js
    assert "state.activityExpanded.delete(expansionKey)" in activity_js


def test_no_persisted_invocation_means_no_subagent_calls(tmp_path):
    query_dir = tmp_path / "query_004"
    query_dir.mkdir()
    (query_dir / "checkpoint.json").write_text(json.dumps({
        "workflow_state": {
            "results": [{
                "query_num": 4,
                "query_raw": "The execution agent should investigate this.",
                "query_parsed": "Investigate this.",
                "result": "A result",
                "timestamp": "2026-01-01T10:10:00",
            }],
        },
    }), encoding="utf-8")

    assert app_module._persisted_subagent_calls(query_dir, 4) == []


def test_no_structured_detail_uses_neutral_fallback(tmp_path):
    query_dir = tmp_path / "query_004"
    query_dir.mkdir()
    (query_dir / "query_004.md").write_text(
        "# Whether a proposed relationship is supported", encoding="utf-8"
    )
    item = app_module._build_discovery_query_item(
        query_dir, 4, {}, {}, False, tmp_path / "round_report.md", False, None,
    )
    assert item["steps"] == []
    assert item["subagent_calls"] == []
    assert item["activity_category"] == "Research investigation"
    assert item["activity_summary"] == "Investigating the current research objective."


def test_incomplete_round_suppresses_report_artifacts(monkeypatch, tmp_path):
    round_dir = tmp_path / "round_4"
    round_dir.mkdir()
    (round_dir / "round_report.md").write_text("# Partial report", encoding="utf-8")
    (round_dir / "memory_report.md").write_text("# Partial memory", encoding="utf-8")
    monkeypatch.setattr(app_module, "_v2_round_status", lambda _info: "running")

    entry = app_module._round_workspace_entry(4, round_dir, 4)
    names = [child["name"] for child in entry["tree"]["children"]]
    assert "round_report.md" not in names
    assert "memory_report.md" not in names
    assert entry["has_round_report"] is False


def test_runner_failure_marks_exact_workspace_round_failed(tmp_path):
    checkpoint = tmp_path / "checkpoint"
    round_dir = checkpoint / "checkpoints" / "round_4"
    (round_dir / "query_001").mkdir(parents=True)
    (checkpoint / "run_state.json").write_text(json.dumps({
        "round": 4,
        "status": "failed",
        "pid": 123,
        "exit_code": 1,
    }), encoding="utf-8")

    entry = app_module._round_workspace_entry(4, round_dir, 4)
    assert entry["status"] == "failed"
    # The same project-level state must not contaminate a different native round.
    other = checkpoint / "checkpoints" / "round_3"
    (other / "query_001").mkdir(parents=True)
    assert app_module._round_workspace_entry(3, other, 3)["status"] == "running"


def test_runner_interruption_marks_exact_workspace_round_stopped(tmp_path):
    checkpoint = tmp_path / "checkpoint"
    round_dir = checkpoint / "checkpoints" / "round_4"
    (round_dir / "query_001").mkdir(parents=True)
    (checkpoint / "run_state.json").write_text(json.dumps({
        "round": 4,
        "status": "interrupted",
        "pid": None,
        "exit_code": -15,
    }), encoding="utf-8")

    entry = app_module._round_workspace_entry(4, round_dir, 4)

    assert entry["status"] == "interrupted"


def test_completed_native_output_wins_over_stale_runner_failure(tmp_path):
    checkpoint = tmp_path / "checkpoint"
    round_dir = checkpoint / "checkpoints" / "round_4"
    round_dir.mkdir(parents=True)
    (round_dir / "round_report.md").write_text("report", encoding="utf-8")
    (round_dir / "memory_report.md").write_text("memory", encoding="utf-8")
    (checkpoint / "run_state.json").write_text(
        json.dumps({"round": 4, "status": "failed"}), encoding="utf-8"
    )
    assert app_module._round_workspace_entry(4, round_dir, 4)["status"] == "completed"


def test_completed_report_is_ordinary_sorted_primary_file(tmp_path):
    round_dir = tmp_path / "round_3"
    (round_dir / "analysis").mkdir(parents=True)
    (round_dir / "execution_agent").mkdir()
    (round_dir / "analysis" / "summary.md").write_text("Summary", encoding="utf-8")
    (round_dir / "execution_agent" / "transcript.txt").write_text("Trace", encoding="utf-8")
    (round_dir / "round_report.md").write_text("# Complete report", encoding="utf-8")
    (round_dir / "selected_hypothesis.md").write_text("Selected", encoding="utf-8")

    entry = app_module._round_workspace_entry(3, round_dir, 3)
    children = entry["tree"]["children"]
    names = [child["name"] for child in children]
    assert names == ["selected_hypothesis.md", "analysis", "execution_agent", "round_report.md"]
    report = next(child for child in children if child["name"] == "round_report.md")
    assert report["path"] == "round_report.md"
    assert report["primary_output"] is True
    assert entry["has_round_report"] is True


def test_report_uses_standard_viewer_and_activity_action_only():
    static = Path(app_module.app.static_folder)
    template = Path(app_module.app.root_path) / app_module.app.template_folder / "round_workspace.html"
    js = (static / "round_workspace.js").read_text(encoding="utf-8")
    css = (static / "round_workspace.css").read_text(encoding="utf-8")
    html = template.read_text(encoding="utf-8")

    assert "item.primary_output" in js and "rw-primary-star" in js
    assert "const disclosure = node('span', 'rw-tree-disclosure-slot')" in js
    assert "disclosure.append(node('span', 'rw-tree-caret', '>'))" in js
    assert "disclosure.setAttribute('aria-hidden','true')" in js
    assert "<svg viewBox=\"0 0 16 16\" width=\"10\" height=\"10\"" in js
    assert ".rw-tree-node.is-file>.rw-tree-row{display:flex;align-items:center;height:25px;min-height:25px;line-height:16px}" in css
    assert ".rw-tree-disclosure-slot{display:inline-flex;align-items:center;justify-content:center;align-self:center;flex:0 0 12px;width:12px;height:16px;line-height:16px}" in css
    assert ".rw-tree-disclosure-slot svg{display:block;flex:0 0 auto;width:10px;height:10px}" in css
    assert ".rw-tree-node.is-file>.rw-tree-row>.rw-tree-name{display:block;flex:1 1 auto;min-width:0;line-height:16px;text-align:left}" in css
    assert ".rw-primary-star{color:var(--v2-accent);pointer-events:none}" in css
    assert "rw-primary-asterisk" not in js
    star_rule = css[css.index(".rw-primary-star{"):]
    star_rule = star_rule[:star_rule.index("}")]
    assert all(offset not in star_rule for offset in ("translateY", "margin-top", "top:", "padding"))
    assert "if(item.primary_output)" in js
    assert "'▧'" not in js
    assert "rw-tree-file-spacer" not in js
    assert "'·'" not in js and "★" not in js
    assert "aria-hidden" in js
    assert "path: item.path, name: item.name" in js
    assert "e.dataTransfer.setData('application/json', JSON.stringify(file))" in js
    assert "tab.draggable=true" in js and "state.dragSource={pane:i,tab:tabIndex}" in js
    assert "function detachDraggedTab(source)" in js
    assert "pane.tabs.splice(source.tab,1)" in js
    assert "if(source&&state.panes.length<2)" in js
    assert "sourcePane.tabs.length<2" in js
    assert "openFile(moved.file,true,direction)" in js
    assert "if(source&&state.panes.length===2)" in js
    assert "sourcePane.tabs.length===1" in js
    assert "state.panes=before?[sourcePane,otherPane]:[otherPane,sourcePane]" in js
    assert "state.orientation=['top','bottom'].includes(direction)?'vertical':'horizontal'" in js
    assert "openFile(moved.file,false,direction)" in js
    assert "dx<0?'left':'right'" in js and "dy<0?'top':'bottom'" in js
    assert "centerTarget>=0?centerTarget" in js
    assert "px>=.2&&px<=.8&&py>=.2&&py<=.8" in js
    assert "explorerMayReplace=!state.dragSource&&state.panes.length<2" in js
    assert "const direction=centerTarget>=0||explorerMayReplace?'center'" in js
    assert "if(direction==='center'&&source&&state.panes.length===2)" in js
    assert "if(state.panes.length<2){openFile(file,true,direction);}" in js
    assert "state.panes.length<2" in js and "state.activePane=['left','top'].includes(direction)?0:1" in js
    assert "ext === '.md'" in js and "rw-markdown" in js
    assert "isFigureFile" in js and "rw-artifact-image" in js
    assert "openReviewRoundReport" in js
    assert "if(state.initialDefaultApplied||!reviewEntry)return" in js
    assert "if(report)openFile(report,false)" in js
    assert "openInitialRoundReport" not in js
    assert "rw-open-round-report" in js and "openFile(report,false)" in js
    assert "function activityRoundReportFile(data,roundIndex)" in js
    assert "artifactPath:'round_report.md'" in js
    assert "openFile(report,false,null,true)" not in js
    assert "rw-file-tabs" in js and "rw-file-tab" in js
    assert "rw-file-tab-close" in js and "Close '+tabFile.name" in js
    assert "if(!paneModel.tabs.length){state.panes.splice(i,1);state.activePane=0;state.paneRatio=50;}" in js
    assert "function compactEmptyPanes()" in js
    assert "const populated=state.panes.filter(pane=>pane&&Array.isArray(pane.tabs)&&pane.tabs.length)" in js
    assert "function renderPanes() {\n    compactEmptyPanes();" in js
    assert "rw-pane-empty" in js and "Open a file from the round explorer." in js
    assert "rw-pane-breadcrumb" in js and "aria-label','File path" in js
    assert "function fileLocation(file)" in js and "String(file.path||file.name).split('/')" in js
    assert "rw-pane-download" not in js and "rw-pane-context" not in js
    assert "'/api/round_workspace_artifact?round='" in js
    assert "'/checkpoint_assets/round_'" in js
    assert "file.sourceKind==='external'" in js
    assert "payload.display_text??payload.markdown" in js
    assert "renderReportMarkdown(article,text,figures)" in js
    assert "cache:'default'" in js
    assert "file.sourceKind===nextKind" in js
    assert "file.sourceKind==='project-code'" in js
    assert "'/api/round_workspace_code_artifact?path='" in js
    assert "file.size=item.size??file.size;return;" in js
    assert "'&source='" in js and "file.artifactPath || file.path" in js
    assert "rw-report" not in html
    assert "Round Report" not in html


def test_workspace_artifact_path_is_round_scoped(monkeypatch, tmp_path):
    rounds = tmp_path / "checkpoints"
    artifact = rounds / "round_2" / "query_001" / "result.csv"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("gene,score\nKLF6,1\n", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("no", encoding="utf-8")
    monkeypatch.setattr(app_module, "_round_workspace_checkpoint_dir_for_session", lambda: rounds)

    assert app_module._round_workspace_artifact_path(2, "query_001/result.csv") == artifact.resolve()
    assert app_module._round_workspace_artifact_path(2, "../../secret.txt") is None
    assert app_module._round_workspace_artifact_path(3, "query_001/result.csv") is None


def test_workspace_artifact_endpoint_supports_legacy_checkpoint_tabs(monkeypatch, tmp_path):
    artifact = tmp_path / "query_001.md"
    artifact.write_text("# Restored file", encoding="utf-8")
    monkeypatch.setattr(
        app_module,
        "_round_workspace_artifact_path",
        lambda round_index, relative_path: (
            artifact if round_index == 2 and relative_path == "query_001/query_001.md" else None
        ),
    )
    client = app_module.app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["authenticated"] = True
    response = client.get("/api/round_workspace_artifact", query_string={
        "round": 2,
        "path": "query_001/query_001.md",
    })
    assert response.status_code == 200
    assert response.data == b"# Restored file"


def _external_artifact_fixture(monkeypatch, tmp_path, round_index=2):
    project = tmp_path / "project"
    rounds = project / "checkpoint" / "checkpoints"
    round_dir = rounds / f"round_{round_index}"
    code = project / "code"
    figure = code / "outputs" / "figures" / "regeneration_score.png"
    tracked = project / "checkpoint" / "images" / "img_004.png"
    round_dir.mkdir(parents=True)
    figure.parent.mkdir(parents=True)
    tracked.parent.mkdir(parents=True)
    figure.write_bytes(b"figure")
    tracked.write_bytes(b"tracked")
    (round_dir / "round_report.md").write_text(
        "# Figures\n\n"
        "![score](outputs/figures/regeneration_score.png)\n"
        "`images/img_004.png`\n",
        encoding="utf-8",
    )
    (round_dir / "config.json").write_text(
        json.dumps({
            "round_idx": round_index,
            "config": {
                "working_directory": "/remote/project",
                "log_dir": "/remote/logs/run-1",
                "checkpoint_base_dir": "/remote/logs/run-1/checkpoints",
            },
        }),
        encoding="utf-8",
    )
    record = {
        "id": "project-1",
        "checkpoint_dir": str(round_dir),
        "code_dir": str(code),
    }
    monkeypatch.setattr(app_module, "_round_workspace_checkpoint_dir_for_session", lambda: round_dir)
    monkeypatch.setattr(app_module, "_active_project_record", lambda: record)
    return round_dir, figure, tracked


def test_trusted_round_registry_aggregates_external_figures(monkeypatch, tmp_path):
    round_dir, figure, tracked = _external_artifact_fixture(monkeypatch, tmp_path)
    with app_module.app.test_request_context("/"):
        registry = app_module._round_workspace_artifact_registry(2)
        assert {source["kind"] for source in registry.values()} == {
            "checkpoint", "execution-output", "run-artifacts",
        }
        output_id = next(key for key, value in registry.items() if value["kind"] == "execution-output")
        run_id = next(key for key, value in registry.items() if value["kind"] == "run-artifacts")
        assert app_module._round_workspace_resolve_artifact(
            2, output_id, "outputs/figures/regeneration_score.png"
        ) == figure.resolve()
        assert app_module._round_workspace_resolve_artifact(
            2, run_id, "images/img_004.png"
        ) == tracked.resolve()

        entry = app_module._round_workspace_entry(2, round_dir, 2, registry)
        assert "outputs" not in {child["name"] for child in entry["tree"]["children"]}
        code_tree = app_module._round_workspace_code_tree()
        output = next(child for child in code_tree["children"] if child["name"] == "outputs")
        external = output["children"][0]["children"][0]
        assert external["visibility"] == "research"
        assert external["path"] == "outputs/figures/regeneration_score.png"
        assert external["artifact_path"] == "outputs/figures/regeneration_score.png"
        assert external["source_kind"] == "project-code"
        assert not str(tmp_path) in json.dumps(entry)


def test_round_registry_reuses_cache_and_invalidates_when_round_state_changes(
    monkeypatch, tmp_path
):
    round_dir, _figure, _tracked = _external_artifact_fixture(monkeypatch, tmp_path)
    calls = 0
    original = app_module._build_round_workspace_artifact_registry

    def counted(round_index):
        nonlocal calls
        calls += 1
        return original(round_index)

    monkeypatch.setattr(app_module, "_build_round_workspace_artifact_registry", counted)
    app_module._ROUND_WORKSPACE_ARTIFACT_CACHE.clear()

    with app_module.app.test_request_context("/"):
        first = app_module._round_workspace_artifact_registry(2)
        second = app_module._round_workspace_artifact_registry(2)
        assert first is second
    with app_module.app.test_request_context("/"):
        third = app_module._round_workspace_artifact_registry(2)
        assert third is first
    assert calls == 1

    report = round_dir / "round_report.md"
    report.write_text(report.read_text(encoding="utf-8") + "\nupdated\n", encoding="utf-8")
    with app_module.app.test_request_context("/"):
        refreshed = app_module._round_workspace_artifact_registry(2)
    assert refreshed is not first
    assert calls == 2


def test_round_workspace_tree_reuses_unchanged_round_entries(
    monkeypatch, tmp_path
):
    rounds = tmp_path / "checkpoints"
    for index in (0, 1):
        query = rounds / f"round_{index}" / "query_001"
        query.mkdir(parents=True)
        (query / "query_001.md").write_text(f"# Round {index}", encoding="utf-8")
        (query / "query_status.json").write_text(
            json.dumps({"status": "completed", "started_at": f"2026-01-0{index + 1}T00:00:00Z"}),
            encoding="utf-8",
        )
    monkeypatch.setattr(app_module, "_round_workspace_checkpoint_dir_for_session", lambda: rounds)
    monkeypatch.setattr(app_module, "_v2_current_studio_round_index", lambda *_args: 1)
    monkeypatch.setattr(app_module, "_active_project_record", lambda: None)
    app_module._ROUND_WORKSPACE_TREE_CACHE.clear()
    app_module._ROUND_WORKSPACE_ROUND_ENTRY_CACHE.clear()
    app_module._ROUND_WORKSPACE_ARTIFACT_CACHE.clear()

    calls = []
    original = app_module._round_workspace_entry

    def counted(index, *args, **kwargs):
        calls.append(index)
        return original(index, *args, **kwargs)

    monkeypatch.setattr(app_module, "_round_workspace_entry", counted)
    client = app_module.app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["authenticated"] = True
        flask_session["v2_active_project_slug"] = "cache-test"

    assert client.get("/api/round_workspace_tree").status_code == 200
    assert calls == [0, 1]

    status = rounds / "round_1" / "query_001" / "query_status.json"
    status.write_text(
        json.dumps({"status": "running", "started_at": "2026-01-02T00:00:00Z"}),
        encoding="utf-8",
    )
    assert client.get("/api/round_workspace_tree").status_code == 200
    assert calls == [0, 1, 1]


def test_external_registry_deduplicates_references_and_rejects_forgery(monkeypatch, tmp_path):
    round_dir, figure, _tracked = _external_artifact_fixture(monkeypatch, tmp_path)
    with (round_dir / "round_report.md").open("a", encoding="utf-8") as report:
        report.write(
            "\n`/remote/project/outputs/figures/regeneration_score.png`\n"
            "`outputs/figures/regeneration_score.png`\n"
        )
    with app_module.app.test_request_context("/"):
        registry = app_module._round_workspace_artifact_registry(2)
        output_id = next(key for key, value in registry.items() if value["kind"] == "execution-output")
        assert len(registry[output_id]["files"]) == 1
        assert app_module._round_workspace_resolve_artifact(2, "forged", "outputs/figures/regeneration_score.png") is None
        assert app_module._round_workspace_resolve_artifact(2, output_id, "../regeneration_score.png") is None
        assert app_module._round_workspace_resolve_artifact(3, output_id, "outputs/figures/regeneration_score.png") is None
        assert figure.exists()


def test_code_tree_artifacts_use_project_level_serving(monkeypatch, tmp_path):
    round_dir, figure, _tracked = _external_artifact_fixture(monkeypatch, tmp_path)
    with app_module.app.test_request_context("/"):
        tree = app_module._round_workspace_code_tree()
        external = next(child for child in tree["children"] if child["name"] == "outputs")
        external = external["children"][0]["children"][0]
    client = app_module.app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["authenticated"] = True
    response = client.get("/api/round_workspace_code_artifact", query_string={
        "path": external["artifact_path"],
    })
    assert response.status_code == 200
    assert response.data == figure.read_bytes()
    assert client.get("/api/round_workspace_code_artifact", query_string={
        "path": "../outside.png",
    }).status_code == 404
    assert client.get("/api/round_workspace_code_artifact", query_string={
        "path": "other/result.png",
    }).status_code == 404


def test_external_registry_rejects_cross_project_and_symlink_escape(monkeypatch, tmp_path):
    round_dir, _figure, _tracked = _external_artifact_fixture(monkeypatch, tmp_path)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"secret")
    linked = round_dir.parents[2] / "code" / "outputs" / "figures" / "linked.png"
    linked.symlink_to(outside)
    with (round_dir / "round_report.md").open("a", encoding="utf-8") as report:
        report.write("\n`outputs/figures/linked.png`\n")
    with app_module.app.test_request_context("/"):
        registry = app_module._round_workspace_artifact_registry(2)
        output = next(value for value in registry.values() if value["kind"] == "execution-output")
        assert "outputs/figures/linked.png" not in output["files"]

    other = tmp_path / "other-project" / "code"
    other.mkdir(parents=True)
    monkeypatch.setattr(app_module, "_active_project_record", lambda: {
        "id": "project-1", "checkpoint_dir": str(round_dir), "code_dir": str(other),
    })
    with app_module.app.test_request_context("/"):
        registry = app_module._round_workspace_artifact_registry(2)
        assert {source["kind"] for source in registry.values()} == {"checkpoint"}


def test_legacy_round_does_not_scan_external_directories(monkeypatch, tmp_path):
    project = tmp_path / "project"
    round_dir = project / "checkpoint" / "checkpoints" / "round_1"
    unrelated = project / "code" / "outputs" / "figures" / "unrelated.png"
    round_dir.mkdir(parents=True)
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b"unrelated")
    (round_dir / "query_001").mkdir()
    (round_dir / "query_001" / "query_001.md").write_text("finding", encoding="utf-8")
    monkeypatch.setattr(app_module, "_round_workspace_checkpoint_dir_for_session", lambda: round_dir)
    monkeypatch.setattr(app_module, "_active_project_record", lambda: {
        "id": "legacy", "checkpoint_dir": str(round_dir), "code_dir": str(project / "code"),
    })
    with app_module.app.test_request_context("/"):
        registry = app_module._round_workspace_artifact_registry(1)
        assert {source["kind"] for source in registry.values()} == {"checkpoint"}
        entry = app_module._round_workspace_entry(1, round_dir, 1, registry)
        assert "unrelated.png" not in json.dumps(entry)


def test_workspace_filter_excludes_monitor_internals():
    ignored = app_module.ROUND_WORKSPACE_TREE_IGNORED_NAMES
    assert {"checkpoint.json", "messages.jsonl", "tool_calls.jsonl", "round_activity.jsonl"} <= ignored


def test_curated_tree_classifies_content_and_recursively_prunes_hidden(tmp_path):
    round_dir = tmp_path / "round_2"
    query = round_dir / "query_001"
    execution = query / "execution_agent"
    hidden_only = round_dir / "internal_only"
    execution.mkdir(parents=True)
    hidden_only.mkdir()
    (query / "query_001.md").write_text("# Scientific finding", encoding="utf-8")
    (execution / "transcript.txt").write_text("technical trace", encoding="utf-8")
    (execution / "messages.jsonl").write_text("{}", encoding="utf-8")
    (hidden_only / "checkpoint.json").write_text("{}", encoding="utf-8")
    (round_dir / "config.json").write_text("{}", encoding="utf-8")
    (round_dir / "selected_hypothesis.md").write_text("Selected", encoding="utf-8")
    (round_dir / "generated_hypotheses.md").write_text("Candidates", encoding="utf-8")
    (round_dir / "memory_report.md").write_text("Memory", encoding="utf-8")
    (round_dir / "round_report.md").write_text("Complete", encoding="utf-8")

    entry = app_module._round_workspace_entry(2, round_dir, 2)
    root = {child["name"]: child for child in entry["tree"]["children"]}

    assert root["selected_hypothesis.md"]["visibility"] == "research"
    assert root["generated_hypotheses.md"]["visibility"] == "technical"
    assert root["memory_report.md"]["visibility"] == "research"
    root_file_names = [child["name"] for child in entry["tree"]["children"] if child["type"] == "file"]
    assert root_file_names[0] == "selected_hypothesis.md"
    assert root_file_names[-2:] == ["memory_report.md", "round_report.md"]
    assert "config.json" not in root
    assert "internal_only" not in root
    assert root["query_001"]["visibility"] == "research"
    execution_node = next(child for child in root["query_001"]["children"] if child["name"] == "execution_agent")
    assert [child["name"] for child in execution_node["children"]] == ["transcript.txt"]
    assert execution_node["children"][0]["visibility"] == "technical"


def test_incomplete_tree_hides_reports_and_completed_tree_keeps_memory_research(tmp_path):
    round_dir = tmp_path / "round_1"
    round_dir.mkdir()
    (round_dir / "round_report.md").write_text("Partial", encoding="utf-8")
    (round_dir / "memory_report.md").write_text("Memory", encoding="utf-8")

    raw = app_module._round_workspace_tree_node(round_dir, round_dir)
    running = app_module._curate_round_workspace_tree(raw, round_dir, "running")
    assert running is not None
    assert running["children"] == []

    raw = app_module._round_workspace_tree_node(round_dir, round_dir)
    completed = app_module._curate_round_workspace_tree(raw, round_dir, "completed")
    files = {child["name"]: child for child in completed["children"]}
    assert files["round_report.md"]["visibility"] == "research"
    assert files["memory_report.md"]["visibility"] == "research"


def test_visibility_uses_context_for_literature_json_and_unknown_files(tmp_path):
    round_dir = tmp_path / "round_0"
    searches = round_dir / "searches"
    results = round_dir / "results"
    searches.mkdir(parents=True)
    results.mkdir()
    literature = searches / "paper.md"
    detail = searches / "paper.json"
    metrics = results / "metrics.json"
    unknown = round_dir / "artifact.bin"
    for path in (literature, detail, metrics, unknown):
        path.write_text("{}", encoding="utf-8")

    classify = lambda path: app_module._round_workspace_visibility(path, round_dir, "completed")
    assert classify(literature) == "technical"
    assert classify(detail) == "technical"
    assert classify(metrics) == "technical"
    assert classify(unknown) == "technical"


def test_default_research_set_is_queries_reports_memory_and_generated_figures(tmp_path):
    round_dir = tmp_path / "round_3"
    query = round_dir / "query_004"
    figures = query / "figures"
    figures.mkdir(parents=True)
    (query / "query_004.md").write_text("# Query result", encoding="utf-8")
    (query / "notes.md").write_text("Intermediate notes", encoding="utf-8")
    (query / "results.csv").write_text("gene,value\nKlf6,1", encoding="utf-8")
    (figures / "effect_size.png").write_bytes(b"png")
    (round_dir / "round_report.md").write_text("# Complete", encoding="utf-8")
    (round_dir / "memory_report.md").write_text("# Memory", encoding="utf-8")
    (round_dir / "selected_hypothesis.md").write_text("Selected", encoding="utf-8")

    entry = app_module._round_workspace_entry(3, round_dir, 3)

    def research_paths(node):
        paths = []
        for child in node.get("children", []):
            if child["type"] == "file" and child["visibility"] == "research":
                paths.append(child["path"])
            paths.extend(research_paths(child))
        return paths

    assert research_paths(entry["tree"]) == [
        "selected_hypothesis.md",
        "query_004/query_004.md",
        "query_004/figures/effect_size.png",
        "memory_report.md",
        "round_report.md",
    ]
    assert "query_004.md" in [child["name"] for child in entry["tree"]["children"]]
    root = {child["name"]: child for child in entry["tree"]["children"]}
    assert root["selected_hypothesis.md"]["visibility"] == "research"


def test_generated_figure_formats_require_recognized_scientific_locations(tmp_path):
    round_dir = tmp_path / "round_4"
    recognized = {
        "query_001/figures/plot.PNG",
        "query_001/execution_agent/output/heatmap.jpg",
        "da_query_reports/query_002/chart.SVG",
        "outputs/figures/model.webp",
        "results/summary.gif",
        "figures/section.tif",
        "visualizations/volume.TIFF",
        "logs/images/img_001.png",
    }
    unrecognized = {
        "backend/preview.png",
        "misc/photo.jpg",
        "diagnostics/chart.svg",
    }
    for relative_path in recognized | unrecognized:
        path = round_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"image")

    classify = lambda relative_path: app_module._round_workspace_visibility(
        round_dir / relative_path, round_dir, "completed"
    )
    assert all(classify(path) == "research" for path in recognized)
    assert all(classify(path) == "hidden" for path in unrecognized)


def test_generated_figures_use_standard_open_drag_and_split_viewer_paths():
    js = (
        Path(app_module.app.static_folder) / "round_workspace.js"
    ).read_text(encoding="utf-8")

    assert "function isFigureFile(name)" in js
    assert "'.tif','.tiff'" in js
    assert "if (isFigureFile(file.name))" in js
    assert "img.src = artifactUrl(file)" in js
    assert "row.addEventListener('click', () => openFile(file, false))" in js
    assert "path: item.path, name: item.name" in js
    assert "JSON.stringify(file)" in js
    assert "if(state.panes.length<2){openFile(file,true,direction);}" in js
    assert "state.panes.length<2" in js


def test_embedded_figures_are_collapsible_and_closed_by_default():
    static_root = Path(app_module.app.static_folder)
    js = (static_root / "round_workspace.js").read_text(encoding="utf-8")
    css = (static_root / "round_workspace.css").read_text(encoding="utf-8")

    assert "function collapseEmbeddedFigures(el)" in js
    assert "node('details','rw-embedded-figure')" in js
    assert "node('summary','rw-embedded-figure-summary'" in js
    assert "details.open=false" in js
    assert "collapseEmbeddedFigures(el)" in js
    assert "if(figures.length)renderReportMarkdown(article,text,figures);else renderMarkdown(article,text)" in js
    assert ".rw-embedded-figure-summary::before{content:'▸'" in css
    assert ".rw-embedded-figure[open]>.rw-embedded-figure-summary::before{transform:rotate(90deg)}" in css


def test_running_round_status_uses_an_evenly_timed_staggered_letter_bounce():
    static_root = Path(app_module.app.static_folder)
    js = (static_root / "round_workspace.js").read_text(encoding="utf-8")
    css = (static_root / "round_workspace.css").read_text(encoding="utf-8")

    assert "if(rawStatus==='running')" in js
    assert "status.classList.add('is-bouncing')" in js
    assert "node('span','rw-round-status-letter',letter)" in js
    assert "const runningBouncePeriodMs = 4000" in js
    assert "Date.now()%runningBouncePeriodMs" in js
    assert "--rw-bounce-phase" in js
    assert "--rw-letter-delay" in js
    assert "@keyframes rw-round-status-letter-bounce" in css
    assert "animation:rw-round-status-letter-bounce 4s" in css
    assert "animation-delay:calc(var(--rw-bounce-phase,0ms) + var(--rw-letter-delay,0ms))" in css
    assert "13.5%{transform:translateY(-2.5px)}" in css
    assert "@media (prefers-reduced-motion:reduce){.rw-round-status-letter{animation:none" in css


def test_embedded_figures_have_proportional_drag_resize_controls():
    static_root = Path(app_module.app.static_folder)
    js = (static_root / "round_workspace.js").read_text(encoding="utf-8")
    css = (static_root / "round_workspace.css").read_text(encoding="utf-8")

    assert "function attachEmbeddedFigureResizer(shell,stage)" in js
    assert "node('button','rw-figure-resize-handle')" in js
    assert "node('span','rw-figure-resize-icon')" in js
    assert "rw-figure-resize-arrow is-northwest" in js
    assert "rw-figure-resize-arrow is-southeast" in js
    assert "node('div','rw-figure-image-stage')" in js
    assert "stage.append(handle)" in js
    assert "Math.min(240,max)" in js
    assert "diagonalDelta=((event.clientX-drag.startX)+(event.clientY-drag.startY))/2" in js
    assert "shell.style.width=width>=range.max-1?'100%'" in js
    assert "handle.setPointerCapture(event.pointerId)" in js
    assert "if(event.key==='Home')next=range.min" in js
    assert "if(event.key==='End')next=range.max" in js
    assert ".rw-figure-resize-shell{box-sizing:border-box;width:100%;max-width:100%" in css
    assert ".rw-figure-image-stage{position:relative;width:100%;max-width:100%" in css
    assert "right:-10px;bottom:-10px" in css
    assert "border:0;border-radius:0;background:transparent" in css
    assert "cursor:nwse-resize;touch-action:none" in css
    assert ".rw-figure-resize-arrow.is-northwest" in css
    assert ".rw-figure-resize-arrow.is-southeast" in css
    assert ".rw-figure-resize-icon{position:relative;display:block;width:10px;height:10px}" in css
    assert ".rw-figure-resize-arrow{position:absolute;display:block;width:7px;height:7px" in css


def test_figure_parent_directories_survive_pruning_and_remain_sorted(tmp_path):
    round_dir = tmp_path / "round_5"
    figures = round_dir / "query_007" / "execution_agent" / "outputs" / "figures"
    figures.mkdir(parents=True)
    (figures / "b_plot.png").write_bytes(b"png")
    (figures / "a_plot.png").write_bytes(b"png")
    (figures / "scratch.tmp").write_bytes(b"temporary")

    raw = app_module._round_workspace_tree_node(round_dir, round_dir)
    curated = app_module._curate_round_workspace_tree(raw, round_dir, "completed")
    query = next(child for child in curated["children"] if child["name"] == "query_007")
    execution = next(child for child in query["children"] if child["name"] == "execution_agent")
    outputs = next(child for child in execution["children"] if child["name"] == "outputs")
    figure_dir = next(child for child in outputs["children"] if child["name"] == "figures")

    assert query["visibility"] == execution["visibility"] == outputs["visibility"] == "research"
    assert figure_dir["visibility"] == "research"
    assert [child["name"] for child in figure_dir["children"]] == ["a_plot.png", "b_plot.png"]
    assert all(child["visibility"] == "research" for child in figure_dir["children"])


def test_memory_report_requires_completed_nonempty_regular_root_file(tmp_path):
    round_dir = tmp_path / "round_6"
    round_dir.mkdir()
    memory = round_dir / "memory_report.md"
    memory.write_text("# Memory", encoding="utf-8")

    assert app_module._round_workspace_visibility(memory, round_dir, "running") == "hidden"
    assert app_module._round_workspace_visibility(memory, round_dir, "completed") == "research"

    memory.write_text("", encoding="utf-8")
    assert app_module._round_workspace_visibility(memory, round_dir, "completed") == "hidden"

    memory.unlink()
    outside = tmp_path / "outside_memory.md"
    outside.write_text("# Outside", encoding="utf-8")
    memory.symlink_to(outside)
    assert app_module._round_workspace_visibility(memory, round_dir, "completed") == "hidden"

    memory.unlink()
    memory.mkdir()
    assert app_module._round_workspace_visibility(memory, round_dir, "completed") == "hidden"


def test_memory_report_is_sorted_without_round_report_special_behavior(tmp_path):
    round_dir = tmp_path / "round_7"
    round_dir.mkdir()
    (round_dir / "round_report.md").write_text("# Round", encoding="utf-8")
    (round_dir / "memory_report.md").write_text("# Memory", encoding="utf-8")
    query = round_dir / "query_001"
    query.mkdir()
    (query / "query_001.md").write_text("# Query", encoding="utf-8")

    entry = app_module._round_workspace_entry(7, round_dir, 7)
    names = [child["name"] for child in entry["tree"]["children"]]
    assert names == ["query_001.md", "memory_report.md", "round_report.md"]
    memory = next(child for child in entry["tree"]["children"] if child["name"] == "memory_report.md")
    report = next(child for child in entry["tree"]["children"] if child["name"] == "round_report.md")
    assert "primary_output" not in memory
    assert report["primary_output"] is True


def test_round_tree_borrows_legacy_launcher_hypothesis_from_previous_round(tmp_path):
    rounds = tmp_path / "checkpoints"
    previous = rounds / "round_2"
    current = rounds / "round_3"
    previous.mkdir(parents=True)
    current.mkdir()
    (previous / "selected_hypothesis.md").write_text("Round 3 launcher", encoding="utf-8")
    (current / "query_001").mkdir()
    (current / "query_001" / "query_001.md").write_text("Result", encoding="utf-8")

    entry = app_module._round_workspace_entry(3, current, 3)
    selected = next(
        child for child in entry["tree"]["children"]
        if child["name"] == "selected_hypothesis.md"
    )

    assert selected["storage_round_index"] == 2
    assert selected["artifact_path"] == "selected_hypothesis.md"
    assert selected["visibility"] == "research"


def test_hidden_artifacts_are_not_served_by_either_checkpoint_endpoint(monkeypatch, tmp_path):
    base = tmp_path / "checkpoints"
    round_dir = base / "round_0"
    execution = round_dir / "query_001" / "execution_agent"
    execution.mkdir(parents=True)
    (round_dir / "round_report.md").write_text("# Report", encoding="utf-8")
    (execution / "transcript.txt").write_text("trace", encoding="utf-8")
    (execution / "messages.jsonl").write_text("{}", encoding="utf-8")
    (round_dir / "config.json").write_text("{}", encoding="utf-8")
    (round_dir / ".flask_session").write_text("session", encoding="utf-8")
    (round_dir / "scratch.tmp").write_text("temporary", encoding="utf-8")
    (round_dir / "checkpoint_state.json").write_text("{}", encoding="utf-8")
    cache = round_dir / "analysis_cache"
    cache.mkdir()
    (cache / "cached.md").write_text("cached", encoding="utf-8")
    (cache / "preview.png").write_bytes(b"cached image")
    backend = round_dir / "backend"
    backend.mkdir()
    (backend / "preview.png").write_bytes(b"backend image")
    figures = round_dir / "query_001" / "figures"
    figures.mkdir()
    (figures / "result.PNG").write_bytes(b"generated image")
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    (round_dir / "linked.md").symlink_to(outside)

    monkeypatch.setattr(app_module, "_get_checkpoint_base", lambda: base)
    monkeypatch.setattr(app_module, "_v2_round_status", lambda _info: "completed")
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True

    assert client.get("/checkpoint_assets/round_0/round_report.md").status_code == 200
    assert client.get("/checkpoint_assets/round_0/query_001/execution_agent/transcript.txt").status_code == 200
    assert client.get("/checkpoint_assets/round_0/query_001/figures/result.PNG").status_code == 200
    for path in (
        "round_0/config.json",
        "round_0/.flask_session",
        "round_0/scratch.tmp",
        "round_0/checkpoint_state.json",
        "round_0/analysis_cache/cached.md",
        "round_0/analysis_cache/preview.png",
        "round_0/backend/preview.png",
        "round_0/query_001/execution_agent/messages.jsonl",
        "round_0/linked.md",
        "round_0/%2e%2e/outside.md",
        "round_0/%2e%2e/round_1/report.md",
        "%2fetc/passwd",
    ):
        api_path = path.replace("%2e%2e", "..").replace("%2f", "/")
        assert client.get("/checkpoint_assets/" + path, follow_redirects=True).status_code in {403, 404}
        assert client.get("/api/checkpoint_file", query_string={"path": api_path}).status_code in {403, 404}
        assert client.get("/api/checkpoint_file", query_string={"path": path.replace("%2e%2e", "..")}).status_code in {403, 404}


def test_invalid_memory_report_is_not_servable(monkeypatch, tmp_path):
    base = tmp_path / "checkpoints"
    round_dir = base / "round_0"
    round_dir.mkdir(parents=True)
    memory = round_dir / "memory_report.md"
    memory.write_text("", encoding="utf-8")

    monkeypatch.setattr(app_module, "_get_checkpoint_base", lambda: base)
    monkeypatch.setattr(app_module, "_v2_round_status", lambda _info: "completed")
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True

    assert client.get("/checkpoint_assets/round_0/memory_report.md").status_code == 404
    assert client.get(
        "/api/checkpoint_file", query_string={"path": "round_0/memory_report.md"}
    ).status_code == 404

    memory.unlink()
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside", encoding="utf-8")
    memory.symlink_to(outside)
    assert client.get("/checkpoint_assets/round_0/memory_report.md").status_code == 404
    assert client.get(
        "/api/checkpoint_file", query_string={"path": "round_0/memory_report.md"}
    ).status_code == 404


def test_explorer_displays_only_research_classified_entries():
    static = Path(app_module.app.static_folder)
    js = (static / "round_workspace.js").read_text(encoding="utf-8")
    css = (static / "round_workspace.css").read_text(encoding="utf-8")

    assert "function visibleResearchTreeNode(item)" in js
    assert "item.visibility!=='research'" in js
    assert "if(!children.length)return null" in js
    assert ".map(visibleResearchTreeNode).filter(Boolean)" in js
    assert "Show technical files" not in js
    assert "technicalStorageKey" not in js
    assert "technicalRounds" not in js
    assert "row.title=entryTooltip(item,owner)" in js
    assert "owner.sourceScope==='code'?'Code':'Round '+owner.round_index" in js
    assert ".rw-technical-toggle" not in css
    assert ".rw-tree-row.is-technical" not in css


def test_markdown_document_uses_scoped_report_layout_and_responsive_typography():
    static = Path(app_module.app.static_folder)
    js = (static / "round_workspace.js").read_text(encoding="utf-8")
    css = (static / "round_workspace.css").read_text(encoding="utf-8")

    assert "rw-markdown rw-markdown-document" in js
    assert ".rw-markdown-document{" in css
    document_rules = css[css.index(".rw-markdown-document{"):]
    assert "width:100%" in document_rules
    assert "max-width:none" in document_rules
    assert "margin:0" in document_rules
    assert "max-width:780px" not in document_rules
    assert "font:400 1rem/1.62 var(--v2-sans)" in document_rules
    assert ".rw-markdown-document h1" in document_rules and "font-size:2.05rem" in document_rules
    assert ".rw-markdown-document h2" in document_rules and "font-size:1.65rem" in document_rules
    assert ".rw-markdown-document h3" in document_rules and "font-size:1.32rem" in document_rules
    assert "@container (max-width:600px)" in document_rules
    assert "@container (max-width:380px)" in document_rules


def test_text_file_content_aligns_with_first_round_chat_label():
    css = (Path(app_module.app.static_folder) / "round_workspace.css").read_text(encoding="utf-8")

    assert ".rw-chat-actions{display:flex;flex:0 0 auto;justify-content:flex-end;min-height:38px" in css
    assert ".rw-chat-messages{padding-top:8px}" in css
    assert ".rw-pane-breadcrumb" in css and "flex:0 0 41px" in css
    assert "padding:5px clamp(28px,4cqi,48px) 44px" in css
    assert ".rw-markdown-document{padding:5px 24px 34px" in css
    assert ".rw-markdown-document{padding:5px 18px 30px" in css
    assert ".rw-code{padding-top:5px}" in css


def test_markdown_lists_have_outside_markers_and_positive_nested_indentation():
    css = (Path(app_module.app.static_folder) / "round_workspace.css").read_text(encoding="utf-8")
    document_rules = css[css.index(".rw-markdown-document{"):]

    assert ".rw-markdown-document ul,.rw-markdown-document ol" in document_rules
    assert "list-style-position:outside" in document_rules
    assert "padding-inline-start:1.65rem" in document_rules
    assert ".rw-markdown-document li{" in document_rules
    assert "padding-inline-start:.18rem" in document_rules
    assert ".rw-markdown-document li>ul,.rw-markdown-document li>ol" in document_rules
    assert "padding-inline-start:1.55rem" in document_rules
    list_section = document_rules[document_rules.index(".rw-markdown-document ul,.rw-markdown-document ol"):document_rules.index(".rw-markdown-document hr")]
    assert "margin-left:-" not in list_section
    assert "margin-inline-start:-" not in list_section
    assert "padding-inline-start:-" not in list_section
    assert "list-style-position:inside" not in list_section


def test_markdown_overflow_media_and_elements_remain_viewer_scoped():
    css = (Path(app_module.app.static_folder) / "round_workspace.css").read_text(encoding="utf-8")
    document_rules = css[css.index(".rw-markdown-document{"):]

    assert ".rw-markdown-document pre{" in document_rules
    assert ".rw-markdown-document table{" in document_rules
    assert document_rules.count("overflow-x:auto") >= 2
    assert ".rw-markdown-document img{" in document_rules
    assert "max-width:100%" in document_rules and "height:auto" in document_rules
    assert ".rw-markdown-document blockquote{" in document_rules
    assert ".rw-markdown-document :not(pre)>code" in document_rules
    assert ".rw-activity h1" not in document_rules
    assert ".rw-chat-turn-body h1" not in document_rules


def test_file_tabs_merge_with_workspace_and_show_path_breadcrumb():
    css = (Path(app_module.app.static_folder) / "round_workspace.css").read_text(encoding="utf-8")

    assert ".rw-workspace,.rw-pane.is-active{box-shadow:none}" in css
    assert ".rw-pane-header,.rw-panel-tabs{box-sizing:border-box;height:41px;min-height:41px;border-bottom:0" in css
    assert ".rw-file-tabs{border-bottom:0}" in css
    assert "scrollbar-width:none" in css
    assert ".rw-file-tabs::-webkit-scrollbar{display:none}" in css
    assert ".rw-file-tabs::-webkit-scrollbar{height:3px}" not in css
    assert ".rw-file-tab.is-active{background:var(--v2-bg)}" in css
    assert ".rw-file-tab:not(.is-active) .rw-file-tab-close{visibility:hidden}" in css
    assert ".rw-file-tab:not(.is-active):hover .rw-file-tab-close,.rw-file-tab:not(.is-active):focus-within .rw-file-tab-close{visibility:visible}" in css
    active_tab_rules = [
        rule.split("}", 1)[0]
        for rule in css.split(".rw-file-tab.is-active{")[1:]
    ]
    assert all("border-bottom-color:var(--v2-accent)" not in rule for rule in active_tab_rules)
    assert ".rw-pane-breadcrumb{" in css
    assert ".rw-viewer,.rw-pane,.rw-pane-body{background:var(--v2-bg)}" in css
    assert ".rw-inspector,.rw-panel-view,.rw-chat-form{background:var(--v2-surface)}" in css
    assert ".rw-panel-tabs{border-bottom:0}" in css
    assert ".rw-pane-header,.rw-panel-tabs{box-sizing:border-box;height:41px;min-height:41px;border-bottom:0;background:var(--v2-surface)}" in css
    assert ".rw-sidebar{" in css and "background:var(--v2-surface)" in css
    assert ".rw-pane-divider{" in css
    assert "cursor:col-resize" in css and "cursor:row-resize" in css


def test_workspace_keeps_disclosure_column_while_studio_title_aligns_to_caret_axis():
    static = Path(app_module.app.static_folder)
    shared_css = (static / "style.css").read_text(encoding="utf-8")
    workspace_css = (static / "round_workspace.css").read_text(encoding="utf-8")
    workspace_js = (static / "round_workspace.js").read_text(encoding="utf-8")
    studio_css = (static / "hypothesis_studio.css").read_text(encoding="utf-8")
    studio_js = (static / "hypothesis_studio.js").read_text(encoding="utf-8")
    audit_css = (static / "project_audit_agent.css").read_text(encoding="utf-8")

    assert ".v2-round-row-leading {" in shared_css
    assert "flex: 0 0 30px" in shared_css
    assert ".v2-round-row-title {" in shared_css
    assert ".v2-round-row-status {" in shared_css
    assert "justify-content: flex-end" in shared_css
    assert "v2-round-row-leading rw-round-caret rw-tree-caret" in workspace_js
    assert "v2-round-row-title rw-round-title" in workspace_js
    assert "v2-round-row-status rw-round-status" in workspace_js
    assert "v2-round-row-leading hs-round-leading" not in studio_js
    assert "v2-round-row-title hs-round-item-name" in studio_js
    assert "v2-round-row-status hs-round-item-trailing" in studio_js

    assert ".rw-sidebar{" in workspace_css and "font-family:var(--v2-sans)" in workspace_css
    tree_row = workspace_css.split(".rw-tree-row{", 1)[1].split("}", 1)[0]
    assert "font:.73rem var(--v2-sans)" in tree_row
    assert "var(--v2-mono" not in tree_row
    assert "font:600 .67rem var(--v2-sans)" in workspace_css
    assert "letter-spacing:.1em" in workspace_css

    assert ".hs-sidebar {" in studio_css and "font-family: var(--v2-sans)" in studio_css
    assert "padding: 0 10px 24px" in studio_css
    assert "padding: 0 9px" in studio_css
    assert "padding: 0 11px 0 18px" in studio_css
    assert "min-height: 32px" in studio_css
    assert "align-items: center" in studio_css
    assert "font-size: 0.67rem" in studio_css
    assert "letter-spacing: 0.1em" in studio_css

    assert ".aa-sidebar {" in audit_css and "font-family: var(--v2-sans)" in audit_css
    assert "font-size: 0.67rem" in audit_css
    assert "letter-spacing: 0.1em" in audit_css


def test_round_chat_queue_matches_audit_controls_without_send_button_bleed():
    static = Path(app_module.app.static_folder)
    workspace_css = (static / "round_workspace.css").read_text(encoding="utf-8")
    audit_css = (static / "project_audit_agent.css").read_text(encoding="utf-8")

    assert ".rw-chat-form button{" not in workspace_css
    assert ".rw-chat-composer-surface>button[type=submit]{" in workspace_css
    for declaration in (
        "width:26px",
        "height:26px",
        "margin-right:-4px",
        "border-radius:999px",
        "background:transparent",
        "color:var(--v2-muted)",
    ):
        assert declaration in workspace_css
        assert declaration.replace(":", ": ") in audit_css
    assert ".rw-chat-queued-delete:hover,.rw-chat-queued-delete:focus-visible" in workspace_css
    assert "padding-inline-start:1.75em" in workspace_css
    assert "list-style-position:outside" in workspace_css
    assert ".rw-chat-turn.assistant .rw-chat-turn-body li+li" in workspace_css


def _generated_report_fixture(name):
    return (
        Path(__file__).parent
        / "fixtures"
        / "round_workspace"
        / name
    ).read_text(encoding="utf-8")


def test_generated_round_report_display_omits_only_preamble_and_renders_science():
    raw = _generated_report_fixture("generated_round_report.md")
    display = app_module._prepare_round_workspace_markdown_for_display(
        3, "round_report.md", raw
    )
    html = app_module.render_markdown_to_html(display)

    assert html.count(">Round 3 Report</h1>") == 1
    assert ">Background and Literature Review</h1>" in html
    assert html.index(">Round 3 Report</h1>") < html.index(">Background and Literature Review</h1>")
    assert "Timestamp" not in html
    assert "<strong>Round:</strong>" not in html
    assert ">Description</h2>" not in html
    assert ">Round Report</h2>" not in html
    assert "before integration with ResultsMemoryManager" not in html
    assert "<li>Survival is subtype-dependent.</li>" in html
    assert "<table>" in html
    assert 'src="figures/resilience.png"' in html
    assert "effect_size = treated.mean() - control.mean()" in html
    assert "[1]" in html


def test_round_report_uses_shared_legacy_body_boundary():
    raw = _generated_report_fixture("generated_round_report.md")
    display = app_module.prepare_generated_report_for_display(
        "round", 3, "round_report.md", raw
    )
    legacy_body = app_module._extract_generated_round_report_body(raw)
    assert legacy_body is not None
    assert display == "# Round 3 Report\n\n" + legacy_body
    headings = app_module._round_workspace_markdown_headings(display)
    assert [heading["text"] for heading in headings[:3]] == [
        "Round 3 Report",
        "Background and Literature Review",
        "Overview of the Research Problem",
    ]


def test_report_figures_use_opaque_urls_and_missing_states(monkeypatch, tmp_path):
    figure = tmp_path / "outputs" / "figures" / "heatmap.png"
    figure.parent.mkdir(parents=True)
    figure.write_bytes(b"png")
    registry = {
        "checkpoint-source": {"kind": "checkpoint", "root": tmp_path / "round_3", "files": None},
        "external-source": {
            "kind": "execution-output",
            "root": tmp_path,
            "files": {
                "outputs/figures/heatmap.png": {
                    "target": figure,
                    "display_path": "outputs/figures/heatmap.png",
                    "artifact_path": "outputs/figures/heatmap.png",
                    "visibility": "research",
                },
            },
        },
    }
    monkeypatch.setattr(app_module, "_round_workspace_artifact_registry", lambda _round: registry)
    with app_module.app.test_request_context("/"):
        display, figures = app_module._prepare_round_workspace_report_figures(
            3,
            "checkpoint-source",
            "round_report.md",
            "![Heatmap](outputs/figures/heatmap.png)",
            "round_report",
        )
        assert display == "![Heatmap](rw-report-figure:rw_fig_0)"
        assert figures[0]["available"] is True
        assert figures[0]["alt"] == "Heatmap"
        assert figures[0]["url"].startswith("/api/round_workspace_artifact?")
        assert "external-source" in figures[0]["url"]

        generic_display, generic_figures = app_module._prepare_round_workspace_report_figures(
            3,
            "checkpoint-source",
            "notes/analysis.md",
            "![Heatmap](../outputs/figures/heatmap.png)",
            "markdown",
        )
        assert generic_display == "![Heatmap](rw-report-figure:rw_fig_0)"
        assert generic_figures[0]["available"] is True

        missing, missing_figures = app_module._prepare_round_workspace_report_figures(
            3,
            "checkpoint-source",
            "round_report.md",
            "![Missing](/lustre/run/outputs/figures/missing.png)",
            "round_report",
        )
        assert "/lustre/" not in missing
        assert missing_figures == [{
            "token": "rw_fig_0", "alt": "Missing", "available": False, "url": None,
        }]

        unauthorized, unauthorized_figures = app_module._prepare_round_workspace_report_figures(
            3,
            "checkpoint-source",
            "round_report.md",
            "![Secret](/etc/private.png)",
            "round_report",
        )
        assert unauthorized == "**Figure unavailable:** Secret"
        assert "/etc/" not in unauthorized
        assert unauthorized_figures == []


def test_generated_query_display_starts_at_result_and_preserves_result_blocks():
    raw = _generated_report_fixture("generated_query_details.md")
    display = app_module._prepare_round_workspace_markdown_for_display(
        3, "query_012/query_012.md", raw
    )
    html = app_module.render_markdown_to_html(display)

    assert html.count(">Query 12 Details</h1>") == 1
    assert ">Analysis</h1>" in html
    assert html.index(">Query 12 Details</h1>") < html.index(">Analysis</h1>")
    for omitted in (
        "Timestamp",
        "Hypothesis Input",
        "Purpose",
        "/lustre/isaac24",
        "placeholder exactly",
        "Your Directive",
        "Raw Query",
        "Execution Agent Report",
    ):
        assert omitted not in html
    assert "resilient subtype signature" in html
    assert "<ol>" in html
    assert "<table>" in html
    assert 'src="figures/query_012_heatmap.png"' in html
    assert "model.fit(expression, outcome)" in html
    assert "[2]" in html


def test_query_report_roles_support_verified_locations_and_agent_wrappers():
    raw = _generated_report_fixture("generated_query_details.md")
    for path in (
        "query_012/query_012.md",
        "da_query_reports/query_012.md",
        "da_query_reports/query_012/query_012.md",
    ):
        assert "# Hypothesis Input" not in app_module._prepare_round_workspace_markdown_for_display(
            3, path, raw
        )
    web_raw = raw.replace("# Execution Agent Report", "# Web Search Report").replace(
        "# Analysis", "# Background and Information"
    )
    display = app_module._prepare_round_workspace_markdown_for_display(
        3, "query_012/query_012.md", web_raw
    )
    assert display.startswith("# Query 12 Details\n\n# Background and Information")


def test_ordinary_and_ambiguous_markdown_fall_back_byte_for_byte():
    ordinary = "# Analysis\n\n## Description\n\nScientific description with a path `/data/a.csv`."
    ambiguous = (
        "# Query 12 Details\n\n**Timestamp:** keep me\n\n"
        "# Hypothesis Input\n\nPotentially meaningful content.\n\n# Analysis\n\nFinding."
    )
    report_like = "# Round 3 Report\n\n## Description\n\nA scientific Description section."

    assert app_module._prepare_round_workspace_markdown_for_display(
        3, "analysis/notes.md", ordinary
    ) == ordinary
    assert app_module._prepare_round_workspace_markdown_for_display(
        3, "query_012/query_012.md", ambiguous
    ) == ambiguous
    assert app_module._prepare_round_workspace_markdown_for_display(
        3, "analysis/round_report.md", report_like
    ) == report_like


def test_display_endpoint_normalizes_markdown_but_raw_artifact_stays_complete(monkeypatch, tmp_path):
    base = tmp_path / "checkpoints"
    round_dir = base / "round_3"
    query_dir = round_dir / "query_012"
    query_dir.mkdir(parents=True)
    raw = _generated_report_fixture("generated_query_details.md")
    query_file = query_dir / "query_012.md"
    query_file.write_text(raw, encoding="utf-8")

    monkeypatch.setattr(app_module, "_get_checkpoint_base", lambda: base)
    monkeypatch.setattr(app_module, "_v2_round_status", lambda _info: "completed")
    monkeypatch.setattr(
        app_module,
        "_round_workspace_artifact_path",
        lambda round_index, relative_path: (
            query_file
            if round_index == 3 and relative_path == "query_012/query_012.md"
            else None
        ),
    )
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True

    display_response = client.get(
        "/api/round_workspace_markdown",
        query_string={"round": 3, "path": "query_012/query_012.md"},
    )
    assert display_response.status_code == 200
    display = display_response.get_json()["markdown"]
    assert "# Analysis" in display
    assert "# Hypothesis Input" not in display

    raw_response = client.get("/checkpoint_assets/round_3/query_012/query_012.md")
    assert raw_response.status_code == 200
    assert raw_response.get_data(as_text=True) == raw
    assert "# Hypothesis Input" in raw_response.get_data(as_text=True)


def test_markdown_viewer_uses_display_endpoint_without_changing_other_artifact_paths():
    js = (
        Path(app_module.app.static_folder) / "round_workspace.js"
    ).read_text(encoding="utf-8")

    assert "const markdownDisplayUrl" in js
    assert "/api/round_workspace_markdown?round=" in js
    assert "markdownFile?markdownDisplayUrl(file):artifactUrl(file)" in js
    assert "img.src = artifactUrl(file)" in js
    assert "frame.src = artifactUrl(file)" in js
