import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ["DISCOVERYAGENT_FRONTEND_LOCAL_ROUND"] = "1"
from AIR.tool_agents import execution_agent_cc
from AIR.tool_agents.execution_agent_cc import ExecutionAgentCC, ExecutionAgentCCConfig
from AIR.utils.agent_config_builders import set_execution_agent_config
from AIR.utils.arg_parsers import (
    ANTHROPIC_MODEL_FIELDS,
    DEFAULT_ANTHROPIC_MODEL,
    create_hb_sherlock_parser,
    normalize_anthropic_model_configuration,
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
