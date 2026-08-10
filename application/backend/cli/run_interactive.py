#!/usr/bin/env python3
"""
Interactive DiscoveryAgent launcher.

Usage (run as module from repo root):
    python -m cli.run_interactive --checkpoint-dir DIR --code-dir DIR
        [--log-dir DIR] [--config-override FILE] [--continue | --resume SESSION]

Loads agent configuration and accumulated findings from the latest round of a
previous automated run, then launches a Claude Code CLI session with all
DiscoveryAgent sub-agents available as MCP tools.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

# Keep repo root on sys.path regardless of invocation style.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
sys.path = [p for p in sys.path if os.path.abspath(p) != _SCRIPT_DIR]
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from AIR.utils.agent_config_builders import (
    load_data_description,
    load_task_description,
    set_deep_research_agent_config,
    set_single_search_agent_config,
    set_census_agent_config,
    set_opentargets_agent_config,
    set_execution_agent_config,
)
from AIR.prompts.hyp_generation import (
    BACKGROUND_REPORT_PROMPT,
    DATASET_DESCRIPTION_PROMPT,
    DATASET_DESCRIPTION_TASK_DESCRIPTION_PROMPT,
)
from AIR.utils.arg_parsers import create_interactive_parser
from AIR.utils.config_loader import load_yaml_config
from cli.src.session_store import (
    build_default_run_log_dir,
    create_session_metadata,
    get_latest_session_metadata,
    register_session_run,
    resolve_head_snapshot_path,
    resolve_session_metadata,
    validate_session_scope,
)
from cli.src.serialization import serialize_agent_config

# Temp files cleaned up when claude CLI replaces this process (atexit won't fire,
# but the OS reclaims tmpfs; we register anyway for the error-exit path).
import atexit
_temp_files = []

def _cleanup_temp_files():
    for f in _temp_files:
        try:
            os.unlink(f)
        except OSError:
            pass

atexit.register(_cleanup_temp_files)


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def _find_latest_round_config(checkpoint_dir: str):
    """Return (config_path, round_num) for the highest-numbered round_N dir
    that contains a config.json, or (None, None) if none found."""
    checkpoints_base = Path(checkpoint_dir) / "checkpoints"
    if not checkpoints_base.exists():
        return None, None

    round_dirs = sorted(
        [d for d in checkpoints_base.iterdir()
         if d.is_dir() and d.name.startswith("round_") and d.name[6:].isdigit()],
        key=lambda d: int(d.name[6:]),
    )
    for round_dir in reversed(round_dirs):
        config_path = round_dir / "config.json"
        if config_path.exists():
            return config_path, int(round_dir.name[6:])

    return None, None


def _load_checkpoint_config(config_path) -> dict:
    """Load the inner config dict from a checkpoint config.json."""
    with open(config_path) as f:
        data = json.load(f)
    return data.get("config", data)


def _find_memory_report(checkpoint_dir: str, latest_round: int):
    """Return path to the most recent completed memory_report.md, or None."""
    checkpoints_base = Path(checkpoint_dir) / "checkpoints"
    for r in range(latest_round, -1, -1):
        report = checkpoints_base / f"round_{r}" / "memory_report.md"
        if report.exists():
            return str(report)
    return None


# ---------------------------------------------------------------------------
# Defaults for fields that may be absent in older checkpoint configs
# ---------------------------------------------------------------------------

_CONFIG_DEFAULTS = {
    "execution_model": "claude-3-5-sonnet-latest",
    "execution_max_turns": 10,
    "hypothesis_model": "claude-3-5-sonnet-latest",
    "memory_llm_model": "claude-sonnet-4-5-20250929",
    "single_search_model": "claude-opus-4-6",
    "single_search_max_tokens": 16384,
    "omit_deepresearch": False,
    "omit_singlesearch": False,
    "use_acs_agent": False,
    "acs_output_dir": None,
    "use_census_agent": False,
    "census_output_dir": None,
    "use_opentargets_agent": False,
    "opentargets_output_dir": None,
    "opentargets_max_turns": 15,
    "opentargets_model": "claude-sonnet-4-6",
    "use_simple_memory": False,
    "no_sandbox_enforcement": False,
    "allowed_read_paths": None,
    "execution_skills_path": None,
    "execution_opentargets_mcp": False,
    "execution_opentargets_servers": None,
    "execution_opentargets_mcp_path": None,
    "execution_opentargets_data_path": None,
    "claude_model": "claude-sonnet-4-6",
    "readme_path": None,
    "task_description_file": None,
    "hint_txt_path": None,
    "analysis_name": None,
    "log_execution_agent": True,
    "sub_agent_architecture": False,
    "claude_md_generator": False,
    "skip_hypothesis": False,
}

_INTERACTIVE_UNSUPPORTED_AGENT_FLAGS = {
    "use_acs_agent": "ACS",
}

_MCP_PREFLIGHT_TIMEOUT_SECONDS = 45
_CLAUDE_HELP_TIMEOUT_SECONDS = 15
_CLAUDE_VALUE_FLAGS = {
    "--allowedTools",
    "--allowed-tools",
    "--append-system-prompt",
    "--debug",
    "--debug-file",
    "--mcp-config",
    "--model",
    "--name",
    "--resume",
    "--session-id",
    "--system-prompt",
}

_MCP_PREFLIGHT_SCRIPT = r"""
import asyncio
import json
import sys
import traceback

