"""Paperclip SDK-backed retrieval provider for deep_research.

Uses the `gxl_paperclip` Python SDK (search -> map -> reduce) rather than
shelling out to the `paperclip` CLI. The SDK is imported lazily inside
deep_research() so importing this module never fails when the optional
`paperclip` extra is not installed -- DeepResearchAgent can still fall back
to the Perplexity provider in that case.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from .base_provider import RetrievalCitation, RetrievalProvider, RetrievalResult, RetrievalStatus

# The Paperclip SDK's search() requires an explicit `limit`
DEFAULT_PAPERCLIP_SEARCH_LIMIT = 10

_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


def _strip_think_blocks(text: str) -> str:
    while _THINK_OPEN in text:
        start = text.find(_THINK_OPEN)
        end = text.find(_THINK_CLOSE, start)
        if end == -1:
            break
        text = text[:start] + text[end + len(_THINK_CLOSE):]
    return text


def _build_map_prompt(query: str) -> str:
    return (
        "Extract the key findings, methods, model organism/cell type, perturbation or "
        "intervention, evidence strength, limitations, and direct relevance to the "
        "following research question. Note whether the paper supports, contradicts, or "
        f"complicates the claim. Research question: {query}"
    )


class PaperclipSDKProvider(RetrievalProvider):
    """Deep-research provider backed by the Paperclip Python SDK."""

    def __init__(self, timeout: float = 1800.0):
        self.timeout = timeout

    def deep_research(self, query: str) -> RetrievalResult:
        audit: Dict[str, Any] = {
            "provider": "paperclip",
            "query": query,
            "timestamp": datetime.datetime.now().isoformat(),
        }

        try:
            import gxl_paperclip
            from gxl_paperclip import MapResultEvent, PaperclipClient
        except ImportError as exc:
            return RetrievalResult(
                status=RetrievalStatus.ERROR,
                markdown=self._error_markdown(f"Paperclip SDK not installed: {exc}"),
                error_message=str(exc),
                audit_metadata=audit,
            )

        audit["sdk_version"] = getattr(gxl_paperclip, "__version__", None)

        try:
            client = PaperclipClient.from_env(timeout=self.timeout)
        except Exception as exc:
            return RetrievalResult(
                status=RetrievalStatus.ERROR,
                markdown=self._error_markdown(f"Paperclip auth/client setup failed: {exc}"),
                error_message=str(exc),
                audit_metadata=audit,
            )

        try:
            search_result = client.search(
                query,
                limit=DEFAULT_PAPERCLIP_SEARCH_LIMIT,
                timeout=self.timeout,
            )
        except Exception as exc:
            return RetrievalResult(
                status=RetrievalStatus.ERROR,
                markdown=self._error_markdown(f"Paperclip search failed: {exc}"),
                error_message=str(exc),
                audit_metadata=audit,
            )

        search_result_id = getattr(search_result, "result_id", None)
        audit["search_result_id"] = search_result_id
        audit["search_count"] = getattr(search_result, "count", None)

        if not search_result_id:
            return RetrievalResult(
                status=RetrievalStatus.EMPTY,
                markdown=self._empty_markdown(query),
                audit_metadata=audit,
            )

        map_question = _build_map_prompt(query)
        map_result_id: Optional[str] = None
        map_output: Optional[str] = None
        progress_event_count = 0

        try:
            for event in client.map_(map_question, from_results=search_result_id, timeout=self.timeout):
                # Be robust to SDK event-shape changes: prefer isinstance against the
                # typed MapResultEvent, but fall back to duck-typing on attributes that
                # only the final result event carries (exit_code/result_id/output).
                is_result_event = isinstance(event, MapResultEvent) or hasattr(event, "exit_code")
                if is_result_event:
                    map_result_id = getattr(event, "result_id", None)
                    map_output = getattr(event, "output", None)
                else:
                    progress_event_count += 1
        except Exception as exc:
            audit["map_progress_events"] = progress_event_count
            return RetrievalResult(
                status=RetrievalStatus.ERROR,
                markdown=self._error_markdown(f"Paperclip map failed: {exc}"),
                error_message=str(exc),
                audit_metadata=audit,
            )

        audit["map_result_id"] = map_result_id
        audit["map_progress_events"] = progress_event_count

        if not map_result_id and not map_output:
            return RetrievalResult(
                status=RetrievalStatus.EMPTY,
                markdown=self._empty_markdown(query),
                audit_metadata=audit,
            )

        reduce_output = ""
        reduce_result_id = None
        if map_result_id:
            try:
                reduce_result = client.execute(
                    "reduce",
                    ["--from", map_result_id, "--strategy", "summarize", query],
                    timeout=self.timeout,
                )
                reduce_output = (getattr(reduce_result, "output", None) or "").strip()
                reduce_result_id = getattr(reduce_result, "result_id", None)
            except Exception as exc:
                # Reduce failing is not fatal -- fall back to the raw map output below.
                audit["reduce_error"] = str(exc)

        audit["reduce_result_id"] = reduce_result_id

        body = reduce_output or (map_output or "").strip()
        if not body:
            return RetrievalResult(
                status=RetrievalStatus.EMPTY,
                markdown=self._empty_markdown(query),
                audit_metadata=audit,
            )

        body = _strip_think_blocks(body)

        citations: List[RetrievalCitation] = []
        for paper in getattr(search_result, "papers", None) or []:
            if not isinstance(paper, dict):
                continue
            citations.append(
                RetrievalCitation(
                    title=paper.get("title"),
                    url=paper.get("url") or paper.get("doi"),
                    result_id=paper.get("id") or paper.get("result_id"),
                )
            )
        audit["citations"] = [
            {"title": c.title, "url": c.url, "result_id": c.result_id} for c in citations
        ]

        markdown = self._success_markdown(body, audit)
        return RetrievalResult(
            status=RetrievalStatus.OK,
            markdown=markdown,
            citations=citations,
            audit_metadata=audit,
        )

    @staticmethod
    def _error_markdown(message: str) -> str:
        return f"**[RETRIEVAL: ERROR]** Paperclip retrieval failed: {message}"

    @staticmethod
    def _empty_markdown(query: str) -> str:
        return f"**[RETRIEVAL: NO RESULTS]** Paperclip returned no results for: {query}"

    @staticmethod
    def _success_markdown(body: str, audit: Dict[str, Any]) -> str:
        if "# " not in body:
            body = "# Research Report\n\n" + body

        sources_lines = ["\n\n## Sources / Audit"]
        if audit.get("search_result_id"):
            sources_lines.append(f"- Paperclip search result ID: `{audit['search_result_id']}`")
        if audit.get("map_result_id"):
            sources_lines.append(f"- Paperclip map result ID: `{audit['map_result_id']}`")
        if audit.get("reduce_result_id"):
            sources_lines.append(f"- Paperclip reduce result ID: `{audit['reduce_result_id']}`")

        return body.strip() + "\n".join(sources_lines) + "\n"
