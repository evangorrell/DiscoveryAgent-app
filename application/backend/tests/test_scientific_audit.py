"""Audit evidence must survive independently of console verbosity."""
import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from AIR.utils.scientific_audit import (
    AuditTrail, RedactingFilter, sanitize_record,
    sanitize_text, provider_exchange_details,
)
from AIR.utils.logger import Logger
from AIR.hyp_agents.hypothesis_gen_agent import HypothesisGenAgent


def records(logger):
    return [json.loads(line) for line in logger.audit_trail.path.read_text().splitlines()]


def make_logger(tmp_path):
    logger = object.__new__(Logger)
    logger.analysis_name = "test_agent"
    logger.audit_trail = AuditTrail(tmp_path, logger.analysis_name)
    stream = io.StringIO()
    logger.terminal_stream = stream
    logger.logger = logging.Logger("isolated-audit-test")
    handler = logging.StreamHandler(stream)
    logger.logger.addHandler(handler)
    logger.logger.addFilter(RedactingFilter())
    return logger, stream


@pytest.mark.parametrize("debug", [False, True])
def test_persistence_is_independent_of_terminal_verbosity(tmp_path, monkeypatch, debug):
    monkeypatch.setenv("DISCOVERYAGENT_DEBUG_PROMPTS", "1" if debug else "0")
    logger, terminal = make_logger(tmp_path)
    logger.log_prompt("user", "Scientific prompt", "research")
    logger.log_response("Scientific answer with citation [1]", "search")
    logger.log_code("result = scientific_computation()")
    logger.log_action("web_search", {"query": "retinal regeneration", "provider": "papers",
                                     "status": "success", "citations": ["https://example.org/paper"]})
    logger.log_error("Returned error context", code="failed_computation()")
    data = records(logger)
    assert len(data) == 5
    assert data[0]["details"]["content"] == "Scientific prompt"
    assert data[1]["details"]["content"] == "Scientific answer with citation [1]"
    assert data[2]["details"]["code"] == "result = scientific_computation()"
    assert data[3]["details"]["query"] == "retinal regeneration"
    assert data[3]["details"]["status"] == "success"
    assert data[4]["details"]["message"] == "Returned error context"
    assert all(d["agent"] == "test_agent" and d["timestamp"] and d["event_id"] for d in data)
    assert ("Scientific prompt" in terminal.getvalue()) is debug
    assert ("retinal regeneration" in terminal.getvalue()) is debug
    assert ("Returned error context" in terminal.getvalue()) is debug


@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("outcome", ["success", "parse_failure", "provider_failure"])
def test_judge_full_decision_and_fallback_are_durable(tmp_path, monkeypatch, debug, outcome):
    monkeypatch.setenv("DISCOVERYAGENT_DEBUG_PROMPTS", "1" if debug else "0")
    logger, terminal = make_logger(tmp_path)
    agent = HypothesisGenAgent.__new__(HypothesisGenAgent)
    agent.logger = logger
    agent.judge_llm_config = SimpleNamespace(api_provider="openai", model_name="test-judge-model")
    answer = "**Selected Hypothesis**: 2\n**Justification**: " + ("Authentic returned evidence. " * 200)
    if outcome == "parse_failure":
        answer = "No valid selection, but this complete explanation must survive."
    create = Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=answer))]))
    if outcome == "provider_failure":
        create.side_effect = RuntimeError("Provider refused: Authorization: Bearer fake-bearer-token")
    agent.judge_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    selected = agent._judge_hypotheses(["Candidate A", "Candidate B"])
    data = records(logger)
    request = next(d["details"] for d in data if d["event"] == "hypothesis_judge_requested")
    event = "hypothesis_judge_failure" if outcome == "provider_failure" else "hypothesis_judge_decision"
    decision = next(d["details"] for d in data if d["event"] == event)
    assert request["candidates"] == ["Candidate A", "Candidate B"]
    assert request["model"] == "test-judge-model" and request["provider"] == "openai"
    assert request["evaluation_id"] == decision["evaluation_id"]
    assert decision["selected_hypothesis"] == selected
    if outcome != "provider_failure":
        assert decision["response"] == answer
    else:
        assert decision["error_type"] == "RuntimeError"
        assert "fake-bearer-token" not in logger.audit_trail.path.read_text()
    assert selected == ("Candidate B" if outcome == "success" else "Candidate A")
    if not debug:
        assert answer not in terminal.getvalue()


