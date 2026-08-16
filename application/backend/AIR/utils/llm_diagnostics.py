"""Shared diagnostics for LLM API exchanges.

Every agent that talks to Anthropic/OpenAI funnels its request, response, and
exceptions through here so that silent failures (empty completions, truncation,
refusals, swallowed SDK errors) leave a searchable trace in ai_researcher.log
instead of an empty string travelling downstream.

Nothing here may raise: diagnostics must never take down the run they describe.
"""

from __future__ import annotations

import traceback
from typing import Any, Dict, Optional

# Cap on any single value rendered into a log line.
_PREVIEW_CHARS = 200
_BODY_CHARS = 1000


def _short(value: Any, limit: int = _PREVIEW_CHARS) -> str:
    """Render any value as a single-line, length-capped string."""
    try:
        text = value if isinstance(value, str) else repr(value)
    except Exception:
        return "<unrenderable>"
    text = text.replace("\n", " ")
    return text if len(text) <= limit else f"{text[:limit]}...(+{len(text) - limit} chars)"


def summarize_response_blocks(response: Any) -> str:
    """Return a compact summary of Anthropic response blocks for debugging."""
    blocks = []
    try:
        content = getattr(response, "content", None) or []
    except Exception:
        return "<content unavailable>"
    for i, block in enumerate(content):
        block_type = getattr(block, "type", None) or "unknown"
        extras = []
        name = getattr(block, "name", None)
        if name:
            extras.append(f"name={name}")
        if block_type in ("text", "thinking"):
            text = getattr(block, block_type, "") or ""
            extras.append(f"chars={len(text)}")
            preview = text.strip().replace("\n", " ")
            if preview:
                extras.append(f"preview={preview[:120]!r}")
        elif block_type == "web_search_tool_result":
            extras.append(f"id={getattr(block, 'id', None)}")
        details = f" ({', '.join(extras)})" if extras else ""
        blocks.append(f"{i}:{block_type}{details}")
    return "; ".join(blocks) if blocks else "<no content blocks>"


def describe_request(api_params: Optional[Dict[str, Any]]) -> str:
    """Summarize the request parameters that produced a response.

    Records the settings that most often explain a bad response — model,
    max_tokens, conversation size, and any thinking/tool/beta options.
    """
    if not isinstance(api_params, dict):
        return "request=<unavailable>"
    parts = [
        f"model={api_params.get('model')}",
        f"max_tokens={api_params.get('max_tokens')}",
    ]
    messages = api_params.get("messages")
    if isinstance(messages, list):
        parts.append(f"messages={len(messages)}")
    system = api_params.get("system")
    if isinstance(system, str):
        parts.append(f"system_chars={len(system)}")
    for optional in ("thinking", "output_config", "temperature", "betas",
                     "context_management", "extra_body"):
        value = api_params.get(optional)
        if value:
            parts.append(f"{optional}={_short(value)}")
    for listed in ("tools", "mcp_servers"):
        value = api_params.get(listed)
        if value:
            parts.append(f"{listed}={len(value)}")
    return " ".join(parts)


def describe_response(response: Any) -> str:
    """Summarize an Anthropic response: why it stopped, what it contained, what it cost."""
    parts = [
        f"model={getattr(response, 'model', None)}",
        f"stop_reason={getattr(response, 'stop_reason', None)}",
    ]
    stop_details = getattr(response, "stop_details", None)
    if stop_details is not None:
        parts.append(f"stop_details={_short(stop_details)}")
    request_id = getattr(response, "_request_id", None)
    if request_id:
        parts.append(f"request_id={request_id}")
    usage = getattr(response, "usage", None)
    if usage is not None:
        usage_parts = []
        for field in ("input_tokens", "output_tokens",
                      "cache_read_input_tokens", "cache_creation_input_tokens"):
            value = getattr(usage, field, None)
            if value:
                usage_parts.append(f"{field.replace('_tokens', '')}={value}")
        if usage_parts:
            parts.append(f"usage({', '.join(usage_parts)})")
    parts.append(f"blocks=[{summarize_response_blocks(response)}]")
    return " ".join(parts)


