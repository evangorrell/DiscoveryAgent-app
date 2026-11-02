import asyncio
import json
import os, re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional, Tuple, Union

DEFAULT_CLAUDE_AGENT_SDK_TIMEOUT_MS = 15 * 60 * 1000


def _configure_claude_sdk_env() -> None:
    # The Claude Agent SDK does not require CLI path configuration.
    os.environ.setdefault("MAX_MCP_OUTPUT_TOKENS", "50000")

    raw_timeout = (
        os.getenv("CLAUDE_AGENT_SDK_TIMEOUT_MS")
        or os.getenv("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT")
    )
    try:
        timeout_ms = int(raw_timeout) if raw_timeout else DEFAULT_CLAUDE_AGENT_SDK_TIMEOUT_MS
    except (TypeError, ValueError):
        timeout_ms = DEFAULT_CLAUDE_AGENT_SDK_TIMEOUT_MS

    # The SDK reads this env var in the parent Python process for initialize and stream-close
    # control waits, so set it here before any SDK sessions are created.
    os.environ["CLAUDE_CODE_STREAM_CLOSE_TIMEOUT"] = str(max(timeout_ms, 60_000))


_configure_claude_sdk_env()


try:
    import claude_agent_sdk as _claude_sdk  # type: ignore

    # Some SDK versions omit newer symbols.
    query = getattr(_claude_sdk, "query", None)
    ClaudeAgentOptions = getattr(_claude_sdk, "ClaudeAgentOptions", None)
    AgentDefinition = getattr(_claude_sdk, "AgentDefinition", None)
    AssistantMessage = getattr(_claude_sdk, "AssistantMessage", None)
    ResultMessage = getattr(_claude_sdk, "ResultMessage", None)
    SystemMessage = getattr(_claude_sdk, "SystemMessage", None)
    StreamEvent = getattr(_claude_sdk, "StreamEvent", None)
    TextBlock = getattr(_claude_sdk, "TextBlock", None)
    ThinkingBlock = getattr(_claude_sdk, "ThinkingBlock", None)
    ToolUseBlock = getattr(_claude_sdk, "ToolUseBlock", None)
    ToolResultBlock = getattr(_claude_sdk, "ToolResultBlock", None)
except Exception:
    # Defer import error until call time so callers can handle gracefully
    query = None
    ClaudeAgentOptions = None
    AgentDefinition = None
    AssistantMessage = None
    ResultMessage = None
    SystemMessage = None
    StreamEvent = None
    TextBlock = None
    ThinkingBlock = None
    ToolUseBlock = None
    ToolResultBlock = None


old_CODE_CHAT_SYSTEM_PROMPT = (
    "You are Claude Code. Answer user questions about the codebase rooted at {working_dir}. "
    "Keep answers concise and cite file paths when helpful."
)