from fastmcp import Client


async def main() -> int:
    config_path = sys.argv[1]
    expected_tools = set(json.loads(sys.argv[2]))
    timeout_seconds = float(sys.argv[3])

    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            config = json.load(fh)

        client = Client(
            config,
            timeout=timeout_seconds,
            init_timeout=timeout_seconds,
        )
        async with client:
            await client.ping()
            tools = await client.list_tools()

        tool_names = sorted(tool.name for tool in tools)
        missing_tools = sorted(expected_tools.difference(tool_names))
        payload = {
            "tool_names": tool_names,
            "missing_tools": missing_tools,
        }
        print(json.dumps(payload))
        return 0 if not missing_tools else 2
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 1


raise SystemExit(asyncio.run(main()))
"""


def _build_mcp_subprocess_env(repo_root: str | None = None) -> dict:
    """
    Preserve the current shell environment for the MCP subprocess.

    Claude launches the DiscoveryAgent MCP server out-of-process. On clusters and
    collaborator machines, replacing the environment with a tiny API-key subset
    drops important runtime context such as PATH, HOME, proxy/SSL settings,
    activated-venv variables, and any CLI/runtime overrides. That can leave the
    server or execution agent unable to locate Python, Claude Code, certificates,
    or site-specific network configuration.
    """
    env = dict(os.environ)
    repo_root = os.path.abspath(repo_root or _REPO_ROOT)
    existing_pythonpath = env.get("PYTHONPATH")
    if existing_pythonpath:
        env["PYTHONPATH"] = os.pathsep.join(
            [repo_root, *(p for p in existing_pythonpath.split(os.pathsep) if p)]
        )
    else:
        env["PYTHONPATH"] = repo_root
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("FASTMCP_SHOW_SERVER_BANNER", "false")
    env.setdefault("FASTMCP_LOG_LEVEL", "ERROR")
    env.setdefault("JUPYTER_PLATFORM_DIRS", "1")
    return env


def _get_mcp_python_executable() -> str:
    """
    Return a Python executable path that preserves the active virtualenv.

    `sys.executable` is usually sufficient, but some launchers and wrappers can
    resolve it to a base interpreter outside the project environment. Prefer the
    venv-local python shim when running inside a virtualenv so the spawned MCP
    server sees the same installed packages as the interactive launcher.
    """
    candidates = [
        Path(sys.prefix) / "bin" / "python",
        Path(sys.prefix) / "bin" / "python3",
        Path(sys.prefix) / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _make_ns(cfg: dict, working_directory: str) -> argparse.Namespace:
    """Build an argparse.Namespace from merged config with defaults filled in."""
    merged = {**_CONFIG_DEFAULTS, **{k: v for k, v in cfg.items() if v is not None}}
    merged["working_directory"] = working_directory
    return argparse.Namespace(**merged)


def _load_text_if_exists(path: str | None) -> str | None:
    """Read a UTF-8 text file when it exists, otherwise return None."""
    if not path:
        return None

    candidate = Path(path).expanduser()
    if not candidate.exists() or not candidate.is_file():
        return None

    with open(candidate, "r", encoding="utf-8") as handle:
        return handle.read()


def _resolve_startup_background_report(
    session_metadata: dict,
    default_report_text: str | None,
    default_report_path: str | None,
) -> tuple[str | None, str | None]:
    """
    Resolve which background report should be injected into the top-level session.

    Interactive CLI sessions rely on Claude's own conversation persistence for
    continuity. Only seed the session from the latest automated checkpoint
    report, not from CLI-generated memory reports.
    """
    return default_report_text, default_report_path


def _build_director_startup_context(
    dataset_description: str,
    task_description: str | None,
    background_report: str | None,
) -> str:
    """
    Build hidden startup context using the exact AIR prompt blocks.

    This mirrors the same dataset/task/background framing used by AIR prompt
    templates, while keeping Claude Code's native system prompt intact.
    """
    sections = []

    if dataset_description:
        if task_description:
            sections.append(
                DATASET_DESCRIPTION_TASK_DESCRIPTION_PROMPT.format(
                    dataset_description=dataset_description,
                    task_description=task_description,
                ).strip()
            )
        else:
            sections.append(
                DATASET_DESCRIPTION_PROMPT.format(
                    dataset_description=dataset_description
                ).strip()
            )

    if background_report:
        sections.append(
            BACKGROUND_REPORT_PROMPT.format(
                background_report=background_report
            ).strip()
        )

    return "\n\n".join(section for section in sections if section)


def _format_launch_command_for_display(cmd: list[str]) -> str:
    """Redact hidden prompt arguments from the printed launch command."""
    redacted = []
    skip_next = False
    for token in cmd:
        if skip_next:
            skip_next = False
            continue
        if token in {"--append-system-prompt", "--system-prompt"}:
            redacted.extend([token, "[hidden discoveryagent startup context]"])
            skip_next = True
        else:
            redacted.append(token)
    return " ".join(redacted)


def _get_claude_help_text() -> str:
    """Return `claude --help` output or exit with a clear launcher error."""
    claude_path = shutil.which("claude")
    if not claude_path:
        sys.exit(
            "Error: `claude` was not found on PATH.\n"
            "Install Claude Code or adjust PATH before launching interactive mode."
        )

    result = subprocess.run(
        [claude_path, "--help"],
        capture_output=True,
        text=True,
        timeout=_CLAUDE_HELP_TIMEOUT_SECONDS,
        env=os.environ,
    )
    help_text = "\n".join(
        part for part in ((result.stdout or "").strip(), (result.stderr or "").strip())
        if part
    )
    if result.returncode != 0 or not help_text:
        details = help_text or "No help output captured."
        sys.exit(
            "Error: failed to inspect the installed Claude Code CLI.\n"
            f"Command: {claude_path} --help\n"
            f"Exit code: {result.returncode}\n"
            f"Details:\n{details}"
        )
    return help_text


def _extract_claude_supported_flags(help_text: str) -> set[str]:
    """Extract `--flag` options from Claude's help text."""
    return set(re.findall(r"(?<![A-Za-z0-9-])--[A-Za-z0-9][A-Za-z0-9-]*", help_text))