def log_api_exchange(logger, source: str, api_params: Optional[Dict[str, Any]],
                     response: Any, text: Optional[str]) -> None:
    """Log one line per API exchange, escalating to ERROR when no text came back.

    An empty completion is the failure this exists to catch: downstream code
    sees only "" and reports a formatting problem it cannot diagnose.
    """
    try:
        detail = (
            f"source={source}\n"
            f"request: {describe_request(api_params)}\n"
            f"response: {describe_response(response)}\n"
            f"extracted_text_chars={len(text or '')}"
        )
        if text and text.strip():
            logger.log_action("api_exchange", detail)
        else:
            logger.log_error(
                "[EMPTY RESPONSE] The model returned no usable text. "
                "Downstream agents will see an empty string.\n" + detail
            )
    except Exception:
        pass


def format_api_exception(exc: Exception, *, headline: str,
                         context: Optional[Dict[str, Any]] = None,
                         include_traceback: bool = False) -> str:
    """Build a detailed SDK error string for user-facing output or logs."""
    lines = [headline, f"Exception: {type(exc).__name__}: {exc}"]
    for key, value in (context or {}).items():
        lines.append(f"{key}: {value}")

    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        lines.append(f"HTTP status: {status_code}")

    request_id = getattr(exc, "request_id", None)
    if request_id:
        lines.append(f"Request ID: {request_id}")

    body = getattr(exc, "body", None)
    if body:
        lines.append(f"Response body: {_short(body, _BODY_CHARS)}")

    response = getattr(exc, "response", None)
    if response is not None:
        resp_status = getattr(response, "status_code", None)
        if resp_status is not None and resp_status != status_code:
            lines.append(f"Response status: {resp_status}")
        text = getattr(response, "text", None)
        if text:
            lines.append(f"Response text: {_short(text, _BODY_CHARS)}")

    if include_traceback:
        lines.append("Traceback:")
        lines.append(traceback.format_exc().rstrip())
    return "\n".join(lines)


def log_api_exception(logger, exc: Exception, *, source: str,
                      api_params: Optional[Dict[str, Any]] = None,
                      retryable: bool = False) -> None:
    """Log an API exception with full SDK detail before it propagates."""
    try:
        context = {"Source": source, "Retryable": "yes" if retryable else "no"}
        if api_params is not None:
            context["Request"] = describe_request(api_params)
        logger.log_error(format_api_exception(
            exc,
            headline="LLM API call failed.",
            context=context,
            include_traceback=not retryable,
        ))
    except Exception:
        pass


def demo() -> None:
    """Self-check: diagnostics render the failure we actually hit, and never raise."""

    class _Block:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class _Logger:
        def __init__(self):
            self.actions, self.errors = [], []

        def log_action(self, action, details=None):
            self.actions.append((action, details))

        def log_error(self, msg, code=None):
            self.errors.append(msg)

    # The observed failure: a response with no text block at all.
    empty = _Block(model="claude-opus-4-6", stop_reason="max_tokens", content=[
        _Block(type="thinking", thinking="x" * 30),
    ], usage=_Block(input_tokens=1193, output_tokens=4096))
    logger = _Logger()
    log_api_exchange(logger, "DirectorAgent", {"model": "claude-opus-4-6", "max_tokens": 4096,
                                               "messages": [1, 2]}, empty, "")
    assert not logger.actions, "empty response must not log as a normal action"
    assert len(logger.errors) == 1 and "EMPTY RESPONSE" in logger.errors[0]
    assert "stop_reason=max_tokens" in logger.errors[0]
    assert "max_tokens=4096" in logger.errors[0]
    assert "thinking" in logger.errors[0]

    # A healthy response logs once, at action level.
    ok = _Block(model="m", stop_reason="end_turn",
                content=[_Block(type="text", text="hello")], usage=None)
    logger = _Logger()
    log_api_exchange(logger, "DirectorAgent", {"model": "m"}, ok, "hello")
    assert logger.actions and not logger.errors

    # Garbage in must not raise.
    logger = _Logger()
    log_api_exchange(logger, "X", None, object(), None)
    log_api_exception(logger, ValueError("boom"), source="X", api_params=None)
    assert len(logger.errors) == 2

    assert "type=" not in describe_response(object())  # tolerates non-responses
    print("llm_diagnostics demo: OK")


if __name__ == "__main__":
    demo()
