"""Regression coverage for the persisted V1 report-summary contract."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from AIR import hypothesis_brancher
from AIR.utils import report_summaries
from scripts import post_process


def test_automatic_prompts_are_the_exact_established_v1_prompts():
    assert report_summaries.SUMMARIZATION_SYSTEM_PROMPT == post_process.SUMMARIZATION_SYSTEM_PROMPT
    assert report_summaries.MEMORY_REPORT_USER_PROMPT == post_process.MEMORY_REPORT_USER_PROMPT
    assert report_summaries.ROUND_REPORT_USER_PROMPT == post_process.ROUND_REPORT_USER_PROMPT
    assert (
        report_summaries.ROUND_REPORT_WITH_HYPOTHESIS_USER_PROMPT
        == post_process.ROUND_REPORT_WITH_HYPOTHESIS_USER_PROMPT
    )


def test_memory_summary_is_a_separate_persisted_sibling(monkeypatch, tmp_path):
    round_dir = tmp_path / "round_3"
    round_dir.mkdir()
    full = round_dir / "memory_report.md"
    summary = round_dir / "memory_report_summary.md"
    full_bytes = (
        b"# Memory-Integrated Report - Round 3\n\n"
        b"## Accumulated Report\n\nFull accumulated findings.\n"
    )
    full.write_bytes(full_bytes)
    monkeypatch.setattr(
        post_process,
        "summarize_content",
        lambda content, prompt, **kwargs: "Persisted memory summary.",
    )

    assert post_process.summarize_report(
        full,
        summary,
        post_process.MEMORY_REPORT_USER_PROMPT,
        "Memory Report",
    ) is True

    assert full.read_bytes() == full_bytes
    assert summary.read_text(encoding="utf-8") == (
        "# Memory Report Summary\n\n"
        "*Automatically generated summary of memory_report.md*\n\n"
        "---\n\n"
        "Persisted memory summary."
    )


def test_post_process_targets_both_report_summary_siblings(monkeypatch, tmp_path):
    checkpoints = tmp_path / "checkpoints"
    round_dir = checkpoints / "round_2"
    round_dir.mkdir(parents=True)
    (round_dir / "memory_report.md").write_text("memory", encoding="utf-8")
    (round_dir / "round_report.md").write_text("round", encoding="utf-8")
    calls = []

    def record(input_path, output_path, prompt, report_type, **kwargs):
        calls.append((input_path, output_path, prompt, report_type, kwargs))
        return True

    monkeypatch.setattr(post_process, "summarize_report", record)
    monkeypatch.setattr(post_process, "load_previous_round_hypothesis", lambda *_: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["post_process.py", "--checkpoint-dir", str(checkpoints), "--round", "2"],
    )

    post_process.main()

    assert [(call[0].name, call[1].name, call[3]) for call in calls] == [
        ("memory_report.md", "memory_report_summary.md", "Memory Report"),
        ("round_report.md", "round_report_summary.md", "Round Report"),
    ]
    assert calls[0][2] is post_process.MEMORY_REPORT_USER_PROMPT
    assert calls[1][2] is post_process.ROUND_REPORT_USER_PROMPT


def test_missing_or_empty_full_report_does_not_create_a_summary(monkeypatch, tmp_path):
    summary = tmp_path / "memory_report_summary.md"
    monkeypatch.setattr(
        post_process,
        "summarize_content",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("the model must not run without a full report")
        ),
    )

    assert post_process.summarize_report(
        tmp_path / "missing.md",
        summary,
        post_process.MEMORY_REPORT_USER_PROMPT,
        "Memory Report",
    ) is False
    empty = tmp_path / "memory_report.md"
    empty.write_text(" \n", encoding="utf-8")
    assert post_process.summarize_report(
        empty,
        summary,
        post_process.MEMORY_REPORT_USER_PROMPT,
        "Memory Report",
    ) is False
    assert not summary.exists()


def _persisted_full_reports(tmp_path: Path, round_number: int = 0) -> Path:
    round_dir = tmp_path / "checkpoints" / f"round_{round_number}"
    round_dir.mkdir(parents=True)
    (round_dir / "round_report.md").write_text("FULL ROUND REPORT", encoding="utf-8")
    (round_dir / "memory_report.md").write_text("FULL MEMORY REPORT", encoding="utf-8")
    return round_dir


def test_fresh_round_generates_both_siblings_from_only_v1_context(tmp_path):
    round_dir = _persisted_full_reports(tmp_path)
    calls = []

    def request(content, prompt, **kwargs):
        calls.append((content, prompt, kwargs))
        return "Concise persisted summary."

    outcomes = report_summaries.persist_report_summaries(
        round_dir,
        0,
        model_name="claude-opus-4-6",
        readme="DATASET README",
        request=request,
    )

    assert [outcome.status for outcome in outcomes] == ["created", "created"]
    assert [call[0] for call in calls] == ["FULL MEMORY REPORT", "FULL ROUND REPORT"]
    assert all(call[2]["readme"] == "DATASET README" for call in calls)
    assert all(call[2]["model_name"] == "claude-opus-4-6" for call in calls)
    assert calls[0][2]["hypothesis"] is None
    assert calls[1][2]["hypothesis"] is None
    assert "Concise persisted summary." in (round_dir / "memory_report_summary.md").read_text()
    assert "Concise persisted summary." in (round_dir / "round_report_summary.md").read_text()


def test_continuation_round_uses_only_previous_selected_hypothesis(tmp_path):
    previous = tmp_path / "checkpoints" / "round_0"
    previous.mkdir(parents=True)
    (previous / "selected_hypothesis.md").write_text("PREVIOUS HYPOTHESIS", encoding="utf-8")
    round_dir = _persisted_full_reports(tmp_path, 1)
    calls = []

    def request(content, prompt, **kwargs):
        calls.append((content, prompt, kwargs))
        return "Summary"

    report_summaries.persist_report_summaries(
        round_dir, 1, model_name="claude-opus-4-6", readme="README", request=request,
    )

    assert calls[0][1] is report_summaries.MEMORY_REPORT_USER_PROMPT
    assert calls[0][2]["hypothesis"] is None
    assert calls[1][1] is report_summaries.ROUND_REPORT_WITH_HYPOTHESIS_USER_PROMPT
    assert calls[1][2]["hypothesis"] == "PREVIOUS HYPOTHESIS"
    assert "Evaluating hypothesis from previous round" in (
        round_dir / "round_report_summary.md"
    ).read_text(encoding="utf-8")


def test_resume_and_repeated_completion_are_idempotent(tmp_path):
    round_dir = _persisted_full_reports(tmp_path)
    calls = []

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return f"Summary call {len(calls)}"

    first = report_summaries.persist_report_summaries(
        round_dir, 0, model_name="claude-opus-4-6", request=request,
    )
    original = {
        path.name: path.read_bytes()
        for path in round_dir.glob("*_report_summary.md")
    }
    resumed = report_summaries.persist_report_summaries(
        round_dir, 0, model_name="claude-opus-4-6", request=request,
    )

    assert len(calls) == 2
    assert [outcome.status for outcome in first] == ["created", "created"]
    assert [outcome.status for outcome in resumed] == ["existing", "existing"]
    assert original == {
        path.name: path.read_bytes()
        for path in round_dir.glob("*_report_summary.md")
    }


def test_partial_existing_state_generates_only_missing_summary(tmp_path):
    round_dir = _persisted_full_reports(tmp_path)
    memory_summary = round_dir / "memory_report_summary.md"
    memory_summary.write_text("historical valid memory summary", encoding="utf-8")
    calls = []

    def request(content, *_args, **_kwargs):
        calls.append(content)
        return "new round summary"

    outcomes = report_summaries.persist_report_summaries(
        round_dir, 0, model_name="claude-opus-4-6", request=request,
    )

    assert calls == ["FULL ROUND REPORT"]
    assert [outcome.status for outcome in outcomes] == ["existing", "created"]
    assert memory_summary.read_text(encoding="utf-8") == "historical valid memory summary"


def test_no_submission_until_both_full_reports_are_persisted(tmp_path):
    round_dir = tmp_path / "round_0"
    round_dir.mkdir()
    (round_dir / "round_report.md").write_text("full", encoding="utf-8")

    outcomes = report_summaries.persist_report_summaries(
        round_dir,
        0,
        model_name="claude-opus-4-6",
        request=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Anthropic must not be called")
        ),
    )

    assert [outcome.status for outcome in outcomes] == ["deferred", "deferred"]
    assert not list(round_dir.glob("*_report_summary.md"))


@pytest.mark.parametrize(
    ("failure", "category"),
    [
        (report_summaries.SummaryRefusalError("refused"), "refusal"),
        (report_summaries.SummaryEmptyResponseError("empty"), "empty_response"),
        (RuntimeError("secret report text must not be logged"), "api_or_persistence_error"),
    ],
)
def test_failure_is_non_destructive_and_diagnostic_is_content_free(tmp_path, failure, category):
    round_dir = _persisted_full_reports(tmp_path)
    full_bytes = {
        path.name: path.read_bytes() for path in round_dir.glob("*_report.md")
    }

    outcomes = report_summaries.persist_report_summaries(
        round_dir,
        0,
        model_name="claude-opus-4-6",
        request=lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )

    assert [outcome.status for outcome in outcomes] == ["failed", "failed"]
    assert all(category in outcome.diagnostic for outcome in outcomes)
    assert all("secret report text" not in outcome.diagnostic for outcome in outcomes)
    assert not list(round_dir.glob("*_report_summary.md"))
    assert full_bytes == {
        path.name: path.read_bytes() for path in round_dir.glob("*_report.md")
    }


class _Messages:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _TransientError(RuntimeError):
    status_code = 503


def _response(text=None, *, stop_reason="end_turn"):
    blocks = [] if text is None else [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(content=blocks, stop_reason=stop_reason, stop_details=None)


def test_anthropic_request_retries_transient_failure_and_never_sends_extra_context():
    messages = _Messages([_TransientError("overloaded"), _response("usable summary")])
    waits = []
    client = SimpleNamespace(messages=messages)

    result = report_summaries.request_summary(
        "FULL REPORT",
        report_summaries.MEMORY_REPORT_USER_PROMPT,
        model_name="claude-opus-4-6",
        readme="README",
        client=client,
        max_retries=1,
        retry_wait_seconds=0.25,
        sleep=waits.append,
    )

    assert result == "usable summary"
    assert len(messages.calls) == 2
    assert waits == [0.25]
    prompt = messages.calls[0]["messages"][0]["content"]
    assert "FULL REPORT" in prompt and "README" in prompt
    assert set(messages.calls[0]) == {
        "model", "max_tokens", "temperature", "system", "messages",
    }


def test_anthropic_refusal_and_empty_output_are_distinct_and_not_retried():
    refusal_messages = _Messages([_response(stop_reason="refusal")])
    with pytest.raises(report_summaries.SummaryRefusalError):
        report_summaries.request_summary(
            "report",
            report_summaries.MEMORY_REPORT_USER_PROMPT,
            model_name="claude-opus-4-6",
            client=SimpleNamespace(messages=refusal_messages),
        )
    empty_messages = _Messages([_response()])
    with pytest.raises(report_summaries.SummaryEmptyResponseError):
        report_summaries.request_summary(
            "report",
            report_summaries.MEMORY_REPORT_USER_PROMPT,
            model_name="claude-opus-4-6",
            client=SimpleNamespace(messages=empty_messages),
        )
    assert len(refusal_messages.calls) == 1
    assert len(empty_messages.calls) == 1


class _Logger:
    def __init__(self):
        self.actions = []
        self.errors = []

    def log_action(self, event, message):
        self.actions.append((event, message))

    def log_error(self, message):
        self.errors.append(message)


def test_brancher_uses_snapshotted_summary_model_and_failure_never_raises(monkeypatch, tmp_path):
    round_dir = _persisted_full_reports(tmp_path)
    working = tmp_path / "code"
    (working / "data").mkdir(parents=True)
    (working / "data" / "README.txt").write_text("README ONLY", encoding="utf-8")
    brancher = object.__new__(hypothesis_brancher.HypothesisBrancher)
    brancher.run_config = {"working_directory": str(working)}
    config = SimpleNamespace(model_name="claude-sonnet-4-6")
    brancher.workflow_config = SimpleNamespace(
        report_summarization_llm_config=config,
        agent_config_list=[],
    )
    logger = _Logger()
    workflow = SimpleNamespace(report_summarization_llm_config=config, logger=logger)
    seen = []

    def fail(*args, **kwargs):
        seen.append((args, kwargs))
        raise RuntimeError("report content should never enter diagnostics")

    monkeypatch.setattr(hypothesis_brancher, "persist_report_summaries", fail)
    brancher._persist_completed_report_summaries(
        workflow,
        0,
        str(round_dir / "round_report.md"),
        str(round_dir / "memory_report.md"),
    )

    assert seen[0][1] == {
        "model_name": "claude-sonnet-4-6",
        "readme": "README ONLY",
    }
    assert logger.errors == [
        "report summary failed: report=paired category=local_error exception=RuntimeError"
    ]


def test_project_model_contract_includes_report_summarization_field():
    from AIR.utils.arg_parsers import ANTHROPIC_MODEL_FIELDS

    assert "report_summarization_model" in ANTHROPIC_MODEL_FIELDS
