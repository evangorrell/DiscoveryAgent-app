#!/usr/bin/env python3
"""
Isolated script to generate a background report using DeepResearchAgent.

This script mirrors the `generate_background_report` functionality from
`AIR/director_workflow.py` and can be run standalone.

Usage:
    python scripts/generate_background_report.py \
        --readme_path /path/to/data/README.txt \
        --task_description_path /path/to/task_description.txt \
        --output_path /path/to/output/background_report.md \
        --log_dir /path/to/logs

    # Or auto-resolve from a failed run checkpoint directory:
    python scripts/generate_background_report.py \
        --checkpoint_dir /path/to/logs/<analysis_timestamp>/checkpoints

Example:
    python scripts/generate_background_report.py \
        --readme_path /scratch/users/oqueen/AIR_project/active/ad_data/data/README.txt \
        --task_description_path /scratch/users/oqueen/AIR_project/active/ad_data/task.txt \
        --output_path ./background_report.md
"""

import argparse
import json
import os
import sys
import threading
import itertools
import time
from pathlib import Path
from datetime import datetime

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from AIR.config import LLMConfig
from AIR.director_workflow import _generate_background_report


def _start_spinner(message: str = "Working..."):
    """
    Lightweight CLI spinner to show progress during long-running LLM calls.
    """
    stop_event = threading.Event()

    def _spin():
        for ch in itertools.cycle("|/-\\"):
            if stop_event.is_set():
                break
            print(f"\r{message} {ch}", end="", flush=True)
            time.sleep(0.1)
        # Clear the spinner line when finished
        print("\r", end="", flush=True)

    t = threading.Thread(target=_spin, daemon=True)
    t.start()
    return stop_event, t


def load_file_content(file_path: str) -> str:
    """Load content from a file if it exists, otherwise return None."""
    if file_path and os.path.exists(file_path):
        try:
            with open(file_path, 'r') as f:
                return f.read()
        except Exception as e:
            print(f"Warning: Could not read file {file_path}: {e}")
            return None
    return None


def load_checkpoint_config(checkpoint_dir: str) -> tuple[dict, Path]:
    """
    Load run configuration from a checkpoint directory or config file.

    Supports:
    - checkpoint base dir containing config.yaml/config.yml/config.json
    - round dir containing config.json
    - direct path to one of the config files above
    """
    target = Path(checkpoint_dir).expanduser().resolve()

    if target.is_file():
        config_path = target
    elif target.is_dir():
        candidates = [
            target / "config.yaml",
            target / "config.yml",
            target / "config.json",
        ]
        # Also support passing round_X directory directly
        if target.name.startswith("round_"):
            candidates.append(target / "config.json")

        config_path = next((p for p in candidates if p.exists()), None)
        if config_path is None:
            raise FileNotFoundError(
                f"No config file found in {target}. Expected config.yaml, config.yml, or config.json."
            )
    else:
        raise FileNotFoundError(f"Checkpoint path does not exist: {target}")

    if config_path.suffix in {".yaml", ".yml"}:
        try:
            yaml = __import__("yaml")
        except ImportError as e:
            raise ImportError(
                "PyYAML is required to read config.yaml files. "
                "Install with `pip install pyyaml` or pass a JSON config file."
            ) from e
        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}
    elif config_path.suffix == ".json":
        with open(config_path, "r") as f:
            raw = json.load(f)
        # Checkpoint JSON can wrap args under "config"
        config = raw.get("config", raw) if isinstance(raw, dict) else {}
    else:
        raise ValueError(f"Unsupported config extension for {config_path}")

    if not isinstance(config, dict):
        raise ValueError(f"Config file did not parse to a dictionary: {config_path}")

    return config, config_path