CODE_CHAT_SYSTEM_PROMPT = """
You are an audit agent that is responsible for intaking a complex research project trace and codebase performed by an AI agent.
You are to answer questions from a human expert, who you can assume is a domain expert in this scientific field, in order to help them better understand the research process, including background, methodology, results, and interpretations.
Your job is to be objective and helpful, providing clear but precise answers to what was done in the research process.
DO NOT inject your own opinions or interpretations into the answers, simply use the presented research trace and codebase to objectively answer questions.
It is of upmost importance that you answer questions correctly as your responses will guide future research decisions by the human expert.

The research trace is provided to you in the form of multiple "checkpoint" files. These are given in structured rounds and queries; queries are parts of a round.
The checkpoint structure can be described as such:

- {working_dir}/checkpoint/checkpoints: the checkpoints directory with all information about the research trace so far.
    - {working_dir}/checkpoint/checkpoints/round_N/: This is the base folder for a given round N
        - {working_dir}/checkpoint/checkpoints/round_N/query_N/: This is the base folder for a given query N. There will be multiple query folders in the round directory. It contains a file query_N.md that describes the query at that step. Each N denotes the order in which they were called; intuitively, 001 comes before 002, etc.
        - {working_dir}/checkpoint/checkpoints/round_N/memory_report.md: This is a full report of the research up until this point.
        - {working_dir}/checkpoint/checkpoints/round_N/round_report.md: This is a report of the research that was done between round (N-1) and round (N), i.e., the report of the research done in round N alone.

The codebase is provided in {working_dir}/code/. It contains all code written by the agent thus far during the research.
Here's a file describing the codebase (from CLAUDE.md in the codebase):
{claude_md_context}

The DOCUMENTATION.md file in the codebase contains more information. It's recommended to use grep to access this file as it's very large.
{report_prompt}
Use both the research trace and codebase to answer user questions. In your answers, cite specific implementations or files from the codebase when helpful. Refer to rounds in which research was done, including the round number such as "In round N, the agent found...".

Format responses as clean GitHub-flavored markdown. Use markdown headings, bullets, numbered lists, and markdown tables when structure helps. Do not use ASCII-art boxes, ASCII tables, or monospaced layouts made with characters like +---, |---|, or repeated dashes; those are hard to render in the web UI.

If a figure file (PNG, JPG, JPEG, GIF, SVG, or WebP — not PDF) exists on disk and would directly support your answer, include it using markdown image syntax with the absolute path: ![brief description](/absolute/path/to/figure.png). Only include figures that are genuinely relevant. Do not include figures that do not exist on disk.
"""

COMPACTION_SYSTEM_PROMPT = """
You are a compaction engine. Your task is to compress a list of USER messages into a single compact USER message.
Only summarize the USER messages. Do not include any assistant responses or system prompts.
Preserve concrete requests, constraints, definitions, decisions, names, file paths, and unresolved questions.
Be concise but retain critical details needed to continue the conversation, including research-critical points.
Emphasize details the user may have added or clarifications that are important to understanding the research.
Output ONLY the compacted user message, with no preamble or formatting.
""".strip()

MULTI_AGENT_ORCHESTRATOR_PROMPT = """
You are the orchestrator for a lightweight multi-agent review system.

Available specialized agents:
- code-reviewer: reads and reviews the codebase under {working_dir}/code/
- checkpoint-reviewer: reads and reviews research checkpoints under {working_dir}/checkpoint/checkpoints/

{report_prompt}

Routing policy:
- Delegate code implementation, architecture, bug-risk, and quality questions to code-reviewer.
- Delegate timeline, experiment/progress, round/query history, and checkpoint trace questions to checkpoint-reviewer.
- For mixed questions, call both agents and synthesize a single concise answer.
- When the user question can be decomposed into independent code-review and checkpoint-review work, launch both subagent tasks immediately and in parallel when possible, then synthesize once both results are available.
- Do not serialize independent subagent work if both investigations can proceed concurrently without depending on each other.

Response policy:
- Cite concrete file paths and, when relevant, round/checkpoint identifiers.
- Stay objective and avoid unsupported speculation.
- Keep the final response concise and directly answer the user question.
- Format the final answer as clean GitHub-flavored markdown. Use markdown tables instead of ASCII-art tables, and do not draw boxes or diagrams with monospaced characters like +--- or repeated dashes.
- If a figure file (PNG, JPG, JPEG, GIF, SVG, or WebP — not PDF) returned by a sub-agent exists on disk and directly supports the answer, include it with markdown image syntax using the absolute path: ![brief description](/absolute/path/to/figure.png). Only include figures that genuinely exist and are relevant.
""".strip()

CODE_REVIEW_AGENT_PROMPT = """
You are a focused code review subagent.
You can only analyze the codebase under {working_dir}/code/.

Here's a file describing the codebase (from CLAUDE.md in the codebase):
{claude_md_context}

Instructions:
- Prioritize correctness, quality, architecture, and implementation details.
- Reference exact file paths in your findings.
- If the question requires checkpoint history, state that checkpoint-reviewer should handle that part.
- Do not modify files.
- Access files in a limited manner - use the CLAUDE.md file and DOCUMENTATION.md file (which should be grepped, not read into context) as much as possible.
""".strip()

