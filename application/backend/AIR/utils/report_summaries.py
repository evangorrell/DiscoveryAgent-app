"""Persist the established V1 round and memory report summaries safely.

This module is deliberately downstream of report persistence.  It submits only
the two full reports plus the small pieces of context used by the V1
``scripts/post_process.py`` workflow: the dataset README and, for continuation
rounds, the previously selected hypothesis.
"""

from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import anthropic

from .llm_diagnostics import refusal_notice

try:  # DiscoveryAgent's supported local runtimes are POSIX.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


SUMMARIZATION_SYSTEM_PROMPT = """You are an expert research analyst tasked with creating concise summaries of scientific research reports.

Your goal is to distill the key findings, methods, and insights from research reports into clear, actionable summaries that preserve the most important information while significantly reducing length.

Focus on:
1. Key findings and discoveries
2. Important methods or approaches used
3. Significant insights or conclusions
4. Any notable limitations or next steps mentioned

Be concise but comprehensive. Use clear scientific language. Aim for a summary that is roughly 1.5 to 2 pages in length. Do not use salesman language or try to convince the reader of your research.
Your job is to be transparent and provide the reader with a clear and concise view of the research that's been done."""

MEMORY_REPORT_USER_PROMPT = """Please summarize the following memory-integrated research report. This report contains accumulated knowledge synthesized across multiple rounds of research.

For context, here is the dataset description and overall research goal:
---
DATASET & RESEARCH CONTEXT:
{readme}
---

Focus on the main discoveries, methods used, and overall conclusions. Preserve the key scientific insights while condensing the content. Relate findings back to the original research goal where relevant.

---
MEMORY REPORT:
{content}
---

In your summary, choose up to 3 figures that you believe are the most relevant to the central findings and key scientific insights. When including them, copy the reference from the memory report and use the exact same path and markdown formatting.
If you include literature references, you must copy the exact reference from the memory report. DO NOT try to add more references than are already given.

Provide a clear, structured summary:"""

ROUND_REPORT_USER_PROMPT = """Please summarize the following round report. This report contains findings from a single round of research queries.

For context, here is the dataset description and overall research goal:
---
DATASET & RESEARCH CONTEXT:
{readme}
---

Focus on what was discovered in this round, the approaches taken, and key results. Highlight the most important findings and how they relate to the overall research goal.

---
ROUND REPORT:
{content}
---

In your summary, choose up to 3 figures that you believe are the most relevant to the central findings and key scientific insights. When including them, copy the reference from the round report and use the exact same path and markdown formatting.
If you include literature references, you must copy the exact reference from the round report. DO NOT try to add more references than are already given.

Provide a clear, structured summary:"""

ROUND_REPORT_WITH_HYPOTHESIS_USER_PROMPT = """Please summarize the following round report, specifically evaluating whether the hypothesis from the previous round was validated or invalidated.

For context, here is the dataset description and overall research goal:
---
DATASET & RESEARCH CONTEXT:
{readme}
---

The hypothesis being tested was:
---
HYPOTHESIS:
{hypothesis}
---

The research findings from this round are:
---
ROUND REPORT:
{content}
---

Your summary should:
1. **State the hypothesis being tested**: Repeat the hypothesis being tested at the beginning of the report.
2. **Hypothesis Verdict**: Clearly state whether the hypothesis was VALIDATED, INVALIDATED, or PARTIALLY SUPPORTED based on the evidence
3. **Supporting Evidence**: Detail the specific findings that support or contradict the hypothesis
4. **Key Discoveries**: Summarize the most important findings from this round
5. **Unexpected Results**: Note any surprising findings or deviations from expectations
6. **Remaining Questions**: Identify any aspects of the hypothesis that remain untested or unclear

After you clearly explain the hypothesis verdict, detail any other key findings from the round that may not be relevant to the hypothesis but are relevant to the overall research question.
In your summary, choose up to 3 figures that you believe are the most relevant to the central findings and key scientific insights. When including them, copy the reference from the round report and use the exact same path and markdown formatting.
If you include literature references, you must copy the exact reference from the round report. DO NOT try to add more references than are already given.

Provide a clear, structured summary:"""


class SummaryRefusalError(RuntimeError):
    """Anthropic explicitly refused the summary request."""


class SummaryEmptyResponseError(RuntimeError):
    """Anthropic returned no usable summary text."""


@dataclass(frozen=True)
class ReportSummaryOutcome:
    report_kind: str
    status: str
    path: Path
    diagnostic: Optional[str] = None


