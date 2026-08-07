import asyncio
import json
import os, re
from pathlib import Path
from typing import Optional, Tuple, Union

project_root = Path(__file__).resolve().parent.parent.parent
claude_cli_path = project_root / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"

# Apply the same environment setup pattern used in gen_paper.py
os.environ.setdefault("CLAUDE_CODE_CLI_PATH", str(claude_cli_path))
os.environ.setdefault("NODE_PATH", str(project_root / "node_modules"))
os.environ.setdefault("MAX_MCP_OUTPUT_TOKENS", "50000")


def _warn_missing_cli():
    if not claude_cli_path.exists():
        print(f"Warning: Claude Code CLI not found at {claude_cli_path}")
        print("Install with: npm install @anthropic-ai/claude-code")


try:
    from claude_agent_sdk import (
        query,
        ClaudeAgentOptions,
        AssistantMessage,
        ResultMessage,
        SystemMessage,
        StreamEvent,
        TextBlock,
        ThinkingBlock,
        ToolUseBlock,
        ToolResultBlock,
    )
except Exception:
    # Defer import error until call time so callers can handle gracefully
    query = None
    ClaudeAgentOptions = None
    AssistantMessage = None
    ResultMessage = None
    SystemMessage = None
    TextBlock = None
    ThinkingBlock = None
    ToolUseBlock = None
    ToolResultBlock = None


def _parse_text_block(msg_str: str) -> Optional[str]:
    """
    Best-effort extraction of assistant text from a streamed message string.
    We intentionally avoid leaking tool traces and only return assistant text blocks.
    """
    if "AssistantMessage" in msg_str and "TextBlock" in msg_str:
        matches = re.findall(r'TextBlock\(text="([^"]*)"', msg_str)
        if not matches:
            matches = re.findall(r"TextBlock\(text='([^']*)'", msg_str)
        cleaned = []
        for match in matches:
            text = match.replace("\\n", "\n").replace('\\"', '"').replace("\\'", "'").strip()
            if text:
                cleaned.append(text)
        if cleaned:
            return "\n\n".join(cleaned)
    return None


def _extract_message_text(msg) -> Optional[str]:
    """
    Prefer structured parsing via SDK types; fall back to regex when unavailable.
    """
    texts = []

    if AssistantMessage and isinstance(msg, AssistantMessage):
        for block in getattr(msg, "content", []):
            if TextBlock and isinstance(block, TextBlock):
                value = getattr(block, "text", None)
                if value:
                    texts.append(value.strip())

    if ResultMessage and isinstance(msg, ResultMessage):
        result_text = getattr(msg, "result", None)
        if result_text:
            texts.append(str(result_text).strip())

    if texts:
        return "\n\n".join(t for t in texts if t)

    # Structured extraction failed; attempt regex fallback
    return _parse_text_block(str(msg))


def _summarize_tool_io(payload) -> str:
    try:
        return json.dumps(payload, indent=2, ensure_ascii=False)
    except TypeError:
        return str(payload)


def _format_trace_message(msg) -> Optional[str]:
    if StreamEvent and isinstance(msg, StreamEvent):
        event = getattr(msg, "event", {}) or {}
        event_type = event.get("type", "stream_event")
        # Keep stream events compact in the trace
        return f"[stream:{event_type}]\n{_summarize_tool_io(event)}"

    if AssistantMessage and isinstance(msg, AssistantMessage):
        chunks = []
        for block in getattr(msg, 'content', []):
            if ThinkingBlock and isinstance(block, ThinkingBlock):
                thinking = getattr(block, 'thinking', '')
                if thinking:
                    chunks.append(f"[thinking]\n{thinking.strip()}")
            elif ToolUseBlock and isinstance(block, ToolUseBlock):
                name = getattr(block, 'name', 'tool')
                input_data = getattr(block, 'input', {})
                chunks.append(f"[tool-use:{name}]\n{_summarize_tool_io(input_data)}")
            elif ToolResultBlock and isinstance(block, ToolResultBlock):
                tool_id = getattr(block, 'tool_use_id', 'result')
                content = getattr(block, 'content', '')
                chunks.append(f"[tool-result:{tool_id}]\n{_summarize_tool_io(content)}")
            elif TextBlock and isinstance(block, TextBlock):
                text = getattr(block, 'text', '')
                if text:
                    chunks.append(f"[assistant]\n{text.strip()}")
        return "\n\n".join([chunk for chunk in chunks if chunk])

    if SystemMessage and isinstance(msg, SystemMessage):
        subtype = getattr(msg, 'subtype', 'system')
        data = getattr(msg, 'data', {})
        return f"[system:{subtype}]\n{_summarize_tool_io(data)}"

    if ResultMessage and isinstance(msg, ResultMessage):
        result_text = getattr(msg, 'result', None)
        if result_text:
            return f"[result]\n{result_text.strip()}"
        return f"[result]\n{_summarize_tool_io(msg.__dict__)}"

    return str(msg).strip()


