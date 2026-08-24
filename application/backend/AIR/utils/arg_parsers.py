import argparse


DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-6"
ANTHROPIC_MODEL_FIELDS = (
    "execution_model",
    "hypothesis_model",
    "memory_llm_model",
    "review_optimization_model",
    "review_designer_model",
    "director_model",
    "report_summarization_model",
    "single_search_model",
    "opentargets_model",
)


def anthropic_model_id(value):
    """Validate the Anthropic identifier contract used by DiscoveryAgent."""
    model_id = str(value or "").strip()
    if not model_id.startswith("claude-"):
        raise argparse.ArgumentTypeError(
            f"{model_id or 'The selected model'} is not a valid Anthropic Claude model identifier"
        )
    return model_id


def normalize_anthropic_model_configuration(args):
    """Apply one project model to every backend model role and validate all.

    Historical checkpoint configs do not have ``project_model``; their
    independently snapshotted fields remain intact on resume.  New V2 configs
    include it, making this the backend-side invariant that all agent roles use
    the same selected Anthropic model.
    """
    project_model = getattr(args, "project_model", None)
    try:
        if project_model:
            project_model = anthropic_model_id(project_model)
            args.project_model = project_model
            for field in ANTHROPIC_MODEL_FIELDS:
                setattr(args, field, project_model)
            return args

        for field in ANTHROPIC_MODEL_FIELDS:
            setattr(args, field, anthropic_model_id(getattr(args, field)))
    except argparse.ArgumentTypeError as exc:
        raise ValueError(str(exc)) from exc
    return args