CHECKPOINT_REVIEW_AGENT_PROMPT = """
You are a focused checkpoint review subagent.
You can only analyze checkpoint artifacts under {working_dir}/checkpoint/checkpoints/.

The research trace is provided to you in the form of multiple "checkpoint" files. These are given in structured rounds and queries; queries are parts of a round.
The checkpoint structure can be described as such:

- {working_dir}/checkpoint/checkpoints: the checkpoints directory with all information about the research trace so far.
    - {working_dir}/checkpoint/checkpoints/round_N/: This is the base folder for a given round N
        - {working_dir}/checkpoint/checkpoints/round_N/query_N/: This is the base folder for a given query N. There will be multiple query folders in the round directory. It contains a file query_N.md that describes the query at that step. Each N denotes the order in which they were called; intuitively, 001 comes before 002, etc.
        - {working_dir}/checkpoint/checkpoints/round_N/memory_report.md: This is a full report of the research up until this point.
        - {working_dir}/checkpoint/checkpoints/round_N/round_report.md: This is a report of the research that was done between round (N-1) and round (N), i.e., the report of the research done in round N alone.

Instructions:
- Prioritize round/query chronology, experiment trace, and report evidence.
- Reference exact checkpoint paths and round/query identifiers.
- If the question requires code implementation details, state that code-reviewer should handle that part.
- Do not modify files.
- Access checkpoints in a limited way - prioritize reading the round_report's instead of individual query files.

{report_prompt}
""".strip()


@dataclass
class CodeChatCompactionConfig:
    user_token_threshold: int = 150000
    keep_last_user_messages: int = 1
    compaction_model: Optional[str] = None


@dataclass
class CodeChatConversation:
    messages: list[dict] = field(default_factory=list)
    sdk_session_id: Optional[str] = None

    def add_user_message(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str) -> None:
        self.messages.append({"role": "assistant", "content": content})

    def estimate_user_tokens(self) -> int:
        return sum(_estimate_tokens_for_text(m.get("content", "")) for m in self.messages if m.get("role") == "user")

    def compact_user_messages(self, summary_text: str, keep_last_n: int = 1) -> bool:
        if not summary_text:
            return False
        user_indices = [i for i, m in enumerate(self.messages) if m.get("role") == "user"]
        if not user_indices:
            return False
        keep_set = set(user_indices[-keep_last_n:]) if keep_last_n > 0 else set()
        summary_inserted = False
        new_messages = []
        for idx, msg in enumerate(self.messages):
            role = msg.get("role")
            if role != "user":
                new_messages.append(msg)
                continue
            if idx in keep_set:
                new_messages.append(msg)
                continue
            if not summary_inserted:
                new_messages.append({"role": "user", "content": summary_text})
                summary_inserted = True
        self.messages = new_messages
        return True


class CodeChatCancelledError(Exception):
    """Raised when a code chat request is cancelled by the caller."""


class CodeChatProviderError(RuntimeError):
    """A terminal SDK failure, not model-authored answer text."""


def _safe_provider_error(message: str) -> str:
    """Classify without echoing provider payloads, keys, or scientific context."""
    value = message.lower()
    for terms, public in (
        (("credit", "balance", "billing"), "The provider reports insufficient credits or a billing problem. Check your account and retry."),
        (("workspace", "organization", "account"), "The provider rejected the account/workspace configuration. Check your provider account and retry."),
        (("auth", "api key", "api_key", "401", "403"), "Provider authentication failed. Check your API key and permissions, then retry."),
        (("model",), "The requested model is unavailable or not permitted for this account. Choose an available model and retry."),
        (("rate", "429", "overload"), "The provider is rate limited or busy. Wait briefly and retry."),
        (("timeout", "timed out"), "The provider request timed out. Try a narrower question and retry."),
        (("max_turns", "maximum number of turns"), "Code agent reached maximum number of turns. Narrow the question and retry."),
    ):
        if any(term in value for term in terms):
            return public
    return "The provider could not complete this request. Check your account and model availability, then retry."


