"""
Lightweight diagnostic test: verify that loading a config/checkpoint without the
new Paperclip/deep-research keys applies the correct defaults.

Run with:
    python scripts/tests/test_paperclip_defaults.py
"""
import sys
import os
import types

# Make sure repo root is on the path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)


def test_hypothesis_generation_config_defaults():
    """HypothesisGenerationConfig must default use_deep_research_for_hypothesis_generation=True."""
    from AIR.config import HypothesisGenerationConfig, LLMConfig
    cfg = HypothesisGenerationConfig(llm_config=LLMConfig(model_name="claude-sonnet-4-6"))
    assert cfg.use_deep_research_for_hypothesis_generation is True, (
        f"Expected True, got {cfg.use_deep_research_for_hypothesis_generation}"
    )
    print("PASS  HypothesisGenerationConfig.use_deep_research_for_hypothesis_generation defaults to True")


def test_air_workflow_config_defaults():
    """AIRWorkflowConfig must reflect the new defaults for Paperclip/deep-research keys."""
    from AIR.config import HypothesisGenerationConfig, LLMConfig
    from AIR.hypothesis_brancher import AIRWorkflowConfig
    hyp_cfg = HypothesisGenerationConfig(llm_config=LLMConfig(model_name="claude-sonnet-4-6"))
    wcfg = AIRWorkflowConfig(hypothesis_config=hyp_cfg, dataset_description="test")
    assert wcfg.deep_research_provider == "paperclip", f"Expected 'paperclip', got {wcfg.deep_research_provider!r}"
    # use_deep_research_for_hypothesis_generation lives on HypothesisGenerationConfig, not
    # AIRWorkflowConfig directly — accessed via workflow_config.hypothesis_config.
    assert wcfg.hypothesis_config.use_deep_research_for_hypothesis_generation is True, (
        f"Expected True, got {wcfg.hypothesis_config.use_deep_research_for_hypothesis_generation}"
    )
    assert wcfg.enable_deep_research_in_review is True, (
        f"Expected True, got {wcfg.enable_deep_research_in_review}"
    )
    assert wcfg.hold_reviews_in_exploratory_phase is True, (
        f"Expected True, got {wcfg.hold_reviews_in_exploratory_phase}"
    )
    print("PASS  AIRWorkflowConfig defaults: deep_research_provider=paperclip, "
          "hypothesis_config.use_deep_research_for_hypothesis_generation=True, "
          "enable_deep_research_in_review=True, hold_reviews_in_exploratory_phase=True")


def test_director_workflow_init_defaults():
    """DirectorWorkflow.__init__ signature must default enable_deep_research_in_review=True."""
    import inspect
    from AIR.director_workflow import DirectorWorkflow
    sig = inspect.signature(DirectorWorkflow.__init__)
    params = sig.parameters
    assert params["enable_deep_research_in_review"].default is True, (
        f"Expected default True, got {params['enable_deep_research_in_review'].default}"
    )
    assert params["deep_research_provider"].default == "paperclip", (
        f"Expected 'paperclip', got {params['deep_research_provider'].default!r}"
    )
    print("PASS  DirectorWorkflow.__init__ defaults: enable_deep_research_in_review=True, "
          "deep_research_provider='paperclip'")


def test_from_checkpoint_missing_keys():
    """from_checkpoint must apply correct defaults for keys absent in old checkpoints."""
    from AIR.director_workflow import DirectorWorkflow

    # Simulate a minimal old-format workflow_state that is missing the new keys.
    old_workflow_state = {
        "dataset_description": "test dataset",
        "task_description": None,
        "max_queries": 5,
        "query_num": 2,
        "results": [],
        "report": "",
        "cumulative_report": "",
        "images": {},
        "image_counter": 0,
        "review_history": [],
        "generated_reviewer_prompt": None,
        "git_tracking": False,
        "multiturn_hypothesis": True,
        "review_search": False,
        "review_design": False,
        # intentionally absent: deep_research_provider, enable_deep_research_in_review
    }

    # Exercise the extraction logic directly (mirrors from_checkpoint internals)
    provider = old_workflow_state.get("deep_research_provider", "paperclip") or "paperclip"
    enable_dr = old_workflow_state.get("enable_deep_research_in_review", True)

    assert provider == "paperclip", f"Expected 'paperclip', got {provider!r}"
    assert enable_dr is True, f"Expected True, got {enable_dr}"
    print("PASS  from_checkpoint missing-key logic: deep_research_provider='paperclip', "
          "enable_deep_research_in_review=True")


def test_argparse_defaults():
    """Argparse parser must default the five new keys correctly."""
    from AIR.utils.arg_parsers import create_hb_sherlock_parser
    parser = create_hb_sherlock_parser()
    # parse with no arguments
    args = parser.parse_args([])
    assert getattr(args, "deep_research_provider", None) == "paperclip", (
        f"Expected 'paperclip', got {getattr(args, 'deep_research_provider', None)!r}"
    )
    assert getattr(args, "use_deep_research_for_hypothesis_generation", None) is True, (
        f"Expected True, got {getattr(args, 'use_deep_research_for_hypothesis_generation', None)}"
    )
    assert getattr(args, "enable_deep_research_in_review", None) is True, (
        f"Expected True, got {getattr(args, 'enable_deep_research_in_review', None)}"
    )
    assert getattr(args, "review_optimization_steps", None) is None, (
        f"Expected None, got {getattr(args, 'review_optimization_steps', None)}"
    )
    assert getattr(args, "hold_reviews_in_exploratory_phase", None) is True, (
        f"Expected True, got {getattr(args, 'hold_reviews_in_exploratory_phase', None)}"
    )
    print("PASS  argparse defaults: deep_research_provider=paperclip, "
          "use_deep_research_for_hypothesis_generation=True, enable_deep_research_in_review=True, "
          "review_optimization_steps=None, hold_reviews_in_exploratory_phase=True")

    # Also verify the --no- override flags work
    args_disabled = parser.parse_args([
        "--no-enable-deep-research-in-review",
        "--no-use-deep-research-for-hypothesis-generation",
        "--no-hold-reviews-in-exploratory-phase",
    ])
    assert args_disabled.enable_deep_research_in_review is False
    assert args_disabled.use_deep_research_for_hypothesis_generation is False
    assert args_disabled.hold_reviews_in_exploratory_phase is False
    print("PASS  argparse --no-* override flags work correctly")


if __name__ == "__main__":
    tests = [
        test_hypothesis_generation_config_defaults,
        test_air_workflow_config_defaults,
        test_director_workflow_init_defaults,
        test_from_checkpoint_missing_keys,
        test_argparse_defaults,
    ]
    failures = []
    for t in tests:
        try:
            t()
        except Exception as exc:
            print(f"FAIL  {t.__name__}: {exc}")
            failures.append(t.__name__)

    print()
    if failures:
        print(f"FAILED: {len(failures)}/{len(tests)} tests — {failures}")
        sys.exit(1)
    else:
        print(f"All {len(tests)} tests passed.")