def _validate_claude_launch_command(
    cmd: list[str],
    *,
    mcp_config_path: str,
) -> None:
    """
    Validate the exact Claude CLI command before replacing this process.

    This catches unsupported flags and malformed flag/value pairs up front,
    instead of handing control to Claude and leaving the user with an opaque
    startup failure.
    """
    help_text = _get_claude_help_text()
    supported_flags = _extract_claude_supported_flags(help_text)
    if not supported_flags:
        sys.exit(
            "Error: could not parse supported flags from `claude --help`.\n"
            f"Help output:\n{help_text}"
        )

    seen_flags: dict[str, str | None] = {}
    i = 1
    while i < len(cmd):
        token = cmd[i]
        if not token.startswith("--"):
            i += 1
            continue
        if token not in supported_flags:
            sys.exit(
                "Error: the installed Claude Code CLI does not support a required launch flag.\n"
                f"Unsupported flag: {token}\n"
                f"Launch command: {_format_launch_command_for_display(cmd)}\n"
                "Upgrade Claude Code or adjust the interactive launcher."
            )
        if token in _CLAUDE_VALUE_FLAGS:
            if i + 1 >= len(cmd):
                sys.exit(f"Error: launch flag {token} is missing its value.")
            value = cmd[i + 1]
            if not value or value.startswith("--"):
                sys.exit(
                    f"Error: launch flag {token} has an invalid value: {value!r}"
                )
            seen_flags[token] = value
            i += 2
            continue
        seen_flags[token] = None
        i += 1

    if "--resume" in seen_flags and "--session-id" in seen_flags:
        sys.exit(
            "Error: launcher built an invalid Claude command with both "
            "`--resume` and `--session-id`."
        )

    if "--mcp-config" not in seen_flags:
        sys.exit("Error: launcher built a Claude command without `--mcp-config`.")

    mcp_path = Path(mcp_config_path)
    if not mcp_path.exists():
        sys.exit(
            "Error: the generated Claude MCP config file is missing.\n"
            f"Expected path: {mcp_path}"
        )

    try:
        uuid.UUID(seen_flags.get("--session-id") or seen_flags.get("--resume") or "")
    except ValueError:
        if "--session-id" in seen_flags or "--resume" in seen_flags:
            bad_flag = "--session-id" if "--session-id" in seen_flags else "--resume"
            sys.exit(
                f"Error: {bad_flag} must be a valid UUID.\n"
                f"Value: {seen_flags.get(bad_flag)!r}"
            )


