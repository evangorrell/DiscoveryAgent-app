"""
Writing agent using Claude Agent SDK multi-agent architecture.

Provides an orchestrator agent that delegates to specialized subagents
for reading checkpoint traces and the project codebase, then synthesizes
a research paper following a LaTeX template.

The subagent architecture is adapted from the audit agent pattern
(originally in DA_selection_ui/AIR/utils/code_chat.py).
"""

import asyncio
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Tuple

os.environ.setdefault("MAX_MCP_OUTPUT_TOKENS", "50000")

# ---------------------------------------------------------------------------
# Claude Agent SDK imports (graceful fallback copied from DA_selection_ui)
# ---------------------------------------------------------------------------
try:
    import claude_agent_sdk as _claude_sdk

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
    query = ClaudeAgentOptions = AgentDefinition = None
    AssistantMessage = ResultMessage = SystemMessage = StreamEvent = None
    TextBlock = ThinkingBlock = ToolUseBlock = ToolResultBlock = None

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

WRITER_ORCHESTRATOR_PROMPT = """
You are a research paper writing agent responsible for producing a high-quality
research paper from an AI-driven scientific research project.

You have access to specialized review subagents:
{agent_descriptions}

Your output directory (working directory): {output_dir}
The LaTeX template has been placed in this directory.

Workflow:
1. FIRST, delegate information gathering to your subagents:
   - Ask checkpoint-reviewer to provide a comprehensive summary of the research:
     objectives, methodology across rounds, key findings per round, and conclusions.
     Ask for specific results, statistics, and data references.
{code_reviewer_workflow_step}
2. After gathering information, explore your working directory to see the template files.
3. Write the research paper following the LaTeX template. Create a new file called
   "research_paper.tex" based on the template structure.
4. Iterate on the paper — review it and improve the writing, structure, and accuracy.

{template_instructions}

Critical file safety rules:
- Do NOT delete or overwrite existing data files.
- Save all generated artifacts within your working directory.
- Use descriptive filenames.

Guidelines for figures:
- View each figure before including it. Verify claims in figure descriptions.
- Include only the most important figures that exhibit key findings.
- When possible, cite figures in text using \\ref{{}}.
- Copy relevant figures into the images/ subdirectory of your working directory.

Guidelines for supplementary materials:
- Write a detailed supplementary materials section with exact tests and methods.
- Include as many figures as needed and be precise in details.

Iterative writing:
- Iterate multiple times on the paper. Write it, review it, and improve each time.

IMPORTANT: Do NOT try to compile the LaTeX file. Focus only on writing and formatting.
IMPORTANT: Only write files within your working directory: {output_dir}

At the end of your work, append a section titled "## Generated Files" listing ALL
created files with absolute paths, one per line prefixed by "- ".
""".strip()

CHECKPOINT_REVIEWER_PROMPT = """
You are a focused checkpoint review subagent for a research paper writing system.
You analyze checkpoint artifacts to help the writing agent understand the research
that was conducted so it can write an accurate paper.

The research trace is at: {checkpoint_dir}

The checkpoint structure can follow two layouts:

Round-aware layout:
- {checkpoint_dir}/round_N/: Base folder for round N
    - {checkpoint_dir}/round_N/query_NNN/: Base folder for query NNN.
      Contains query_NNN.md describing the query and results at that step.
      Queries are ordered numerically (001 before 002, etc.).
    - {checkpoint_dir}/round_N/memory_report.md: Full cumulative report up to this point.
    - {checkpoint_dir}/round_N/round_report.md: Report of research done in round N alone.

Legacy flat layout:
- {checkpoint_dir}/query_NNN/: Query folders directly under checkpoints.
    - Contains checkpoint.json and optionally query_NNN.md.

Instructions:
- Start by listing the checkpoint directory to determine which layout is used.
- Prioritize reading round_report.md and memory_report.md files over individual query files.
- Reference specific rounds and query identifiers in your findings.
- Provide detailed, structured summaries that the paper writer can directly use.
- Include specific results, statistics, numerical findings, and data references — not
  just high-level overviews.
- If asked about methodology, describe the experimental approach used in each round.
- Do not modify any files.
""".strip()

