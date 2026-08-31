import asyncio
import inspect
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ["DISCOVERYAGENT_FRONTEND_LOCAL_ROUND"] = "1"
from AIR.config import HypothesisGenerationConfig, LLMConfig
from AIR.checkpoint_manager import CheckpointManager
from AIR.director_workflow import DirectorWorkflow
from AIR.hypothesis_brancher import AIRWorkflowConfig, HypothesisBrancher
from AIR.tool_agents import execution_agent_cc
from AIR.tool_agents.deep_research import DeepResearchAgent, DeepResearchConfig
from AIR.tool_agents.execution_agent_cc import ExecutionAgentCC, ExecutionAgentCCConfig
from AIR.utils.agent_config_builders import (
    set_deep_research_agent_config,
    set_execution_agent_config,
)
from AIR.utils.arg_parsers import (
    ANTHROPIC_MODEL_FIELDS,
    DEFAULT_ANTHROPIC_MODEL,
    create_hb_sherlock_parser,
    normalize_anthropic_model_configuration,
)
from AIR.utils.config_loader import (
    AGENT_BEHAVIOR_VERSION_KEY,
    CURRENT_AGENT_BEHAVIOR_VERSION,
    apply_updates_with_cli_priority,
    explicit_cli_destinations,
    merge_checkpoint_behavior_context,
    resolve_checkpoint_behavior,
    snapshot_args_to_yaml,
)
from cli.run_interactive import _CONFIG_DEFAULTS
os.environ.pop("DISCOVERYAGENT_FRONTEND_LOCAL_ROUND", None)


class CaptureLogger:
    def __init__(self):
        self.actions = []
        self.errors = []

    def log_action(self, action, details=None):
        self.actions.append((action, details))

    def log_error(self, message, code=None):
        self.errors.append((message, code))


