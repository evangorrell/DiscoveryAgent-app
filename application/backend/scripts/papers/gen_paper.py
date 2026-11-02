import argparse
import asyncio
from pathlib import Path
import sys
import re
import shutil
import os

# Configure Claude Code CLI path BEFORE importing SDK
# This script lives in scripts/papers/, so repo root is two levels up.
project_root = Path(__file__).resolve().parents[2]
claude_cli_path = project_root / "node_modules/@anthropic-ai/claude-code/cli.js"

# Set environment variables
os.environ['CLAUDE_CODE_CLI_PATH'] = str(claude_cli_path)
os.environ['NODE_PATH'] = str(project_root / "node_modules")
os.environ["MAX_MCP_OUTPUT_TOKENS"] = "50000"

# Verify the CLI path exists
if not claude_cli_path.exists():
    print(f"Warning: Claude Code CLI not found at {claude_cli_path}")
    print(f"Please ensure Claude Code is installed with: npm install @anthropic-ai/claude-code")

try:
    from claude_agent_sdk import query, ClaudeAgentOptions
except ImportError:
    print("claude-code-sdk not installed. Please install with: pip install claude-code-sdk", file=sys.stderr)
    sys.exit(1)


def load_skill_from_directory(skill_dir: Path) -> tuple[str, list[str]]:
    """
    Load a skill from a directory containing SKILL.md and supporting files.

    Returns:
        tuple: (skill_content, list of other file paths in the directory)
    """
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        raise FileNotFoundError(f"SKILL.md not found in {skill_dir}")

    skill_content = skill_file.read_text()

    # Collect all other files in the directory (recursively)
    other_files = []
    for path in skill_dir.rglob("*"):
        if path.is_file() and path.name != "SKILL.md":
            other_files.append(str(path))

    return skill_content, other_files


def build_skill_based_system_prompt(working_directory: Path, skill_dir: Path) -> str:
    """
    Build a minimal system prompt when a skill directory is provided.
    All instructions come from the skill itself.
    """
    skill_content, reference_files = load_skill_from_directory(skill_dir)

    reference_section = ""
    if reference_files:
        reference_section = "\n\n### Reference Files Available\n\nThe skill directory contains the following reference files that you can read using the Read tool:\n"
        for f in reference_files:
            reference_section += f"- {f}\n"
        reference_section += "\nConsult these reference files as needed when following the skill instructions."

    return f"""You are Claude Code, an autonomous coding and analysis system.

Working directory: {working_directory}

You have access to tools: Read, Write, Bash, Grep, Glob

File safety: Do NOT delete or overwrite existing data files. Prefer creating new files.

---

# Skill Instructions

{skill_content}
{reference_section}
"""


def build_legacy_system_prompt(working_directory: Path) -> str:
    """
    Build the original detailed system prompt for backward compatibility
    when no skill directory is provided.
    """
    ref_command = r"\ref{}"

    return f"""
You are Claude Code, an autonomous coding and analysis system with full repo/file access.

Your environment:
- Working directory: {working_directory}
- You may use tools: Read, Write, Bash
- You can create files and directories, and write LaTeX/Markdown/CSV/PNG outputs
- DO NOT assume the existence of any files that you do not see in your working directory. 

Critical file safety rules:
- Do NOT delete or overwrite existing data files. Prefer creating new files.
- Prefer copying rather than moving existing figures unless strictly necessary for the deliverable structure.
- Use descriptive filenames with timestamps to avoid conflicts.
- Always save generated artifacts within the provided working directory, in appropriate subfolders.

Deliverable requirements:
- At the end of your work, append a section titled "## Generated Files" that lists ALL created files with absolute paths, one per line prefixed by "- ".
- Save plots and images to files (do not rely on display). Ensure referenced images exist on disk.

Guidelines for figures:
- Make sure to view each figure before adding them to the paper. The descriptions of the figures may be wrong, and you should verify their claims before adding them to the paper.
- Add only the figures that are most important and can be used to exhibit the findings in the paper.
- When possible, attempt to cite the figures in text, using the {ref_command} command.

Guidelines for supplementary materials:
- You should write a detailed supplementary materials section in the paper that details exact tests and methods for analyses mentioned in the paper.
- This should contain as many figures as you see fit, and you should be exact in your details.

Iterative writing:
- You should iterate multiple times on the paper. Write it and then review it, improving each time.

IMPORTANT: DO NOT try to compile the LaTex file, that will be done externally. Focus only on writing the paper in the file and ensuring that the formatting is correct.
"""