CODE_REVIEWER_PROMPT = """
You are a focused code review subagent for a research paper writing system.
You analyze the research project directory to help the writing agent understand
the methods, implementations, and analysis outputs.

The project directory is at: {code_dir}

{claude_md_section}

Instructions:
- Focus on understanding the analytical methods, algorithms, and data processing pipelines.
- Reference exact file paths in your findings.
- Describe methodology in terms suitable for a research paper methods section.
- Identify key parameters, configurations, and design choices.
- Look for documentation files (CLAUDE.md, DOCUMENTATION.md, README) first to
  understand the project structure.
- If DOCUMENTATION.md exists, use grep to search it rather than reading it fully
  (it may be very large).
- Look for final_report.md or similar summary documents that describe the research.
- Look for figures and plots that could be used in the paper. Report their paths.
- Do not modify any files.
""".strip()

# ---------------------------------------------------------------------------
# Agent definition builder
# ---------------------------------------------------------------------------


def _build_agent_definitions(
    checkpoint_dir: Path,
    code_dir: Optional[Path],
    claude_md_context: str,
) -> dict:
    """Build subagent definitions for the writing orchestrator."""

    def _agent_def(description: str, prompt: str, tools: list[str]) -> object:
        if AgentDefinition is not None:
            return AgentDefinition(description=description, prompt=prompt, tools=tools)
        return {"description": description, "prompt": prompt, "tools": tools}

    agents = {
        "checkpoint-reviewer": _agent_def(
            description=(
                "Checkpoint trace specialist. Use for understanding the research "
                "timeline, rounds, queries, hypotheses, key results, and overall "
                "research narrative."
            ),
            prompt=CHECKPOINT_REVIEWER_PROMPT.format(checkpoint_dir=checkpoint_dir),
            tools=["Read", "Grep", "Glob", "Bash"],
        ),
    }

    if code_dir and code_dir.exists():
        if claude_md_context and claude_md_context != "None available":
            claude_md_section = (
                "Here is the CLAUDE.md context for the project:\n"
                f"{claude_md_context}"
            )
        else:
            claude_md_section = (
                "No CLAUDE.md available. Explore the directory to understand "
                "the project structure."
            )
        agents["code-reviewer"] = _agent_def(
            description=(
                "Project codebase and outputs specialist. Use for understanding "
                "implementations, methods, algorithms, analysis code, figures, "
                "and technical details."
            ),
            prompt=CODE_REVIEWER_PROMPT.format(
                code_dir=code_dir,
                claude_md_section=claude_md_section,
            ),
            tools=["Read", "Grep", "Glob", "Bash"],
        )

    return agents


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _find_user_claude_cli() -> Optional[str]:
    """
    Return the path to the user's installed claude binary, if any.

    Checks PATH (shutil.which) and common install locations.  Returns None
    to let the SDK fall back to its bundled CLI.
    """
    import shutil

    if found := shutil.which("claude"):
        return found
    candidates = [
        Path.home() / ".local" / "bin" / "claude",
        Path.home() / ".npm-global" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def _resolve_cli_path_override() -> Optional[str]:
    """
    Resolve an explicit Claude CLI override, if requested.

    Default behavior is to let the SDK pick the runtime so it can use a
    compatible Claude Code build with full subagent support.
    """
    explicit = os.getenv("WRITING_AGENT_CLI_PATH")
    if explicit:
        p = Path(explicit).expanduser()
        if p.exists():
            return str(p)
        print(f"Warning: WRITING_AGENT_CLI_PATH does not exist: {p}")
        return None

    if os.getenv("WRITING_AGENT_USE_USER_CLI", "").lower() in {"1", "true", "yes"}:
        return _find_user_claude_cli()
    return None


def _read_claude_md(code_dir: Optional[Path]) -> str:
    """Try to read CLAUDE.md from the project directory."""
    if code_dir is None:
        return "None available"
    for name in ("CLAUDE.md", "claude.md"):
        p = code_dir / name
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8", errors="ignore").strip()
                if text:
                    max_chars = 12000
                    if len(text) > max_chars:
                        return (
                            text[:max_chars]
                            + "\n\n[... CLAUDE.md truncated for prompt size safety ...]"
                        )
                    return text
            except Exception:
                pass
    return "None available"


def _ensure_ps_in_path() -> Optional[str]:
    """
    Ensure a `ps` executable exists in PATH for Claude runtime process handling.

    Some sandbox/container environments omit procps entirely; Claude runtime can
    fail hard if `ps` is missing. In that case we prepend a tiny compatibility
    shim that supports the specific invocation pattern used by the runtime.
    """
    if shutil.which("ps"):
        return None

    shim_dir = Path(tempfile.mkdtemp(prefix="claude_ps_shim_"))
    shim_path = shim_dir / "ps"
    shim_path.write_text(
        "#!/usr/bin/env sh\n"
        "if [ \"$1\" = \"-o\" ] && [ \"$2\" = \"pid\" ] && [ \"$3\" = \"--no-headers\" ] && [ \"$4\" = \"--ppid\" ] && [ -n \"$5\" ]; then\n"
        "  ppid=\"$5\"\n"
        "  for f in /proc/[0-9]*/stat; do\n"
        "    [ -r \"$f\" ] || continue\n"
        "    pid=\"$(basename \"$(dirname \"$f\")\")\"\n"
        "    # /proc/<pid>/stat fields: 1=pid 2=comm 3=state 4=ppid\n"
        "    set -- $(cat \"$f\" 2>/dev/null)\n"
        "    proc_ppid=\"$4\"\n"
        "    if [ \"$proc_ppid\" = \"$ppid\" ]; then\n"
        "      echo \"$pid\"\n"
        "    fi\n"
        "  done\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    shim_path.chmod(0o755)
    os.environ["PATH"] = f"{shim_dir}:{os.environ.get('PATH', '')}"
    print(f"Warning: `ps` not found in PATH; using compatibility shim at {shim_path}")
    return str(shim_path)


def parse_stream_message(message) -> str:
    """Format a streamed SDK message for readable stdout output."""
    mtype = type(message).__name__

    # --- SystemMessage (init/other) ---
    if SystemMessage is not None and isinstance(message, SystemMessage):
        subtype = getattr(message, "subtype", "")
        if subtype == "init":
            data = getattr(message, "data", {})
            tools = data.get("tools", []) if isinstance(data, dict) else []
            tools_str = ", ".join(tools[:8]) + ("..." if len(tools) > 8 else "")
            return f"[Init] session={data.get('session_id', '?')[:8]}  tools=[{tools_str}]"
        return f"[SystemMessage subtype={subtype!r}]"

    # --- AssistantMessage ---
    if AssistantMessage is not None and isinstance(message, AssistantMessage):
        parts = []
        content = getattr(message, "content", [])
        if not isinstance(content, list):
            content = []
        for block in content:
            btype = type(block).__name__
            if TextBlock is not None and isinstance(block, TextBlock):
                text = getattr(block, "text", "")
                parts.append(text[:600] + ("..." if len(text) > 600 else ""))
            elif ToolUseBlock is not None and isinstance(block, ToolUseBlock):
                name = getattr(block, "name", "?")
                inp = getattr(block, "input", {})
                # Show a compact summary of the input
                if isinstance(inp, dict):
                    desc = inp.get("description", inp.get("prompt", inp.get("command", "")))
                    if desc:
                        parts.append(f"[Tool:{name}] {str(desc)[:200]}")
                    else:
                        parts.append(f"[Tool:{name}] {json.dumps(inp)[:200]}")
                else:
                    parts.append(f"[Tool:{name}] {str(inp)[:200]}")
            elif ThinkingBlock is not None and isinstance(block, ThinkingBlock):
                parts.append("[Thinking...]")
            else:
                parts.append(f"[{btype}]")
        return "\n".join(parts) if parts else "[AssistantMessage (empty)]"

    # --- ResultMessage ---
    if ResultMessage is not None and isinstance(message, ResultMessage):
        is_error = getattr(message, "is_error", None)
        num_turns = getattr(message, "num_turns", None)
        stop_reason = getattr(message, "stop_reason", None)
        result = getattr(message, "result", "")
        lines = [
            f"[Result] is_error={is_error}  num_turns={num_turns}  stop_reason={stop_reason!r}"
        ]
        if result:
            lines.append(f"  result: {str(result)[:400]}")
        if is_error:
            lines.append("  *** AGENT ENDED WITH ERROR ***")
        return "\n".join(lines)

    # --- ToolResultBlock in UserMessage ---
    if ToolResultBlock is not None:
        content = getattr(message, "content", None)
        if isinstance(content, list):
            for block in content:
                if isinstance(block, ToolResultBlock):
                    tool_use_id = getattr(block, "tool_use_id", "?")
                    c = getattr(block, "content", "")
                    c_str = str(c)
                    return f"[ToolResult id={tool_use_id[:8]}] {c_str[:300]}{'...' if len(c_str) > 300 else ''}"

    # --- Fallback: show type + truncated repr ---
    return f"[{mtype}] {str(message)[:300]}"


def extract_generated_files(raw_output: str) -> list[str]:
    """Extract the list of generated files from agent output."""
    patterns = [
        r"## Generated Files\s*\n((?:- [^\n]+\n?)*)",
        r"Generated Files:\s*\n((?:- [^\n]+\n?)*)",
        r"Files Created:\s*\n((?:- [^\n]+\n?)*)",
        r"Output Files:\s*\n((?:- [^\n]+\n?)*)",
    ]
    for pat in patterns:
        m = re.search(pat, raw_output, re.IGNORECASE | re.MULTILINE)
        if m:
            block = m.group(1)
            files = []
            for line in block.splitlines():
                line = line.strip()
                if line.startswith("- "):
                    fp = line[2:].strip()
                    if fp:
                        files.append(fp)
            if files:
                return files
    return []


# ---------------------------------------------------------------------------
# Main agent runner
# ---------------------------------------------------------------------------


async def run_writing_agent(
    checkpoint_dir: Path,
    output_dir: Path,
    code_dir: Optional[Path] = None,
    model: Optional[str] = None,
    max_turns: int = 50,
    template_instructions: str = "",
    encourage_replotting: bool = False,
) -> str:
    """
    Run the multi-agent writing system to produce a research paper.

    Args:
        checkpoint_dir: Path to the checkpoints directory.
        output_dir: Path to the output directory (where the paper is written).
                    The LaTeX template should already be copied here.
        code_dir: Optional path to the project/code directory.
        model: Claude model to use (default chosen by SDK).
        max_turns: Maximum agent interaction turns.
        template_instructions: Extra instructions about the template to include
                               in the orchestrator prompt.
        encourage_replotting: Whether to encourage figure re-plotting.

    Returns:
        Raw concatenated agent output string.
    """
    if query is None or ClaudeAgentOptions is None:
        raise RuntimeError(
            "claude-agent-sdk not installed. Install with: pip install claude-agent-sdk"
        )

    _ensure_ps_in_path()

    checkpoint_dir = Path(checkpoint_dir).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    code_dir = Path(code_dir).expanduser().resolve() if code_dir else None

    if not checkpoint_dir.exists():
        raise ValueError(f"Checkpoint directory not found: {checkpoint_dir}")
    if not output_dir.exists():
        raise ValueError(f"Output directory not found: {output_dir}")

    claude_md_context = _read_claude_md(code_dir)

    # Build subagent definitions
    agents = _build_agent_definitions(
        checkpoint_dir=checkpoint_dir,
        code_dir=code_dir,
        claude_md_context=claude_md_context,
    )

    # Build agent description list and workflow step for prompt
    agent_desc_lines = []
    for name, defn in agents.items():
        desc = defn.description if hasattr(defn, "description") else defn.get("description", "")
        agent_desc_lines.append(f"- {name}: {desc}")
    agent_descriptions = "\n".join(agent_desc_lines)

    has_code_reviewer = "code-reviewer" in agents
    code_reviewer_workflow_step = (
        "   - Ask code-reviewer to describe the key methods, algorithms, analysis\n"
        "     approaches, and any important figures or plots available in the project."
        if has_code_reviewer
        else ""
    )

    # Build orchestrator system prompt
    system_prompt = WRITER_ORCHESTRATOR_PROMPT.format(
        output_dir=output_dir,
        template_instructions=template_instructions,
        agent_descriptions=agent_descriptions,
        code_reviewer_workflow_step=code_reviewer_workflow_step,
    )

    if encourage_replotting:
        system_prompt += (
            "\n\nIMPORTANT: Before including any plots, view them first. "
            "If the visualizations could be improved, regenerate plots in a "
            "replotting/ subdirectory within your working directory. Do not "
            "modify original data, only replot with improved parameters."
        )

    # Build user prompt
    agent_list = ", ".join(agents.keys())
    code_step = (
        "\n3. Ask code-reviewer to describe the methodology, key algorithms, "
        "analysis approaches, and available figures."
        if has_code_reviewer
        else ""
    )
    user_prompt = (
        "Write a comprehensive research paper based on the research project data.\n\n"
        f"You have access to the following subagents: {agent_list}\n"
        "Use them to gather all necessary information before writing.\n\n"
        "Steps:\n"
        "1. Ask checkpoint-reviewer to summarize the full research narrative: objectives,\n"
        "   rounds of investigation, key findings per round, specific results and\n"
        "   statistics, and overall conclusions.\n"
        "2. Ask checkpoint-reviewer for details on methodology and experimental design\n"
        "   across all rounds."
        f"{code_step}\n"
        f"{'4' if has_code_reviewer else '3'}. Explore your working directory to see what template files are available.\n"
        f"{'5' if has_code_reviewer else '4'}. Write the research paper following the template, incorporating all\n"
        "   gathered information into a coherent narrative.\n"
        f"{'6' if has_code_reviewer else '5'}. Review and iterate on the paper to improve quality and accuracy.\n\n"
        f"IMPORTANT: Only write files in your working directory: {output_dir}"
    )

    # By default, let the SDK resolve its runtime.
    # Optional overrides are available via env vars:
    #   WRITING_AGENT_CLI_PATH=/abs/path/to/claude
    #   WRITING_AGENT_USE_USER_CLI=1
    cli_path = _resolve_cli_path_override()

    options = ClaudeAgentOptions(
        max_turns=max_turns,
        max_buffer_size=int(
            os.getenv("CLAUDE_CODE_MAX_BUFFER_SIZE", str(50 * 1024 * 1024))
        ),
        system_prompt=system_prompt,
        cwd=str(output_dir),
        allowed_tools=["Read", "Write", "Bash", "Grep", "Glob", "Task"],
        permission_mode="acceptEdits",
        model=model,
        agents=agents,
    )
    if cli_path:
        options.cli_path = cli_path

    print(f"Running writing agent...")
    print(f"  Checkpoint dir: {checkpoint_dir}")
    print(f"  Code dir:       {code_dir or 'N/A'}")
    print(f"  Output dir:     {output_dir}")
    print(f"  Model:          {model or 'default'}")
    print(f"  Max turns:      {max_turns}")
    print(f"  Subagents:      {agent_list}")
    print(f"  CLI path:       {cli_path or '(SDK bundled)'}")
    print("--- Streaming progress ---", flush=True)

    # The Claude Code CLI refuses to launch when CLAUDECODE is set (nested session
    # guard).  Temporarily remove it so the SDK subprocess can start cleanly.
    _nested_guard = os.environ.pop("CLAUDECODE", None)

    messages = []
    msg_count = 0
    try:
        async for msg in query(prompt=user_prompt, options=options):
            msg_count += 1
            messages.append(str(msg))
            pretty = parse_stream_message(msg)
            if pretty:
                print(f"[msg {msg_count}] {pretty}", flush=True)
    except Exception as e:
        print(f"\n*** SDK error after {msg_count} messages: {type(e).__name__}: {e} ***", flush=True)
        raise
    finally:
        if _nested_guard is not None:
            os.environ["CLAUDECODE"] = _nested_guard

    print(f"--- Writing complete ({msg_count} messages received) ---\n", flush=True)
    return "\n".join(messages)
