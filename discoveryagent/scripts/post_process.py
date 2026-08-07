"""
Post-process script for AIR workflow checkpoints.

This script generates summaries for memory_report.md and round_report.md
from the latest round in a checkpoint directory using an LLM.
"""

import os
import sys
import argparse
from pathlib import Path
from typing import Optional, Tuple

import anthropic
from dotenv import load_dotenv

load_dotenv()

DATA_PATH = "/scratch/users/oqueen/AIR_project/active"
CHECKPOINT_PATH = "/scratch/users/oqueen/AIR_project/active/LOGS"

# Summarization prompts
SUMMARIZATION_SYSTEM_PROMPT = """You are an expert research analyst tasked with creating concise summaries of scientific research reports.

Your goal is to distill the key findings, methods, and insights from research reports into clear, actionable summaries that preserve the most important information while significantly reducing length.

Focus on:
1. Key findings and discoveries
2. Important methods or approaches used
3. Significant insights or conclusions
4. Any notable limitations or next steps mentioned

Be concise but comprehensive. Use clear scientific language. Aim for a summary that is roughly 1.5 to 2 pages in length. Do not use salesman language or try to convince the reader of your research.
Your job is to be transparent and provide the reader with a clear and concise view of the research that's been done."""

MEMORY_REPORT_USER_PROMPT = """Please summarize the following memory-integrated research report. This report contains accumulated knowledge synthesized across multiple rounds of research.

For context, here is the dataset description and overall research goal:
---
DATASET & RESEARCH CONTEXT:
{readme}
---

Focus on the main discoveries, methods used, and overall conclusions. Preserve the key scientific insights while condensing the content. Relate findings back to the original research goal where relevant.

---
MEMORY REPORT:
{content}
---

In your summary, choose up to 3 figures that you believe are the most relevant to the central findings and key scientific insights. When including them, copy the reference from the memory report and use the exact same path and markdown formatting.
If you include literature references, you must copy the exact reference from the memory report. DO NOT try to add more references than are already given.

Provide a clear, structured summary:"""

ROUND_REPORT_USER_PROMPT = """Please summarize the following round report. This report contains findings from a single round of research queries.

For context, here is the dataset description and overall research goal:
---
DATASET & RESEARCH CONTEXT:
{readme}
---

Focus on what was discovered in this round, the approaches taken, and key results. Highlight the most important findings and how they relate to the overall research goal.

---
ROUND REPORT:
{content}
---

In your summary, choose up to 3 figures that you believe are the most relevant to the central findings and key scientific insights. When including them, copy the reference from the round report and use the exact same path and markdown formatting.
If you include literature references, you must copy the exact reference from the round report. DO NOT try to add more references than are already given.

Provide a clear, structured summary:"""

ROUND_REPORT_WITH_HYPOTHESIS_USER_PROMPT = """Please summarize the following round report, specifically evaluating whether the hypothesis from the previous round was validated or invalidated.

For context, here is the dataset description and overall research goal:
---
DATASET & RESEARCH CONTEXT:
{readme}
---

The hypothesis being tested was:
---
HYPOTHESIS:
{hypothesis}
---

The research findings from this round are:
---
ROUND REPORT:
{content}
---

Your summary should:
1. **State the hypothesis being tested**: Repeat the hypothesis being tested at the beginning of the report.
2. **Hypothesis Verdict**: Clearly state whether the hypothesis was VALIDATED, INVALIDATED, or PARTIALLY SUPPORTED based on the evidence
3. **Supporting Evidence**: Detail the specific findings that support or contradict the hypothesis
4. **Key Discoveries**: Summarize the most important findings from this round
5. **Unexpected Results**: Note any surprising findings or deviations from expectations
6. **Remaining Questions**: Identify any aspects of the hypothesis that remain untested or unclear

After you clearly explain the hypothesis verdict, detail any other key findings from the round that may not be relevant to the hypothesis but are relevant to the overall research question.
In your summary, choose up to 3 figures that you believe are the most relevant to the central findings and key scientific insights. When including them, copy the reference from the round report and use the exact same path and markdown formatting.
If you include literature references, you must copy the exact reference from the round report. DO NOT try to add more references than are already given.

Provide a clear, structured summary:"""


def load_readme(data_dir: Path) -> Optional[str]:
    """
    Load the README.txt from the data directory.

    Args:
        data_dir: Path to the data directory containing README.txt

    Returns:
        The README content if found, None otherwise
    """
    readme_path = data_dir / "README.txt"

    if not readme_path.exists():
        print(f"  ⚠️  No README.txt found at {readme_path}")
        return None

    try:
        with open(readme_path, 'r', encoding='utf-8') as f:
            content = f.read()
        print(f"  📋 Loaded dataset context from {readme_path}")
        return content
    except Exception as e:
        print(f"  ⚠️  Failed to load README.txt: {e}")
        return None


