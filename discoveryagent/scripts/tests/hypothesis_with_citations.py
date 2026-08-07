#!/usr/bin/env python3
"""
Citations-enabled hypothesis generator for experimentation.

This script loads a memory report, asks the hypothesis generator to
produce hypotheses with citations enabled, and then drops into ipdb so
you can inspect the raw response and citation structure.
"""

import argparse
import sys
from pathlib import Path

# ipdb is preferred for inspection; fall back to pdb if unavailable.
try:
    import ipdb  # type: ignore
except ImportError:  # pragma: no cover
    import pdb as ipdb

# Make AIR imports work the same way as scripts/select_hypothesis.py
sys.path.insert(0, str(Path(__file__).parent.parent))

from AIR.base_llm import AnthropicCitationsLLM
from AIR.config import LLMConfig
from scripts.select_hypothesis import InteractiveHypothesisGenerator


DEFAULT_REPORT_PATH = (
    "/Users/owenqueen/Desktop/stanford_research/ai_researcher/"
    "logs/election_select_q5_e20_20251130_205831_logs/"
    "checkpoints/round_4/memory_report.md"
)


def _build_document_block(text: str, title: str):
    return {
        "type": "document",
        "source": {
            "type": "text",
            "media_type": "text/plain",
            "data": text,
        },
        "title": title,
        "context": "Memory report used for citations-enabled hypothesis generation.",
        "citations": {"enabled": True},
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate hypotheses with citations enabled from a memory report."
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

    if generator.hypothesis_config.llm_config.api_provider != "anthropic":
        raise ValueError("Citations flow requires an Anthropic model.")

    n = args.num_hypotheses or getattr(
        generator.hypothesis_config, "n_candidate_hypotheses", 3
    )
    combined_background = generator._build_combined_background()
    prompt = generator._build_diverse_generation_prompt(n, combined_background)

    # Explicitly request citations so Sonnet 3.7+ complies even with formatting.
    user_text = (
        "Use citations to back up every hypothesis and justification. "
        "Keep citations inside the structured output.\n\n"
        f"{prompt}"
    )

    doc_blocks = [_build_document_block(generator.memory_report, report_path.name)]

    citations_llm = AnthropicCitationsLLM(
        llm_config=generator.hypothesis_config.llm_config,
        system_prompt=generator.hypothesis_gen_agent.system_prompt,
        max_tokens=generator.hypothesis_config.max_tokens,
    )

    raw_text, response = citations_llm.forward_with_documents(
        user_text=user_text,
        documents=doc_blocks,
    )

    parsed_hypotheses = generator._parse_multiple_hypotheses(raw_text)
    if not parsed_hypotheses and raw_text:
        parsed_hypotheses = [raw_text.strip()]

    print("\n=== Candidate Hypotheses (citations-enabled) ===\n")
    for idx, hyp in enumerate(parsed_hypotheses, 1):
        print(f"--- Hypothesis {idx} ---\n{hyp}\n")

    # Drop into debugger to inspect `response` and `raw_text` (with citations).
    ipdb.set_trace()

    return 0


if __name__ == "__main__":
    sys.exit(main())