def create_hb_sherlock_parser():
    """Argument parser for scripts/run_hb_sherlock.py."""
    parser = argparse.ArgumentParser(description="Run HypothesisBrancher workflow on any dataset")

    # Config file arguments (checkpoint or YAML)
    parser.add_argument("--config-file", type=str, default=None,
                        help="Path to config.json file from a previous checkpoint to load configuration from. "
                             "Command-line arguments will override config file values.")
    parser.add_argument("--config-yaml", type=str, default=None,
                        help="Path to YAML file containing run arguments. CLI overrides YAML.")

    # HypothesisBrancher-specific parameters
    parser.add_argument("--num_rounds", type=int, default=None,
                        help="Number of rounds to execute (default: 1)")
    parser.add_argument("--num_branches", type=int, default=1,
                        help="Number of parallel branches per round (default: 1)")
    parser.add_argument("--num_parallel_workflows", type=int, default=1,
                        help="Number of workflows to run in parallel (default: 1, parallel not yet implemented)")
    parser.add_argument("--num_exploration_rounds", type=int, default=None,
                        help="Number of exploration rounds (default: None)")
    parser.add_argument("--separate_hypotheses_generation", action="store_true",
                        help="Enable separate hypotheses generation")
    parser.add_argument("--legacy_workflow", action="store_true",
                        help="Use legacy AIRWorkflowModular instead of DirectorWorkflow inside HypothesisBrancher")

    # Required arguments (can be provided via --config-file or command line)
    parser.add_argument("--working_directory", type=str, default=None,
                        help="Working directory path where the dataset is located")
    parser.add_argument("--dataset_name", type=str, default=None)
    parser.add_argument("--log-dir", type=str, default=None, dest="log_dir",
                        help="Log directory path (automatically set from config during resumption)")
    parser.add_argument("--checkpoint-base-dir", type=str, default=None, dest="checkpoint_base_dir",
                        help="Checkpoint base directory path (automatically set from config during resumption)")

    parser.add_argument("--analysis_name", type=str, default=None,
                        help="Name for the analysis (e.g., 'eeg_analysis', 'dam_analysis'). "
                             "Required unless loading from --config-file.")

    # Optional arguments
    parser.add_argument("--task_description_file", type=str, default=None,
                        help="Path to file containing task description (relative to working_directory or absolute path). "
                             "If not provided, only dataset description from README.txt will be used.")
    parser.add_argument("--task_description", type=str, default=None,
                        help="Inline task description loaded from config.json/YAML. "
                             "task_description_file takes precedence when both are present.")
    parser.add_argument("--readme-path", type=str, dest="readme_path", default=None,
                        help="Optional path to dataset README to use instead of data/README.txt")
    parser.add_argument("--num_queries", type=int, default=5,
                        help="Number of queries to generate from hypothesis agent per branch (default: 5)")
    parser.add_argument("--execution_max_turns", type=int, default=0,
                        help="Maximum number of turns for the execution agent. "
                             "0 (the default) lets it run until it finishes the task.")

    # Models
    parser.add_argument("--project_model", type=anthropic_model_id, default=None,
                        help="One Anthropic model for every DiscoveryAgent agent role")
    parser.add_argument("--execution_model", type=anthropic_model_id, default=DEFAULT_ANTHROPIC_MODEL,
                        help="LLM model for the execution agent")
    parser.add_argument("--hypothesis_model", type=anthropic_model_id, default=DEFAULT_ANTHROPIC_MODEL,
                        help="Model for the hypothesis agent")
    parser.add_argument("--memory_llm_model", type=anthropic_model_id, default=DEFAULT_ANTHROPIC_MODEL,
                        help="LLM model for the memory agent")
    parser.add_argument("--review_optimization_model", type=anthropic_model_id, default=DEFAULT_ANTHROPIC_MODEL,
                        help="LLM model for the review optimization agent")
    parser.add_argument("--review_designer_model", type=anthropic_model_id, default=DEFAULT_ANTHROPIC_MODEL,
                        help="LLM model for the review designer agent")
    parser.add_argument("--director_model", type=anthropic_model_id, default=DEFAULT_ANTHROPIC_MODEL,
                        help=f"LLM model for the director agent (default: {DEFAULT_ANTHROPIC_MODEL})")
    parser.add_argument("--report_summarization_model", type=anthropic_model_id, default=DEFAULT_ANTHROPIC_MODEL,
                        help=f"LLM model for report summarization (default: {DEFAULT_ANTHROPIC_MODEL})")
    parser.add_argument("--single_search_model", type=anthropic_model_id, default=DEFAULT_ANTHROPIC_MODEL,
                        help=f"LLM model for the single search agent (default: {DEFAULT_ANTHROPIC_MODEL})")
    parser.add_argument("--single_search_max_tokens", type=int, default=16384,
                        help="Maximum output tokens for the single search agent (default: 16384)")
    # Paperclip is the default deep-research provider; this flag is an optional override,
    # not a required config key — missing or empty values always fall back to "paperclip".
    parser.add_argument("--deep_research_provider", type=str, default="paperclip",
                        choices=["paperclip", "perplexity"],
                        help="Retrieval backend for the deep_research agent (default: paperclip). "
                             "Optional override — paperclip is used whenever this key is absent.")
    parser.add_argument("--enable_deep_research_in_review", action="store_true",
                        dest="enable_deep_research_in_review",
                        help="Allow the within-round reviewer to flag a literature-sensitive claim "
                             "and validate it via DeepResearchAgent (default: enabled)")
    parser.add_argument("--no-enable-deep-research-in-review", action="store_false",
                        dest="enable_deep_research_in_review",
                        help="Disable DeepResearch-assisted review validation")
    parser.set_defaults(enable_deep_research_in_review=True)

    # Human hypothesis selection arguments
    parser.add_argument("--human-selection", action="store_true",
                        help="Enable human hypothesis selection mode (step-by-step execution)")
    parser.add_argument("--selected-hypothesis-file", type=str, default=None,
                        help="Path to file containing human-selected hypothesis (for resume after selection)")
    parser.add_argument("--no-auto-hypothesis", action="store_true",
                        help="Disable automatic hypothesis generation at end of rounds (only effective with --human-selection)")

    parser.add_argument("--omit_report_summarization", action="store_true", help="Omit report summarization")
    parser.add_argument("--omit_deepresearch", action="store_true",
                        help="Omit deep research agent")
    parser.add_argument("--omit_singlesearch", action="store_true",
                        help="Omit single search agent")
    parser.add_argument("--use_acs_agent", action="store_true",
                        help="Include ACS data agent for Census American Community Survey queries")
    parser.add_argument("--acs_output_dir", type=str, default=None,
                        help="Output directory for ACS downloads. If relative, it is resolved against working_directory (default: <working_directory>/acs_downloads)")

    # Census data agent arguments (broader Census Bureau access: ACS + PUMS + IPUMS)
    parser.add_argument("--use_census_agent", action="store_true",
                        help="Include Census data agent for broad U.S. Census Bureau data access (ACS, PUMS, IPUMS)")
    parser.add_argument("--census_output_dir", type=str, default=None,
                        help="Output directory for Census downloads. If relative, it is resolved against working_directory (default: <working_directory>/census_downloads)")

    # OpenTargets agent arguments
    parser.add_argument("--use_opentargets_agent", action="store_true",
                        help="Include OpenTargets agent for drug discovery and target validation queries")
    parser.add_argument("--opentargets_output_dir", type=str, default=None,
                        help="Output directory for OpenTargets downloads. If relative, resolved against working_directory. "
                             "In expanding_dataset mode, defaults to expanding_dataset_dir.")
    parser.add_argument("--opentargets_max_turns", type=int, default=15,
                        help="Maximum Claude Code turns per OpenTargets query (default: 15)")
    parser.add_argument("--opentargets_model", type=anthropic_model_id, default=DEFAULT_ANTHROPIC_MODEL,
                        help=f"Model for the OpenTargets agent Claude Code session (default: {DEFAULT_ANTHROPIC_MODEL})")

    # OpenTargets MCP for execution agent (direct MCP access within ExecutionAgentCC)
    parser.add_argument("--execution_opentargets_mcp", action="store_true",
                        help="Enable OpenTargets MCP servers directly within the execution agent (Claude Code)")
    parser.add_argument("--execution_opentargets_servers", type=str, default=None,
                        help="Comma-separated list of OpenTargets MCP servers for execution agent. "
                             "Options: genetics,disease,association,target,expression,drug. "
                             "Default: all servers enabled")
    parser.add_argument("--execution_opentargets_mcp_path", type=str, default=None,
                        help="Path to the OpenTargets MCP package (default: /home/groups/jamesz/oqueen/virtualbiotech_mcp_package)")
    parser.add_argument("--execution_opentargets_data_path", type=str, default=None,
                        help="Path to OpenTargets data directory (default: /oak/stanford/groups/jamesz/hgzhang/targets/open_target_25_09)")
    parser.add_argument("--execution_skills_path", type=str, default=None,
                        help="Claude Code skills path(s) for the execution agent. "
                             "Accepts: (1) a single path, (2) comma-separated paths via CLI, "
                             "or (3) a YAML list of paths. Each path can be a skill directory "
                             "containing SKILL.md or a parent directory containing multiple skill folders.")

    parser.add_argument("--review_optimization_steps", type=int, default=None,
                        help="Number of queries between review optimization feedback")
    parser.add_argument("--review_search", action="store_true", help="Use search for review optimization")
    parser.add_argument("--review_design", action="store_true",
                        help="Use ReviewDesigner to generate custom reviewer prompts based on research task")
    parser.add_argument("--claude_md_generator", action="store_true", help="Use CLAUDE.md generator")
    parser.add_argument("--log_execution_agent", action="store_true", dest="log_execution_agent",
                        help="Enable per-query execution agent SDK logging into checkpoint folders (default: True)")
    parser.add_argument("--no-log_execution_agent", action="store_false", dest="log_execution_agent",
                        help="Disable per-query execution agent SDK logging")
    parser.set_defaults(log_execution_agent=True)
    parser.add_argument("--explicit_hypothesis_step", type=int, default=None,
                        help="Step at which to start explicit hypothesis generation")
    parser.add_argument("--exploratory_phase_warmup_steps", type=int, default=None,
                        help="Number of warmup steps for the exploratory phase")
    parser.add_argument("--hold_reviews_in_exploratory_phase", action="store_true",
                        dest="hold_reviews_in_exploratory_phase",
                        help="Hold reviews in the exploratory phase (default: True, use --no-hold-reviews to disable)")
    parser.add_argument("--no-hold-reviews-in-exploratory-phase", action="store_false",
                        dest="hold_reviews_in_exploratory_phase",
                        help="Disable holding reviews in exploratory phase (reviews will run from the start)")
    parser.set_defaults(hold_reviews_in_exploratory_phase=True)
    parser.add_argument("--no_sandbox", dest="no_sandbox", action="store_true",
                        help="Disable sandbox mode and run in original directory")
    parser.add_argument("--no-sandbox-enforcement", dest="no_sandbox_enforcement", action="store_true",
                        help="Disable sandbox enforcement hooks (agent can read/write outside sandbox)")
    parser.add_argument("--allowed-read-paths", type=str, default=None, dest="allowed_read_paths",
                        help="Comma-separated list of additional absolute paths the agent may read from")
    parser.add_argument("--existing_codebase", type=str, default=None,
                        help="Path to existing codebase directory with pre-built structure (bypasses sandbox creation)")
    parser.add_argument("--claude_md_template_path", type=str, default=None,
                        help="Path to CLAUDE.md template file for sandbox initialization")
    parser.add_argument("--hint_txt_path", type=str, default=None,
                        help="Path to txt file that contains a hint; is simply appended to the README")
    parser.add_argument("--codebase_structure_prompt_path", type=str,
                        default="/AIR_project/instructions/PROJECT_STRUCTURE.txt",
                        help="Path to txt file that contains a codebase structure prompt")
    parser.add_argument("--init_code_dir_list", type=str, default=None,
                        help="Optional comma-separated list of directories to initialize in the codebase")
    parser.add_argument("--sub_agent_architecture", action="store_true", help="Use sub-agent architecture")
    parser.add_argument("--background_path", type=str, default=None,
                        help="Path to directory containing prior-work PDFs to summarize into `deep_research_background.md`")
    parser.add_argument("--background_report_path", type=str, default=None,
                        help="Path to a text file containing accumulated knowledge/report from previous rounds")
    parser.add_argument("--generate_background_report", action="store_true",
                        help="Generate a background report for the hypothesis agent")
    parser.add_argument("--pressure_multiturn_prompt", action="store_true",
                        help="(Deprecated) Pressure prompt is now the default")
    parser.add_argument("--legacy_multiturn_prompt", action="store_true",
                        help="Use legacy multiturn prompt behavior (no pressure prompt)")
    parser.add_argument("--use_simple_memory", action="store_true", dest="use_simple_memory",
                        help="Use simple (1-agent) memory instead of ACE (3-agent) memory")
    parser.add_argument("--git_tracking", action="store_true",
                        help="Enable git version control tracking of workflow changes")

    parser.add_argument("--allow_director_to_stop", action="store_true",
                        help="Allow director to stop the workflow")
    parser.add_argument("--generate_plan", action="store_true",
                        help="Generate a plan for each hypothesis")
    parser.add_argument("--use_deep_research_for_hypothesis_generation", action="store_true",
                        dest="use_deep_research_for_hypothesis_generation",
                        help="Generate a per-round external literature novelty/grounding context "
                             "via DeepResearchAgent and inject it into hypothesis generation (default: enabled)")
    parser.add_argument("--no-use-deep-research-for-hypothesis-generation", action="store_false",
                        dest="use_deep_research_for_hypothesis_generation",
                        help="Disable DeepResearch-backed hypothesis generation context")
    parser.set_defaults(use_deep_research_for_hypothesis_generation=True)

    # Compaction support (Anthropic only)
    parser.add_argument("--enable_compaction", action="store_true",
                        help="Enable server-side context compaction for long conversations (Anthropic only, requires claude-opus-4-6)")
    parser.add_argument("--compaction_trigger_tokens", type=int, default=150000,
                        help="Token threshold to trigger compaction (default: 150000, min: 50000)")

    # Expanding dataset mode arguments
    parser.add_argument("--expanding-dataset", action="store_true", dest="expanding_dataset",
                        help="Enable expanding dataset mode. The data directory is treated as a growing "
                             "directory and dataset_description is refreshed after each query to reflect "
                             "new files. Agents can add files to the data/ directory.")
    parser.add_argument(
        "--expanding-dataset-dir", "--expanding-data-directory",
        type=str,
        default="data/downloads",
        dest="expanding_dataset_dir",
        help="Path to the data directory to monitor for expanding dataset mode. If relative, it is resolved against working_directory. Defaults to data/downloads"
    )

    return parser


