#!/usr/bin/env python3
"""
Minimal wrapper to generate hypotheses from a memory report so we can
experiment with Claude citations. This reuses InteractiveHypothesisGenerator
from scripts/select_hypothesis.py and simply prints out the generated
hypotheses for the provided report.
"""

import argparse
import sys
from pathlib import Path

# Make AIR imports work the same way as scripts/select_hypothesis.py
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.select_hypothesis import InteractiveHypothesisGenerator


DEFAULT_REPORT_PATH = (
    "/Users/owenqueen/Desktop/stanford_research/ai_researcher/"
    "logs/election_select_q5_e20_20251130_205831_logs/"
    "checkpoints/round_4/memory_report.md"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate hypotheses from a memory report using the interactive generator."
    )
    parser.add_argument(
        "--report-path",
        type=str,
        default=DEFAULT_REPORT_PATH,
        help="Path to memory_report.md (default points to a testing report).",
    )
    parser.add_argument(
        "--num-hypotheses",
        type=int,
        default=None,
        help="Optional override for number of hypotheses to request.",
    )
    parser.add_argument(
        "--use-legacy",
        action="store_true",
        help="Use legacy multi-call generation instead of single-shot.",
    )
    args = parser.parse_args()

    report_path = Path(args.report_path).expanduser()
    if not report_path.exists():
        print(f"❌ Report path not found: {report_path}")
        return 1

    checkpoint_dir = report_path.parent
    print(f"✓ Using checkpoint directory: {checkpoint_dir}")

    generator = InteractiveHypothesisGenerator(
        checkpoint_dir=str(checkpoint_dir),
        register_signal_handlers=False,
        num_hypotheses=args.num_hypotheses,
    )

    if not generator.validate_checkpoint():
        return 1

    if not generator.load_checkpoint():
        return 1

    # Force the generator to use the provided report file for background context.
    generator.memory_report = report_path.read_text().strip()
    print(f"✓ Loaded memory report from {report_path}")

    generator.initialize_agent()

    n = args.num_hypotheses or getattr(
        generator.hypothesis_config, "n_candidate_hypotheses", 3
    )
    hypotheses = generator.generate_candidates(n=n, use_legacy=args.use_legacy)

    print("\n=== Candidate Hypotheses ===\n")
    for idx, hyp in enumerate(hypotheses, 1):
        print(f"--- Hypothesis {idx} ---\n{hyp}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())


