#!/usr/bin/env python3
"""
CLI helper for debugging Claude Code chat streaming.

Usage:
    python3 tests/code_chat.py "What does the code chat helper return?"
    python3 tests/code_chat.py "Question" --code-dir /path/to/repo --max-turns 4
"""

import argparse
import asyncio
import sys
from pathlib import Path

import ipdb


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from AIR.utils import code_chat


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Debug Claude Code chat streaming output from the CLI.")
    parser.add_argument("question", help="Question to ask the Claude Code agent.")
    parser.add_argument(
        "--code-dir",
        default=REPO_ROOT,
        help="Directory to mount for Claude Code (defaults to repository root).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Optional model override passed through to Claude Code.",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="Maximum number of turns for the Claude Code agent.",
    )
    parser.add_argument(
        "--multi-agent",
        action="store_true",
        help="Enable orchestrator + code-reviewer + checkpoint-reviewer subagents.",
    )
    return parser.parse_args()


async def capture_stream(
    question: str,
    code_dir: Path,
    model: str | None,
    max_turns: int,
    multi_agent: bool,
) -> dict:
    """
    Run a streaming Claude Code chat turn and capture raw messages plus assembled text.
    Returns a dict with keys:
      - final_answer: str
      - stream_text_parts: list[str]
      - trace_chunks: list[str]
      - stream_events: list[dict]
      - messages: list[Any]
    """
    if code_chat.query is None or code_chat.ClaudeAgentOptions is None:
        # Give a clearer error when the SDK import failed (often due to API changes)
        try:
            import claude_agent_sdk as sdk  # type: ignore
            version = getattr(sdk, "__version__", "unknown")
            missing = []
            for attr in ["query", "ClaudeAgentOptions", "StreamEvent"]:
                if not hasattr(sdk, attr):
                    missing.append(attr)
            detail = f"Installed claude-agent-sdk version: {version}. Missing symbols: {', '.join(missing) or 'none'}."
        except Exception as e:  # pragma: no cover - best effort diagnostic
            detail = f"Failed to import claude-agent-sdk: {e}"
        raise RuntimeError(
            "claude-agent-sdk import failed in AIR.utils.code_chat. "
            "Verify SDK version and API compatibility. " + detail
        )

    effective_multi_agent = multi_agent and code_chat._is_multiagent_code_chat_enabled(code_dir)
    claude_md_context = "None available"
    claude_md_path = code_dir / "CLAUDE.md"
    if claude_md_path.exists():
        try:
            text = claude_md_path.read_text(encoding="utf-8", errors="ignore").strip()
            if text:
                claude_md_context = text
        except Exception:
            claude_md_context = "None available"

    system_prompt = (
        code_chat.MULTI_AGENT_ORCHESTRATOR_PROMPT.format(
            working_dir=code_dir,
            report_prompt="",
        )
        if effective_multi_agent
        else f"You are Claude Code. Answer user questions about the codebase rooted at {code_dir}. "
        f"Keep answers concise and cite file paths when helpful."
    )
    allowed_tools = ["Read", "Bash", "Grep", "Glob"]
    options_kwargs = {}
    if effective_multi_agent:
        allowed_tools.append("Task")
        options_kwargs["agents"] = code_chat._build_multi_agent_definitions(
            working_dir=code_dir,
            claude_md_context=claude_md_context,
            report_prompt="",
        )

    options = code_chat.ClaudeAgentOptions(
        max_turns=max_turns,
        system_prompt=system_prompt,
        cwd=code_dir,
        allowed_tools=allowed_tools,
        permission_mode="acceptEdits",
        model=model,
        **options_kwargs,
    )

    final_answer = None
    trace_chunks: list[str] = []
    stream_text_parts: list[str] = []
    stream_events: list[dict] = []
    messages: list = []

    async for msg in code_chat.query(prompt=question, options=options):
        messages.append(msg)

        formatted = code_chat._format_trace_message(msg)
        if formatted:
            trace_chunks.append(formatted)

        if code_chat.StreamEvent and isinstance(msg, code_chat.StreamEvent):
            event = getattr(msg, "event", {}) or {}
            stream_events.append(event)
            event_type = event.get("type")
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

        text = code_chat._extract_message_text(msg)
        if text:
            final_answer = text

    if not final_answer and stream_text_parts:
        final_answer = "".join(stream_text_parts)

    return {
        "final_answer": final_answer,
        "stream_text_parts": stream_text_parts,
        "trace_chunks": trace_chunks,
        "stream_events": stream_events,
        "messages": messages,
    }


def main():
    args = parse_args()
    code_dir = Path(args.code_dir).expanduser().resolve()
    if not code_dir.exists():
        raise SystemExit(f"code_dir does not exist: {code_dir}")

    debug_state = asyncio.run(
        capture_stream(
            question=args.question,
            code_dir=code_dir,
            model=args.model,
            max_turns=args.max_turns,
            multi_agent=args.multi_agent,
        )
    )

    print("\nFinal answer from Claude Code:\n")
    print(debug_state["final_answer"])
    print("\nDropping into ipdb. Inspect `debug_state` for streaming outputs and raw messages.\n")
    ipdb.set_trace()


if __name__ == "__main__":
    main()