def generate_background_report(
    dataset_description: str,
    task_description: str = None,
    background_path: str = None,
    log_dir: str = "logs",
    max_output_tokens: int = 8192,
    summary_llm_config: LLMConfig = None,
) -> str:
    """
    Create a background report using the shared workflow implementation.

    Args:
        dataset_description: Description of the dataset being explored.
        task_description: Task/goal description for the run.
        background_path: Optional directory of prior-work PDFs to summarize instead of running open deep research.
        log_dir: Directory for agent logging/output.
        max_output_tokens: Maximum tokens for output.
        summary_llm_config: Optional LLM config for PDF summarization mode.

    Returns:
        The generated Markdown report as a string.
    """
    return _generate_background_report(
        dataset_description=dataset_description,
        task_description=task_description,
        background_path=background_path,
        log_dir=log_dir,
        summary_llm_config=summary_llm_config,
        max_output_tokens=max_output_tokens,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Generate a background report from dataset/task context or summarize supplied prior-work PDFs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument(
        "--readme_path",
        type=str,
        default=None,
        help="Path to the data README file (dataset description)"
    )
    parser.add_argument(
        "--checkpoint_dir",
        type=str,
        default=None,
        help="Checkpoint directory (or config file) to auto-resolve README/task/log/output paths"
    )
    parser.add_argument(
        "--task_description_path",
        type=str,
        default=None,
        help="Path to the task description file (optional)"
    )
    parser.add_argument(
        "--background_path",
        type=str,
        default=None,
        help="Path to prior-work PDFs. If provided, the report will summarize only these PDFs."
    )
    parser.add_argument(
        "--output_path",
        type=str,
        default=None,
        help="Path to save the generated report. Defaults to ./background_report_{timestamp}.md"
    )
    parser.add_argument(
        "--log_dir",
        type=str,
        default=None,
        help="Directory for agent logs. In checkpoint mode defaults to config log_dir; otherwise logs"
    )
    parser.add_argument(
        "--max_output_tokens",
        type=int,
        default=8192,
        help="Maximum output tokens for the report (default: 8192)"
    )
    parser.add_argument(
        "--datapath_placeholder",
        type=str,
        default=None,
        help="Value to substitute for {datapath} placeholder in readme/task files"
    )

    args = parser.parse_args()

    if not args.readme_path and not args.checkpoint_dir:
        parser.error("Either --readme_path or --checkpoint_dir must be provided.")

    resolved_log_dir = args.log_dir
    resolved_readme_path = args.readme_path
    resolved_task_description_path = args.task_description_path
    resolved_datapath_placeholder = args.datapath_placeholder
    resolved_output_path = Path(args.output_path) if args.output_path else None

    if args.checkpoint_dir:
        config, config_path = load_checkpoint_config(args.checkpoint_dir)
        working_directory = config.get("working_directory")

        if resolved_readme_path is None:
            resolved_readme_path = config.get("readme_path")
            if resolved_readme_path is None and working_directory:
                resolved_readme_path = os.path.join(working_directory, "data", "README.txt")

        if resolved_task_description_path is None:
            resolved_task_description_path = config.get("task_description_file")

        if resolved_log_dir is None:
            resolved_log_dir = config.get("log_dir")

        if resolved_datapath_placeholder is None and working_directory:
            resolved_datapath_placeholder = os.path.join(working_directory, "data")

        if resolved_output_path is None and resolved_log_dir:
            resolved_output_path = Path(resolved_log_dir) / "deep_research_background.md"

        print(f"✓ Loaded checkpoint config from: {config_path}")
        if working_directory:
            print(f"  working_directory: {working_directory}")

    if resolved_log_dir is None:
        resolved_log_dir = "logs"

    if resolved_readme_path is None:
        print("❌ Error: Could not resolve README path. Provide --readme_path explicitly.")
        sys.exit(1)

    # Load dataset description
    print(f"\n{'=' * 80}")
    print("📄 Loading Dataset Description")
    print(f"{'=' * 80}")

    dataset_description = load_file_content(resolved_readme_path)
    if not dataset_description:
        print(f"❌ Error: Could not load dataset description from {resolved_readme_path}")
        sys.exit(1)

    # Replace datapath placeholder if provided
    if resolved_datapath_placeholder:
        dataset_description = dataset_description.replace("{datapath}", resolved_datapath_placeholder)

    print(f"✓ Loaded dataset description from: {resolved_readme_path}")
    print(f"  Size: {len(dataset_description)} characters")

    # Load task description if provided
    task_description = None
    if resolved_task_description_path:
        task_description = load_file_content(resolved_task_description_path)
        if task_description:
            if resolved_datapath_placeholder:
                task_description = task_description.replace("{datapath}", resolved_datapath_placeholder)
            print(f"✓ Loaded task description from: {resolved_task_description_path}")
            print(f"  Size: {len(task_description)} characters")
        else:
            print(f"⚠️  Warning: Could not load task description from {resolved_task_description_path}")

    # Set output path
    if resolved_output_path:
        output_path = resolved_output_path
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = Path(f"background_report_{timestamp}.md")

    # Ensure log directory exists
    os.makedirs(resolved_log_dir, exist_ok=True)

    # Generate the report
    print(f"\n{'=' * 80}")
    print("🔎 Generating Background Report")
    print(f"{'=' * 80}")
    print("Mode: PDF-only summary" if args.background_path else "Mode: open deep research")
    print(f"Max output tokens: {args.max_output_tokens}")
    print(f"Log directory: {resolved_log_dir}")
    print()

    spinner_stop, spinner_thread = _start_spinner("🔎 Generating background report...")

    try:
        report = generate_background_report(
            dataset_description=dataset_description,
            task_description=task_description,
            background_path=args.background_path,
            log_dir=resolved_log_dir,
            max_output_tokens=args.max_output_tokens,
        )
    finally:
        spinner_stop.set()
        spinner_thread.join()

    # Save the report
    print(f"\n{'=' * 80}")
    print("💾 Saving Report")
    print(f"{'=' * 80}")

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_path.write_text(report)
    print(f"✓ Report saved to: {output_path}")
    print(f"  Size: {len(report)} characters")

    # Print preview
    print(f"\n{'=' * 80}")
    print("📝 Report Preview (first 500 chars)")
    print(f"{'=' * 80}")
    print(report[:500] + ("..." if len(report) > 500 else ""))

    print(f"\n{'=' * 80}")
    print("✅ Background report generation complete!")
    print(f"{'=' * 80}\n")


if __name__ == "__main__":
    main()