# Minimal user prompt when using skill-based mode
SKILL_BASED_USER_PROMPT = """
Analyze the data and reports in this directory and generate documentation following the skill instructions provided in the system prompt.
Start by exploring the directory to understand what data and reports are available.
You are to write your report following the instructions provided to you already and the templates in the `research_paper` directory. 

IMPORTANT: Only write in the research_paper directory, do not put any files elsewhere.
IMPORTANT: Do not assume the existence of any external files that you do not see in the research_paper directory. You are to create this directory such that it can be compiled as a standalone LaTex project.
""".strip()

# Legacy user prompt for backward compatibility (neurips template)
LEGACY_USER_PROMPT = """
Take the report written in "final_report.md" and write a research paper based off of the data stored from
the analyses in this directory. You should output your research report by following the LaTex template at
research_paper/neurips_2024.tex. Focus on compiling a narrative from the results - your output should be a
research paper that will be read and interpreted by humans. Use at least 5 figures in your writeup, and move any relevant
figures into the research_paper/images directory.

IMPORTANT: Only write in the research_paper directory, do not put any files elsewhere.
IMPORTANT: Your research paper must be written in a new file based on the neurips_2024.tex template. Call this new file "research_paper.tex".
""".strip()


def parse_stream_message(message) -> str:
    """Format Claude Code streamed messages for readable stdout."""
    msg_str = str(message)
    try:
        if "SystemMessage" in msg_str and "subtype='init'" in msg_str:
            return "Initializing Claude Code session..."
        if "AssistantMessage" in msg_str and "TextBlock" in msg_str:
            m = re.search(r'TextBlock\(text="([^"]*)"', msg_str) or re.search(r"TextBlock\(text='([^']*)'", msg_str)
            if m:
                text = m.group(1).replace('\\n', '\n').replace('\\"', '"').replace("\\'", "'")
                return text[:400] + ("..." if len(text) > 400 else "")
            return "[Assistant]"
        if "AssistantMessage" in msg_str and "ToolUseBlock" in msg_str:
            tool = re.search(r"name='([^']*)'", msg_str) or re.search(r'name="([^"]*)"', msg_str)
            tool_name = tool.group(1) if tool else "tool"
            desc = re.search(r"'description': '([^']*)'", msg_str) or re.search(r'"description": "([^"]*)"', msg_str)
            return f"Using {tool_name}: {desc.group(1) if desc else ''}"
        if "UserMessage" in msg_str and "ToolResultBlock" in msg_str:
            content = re.search(r"content='([^']*)'", msg_str) or re.search(r'content="([^"]*)"', msg_str)
            if content:
                c = content.group(1).replace('\\n', ' ')
                return f"Tool result: {c[:300]}{'...' if len(c)>300 else ''}"
            return "[Tool result]"
    except Exception:
        pass
    return "[Message update]"


async def run_generation(working_dir: Path, max_turns: int, model: str = None, encourage_replotting: bool = False, skill_dir: Path = None) -> str:
    # Check if ANTHROPIC_API_KEY is set
    if not os.getenv('ANTHROPIC_API_KEY'):
        print("Warning: ANTHROPIC_API_KEY environment variable is not set.")
        print("Please set it with: export ANTHROPIC_API_KEY='your_api_key_here'")
        print("Or ensure it's available in your environment.")

    # Choose prompts based on whether skill_dir is provided
    if skill_dir:
        # Skill-based mode: minimal prompts, rely on skill for instructions
        system_prompt = build_skill_based_system_prompt(working_dir, skill_dir)
        user_prompt = SKILL_BASED_USER_PROMPT
        print("Using skill-based mode with minimal prompts")
    else:
        # Legacy mode: detailed prompts for neurips template
        system_prompt = build_legacy_system_prompt(working_dir)
        user_prompt = LEGACY_USER_PROMPT
        print("Using legacy mode with neurips template prompts")

    if encourage_replotting:
        system_prompt += """\n\nIMPORTANT: Before including any plots in the final report, you're encouraged to view these reports.
If the reports have errors or the visualization could be improved, you're encouraged to regenerate the plots.
If you decide to regenerate any plots, you should put this in the replotting/ directory in the main directory.
Do not modify any of the data, but rather only replot or regenerate the figures with some alternative parameters to improve visualization."""

    options = ClaudeAgentOptions(
        max_turns=max_turns,
        max_buffer_size=int(os.getenv("CLAUDE_CODE_MAX_BUFFER_SIZE", str(50 * 1024 * 1024))),
        system_prompt=system_prompt,
        cwd=working_dir,
        allowed_tools=["Read", "Write", "Bash", "Grep", "Glob"],
        permission_mode="acceptEdits",
        model=model
    )

    print(f"Running Claude Code in: {working_dir}")
    print(f"Max turns: {max_turns}")
    print("--- Streaming progress ---")

    messages = []
    async for msg in query(prompt=user_prompt, options=options):
        messages.append(str(msg))
        pretty = parse_stream_message(msg)
        if pretty:
            print(pretty)

    print("--- Run complete ---\n")
    return "\n".join(messages)