def _expected_mcp_tools(agent_configs: dict, include_hypothesis: bool) -> list[str]:
    """Return the tool names that must be present for a healthy interactive session."""
    expected = {
        "get_research_report",
        "update_research_report",
        "save_checkpoint",
    }

    if "execution_agent" in agent_configs:
        expected.add("execute_analysis")
    if "deep_research" in agent_configs:
        expected.add("deep_research")
    if "web_search" in agent_configs:
        expected.add("web_search")
    if "acs_data_agent" in agent_configs:
        expected.add("acs_query")
    if "census_data_agent" in agent_configs:
        expected.add("census_query")
    if "opentargets_agent" in agent_configs:
        expected.add("opentargets_query")
    if include_hypothesis:
        expected.add("generate_hypothesis")

    return sorted(expected)


def _probe_discoveryagent_mcp(
    mcp_cli_config_path: str,
    expected_tools: list[str],
    *,
    python_executable: str,
    cwd: str,
) -> list[str]:
    """
    Verify that the DiscoveryAgent MCP server actually starts and exposes tools.

    This runs a short-lived FastMCP client handshake before launching Claude so
    installation and environment problems fail here with an actionable error,
    rather than surfacing later as a confusing empty-tool session.
    """
    result = subprocess.run(
        [
            python_executable,
            "-c",
            _MCP_PREFLIGHT_SCRIPT,
            mcp_cli_config_path,
            json.dumps(expected_tools),
            str(_MCP_PREFLIGHT_TIMEOUT_SECONDS),
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=_MCP_PREFLIGHT_TIMEOUT_SECONDS + 5,
        env=_build_mcp_subprocess_env(cwd),
    )

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    if result.returncode == 0:
        try:
            payload = json.loads(stdout.splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            details = stderr or stdout or "No additional output captured."
            sys.exit(
                "Error: DiscoveryAgent MCP preflight produced an unreadable success response.\n"
                f"Reason: {exc}\n"
                f"Details:\n{details}"
            )
        return payload.get("tool_names", [])

    if result.returncode == 2:
        try:
            payload = json.loads(stdout.splitlines()[-1])
            missing_tools = payload.get("missing_tools", [])
            available_tools = payload.get("tool_names", [])
        except (IndexError, json.JSONDecodeError):
            missing_tools = expected_tools
            available_tools = []
        details = stderr or stdout or "No additional output captured."
        sys.exit(
            "Error: DiscoveryAgent MCP server started, but required tools were not registered.\n"
            f"Missing tools: {', '.join(missing_tools) or 'unknown'}\n"
            f"Available tools: {', '.join(available_tools) or 'none'}\n"
            "This usually means an environment or dependency problem prevented one or more agents from initializing.\n"
            f"Details:\n{details}"
        )

    details = stderr or stdout or "No additional output captured."
    sys.exit(
        "Error: DiscoveryAgent MCP preflight failed before Claude launched.\n"
        "Check your Python environment, installed extras, API keys, and Claude Code installation.\n"
        f"Details:\n{details}"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = create_interactive_parser()
    args = parser.parse_args()

    # -- Validate inputs --
    checkpoint_dir = os.path.abspath(args.checkpoint_dir)
    working_directory = os.path.abspath(args.code_dir)

    if not os.path.exists(checkpoint_dir):
        sys.exit(f"Error: --checkpoint-dir does not exist: {checkpoint_dir}")
    if not os.path.exists(working_directory):
        sys.exit(f"Error: --code-dir does not exist: {working_directory}")

    # -- Load checkpoint config --
    config_path, round_num = _find_latest_round_config(checkpoint_dir)
    if config_path is None:
        sys.exit(f"Error: no round_N/config.json found under {checkpoint_dir}/checkpoints/")

    print(f"\nLoading config from round {round_num}: {config_path}")
    cfg = _load_checkpoint_config(config_path)

    # -- Apply config-override --
    if args.config_override:
        override = load_yaml_config(args.config_override)
        if override:
            cfg.update(override)
            print(f"Applied config override: {args.config_override}")

    disabled_agents = []
    for flag, label in _INTERACTIVE_UNSUPPORTED_AGENT_FLAGS.items():
        if cfg.get(flag):
            disabled_agents.append(label)
        cfg[flag] = False
    if disabled_agents:
        print(
            "Interactive CLI does not load these optional agents: "
            + ", ".join(disabled_agents)
        )

    # -- Load memory report --
    accumulated_report = None
    report_path = _find_memory_report(checkpoint_dir, round_num)
    if report_path:
        accumulated_report = _load_text_if_exists(report_path)
        if accumulated_report:
            print(
                f"Loaded accumulated findings ({len(accumulated_report):,} chars) "
                f"from round {round_num}"
            )

    # -- Build namespace for agent config builders --
    ns = _make_ns(cfg, working_directory)
    analysis_name = ns.analysis_name or Path(working_directory).name

    # -- Resolve session --
    resumed_session = args.continue_session or bool(args.resume_session)
    if args.continue_session:
        session_metadata = get_latest_session_metadata(checkpoint_dir)
        if session_metadata is None:
            sys.exit(
                "Error: no prior interactive sessions found. "
                "Start a new session without --continue."
            )
    elif args.resume_session:
        try:
            session_metadata = resolve_session_metadata(checkpoint_dir, args.resume_session)
        except (FileNotFoundError, ValueError) as exc:
            sys.exit(f"Error: {exc}")
    else:
        session_metadata = create_session_metadata(
            checkpoint_dir=checkpoint_dir,
            code_dir=working_directory,
            analysis_name=analysis_name,
            display_name=args.session_name,
        )

    try:
        validate_session_scope(session_metadata, checkpoint_dir, working_directory)
    except ValueError as exc:
        sys.exit(f"Error: {exc}")

    resume_checkpoint_path = resolve_head_snapshot_path(session_metadata)
    resume_checkpoint = str(resume_checkpoint_path) if resume_checkpoint_path else None

    # -- Resolve log-dir --
    if args.log_dir:
        log_dir = os.path.abspath(args.log_dir)
    else:
        log_dir = str(build_default_run_log_dir(session_metadata))
    os.makedirs(log_dir, exist_ok=True)
    session_metadata = register_session_run(
        session_metadata,
        log_dir=log_dir,
        resumed_from_checkpoint=resume_checkpoint,
    )

    # -- Load dataset description --
    readme_path = ns.readme_path or os.path.join(working_directory, "data", "README.txt")
    dataset_description = load_data_description(readme_path) or ""
    dataset_description = dataset_description.replace(
        "{datapath}", os.path.join(working_directory, "data")
    )
    if ns.hint_txt_path and os.path.exists(ns.hint_txt_path):
        with open(ns.hint_txt_path) as f:
            dataset_description += f"\n\n{f.read()}"

    # -- Load task description --
    task_description = None
    if ns.task_description_file:
        td_path = (
            ns.task_description_file if os.path.isabs(ns.task_description_file)
            else os.path.join(working_directory, ns.task_description_file)
        )
        task_description = load_task_description(td_path)
        if task_description:
            task_description = task_description.replace(
                "{datapath}", os.path.join(working_directory, "data")
            )

    startup_background_report, startup_background_report_path = (
        _resolve_startup_background_report(
            session_metadata,
            accumulated_report,
            report_path,
        )
    )
    startup_context = _build_director_startup_context(
        dataset_description=dataset_description,
        task_description=task_description,
        background_report=startup_background_report,
    )

    # -- Build agent configs --
    agent_configs = {}

    exec_config = set_execution_agent_config(ns, dataset_description, working_directory, analysis_name)
    exec_config.log_dir = log_dir
    agent_configs["execution_agent"] = serialize_agent_config(exec_config)

    if not ns.omit_deepresearch:
        dr_config = set_deep_research_agent_config(ns)
        dr_config.log_dir = log_dir
        agent_configs["deep_research"] = serialize_agent_config(dr_config)

    if not ns.omit_singlesearch:
        ss_config = set_single_search_agent_config(ns)
        ss_config.log_dir = log_dir
        agent_configs["web_search"] = serialize_agent_config(ss_config)

    if ns.use_census_agent:
        if os.getenv("CENSUS_AGENT_AVAILABLE", "").lower() in ("1", "true", "yes"):
            census_config = set_census_agent_config(ns, working_directory)
            census_config.log_dir = log_dir
            agent_configs["census_data_agent"] = serialize_agent_config(census_config)
        else:
            print(
                "Census agent requested (use_census_agent=true) but "
                "CENSUS_AGENT_AVAILABLE is not set in the environment — skipping."
            )

    if ns.use_opentargets_agent:
        if os.getenv("OPENTARGETS_AGENT_AVAILABLE", "").lower() in ("1", "true", "yes"):
            ot_config = set_opentargets_agent_config(ns, working_directory)
            ot_config.log_dir = log_dir
            agent_configs["opentargets_agent"] = serialize_agent_config(ot_config)
        else:
            print(
                "OpenTargets agent requested (use_opentargets_agent=true) but "
                "OPENTARGETS_AGENT_AVAILABLE is not set in the environment — skipping."
            )

    hyp_gen_config = {"model_name": ns.hypothesis_model}

    # -- MCP server config --
    mcp_config_data = {
        "log_dir": log_dir,
        "analysis_name": analysis_name,
        "dataset_description": dataset_description,
        "task_description": task_description,
        "use_simple_memory": ns.use_simple_memory,
        "memory_llm_model": ns.memory_llm_model,
        "resume_checkpoint": resume_checkpoint,
        "session_id": session_metadata["session_id"],
        "session_metadata_path": str(
            Path(session_metadata["session_dir"]) / "session.json"
        ),
        "agents": agent_configs,
        "hypothesis_gen_config": hyp_gen_config,
    }

    config_fd, config_path_tmp = tempfile.mkstemp(suffix=".json", prefix="da_interactive_")
    _temp_files.append(config_path_tmp)
    with os.fdopen(config_fd, "w") as f:
        json.dump(mcp_config_data, f, indent=2, default=str)

    # -- Write MCP config for Claude CLI --
    python_exe = _get_mcp_python_executable()
    discoveryagent_root = _REPO_ROOT
    mcp_cli_config = {
        "mcpServers": {
            "discoveryagent": {
                "command": python_exe,
                "args": ["-m", "cli.src.mcp_server", "--config", config_path_tmp],
                "cwd": discoveryagent_root,
                "env": _build_mcp_subprocess_env(discoveryagent_root),
            }
        }
    }
    mcp_fd, mcp_config_path = tempfile.mkstemp(suffix=".json", prefix="da_mcp_")
    _temp_files.append(mcp_config_path)
    with os.fdopen(mcp_fd, "w") as f:
        json.dump(mcp_cli_config, f, indent=2)

    expected_tools = _expected_mcp_tools(
        agent_configs,
        include_hypothesis=bool(hyp_gen_config),
    )
    print("Checking DiscoveryAgent MCP server...")
    available_tools = _probe_discoveryagent_mcp(
        mcp_config_path,
        expected_tools,
        python_executable=python_exe,
        cwd=discoveryagent_root,
    )
    print(
        "DiscoveryAgent MCP server launched successfully. "
        f"Tools: {', '.join(available_tools)}"
    )

    # -- Print session summary --
    claude_model = ns.claude_model or "claude-sonnet-4-6"
    print(f"\n{'=' * 60}")
    print(f"DiscoveryAgent Interactive Session")
    print(f"{'=' * 60}")
    print(f"Session:     {session_metadata['session_id']}")
    print(f"Name:        {session_metadata.get('display_name') or analysis_name}")
    print(f"Mode:        {'Resume' if resumed_session else 'New'}")
    print(f"Analysis:    {analysis_name}")
    print(f"Code dir:    {working_directory}")
    print(f"Log dir:     {log_dir}")
    print(f"Agents:      {', '.join(agent_configs.keys())}")
    print(f"Model:       {claude_model}")
    if startup_background_report:
        print(
            f"Prior work:  {len(startup_background_report):,} chars loaded "
            f"from {startup_background_report_path}"
        )
    if startup_context:
        print(f"Context:     {len(startup_context):,} hidden chars injected into Claude Code")
    if resume_checkpoint:
        print(f"Restored:    {resume_checkpoint}")
    print(f"Continue:    python -m cli.run_interactive --checkpoint-dir {checkpoint_dir} --code-dir {working_directory} --continue")
    print(f"Resume by ID: python -m cli.run_interactive --checkpoint-dir {checkpoint_dir} --code-dir {working_directory} --resume {session_metadata['session_id']}")
    print("Resume note: use the interactive launcher commands above, not raw `claude --resume`.")
    print(f"\nWatch execution: tail -f {log_dir}/interactive_stream.log")
    print(f"{'=' * 60}\n")

    # -- Launch Claude CLI from code-dir so it picks up CLAUDE.md there --
    os.chdir(working_directory)
    cmd = [
        "claude",
        "--mcp-config", mcp_config_path,
        "--strict-mcp-config",
        "--model", claude_model,
        "--allowedTools", "mcp__discoveryagent__*",
    ]
    if resumed_session:
        cmd += ["--resume", session_metadata["claude_session_id"]]
    else:
        cmd += ["--session-id", session_metadata["claude_session_id"]]
        if session_metadata.get("display_name"):
            cmd += ["--name", session_metadata["display_name"]]
    if startup_context:
        cmd += ["--append-system-prompt", startup_context]
    _validate_claude_launch_command(cmd, mcp_config_path=mcp_config_path)
    print(f"Launching: {_format_launch_command_for_display(cmd)}\n")
    os.execvp("claude", cmd)


if __name__ == "__main__":
    main()
