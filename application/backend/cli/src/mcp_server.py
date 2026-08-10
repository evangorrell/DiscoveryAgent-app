"""
MCP server for DiscoveryAgent interactive mode.

Exposes sub-agents as MCP tools so Claude Code can invoke them on behalf
of the scientist. Runs via stdio transport.

Usage:
    python -m cli.src.mcp_server --config /path/to/config.json
"""

import argparse
import contextlib
import json
import os
import sys
import time
import traceback
from importlib import import_module
from pathlib import Path
from threading import RLock
from typing import Optional

from fastmcp import FastMCP
from cli.src.session_store import extract_snapshot_id, update_session_progress

# ---------------------------------------------------------------------------
# Startup: parse config before importing heavy agent modules
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="DiscoveryAgent MCP Server")
    p.add_argument("--config", required=True, help="Path to JSON config file")
    return p.parse_args()


def load_config(path: str) -> dict:
    with open(path, "r") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Execution streamer — routes execution agent output to stderr + log file
# ---------------------------------------------------------------------------

class ExecutionStreamer:
    """Streams execution agent output to stderr and a log file."""

    def __init__(self, log_dir: str):
        self.stream_log_path = os.path.join(log_dir, "interactive_stream.log")
        os.makedirs(log_dir, exist_ok=True)
        self.stream_log = open(self.stream_log_path, "a")

    def write(self, message: str):
        sys.stderr.write(message + "\n")
        sys.stderr.flush()
        self.stream_log.write(message + "\n")
        self.stream_log.flush()

    def close(self):
        self.stream_log.close()


# ---------------------------------------------------------------------------
# Agent instantiation helpers
# ---------------------------------------------------------------------------

_STDIO_GUARD_LOCK = RLock()


@contextlib.contextmanager
def _guard_mcp_stdout():
    """
    Redirect process stdout to stderr while MCP tool code is running.

    FastMCP uses stdout for the JSON-RPC transport. Any incidental writes from
    agent code, SDKs, or dependency logging can corrupt the transport and cause
    Claude Code to report a generic internal tool error. Guard tool execution by
    temporarily mapping fd 1 to stderr and restoring it afterwards.
    """
    with _STDIO_GUARD_LOCK:
        saved_stdout_fd = os.dup(1)
        previous_stdout = sys.stdout
        try:
            os.dup2(2, 1)
            sys.stdout = sys.__stderr__
            yield
        finally:
            try:
                os.dup2(saved_stdout_fd, 1)
            finally:
                os.close(saved_stdout_fd)
            sys.stdout = previous_stdout


def _build_agent(agent_type: str, config_data: dict, logger):
    """Instantiate an agent from its type string and serialized config dict."""
    from AIR.tool_agents.agent_directory import STRING_TO_AGENT_MAP

    agent_cls = STRING_TO_AGENT_MAP.get(agent_type)
    if agent_cls is None:
        raise ValueError(f"Unknown agent type: {agent_type}")

    # Reconstruct config dataclass from dict
    from cli.src.serialization import deserialize_agent_config
    config_cls = _get_config_class(agent_type)
    config = deserialize_agent_config(config_cls, config_data)

    return agent_cls(config, logger=logger)


def _load_symbol(spec: str):
    module_name, symbol_name = spec.split(":", 1)
    module = import_module(module_name)
    return getattr(module, symbol_name)


def _get_config_class(agent_type: str):
    """Map agent type string to its config dataclass."""
    mapping = {
        "execution_agent": "AIR.tool_agents.execution_agent_cc:ExecutionAgentCCConfig",
        "web_search": "AIR.tool_agents.single_search:SingleSearchConfig",
        "deep_research": "AIR.tool_agents.deep_research:DeepResearchConfig",
        "acs_data_agent": "AIR.tool_agents.specialized.acs_agent:ACSDataAgentConfig",
        "census_data_agent": "AIR.tool_agents.specialized.census_agent:CensusDataAgentConfig",
        "opentargets_agent": "AIR.tool_agents.specialized.opentargets_agent:OpenTargetsAgentConfig",
    }
    spec = mapping.get(agent_type)
    if spec is None:
        raise ValueError(f"No config class for agent type: {agent_type}")
    return _load_symbol(spec)