@pytest.mark.parametrize("field", [
    "api_key", "Authorization", "access_token", "refresh_token", "clientSecret",
    "password", "cookies", "cookie", "session_secret", "private_key", "credentials",
    "authentication", "oauth_token",
])
def test_nested_credentials_redacted_without_losing_research(tmp_path, field):
    logger, terminal = make_logger(tmp_path)
    secret = "fake-test-secret-" + field
    payload = {"query": "Does gene A promote regeneration?", "status": "success",
               "provider": "test-service", "nested": {field: secret},
               "diagnostic": "credential echoed: " + secret,
               "model": "research-model", "input_tokens": 120, "output_tokens": 40}
    logger.log_action("tool_call", payload)
    persisted = logger.audit_trail.path.read_text()
    assert secret not in persisted
    details = records(logger)[0]["details"]
    assert details["query"] == payload["query"]
    assert details["status"] == "success" and details["input_tokens"] == 120
    assert details["nested"][field] == "[REDACTED]"


def test_credential_urls_and_free_text_redacted():
    text = ("https://user:fake-url-password@example.org/data?access_token=fake-oauth&gene=SOX11 "
            "--api-key fake-command-key\nAuthorization: Bearer fake-header-token\n"
            "password='fake-password'\n-----BEGIN PRIVATE KEY-----\nfake-key\n-----END PRIVATE KEY-----")
    cleaned = sanitize_text(text)
    for secret in ("fake-url-password", "fake-oauth", "fake-command-key",
                   "fake-header-token", "fake-password", "fake-key"):
        assert secret not in cleaned
    assert "gene=SOX11" in cleaned


def test_inline_basic_cookie_and_signed_url_credentials_redacted():
    text = ("Provider error Authorization: Basic fake-basic-auth\n"
            "Request Cookie: sid=fake-cookie; refresh=fake-refresh\n"
            "https://example.org/data?key=fake-api-key&gene=SOX11&X-Amz-Signature=fake-signature")
    cleaned = sanitize_text(text)
    assert all(secret not in cleaned for secret in (
        "fake-basic-auth", "fake-cookie", "fake-refresh", "fake-api-key", "fake-signature"))
    assert "gene=SOX11" in cleaned


def test_binary_and_unknown_sdk_objects_not_dumped():
    class SDKClient:
        def __repr__(self):
            raise AssertionError("Raw SDK objects must never be rendered")
    cleaned = sanitize_record({"attachment": {"type": "base64", "data": "A" * 50000},
                               "sdk": SDKClient(), "raw": b"data", "query": "scientific question"})
    assert len(json.dumps(cleaned)) < 1000
    assert cleaned["attachment"]["data"]["omitted"] == "binary"
    assert cleaned["query"] == "scientific question"


def test_project_isolation_append_restart_and_order(tmp_path):
    a = make_logger(tmp_path / "project-a")[0]
    b = make_logger(tmp_path / "project-b")[0]
    a.log_action("first", {"query": "A"})
    b.log_action("first", {"query": "B"})
    resumed = make_logger(tmp_path / "project-a")[0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: resumed.log_action("continued", {"number": n}), range(20)))
    data = records(a)
    assert len(data) == 21
    assert data[0]["details"]["query"] == "A"
    assert records(b)[0]["details"]["query"] == "B"
    offsets = [d["offset"] for d in data]
    assert offsets == sorted(set(offsets))
    assert (a.audit_trail.path.stat().st_mode & 0o777) == 0o600