def _existing_summary_is_valid(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        return bool(path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeError):
        return False


def _full_report(path: Path) -> Optional[str]:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    return content if content.strip() else None


def _response_text(response) -> str:
    refusal = refusal_notice(response)
    if refusal:
        raise SummaryRefusalError(refusal)
    parts = []
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", None)
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    summary = "\n\n".join(parts).strip()
    if not summary:
        raise SummaryEmptyResponseError("Anthropic returned no usable summary text")
    return summary


def _retryable_anthropic_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status in {408, 409, 429} or (isinstance(status, int) and status >= 500):
        return True
    message = str(exc).lower()
    return any(token in message for token in ("overloaded", "timeout", "connection", "429", "500", "503"))


def request_summary(
    content: str,
    prompt_template: str,
    *,
    model_name: str,
    readme: Optional[str] = None,
    hypothesis: Optional[str] = None,
    max_tokens: int = 8192,
    max_retries: int = 5,
    retry_wait_seconds: float = 60.0,
    client=None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Make one bounded-retry Anthropic-only V1 summary request."""
    if not str(model_name or "").startswith("claude-"):
        raise ValueError("report summarization requires an Anthropic Claude model")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if client is None:
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is not configured")
        client = anthropic.Anthropic(api_key=api_key)
    prompt = prompt_template.format(
        content=content,
        readme=readme if readme is not None else "(No dataset context available)",
        hypothesis=hypothesis or "",
    )
    api_params = {
        "model": model_name,
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "system": SUMMARIZATION_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }
    for attempt in range(max_retries + 1):
        try:
            return _response_text(client.messages.create(**api_params))
        except (SummaryRefusalError, SummaryEmptyResponseError):
            raise
        except Exception as exc:
            if attempt >= max_retries or not _retryable_anthropic_error(exc):
                raise
            sleep(retry_wait_seconds)
    raise RuntimeError("unreachable summary retry state")


def _summary_document(report_kind: str, report_name: str, summary: str, *, hypothesis: bool) -> str:
    title = "Memory Report" if report_kind == "memory" else "Round Report"
    sections = [
        f"# {title} Summary",
        "",
        f"*Automatically generated summary of {report_name}*",
        "",
    ]
    if hypothesis:
        sections.extend(["*Evaluating hypothesis from previous round*", ""])
    sections.extend(["---", "", summary.strip()])
    return "\n".join(sections)


def _atomic_write(path: Path, content: str) -> None:
    if path.is_symlink():
        raise ValueError("summary target must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _safe_failure_diagnostic(report_kind: str, exc: Exception) -> str:
    if isinstance(exc, SummaryRefusalError):
        category = "refusal"
    elif isinstance(exc, SummaryEmptyResponseError):
        category = "empty_response"
    else:
        category = "api_or_persistence_error"
    status = getattr(exc, "status_code", None)
    request_id = getattr(exc, "request_id", None)
    details = [f"report={report_kind}", f"category={category}", f"exception={type(exc).__name__}"]
    if status is not None:
        details.append(f"http_status={status}")
    if request_id:
        details.append(f"request_id={request_id}")
    return "report summary failed: " + " ".join(details)


def persist_report_summaries(
    round_dir: Path,
    round_number: int,
    *,
    model_name: str,
    readme: Optional[str] = None,
    max_tokens: int = 8192,
    request: Callable[..., str] = request_summary,
) -> tuple[ReportSummaryOutcome, ReportSummaryOutcome]:
    """Generate missing V1 sibling summaries after both full reports exist.

    A per-round advisory lock makes completion/re-entry idempotent across local
    processes.  Existing nonempty summaries are never submitted or rewritten.
    """
    round_dir = Path(round_dir).expanduser().absolute()
    full_paths = {
        "memory": round_dir / "memory_report.md",
        "round": round_dir / "round_report.md",
    }
    contents = {kind: _full_report(path) for kind, path in full_paths.items()}
    if not all(contents.values()):
        return tuple(
            ReportSummaryOutcome(
                kind,
                "deferred",
                round_dir / f"{kind}_report_summary.md",
                "both persisted full reports are required",
            )
            for kind in ("memory", "round")
        )

    lock_path = round_dir / ".report_summaries.lock"
    round_dir.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a", encoding="utf-8") as lock:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            previous_hypothesis = None
            if round_number > 0:
                previous_path = round_dir.parent / f"round_{round_number - 1}" / "selected_hypothesis.md"
                previous_hypothesis = _full_report(previous_path)
            outcomes = []
            for report_kind, prompt_template in (
                ("memory", MEMORY_REPORT_USER_PROMPT),
                (
                    "round",
                    ROUND_REPORT_WITH_HYPOTHESIS_USER_PROMPT
                    if previous_hypothesis else ROUND_REPORT_USER_PROMPT,
                ),
            ):
                target = round_dir / f"{report_kind}_report_summary.md"
                if _existing_summary_is_valid(target):
                    outcomes.append(ReportSummaryOutcome(report_kind, "existing", target))
                    continue
                try:
                    summary = request(
                        contents[report_kind],
                        prompt_template,
                        model_name=model_name,
                        readme=readme,
                        hypothesis=previous_hypothesis if report_kind == "round" else None,
                        max_tokens=max_tokens,
                    )
                    if not isinstance(summary, str) or not summary.strip():
                        raise SummaryEmptyResponseError("summary output was empty")
                    _atomic_write(
                        target,
                        _summary_document(
                            report_kind,
                            full_paths[report_kind].name,
                            summary,
                            hypothesis=bool(previous_hypothesis and report_kind == "round"),
                        ),
                    )
                    outcomes.append(ReportSummaryOutcome(report_kind, "created", target))
                except Exception as exc:
                    outcomes.append(
                        ReportSummaryOutcome(
                            report_kind,
                            "failed",
                            target,
                            _safe_failure_diagnostic(report_kind, exc),
                        )
                    )
            return tuple(outcomes)
        finally:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
