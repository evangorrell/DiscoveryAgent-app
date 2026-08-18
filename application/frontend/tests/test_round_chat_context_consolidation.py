"""Exact report, artifact, and durable-history grounding for V2 Round Chat."""

from pathlib import Path
from types import SimpleNamespace
import sys

from da_selection_ui import app as app_module


class _FakeAPIStatusError(Exception):
    status_code = 500


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("DISCOVERYAGENT_DATA_DIR", str(tmp_path / "managed"))
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path / "legacy")
    client = app_module.app.test_client()
    with client.session_transaction() as state:
        state["username"] = "round-chat-context-test"
        state["app_mode"] = app_module.APP_MODE_SELECTION
    return client


def _create_project(client, name):
    response = client.post("/api/projects", data={
        "project_name": name,
        "task_description": f"Investigate {name}.",
        "dataset_context": f"Dataset context for {name}.",
    })
    project_id = response.get_json()["project_id"]
    return next(item for item in app_module._v2_load_projects() if item["id"] == project_id)


def _round_dir(project, round_index):
    root = app_module._v2_round_dirs_root_from_any_checkpoint_path(
        Path(project["checkpoint_dir"])
    )
    round_dir = root / f"round_{round_index}"
    round_dir.mkdir(parents=True, exist_ok=True)
    return round_dir


def _complete_round(project, round_index, marker, *, summary=True):
    round_dir = _round_dir(project, round_index)
    (round_dir / "round_report.md").write_text(
        f"# Round {round_index} Report\n\nFULL {marker}", encoding="utf-8"
    )
    if summary:
        (round_dir / "round_report_summary.md").write_text(
            f"# Summary\n\nSUMMARY {marker}", encoding="utf-8"
        )
    query = round_dir / "query_001"
    query.mkdir(exist_ok=True)
    (query / "query_001.md").write_text(
        f"# Query 1\n\nARTIFACT {marker}", encoding="utf-8"
    )
    return round_dir


