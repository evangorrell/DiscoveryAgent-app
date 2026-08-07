"""
CLAUDE.md generator for the interactive research CLI.

Builds a context file that tells Claude Code about the research task, available
MCP tools, and workflow guidance so the scientist gets an effective assistant.
"""

from typing import List, Dict, Optional


# Tool descriptions for the CLAUDE.md - adapted from tool_agent_prompt_generator()
TOOL_DESCRIPTIONS = {
    "execute_analysis": {
        "name": "execute_analysis",
        "description": (
            "Run data analysis and code execution on the dataset. "
            "Pass a detailed query describing the experiments, statistical tests, "
            "or visualizations you want. The execution agent has full access to "
            "the working directory and can read/write files, run Python code, "
            "and generate plots."
        ),
        "when": "Use for any computational analysis, data exploration, statistical testing, or visualization.",
    },
    "deep_research": {
        "name": "deep_research",
        "description": (
            "Perform a deep literature and web research synthesis. "
            "Returns a comprehensive report synthesizing findings from multiple sources."
        ),
        "when": (
            "Use for comprehensive literature reviews, understanding methodological "
            "approaches, or surveying a research area. Expensive - use sparingly."
        ),
    },
    "web_search": {
        "name": "web_search",
        "description": "Perform a quick web search and return a concise summary.",
        "when": "Use for quick factual lookups, finding specific databases, or checking recent publications.",
    },
    "generate_hypothesis": {
        "name": "generate_hypothesis",
        "description": (
            "Generate research hypotheses based on the dataset description, "
            "background knowledge, and task description."
        ),
        "when": "Use at the start of research or when pivoting to explore new directions.",
    },
    "acs_query": {
        "name": "acs_query",
        "description": "Query the American Community Survey (ACS) Census data.",
        "when": "Use when you need demographic or socioeconomic data from the U.S. Census ACS.",
    },
    "census_query": {
        "name": "census_query",
        "description": "Query broad U.S. Census Bureau data (ACS, PUMS, IPUMS).",
        "when": "Use when you need Census data beyond what ACS provides.",
    },
    "opentargets_query": {
        "name": "opentargets_query",
        "description": "Query OpenTargets for drug discovery and target validation data.",
        "when": "Use when you need drug-target associations, disease data, or pharmacological information.",
    },
    "get_research_report": {
        "name": "get_research_report",
        "description": "Retrieve the current accumulated research report.",
        "when": "Use to review what has been discovered so far before planning next steps.",
    },
    "update_research_report": {
        "name": "update_research_report",
        "description": "Manually add findings to the accumulated research report.",
        "when": "Use to record insights or conclusions that weren't captured by automatic report updates.",
    },
    "save_checkpoint": {
        "name": "save_checkpoint",
        "description": "Manually save a checkpoint of the current session state.",
        "when": "Use before major direction changes or when you want to create a restore point.",
    },
}


def build_interactive_claude_md(
    dataset_description: str,
    task_description: Optional[str] = None,
    background_report: Optional[str] = None,
    tool_names: Optional[List[str]] = None,
    working_directory: Optional[str] = None,
    slurm_info: Optional[str] = None,
    accumulated_report: Optional[str] = None,
    log_dir: Optional[str] = None,
) -> str:
    """Build a CLAUDE.md file for the interactive research session.

    Args:
        dataset_description: Contents of the dataset README.
        task_description: Optional research task specification.
        background_report: Optional prior accumulated knowledge.
        tool_names: List of MCP tool names that are available.
        working_directory: Path to the data/working directory.
        slurm_info: Optional SLURM resource information.
        accumulated_report: Optional report from a resumed session.
        log_dir: Path to the log directory.

    Returns:
        The full CLAUDE.md content string.
    """
    sections = []

    # Role
    sections.append(
        "# Interactive Research Assistant\n\n"
        "You are assisting a scientist in conducting interactive research using "
        "the DiscoveryAgent infrastructure. Your role is to help the scientist "
        "plan experiments, execute analyses, synthesize findings, and build a "
        "comprehensive research report.\n\n"
        "## Research Methodology\n\n"
        "- Base conclusions on evidence, not assumptions\n"
        "- Consider alternative explanations for findings\n"
        "- Acknowledge limitations and uncertainties\n"
        "- Distinguish correlation from causation\n"
        "- Quantify findings when possible (statistical tests, effect sizes)\n"
        "- Build on previous findings rather than starting fresh each time\n"
    )

    # Dataset description
    if dataset_description:
        sections.append(
            f"## Dataset Description\n\n{dataset_description}\n"
        )

    # Task description
    if task_description:
        sections.append(
            f"## Research Task\n\n{task_description}\n"
        )

    # Background report
    if background_report:
        sections.append(
            f"## Background Knowledge\n\n"
            f"The following is prior accumulated knowledge from previous research:\n\n"
            f"{background_report}\n"
        )

    # Accumulated report (resume)
    if accumulated_report:
        sections.append(
            f"## Accumulated Findings (Resumed Session)\n\n"
            f"The following findings have been accumulated from the previous session:\n\n"
            f"{accumulated_report}\n"
        )

    # Available MCP tools
    if tool_names:
        tools_section = "## Available Research Tools\n\n"
        tools_section += (
            "You have access to the following MCP tools via the `discoveryagent` server. "
            "Use them to execute analyses, search literature, and manage the research report.\n\n"
        )
        for name in tool_names:
            info = TOOL_DESCRIPTIONS.get(name, {})
            desc = info.get("description", "No description available.")
            when = info.get("when", "")
            tools_section += f"### `{name}`\n\n{desc}\n"
            if when:
                tools_section += f"\n**When to use:** {when}\n"
            tools_section += "\n"
        sections.append(tools_section)

    # SLURM info
    if slurm_info:
        sections.append(f"{slurm_info}\n")

    # Working directory
    if working_directory:
        sections.append(
            f"## Working Directory\n\n"
            f"- Data location: `{working_directory}/data/`\n"
            f"- Output files will be written to: `{working_directory}/`\n"
        )

    if log_dir:
        sections.append(
            f"## Logging\n\n"
            f"- Log directory: `{log_dir}`\n"
            f"- Checkpoints: `{log_dir}/checkpoints/interactive/`\n"
            f"- Execution stream log: `{log_dir}/interactive_stream.log`\n"
        )

    # Workflow guidance
    sections.append(
        "## Workflow Guidance\n\n"
        "1. **Start with understanding**: Review the dataset description and task. "
        "Use `get_research_report` to see any prior findings.\n"
        "2. **Plan before executing**: Discuss the research plan with the scientist "
        "before running expensive tools.\n"
        "3. **Iterate**: Use `execute_analysis` for computational work, "
        "`deep_research` or `web_search` for literature context.\n"
        "4. **Report accumulation**: After each significant analysis, findings are "
        "automatically accumulated into the research report. Use "
        "`get_research_report` to review progress.\n"
        "5. **Checkpoints**: The system auto-checkpoints after each tool call. "
        "Use `save_checkpoint` before major direction changes.\n"
    )

    return "\n".join(sections)