# ---------------------------------------------------------------------------
# MCP Server setup
# ---------------------------------------------------------------------------

def create_server(config: dict):
    """Build the FastMCP server with all configured tools."""
    from AIR.utils.logger import Logger
    from cli.src.interactive_state import InteractiveState
    from AIR.config import LLMConfig
    from AIR.tool_agents.execution_agent_cc import ExecutionAgentCC
    from AIR.tool_agents.execution_sdk_tracker import ExecutionSDKTracker
    from AIR.hyp_agents.hypothesis_gen_agent import HypothesisGenAgent
    from AIR.config import HypothesisGenerationConfig

    mcp = FastMCP("discoveryagent")

    # -- Unpack config --
    log_dir = config.get("log_dir", "logs")
    analysis_name = config.get("analysis_name", "interactive")
    dataset_description = config.get("dataset_description", "")
    task_description = config.get("task_description")
    resume_checkpoint = config.get("resume_checkpoint")
    session_metadata_path = config.get("session_metadata_path")

    # -- Logger --
    # main() has already redirected fd 1 → stderr for the duration of create_server().
    # Logger fires logger.info() during __init__ (via console_handler → sys.__stdout__)
    # and sets sys.stdout = StreamToLogger(...).  Both are safe here because fd 1
    # currently points to stderr.  After Logger init we permanently redirect the
    # console_handler to sys.__stderr__ so it never touches fd 1 again.
    import logging as _logging
    logger = Logger(analysis_name=analysis_name, log_dir=log_dir)

    # Permanently redirect console_handler to stderr so log output never goes
    # to fd 1 (the MCP JSON-RPC channel) once main() restores it.
    for _h in list(_logging.getLogger("ai_researcher").handlers):
        if isinstance(_h, _logging.StreamHandler) and not isinstance(_h, _logging.FileHandler):
            _h.stream = sys.__stderr__

    logger.log_action("interactive_session_start", json.dumps({
        "analysis_name": analysis_name,
        "log_dir": log_dir,
        "agents": list(config.get("agents", {}).keys()),
    }, default=str))

    # -- Interactive state --
    state = InteractiveState(
        log_dir=log_dir,
        dataset_description=dataset_description,
        task_description=task_description,
        memory_manager=None,
        logger=logger,
        resume_from=resume_checkpoint,
        session_metadata_path=session_metadata_path,
    )

    # -- Execution streamer --
    streamer = ExecutionStreamer(log_dir)

    # -- Execution SDK tracker --
    execution_tracker = ExecutionSDKTracker(logger=logger)

    # -- Instantiate agents --
    agents = {}
    agent_configs = config.get("agents", {})
    for agent_type, agent_data in agent_configs.items():
        try:
            agents[agent_type] = _build_agent(agent_type, agent_data, logger)
            logger.log_action("agent_initialized", f"type={agent_type}")
        except Exception as e:
            logger.log_error(f"Failed to initialize agent {agent_type}: {e}")
            sys.stderr.write(f"WARNING: Failed to initialize {agent_type}: {e}\n")

    # -- Hypothesis gen agent (optional) --
    hyp_gen_agent = None
    hyp_config_data = config.get("hypothesis_gen_config")
    if hyp_config_data:
        try:
            hyp_llm_config = LLMConfig(
                model_name=hyp_config_data.get("model_name", "claude-3-5-sonnet-latest"),
                max_tokens=16384,
            )
            hyp_config = HypothesisGenerationConfig(
                llm_config=hyp_llm_config,
                log_dir=log_dir,
                max_tokens=16384,
            )
            hyp_gen_agent = HypothesisGenAgent(config=hyp_config, logger=logger)
        except Exception as e:
            logger.log_error(f"Failed to initialize HypothesisGenAgent: {e}")

    # -- Helper: wrap tool calls with logging and state tracking --
    def _sync_session_metadata(last_checkpoint_dir: Optional[str] = None) -> None:
        if not session_metadata_path:
            return
        kwargs = {
            "last_run_dir": log_dir,
            "last_memory_report": state.report_file_path,
        }
        if last_checkpoint_dir:
            kwargs["last_interactive_checkpoint"] = last_checkpoint_dir
            kwargs["head_snapshot_id"] = extract_snapshot_id(last_checkpoint_dir)
        try:
            update_session_progress(session_metadata_path, **kwargs)
        except Exception as exc:
            logger.log_error(f"Failed to update session metadata: {exc}")

    def _run_tool(tool_name: str, query: str, execute_fn) -> str:
        """Execute an agent and handle logging, checkpointing, report update."""
        with _guard_mcp_stdout():
            logger.log_action("mcp_tool_invoked", tool_name)
            logger.log_prompt("user", query, prompt_name=tool_name)
            streamer.write(f"[{tool_name}] Starting: {query[:200]}...")

            start_time = time.time()
            error = False
            try:
                result = execute_fn()
                if not isinstance(result, str):
                    result = str(result)
            except Exception as e:
                summary = str(e).strip() or f"{type(e).__name__}: {e}"
                result = (
                    f"Error executing {tool_name}: {summary}\n"
                    f"Debug log: {logger.get_log_path()}\n"
                    f"Action log: {logger.get_action_log_path()}\n"
                    f"Stream log: {streamer.stream_log_path}"
                )
                error = True
                logger.log_error(f"{result}\nTraceback:\n{traceback.format_exc()}")
                streamer.write(f"[{tool_name}] Failed. See logs in {log_dir}")

            duration = time.time() - start_time
            logger.log_response(result, source=tool_name)
            logger.log_action("query_completed", json.dumps({
                "tool": tool_name,
                "success": not error,
                "duration_seconds": round(duration, 2),
                "result_chars": len(result),
            }))

            streamer.write(f"[{tool_name}] Completed in {duration:.1f}s ({len(result)} chars)")

            # Record and checkpoint
            checkpoint_dir = state.record_tool_call(tool_name, query, result, error=error, duration_seconds=duration)

            logger.log_action("checkpoint_saved", checkpoint_dir)
            _sync_session_metadata(checkpoint_dir)
        return result

    # -----------------------------------------------------------------------
    # Register MCP tools
    # -----------------------------------------------------------------------

    # -- execute_analysis --
    if "execution_agent" in agents:
        exec_agent = agents["execution_agent"]

        @mcp.tool()
        def execute_analysis(query: str) -> str:
            """Execute data analysis and code on the dataset. Pass a detailed query describing experiments, statistical tests, or visualizations."""
            def _run():
                # Set up per-query logging
                query_num = state.query_count
                checkpoint_dir = state.get_checkpoint_dir(query_num)
                checkpoint_dir.mkdir(parents=True, exist_ok=True)

                if hasattr(exec_agent, 'execution_tracker') and exec_agent.execution_tracker:
                    exec_agent.execution_tracker.set_query_log_dir(
                        str(checkpoint_dir), query_num=query_num
                    )

                return exec_agent.execute(
                    query,
                    dataset_description=dataset_description,
                    query_num=query_num,
                )
            return _run_tool("execute_analysis", query, _run)

    # -- deep_research --
    if "deep_research" in agents:
        research_agent = agents["deep_research"]

        @mcp.tool()
        def deep_research(query: str) -> str:
            """Perform deep literature and web research. Returns a comprehensive synthesis report. Expensive - use sparingly."""
            return _run_tool("deep_research", query, lambda: research_agent.execute(query))

    # -- web_search --
    if "web_search" in agents:
        search_agent = agents["web_search"]

        @mcp.tool()
        def web_search(query: str) -> str:
            """Quick web search returning a concise summary. Use for factual lookups and specific references."""
            return _run_tool("web_search", query, lambda: search_agent.execute(query))

    # -- generate_hypothesis --
    if hyp_gen_agent is not None:
        @mcp.tool()
        def generate_hypothesis(
            description: str,
            background: str = "",
            task: str = "",
            n: int = 3,
        ) -> str:
            """Generate research hypotheses based on the dataset and background knowledge."""
            def _run():
                return hyp_gen_agent.generate_hypothesis(
                    description=description,
                    background=background,
                    task=task,
                    n_hypotheses=n,
                )
            return _run_tool("generate_hypothesis", description, _run)

    # -- acs_query --
    if "acs_data_agent" in agents:
        acs_agent = agents["acs_data_agent"]

        @mcp.tool()
        def acs_query(query: str) -> str:
            """Query the American Community Survey (ACS) Census data."""
            return _run_tool("acs_query", query, lambda: acs_agent.execute(query))

    # -- census_query --
    if "census_data_agent" in agents:
        census_agent = agents["census_data_agent"]

        @mcp.tool()
        def census_query(query: str) -> str:
            """Query broad U.S. Census Bureau data (ACS, PUMS, IPUMS)."""
            return _run_tool("census_query", query, lambda: census_agent.execute(query))

    # -- opentargets_query --
    if "opentargets_agent" in agents:
        ot_agent = agents["opentargets_agent"]

        @mcp.tool()
        def opentargets_query(query: str) -> str:
            """Query OpenTargets for drug discovery and target validation data."""
            return _run_tool("opentargets_query", query, lambda: ot_agent.execute(query))

    # -- State management tools (always available) --

    @mcp.tool()
    def get_research_report() -> str:
        """Retrieve the current accumulated research report showing all findings so far."""
        report = state.get_report()
        if not report:
            return "No research report has been accumulated yet. Run some analyses first."
        return report

    @mcp.tool()
    def update_research_report(new_findings: str) -> str:
        """Manually add findings to the accumulated research report."""
        updated = state.update_report(new_findings)
        checkpoint_dir = state.save_checkpoint(
            state.query_count,
            trigger="report_update",
        )
        _sync_session_metadata(checkpoint_dir)
        return f"Report updated successfully. Current report length: {len(updated)} characters."

    @mcp.tool()
    def save_checkpoint() -> str:
        """Manually save a checkpoint of the current session state."""
        checkpoint_dir = state.save_checkpoint(state.query_count)
        _sync_session_metadata(checkpoint_dir)
        stats = state.get_statistics()
        return (
            f"Checkpoint saved to: {checkpoint_dir}\n"
            f"Session stats: {stats['query_count']} queries, "
            f"{stats['report_length']} chars in report"
        )

    # -- Sanitize root logger handlers --
    # Some agent dependencies (e.g. acs_access/config.py) call
    # logging.basicConfig(handlers=[RichHandler(Console())]) at module level.
    # Console() defaults to stdout, which would corrupt the MCP stdio channel
    # once main() restores fd 1.  Redirect all root-logger stdout handlers to
    # stderr now, while fd 1 still points at stderr (so we can be sure we're
    # catching every handler that was created with the current fd 1).
    try:
        from rich.console import Console as _Console
    except ImportError:
        _Console = None
    for _h in list(_logging.getLogger().handlers):
        _console = getattr(_h, "console", None)
        if _console is not None and _Console is not None:
            # RichHandler (inherits from logging.Handler, not StreamHandler) —
            # swap its Console to one that writes permanently to stderr.
            _h.console = _Console(stderr=True)
        elif (isinstance(_h, _logging.StreamHandler)
              and not isinstance(_h, _logging.FileHandler)
              and getattr(_h, "stream", None) in (sys.stdout, sys.__stdout__)):
            _h.stream = sys.__stderr__

    return mcp


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    config = load_config(args.config)

    # Log startup to stderr (stdout is MCP protocol)
    sys.stderr.write(f"DiscoveryAgent MCP server starting...\n")
    sys.stderr.write(f"Config: {args.config}\n")
    sys.stderr.write(f"Log dir: {config.get('log_dir', 'logs')}\n")

    # Protect the MCP stdio channel (fd 1) during ALL of server initialization.
    # Logger.__init__, agent imports, and any other setup code may write to
    # sys.stdout or fd 1.  Redirect fd 1 → fd 2 (stderr) for the entire
    # create_server() call, then restore it right before server.run() so that
    # FastMCP gets a clean, uncontaminated stdout for JSON-RPC.
    _saved_stdout_fd = os.dup(1)
    os.dup2(2, 1)               # fd 1 → stderr for the whole init phase
    sys.stdout = sys.__stderr__  # Python-level guard against StreamToLogger writes

    try:
        server = create_server(config)
    finally:
        # Restore fd 1 → real MCP stdout and give FastMCP the real file objects
        os.dup2(_saved_stdout_fd, 1)
        os.close(_saved_stdout_fd)
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__

    server.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
