"""
Extracted agent configuration builders.

These functions were originally in scripts/run_hb_sherlock.py. They are shared
by both the automated HypothesisBrancher runner and the interactive CLI launcher.
"""

import os

from AIR.config import LLMConfig
from AIR.tool_agents.single_search import SingleSearchConfig
from AIR.tool_agents.deep_research import DeepResearchConfig
from AIR.tool_agents.execution_agent_cc import ExecutionAgentCCConfig

from AIR.prompts.tools import (
    DEEP_RESEARCH_TAG,
    DEEP_RESEARCH_PROMPT,
    SINGLE_WEB_SEARCH_TAG,
    SINGLE_WEB_SEARCH_PROMPT,
    EXECUTION_AGENT_TAG,
    EXECUTION_AGENT_PROMPT,
)


def load_data_description(data_path):
    """Load dataset description from a file if provided, otherwise return None."""
    if data_path and os.path.exists(data_path):
        try:
            with open(data_path, "r") as f:
                return f.read()
        except Exception as e:
            print(f"Warning: Could not read data description from {data_path}: {e}")
    return None


def load_task_description(task_path):
    """Load task description from a file if provided, otherwise return None."""
    if task_path and os.path.exists(task_path):
        try:
            with open(task_path, "r") as f:
                return f.read()
        except Exception as e:
            print(f"Warning: Could not read task description from {task_path}: {e}")
    return None


def load_background_report(report_path):
    """Load background report from a file if provided, otherwise return None."""
    if report_path and os.path.exists(report_path):
        try:
            with open(report_path, "r") as f:
                content = f.read().strip()
                return content if content else None
        except Exception as e:
            print(f"Warning: Could not read background report from {report_path}: {e}")
    return None


def set_deep_research_agent_config(args):
    # llm_config is only consumed by the "perplexity" (and legacy "openai") path;
    # it's still built unconditionally so that fallback keeps working unchanged.
    llm_config = LLMConfig(model_name="sonar-deep-research", api_provider="perplexity")
    return DeepResearchConfig(
        agent_name=DEEP_RESEARCH_TAG,
        tag=DEEP_RESEARCH_TAG,
        tool_prompt=DEEP_RESEARCH_PROMPT,
        llm_config=llm_config,
        log_dir="logs",
        max_output_tokens=5000,
        deep_research_provider=getattr(args, "deep_research_provider", "paperclip"),
    )


def set_single_search_agent_config(args):
    llm_config = LLMConfig(
        model_name=args.single_search_model,
        max_tokens=args.single_search_max_tokens,
    )
    return SingleSearchConfig(
        agent_name=SINGLE_WEB_SEARCH_TAG,
        tag=SINGLE_WEB_SEARCH_TAG,
        tool_prompt=SINGLE_WEB_SEARCH_PROMPT,
        llm_config=llm_config,
        log_dir="logs",
        max_tokens=args.single_search_max_tokens,
        max_web_searches=3,
    )


def set_acs_agent_config(args, working_directory):
    """Configure the ACSDataAgent (American Community Survey helper)."""
    try:
        from AIR.tool_agents.specialized.acs_agent import ACSDataAgentConfig
    except ImportError as exc:
        raise ImportError(
            "ACS agent requested but its optional dependencies are not installed. "
            "Install them with `uv sync --extra acs` or `pip install -e \".[acs]\"`."
        ) from exc

    if getattr(args, "expanding_dataset", False):
        default_output = (
            os.path.join(working_directory, args.expanding_dataset_dir)
            if working_directory
            else "./data/downloads"
        )
    else:
        default_output = (
            os.path.join(working_directory, "acs_downloads")
            if working_directory
            else "./acs_downloads"
        )

    if args.acs_output_dir:
        output_dir = (
            os.path.join(working_directory, args.acs_output_dir)
            if working_directory and not os.path.isabs(args.acs_output_dir)
            else args.acs_output_dir
        )
    else:
        output_dir = default_output

    os.makedirs(output_dir, exist_ok=True)

    return ACSDataAgentConfig(
        agent_name="acs_data_agent",
        tag="acs",
        output_dir=output_dir,
        log_dir="logs",
    )


def set_census_agent_config(args, working_directory):
    """Configure the CensusDataAgent (broad Census Bureau access)."""
    from AIR.tool_agents.specialized.census_agent import CensusDataAgentConfig

    if getattr(args, "expanding_dataset", False):
        default_output = (
            os.path.join(working_directory, args.expanding_dataset_dir)
            if working_directory
            else "./data/downloads"
        )
    else:
        default_output = (
            os.path.join(working_directory, "census_downloads")
            if working_directory
            else "./census_downloads"
        )

    if args.census_output_dir:
        output_dir = (
            os.path.join(working_directory, args.census_output_dir)
            if working_directory and not os.path.isabs(args.census_output_dir)
            else args.census_output_dir
        )
    else:
        output_dir = default_output

    os.makedirs(output_dir, exist_ok=True)

    return CensusDataAgentConfig(
        agent_name="census_data_agent",
        tag="census",
        output_dir=output_dir,
        log_dir="logs",
    )