def extract_generated_files(raw_output: str):
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


def main():
    parser = argparse.ArgumentParser(description="Generate a research paper in a directory using Claude Code")
    parser.add_argument("--analysis_dir", help="Path to the analysis directory (must contain final_report.md)", required=True)
    parser.add_argument("--template_dir_path", help="Path to the LaTeX template directory to copy into the analysis directory as 'research_paper'", 
        default = str(project_root / "report_templates" / "neurips_template"))
    parser.add_argument("--max_turns", type=int, default=12, dest="max_turns", help="Maximum interaction turns")
    parser.add_argument("--model", help="Claude model to use", default="claude-sonnet-4-5-20250929")
    parser.add_argument("--encourage_replotting", action="store_true", help="Encourage the agent to replot figures if necessary")
    parser.add_argument("--skill-dir-path", dest="skill_dir_path", help="Path to a directory containing SKILL.md and supporting reference files")
    args = parser.parse_args()

    print(f"Configuration:")
    print(f"  Analysis directory: {args.analysis_dir}")
    print(f"  Template directory: {args.template_dir_path}")
    print(f"  Max turns: {args.max_turns}")
    print(f"  Claude model: {args.model}")
    print(f"  Encourage replotting: {args.encourage_replotting}")
    print(f"  Skill directory: {args.skill_dir_path}")
    print()

    working_dir = Path(args.analysis_dir).expanduser().resolve()
    if not working_dir.exists() or not working_dir.is_dir():
        print(f"Error: directory not found: {working_dir}", file=sys.stderr)
        sys.exit(2)

    template_src = Path(args.template_dir_path).expanduser().resolve()
    if not template_src.exists() or not template_src.is_dir():
        print(f"Error: template_dir_path not found or not a directory: {template_src}", file=sys.stderr)
        sys.exit(2)

    # Copy template into working directory as research_paper
    dest_template = working_dir / "research_paper"
    if dest_template.exists():
        print(f"Info: {dest_template} already exists. Skipping copy of template directory.")
    else:
        try:
            shutil.copytree(template_src, dest_template)
            print(f"Copied template directory from {template_src} to {dest_template}")
        except Exception as e:
            print(f"Error copying template directory: {e}", file=sys.stderr)
            sys.exit(1)

    # Ensure images subfolder exists so moves don't fail
    images_dir = dest_template / "images"
    try:
        images_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    # Validate skill directory if provided
    skill_dir = None
    if args.skill_dir_path:
        skill_dir = Path(args.skill_dir_path).expanduser().resolve()
        if not skill_dir.exists() or not skill_dir.is_dir():
            print(f"Error: skill directory not found: {skill_dir}", file=sys.stderr)
            sys.exit(2)
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            print(f"Error: SKILL.md not found in skill directory: {skill_dir}", file=sys.stderr)
            sys.exit(2)
        print(f"Loaded skill from: {skill_dir}")
    else:
        # Legacy mode sanity checks (only when not using skill-based mode)
        final_report = working_dir / "final_report.md"
        template_tex = dest_template / "neurips_2024.tex"
        if not final_report.exists():
            print(f"Warning: {final_report} not found. The agent will attempt to locate or create necessary inputs.")
        if not template_tex.exists():
            print(f"Warning: {template_tex} not found in copied template. Ensure the LaTeX template exists for best results.")

    try:
        raw = asyncio.run(run_generation(working_dir, args.max_turns, args.model, args.encourage_replotting, skill_dir=skill_dir))
    except KeyboardInterrupt:
        print("Interrupted by user.")
        sys.exit(130)

    files = extract_generated_files(raw)
    if files:
        print("Generated files:")
        for f in files:
            print(f"- {f}")
    else:
        print("No generated files block detected. Review the console output above for details.")


if __name__ == "__main__":
    main()