def test_no_unscoped_or_repository_storage(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="absolute"):
        AuditTrail("logs", "test")
    import AIR.utils.scientific_audit as audit_module
    with pytest.raises(ValueError, match="source checkout"):
        AuditTrail(Path(audit_module.__file__).resolve().parent / "logs", "test")
    # A managed scientific project may itself use Git; it is not app source.
    (tmp_path / ".git").mkdir()
    trail = AuditTrail(tmp_path / "checkpoint", "test")
    trail.append("project_event", {"query": "science"})
    assert trail.path.is_file()


def test_symlink_target_refused(tmp_path):
    target = tmp_path / "existing"
    target.write_text("untouched")
    (tmp_path / "scientific_audit.jsonl").symlink_to(target)
    with pytest.raises(OSError):
        make_logger(tmp_path)[0].log_action("event", {"query": "science"})
    assert target.read_text() == "untouched"


def test_persistence_failure_is_not_silent(tmp_path, monkeypatch, capfd):
    logger = make_logger(tmp_path)[0]
    monkeypatch.setattr("os.fsync", Mock(side_effect=OSError("fake disk failure")))
    with pytest.raises(OSError):
        logger.log_action("event", {"query": "science"})
    assert "not fully audited" in capfd.readouterr().err


def test_provider_metadata_allowlist_and_error(tmp_path):
    logger, terminal = make_logger(tmp_path)
    response = SimpleNamespace(model="research-model", id="response-1", stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=12, output_tokens=42))
    details = provider_exchange_details("HypothesisGenAgent", {
        "model": "research-model", "temperature": 0.3, "max_tokens": 2048,
        "messages": [{"content": "already saved conversation"}],
        "tools": [{"name": "search", "authentication": {"access_token": "fake-oauth"}}],
    }, response, text="Answer", provider="anthropic", attempt=1)
    logger.log_action("api_exchange", details)
    failure = RuntimeError("API key=not-a-credential; password=fake-password")
    logger.log_action("api_failure", provider_exchange_details(
        "HypothesisGenAgent", {"model": "research-model"}, error=failure, retryable=True))
    saved = records(logger)
    assert saved[0]["details"]["request"]["temperature"] == 0.3
    assert saved[0]["details"]["response"]["usage"]["output_tokens"] == 42
    assert saved[0]["details"]["provider"] == "anthropic"
    assert saved[0]["details"]["status"] == "success"
    assert saved[1]["details"]["retryable"] is True
    assert "fake-oauth" not in logger.audit_trail.path.read_text()
    assert "fake-password" not in logger.audit_trail.path.read_text()


def test_debug_output_also_redacts_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCOVERYAGENT_DEBUG_PROMPTS", "1")
    logger, terminal = make_logger(tmp_path)
    logger.log_action("tool", {"query": "retinal research", "Authorization": "Bearer fake-access"})
    assert "fake-access" not in terminal.getvalue()
    assert "retinal research" in terminal.getvalue()


def test_real_logger_separates_audit_files_from_terminal(tmp_path):
    import os
    import subprocess
    import sys
    script = """
import os, sys
from AIR.utils.logger import Logger
logger = Logger("real-test", log_dir=sys.argv[1])
os.environ["DISCOVERYAGENT_DEBUG_PROMPTS"] = "0"
logger.log_prompt("user", "Quiet scientific input", "test")
os.environ["DISCOVERYAGENT_DEBUG_PROMPTS"] = "1"
logger.log_response("Verbose scientific answer", "test")
logger.log_action("tool", {"query": "safe query", "Authorization": "Bearer fake-auth-value"})
print("password=fake-password-value")
"""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path)],
                            env=env, text=True, capture_output=True, timeout=60, check=True)
    output = result.stdout + result.stderr
    assert "Quiet scientific input" not in output
    assert "Verbose scientific answer" in output
    audit = (tmp_path / "scientific_audit.jsonl").read_text()
    operational = (tmp_path / "ai_researcher.log").read_text()
    assert "Quiet scientific input" in audit and "Verbose scientific answer" in audit
    assert "Quiet scientific input" not in operational and "Verbose scientific answer" not in operational
    assert "fake-auth-value" not in audit + operational + output
    assert "fake-password-value" not in operational + output