def _estimate_tokens_for_text(text: str) -> int:
    # Rough heuristic: ~4 chars per token
    if not text:
        return 0
    return max(1, int(len(text) / 4))


def _serialize_conversation(messages: list[dict]) -> str:
    parts = []
    for msg in messages:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        label = "User" if role == "user" else "Assistant"
        parts.append(f"{label}: {content}")
    return "\n\n".join(parts)


CLAUDE_CONTEXT_BETAS = []


def _claude_options_supports_betas() -> bool:
    if ClaudeAgentOptions is None:
        return False
    try:
        import inspect

        sig = inspect.signature(ClaudeAgentOptions)
        return "betas" in sig.parameters
    except Exception:
        return False


def _build_claude_options_kwargs() -> dict:
    kwargs = {}
    if CLAUDE_CONTEXT_BETAS and _claude_options_supports_betas():
        kwargs["betas"] = CLAUDE_CONTEXT_BETAS
    return kwargs


def _agent_definition_supports_field(*field_names: str) -> bool:
    if AgentDefinition is None:
        return False
    try:
        import inspect

        sig = inspect.signature(AgentDefinition)
        return any(name in sig.parameters for name in field_names)
    except Exception:
        return False


def _build_multi_agent_definitions(working_dir: Path, claude_md_context: str, report_prompt: str) -> dict:
    def _agent_definition(
        description: str,
        prompt: str,
        tools: list[str],
        *,
        enable_background: bool = False,
    ) -> object:
        extra_kwargs = {}
        # Newer Claude Code SDKs may support background-capable subagents.
        if enable_background and _agent_definition_supports_field("background"):
            extra_kwargs["background"] = True
        if _agent_definition_supports_field("max_turns"):
            extra_kwargs["max_turns"] = 4
        elif _agent_definition_supports_field("maxTurns"):
            extra_kwargs["maxTurns"] = 4
        if AgentDefinition is not None:
            return AgentDefinition(description=description, prompt=prompt, tools=tools, **extra_kwargs)
        payload = {
            "description": description,
            "prompt": prompt,
            "tools": tools,
        }
        payload.update(extra_kwargs)
        return payload

    return {
        "code-reviewer": _agent_definition(
            description="Codebase review specialist. Use for implementation, architecture, quality, and bug-risk analysis.",
            prompt=CODE_REVIEW_AGENT_PROMPT.format(
                working_dir=working_dir,
                claude_md_context=claude_md_context,
            ),
            tools=["Read", "Grep", "Glob"],
            enable_background=True,
        ),
        "checkpoint-reviewer": _agent_definition(
            description="Checkpoint trace specialist. Use for rounds/queries progress, timeline, and report evidence analysis.",
            prompt=CHECKPOINT_REVIEW_AGENT_PROMPT.format(
                working_dir=working_dir,
                report_prompt=report_prompt,
            ),
            tools=["Read", "Grep", "Glob"],
            enable_background=True,
        ),
    }


@lru_cache(maxsize=64)
def _read_claude_md_context_cached(path_str: str, mtime_ns: int) -> Optional[str]:
    path = Path(path_str)
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return None
    return text or None


def _load_claude_md_context(working_dir: Path) -> str:
    claude_md_path = working_dir / "CLAUDE.md"
    if not claude_md_path.exists():
        return "None available"
    try:
        stat = claude_md_path.stat()
    except Exception:
        return "None available"
    cached = _read_claude_md_context_cached(str(claude_md_path), int(stat.st_mtime_ns))
    return cached if cached else "None available"


def _read_env_value_from_dotenv(dotenv_path: Path, key: str) -> Optional[str]:
    if not dotenv_path.exists() or not dotenv_path.is_file():
        return None
    try:
        for raw_line in dotenv_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            lhs, rhs = line.split("=", 1)
            if lhs.strip() != key:
                continue
            value = rhs.strip().strip('"').strip("'")
            if "#" in value:
                value = value.split("#", 1)[0].strip()
            return value
    except Exception:
        return None
    return None