async def _run_code_chat_stream(
    question: str,
    code_dir: Path,
    model: Optional[str],
    max_turns: int,
) -> Tuple[str, Optional[str]]:
    if query is None or ClaudeAgentOptions is None:
        raise RuntimeError("claude-agent-sdk not installed. Install with: pip install claude-agent-sdk")

    _warn_missing_cli()

    options = ClaudeAgentOptions(
        max_turns=max_turns,
        max_buffer_size=int(os.getenv("CLAUDE_CODE_MAX_BUFFER_SIZE", str(50 * 1024 * 1024))),
        system_prompt=f"You are Claude Code. Answer user questions about the codebase rooted at {code_dir}. "
        f"Keep answers concise and cite file paths when helpful.",
        cwd=code_dir,
        allowed_tools=["Read", "Bash", "Grep", "Glob"],  # no write access per requirements
        permission_mode="acceptEdits",
        model=model,
    )

    final_answer: Optional[str] = None
    trace_chunks: list[str] = []
    stream_text_parts: list[str] = []

    async for msg in query(prompt=question, options=options):
        trace_chunk = _format_trace_message(msg)
        if trace_chunk:
            trace_chunks.append(trace_chunk)

        if StreamEvent and isinstance(msg, StreamEvent):
            event = getattr(msg, "event", {}) or {}
            event_type = event.get("type")
            # Capture streamed text deltas so we can return the final assembled message.
            if event_type == "content_block_delta":
                delta = event.get("delta", {}) or {}
                if delta.get("type") == "text_delta":
                    text_delta = delta.get("text", "")
                    if text_delta:
                        stream_text_parts.append(text_delta)
                        final_answer = "".join(stream_text_parts)
            elif event_type == "content_block_stop":
                content_block = event.get("content_block", {}) or {}
                text = content_block.get("text")
                if text:
                    stream_text_parts.append(text)
                    final_answer = "".join(stream_text_parts)
            elif event_type == "message_stop" and stream_text_parts and not final_answer:
                final_answer = "".join(stream_text_parts)
            continue

        text = _extract_message_text(msg)
        if text:
            final_answer = text

    trace_output = "\n\n".join(trace_chunks) if trace_chunks else None

    if not final_answer and stream_text_parts:
        final_answer = "".join(stream_text_parts)

    if final_answer:
        return final_answer.strip(), trace_output
    return "No answer received from Claude Code.", trace_output


def run_code_chat(
    question: str,
    code_dir: Path,
    model: Optional[str] = None,
    max_turns: int = 6,
    include_trace: bool = False,
) -> Union[str, dict]:
    """
    Run a single code Q&A turn against Claude Code mounted at code_dir.

    Returns the final assistant text response by default. When include_trace=True,
    returns a dict with keys:
      - 'answer': final assistant text
      - 'thinking': raw trace of thinking/tool messages (may be None)
    """
    code_dir = Path(code_dir).expanduser().resolve()
    if not code_dir.exists() or not code_dir.is_dir():
        raise ValueError(f"code_dir does not exist or is not a directory: {code_dir}")

    try:
        answer, trace = asyncio.run(
            _run_code_chat_stream(
                question=question,
                code_dir=code_dir,
                model=model,
                max_turns=max_turns,
            )
        )
        if include_trace:
            return {
                'answer': answer,
                'thinking': trace
            }
        return answer
    except KeyboardInterrupt:
        return {
            'answer': "Interrupted.",
            'thinking': None
        } if include_trace else "Interrupted."
    except Exception as e:
        error_text = f"Error running code chat: {e}"
        return {
            'answer': error_text,
            'thinking': None
        } if include_trace else error_text