def load_previous_round_hypothesis(checkpoint_path: Path, current_round: int) -> Optional[str]:
    """
    Load the selected hypothesis from the previous round.

    Args:
        checkpoint_path: Path to the checkpoint directory
        current_round: The current round number

    Returns:
        The hypothesis text if found, None otherwise
    """
    if current_round <= 0:
        return None

    previous_round = current_round - 1
    hypothesis_path = checkpoint_path / f"round_{previous_round}" / "selected_hypothesis.md"

    if not hypothesis_path.exists():
        print(f"  ⚠️  No selected_hypothesis.md found in round_{previous_round}")
        return None

    try:
        with open(hypothesis_path, 'r', encoding='utf-8') as f:
            content = f.read()
        print(f"  📋 Loaded hypothesis from round_{previous_round}/selected_hypothesis.md")
        return content
    except Exception as e:
        print(f"  ⚠️  Failed to load hypothesis from round_{previous_round}: {e}")
        return None


def find_latest_round(checkpoint_path: Path) -> Optional[Tuple[int, Path]]:
    """
    Find the latest round directory within a checkpoint path.

    Looks for directories matching the pattern 'round_*' and returns
    the highest numbered round.

    Args:
        checkpoint_path: Path to the checkpoint directory

    Returns:
        Tuple of (round_number, round_path) or None if no rounds found
    """
    if not checkpoint_path.exists():
        return None

    # Look for round directories
    round_dirs = [
        d for d in checkpoint_path.iterdir()
        if d.is_dir() and d.name.startswith('round_')
    ]

    if not round_dirs:
        return None

    # Extract round numbers and find the maximum
    rounds_with_nums = []
    for round_dir in round_dirs:
        try:
            round_num = int(round_dir.name.split('_')[1])
            rounds_with_nums.append((round_num, round_dir))
        except (ValueError, IndexError):
            continue

    if not rounds_with_nums:
        return None

    # Return the highest round number
    return max(rounds_with_nums, key=lambda x: x[0])