def _is_multiagent_code_chat_enabled(working_dir: Path) -> bool:
    env_value = os.getenv("MULTIAGENT_CODE_CHAT")
    if env_value is None:
        env_value = _read_env_value_from_dotenv(working_dir / ".env", "MULTIAGENT_CODE_CHAT")
    return (env_value or "").strip() == "1"


def _extract_json_error_message(payload: object) -> Optional[str]:
    if isinstance(payload, dict):
        error_obj = payload.get("error")
        if isinstance(error_obj, dict):
            message = error_obj.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        if isinstance(error_obj, str) and error_obj.strip():
            return error_obj.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return None


def _extract_anthropic_error_message(err: Exception) -> Optional[str]:
    # Try common anthropic/httpx exception shapes first.
    response = getattr(err, "response", None)
    if response is not None:
        json_method = getattr(response, "json", None)
        if callable(json_method):
            try:
                data = json_method()
                message = _extract_json_error_message(data)
                if message:
                    return message
            except Exception:
                pass
        text = getattr(response, "text", None)
        if isinstance(text, str) and text.strip():
            try:
                data = json.loads(text)
                message = _extract_json_error_message(data)
                if message:
                    return message
            except Exception:
                pass
        content = getattr(response, "content", None)
        if isinstance(content, (bytes, str)) and content:
            try:
                raw = content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else content
                data = json.loads(raw)
                message = _extract_json_error_message(data)
                if message:
                    return message
            except Exception:
                pass

    # Fallback: some SDKs attach a body or error dict.
    body = getattr(err, "body", None)
    if body is not None:
        message = _extract_json_error_message(body)
        if message:
            return message

    error = getattr(err, "error", None)
    if error is not None:
        message = _extract_json_error_message(error)
        if message:
            return message

    return None


def _is_timeout_like_error_message(message: Optional[str]) -> bool:
    if not message:
        return False
    lowered = message.lower()
    timeout_signals = (
        "timeout",
        "timed out",
        "deadline exceeded",
        "stream close timeout",
        "read timeout",
        "connect timeout",
    )
    return any(signal in lowered for signal in timeout_signals)


async def _run_user_compaction(
    user_messages_text: str,
    working_dir: Path,
    model: Optional[str],
) -> Optional[str]:
    if query is None or ClaudeAgentOptions is None:
        raise RuntimeError("claude-agent-sdk not installed. Install with: pip install claude-agent-sdk")

    options = ClaudeAgentOptions(
        max_turns=1,
        system_prompt=COMPACTION_SYSTEM_PROMPT,
        cwd=working_dir,
        allowed_tools=[],
        permission_mode="dontAsk",
        model=model,
        **_build_claude_options_kwargs(),
        # Prevent SDK JSON buffer overflow for large tool outputs.
        max_buffer_size=50 * 1024 * 1024,
    )

    final_answer: Optional[str] = None
    stream_text_parts: list[str] = []

    async for msg in query(prompt=user_messages_text, options=options):
        event_payload = None
        if StreamEvent and isinstance(msg, StreamEvent):
            event_payload = getattr(msg, "event", {}) or {}
        elif hasattr(msg, "event"):
            event_payload = getattr(msg, "event", {}) or {}
        elif isinstance(msg, dict) and "event" in msg:
            event_payload = msg.get("event", {}) or {}

        if event_payload is not None:
            event_type = event_payload.get("type")
            if event_type == "content_block_delta":
                delta = event_payload.get("delta", {}) or {}
                if delta.get("type") == "text_delta":
                    text_delta = delta.get("text", "")
                    if text_delta:
                        stream_text_parts.append(text_delta)
                        final_answer = "".join(stream_text_parts)
            elif event_type == "content_block_stop":
                content_block = event_payload.get("content_block", {}) or {}
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

    if not final_answer and stream_text_parts:
        final_answer = "".join(stream_text_parts)

    return final_answer.strip() if final_answer else None


