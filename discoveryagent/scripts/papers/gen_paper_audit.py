#!/usr/bin/env python3
"""
Generate a research paper from a DiscoveryAgent project using the multi-agent
writing system.

Takes a project prefix (like scripts/utils/check_progress.py) and launches an
orchestrator agent with checkpoint-reviewer and code-reviewer subagents that
gather research information, then write a LaTeX paper.

The paper is written to a timestamped directory at the project's log level:
    DATA_PATH/LOGS/{project_prefix}/research_paper_{timestamp}/

Usage:
    # Using project prefix (resolves checkpoint and code dirs automatically):
    python scripts/papers/gen_paper_audit.py --project-prefix xray_data/xray_select_q10_20260108_090041

    # Using explicit paths:
    python scripts/papers/gen_paper_audit.py \\
        --checkpoint-dir /path/to/checkpoints \\
        --code-dir /path/to/project

    # With custom template and model:
    python scripts/papers/gen_paper_audit.py \\
        --project-prefix dataset/run_name \\
        --template-dir-path /path/to/template \\
        --model claude-opus-4-6 \\
        --max-turns 80
"""

import argparse
import asyncio
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# SDK bootstrap — configure CLI path before importing the SDK
# ---------------------------------------------------------------------------
# This script lives in scripts/papers/, so repo root is two levels up.
project_root = Path(__file__).resolve().parents[2]
claude_cli_path = project_root / "node_modules/@anthropic-ai/claude-code/cli.js"

# Only set SDK runtime env vars if they are unset and local paths exist.
# This avoids forcing stale/nonexistent CLI paths that can break subagents.
if not os.getenv("CLAUDE_CODE_CLI_PATH") and claude_cli_path.exists():
    os.environ["CLAUDE_CODE_CLI_PATH"] = str(claude_cli_path)

node_modules_path = project_root / "node_modules"
if not os.getenv("NODE_PATH") and node_modules_path.exists():
    os.environ["NODE_PATH"] = str(node_modules_path)

os.environ.setdefault("MAX_MCP_OUTPUT_TOKENS", "50000")

configured_cli = os.getenv("CLAUDE_CODE_CLI_PATH")
if configured_cli and not Path(configured_cli).exists():
    print(f"Warning: CLAUDE_CODE_CLI_PATH points to a missing file: {configured_cli}")
elif not configured_cli and not claude_cli_path.exists():
    print(f"Warning: Claude Code CLI not found at {claude_cli_path}")
    print("Please ensure Claude Code is installed with: npm install @anthropic-ai/claude-code")

# Add project root to path so we can import AIR modules
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(project_root / ".env", override=True)