class RestoreProbeWorkflow(DirectorWorkflow):
    """Minimal constructor surface for exercising checkpoint migration only."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.logger = CaptureLogger()
        self.agents = {}


def execution_args(max_turns):
    return SimpleNamespace(
        execution_model="claude-sonnet-4-6",
        execution_max_turns=max_turns,
        claude_md_generator=False,
        log_execution_agent=True,
        execution_skills_path=None,
        sub_agent_architecture=False,
        no_sandbox_enforcement=False,
        no_sandbox=False,
        no_url_blocker=False,
        allowed_read_paths=None,
    )


def test_parser_and_interactive_cli_default_to_uncapped_execution():
    parsed = create_hb_sherlock_parser().parse_args([])

    assert parsed.execution_max_turns == 0
    assert parsed.director_model == "claude-opus-4-6"
    assert _CONFIG_DEFAULTS["execution_max_turns"] == 0
    assert ExecutionAgentCCConfig().max_turns is None


def test_all_discoveryagent_model_fields_share_one_anthropic_default():
    parsed = create_hb_sherlock_parser().parse_args([])

    assert DEFAULT_ANTHROPIC_MODEL == "claude-opus-4-6"
    assert {getattr(parsed, field) for field in ANTHROPIC_MODEL_FIELDS} == {
        DEFAULT_ANTHROPIC_MODEL
    }


def test_project_model_overrides_every_backend_agent_role():
    parsed = create_hb_sherlock_parser().parse_args([
        "--project_model", "claude-sonnet-4-6",
        "--execution_model", "claude-haiku-4-5-20251001",
    ])

    normalize_anthropic_model_configuration(parsed)

    assert parsed.project_model == "claude-sonnet-4-6"
    assert {getattr(parsed, field) for field in ANTHROPIC_MODEL_FIELDS} == {
        "claude-sonnet-4-6"
    }


def test_historical_config_without_project_model_keeps_its_snapshotted_roles():
    parsed = create_hb_sherlock_parser().parse_args([])
    parsed.project_model = None
    parsed.hypothesis_model = "claude-opus-4-6"
    parsed.execution_model = "claude-sonnet-4-6"

    normalize_anthropic_model_configuration(parsed)

    assert parsed.hypothesis_model == "claude-opus-4-6"
    assert parsed.execution_model == "claude-sonnet-4-6"


def test_non_anthropic_model_identifiers_are_rejected():
    with pytest.raises(SystemExit):
        create_hb_sherlock_parser().parse_args(["--project_model", "gpt-4o"])

    parsed = create_hb_sherlock_parser().parse_args([])
    parsed.project_model = "gpt-4o"  # simulates a bad checkpoint value
    with pytest.raises(ValueError):
        normalize_anthropic_model_configuration(parsed)


def test_hypothesis_generation_config_keeps_automatic_literature_grounding_opt_in():
    config = HypothesisGenerationConfig(
        llm_config=LLMConfig(model_name="claude-sonnet-4-6")
    )

    assert config.use_deep_research_for_hypothesis_generation is False


def test_air_workflow_config_defaults_to_paperclip_deep_research():
    hypothesis_config = HypothesisGenerationConfig(
        llm_config=LLMConfig(model_name="claude-sonnet-4-6")
    )
    workflow_config = AIRWorkflowConfig(
        hypothesis_config=hypothesis_config,
        dataset_description="test",
    )

    assert workflow_config.deep_research_provider == "paperclip"
    assert workflow_config.hypothesis_config.use_deep_research_for_hypothesis_generation is False
    assert workflow_config.enable_deep_research_in_review is False
    assert workflow_config.resume_behavior_overrides is False
    assert workflow_config.hold_reviews_in_exploratory_phase is True


def test_director_workflow_defaults_to_paperclip_deep_research():
    parameters = inspect.signature(DirectorWorkflow.__init__).parameters

    assert parameters["enable_deep_research_in_review"].default is False
    assert parameters["deep_research_provider"].default == "paperclip"


def test_empty_provider_normalizes_to_paperclip_across_agent_entry_points(tmp_path):
    built = set_deep_research_agent_config(
        SimpleNamespace(deep_research_provider=None)
    )
    direct = DeepResearchAgent(
        DeepResearchConfig(log_dir=str(tmp_path), deep_research_provider=None),
        logger=CaptureLogger(),
    )
    workflow_config = AIRWorkflowConfig(
        hypothesis_config=HypothesisGenerationConfig(
            llm_config=LLMConfig(model_name="claude-sonnet-4-6")
        ),
        dataset_description="test",
        deep_research_provider=None,
    )

    assert built.deep_research_provider == "paperclip"
    assert direct.config.deep_research_provider == "paperclip"
    assert direct.deep_research_provider == "paperclip"
    assert workflow_config.deep_research_provider == "paperclip"


def test_legacy_resume_preserves_pre_v2_research_behavior():
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
    }

    resolved, used_legacy_behavior = resolve_checkpoint_behavior(old_workflow_state)

    assert used_legacy_behavior is True
    assert resolved[AGENT_BEHAVIOR_VERSION_KEY] == 1
    assert resolved["deep_research_provider"] == "perplexity"
    assert resolved["use_deep_research_for_hypothesis_generation"] is False
    assert resolved["enable_deep_research_in_review"] is False


def test_complete_v2_resume_preserves_explicit_research_behavior():
    saved = {
        "deep_research_provider": "paperclip",
        "use_deep_research_for_hypothesis_generation": True,
        "enable_deep_research_in_review": True,
    }

    resolved, used_legacy_behavior = resolve_checkpoint_behavior(saved)

    assert used_legacy_behavior is False
    assert resolved[AGENT_BEHAVIOR_VERSION_KEY] == CURRENT_AGENT_BEHAVIOR_VERSION
    assert resolved["deep_research_provider"] == "paperclip"
    assert resolved["use_deep_research_for_hypothesis_generation"] is True
    assert resolved["enable_deep_research_in_review"] is True


def test_partially_migrated_resume_preserves_values_and_uses_legacy_for_missing_fields():
    resolved, used_legacy_behavior = resolve_checkpoint_behavior({
        "deep_research_provider": "paperclip",
        "enable_deep_research_in_review": None,
    })

    assert used_legacy_behavior is True
    assert resolved["deep_research_provider"] == "paperclip"
    assert resolved["use_deep_research_for_hypothesis_generation"] is False
    assert resolved["enable_deep_research_in_review"] is False


def test_pre_versioned_pr_query_checkpoint_preserves_pr_behavior():
    resolved, used_legacy_behavior = resolve_checkpoint_behavior({
        "deep_research_provider": "paperclip",
        "enable_deep_research_in_review": True,
    })

    assert used_legacy_behavior is False
    assert resolved[AGENT_BEHAVIOR_VERSION_KEY] == CURRENT_AGENT_BEHAVIOR_VERSION
    assert resolved["deep_research_provider"] == "paperclip"
    assert resolved["use_deep_research_for_hypothesis_generation"] is True
    assert resolved["enable_deep_research_in_review"] is True


def test_current_version_missing_fields_uses_original_architecture_defaults():
    resolved, used_legacy_behavior = resolve_checkpoint_behavior({
        AGENT_BEHAVIOR_VERSION_KEY: CURRENT_AGENT_BEHAVIOR_VERSION,
    })

    assert used_legacy_behavior is False
    assert resolved["deep_research_provider"] == "paperclip"
    assert resolved["use_deep_research_for_hypothesis_generation"] is False
    assert resolved["enable_deep_research_in_review"] is False


def test_future_behavior_version_is_rejected():
    with pytest.raises(ValueError, match="Unsupported agent behavior version"):
        resolve_checkpoint_behavior({
            AGENT_BEHAVIOR_VERSION_KEY: CURRENT_AGENT_BEHAVIOR_VERSION + 1,
        })


def test_malformed_behavior_version_is_rejected():
    for invalid_version in ("bogus", 2.9, True, 0, -1):
        with pytest.raises(ValueError, match="Invalid agent behavior version"):
            resolve_checkpoint_behavior({
                AGENT_BEHAVIOR_VERSION_KEY: invalid_version,
            })


def test_adjacent_round_config_only_fills_missing_checkpoint_behavior():
    merged = merge_checkpoint_behavior_context(
        {
            "deep_research_provider": "paperclip",
            "enable_deep_research_in_review": True,
        },
        {
            "config": {
                "deep_research_provider": "perplexity",
                "use_deep_research_for_hypothesis_generation": False,
                "enable_deep_research_in_review": False,
            }
        },
    )

    assert merged["deep_research_provider"] == "paperclip"
    assert merged["use_deep_research_for_hypothesis_generation"] is False
    assert merged["enable_deep_research_in_review"] is True


def test_explicit_default_valued_cli_options_override_legacy_resume_values():
    parser = create_hb_sherlock_parser()
    argv = [
        "--deep_research_provider", "paperclip",
        "--enable_deep_research_in_review",
        "--use_deep_research_for_hypothesis_generation",
    ]
    parsed = parser.parse_args(argv)
    explicit = explicit_cli_destinations(parser, argv)
    legacy, _ = resolve_checkpoint_behavior({})

    apply_updates_with_cli_priority(parsed, legacy, explicit, parser=parser)

    assert parsed.deep_research_provider == "paperclip"
    assert parsed.use_deep_research_for_hypothesis_generation is True
    assert parsed.enable_deep_research_in_review is True


def test_yaml_snapshot_excludes_process_local_resume_state(tmp_path):
    args = SimpleNamespace(
        deep_research_provider="paperclip",
        _resume_behavior_overrides=True,
        _resume_memory_report_path="private.md",
    )
    output_path = tmp_path / "config.yaml"

    snapshot_args_to_yaml(args, str(output_path))
    saved = output_path.read_text()

    assert "deep_research_provider: paperclip" in saved
    assert "_resume_behavior_overrides" not in saved
    assert "_resume_memory_report_path" not in saved


def test_direct_mid_round_restore_uses_legacy_behavior_for_missing_fields(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps({
        "workflow_state": {
            "dataset_description": "legacy dataset",
            "query_num": 2,
        },
        "agents": {},
    }))

    restored = RestoreProbeWorkflow.from_checkpoint(str(checkpoint))

    assert restored.deep_research_provider == "perplexity"
    assert restored.enable_deep_research_in_review is False
    assert restored.hypothesis_config.use_deep_research_for_hypothesis_generation is False
    assert any(action == "legacy_behavior_restored" for action, _ in restored.logger.actions)


def test_direct_mid_round_restore_preserves_v2_checkpoint_behavior(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps({
        "workflow_state": {
            AGENT_BEHAVIOR_VERSION_KEY: CURRENT_AGENT_BEHAVIOR_VERSION,
            "dataset_description": "v2 dataset",
            "query_num": 2,
            "deep_research_provider": "paperclip",
            "use_deep_research_for_hypothesis_generation": True,
            "enable_deep_research_in_review": True,
        },
        "agents": {},
    }))

    restored = RestoreProbeWorkflow.from_checkpoint(str(checkpoint))

    assert restored.deep_research_provider == "paperclip"
    assert restored.enable_deep_research_in_review is True
    assert restored.hypothesis_config.use_deep_research_for_hypothesis_generation is True


def test_direct_restore_uses_adjacent_round_config_for_early_pr_checkpoint(tmp_path):
    query_dir = tmp_path / "round_0" / "query_2"
    query_dir.mkdir(parents=True)
    checkpoint = query_dir / "checkpoint.json"
    checkpoint.write_text(json.dumps({
        "workflow_state": {
            "deep_research_provider": "paperclip",
            "enable_deep_research_in_review": True,
        },
        "agents": {},
    }))
    (tmp_path / "round_0" / "config.json").write_text(json.dumps({
        "config": {
            "deep_research_provider": "paperclip",
            "use_deep_research_for_hypothesis_generation": False,
            "enable_deep_research_in_review": True,
        }
    }))

    restored = RestoreProbeWorkflow.from_checkpoint(str(checkpoint))

    assert restored.deep_research_provider == "paperclip"
    assert restored.hypothesis_config.use_deep_research_for_hypothesis_generation is False
    assert restored.enable_deep_research_in_review is True


def test_initial_hypothesis_checkpoint_persists_behavior_profile(tmp_path):
    workflow = SimpleNamespace(
        dataset_description="test",
        task_description=None,
        max_queries=10,
        deep_research_provider="paperclip",
        hypothesis_config=HypothesisGenerationConfig(
            llm_config=LLMConfig(model_name="claude-sonnet-4-6"),
            use_deep_research_for_hypothesis_generation=True,
        ),
        enable_deep_research_in_review=True,
        logger=CaptureLogger(),
    )
    manager = CheckpointManager(str(tmp_path), workflow, round_num=0)

    checkpoint_path = manager.save_hypothesis_checkpoint("hypothesis")
    saved = json.loads(Path(checkpoint_path).read_text())["workflow_state"]

    assert saved[AGENT_BEHAVIOR_VERSION_KEY] == CURRENT_AGENT_BEHAVIOR_VERSION
    assert saved["deep_research_provider"] == "paperclip"
    assert saved["use_deep_research_for_hypothesis_generation"] is True
    assert saved["enable_deep_research_in_review"] is True


def test_model_only_hypothesis_config_does_not_override_checkpoint_behavior(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps({
        "workflow_state": {
            AGENT_BEHAVIOR_VERSION_KEY: CURRENT_AGENT_BEHAVIOR_VERSION,
            "deep_research_provider": "paperclip",
            "use_deep_research_for_hypothesis_generation": True,
            "enable_deep_research_in_review": True,
        },
        "agents": {},
    }))
    model_config = HypothesisGenerationConfig(
        llm_config=LLMConfig(model_name="claude-sonnet-4-6")
    )

    restored = RestoreProbeWorkflow.from_checkpoint(
        str(checkpoint),
        hypothesis_config=model_config,
    )

    assert restored.hypothesis_config is not model_config
    assert model_config.use_deep_research_for_hypothesis_generation is False
    assert restored.hypothesis_config.use_deep_research_for_hypothesis_generation is True


def test_explicit_mid_round_behavior_overrides_remain_available(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps({
        "workflow_state": {
            AGENT_BEHAVIOR_VERSION_KEY: CURRENT_AGENT_BEHAVIOR_VERSION,
            "deep_research_provider": "paperclip",
            "use_deep_research_for_hypothesis_generation": True,
            "enable_deep_research_in_review": True,
        },
        "agents": {},
    }))

    restored = RestoreProbeWorkflow.from_checkpoint(
        str(checkpoint),
        deep_research_provider="perplexity",
        use_deep_research_for_hypothesis_generation=False,
        enable_deep_research_in_review=False,
    )

    assert restored.deep_research_provider == "perplexity"
    assert restored.hypothesis_config.use_deep_research_for_hypothesis_generation is False
    assert restored.enable_deep_research_in_review is False


@pytest.mark.parametrize("authoritative", [False, True])
def test_brancher_only_overrides_checkpoint_behavior_when_marked_authoritative(
    monkeypatch,
    authoritative,
):
    hypothesis_config = HypothesisGenerationConfig(
        llm_config=LLMConfig(model_name="claude-sonnet-4-6"),
        use_deep_research_for_hypothesis_generation=False,
    )
    workflow_config = AIRWorkflowConfig(
        hypothesis_config=hypothesis_config,
        dataset_description="test",
        deep_research_provider="perplexity",
        enable_deep_research_in_review=False,
        resume_behavior_overrides=authoritative,
    )
    brancher = HypothesisBrancher.__new__(HypothesisBrancher)
    brancher.workflow_config = workflow_config
    brancher.data_manager = None
    captured = {}
    restored = SimpleNamespace(
        query_num=1,
        report_filename="unused",
        hypothesis_config=HypothesisGenerationConfig(
            llm_config=LLMConfig(model_name="claude-sonnet-4-6"),
            use_deep_research_for_hypothesis_generation=True,
        ),
        deep_research_provider="paperclip",
        enable_deep_research_in_review=True,
    )

    def fake_from_checkpoint(**kwargs):
        captured.update(kwargs)
        return restored

    monkeypatch.setattr(DirectorWorkflow, "from_checkpoint", fake_from_checkpoint)

    assert brancher._restore_workflow_from_checkpoint("checkpoint.json", 0) is restored
    behavior_keys = {
        "deep_research_provider",
        "use_deep_research_for_hypothesis_generation",
        "enable_deep_research_in_review",
    }
    if authoritative:
        assert {key: captured[key] for key in behavior_keys} == {
            "deep_research_provider": "perplexity",
            "use_deep_research_for_hypothesis_generation": False,
            "enable_deep_research_in_review": False,
        }
    else:
        assert behavior_keys.isdisjoint(captured)


def test_brancher_syncs_checkpoint_behavior_before_saving_later_rounds():
    original_hypothesis_config = HypothesisGenerationConfig(
        llm_config=LLMConfig(model_name="claude-sonnet-4-6"),
        use_deep_research_for_hypothesis_generation=False,
    )
    brancher = HypothesisBrancher.__new__(HypothesisBrancher)
    brancher.workflow_config = AIRWorkflowConfig(
        hypothesis_config=original_hypothesis_config,
        dataset_description="test",
        deep_research_provider="perplexity",
        enable_deep_research_in_review=False,
        resume_behavior_overrides=False,
    )
    restored = SimpleNamespace(
        hypothesis_config=HypothesisGenerationConfig(
            llm_config=LLMConfig(model_name="claude-sonnet-4-6"),
            use_deep_research_for_hypothesis_generation=True,
        ),
        deep_research_provider="paperclip",
        enable_deep_research_in_review=True,
    )

    brancher._sync_restored_workflow_behavior(restored)

    assert brancher.workflow_config.hypothesis_config is not original_hypothesis_config
    assert original_hypothesis_config.use_deep_research_for_hypothesis_generation is False
    assert brancher.workflow_config.hypothesis_config.use_deep_research_for_hypothesis_generation is True
    assert brancher.workflow_config.deep_research_provider == "paperclip"
    assert brancher.workflow_config.enable_deep_research_in_review is True


def test_parser_defaults_and_overrides_for_paperclip_deep_research():
    parser = create_hb_sherlock_parser()
    parsed = parser.parse_args([])

    assert parsed.deep_research_provider == "paperclip"
    assert parsed.use_deep_research_for_hypothesis_generation is False
    assert parsed.enable_deep_research_in_review is False
    assert parsed.review_optimization_steps is None
    assert parsed.hold_reviews_in_exploratory_phase is True

    enabled = parser.parse_args([
        "--enable_deep_research_in_review",
        "--use_deep_research_for_hypothesis_generation",
        "--no-hold-reviews-in-exploratory-phase",
    ])
    assert enabled.enable_deep_research_in_review is True
    assert enabled.use_deep_research_for_hypothesis_generation is True
    assert enabled.hold_reviews_in_exploratory_phase is False


@pytest.mark.parametrize(
    "raw, expected",
    [(None, None), (0, None), (7, 7)],
)
def test_configuration_builder_normalizes_zero_and_none_to_uncapped(raw, expected):
    config = set_execution_agent_config(
        execution_args(raw),
        dataset_description="dataset",
        working_directory="/tmp/project",
        analysis_name="analysis",
    )

    assert config.max_turns_query_execution == expected


def test_url_blocker_is_enabled_by_default_and_has_an_explicit_opt_out():
    parser = create_hb_sherlock_parser()
    defaults = parser.parse_args([])
    disabled = parser.parse_args(["--no-url-blocker"])

    default_config = set_execution_agent_config(
        defaults,
        dataset_description="dataset",
        working_directory="/tmp/project",
        analysis_name="analysis",
    )
    disabled_config = set_execution_agent_config(
        disabled,
        dataset_description="dataset",
        working_directory="/tmp/project",
        analysis_name="analysis",
    )

    assert default_config.url_blocker_enabled is True
    assert disabled_config.url_blocker_enabled is False


def bare_execution_agent(max_turns, tmp_path: Path):
    agent = ExecutionAgentCC.__new__(ExecutionAgentCC)
    agent.max_turns_query_execution = max_turns
    agent.max_turns = max_turns
    agent.sub_agent_architecture = False
    agent.codebase_structure_prompt = None
    agent.working_directory = tmp_path
    agent.logger = CaptureLogger()
    agent.execution_tracker = None
    agent.sandbox_enforcement = False
    agent.allowed_read_paths = None
    agent.llm_config = SimpleNamespace(model_name="claude-sonnet-4-6")
    agent.config = SimpleNamespace()
    agent.claude_md_generator = False
    return agent


def test_sandbox_path_guidance_does_not_require_a_structure_prompt(
    monkeypatch,
    tmp_path,
):
    agent = bare_execution_agent(None, tmp_path)
    agent.sandbox_enforcement = True
    monkeypatch.setattr(execution_agent_cc, "get_slurm_resource_info", lambda: None)

    system_prompt = agent._get_system_prompt()

    assert "SANDBOX PATH RULES" in system_prompt
    assert str(tmp_path) in system_prompt


@pytest.mark.parametrize(
    "max_turns, expected_fragment",
    [(None, None), (0, None), (6, "total of 6 steps")],
)
def test_budget_prompt_material_only_exists_for_a_real_cap(
    monkeypatch, tmp_path, max_turns, expected_fragment
):
    agent = bare_execution_agent(max_turns, tmp_path)
    monkeypatch.setattr(execution_agent_cc, "get_slurm_resource_info", lambda: None)

    budget = agent._step_budget_section()
    system_prompt = agent._get_system_prompt()

    if expected_fragment is None:
        assert budget == ""
        assert "⏱️ BUDGET" not in system_prompt
        assert "None steps" not in system_prompt
        assert "0 steps" not in system_prompt
    else:
        assert expected_fragment in budget
        assert expected_fragment in system_prompt


class FakeClaudeClient:
    def __init__(self):
        self.queries = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def query(self, prompt):
        self.queries.append(prompt)

    async def receive_response(self):
        if False:
            yield None


@pytest.mark.parametrize("max_turns", [None, 0, 4])
def test_analysis_prompt_uses_the_same_budget_rule(tmp_path, max_turns):
    agent = bare_execution_agent(max_turns, tmp_path)
    client = FakeClaudeClient()
    agent.claude_client = client
    agent._format_message_for_display = lambda _message: ""
    agent._direct_print = lambda _message: None

    async def summary(*_args, **_kwargs):
        return "summary"

    agent._generate_concise_summary = summary

    result = asyncio.run(
        agent._execute_with_report_generation("analyze", "dataset", query_num=1, output_files=[])
    )

    assert result["concise_report"] == "summary"
    assert len(client.queries) == 1
    if max_turns:
        assert f"total of {max_turns} steps" in client.queries[0]
    else:
        assert "⏱️ BUDGET" not in client.queries[0]
        assert "None steps" not in client.queries[0]
        assert "0 steps" not in client.queries[0]


@pytest.mark.parametrize("max_turns", [None, 0, 8])
def test_claude_sdk_options_omit_max_turns_unless_capped(
    monkeypatch, tmp_path, max_turns
):
    agent = bare_execution_agent(max_turns, tmp_path)
    agent._get_system_prompt = lambda: "system prompt"
    agent._build_opentargets_mcp_configs = lambda: {}
    agent._configure_execution_skills = lambda: None
    agent._discover_execution_skills = lambda: []
    captured = {}

    monkeypatch.setattr(
        execution_agent_cc,
        "_create_claude_agent_options",
        lambda options: captured.update(options) or options,
    )
    monkeypatch.setattr(
        execution_agent_cc,
        "ClaudeSDKClient",
        lambda options: SimpleNamespace(options=options),
    )

    agent._init_claude_code_session()

    if max_turns:
        assert captured["max_turns"] == max_turns
    else:
        assert "max_turns" not in captured


def test_saved_state_preserves_none_for_uncapped_execution(tmp_path):
    agent = bare_execution_agent(None, tmp_path)
    agent.claude_client = None
    agent.execution_context = {}
    agent.existing_files = set()
    agent.execution_count = 0
    agent.analysis_name = "analysis"
    agent.separate_report_step = False
    agent.execute_context_persistence = True
    agent.log_execution_agent = True

    state = agent.save_state()

    assert state["config_info"]["max_turns_query_execution"] is None
    assert state["claude_session_info"]["max_turns"] is None