def _maybe_compact_conversation(
    conversation: CodeChatConversation,
    working_dir: Path,
    model: Optional[str],
    config: CodeChatCompactionConfig,
) -> bool:
    if conversation.estimate_user_tokens() < config.user_token_threshold:
        return False

    user_messages_text = _serialize_conversation([m for m in conversation.messages if m.get("role") == "user"])
    if not user_messages_text:
        return False

    summary = asyncio.run(
        _run_user_compaction(
            user_messages_text=user_messages_text,
            working_dir=working_dir,
            model=config.compaction_model or model,
        )
    )
    if not summary:
        return False

    summary_text = "[Compacted user context]\n" + summary.strip()
    return conversation.compact_user_messages(summary_text, keep_last_n=config.keep_last_user_messages)


def _parse_text_block(msg_str: str) -> Optional[str]:
    """Extract assistant text blocks from a streamed message string."""
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
    if (StreamEvent and isinstance(msg, StreamEvent)) or hasattr(msg, "event") or (isinstance(msg, dict) and "event" in msg):
        event = getattr(msg, "event", {}) if hasattr(msg, "event") else msg.get("event", {}) if isinstance(msg, dict) else {}
        event = event or {}
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
    working_dir: Path,
    model: Optional[str],
    max_turns: int,
    report_prompt: Optional[str] = None,
    multi_agent: bool = False,
    cancel_requested: Optional[Callable[[], bool]] = None,
    cancel_poll_interval_s: float = 0.25,
) -> Tuple[str, Optional[str], Optional[str]]:
    if query is None or ClaudeAgentOptions is None:
        raise RuntimeError("claude-agent-sdk not installed. Install with: pip install claude-agent-sdk")

    claude_md_context = _load_claude_md_context(working_dir)
    report_prompt = report_prompt if report_prompt else ""

    system_prompt = (
        MULTI_AGENT_ORCHESTRATOR_PROMPT.format(
            working_dir=working_dir,
            report_prompt=report_prompt,
        )
        if multi_agent
        else CODE_CHAT_SYSTEM_PROMPT.format(
            working_dir=working_dir,
            claude_md_context=claude_md_context,
            report_prompt=report_prompt,
        )
    )

    allowed_tools = ["Read", "Bash", "Grep", "Glob"]
    options_kwargs = _build_claude_options_kwargs()
    if multi_agent:
        allowed_tools.append("Task")
        options_kwargs["agents"] = _build_multi_agent_definitions(
            working_dir=working_dir,
            claude_md_context=claude_md_context,
            report_prompt=report_prompt,
        )

    options = ClaudeAgentOptions(
        max_turns=max_turns,
        system_prompt=system_prompt,
        cwd=working_dir,
        allowed_tools=allowed_tools,  # include Bash for read-only commands like ls
        permission_mode="dontAsk",  # allow read-only tools without interactive prompts
        model=model,
        **options_kwargs,
        # Prevent SDK JSON buffer overflow for large tool outputs.
        max_buffer_size=50 * 1024 * 1024,
    )

    final_answer: Optional[str] = None
    trace_chunks: list[str] = []
    stream_text_parts: list[str] = []
    active_session_id: Optional[str] = None

    def _cancelled() -> bool:
        if not cancel_requested:
            return False
        try:
            return bool(cancel_requested())
        except Exception:
            return False

    stream = query(prompt=question, options=options)
    iterator = stream.__aiter__() if hasattr(stream, "__aiter__") else stream
    message_queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
    queue_poll_timeout = max(0.05, cancel_poll_interval_s)
    stream_reader_task: Optional[asyncio.Task] = None

    async def _read_stream_into_queue() -> None:
        try:
            while True:
                try:
                    msg = await iterator.__anext__()
                except StopAsyncIteration:
                    break
                await message_queue.put(("message", msg))
        except Exception as exc:
            await message_queue.put(("error", exc))
        finally:
            try:
                await stream.aclose()
            except Exception:
                pass
            await message_queue.put(("done", None))

    stream_reader_task = asyncio.create_task(_read_stream_into_queue())
    try:
        while True:
            if _cancelled():
                if stream_reader_task is not None:
                    stream_reader_task.cancel()
                    try:
                        await asyncio.wait_for(stream_reader_task, timeout=1.0)
                    except BaseException:
                        pass
                    stream_reader_task = None
                raise CodeChatCancelledError("Code chat request cancelled.")

            try:
                item_type, payload = await asyncio.wait_for(
                    message_queue.get(),
                    timeout=queue_poll_timeout,
                )
            except asyncio.TimeoutError:
                continue

            if item_type == "done":
                break

            if item_type == "error":
                if isinstance(payload, BaseException):
                    raise payload
                raise RuntimeError(f"Unexpected stream reader error payload: {payload!r}")

            if item_type != "message":
                continue

            msg = payload

            if _cancelled():
                raise CodeChatCancelledError("Code chat request cancelled.")

            session_id = getattr(msg, "session_id", None)
            if isinstance(session_id, str) and session_id.strip():
                active_session_id = session_id.strip()

            trace_chunk = _format_trace_message(msg)
            if trace_chunk:
                trace_chunks.append(trace_chunk)

            event_payload = None
            if StreamEvent and isinstance(msg, StreamEvent):
                event_payload = getattr(msg, "event", {}) or {}
            elif hasattr(msg, "event"):
                event_payload = getattr(msg, "event", {}) or {}
            elif isinstance(msg, dict) and "event" in msg:
                event_payload = msg.get("event", {}) or {}

            if event_payload is not None:
                event_type = event_payload.get("type")
                # Capture streamed text deltas so we can return the final assembled message.
                if event_type == "content_block_delta":
                    delta = event_payload.get("delta", {}) or {}
                    if delta.get("type") == "text_delta":
                        text_delta = delta.get("text", "")
                        if text_delta:
                            stream_text_parts.append(text_delta)
                            final_answer = "".join(stream_text_parts)
                elif event_type == "content_block_stop":
                    content_block = event_payload.get("content_block", {}) or {}
                    text = content_block.get("text")
                    if text:
                        stream_text_parts.append(text)
                        final_answer = "".join(stream_text_parts)
                elif event_type == "message_stop" and stream_text_parts and not final_answer:
                    final_answer = "".join(stream_text_parts)
                continue

            if ResultMessage and isinstance(msg, ResultMessage):
                subtype = getattr(msg, "subtype", None)
                if getattr(msg, "is_error", False) or str(subtype or '').startswith('error'):
                    raise CodeChatProviderError(str(getattr(msg, 'result', None) or subtype))

            if AssistantMessage and isinstance(msg, AssistantMessage) and getattr(msg, 'error', None):
                raise CodeChatProviderError(_extract_message_text(msg) or str(msg.error))
            text = _extract_message_text(msg)
            if text:
                final_answer = text
    finally:
        if stream_reader_task is not None and not stream_reader_task.done():
            stream_reader_task.cancel()
            try:
                await asyncio.wait_for(stream_reader_task, timeout=1.0)
            except BaseException:
                pass

    trace_output = "\n\n".join(trace_chunks) if trace_chunks else None

    if not final_answer and stream_text_parts:
        final_answer = "".join(stream_text_parts)

    if final_answer:
        return final_answer.strip(), trace_output, active_session_id
    return "No answer received from Claude Code.", trace_output, active_session_id