def create_interactive_parser():
    """Argument parser for cli/run_interactive.py."""
    parser = argparse.ArgumentParser(
        description=(
            "Launch an interactive DiscoveryAgent research session.\n\n"
            "Agent configuration (models, tools, task description) is loaded automatically\n"
            "from the latest checkpoint in --checkpoint-dir. Use --config-override to\n"
            "change any of those settings."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--checkpoint-dir", required=True, dest="checkpoint_dir",
        metavar="DIR",
        help="Log directory from a previous automated run (contains checkpoints/round_N/). "
             "Agent config and accumulated findings are loaded from the latest round automatically.",
    )
    parser.add_argument(
        "--code-dir", required=True, dest="code_dir",
        metavar="DIR",
        help="Working directory containing the code and data for this project.",
    )
    parser.add_argument(
        "--log-dir", default=None, dest="log_dir",
        metavar="DIR",
        help="Output directory for interactive session logs and checkpoints. "
             "Defaults to a new run folder under the interactive session directory.",
    )
    parser.add_argument(
        "--config-override", default=None, dest="config_override",
        metavar="FILE",
        help="Path to a YAML file with config values to override the checkpoint config "
             "(e.g., execution_model, hypothesis_model, omit_deepresearch).",
    )
    session_group = parser.add_mutually_exclusive_group()
    session_group.add_argument(
        "-c", "--continue", dest="continue_session", action="store_true",
        help="Continue the most recent interactive session for this checkpoint/code directory.",
    )
    session_group.add_argument(
        "-r", "--resume", dest="resume_session", metavar="SESSION",
        help="Resume a specific interactive session by session ID, unique prefix, or session path.",
    )
    parser.add_argument(
        "-n", "--name", dest="session_name", default=None, metavar="NAME",
        help="Display name for a new interactive session.",
    )

    return parser