def summarize_content(
    content: str,
    user_prompt_template: str,
    model_name: str = "claude-sonnet-4-6",
    max_tokens: int = 8192,
    hypothesis: Optional[str] = None,
    readme: Optional[str] = None,
) -> str:
    """
    Summarize content using Claude API.

    Args:
        content: The content to summarize
        user_prompt_template: Template for the user prompt with {content} placeholder
        model_name: Claude model to use
        max_tokens: Maximum tokens for response
        hypothesis: Optional hypothesis text for hypothesis-aware summarization
        readme: Optional README content for dataset/research context

    Returns:
        Summary text
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable must be set")

    client = anthropic.Anthropic(api_key=api_key)

    # Truncate content if too long (to avoid token limits)
    max_content_chars = 100000  # ~25k tokens roughly
    if len(content) > max_content_chars:
        content = content[:max_content_chars] + "\n\n[... content truncated due to length ...]"

    # Build format kwargs
    format_kwargs = {"content": content}
    if hypothesis is not None:
        format_kwargs["hypothesis"] = hypothesis
    if readme is not None:
        format_kwargs["readme"] = readme
    else:
        format_kwargs["readme"] = "(No dataset context available)"

    user_prompt = user_prompt_template.format(**format_kwargs)

    response = client.messages.create(
        model=model_name,
        max_tokens=max_tokens,
        temperature=0.3,
        system=SUMMARIZATION_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": user_prompt}
        ]
    )

    return response.content[0].text


def summarize_report(
    report_path: Path,
    output_path: Path,
    user_prompt_template: str,
    report_type: str,
    hypothesis: Optional[str] = None,
    readme: Optional[str] = None,
    model_name: str = "claude-sonnet-4-6",
) -> bool:
    """
    Read a report, summarize it, and save the summary.

    Args:
        report_path: Path to the input report
        output_path: Path for the output summary
        user_prompt_template: Prompt template for summarization
        report_type: Type of report (for logging)
        hypothesis: Optional hypothesis text for hypothesis-aware summarization
        readme: Optional README content for dataset/research context
        model_name: Claude model to use for summarization

    Returns:
        True if successful, False otherwise
    """
    if not report_path.exists():
        print(f"  ⚠️  {report_type} not found at {report_path}")
        return False

    print(f"  📄 Reading {report_type} from {report_path}")

    try:
        with open(report_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"  ❌ Failed to read {report_type}: {e}")
        return False

    if not content.strip():
        print(f"  ⚠️  {report_type} is empty")
        return False

    print(f"  🤖 Summarizing {report_type} ({len(content):,} characters)...")
    if hypothesis:
        print(f"  🔬 Using hypothesis from previous round for evaluation")

    try:
        summary = summarize_content(content, user_prompt_template, model_name=model_name, hypothesis=hypothesis, readme=readme)
    except Exception as e:
        print(f"  ❌ Failed to summarize {report_type}: {e}")
        return False

    print(f"  💾 Saving summary to {output_path}")

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(f"# {report_type} Summary\n\n")
            f.write(f"*Automatically generated summary of {report_path.name}*\n\n")
            if hypothesis:
                f.write(f"*Evaluating hypothesis from previous round*\n\n")
            f.write("---\n\n")
            f.write(summary)
    except Exception as e:
        print(f"  ❌ Failed to save summary: {e}")
        return False

    print(f"  ✅ {report_type} summary saved ({len(summary):,} characters)")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Post-process AIR workflow checkpoints: generate summaries for memory and round reports"
    )
    parser.add_argument(
        "--project-prefix",
        type=str,
        required=False,
        help="Project prefix (relative path under DATA_PATH and CHECKPOINT_PATH)"
    )
    parser.add_argument(
        "--checkpoint-dir",
        type=str,
        required=False,
        help="Direct path to checkpoint directory (alternative to --project-prefix)"
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        required=False,
        help="Direct path to data directory containing README.txt (required with --checkpoint-dir)"
    )
    parser.add_argument(
        "--round",
        type=int,
        required=False,
        help="Specific round number to summarize (default: latest round)"
    )
    parser.add_argument(
        "--model",
        type=str,
        default="claude-sonnet-4-6",
        help="Claude model to use for summarization (default: claude-sonnet-4-6)"
    )
    args = parser.parse_args()

    # Determine checkpoint path and data path
    data_path = None
    if args.checkpoint_dir:
        checkpoint_path = Path(args.checkpoint_dir)
        if args.data_dir:
            data_path = Path(args.data_dir)
        else:
            print("⚠️  No --data-dir specified; README.txt context will not be available")
    elif args.project_prefix:
        if DATA_PATH is None:
            print("❌ DATA_PATH environment variable is not set")
            sys.exit(1)
        project_path = Path(DATA_PATH) / args.project_prefix
        if not project_path.exists():
            print(f"❌ Project path does not exist: {project_path}")
            sys.exit(1)
        data_path = project_path / "data"
        checkpoint_path = Path(CHECKPOINT_PATH) / args.project_prefix / "checkpoints"
    else:
        print("❌ Must specify either --project-prefix or --checkpoint-dir")
        sys.exit(1)

    if not checkpoint_path.exists():
        print(f"❌ Checkpoint path does not exist: {checkpoint_path}")
        sys.exit(1)

    print(f"📁 Checkpoint directory: {checkpoint_path}")
    if data_path:
        print(f"📁 Data directory: {data_path}")
    print(f"🤖 Model: {args.model}")

    # Find the target round
    if args.round is not None:
        round_num = args.round
        round_dir = checkpoint_path / f"round_{round_num}"
        if not round_dir.exists():
            print(f"❌ Round {round_num} directory not found: {round_dir}")
            sys.exit(1)
    else:
        result = find_latest_round(checkpoint_path)
        if result is None:
            print("❌ No round directories found in checkpoint path")
            sys.exit(1)
        round_num, round_dir = result

    print(f"🎯 Target round: {round_num} ({round_dir})")
    print()

    # Load README.txt for dataset/research context
    readme_content = None
    if data_path:
        print("📚 Loading dataset context...")
        readme_content = load_readme(data_path)
        print()

    # Step 1: Summarize memory_report.md
    print("📊 Step 1: Summarizing memory_report.md")
    memory_report_path = round_dir / "memory_report.md"
    memory_summary_path = round_dir / "memory_report_summary.md"
    summarize_report(
        memory_report_path,
        memory_summary_path,
        MEMORY_REPORT_USER_PROMPT,
        "Memory Report",
        readme=readme_content,
        model_name=args.model,
    )
    print()

    # Step 2: Summarize round_report.md
    # If this is round > 0, load hypothesis from previous round
    print("📊 Step 2: Summarizing round_report.md")
    previous_hypothesis = load_previous_round_hypothesis(checkpoint_path, round_num)

    round_report_path = round_dir / "round_report.md"
    round_summary_path = round_dir / "round_report_summary.md"

    if previous_hypothesis:
        # Use hypothesis-aware prompt
        summarize_report(
            round_report_path,
            round_summary_path,
            ROUND_REPORT_WITH_HYPOTHESIS_USER_PROMPT,
            "Round Report",
            hypothesis=previous_hypothesis,
            readme=readme_content,
            model_name=args.model,
        )
    else:
        # Use standard prompt (round 0 or no hypothesis found)
        summarize_report(
            round_report_path,
            round_summary_path,
            ROUND_REPORT_USER_PROMPT,
            "Round Report",
            readme=readme_content,
            model_name=args.model,
        )
    print()

    print("✅ Post-processing complete!")


if __name__ == "__main__":
    main()