def run_code_chat(
    question: str,
    working_dir: Path,
    model: Optional[str] = None,
    max_turns: int = 100,
    include_trace: bool = False,
    report_prompt: Optional[str] = None,
    multi_agent: bool = False,
    conversation: Optional[CodeChatConversation] = None,
    compaction_config: Optional[CodeChatCompactionConfig] = None,
    cancel_requested: Optional[Callable[[], bool]] = None,
) -> Union[str, dict]:
    """
    Run a single code Q&A turn against Claude Code mounted at working_dir.

    Returns the final assistant text response by default. When include_trace=True,
    returns a dict with keys:
      - 'answer': final assistant text
      - 'thinking': raw trace of thinking/tool messages (may be None)
    """
    working_dir = Path(working_dir).expanduser().resolve()
    if not working_dir.exists() or not working_dir.is_dir():
        raise ValueError(f"working_dir does not exist or is not a directory: {working_dir}")

    conversation_snapshot = [dict(m) for m in conversation.messages] if conversation is not None else None
    conversation_session_snapshot = conversation.sdk_session_id if conversation is not None else None
    try:
        effective_multi_agent = multi_agent and _is_multiagent_code_chat_enabled(working_dir)
        prompt_text = question
        if conversation is not None:
            conversation.add_user_message(question)
            # The installed SDK documents query() as stateless; serialize turns
            # explicitly instead of relying on CLI resume semantics for context.
            if compaction_config is not None:
                _maybe_compact_conversation(
                    conversation=conversation,
                    working_dir=working_dir,
                    model=model,
                    config=compaction_config,
                )
            serialized = _serialize_conversation(conversation.messages)
            prompt_text = (
                "Conversation so far:\n"
                f"{serialized}\n\n"
                "Please answer the most recent user message above."
            )

        answer, trace, session_id = asyncio.run(
            _run_code_chat_stream(
                question=prompt_text,
                working_dir=working_dir,
                model=model,
                max_turns=max_turns,
                report_prompt=report_prompt,
                multi_agent=effective_multi_agent,
                cancel_requested=cancel_requested,
            )
        )
        if conversation is not None:
            if session_id:
                conversation.sdk_session_id = session_id
            conversation.add_assistant_message(answer)
        if include_trace:
            return {
                'answer': answer,
                'thinking': trace
            }
        return answer
    except CodeChatCancelledError:
        if conversation is not None and conversation_snapshot is not None:
            conversation.messages = conversation_snapshot
            conversation.sdk_session_id = conversation_session_snapshot
        cancelled_text = "Code chat request cancelled."
        return {
            'answer': cancelled_text,
            'thinking': None,
            'cancelled': True,
        } if include_trace else cancelled_text
    except KeyboardInterrupt:
        return {
            'answer': "Interrupted.",
            'thinking': None
        } if include_trace else "Interrupted."
    except Exception as e:
        if conversation is not None and conversation_snapshot is not None:
            conversation.messages = conversation_snapshot
            conversation.sdk_session_id = conversation_session_snapshot
        if isinstance(e, CodeChatProviderError):
            message = _safe_provider_error(str(e))
            return {'answer': '', 'thinking': None, 'error': message,
                    'error_type': 'provider_error'} if include_trace else message
        anth_error = _extract_anthropic_error_message(e)
        if anth_error:
            anth_error = _safe_provider_error(anth_error)
            reset_hint = "Reset the chat context and try the query again."
            error_type = "timeout" if _is_timeout_like_error_message(anth_error) else "provider_error"
            message = anth_error
            if error_type == "timeout":
                message = (
                    f"{anth_error}\n\n"
                    "This request likely exceeded the chat timeout window. "
                    "Try narrowing the question, or increase timeout settings "
                    "(CLAUDE_AGENT_SDK_TIMEOUT_MS / server timeout)."
                )
            if include_trace:
                return {
                    'answer': '',
                    'thinking': None,
                    'error': message,
                    'error_type': error_type,
                    'reset_hint': reset_hint,
                }
            return f"{message}\n\n{reset_hint}"
        raw_error = str(e).strip() or type(e).__name__
        if _is_timeout_like_error_message(raw_error):
            error_text = (
                "Code chat timed out before completing the request. "
                "Try a narrower query, or increase timeout settings "
                "(CLAUDE_AGENT_SDK_TIMEOUT_MS / server timeout)."
            )
            return {
                'answer': '',
                'thinking': None,
                'error': error_text,
                'error_type': 'timeout',
            } if include_trace else error_text
        error_text = "The Audit request could not be completed. Please retry; if it persists, check the provider configuration."
        return {
            'answer': '',
            'thinking': None,
            'error': error_text,
            'error_type': 'runtime_error',
        } if include_trace else error_text
