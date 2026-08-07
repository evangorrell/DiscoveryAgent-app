import argparse
import asyncio
from pathlib import Path
import sys
import shutil
import os

# Resolve repo root from scripts/papers/ location.
project_root = Path(__file__).resolve().parents[2]

# Try to import the report_writing_agent package
try:
    from report_writer import run_generation
except ImportError:
    print("report_writing_agent not installed. Please install with:", file=sys.stderr)
    print("  pip install report-writing-agent", file=sys.stderr)
    print("  # or from a local clone:", file=sys.stderr)
    print("  cd /path/to/report_writing_agent && pip install -e .", file=sys.stderr)
    sys.exit(1)


def extract_generated_files_from_dir(research_paper_dir: Path):
    """
    Extract list of generated files from the research_paper directory.
    Since the multi-agent pipeline generates files directly, we can scan the directory.
    """
    files = []
    if research_paper_dir.exists():
        # Get the main research paper
        research_paper_tex = research_paper_dir / "research_paper.tex"
        if research_paper_tex.exists():
            files.append(str(research_paper_tex))

        # Get images
        images_dir = research_paper_dir / "images"
        if images_dir.exists():
            for img in images_dir.iterdir():
                if img.is_file():
                    files.append(str(img))

    # Get intermediate agent outputs in the working directory
    working_dir = research_paper_dir.parent
    intermediate_files = [
        "narrative_analysis_report.md",
        "figure_audit_report.md",
        "figure_manifest.md",
        "methods_details.md",
        "results_extraction.md"
    ]

    for fname in intermediate_files:
        fpath = working_dir / fname
        if fpath.exists():
            files.append(str(fpath))

    return files


def main():
    parser = argparse.ArgumentParser(
        description="Generate a research paper using report_writing_agent multi-agent pipeline"
    )
    parser.add_argument(
        "--analysis_dir",
        help="Path to the analysis directory (must contain final_report.md)",
        required=True
    )
    parser.add_argument(
        "--template_dir_path",
        help="Path to the LaTeX template directory to copy into the analysis directory as 'research_paper'",
        default=str(project_root / "report_templates" / "neurips_template")
    )
    parser.add_argument(
        "--max_turns",
        type=int,
        default=12,
        dest="max_turns",
        help="Maximum interaction turns per agent"
    )
    parser.add_argument(
        "--model",
        help="Claude model to use",
        default="claude-sonnet-4-5-20250929"
    )
    parser.add_argument(
        "--encourage_replotting",
        action="store_true",
        help="Encourage the agent to replot figures if necessary (kept for compatibility)"
    )
    args = parser.parse_args()

    print("="*80)
    print("REPORT WRITING AGENT - Multi-Agent Paper Generation")
    print("="*80)
    print(f"\nConfiguration:")
    print(f"  Analysis directory: {args.analysis_dir}")
    print(f"  Template directory: {args.template_dir_path}")
    print(f"  Max turns per agent: {args.max_turns}")
    print(f"  Claude model: {args.model}")
    print(f"  Encourage replotting: {args.encourage_replotting}")
    print()

    # Validate working directory
    working_dir = Path(args.analysis_dir).expanduser().resolve()
    if not working_dir.exists() or not working_dir.is_dir():
        print(f"Error: directory not found: {working_dir}", file=sys.stderr)
        sys.exit(2)

    # Validate template directory
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

    # Optional sanity checks
    final_report = working_dir / "final_report.md"
    template_tex = dest_template / "neurips_2024.tex"
    if not final_report.exists():
        print(f"Warning: {final_report} not found. The agent will attempt to locate or create necessary inputs.")
    if not template_tex.exists():
        print(f"Warning: {template_tex} not found in copied template. Ensure the LaTeX template exists for best results.")

    # Check for ANTHROPIC_API_KEY
    if not os.getenv('ANTHROPIC_API_KEY'):
        print("\nError: ANTHROPIC_API_KEY environment variable is not set.", file=sys.stderr)
        print("Please set it with: export ANTHROPIC_API_KEY='your_api_key_here'", file=sys.stderr)
        sys.exit(1)

    print("\nStarting multi-agent pipeline...")
    print("This will use 6 specialized agents:")
    print("  1. Results Relevancy Analyst - Narrative construction")
    print("  2. Figure Diagnostician - Figure quality assessment")
    print("  3. Plot Engineer - Publication-quality figure generation")
    print("  4. Methods Analyst - Methodological documentation")
    print("  5. Results Analyst - Experimental results extraction")
    print("  6. LaTeX Writer - Manuscript synthesis")
    print()

    # Run the multi-agent pipeline
    try:
        raw_output = asyncio.run(
            run_generation(
                working_dir=working_dir,
                max_turns=args.max_turns,
                model=args.model,
                encourage_replotting=args.encourage_replotting
            )
        )
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        sys.exit(130)
    except Exception as e:
        print(f"\nError during pipeline execution: {e}", file=sys.stderr)
        sys.exit(1)

    # Extract and display generated files
    print("\n" + "="*80)
    print("Pipeline Complete!")
    print("="*80)

    files = extract_generated_files_from_dir(dest_template)
    if files:
        print("\nGenerated files:")
        for f in files:
            print(f"  - {f}")
    else:
        print("\nNo generated files detected in expected locations.")
        print("Review the console output above for details.")

    print(f"\nMain output: {dest_template / 'research_paper.tex'}")
    print(f"To compile: cd {dest_template} && pdflatex research_paper.tex")


if __name__ == "__main__":
    main()