def _capture(monkeypatch):
    calls = []

    class _Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="Grounded answer")],
                stop_reason="end_turn",
            )

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(
        Anthropic=lambda: SimpleNamespace(messages=_Messages()),
        APIStatusError=_FakeAPIStatusError,
    ))
    monkeypatch.setattr(app_module, "_resolve_code_chat_model", lambda *_args: "test-model")
    monkeypatch.setattr(app_module, "get_generator", lambda: None)
    monkeypatch.setattr(app_module, "_write_interaction_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app_module, "log_event", lambda *_args, **_kwargs: None)
    return calls


def _ask(client, project, round_index, *, viewer_file=None, message="What matters here?", request_id="current"):
    response = client.post(
        f"/api/round_chat?project={project['url_slug']}&round={round_index}",
        json={
            "message": message,
            "mode": "all",
            "request_id": request_id,
            "viewer_file": viewer_file,
        },
    )
    assert response.status_code == 200
    assert response.get_json()["success"] is True


def _viewer(round_index, path="round_report.md", *, view="full", source=None):
    value = {
        "round": round_index,
        "sourceKind": "checkpoint",
        "artifactPath": path,
        "path": path,
        "name": Path(path).name,
    }
    if view:
        value["reportView"] = view
    if source:
        value["source"] = source
    return value


def test_summary_and_full_ground_different_content_from_same_exact_round(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project = _create_project(client, "Exact Report Context")
    _complete_round(project, 1, "ROUND ONE")
    _complete_round(project, 2, "ROUND TWO")
    calls = _capture(monkeypatch)

    _ask(client, project, 1, viewer_file=_viewer(1, view="summary"), request_id="summary")
    _ask(client, project, 1, viewer_file=_viewer(1, view="full"), request_id="full")
    _ask(client, project, 2, viewer_file=_viewer(2, view="full"), request_id="round-two")
    summary_prompt = calls[0]["messages"][-1]["content"]
    full_prompt = calls[1]["messages"][-1]["content"]
    round_two_prompt = calls[2]["messages"][-1]["content"]

    assert "SUMMARY ROUND ONE" in summary_prompt
    assert "FULL ROUND ONE" not in summary_prompt
    assert "FULL ROUND ONE" in full_prompt
    assert "SUMMARY ROUND ONE" not in full_prompt
    assert "ROUND TWO" not in summary_prompt + full_prompt
    assert "FULL ROUND TWO" in round_two_prompt
    assert "ROUND ONE" not in round_two_prompt
    assert str(tmp_path) not in summary_prompt + full_prompt + round_two_prompt


def test_missing_summary_is_explicit_and_never_uses_another_round(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project = _create_project(client, "Missing Summary Context")
    _complete_round(project, 1, "ROUND ONE", summary=False)
    _complete_round(project, 2, "ROUND TWO", summary=True)
    calls = _capture(monkeypatch)

    _ask(client, project, 1, viewer_file=_viewer(1, view="summary"))
    prompt = calls[0]["messages"][-1]["content"]

    assert "round_report_summary.md: unavailable for this exact round" in prompt
    assert "FULL ROUND ONE" in prompt
    assert "SUMMARY ROUND TWO" not in prompt


def test_missing_full_report_is_explicit_and_never_drifts_to_latest(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project = _create_project(client, "Missing Full Report Context")
    _round_dir(project, 1)
    _complete_round(project, 2, "ROUND TWO")
    calls = _capture(monkeypatch)

    _ask(client, project, 1, viewer_file=_viewer(1, view="full"))
    prompt = calls[0]["messages"][-1]["content"]

    assert "round_report.md: unavailable for this exact round" in prompt
    assert "ROUND TWO" not in prompt


def test_exact_report_context_is_capped(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project = _create_project(client, "Capped Report Context")
    round_one = _complete_round(project, 1, "CAPPED")
    (round_one / "round_report.md").write_text(
        "REPORT START\n" + ("r" * 20_000) + "\nREPORT END",
        encoding="utf-8",
    )
    calls = _capture(monkeypatch)

    _ask(client, project, 1, viewer_file=_viewer(1, view="full"))
    prompt = calls[0]["messages"][-1]["content"]

    assert "REPORT START" in prompt
    assert "...[truncated]..." in prompt
    assert "REPORT END" not in prompt


def test_open_text_artifact_is_grounded_by_content_and_composes_with_report_and_runtime(
    monkeypatch, tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    project = _create_project(client, "Artifact Runtime Context")
    round_one = _complete_round(project, 1, "ROUND ONE")
    _complete_round(project, 2, "ROUND TWO")
    (round_one / "live_round.log").write_text(
        "[ERROR] RUNTIME ROUND ONE MARKER", encoding="utf-8"
    )
    calls = _capture(monkeypatch)

    _ask(
        client,
        project,
        1,
        viewer_file=_viewer(1, "query_001/query_001.md", view=None),
        message="Why did query 1 fail during execution?",
    )
    prompt = calls[0]["messages"][-1]["content"]

    assert "Currently open trusted textual artifact" in prompt
    assert "Round 1 / query_001/query_001.md" in prompt
    assert "ARTIFACT ROUND ONE" in prompt
    assert "FULL ROUND ONE" in prompt
    assert "RUNTIME ROUND ONE MARKER" in prompt
    assert "ROUND TWO" not in prompt


def test_open_text_artifact_content_is_capped(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project = _create_project(client, "Capped Artifact Context")
    round_one = _complete_round(project, 1, "CAPPED")
    notes = round_one / "query_001" / "query_001.md"
    notes.write_text(
        "OPEN ARTIFACT START\n" + ("x" * 20_000) + "\nOPEN ARTIFACT END",
        encoding="utf-8",
    )
    calls = _capture(monkeypatch)

    _ask(
        client,
        project,
        1,
        viewer_file=_viewer(1, "query_001/query_001.md", view=None),
    )
    prompt = calls[0]["messages"][-1]["content"]

    assert "OPEN ARTIFACT START" in prompt
    assert "...[truncated]..." in prompt
    assert "OPEN ARTIFACT END" not in prompt


def test_cross_round_project_binary_unsafe_and_oversized_artifacts_are_not_injected(
    monkeypatch, tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    project_a = _create_project(client, "Artifact Isolation A")
    project_b = _create_project(client, "Artifact Isolation B")
    round_a = _complete_round(project_a, 1, "PROJECT A")
    _complete_round(project_a, 2, "PROJECT A ROUND TWO")
    _complete_round(project_b, 1, "PROJECT B SECRET MARKER")
    figures = round_a / "figures"
    figures.mkdir()
    (figures / "binary.png").write_bytes(b"\x89PNG\x00BINARY SECRET")
    (round_a / ".env").write_text("SECRET_TOKEN=unsafe", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("SYMLINK ESCAPE SECRET", encoding="utf-8")
    (round_a / "query_001" / "escape.txt").symlink_to(outside)
    calls = _capture(monkeypatch)

    client.get(f"/projects/{project_b['url_slug']}")
    tree = client.get("/api/round_workspace_tree").get_json()
    round_b = next(item for item in tree["rounds"] if item["round_index"] == 1)
    project_b_query = next(
        item for item in round_b["tree"]["children"]
        if item.get("path") == "query_001/query_001.md"
    )

    _ask(client, project_a, 1, viewer_file=_viewer(2, "query_001/query_001.md", view=None), request_id="cross-round")
    _ask(
        client,
        project_a,
        1,
        viewer_file=_viewer(
            1,
            "query_001/query_001.md",
            view=None,
            source=project_b_query["source"],
        ),
        request_id="cross-project",
    )
    _ask(client, project_a, 1, viewer_file=_viewer(1, "figures/binary.png", view=None), request_id="binary")
    _ask(client, project_a, 1, viewer_file=_viewer(1, ".env", view=None), request_id="unsafe")
    _ask(client, project_a, 1, viewer_file=_viewer(1, "../outside.txt", view=None), request_id="traversal")
    _ask(client, project_a, 1, viewer_file=_viewer(1, "query_001/escape.txt", view=None), request_id="symlink")
    monkeypatch.setattr(app_module, "ROUND_CHAT_ARTIFACT_MAX_BYTES", 8)
    _ask(client, project_a, 1, viewer_file=_viewer(1, "query_001/query_001.md", view=None), request_id="oversized")

    for call in calls:
        prompt = call["messages"][-1]["content"]
        assert "Currently open trusted textual artifact" not in prompt
        assert "PROJECT B SECRET MARKER" not in prompt
        assert "BINARY SECRET" not in prompt
        assert "SECRET_TOKEN" not in prompt
        assert "SYMLINK ESCAPE SECRET" not in prompt


def _seed_history(project, rounds):
    app_module._write_conversation_surface_state(
        "round_workspace_chat",
        "main",
        {"rounds": rounds},
        project["id"],
    )


def test_prior_completed_turns_survive_refresh_and_remain_project_round_isolated(
    monkeypatch, tmp_path,
):
    first = _client(monkeypatch, tmp_path)
    project_a = _create_project(first, "History Isolation A")
    project_b = _create_project(first, "History Isolation B")
    _complete_round(project_a, 1, "A ROUND ONE")
    _complete_round(project_a, 2, "A ROUND TWO")
    _complete_round(project_b, 1, "B ROUND ONE")
    _seed_history(project_a, {
        "1": [
            {"id": "u-a1", "requestId": "a1", "role": "user", "text": "PRIOR USER A1"},
            {"id": "a-a1", "requestId": "a1", "role": "assistant", "text": "PRIOR ANSWER A1", "jobStatus": "succeeded", "pending": False},
            {"id": "u-fail", "requestId": "fail", "role": "user", "text": "FAILED USER"},
            {"id": "a-fail", "requestId": "fail", "role": "assistant", "text": "FAILED ANSWER", "jobStatus": "failed", "pending": False, "failed": True},
            {"id": "u-cancel", "requestId": "cancel", "role": "user", "text": "CANCELLED USER"},
            {"id": "a-cancel", "requestId": "cancel", "role": "assistant", "text": "CANCELLED ANSWER", "jobStatus": "cancelled", "pending": False, "cancelled": True},
        ],
        "2": [
            {"id": "u-a2", "requestId": "a2", "role": "user", "text": "ROUND TWO USER"},
            {"id": "a-a2", "requestId": "a2", "role": "assistant", "text": "ROUND TWO ANSWER", "jobStatus": "succeeded", "pending": False},
        ],
    })
    _seed_history(project_b, {"1": [
        {"id": "u-b1", "requestId": "b1", "role": "user", "text": "PROJECT B USER"},
        {"id": "a-b1", "requestId": "b1", "role": "assistant", "text": "PROJECT B ANSWER", "jobStatus": "succeeded", "pending": False},
    ]})
    calls = _capture(monkeypatch)

    refreshed = _client(monkeypatch, tmp_path)
    _ask(refreshed, project_a, 1, request_id="follow-up")
    messages = calls[0]["messages"]

    assert [item["content"] for item in messages[:-1]] == [
        "PRIOR USER A1", "PRIOR ANSWER A1",
    ]
    joined = str(messages)
    assert "FAILED" not in joined
    assert "CANCELLED" not in joined
    assert "ROUND TWO USER" not in joined
    assert "PROJECT B USER" not in joined
    assert "FULL A ROUND ONE" in messages[-1]["content"]


def test_prior_history_is_bounded_to_newest_six_completed_exchanges(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project = _create_project(client, "Bounded History")
    _complete_round(project, 1, "BOUNDED")
    messages = []
    for index in range(10):
        messages.extend([
            {"id": f"u-{index}", "requestId": f"r-{index}", "role": "user", "text": f"USER {index}"},
            {"id": f"a-{index}", "requestId": f"r-{index}", "role": "assistant", "text": f"ANSWER {index}", "jobStatus": "succeeded", "pending": False},
        ])
    _seed_history(project, {"1": messages})

    prior = app_module._round_chat_prior_messages(project["id"], 1, "current")

    assert len(prior) == 12
    assert prior[0]["content"] == "USER 4"
    assert prior[-1]["content"] == "ANSWER 9"
    assert sum(len(item["content"]) for item in prior) <= app_module.ROUND_CHAT_HISTORY_MAX_CHARS