from AIR.writing_agent.agent import run_writing_agent, extract_generated_files

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATA_PATH = os.getenv("DATA_PATH", "/scratch/users/oqueen/AIR_project/active")


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def resolve_project_paths(project_prefix: str) -> tuple:
    """
    Resolve checkpoint dir, code dir, and log dir from a project prefix.

    Layout:
        DATA_PATH/LOGS/{project_prefix}/checkpoints/   -> checkpoint trace
        DATA_PATH/{project_prefix}/                     -> project outputs (code, data, figures)
        DATA_PATH/LOGS/{project_prefix}/                -> log dir (paper output goes here)

    Returns:
        (checkpoint_dir, code_dir, log_dir)
    """
    log_dir = Path(DATA_PATH) / "LOGS" / project_prefix
    checkpoint_dir = log_dir / "checkpoints"
    code_dir = Path(DATA_PATH) / project_prefix
    return checkpoint_dir, code_dir, log_dir


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Generate a research paper from a DiscoveryAgent project "
        "using the multi-agent writing system.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/papers/gen_paper_audit.py --project-prefix xray_data/run_name\n"
            "  python scripts/papers/gen_paper_audit.py --checkpoint-dir /path/to/checkpoints\n"
        ),
    )

    # --- Project identification ---
    parser.add_argument(
        "--project-prefix",
        dest="project_prefix",
        help="Project prefix (e.g. dataset_name/run_dir_name). "
        "Resolves checkpoint and code dirs automatically via DATA_PATH.",
    )
    parser.add_argument(
        "--checkpoint-dir",
        dest="checkpoint_dir",
        help="Explicit path to the checkpoints directory "
        "(overrides project-prefix resolution).",
    )
    parser.add_argument(
        "--code-dir",
        dest="code_dir",
        help="Explicit path to the project/code directory "
        "(overrides project-prefix resolution).",
    )

    # --- Template and output ---
    parser.add_argument(
        "--template-dir-path",
        dest="template_dir_path",
        default=str(project_root / "report_templates" / "neurips_template"),
        help="Path to the LaTeX template directory to copy into the output "
        "(default: neurips_template).",
    )
    parser.add_argument(
        "--output-dir",
        dest="output_dir",
        help="Explicit output directory. If not set, creates "
        "research_paper_{timestamp} under the project log dir.",
    )

    # --- Agent configuration ---
    parser.add_argument(
        "--max-turns",
        type=int,
        default=100,
        dest="max_turns",
        help="Maximum agent interaction turns (default: 50).",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        help="Claude model to use (default: claude-sonnet-4-6).",
    )
    parser.add_argument(
        "--encourage-replotting",
        action="store_true",
        dest="encourage_replotting",
        help="Encourage the agent to regenerate plots if needed.",
    )

    args = parser.parse_args()

    # ---- Resolve paths ----
    if args.project_prefix:
        auto_ckpt, auto_code, log_dir = resolve_project_paths(args.project_prefix)
        checkpoint_dir = (
            Path(args.checkpoint_dir).expanduser().resolve()
            if args.checkpoint_dir
            else auto_ckpt
        )
        code_dir = (
            Path(args.code_dir).expanduser().resolve()
            if args.code_dir
            else auto_code
        )
    elif args.checkpoint_dir:
        checkpoint_dir = Path(args.checkpoint_dir).expanduser().resolve()
        code_dir = (
            Path(args.code_dir).expanduser().resolve() if args.code_dir else None
        )
        log_dir = checkpoint_dir.parent
    else:
        print(
            "Error: must provide either --project-prefix or --checkpoint-dir",
            file=sys.stderr,
        )
        sys.exit(1)

    # ---- Validate paths ----
    if not checkpoint_dir.exists():
        print(
            f"Error: checkpoint directory not found: {checkpoint_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    if code_dir and not code_dir.exists():
        print(
            f"Warning: code directory not found: {code_dir}. "
            "Proceeding without code review."
        )
        code_dir = None

    # ---- Create timestamped output directory ----
    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser().resolve()
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = log_dir / f"research_paper_{timestamp}"

    output_dir.mkdir(parents=True, exist_ok=True)

    # ---- Copy template into output directory ----
    template_src = Path(args.template_dir_path).expanduser().resolve()
    if not template_src.exists():
        print(
            f"Error: template directory not found: {template_src}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Copy each item from the template into the output dir (skip existing)
    for item in template_src.iterdir():
        dest = output_dir / item.name
        if not dest.exists():
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

    # Ensure images directory exists
    (output_dir / "images").mkdir(parents=True, exist_ok=True)

    # ---- Build template instructions ----
    template_tex_files = list(output_dir.glob("*.tex"))
    if template_tex_files:
        template_names = ", ".join(f.name for f in template_tex_files)
        template_instructions = (
            f"LaTeX template file(s) available in your working directory: {template_names}\n"
            "Write your paper based on the template structure. Create a new file called\n"
            "'research_paper.tex' based on the template. Place figures in the images/\n"
            "subdirectory."
        )
    else:
        template_instructions = (
            "No LaTeX template found. Write the paper as a well-structured LaTeX\n"
            "document called 'research_paper.tex'."
        )

    # ---- Print configuration ----
    print("Configuration:")
    print(f"  Project prefix:      {args.project_prefix or 'N/A'}")
    print(f"  Checkpoint dir:      {checkpoint_dir}")
    print(f"  Code dir:            {code_dir or 'N/A'}")
    print(f"  Output dir:          {output_dir}")
    print(f"  Template:            {template_src}")
    print(f"  Model:               {args.model}")
    print(f"  Max turns:           {args.max_turns}")
    print(f"  Encourage replotting: {args.encourage_replotting}")
    print()

    # ---- Run the writing agent ----
    try:
        raw = asyncio.run(
            run_writing_agent(
                checkpoint_dir=checkpoint_dir,
                output_dir=output_dir,
                code_dir=code_dir,
                model=args.model,
                max_turns=args.max_turns,
                template_instructions=template_instructions,
                encourage_replotting=args.encourage_replotting,
            )
        )
    except KeyboardInterrupt:
        print("Interrupted by user.")
        sys.exit(130)

    # ---- Convert LaTeX to Markdown ----
    tex_path = output_dir / "research_paper.tex"
    if tex_path.exists():
        try:
            import pypandoc

            md_path = output_dir / "research_paper.md"
            pypandoc.convert_file(
                str(tex_path), "md", outputfile=str(md_path)
            )
            print(f"Converted LaTeX to Markdown: {md_path}")
        except ImportError:
            print(
                "Warning: pypandoc not installed, skipping LaTeX -> Markdown "
                "conversion. Install with: pip install pypandoc_binary"
            )
        except Exception as e:
            print(f"Warning: LaTeX -> Markdown conversion failed: {e}")
    else:
        print(
            f"Warning: {tex_path} not found, skipping Markdown conversion."
        )

    # ---- Report results ----
    files = extract_generated_files(raw)
    if files:
        print("Generated files:")
        for f in files:
            print(f"  - {f}")
    else:
        print(
            "No generated files block detected. Check the output directory:"
        )
        print(f"  {output_dir}")


if __name__ == "__main__":
    main()