def test_real_diagnostics_persist_success_and_error(tmp_path):
    from AIR.utils.llm_diagnostics import log_api_exchange, log_api_exception
    logger, terminal = make_logger(tmp_path)
    response = SimpleNamespace(model="model", content=[], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=12, output_tokens=34))
    log_api_exchange(logger, "DirectorAgent", {"model": "model", "max_tokens": 4000}, response, "answer")
    log_api_exception(logger, RuntimeError("password=fake-auth-failure"),
                      source="DirectorAgent", api_params={"model": "model"}, retryable=True)
    saved = records(logger)
    assert saved[0]["details"]["response"]["usage"]["output_tokens"] == 34
    assert saved[0]["details"]["request"]["max_tokens"] == 4000
    assert saved[1]["details"]["status"] == "error"
    assert saved[1]["details"]["retryable"] is True
    assert "fake-auth-failure" not in logger.audit_trail.path.read_text()
    assert "fake-auth-failure" not in terminal.getvalue()


def test_memory_parse_failure_response_is_retained(tmp_path):
    from AIR.memory.results_memory import AnalyzerAgent
    logger, terminal = make_logger(tmp_path)
    analyzer = AnalyzerAgent.__new__(AnalyzerAgent)
    analyzer.audit_logger = logger
    analyzer.llm = lambda *args, **kwargs: ("Returned unparseable scientific explanation", 0.0)
    result, cost = analyzer.analyze("Saved integrated report")
    assert result == {"redundancies": []}
    saved = records(logger)[0]
    assert saved["event"] == "memory_analysis_parse_failure"
    assert saved["details"]["response"] == "Returned unparseable scientific explanation"
    assert "Returned unparseable scientific explanation" not in terminal.getvalue()


def test_execution_history_reuses_nested_redactor(tmp_path):
    from AIR.tool_agents.execution_sdk_tracker import ExecutionSDKTracker
    tracker = ExecutionSDKTracker(logger=Mock())
    tracker.set_query_log_dir(str(tmp_path / "round_1" / "query_01"), query_num=1)
    tracker._write_jsonl(tracker.tool_log_file, {
        "tool": "search", "query": "optic nerve", "status": "success",
        "credentials": {"oauth_token": "fake-execution-oauth"},
    })
    path = tracker.query_log_dir / "tool_calls.jsonl"
    tracker.close()
    assert "fake-execution-oauth" not in path.read_text()
    assert json.loads(path.read_text().splitlines()[-1])["query"] == "optic nerve"


def test_standalone_logger_keeps_configured_relative_run_path(tmp_path):
    import os
    import subprocess
    import sys
    source = Path(__file__).resolve().parents[1]
    script = """
import sys
from AIR.utils.logger import Logger
logger = Logger("standalone", log_dir="run/checkpoint")
logger.log_prompt("user", "Standalone scientific input")
assert logger.audit_trail.directory == (__import__('pathlib').Path.cwd() / "run/checkpoint").resolve()
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script], cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(source), PYTHONDONTWRITEBYTECODE="1",
                 DISCOVERYAGENT_DEBUG_PROMPTS="0"),
        text=True, capture_output=True, timeout=60, check=True,
    )
    assert "Standalone scientific input" not in result.stdout + result.stderr
    saved = json.loads((tmp_path / "run/checkpoint/scientific_audit.jsonl").read_text())
    assert saved["details"]["content"] == "Standalone scientific input"


def test_unscoped_legacy_memory_failure_warns_without_leaking_secrets(capsys):
    from AIR.memory.results_memory import AnalyzerAgent
    analyzer = AnalyzerAgent.__new__(AnalyzerAgent)
    analyzer.llm = lambda *args, **kwargs: ("Scientific failure password=fake-memory-secret", 0.0)
    result, _ = analyzer.analyze("Saved integrated report")
    assert result == {"redundancies": []}
    output = capsys.readouterr().out
    assert "not durably audited" in output
    assert "Scientific failure" in output
    assert "fake-memory-secret" not in output
