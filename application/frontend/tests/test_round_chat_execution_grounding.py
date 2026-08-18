"""Focused V2 Round Chat grounding contracts after Round Monitor retirement."""

from pathlib import Path
from types import SimpleNamespace
import sys

from da_selection_ui import app as app_module


class _FakeAPIStatusError(Exception):
    status_code = 500


def _capture_round_chat_prompt(monkeypatch, tmp_path, message):
    round_dir = tmp_path / "round_4"
    (round_dir / "query_003").mkdir(parents=True)
    captured = {}

    class _Messages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="Grounded answer")],
                stop_reason="end_turn",
            )

    fake_anthropic = SimpleNamespace(
        Anthropic=lambda: SimpleNamespace(messages=_Messages()),
        APIStatusError=_FakeAPIStatusError,
    )
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
    monkeypatch.setattr(
        app_module,
        "_round_activity_dir_for_request",
        lambda: (4, round_dir, None),
    )
    monkeypatch.setattr(
        app_module,
        "_round_chat_file_nav_inventory",
        lambda *_args, **_kwargs: "FILE NAV",
    )
    monkeypatch.setattr(
        app_module,
        "_load_query_specific_context",
        lambda *_args, **_kwargs: "QUERY-SPECIFIC EVIDENCE",
    )
    monkeypatch.setattr(
        app_module,
        "_round_chat_context_bundle",
        lambda _round_dir, mode="queries": f"{mode.upper()} EVIDENCE",
    )
    monkeypatch.setattr(app_module, "_resolve_code_chat_model", lambda *_args: "test-model")
    monkeypatch.setattr(app_module, "get_generator", lambda: None)
    monkeypatch.setattr(app_module, "_write_interaction_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app_module, "log_event", lambda *_args, **_kwargs: None)

    response = app_module.app.test_client().post(
        "/api/round_chat?round=4",
        json={"message": message, "mode": "all"},
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    return captured["messages"][0]["content"]


def test_query_specific_scientific_question_keeps_query_only_grounding(
    monkeypatch, tmp_path,
):
    prompt = _capture_round_chat_prompt(
        monkeypatch, tmp_path, "What scientific result did query 3 find?",
    )

    assert "QUERY-SPECIFIC EVIDENCE" in prompt
    assert "CODE EVIDENCE" not in prompt


def test_execution_question_receives_runtime_grounding(monkeypatch, tmp_path):
    prompt = _capture_round_chat_prompt(
        monkeypatch, tmp_path, "What errors appeared in the runtime logs?",
    )

    assert "QUERIES EVIDENCE" in prompt
    assert "CODE EVIDENCE" in prompt


def test_query_specific_execution_question_gets_query_and_runtime_grounding(
    monkeypatch, tmp_path,
):
    prompt = _capture_round_chat_prompt(
        monkeypatch, tmp_path, "Why did query 3 fail during execution?",
    )

    assert "QUERY-SPECIFIC EVIDENCE" in prompt
    assert "# Bounded runtime and execution evidence" in prompt
    assert "CODE EVIDENCE" in prompt


def test_execution_intent_covers_run_state_language():
    assert app_module._is_round_execution_intent("Did query 3 run?")
    assert app_module._is_round_execution_intent("What is query 12 status?")
    assert not app_module._is_round_execution_intent(
        "What scientific result did query 3 find?",
    )


def test_runtime_bundle_redacts_secrets_and_sensitive_artifact_names(tmp_path):
    round_dir = tmp_path / "round_2"
    query_dir = round_dir / "query_001"
    query_dir.mkdir(parents=True)
    (round_dir / "live_round.log").write_text(
        "API_KEY=sk-super-secret\n"
        "Authorization: Bearer bearer-secret-value\n"
        "fetch https://alice:hunter2@example.test/path\n"
        "[ERROR] request failed with token: raw-token-value\n",
        encoding="utf-8",
    )
    for name in ("results.csv", ".env", "credentials.json", "private_key.pem"):
        (query_dir / name).write_text("fixture", encoding="utf-8")

    context = app_module._round_chat_context_bundle(round_dir, mode="code")

    assert "sk-super-secret" not in context
    assert "bearer-secret-value" not in context
    assert "hunter2" not in context
    assert "raw-token-value" not in context
    assert "[REDACTED]" in context
    assert "results.csv" in context
    assert ".env" not in context
    assert "credentials.json" not in context
    assert "private_key.pem" not in context


def test_v2_has_no_chat_mode_toggle_and_keeps_durable_job_transport():
    static = Path(app_module.app.static_folder)
    template = Path(app_module.app.template_folder or "templates")
    if not template.is_absolute():
        template = Path(app_module.app.root_path) / template
    javascript = (static / "round_workspace.js").read_text(encoding="utf-8")
    html = (template / "round_workspace.html").read_text(encoding="utf-8")
    visible_source = javascript + "\n" + html

    assert "Queries & Reports" not in visible_source
    assert "Code Execution" not in visible_source
    assert "chat-mode" not in visible_source.lower()
    assert "/api/round_chat_jobs" in javascript
    assert "surface:'round_workspace_chat'" in javascript


def test_durable_round_chat_jobs_continue_to_force_unified_mode():
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    route = source[
        source.index("def api_round_chat_jobs():"):
        source.index("@app.route", source.index("def api_round_chat_jobs():") + 1)
    ]

    assert "'mode': 'all'" in route