def set_opentargets_agent_config(args, working_directory):
    """Configure the OpenTargetsAgent for drug discovery and target validation."""
    try:
        from AIR.tool_agents.specialized.opentargets_agent import OpenTargetsAgentConfig
    except ImportError as exc:
        raise ImportError(
            "OpenTargets agent requested but its optional dependencies are not installed. "
            "Install them with `uv sync --extra opentargets` or "
            "`pip install -e \".[opentargets]\"`."
        ) from exc

    if args.opentargets_output_dir:
        output_dir = (
            os.path.join(working_directory, args.opentargets_output_dir)
            if working_directory and not os.path.isabs(args.opentargets_output_dir)
            else args.opentargets_output_dir
        )
    elif getattr(args, "expanding_dataset", False):
        output_dir = (
            os.path.join(working_directory, args.expanding_dataset_dir)
            if working_directory
            else "./data/downloads"
        )
    else:
        output_dir = (
            os.path.join(working_directory, "opentargets_downloads")
            if working_directory
            else "./opentargets_downloads"
        )

    os.makedirs(output_dir, exist_ok=True)

    return OpenTargetsAgentConfig(
        agent_name="opentargets_agent",
        tag="opentargets",
        max_turns=args.opentargets_max_turns,
        model=getattr(args, "opentargets_model", None),
        output_dir=output_dir,
        log_dir="logs",
    )


def set_execution_agent_config(
    args,
    dataset_description,
    working_directory,
    analysis_name,
    codebase_structure_prompt=None,
):
    llm_config = LLMConfig(model_name=args.execution_model, max_tokens=16384)

    execution_max_turns = (
        args.execution_max_turns if args.execution_max_turns is not None else 10
    )

    opentargets_mcp_servers = None
    if (
        hasattr(args, "execution_opentargets_servers")
        and args.execution_opentargets_servers
    ):
        opentargets_mcp_servers = [
            s.strip() for s in args.execution_opentargets_servers.split(",")
        ]

    config_kwargs = {
        "agent_name": EXECUTION_AGENT_TAG,
        "tag": EXECUTION_AGENT_TAG,
        "tool_prompt": EXECUTION_AGENT_PROMPT,
        "llm_config": llm_config,
        "analysis_name": analysis_name,
        "dataset_description": dataset_description,
        "readme_truncate_length": None,
        "working_directory": working_directory,
        "manual_file_tracking": False,
        "max_turns_query_execution": execution_max_turns,
        "separate_report_step": False,
        "disable_file_moves": True,
        "claude_md_generator": getattr(args, "claude_md_generator", False),
        "log_execution_agent": getattr(args, "log_execution_agent", True),
        "execution_skills_path": getattr(args, "execution_skills_path", None),
        "codebase_structure_prompt": codebase_structure_prompt,
        "sub_agent_architecture": getattr(args, "sub_agent_architecture", False),
    }

    no_sandbox_enforcement = getattr(args, "no_sandbox_enforcement", False)
    no_sandbox = getattr(args, "no_sandbox", False)
    config_kwargs["sandbox_enforcement"] = not (no_sandbox_enforcement or no_sandbox)

    allowed_read_paths_raw = getattr(args, "allowed_read_paths", None)
    if allowed_read_paths_raw:
        config_kwargs["allowed_read_paths"] = [
            p.strip() for p in allowed_read_paths_raw.split(",") if p.strip()
        ]

    if hasattr(args, "execution_opentargets_mcp") and args.execution_opentargets_mcp:
        config_kwargs["opentargets_mcp_enabled"] = True
        if opentargets_mcp_servers:
            config_kwargs["opentargets_mcp_servers"] = opentargets_mcp_servers
        if (
            hasattr(args, "execution_opentargets_mcp_path")
            and args.execution_opentargets_mcp_path
        ):
            config_kwargs["opentargets_mcp_path"] = args.execution_opentargets_mcp_path
        if (
            hasattr(args, "execution_opentargets_data_path")
            and args.execution_opentargets_data_path
        ):
            config_kwargs["opentargets_data_path"] = (
                args.execution_opentargets_data_path
            )

    return ExecutionAgentCCConfig(**config_kwargs)
