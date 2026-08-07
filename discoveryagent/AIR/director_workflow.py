"""
Director-Based Workflow

This workflow implements a hypothesis-directed research approach where:
1. HypothesisGenAgent generates an initial hypothesis at the start (always, not optional)
2. DirectorAgent receives the hypothesis and directs research to test it
3. Tool agents execute the research queries
4. The workflow focuses on testing and validating the proposed hypothesis

KEY DIFFERENCES FROM AIRWorkflowModular:
- ALWAYS generates hypothesis first (no init_generate_hypothesis parameter)
- Hypothesis is shown to DirectorAgent via feedback on first query
- No mid-workflow hypothesis generation (explicit_hypothesis_step not used)
- DirectorAgent is instructed to "explore and test" the hypothesis
- Cleaner separation: HypothesisGenAgent generates, DirectorAgent tests

UNCERTAINTIES & NOTES:
1. Hypothesis Presentation: Currently using feedback mechanism to show hypothesis
   to director. Alternative would be to add it to dataset_description or as a
   separate parameter to generate_query(). Current approach keeps it visible
   in logs and allows for easy modification.

2. Resume Behavior: When resuming, we assume director agent state contains
   hypothesis context. Could alternatively load hypothesis from checkpoint
   and re-inject it. Need to test resume behavior.

3. Hypothesis Storage: hypothesis_output is stored in local variable but not
   as instance variable. Should we store self.hypothesis_output for access
   later (e.g., in final report)?

4. Multiturn Context: Both director and hypothesis agents maintain their own
   conversation contexts. The hypothesis context after generation is not
   explicitly transferred to director (only via the feedback text). This
   seems intentional but worth noting.

5. Final Report: Currently uses director_agent.generate_final_report() which
   will see its own conversation history. Should final report mention the
   original hypothesis? Currently implicit in director's context.
"""

import re
import inspect
import openai
import anthropic
import os
import json
import datetime
import pathlib
import time
import threading
import itertools
from pathlib import Path
import shutil

from typing import Optional, List, Dict, Any
from termcolor import colored
from .utils.logger import Logger
from .utils import progress_recorder
from .config import LLMConfig, ExecutionAgentConfig, HypothesisGenerationConfig, get_default_execution_config, get_default_hypothesis_config
from .context_managers import NotebookManager, NotebookManagerXML, strip_code_markers
from .tool_agents.execution_agent_cc import ExecutionAgentCC
from .checkpoint_manager import CheckpointManager
from .tool_agents.deep_research import DeepResearchAgent, DeepResearchConfig
from .prompts.tools import DEEP_RESEARCH_TAG
# from .hyp_agents.hypothesis_agent import CodeActHypothesisAgent
# from .hyp_agents.hypothesis_agent_multiturn import CodeActHypothesisAgentMultiTurn
from .hyp_agents.director_agent import DirectorAgent
from .hyp_agents.hypothesis_gen_agent import HypothesisGenAgent, _is_retrieval_failure_markdown
import nbformat as nbf
from .prompts.hyp_generation import (
    EVIDENCE_DRIVEN_HYPOTHESIS_GENERATION_SYSTEM_PROMPT,
    DATASET_DESCRIPTION_PROMPT,
    REPORT_PROMPT
)

from .tool_agents.base_llm_with_search import BaseLLMWithSearch
from .tool_agents.agent_directory import STRING_TO_AGENT_MAP
from .tool_agents.execution_agent_cc import ExecutionAgentCC

from .base_llm import OnePassLLM
from .meta_agents.review_designer import ReviewDesigner
from .utils.pdf_loader import load_pdf_texts_from_directory
from .utils.git_version_control import GitVersionControl

REPORT_SUMMARY_PROMPT = """
You will be shown multiple queries and results of these queries for explorations of an AI scientists on a dataset.
Your job is to summarize the findings and main results within these queries and explorations.
You must faithfully represent and summarize the findings and explorations of the agent in its exploratory process.
You should follow standard scientific writing practices and faithfully intepret the data presented to you in order to form a narrative about the exploration thus far.
Your outputs will be read by a critic, so be sure to include all details that would be needed to properly review the work thus far.
"""

BACKGROUND_PDF_SUMMARY_SYSTEM_PROMPT = """
You are an expert scientific reader. Summarize prior work using only the PDF text provided by the user.
Do not perform web search. Do not rely on outside knowledge. If the extracted text is incomplete or noisy,
state that limitation explicitly instead of filling gaps from memory.
"""

def tool_agent_prompt_generator(agent_list: List[Any]) -> str:
    """
    Generate a prompt for a list of tool agents.
    """
    prompt = "You are given access to helper agents which can be used to complete your task."
    for i, a in enumerate(agent_list):
        a_config = a.config_to_prompt()
        prompt += f"\n\n**Helper Agent {i+1}**:\n{a_config}"
    return prompt


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


def _generate_background_report(
    dataset_description: str,
    task_description: Optional[str] = None,
    background_path: Optional[str] = None,
    log_dir: str = "logs",
    summary_llm_config: Optional[LLMConfig] = None,
    max_output_tokens: int = 8192,
    deep_research_provider: str = "paperclip",
) -> str:
    """
    Create a background report.

    Args:
        dataset_description: Description of the dataset being explored.
        task_description: Task/goal description for the run.
        background_path: Optional directory of PDFs to summarize into the report.
        log_dir: Directory for agent logging/output.
        summary_llm_config: Optional LLM config used for PDF-only summarization mode.
        max_output_tokens: Maximum output tokens for the generated report.
        deep_research_provider: "paperclip" (default) or "perplexity". Selects the
            retrieval backend for the non-PDF (deep_research) report path below.

    Returns:
        The generated Markdown report as a string.
    """
    if background_path:
        pdf_documents = load_pdf_texts_from_directory(
            background_path,
            max_chars_per_pdf=20000,
        )
        if not pdf_documents:
            raise ValueError(f"No readable PDF files found in {background_path}")

        llm_config = summary_llm_config
        if llm_config is None:
            if os.getenv("ANTHROPIC_API_KEY"):
                llm_config = LLMConfig(
                    model_name="claude-sonnet-4-5-20250929",
                    api_provider="anthropic",
                    max_tokens=max_output_tokens,
                )
            elif os.getenv("ZOULAB_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"):
                llm_config = LLMConfig(
                    model_name="gpt-4o-mini",
                    api_provider="openai",
                    max_tokens=max_output_tokens,
                )
            else:
                raise ValueError(
                    "background_path now requires a standard LLM to summarize the supplied PDFs, "
                    "but no summary_llm_config was provided and no OpenAI/Anthropic API key was found."
                )

        pdf_sections = []
        total_chars = 0
        max_total_chars = 120000
        truncated = False
        for pdf_doc in pdf_documents:
            text = (pdf_doc.get("text") or "").strip()
            if not text:
                text = "[No extractable text was found in this PDF.]"

            section = (
                f"## PDF: {pdf_doc['filename']}\n"
                f"File size: {pdf_doc['size_bytes'] / (1024 * 1024):.1f} MB\n\n"
                f"{text}"
            )

            if total_chars + len(section) > max_total_chars:
                remaining = max_total_chars - total_chars
                if remaining > 0:
                    pdf_sections.append(section[:remaining])
                truncated = True
                break

            pdf_sections.append(section)
            total_chars += len(section)

        prompt = f"""Summarize the prior work described in the provided PDFs.

Only use the document text below. Do not use web search, README context, task context, or any outside knowledge.

Goals:
1) Summarize each paper or document's main question, methods, and findings when the text supports it.
2) Synthesize cross-paper themes, recurring methods, and important disagreements.
3) Identify limitations or ambiguities caused by incomplete PDF text extraction.
4) Conclude with actionable takeaways and open questions grounded only in these PDFs.

Return a detailed Markdown report with sections for:
- Executive Summary
- Per-Document Summaries
- Cross-Document Themes
- Limitations of the Provided Documents
- Actionable Takeaways

Provided PDF text:

{chr(10).join(pdf_sections)}
"""

        if truncated:
            prompt += "\n\nNote: the provided PDF excerpts were truncated to fit the summarization context window."

        llm = OnePassLLM(
            system_prompt=BACKGROUND_PDF_SUMMARY_SYSTEM_PROMPT,
            llm_config=llm_config,
        )
        return llm(prompt, max_tokens=max_output_tokens)

    # llm_config is only consumed by the "perplexity" (and legacy "openai") path;
    # it's still built unconditionally so that fallback keeps working unchanged.
    llm_config = LLMConfig(model_name="sonar-deep-research", api_provider="perplexity")
    deep_research_config = DeepResearchConfig(
        llm_config=llm_config,
        log_dir=log_dir,
        max_output_tokens=max_output_tokens,
        deep_research_provider=deep_research_provider,
    )
    agent = DeepResearchAgent(config=deep_research_config)

    tdstr = f"\n## Task description\n{task_description}\n" if task_description else ""

    prompt = f"""Generate a thorough background report that encompasses previous work that's been done related to the following dataset and task:

## Dataset description
{dataset_description}
{tdstr}
Goals:
1) Summarize the work that's been done on this dataset and related datasets in the past.
2) Highlight considerations and methods that have been successful for analyzing this type of data.
3) Research some specific research questions that can be investigated with this type of data.
4) Keep it actionable and tightly scoped to the dataset/task.
5) Be precise and scientific. This report will be used for scientific decision-making, so be transparent and rigorous in your reporting.
6) Focus on published, peer-reviewed research articles when gathering sources.

Report your findings in Markdown format in a detailed, research-style report.
"""

    report = agent.execute(prompt)

    return report


def _generate_hypothesis_literature_context(
    dataset_description: str,
    task_description: Optional[str],
    round_num: Optional[int],
    log_dir: str,
    deep_research_provider: str = "paperclip",
) -> Optional[str]:
    """
    Generate a focused, external-literature novelty/grounding context for hypothesis
    generation, using the existing DeepResearchAgent. Modeled on _generate_background_report()'s
    DeepResearchAgent usage, but targets a distinct, narrower query (literature novelty/
    contradiction/gap context, not a general background summary) and is round-scoped rather
    than whole-run-cached, since it should reflect the current round rather than be reused
    across the whole HypothesisBrancher run.

    Always persists the raw result (success or failure) under the round-scoped searches
    directory for audit, but returns None on retrieval failure/empty results so callers
    don't inject error text into the hypothesis generation prompt. Never raises.
    """
    task_section = f"\n\n## Task description\n{task_description}\n" if task_description else ""

    query = f"""Conduct a focused external literature review to support novelty filtering and
grounding for hypothesis generation on the following dataset and task. Do not summarize our
own prior internal analysis or memory reports -- focus only on the external scientific literature.

## Dataset description
{dataset_description}
{task_section}
Report your findings in Markdown, covering:
1) Known mechanisms relevant to this task.
2) Disputed or contradictory findings in the literature.
3) Candidate biological/mechanistic factors, variables, or interventions worth considering,
   appropriate to the system and context described above.
4) What is already well-established and therefore less novel to propose as a new hypothesis.
5) Gaps in the literature that remain open.
6) Concrete, specific predictions that could be tested with data of this type.
7) Source/result IDs for any retrieved papers or documents, where available.
8) Explicit warnings about claims that are likely already well-known, overclaimed in prior
   literature, or overgeneralized from a different system, organism, disease area, cell type,
   assay, or intervention rather than the specific context under study here.

Be precise and scientific; this will be used to filter out non-novel hypotheses, not to
generate hypotheses directly.
"""

    round_label = round_num if isinstance(round_num, int) and round_num >= 0 else 0
    searches_dir = Path(log_dir) / "searches" / f"round_{round_label}"
    try:
        searches_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    try:
        # llm_config is only consumed by the "perplexity" (and legacy "openai") path;
        # it's still built unconditionally so that fallback keeps working unchanged.
        llm_config = LLMConfig(model_name="sonar-deep-research", api_provider="perplexity")
        config = DeepResearchConfig(
            llm_config=llm_config,
            log_dir=log_dir,
            deep_research_provider=deep_research_provider,
        )
        agent = DeepResearchAgent(config=config)
        result_markdown = agent.execute(query, searches_dir=str(searches_dir))
    except Exception as exc:
        # DeepResearchAgent.execute() is expected to never raise, but this is a safety net
        # so a literature-context failure can never break hypothesis generation.
        result_markdown = f"**[RETRIEVAL: ERROR]** Unexpected error generating literature context: {exc}"

    is_failure = _is_retrieval_failure_markdown(result_markdown)

    try:
        (searches_dir / "hypothesis_literature_context.md").write_text(result_markdown or "")
    except Exception:
        pass

    try:
        sidecar = {
            "provider": deep_research_provider,
            "round_num": round_label,
            "status": "error" if is_failure else "ok",
            "timestamp": datetime.datetime.now().isoformat(),
            "markdown_artifact": "hypothesis_literature_context.md",
            "note": (
                "If deep_research_provider == 'paperclip', DeepResearchAgent already wrote its "
                "own timestamped audit sidecar (deep_research_paperclip_<ts>.json) in this same "
                "directory with structured result IDs/citations; this file is intentionally a "
                "thin pointer, not a duplicate of that audit."
            ),
        }
        (searches_dir / "hypothesis_literature_context.json").write_text(
            json.dumps(sidecar, indent=2, default=str)
        )
    except Exception:
        pass

    if is_failure:
        return None
    return result_markdown


_NEEDS_LITERATURE_CHECK_RE = re.compile(
    r"<NEEDS_LITERATURE_CHECK>([\s\S]*?)</NEEDS_LITERATURE_CHECK>", re.IGNORECASE
)


def _extract_needs_literature_check(reviewer_output: Optional[str]) -> Optional[str]:
    """Extract the first <NEEDS_LITERATURE_CHECK>...</NEEDS_LITERATURE_CHECK> claim, if any."""
    if not reviewer_output:
        return None
    match = _NEEDS_LITERATURE_CHECK_RE.search(reviewer_output)
    if not match:
        return None
    claim = match.group(1).strip()
    return claim or None


def _generate_review_literature_context(
    claim: str,
    round_num: Optional[int],
    query_num: Optional[int],
    log_dir: str,
    deep_research_provider: str = "paperclip",
) -> Optional[str]:
    """
    Generate a focused external-literature validation context for a reviewer-flagged claim,
    using the existing DeepResearchAgent. Modeled on _generate_hypothesis_literature_context(),
    but targets claim validation (established/contradicted/specificity/overclaiming/safety)
    rather than general novelty/grounding for new hypotheses.

    Always persists the raw result (success or failure) under the round-scoped searches
    directory for audit, but returns None on retrieval failure/empty results so callers
    don't inject error text into review feedback. Never raises.
    """
    query = f"""Validate the following claim against the external scientific literature. Do not
summarize our own prior internal analysis or memory reports -- focus only on the external
scientific literature.

## Claim to validate
{claim}

Answer the following succinctly in Markdown:
1) Is this claim already well-established in the literature, or is it genuinely underexplored?
2) Is there contradictory or complicating evidence?
3) Is the claim specific to the biological system, organism, disease area, cell type,
   perturbation/intervention, or dataset context under study, or is it overgeneralized from a
   different model system, disease context, cell type, assay, or intervention?
4) Is the claim overclaimed relative to what the literature actually supports?
5) Are there known safety risks, translational limitations, confounders, or mechanism-specific
   caveats worth noting?
6) What is the most cautious, defensible interpretation of the external literature, and what
   evidence would make this claim more or less plausible in the current dataset?

Include source/result IDs where available. Be precise and scientific; this will be used to
sanity-check a research claim, not to generate new hypotheses.
"""

    round_label = round_num if isinstance(round_num, int) and round_num >= 0 else 0
    query_label = query_num if isinstance(query_num, int) and query_num >= 0 else 0
    searches_dir = Path(log_dir) / "searches" / f"round_{round_label}"
    try:
        searches_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    try:
        # llm_config is only consumed by the "perplexity" (and legacy "openai") path;
        # it's still built unconditionally so that fallback keeps working unchanged.
        llm_config = LLMConfig(model_name="sonar-deep-research", api_provider="perplexity")
        config = DeepResearchConfig(
            llm_config=llm_config,
            log_dir=log_dir,
            deep_research_provider=deep_research_provider,
        )
        agent = DeepResearchAgent(config=config)
        result_markdown = agent.execute(query, searches_dir=str(searches_dir))
    except Exception as exc:
        # DeepResearchAgent.execute() is expected to never raise, but this is a safety net
        # so a review-literature-check failure can never break the review loop.
        result_markdown = f"**[RETRIEVAL: ERROR]** Unexpected error generating review literature context: {exc}"

    is_failure = _is_retrieval_failure_markdown(result_markdown)

    base_name = f"review_literature_context_query_{query_label}"
    try:
        (searches_dir / f"{base_name}.md").write_text(result_markdown or "")
    except Exception:
        pass

    try:
        sidecar = {
            "provider": deep_research_provider,
            "round_num": round_label,
            "query_num": query_label,
            "claim": claim,
            "status": "error" if is_failure else "ok",
            "timestamp": datetime.datetime.now().isoformat(),
            "markdown_artifact": f"{base_name}.md",
            "note": (
                "If deep_research_provider == 'paperclip', DeepResearchAgent already wrote its "
                "own timestamped audit sidecar (deep_research_paperclip_<ts>.json) in this same "
                "directory with structured result IDs/citations; this file is intentionally a "
                "thin pointer, not a duplicate of that audit."
            ),
        }
        (searches_dir / f"{base_name}.json").write_text(json.dumps(sidecar, indent=2, default=str))
    except Exception:
        pass

    if is_failure:
        return None
    return result_markdown


class DirectorWorkflow:
    """
    Director-based workflow that follows a hypothesis-directed research pattern.

    This workflow always generates a hypothesis first using HypothesisGenAgent, then
    uses DirectorAgent to direct research queries aimed at testing and validating
    that hypothesis.
    """
    def __init__(self,
                 hypothesis_config: HypothesisGenerationConfig,
                 dataset_description: str,
                 task_description: str = None,
                 agent_config_list: Optional[List[Any]] = None,
                 max_queries: int = 10,
                 log_dir: str = "logs",
                 images_dir: str = "logs",
                 output_home: Optional[str] = None,
                 multiturn_hypothesis: bool = True,
                 review_optimization_steps = None,
                 review_optimization_llm_config = None,
                 review_search = False,
                 review_design = False,
                 review_designer_llm_config = None,
                 report_summarization_llm_config = None, # TODO: will need to update this in the future if we want it to be multi-agent
                 explicit_hypothesis_step = None,
                 exploratory_phase_warmup_steps = None,
                 hold_reviews_in_exploratory_phase = True,
                 background_path: Optional[str] = None,
                 background_report: Optional[str] = None,
                 git_tracking: bool = False,
                 report_filename: Optional[str] = "final_report.md",
                 max_tool_invocation_retries: int = 3,
                 director_hypothesis_config: Optional[HypothesisGenerationConfig] = None,
                 generate_background_report: bool = False,
                 # Paperclip is the default deep-research provider; an optional override.
                 # Missing or empty deep_research_provider always falls back to "paperclip".
                 deep_research_provider: str = "paperclip",
                 # Validate literature-sensitive reviewer claims via DeepResearchAgent.
                 # Does not change review cadence (controlled by review_optimization_steps).
                 enable_deep_research_in_review: bool = True,
        ):
        """
        Initialize the AIRWorkflow.

        Args:
            hypothesis_config (HypothesisGenerationConfig): Configuration for the hypothesis agent
            dataset_description (str): Description of the dataset
            agent_config_list (Optional[List[Any]]): List of agent configurations to use
            max_queries (int): Maximum number of queries to execute
            log_dir (str): Directory for logging
            images_dir (str): Directory for storing images
            output_home (Optional[str]): Output directory for results
            multiturn_hypothesis (bool): Enable multi-turn hypothesis generation
            review_optimization_steps (int): Number of steps between review optimizations
            review_optimization_llm_config (LLMConfig): Configuration for review optimization LLM
            review_search (bool): Enable web search tools for the reviewer
            review_design (bool): Use ReviewDesigner to generate custom reviewer prompts based on research task
            review_designer_llm_config (Optional[LLMConfig]): Configuration for the ReviewDesigner LLM.
                If None and review_design=True, defaults to claude-sonnet-4-6
            report_summarization_llm_config (LLMConfig): Configuration for report summarization LLM
            explicit_hypothesis_step (int): Step at which to start explicit hypothesis generation
            exploratory_phase_warmup_steps (int): Number of warmup steps for exploratory phase
            hold_reviews_in_exploratory_phase (bool): Hold reviews during exploratory phase
            background_path (Optional[str]): Path to directory containing prior-work PDFs to summarize
            background_report (Optional[str]): Accumulated knowledge/report from previous rounds.
                This is kept separate from the current round's report to cleanly track accumulated knowledge.
            git_tracking (bool): Enable git version control tracking of workflow changes
            report_filename (str): Filename for the final report (default: "final_report.md"). 
                Can include subdirectories (e.g., "reports/report_round1.md")
            max_tool_invocation_retries (int): Maximum number of retries for tool invocation format errors (default: 3)
            deep_research_provider (str): "paperclip" (default) or "perplexity". Selects the retrieval
                backend used when generating the background report via _generate_background_report().
            enable_deep_research_in_review (bool): If True, the within-round reviewer may flag a
                literature-sensitive claim with <NEEDS_LITERATURE_CHECK> and have it validated via
                DeepResearchAgent before the resulting feedback steers the next query. Disabled by
                default; does not change review cadence (still controlled by review_optimization_steps /
                hold_reviews_in_exploratory_phase / explicit_hypothesis_step).
        """
        self.agent_config_list = agent_config_list if agent_config_list is not None else []
        # Core hypothesis configuration used for HypothesisGenAgent and human-selection settings
        self.hypothesis_config = hypothesis_config
        self.dataset_description = dataset_description
        self.task_description = task_description
        self.max_queries = max_queries
        self.multiturn_hypothesis = multiturn_hypothesis
        self.review_search = review_search
        self.review_design = review_design
        self.git_tracking = git_tracking
        self.report_filename = report_filename
        self.max_tool_invocation_retries = max_tool_invocation_retries
        self.round_num = None  # Will be set by HypothesisBrancher if used
        self.background_report = background_report  # Accumulated knowledge from previous rounds
        self.log_dir = log_dir
        self.deep_research_provider = deep_research_provider
        self.enable_deep_research_in_review = enable_deep_research_in_review

        self.review_optimization_steps = review_optimization_steps
        self.review_optimization_llm_config = review_optimization_llm_config
        self.review_designer_llm_config = review_designer_llm_config
        self.report_summarization_llm_config = report_summarization_llm_config

        self.explicit_hypothesis_step = explicit_hypothesis_step
        self.exploratory_phase_warmup_steps = exploratory_phase_warmup_steps
        self.hold_reviews_in_exploratory_phase = hold_reviews_in_exploratory_phase

        self.generate_background_report = generate_background_report

        # Determine output_home: explicit arg -> default "outputs"
        self.output_home = output_home if output_home is not None else "outputs"

        # Initialize logger first so it can be passed to agents
        self.logger = Logger("director_workflow", log_dir=log_dir)

        if background_path and not self.generate_background_report:
            self.logger.log_action(
                "background_path_unused",
                "background_path was provided without generate_background_report; PDFs will not be injected directly into agents.",
            )
            print("ℹ️ background_path is now only used when generating `deep_research_background.md`.")

        if self.generate_background_report:
            self.background_report_path = Path(self.log_dir) / "deep_research_background.md"
            try:
                if self.background_report_path.exists():
                    # Re-use existing background report to avoid unnecessary regeneration
                    self.logger.log_action("background_report_exists", f"Using cached report at: {self.background_report_path}")
                    print(f"✅ Using existing background report at: {self.background_report_path}")
                else:
                    spinner_message = (
                        "🔎 Summarizing research background..."
                        if background_path
                        else "🔎 Generating background report..."
                    )
                    spinner_stop, spinner_thread = _start_spinner(spinner_message)
                    try:
                        generated_report = _generate_background_report(
                            dataset_description=self.dataset_description,
                            task_description=self.task_description,
                            background_path=background_path,
                            log_dir=self.log_dir,
                            summary_llm_config=(
                                director_hypothesis_config.llm_config
                                if director_hypothesis_config is not None
                                else hypothesis_config.llm_config
                            ),
                            deep_research_provider=deep_research_provider,
                        )
                    finally:
                        spinner_stop.set()
                        spinner_thread.join()

                    # if self.background_report:
                    #     self.background_report = self.background_report.strip() + "\n\n" + generated_report
                    # else:
                    #     self.background_report = generated_report

                    self.background_report_path.write_text(generated_report)
                    self.logger.log_action("background_report_generated", f"Wrote report to: {self.background_report_path}")
                    print(f"📝 Generated background report at: {self.background_report_path}")
            except Exception as e:
                self.logger.log_error(f"Failed to generate background report: {e}")
                print(f"⚠️ Failed to generate background report: {e}")
        else:
            self.background_report_path = None

        self._initialize_agents()

        self.hypothesis_agent_tool_prompt = tool_agent_prompt_generator(list(self.agents.values()))
        self.allow_director_to_stop = getattr(director_hypothesis_config, "allow_director_to_stop", False)

        # Initialize DirectorAgent for query generation and research orchestration.
        # If a separate director_hypothesis_config is provided, use it; otherwise fall back
        # to the main hypothesis_config so existing behavior is preserved.
        director_config = director_hypothesis_config if (director_hypothesis_config is not None) else hypothesis_config
        self.director_agent = DirectorAgent(
            config=director_config,
            tool_agent_prompt=self.hypothesis_agent_tool_prompt,
            logger=self.logger
        )

        # Initialize HypothesisGenAgent for hypothesis generation
        # Extract multi-hypothesis parameters from the core hypothesis_config
        n_hypotheses = getattr(self.hypothesis_config, 'n_hypotheses', 1)
        judge_llm_config = getattr(self.hypothesis_config, 'judge_llm_config', None)

        self.hypothesis_gen_agent = HypothesisGenAgent(
            config=self.hypothesis_config,
            background_report_path=self.background_report_path,
            logger=self.logger,
            n_hypotheses=n_hypotheses,
            judge_llm_config=judge_llm_config,
            helper_agents_prompt=self.hypothesis_agent_tool_prompt,
        )

        # Keep backward compatibility reference
        self.hypothesis_agent = self.director_agent
        
        # Store results
        self.results = []
        self.report = ""
        self.cumulative_report = ""

        # Initialize review tracking
        self.review_history = []  # List to track all reviews: [{query_num, round_num, timestamp, input_length, output_length}]
        self.generated_reviewer_prompt = None  # Store the generated reviewer prompt (if review_design=True)

        # Initialize image tracking
        self.images = {}  # Dictionary to track all images: {image_id: {filename, description, query_num, step_num}}
        self.image_counter = 0
        self.images_dir = os.path.join(images_dir, "images")
        os.makedirs(images_dir, exist_ok=True)

        if self.review_optimization_llm_config is not None:
            # Conditionally use ReviewDesigner based on review_design flag
            if self.review_design:
                # Use ReviewDesigner to generate a custom reviewer prompt based on the research task
                # Use provided config or default to claude-sonnet-4-6
                designer_llm_config = self.review_designer_llm_config or LLMConfig(model_name="claude-sonnet-4-5-20250929", max_tokens = 8192)
                review_designer = ReviewDesigner(
                    llm_config=designer_llm_config
                )
                
                # Add context about search tool availability
                additional_context = None
                if self.review_search:
                    additional_context = (
                        "The reviewer will have access to web search tools to evaluate the novelty "
                        "and impact of the agent's findings by searching for similar studies and relevant literature."
                    )

                if self.task_description is not None:
                    research_task = self.dataset_description +"\n\n## Task Description\n" + self.task_description
                else:
                    research_task = self.dataset_description
                
                # Generate the custom reviewer prompt
                generated_reviewer_prompt = review_designer.design_reviewer_prompt(
                    research_task=research_task,
                    additional_context=additional_context
                )

                # Store the generated prompt in the workflow instance
                self.generated_reviewer_prompt = generated_reviewer_prompt

                self.logger.log_action("reviewer_prompt_generated",
                    f"Generated custom reviewer prompt using {designer_llm_config.model_name} ({len(generated_reviewer_prompt)} chars)")

                self.logger.log_action("generated_reviewer_prompt", f"Generated reviewer prompt: {generated_reviewer_prompt}")
                self.logger.log_prompt("reviewer", generated_reviewer_prompt, "generated_reviewer_prompt")

                # Create a copy of the config with the generated system prompt
                review_config_with_prompt = LLMConfig(
                    model_name=self.review_optimization_llm_config.model_name,
                    api_provider=self.review_optimization_llm_config.api_provider,
                    api_key=self.review_optimization_llm_config.api_key,
                    temperature=self.review_optimization_llm_config.temperature,
                    frequency_penalty=self.review_optimization_llm_config.frequency_penalty,
                    max_tokens=self.review_optimization_llm_config.max_tokens,
                    system_prompt=generated_reviewer_prompt
                )
            else:
                # Use the provided config as-is (with its existing system_prompt if any)
                review_config_with_prompt = self.review_optimization_llm_config
                self.logger.log_action("reviewer_config_used", 
                    "Using provided review optimization config without ReviewDesigner")
            
            if self.review_search:
                self.review_optimization_llm = BaseLLMWithSearch(
                    llm_config=review_config_with_prompt,
                    logger=self.logger # Passed for redundancy
                )
            else:
                self.review_optimization_llm = OnePassLLM(
                    llm_config=review_config_with_prompt
                )
        self.report_summarization_llm = None
        if self.report_summarization_llm_config is not None:
            self.report_summarization_llm = OnePassLLM(
                system_prompt=REPORT_SUMMARY_PROMPT,
                llm_config=self.report_summarization_llm_config
            )

        # Initialize checkpoint manager with round number (if set)
        self.checkpoint_manager = CheckpointManager(
            log_dir=self.log_dir,
            workflow=self,
            round_num=self.round_num
        )

        # Save reviewer prompt checkpoint if it was generated
        if self.generated_reviewer_prompt is not None:
            try:
                # Determine the designer model name
                designer_model_name = None
                if hasattr(self, 'review_designer_llm_config') and self.review_designer_llm_config:
                    designer_model_name = self.review_designer_llm_config.model_name
                elif self.review_design:
                    designer_model_name = "claude-sonnet-4-5-20250929"  # Default from line 218

                reviewer_prompt_path = self.checkpoint_manager.save_reviewer_prompt_checkpoint(
                    reviewer_prompt=self.generated_reviewer_prompt,
                    designer_model=designer_model_name
                )
                if reviewer_prompt_path:
                    self.logger.log_action("reviewer_prompt_checkpointed",
                        f"Reviewer prompt saved to checkpoint at {reviewer_prompt_path}")
                    print(f"💾 Reviewer prompt checkpointed: {os.path.basename(reviewer_prompt_path)}")
                else:
                    self.logger.log_error("Failed to checkpoint reviewer prompt (empty path returned)")
            except Exception as e:
                error_msg = f"Error checkpointing reviewer prompt: {str(e)}"
                self.logger.log_error("reviewer_prompt_checkpoint_failed", error_msg)
                print(f"⚠️ {error_msg}")

        # Initialize git version control if enabled
        self.git_vc = None
        if self.git_tracking:
            # Get working directory from ExecutionAgentCC if available
            working_dir = None
            for agent in self.agents.values(): # Loops to find the ExecutionAgent - should be a small loop
                if isinstance(agent, ExecutionAgentCC) and hasattr(agent, 'working_directory'):
                    working_dir = agent.working_directory
                    break

            if working_dir:
                try:
                    self.git_vc = GitVersionControl(working_dir, logger=self.logger)
                    if self.git_vc.init_repo():
                        self.logger.log_action("git_tracking_enabled",
                            f"Git version control enabled for {working_dir}")
                        print(f"🔧 Git version control enabled")
                    else:
                        self.logger.log_error("Failed to initialize git repository")
                        self.git_vc = None
                except Exception as e:
                    self.logger.log_error(f"Failed to initialize git version control: {str(e)}")
                    self.git_vc = None
            else:
                self.logger.log_action("git_tracking_skipped",
                    "Git tracking enabled but no ExecutionAgentCC with working_directory found")
                print(f"⚠️ Git tracking enabled but no working directory available")

    def _initialize_agents(self):
        self.agents = {}
        for agent_config in self.agent_config_list:
            self.agents[agent_config.tag] = STRING_TO_AGENT_MAP[agent_config.agent_name](config=agent_config, logger=self.logger)

    def _test_iteration_condition_with_warmup(self, query_num, frequency) -> bool:
        """
        Test if we should perform an action (like review feedback) based on warmup period.

        This is used when hold_reviews_in_exploratory_phase is True to delay reviews
        until after the warmup period defined by exploratory_phase_warmup_steps.

        Args:
            query_num: Current query number
            frequency: How often to perform the action (e.g., every N queries)

        Returns:
            bool: True if the action should be performed, False otherwise
        """
        if self.explicit_hypothesis_step is None:
            return False # We're not using explicit hypotheses

        # Treat None as 0 (no warmup period)
        warmup_steps = self.exploratory_phase_warmup_steps if self.exploratory_phase_warmup_steps is not None else 0

        if query_num < warmup_steps:
            return False # We're before the exploratory phase
        elif query_num == warmup_steps:
            # We're right at the exploratory phase (or immediately if no warmup)
            return True
        elif ((query_num - warmup_steps) % frequency) == 0:
            # We're after the exploratory phase and now trying to generate explicit hypotheses
            return True
        else:
            return False # Else, should be false

    def _progress_round_dir(self, query_num: int) -> Optional[Path]:
        try:
            return self.checkpoint_manager.get_checkpoint_dir(query_num).parent
        except Exception:
            return None

    def execute_agent(self, hypothesis_agent_out: str) -> str:
        """
        Execute the agent based on the tool call output from the hypothesis agent.
        The output is expected to be in the form <tag>[query to agent]</tag>.
        """
        import re
        # Match <tag>...</tag> and extract tag and query
        # Use search so the XML-style tool call can appear anywhere in the text (not only at the start)
        match = re.search(r'<(\w+)>([\s\S]*?)</\1>', hypothesis_agent_out, re.DOTALL)
        if not match:
            raise ValueError(f"Invalid tool call format: {hypothesis_agent_out}")
        tag, query = match.group(1), match.group(2).strip()
        print(f"🔧 Executing agent with tag '{tag}'")
        if tag not in self.agents:
            raise ValueError(f"Agent with tag '{tag}' not found in self.agents")

        current_query_num_for_progress = getattr(self, "query_num", None)
        if current_query_num_for_progress is not None:
            progress_round_dir = self._progress_round_dir(current_query_num_for_progress)
            progress_recorder.write_query_started(progress_round_dir, current_query_num_for_progress, objective=query)
            progress_step = "running_literature_check" if tag == DEEP_RESEARCH_TAG else "executing_code"
            progress_recorder.write_query_step(progress_round_dir, current_query_num_for_progress, progress_step)

        agent = self.agents[tag]
        current_query_num = self.query_num if hasattr(self, "query_num") else None
        self.logger.log_action(
            "helper_agent_called",
            (
                f"tag={tag}\n"
                f"agent_type={agent.__class__.__name__}\n"
                f"query_num={current_query_num}\n"
                f"query_chars={len(query)}"
            ),
        )
        # Persist search outputs under the run log directory to avoid polluting
        # the execution working directory. When available, scope by round.
        searches_dir = None
        try:
            if getattr(self, "log_dir", None):
                round_num = getattr(self, "round_num", None)
                if isinstance(round_num, int) and round_num >= 0:
                    searches_dir = str(Path(self.log_dir) / "searches" / f"round_{round_num}")
                else:
                    searches_dir = str(Path(self.log_dir) / "searches")
            else:
                exec_agent = next((a for a in self.agents.values() if isinstance(a, ExecutionAgentCC)), None)
                if exec_agent and hasattr(exec_agent, 'working_directory'):
                    searches_dir = str(Path(exec_agent.working_directory) / "searches")
        except Exception:
            searches_dir = None

        # If this is an ExecutionAgentCC, pass filesystem context and variables directly
        if isinstance(agent, ExecutionAgentCC):
            # Pass variables directly to the execute method instead of updating config
            checkpoint_query_dir = None
            try:
                if hasattr(self, "checkpoint_manager") and self.query_num is not None:
                    checkpoint_dir = self.checkpoint_manager.get_checkpoint_dir(self.query_num)
                    checkpoint_dir.mkdir(parents=True, exist_ok=True)
                    checkpoint_query_dir = str(checkpoint_dir)
            except Exception:
                checkpoint_query_dir = None

            kwargs = {
                'query_num': self.query_num if hasattr(self, 'query_num') else None,
                'image_tracker': self  # Pass the workflow as image tracker
            }
            if searches_dir:
                kwargs['searches_dir'] = searches_dir
            if checkpoint_query_dir:
                kwargs['checkpoint_query_dir'] = checkpoint_query_dir
            agent_output = agent.execute(query, **kwargs)
            self.logger.log_action(
                "helper_agent_completed",
                (
                    f"tag={tag}\n"
                    f"agent_type={agent.__class__.__name__}\n"
                    f"query_num={current_query_num}\n"
                    f"output_chars={len(agent_output) if isinstance(agent_output, str) else len(str(agent_output))}"
                ),
            )
            return agent_output, query

        # For other agents, call execute and pass searches_dir when the method supports it
        try:
            sig = inspect.signature(agent.execute)
            if 'searches_dir' in sig.parameters:
                agent_output = agent.execute(query, searches_dir=searches_dir)
            else:
                agent_output = agent.execute(query)
        except Exception:
            # Fallback to calling without searches_dir
            agent_output = agent.execute(query)

        self.logger.log_action(
            "helper_agent_completed",
            (
                f"tag={tag}\n"
                f"agent_type={agent.__class__.__name__}\n"
                f"query_num={current_query_num}\n"
                f"output_chars={len(agent_output) if isinstance(agent_output, str) else len(str(agent_output))}"
            ),
        )

        try:
            agent.cleanup()
        except Exception:
            pass
        return agent_output, query

    def run(self, final_report_step: bool = False, exploratory_mode: bool = False) -> Dict[str, Any]:
        """
        Run the Director workflow.

        This workflow behavior depends on the mode:
        - Normal mode: Generates hypothesis first, then tests it
        - Exploratory mode: Skips hypothesis generation, explores data freely

        Args:
            final_report_step: Whether to generate a structured final report at the end
            exploratory_mode: If True, skip hypothesis generation and run in exploration mode

        Returns:
            Dict[str, Any]: Results of the workflow
        """
        # Determine starting point (for resume support)
        # Start from the next query number after the last successful query
        start_query = getattr(self, 'query_num', 0) + 1
        mid_round_resuming = start_query > 1

        if mid_round_resuming:
            print(f"🔄 Resuming Director Workflow from query {start_query}")
            print(f"📊 Previous queries completed: {start_query - 1}")
        else:
            print(f"🚀 Starting Director Workflow")
            print(f"📊 Dataset: {self.dataset_description[:100]}...")

        if self.task_description is not None:
            print(f"📝 Task: {self.task_description[:100]}...")
        print(f"🔍 Max queries: {self.max_queries}")

        # Log workflow start
        if mid_round_resuming:
            self.logger.log_action("workflow_resumed", f"Resuming from query {start_query} | max_queries={self.max_queries}")
        else:
            self.logger.log_action("workflow_started", f"Dataset: {self.dataset_description[:100]}... | max_queries={self.max_queries}")

        # Initialize feedback - use refinement_feedback if resuming and provided
        feedback = None
        if mid_round_resuming and hasattr(self, 'refinement_feedback') and self.refinement_feedback is not None:
            feedback = self.refinement_feedback # Apply refinement feedback to the first query after resume
            self.logger.log_action("refinement_feedback_loaded",
                f"Using refinement feedback for first query after resume: {len(feedback)} characters")
            print(f"📝 Applying refinement feedback to guide next query")

        tool_invocation_retry_count = 0

        # Store the generated hypothesis for reference
        hypothesis_output = None

        # Determine if we should skip hypothesis generation
        # Skip if: resuming, exploratory_mode=True, or human_selection_mode=True
        skip_hypothesis_generation = mid_round_resuming or exploratory_mode or self.hypothesis_config.human_selection_mode

        if not skip_hypothesis_generation:
            # Normal mode: generate single hypothesis
            print(f"\n🧠 Generating hypothesis before directing research...")
            self.logger.log_action("hypothesis_generation",
                "Calling hypothesis_gen_agent.generate_hypothesis() to establish research direction")

            # Optional: build a per-round external literature novelty/grounding context via
            # DeepResearchAgent before generating the hypothesis. Enabled by default, but safe/disableable; never
            # raises, and never injects a retrieval-failure marker into the hypothesis prompt.
            literature_context = None
            if getattr(self.hypothesis_config, "use_deep_research_for_hypothesis_generation", True):
                try:
                    literature_context = _generate_hypothesis_literature_context(
                        dataset_description=self.dataset_description,
                        task_description=self.task_description,
                        round_num=self.round_num,
                        log_dir=self.log_dir,
                        deep_research_provider=self.deep_research_provider,
                    )
                except Exception as e:
                    self.logger.log_error(f"Failed to generate hypothesis literature context: {e}")
                    literature_context = None

            # Call hypothesis generation agent to generate hypothesis
            hypothesis_output = self.hypothesis_gen_agent.generate_hypothesis(
                dataset_description=self.dataset_description,
                task_description=self.task_description,
                background_report=self.background_report,  # Consolidated knowledge
                literature_context=literature_context,
            )

            if hypothesis_output:
                self.logger.log_action("hypothesis_generated",
                    f"Generated {len(hypothesis_output)} characters of hypothesis")
                print(f"✓ Hypothesis generated ({len(hypothesis_output)} characters)")

                # Save hypothesis checkpoint as hypothesis_000
                try:
                    hypothesis_checkpoint_path = self.checkpoint_manager.save_hypothesis_checkpoint(hypothesis_output)
                    if hypothesis_checkpoint_path:
                        self.logger.log_action("hypothesis_checkpoint_saved",
                            f"Hypothesis saved to checkpoint at {hypothesis_checkpoint_path}")
                        print(f"💾 Hypothesis checkpoint saved")
                    else:
                        self.logger.log_error("Failed to save hypothesis checkpoint (empty path returned)")
                except Exception as e:
                    self.logger.log_error(f"Failed to save hypothesis checkpoint: {str(e)}")
            else:
                self.logger.log_action("hypothesis_failed", "No hypothesis output generated")
                print(f"⚠️ Warning: Hypothesis generation returned no output")

            print()  # Add spacing before main loop
        elif exploratory_mode:
            # Exploratory mode: skip hypothesis generation
            print(f"\n🔍 Running in EXPLORATORY MODE")
            print(f"   Director will explore the dataset without a specific hypothesis\n")
            hypothesis_output = None
            self.logger.log_action("exploratory_mode",
                "Exploratory mode enabled - skipping hypothesis generation")
        else:
            # RESUMING: Only load selected hypothesis when not mid-round resuming.
            if mid_round_resuming:
                # Mid-round resume: hypothesis should already be in director agent state
                self.logger.log_action("hypothesis_resume",
                    "Mid-round resume - hypothesis context should be in director agent state")
                print(f"📝 Resuming with hypothesis context from checkpoint")
                hypothesis_output = None
            elif self.hypothesis_config.selected_hypothesis_file:
                # Load human-selected hypothesis (round resume)
                hypothesis_output = self.checkpoint_manager.load_selected_hypothesis(
                    self.hypothesis_config.selected_hypothesis_file
                )

                if hypothesis_output:
                    self.logger.log_action("selected_hypothesis_loaded",
                        f"Loaded human-selected hypothesis ({len(hypothesis_output)} chars)")
                    print(f"\n✅ Loaded human-selected hypothesis ({len(hypothesis_output)} characters)")
                    print(f"📄 From: {self.hypothesis_config.selected_hypothesis_file}\n")
                else:
                    raise ValueError(
                        f"Failed to load selected hypothesis from {self.hypothesis_config.selected_hypothesis_file}")
            else:
                # Normal resume: hypothesis context should be in director state
                self.logger.log_action("hypothesis_resume",
                    "Resuming - hypothesis context should be in director agent state")
                print(f"📝 Resuming with hypothesis context from checkpoint")
                hypothesis_output = None

        # Use query_num for successful queries, attempt_count for loop control
        query_num = start_query
        attempt_count = 0
        director_requested_stop = False

        while query_num <= self.max_queries:
            attempt_count += 1
            self.query_num = query_num
            self.logger.log_action("query_start", f"Query {query_num}/{self.max_queries} (attempt {attempt_count})")

            # Estimate and print director context token usage before generation
            if self.multiturn_hypothesis:
                dir_tokens = self.director_agent.estimate_context_tokens()
            else:
                dir_tokens = 0
            self.logger.log_action("director_context_tokens", f"query={query_num}, tokens={dir_tokens}")

            # Generate query from director agent
            # Log feedback if provided
            if feedback is not None:
                self.logger.log_action("feedback_provided", f"Providing feedback to director agent for query {query_num}: {len(feedback)} characters")

            query = self.director_agent.generate_query(
                dataset_description=self.dataset_description,
                report=self.report,
                query_count=query_num,
                total_queries=self.max_queries,
                feedback=feedback,
                after_hypothesis_step=False,  # No mid-workflow hypothesis generation in Director mode
                background_report=self.background_report,
                target_hypothesis=hypothesis_output,  # Passed on every call; director handles first-time injection
                human_selection_mode=self.hypothesis_config.human_selection_mode,
                exploratory_mode=exploratory_mode,  # Tells director to explore data without hypothesis
            )

            if feedback is not None:
                feedback = None # Reset so it's not added next time
            
            # Check if no query was generated (agent decided to end iterations)
            if query is None:
                print(f"🛑 No query generated by hypothesis agent - ending iterations at query {query_num}")
                self.logger.log_action("query_generation", "No query generated - ending iterations")
                break  # Exit while loop

            # Director can opt to stop early by emitting the sentinel
            if self.allow_director_to_stop and ("<<STOP>>" in query):
                print(f"🛑 Director requested stop via <<STOP>> at query {query_num}")
                self.logger.log_action("director_stop_requested", f"Query {query_num}: Director emitted <<STOP>>")
                director_requested_stop = True
                break  # Exit query loop early
            
            self.logger.log_response(f"Generated query: {query}", "hypothesis_agent")
            
            # Check query for "<STOP>" pattern
            
            # Execute query with CodeAct execution agent
            self.logger.log_action("execute_query", f"Executing query {query_num} with CodeAct framework")
            # Accept XML-style tool call appearing anywhere in the response
            xml_match = re.search(r'<(\w+)>([\s\S]*?)</\1>', query, re.DOTALL)
            if xml_match:
                # Use the general agent execution logic
                try:
                    agent_output, parsed_query = self.execute_agent(query)
                except Exception as exec_agent_exc:
                    progress_recorder.write_query_failed(
                        self._progress_round_dir(query_num), query_num, error=str(exec_agent_exc)
                    )
                    raise
                # Reset retry counter on success
                tool_invocation_retry_count = 0
            else:
                # If multiturn, prompt the hypothesis agent to try again and restart loop
                if self.multiturn_hypothesis:
                    tool_invocation_retry_count += 1

                    if tool_invocation_retry_count > self.max_tool_invocation_retries:
                        error_msg = f"Max tool invocation retries ({self.max_tool_invocation_retries}) exceeded. Gracefully exiting query loop (early stopping)."
                        self.logger.log_error(error_msg)
                        self.logger.log_action("early_stopping", f"Query {query_num}: Max tool invocation retries exceeded - proceeding to summary generation")
                        print(f"⚠️ Max tool invocation retries exceeded - ending iterations early at query {query_num}")
                        break  # Exit while loop

                    self.logger.log_error(f"Tool invocation did not match expected format (attempt {tool_invocation_retry_count}/{self.max_tool_invocation_retries}). Asking hypothesis agent to try again with shorter query.")
                    self.logger.log_action("tool_invocation_error", f"Query {query_num}: Tool invocation did not match expected format. Retry attempt {tool_invocation_retry_count}")

                    # Add feedback to hypothesis agent for next iteration
                    feedback = """Your previous query did not contain a properly formatted tool invocation. This is likely because your query was too long and got cut off before completing the XML tags.

Please try again with a SHORTER and more concise query. Remember to use the format:
**Interpretation**: [Brief interpretation]
**Thought**: [Brief thought]
<[tag]>
[Concise query to helper agent]
</[tag]>

Make sure your query is complete and the closing XML tag is present. Keep your interpretation and thought sections brief (1-2 sentences each), and make your actual query to the helper agent concise and focused on the most important aspects."""

                    # Continue to next iteration (retry with same query_num, don't increment)
                    continue  # Retry without incrementing query_num
                else:
                    error_msg = f"Tool invocation did not match expected format. Gracefully exiting query loop (early stopping)."
                    self.logger.log_error(error_msg)
                    self.logger.log_action("early_stopping", f"Query {query_num}: Invalid tool invocation format - proceeding to summary generation")
                    print(f"⚠️ Tool invocation format error - ending iterations early at query {query_num}")
                    break  # Exit while loop
            
            self.logger.log_action("query_executed", f"Query {query_num} executed successfully")
            progress_recorder.write_query_step(
                self._progress_round_dir(query_num), query_num, "analyzing_results"
            )

            # Store result (keep both raw and parsed queries)
            # If parsed_query is not defined (shouldn't happen when xml_match), fall back to raw query
            self.results.append({
                'query_num': query_num,
                'query_raw': query,
                'query_parsed': parsed_query,
                'result': agent_output,
                'timestamp': datetime.datetime.now().isoformat()
            })

            # REPORT UPDATE - pass parsed query so reports see the cleaned/inner query text
            self.report_update(agent_output, parsed_query, query_num)
            progress_recorder.write_query_step(
                self._progress_round_dir(query_num), query_num, "writing_query_report"
            )
            # Save per-iteration checkpoint using new system
            try:
                checkpoint_path = self.checkpoint_manager.save_checkpoint(query_num)
                if checkpoint_path:
                    self.logger.log_action("checkpoint_saved", f"Query {query_num} checkpoint saved")
                else:
                    self.logger.log_error("Checkpoint save returned empty path")
            except Exception as e:
                self.logger.log_error(f"Failed to save iteration checkpoint: {str(e)}")

            self.logger.log_action("query_completed", f"Query {query_num} completed successfully")
            progress_recorder.write_query_completed(
                self._progress_round_dir(query_num), query_num, summary=agent_output
            )
            # Git version control: commit changes after query completion (before incrementing query_num)
            if self.git_vc is not None:
                try:
                    if self.git_vc.add_all():
                        commit_message = f"Query {query_num}"
                        self.git_vc.commit(commit_message, round_num=self.round_num)
                except Exception as e:
                    self.logger.log_error(f"Git commit failed for query {query_num}: {str(e)}")

            # Increment query_num only after successful completion and git commit
            query_num += 1
            attempt_count = 0  # Reset attempt counter for next query

            # Expanding dataset mode: refresh dataset description after each query
            # This allows agents to see newly added data files
            if hasattr(self, 'data_manager') and self.data_manager is not None:
                self._refresh_dataset_description()

            if (self.review_optimization_steps is not None):
                perform_review = False
                # Note: query_num was just incremented, so we need to check the previous query number
                previous_query_num = query_num - 1

                # Skip review on the last query - feedback won't be used since the loop will exit
                if previous_query_num >= self.max_queries:
                    perform_review = False
                elif self.hold_reviews_in_exploratory_phase:
                    # Sets to true if frequency is met
                    perform_review = self._test_iteration_condition_with_warmup(previous_query_num, self.review_optimization_steps)
                elif (previous_query_num % self.review_optimization_steps == 0):
                    perform_review = True

                if perform_review:
                    self.logger.log_action("feedback_triggered", f"Query {previous_query_num} completed, triggering feedback generation (every {self.review_optimization_steps} queries)")
                    feedback = self.get_feedback()
        
        # Save the final report
        final_report_content = self.cumulative_report  # Default to cumulative report
        
        if final_report_step:
            # Generate a structured final report using the director agent
            self.logger.log_action("final_report_generation_start",
                "Calling director agent to generate structured final report")
            print(f"\n📝 Generating structured final report...")

            try:
                report_to_pass = self.report if self.multiturn_hypothesis else self.cumulative_report
                structured_report = self.director_agent.generate_final_report(
                    report=report_to_pass # This should have been updated in report_update() call above
                )
                
                if structured_report:
                    self.logger.log_action("final_report_generated", 
                        f"Generated structured report ({len(structured_report)} characters)")
                    print(f"✓ Structured final report generated ({len(structured_report)} characters)")
                    
                    # Save the structured report instead of the raw cumulative report
                    report_path = self._save_final_report(report=structured_report)
                    final_report_content = structured_report  # Use structured report for return
                else:
                    self.logger.log_error("final_report_generation_failed", 
                        "No structured report generated, falling back to cumulative report")
                    print(f"⚠️ Warning: Final report generation returned no output, using cumulative report")
                    report_path = self._save_final_report()
                    final_report_content = self.cumulative_report  # Fallback to cumulative
            except Exception as e:
                error_msg = f"Error generating final report: {str(e)}"
                self.logger.log_error("final_report_generation_error", error_msg)
                print(f"⚠️ {error_msg}, falling back to cumulative report")
                report_path = self._save_final_report()
                final_report_content = self.cumulative_report  # Fallback to cumulative
        else:
            report_path = self._save_final_report() # Uses internal self.cumulative_report
        
        # Log final status if director stopped early
        if director_requested_stop:
            self.logger.log_action("workflow_stopped_early",
                f"Director emitted <<STOP>> at query {query_num}")

        self.logger.log_action("workflow_completed", f"Total queries executed: {len([r for r in self.results if not r.get('error', False)])}")
        self.logger.log_action("final_report_saved", f"Report path: {report_path}")

        return {
            'dataset_description': self.dataset_description,
            'max_queries': self.max_queries,
            'results': self.results,
            'report': final_report_content,  # Return actual report content, not path
            'report_path': report_path,
            'timestamp': datetime.datetime.now().isoformat(),
            'status': 'stopped_by_director' if director_requested_stop else 'completed'
        }
    
    def report_update(self, result: str, query: str, query_num: int):
        if self.multiturn_hypothesis:
            self.report = f"\n\n## Query {query_num}\n\n**Query**: {query}\n\n**Result**:\n{result}"
        else:
            self.report += f"\n\n## Query {query_num}\n\n**Query**: {query}\n\n**Result**:\n{result}"
        
        self.cumulative_report += f"\n\n## Query {query_num}\n\n**Query**: {query}\n\n**Result**:\n{result}"
        
        return 0

    def _refresh_dataset_description(self):
        """
        Refresh the dataset description with current data directory contents.
        
        This is called after each query when expanding_dataset_mode is enabled
        (data_manager is set by HypothesisBrancher).
        """
        if not hasattr(self, 'data_manager') or self.data_manager is None:
            return
        
        # Scan for changes
        self.data_manager.scan()
        
        # Check for new files and log them
        new_files = self.data_manager.detect_new_files()
        if new_files:
            self.logger.log_action("data_files_added", 
                f"Detected {len(new_files)} new data file(s): {new_files[:5]}")
            print(f"📁 Detected {len(new_files)} new data file(s):")
            for f in new_files[:5]:  # Show first 5
                print(f"   + {f}")
            if len(new_files) > 5:
                print(f"   + ... and {len(new_files) - 5} more")
        
        # Update dataset description
        old_len = len(self.dataset_description)
        self.dataset_description = self.data_manager.update_dataset_description()
        new_len = len(self.dataset_description)
        
        if new_len != old_len:
            self.logger.log_action("dataset_description_refreshed",
                f"Dataset description updated: {old_len} -> {new_len} chars")

    def make_feedback_prompt(self):
        literature_check_section = ""
        if self.enable_deep_research_in_review:
            literature_check_section = """
When any of the following conditions apply, emit a literature-check tag so a deep_research system 
can validate the claim before it steers the next query:

- A claim may already be known and the novelty of the finding is uncertain.
- A gene, mechanism, pathway, or candidate is asserted as "literature-supported" without
  direct verification in the agent's own computational output.
- A biological interpretation depends on evidence outside the current computation (e.g.,
  an assumed mechanism, a known drug target, a claimed disease association).
- You need external literature context to judge whether the result is novel, obvious,
  contradicted, or already thoroughly characterized.

Use this exact tag format (one tag per review, one neutral claim or question per tag):

<NEEDS_LITERATURE_CHECK>
short neutral claim or question to verify
</NEEDS_LITERATURE_CHECK>

Keep it selective: do not emit this tag for pure computation, statistics, or data-analysis
questions. Use it when scientific rigor requires external grounding — not as a fallback for
uncertainty about methodology.
"""

        prompt = \
f"""Please generate a thorough review of the following report.

First, here is some information about the dataset:

{self.dataset_description}

And here's a background report on what we've previously explored and learned on this dataset and task:

{self.background_report}

And here is the most recent analysis. You should focus your critiques on these findings and approaches:

{self.cumulative_report}

Now generate a thorough review of these findings.
{literature_check_section}"""
        return prompt

    def get_feedback(self):
        """
        Get feedback from the LLM on the current report.
        """
        if self.review_optimization_llm is None:
            return None

        self.logger.log_action("feedback_generation_start", "Starting feedback generation from review optimization LLM")

        if self.report_summarization_llm is not None:
            report_summary = self.summarize_cumulative_report()
        else:
            #report_summary = self.cumulative_report # Option 1: Can use the full cumulative report for review instead of summarizing
            report_summary = self.make_feedback_prompt()

        if report_summary is None:
            self.logger.log_error("feedback_generation_failed", "Failed to generate feedback: report summary is None")
            return None

        # Store the input for checkpointing
        reviewer_input = report_summary

        feedback = self.review_optimization_llm.forward(report_summary)

        self.logger.log_action("feedback_generated", f"Feedback generated successfully: {len(feedback)} characters")
        self.logger.log_response(f"Generated feedback: {feedback[:200]}...", "review_optimization_llm")

        # Optional: selectively validate a reviewer-flagged claim against external literature.
        # Gated on enable_deep_research_in_review and the reviewer actually emitting the tag --
        # this doesn't run on every review, and never raises out of get_feedback().
        if self.enable_deep_research_in_review:
            claim = _extract_needs_literature_check(feedback)
            if claim:
                literature_context = None
                try:
                    literature_context = _generate_review_literature_context(
                        claim=claim,
                        round_num=self.round_num,
                        query_num=self.query_num,
                        log_dir=self.log_dir,
                        deep_research_provider=self.deep_research_provider,
                    )
                except Exception as e:
                    self.logger.log_error(
                        f"review_literature_check_error: Failed to run literature check "
                        f"for claim '{claim[:120]}': {e}"
                    )
                    literature_context = None

                if literature_context:
                    feedback = feedback.rstrip() + "\n\n## External Literature Check\n\n" + literature_context.strip() + "\n"
                    self.logger.log_action(
                        "review_literature_check_appended",
                        f"Appended external literature check ({len(literature_context)} characters) to review feedback",
                    )
                else:
                    self.logger.log_action(
                        "review_literature_check_skipped",
                        f"Literature check for claim '{claim[:120]}' returned no usable result "
                        f"(provider={self.deep_research_provider}); review feedback used without it.",
                    )

        # Save review using CheckpointManager (new mechanism)
        try:
            review_checkpoint_path = self.checkpoint_manager.save_review_checkpoint(
                query_num=self.query_num,
                reviewer_input=reviewer_input,
                reviewer_output=feedback
            )
            if review_checkpoint_path:
                # Track review in history
                review_entry = {
                    'query_num': self.query_num,
                    'round_num': self.round_num,
                    'timestamp': datetime.datetime.now().isoformat(),
                    'input_length': len(reviewer_input),
                    'output_length': len(feedback),
                    'checkpoint_path': review_checkpoint_path
                }
                self.review_history.append(review_entry)

                self.logger.log_action("review_checkpoint_saved",
                    f"Review for query {self.query_num} saved to checkpoint at {review_checkpoint_path}")
                print(f"📝 Review checkpoint saved: {os.path.basename(review_checkpoint_path)}")
            else:
                self.logger.log_error("Failed to save review checkpoint (empty path returned)")
        except Exception as e:
            error_msg = f"Error saving review checkpoint: {str(e)}"
            self.logger.log_error("review_checkpoint_save_failed", error_msg)
            print(f"⚠️ {error_msg}")

        return feedback

    def _save_final_report(self, report: Optional[str] = None) -> str:
        """
        Save the final report to a file. Only saves the final `self.report` content.

        Args:
            final_report (str): The final report content to save.

        Returns:
            str: Path to the saved report file, or empty string if saving is skipped
        """
        # Skip saving if report_filename is None (report will be saved in checkpoints instead)
        if self.report_filename is None:
            return ""

        # Ensure output directory exists
        os.makedirs(self.output_home, exist_ok=True)

        # Use the configured filename for the final report (may include subdirectories)
        report_path = os.path.join(self.output_home, self.report_filename)
        
        # Ensure the subdirectory exists if the filename includes a path
        os.makedirs(os.path.dirname(report_path), exist_ok=True)

        # Create the full report content - include dataset description and the final report content
        if report is None:
            dd_report = self.dataset_description +"\n\n## Task Description\n" + self.task_description if self.task_description is not None else self.dataset_description

            report_content = f"""# Scientific Analysis Report

Generated on: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## Dataset Description
{dd_report}

## Final Report
{self.cumulative_report}

{self.get_image_references()}
"""
        else:
            report_content = report # Just copy what was passed
            
        # Save the report
        try:
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(report_content)
            
            try:
                self.logger.log_action("report_saved", f"Report saved to {report_path}")
            except Exception:
                pass
            return report_path
            
        except Exception as e:
            error_msg = f"Error saving report: {str(e)}"
            try:
                self.logger.log_error(error_msg)
            except Exception:
                print(f"⚠️ Warning: {error_msg}")
            return ""

    def add_image(self, image_data: str, description: str, query_num: int, step_num: int) -> str:
        """
        Add an image to the tracking system and save it to disk.
        
        Args:
            image_data (str): Base64 encoded image data
            description (str): Description of the image
            query_num (int): Query number when image was generated
            step_num (int): Step number when image was generated
            
        Returns:
            str: Image reference ID for use in reports
        """
        self.image_counter += 1
        image_id = f"img_{self.image_counter:03d}"
        
        # Generate filename
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{image_id}_{timestamp}.png"
        filepath = os.path.join(self.images_dir, filename)
        
        # Save image to disk
        try:
            import base64
            # Remove data URL prefix if present
            if image_data.startswith('data:image'):
                image_data = image_data.split(',')[1]
            
            # Decode and save
            image_bytes = base64.b64decode(image_data)
            with open(filepath, 'wb') as f:
                f.write(image_bytes)
            
            # Track the image
            self.images[image_id] = {
                'filename': filename,
                'filepath': filepath,
                'description': description,
                'query_num': query_num,
                'step_num': step_num,
                'timestamp': datetime.datetime.now().isoformat()
            }
            
            self.logger.log_action("image_saved", f"Image {image_id} saved as {filename}")
            print(f"📊 Image tracked: {image_id} -> {filename} ({description[:50]}...)")
            return image_id
            
        except Exception as e:
            error_msg = f"Error saving image {image_id}: {str(e)}"
            self.logger.log_error(error_msg)
            print(f"⚠️ {error_msg}")
            return None

    def get_image_references(self) -> str:
        """
        Get a formatted string of all image references for use in reports.
        
        Returns:
            str: Formatted image references
        """
        if not self.images:
            return ""
        
        references = "\n\n## Generated Visualizations\n\n"
        for image_id, image_info in self.images.items():
            references += f"**{image_id}** (Query {image_info['query_num']}, Step {image_info['step_num']}): {image_info['description']}\n"
            references += f"*File: {image_info['filename']}*\n\n"
        
        return references

    def get_image_citation_instructions(self) -> str:
        """
        Get instructions for the execution agent on how to cite images in reports.
        
        Returns:
            str: Citation instructions
        """
        if not self.images:
            return ""
        
        instructions = "\n\n**IMAGE CITATION INSTRUCTIONS:**\n"
        instructions += "When writing your final response, reference the generated images using their IDs. "
        instructions += "For example: 'As shown in img_001, the data exhibits a clear trend...' or 'Figure img_002 demonstrates...'\n\n"
        instructions += "Available images:\n"
        
        for image_id, image_info in self.images.items():
            instructions += f"- {image_id}: {image_info['description']} (Query {image_info['query_num']}, Step {image_info['step_num']})\n"
        
        return instructions

    def summarize_cumulative_report(self) -> str:
        """
        Summarize the `self.cumulative_report` into a larger, polished report using the
        pre-configured `self.report_summarization_llm`. This runs independently of the 
        hypothesis/execution agents so it will not interfere with their internal state 
        or conversations.

        Returns:
            The generated summary string. On error, returns an error description.
        """
        # Check if we have a report summarization LLM configured
        if not hasattr(self, 'report_summarization_llm') or (self.report_summarization_llm is None):
            return "Error: No report summarization LLM configured. Set report_summarization_llm_config during initialization."

        # Do not touch any agent state here; operate only on self.cumulative_report
        if not self.cumulative_report:
            return ""

        try:
            self.logger.log_action("summarize_start", "Starting LLM summarization of cumulative report")
        except Exception:
            pass

        if self.task_description is not None:
            prompt = f"Summarize the following report, taking into account the dataset description and results of the analysis thus far:\n\n## Dataset Description\n{self.dataset_description}\n\n## Task Description\n{self.task_description}\n\n## Iterative Analysis\n{self.cumulative_report}\n\nPlease produce the final report in markdown format."
        else:
            prompt = f"Summarize the following report, taking into account the dataset description and results of the analysis thus far:\n\n## Dataset Description\n{self.dataset_description}\n\n## Iterative Analysis\n{self.cumulative_report}\n\nPlease produce the final report in markdown format."

        summary = ""
        try:
            # Use the pre-configured LLM to generate the summary
            summary = self.report_summarization_llm(prompt)

        except Exception as e:
            err = f"Error during summarization: {str(e)}"
            try:
                self.logger.log_error(err)
            except Exception:
                pass
            return err
        try:
            self.logger.log_response(summary, "report_summarization")
        except Exception:
            print(f"📝 Generated summary:\n{summary}")

        return summary

    @staticmethod
    def load_checkpoint_metadata(checkpoint_path_or_query: str, log_dir: str = 'logs') -> Dict[str, Any]:
        """
        Load checkpoint metadata without creating a workflow instance.
        Useful for extracting configuration details before resuming.
        
        Args:
            checkpoint_path_or_query: Path to checkpoint file/directory or query number
            log_dir: Log directory (used if checkpoint_path_or_query is a query number)
            
        Returns:
            Dict containing checkpoint metadata including workflow_state and filesystem info
        """
        from pathlib import Path
        import json
        
        # Determine checkpoint path
        path = Path(checkpoint_path_or_query)
        
        if path.is_dir():
            if path.name == "checkpoints":
                checkpoint_file = path / "latest" / "checkpoint.json"
            elif path.name.startswith("query_"):
                checkpoint_file = path / "checkpoint.json"
            else:
                checkpoint_file = path / "checkpoints" / "latest" / "checkpoint.json"
        elif path.suffix == '.json':
            checkpoint_file = path
        else:
            try:
                query_num = int(checkpoint_path_or_query)
                checkpoint_file = Path(log_dir) / "checkpoints" / f"query_{query_num:03d}" / "checkpoint.json"
            except ValueError:
                raise ValueError(f"Invalid checkpoint path or query number: {checkpoint_path_or_query}")
        
        if not checkpoint_file.exists():
            raise FileNotFoundError(f"Checkpoint file not found: {checkpoint_file}")
        
        with open(checkpoint_file, 'r', encoding='utf-8') as f:
            checkpoint_data = json.load(f)
        
        return {
            'checkpoint_path': str(checkpoint_file),
            'checkpoint_dir': str(checkpoint_file.parent),
            'workflow_state': checkpoint_data.get('workflow_state', {}),
            'filesystem': checkpoint_data.get('filesystem', {}),
            'agents': checkpoint_data.get('agents', {}),
            'timestamp': checkpoint_data.get('timestamp', ''),
            'query_num': checkpoint_data.get('query_num', 0),
            'checkpoint_version': checkpoint_data.get('checkpoint_version', '1.0')
        }

    @classmethod
    def from_checkpoint(cls, checkpoint_path_or_query: str, **override_kwargs) -> 'DirectorWorkflow':
        """
        Load a DirectorWorkflow instance from a checkpoint.

        Args:
            checkpoint_path_or_query: Path to checkpoint file/directory or query number
            **override_kwargs: Optional parameters to override during restoration
                Required: agent_config_list (List[AgentConfig])
                Optional: log_dir, output_home, hypothesis_config, refinement_feedback, and other workflow parameters

        Returns:
            AIRWorkflowModular: Restored workflow instance
        """
        from pathlib import Path
        
        # Determine checkpoint path
        path = Path(checkpoint_path_or_query)
        
        if path.is_dir():
            # Directory provided - look for checkpoint file
            if path.name == "checkpoints":
                # Checkpoints directory - use latest
                checkpoint_file = path / "latest" / "checkpoint.json"
            elif path.name.startswith("query_"):
                # Specific query directory
                checkpoint_file = path / "checkpoint.json"
            else:
                # Look for checkpoints subdirectory
                checkpoint_file = path / "checkpoints" / "latest" / "checkpoint.json"
        elif path.suffix == '.json':
            # Direct checkpoint file path
            checkpoint_file = path
        else:
            # Try to interpret as query number
            try:
                query_num = int(checkpoint_path_or_query)
                # Need to find the log directory - use current directory as fallback
                log_dir = override_kwargs.get('log_dir', 'logs')
                checkpoint_file = Path(log_dir) / "checkpoints" / f"query_{query_num:03d}" / "checkpoint.json"
            except ValueError:
                raise ValueError(f"Invalid checkpoint path or query number: {checkpoint_path_or_query}")
        
        if not checkpoint_file.exists():
            raise FileNotFoundError(f"Checkpoint file not found: {checkpoint_file}")
        
        # Load checkpoint data
        import json
        with open(checkpoint_file, 'r', encoding='utf-8') as f:
            checkpoint_data = json.load(f)
        
        # Extract workflow state
        workflow_state = checkpoint_data.get('workflow_state', {})

        # Reconstruct configuration objects
        def _reconstruct_llm_config(config_dict):
            if isinstance(config_dict, dict):
                try:
                    return LLMConfig(**config_dict)
                except Exception:
                    return LLMConfig(model_name=config_dict.get('model_name', 'claude-3-5-sonnet-20241022'))
            return LLMConfig(model_name='claude-3-5-sonnet-20241022')

        # Create default hypothesis config - will be updated from checkpoint
        hypothesis_config = get_default_hypothesis_config()

        # Extract workflow configuration flags from checkpoint (for proper restoration).
        # Old checkpoints missing deep_research_provider/enable_deep_research_in_review
        # automatically receive the new defaults so they behave correctly on resume.
        checkpoint_git_tracking = workflow_state.get('git_tracking', False)
        checkpoint_multiturn = workflow_state.get('multiturn_hypothesis', True)
        checkpoint_review_search = workflow_state.get('review_search', False)
        checkpoint_review_design = workflow_state.get('review_design', False)
        # Paperclip is the default; missing key in old checkpoints → "paperclip".
        checkpoint_deep_research_provider = workflow_state.get('deep_research_provider', 'paperclip') or 'paperclip'
        checkpoint_enable_deep_research_in_review = workflow_state.get('enable_deep_research_in_review', True)

        # Override with any provided parameters
        init_kwargs = {
            'hypothesis_config': hypothesis_config,
            'dataset_description': workflow_state.get('dataset_description', ''),
            'task_description': workflow_state.get('task_description', None),
            'agent_config_list': override_kwargs.get('agent_config_list', []),
            'max_queries': workflow_state.get('max_queries', 10),
            'log_dir': override_kwargs.get('log_dir', 'logs'),
            'output_home': override_kwargs.get('output_home', 'outputs'),
            # Use checkpoint values as defaults, can be overridden by override_kwargs below
            'git_tracking': checkpoint_git_tracking,
            'multiturn_hypothesis': checkpoint_multiturn,
            'review_search': checkpoint_review_search,
            'review_design': checkpoint_review_design,
            'deep_research_provider': checkpoint_deep_research_provider,
            'enable_deep_research_in_review': checkpoint_enable_deep_research_in_review,
        }

        # Apply any additional overrides (exclude parameters not in __init__)
        # This allows explicit override_kwargs to take precedence over checkpoint values
        for key, value in override_kwargs.items():
            if key not in ['agent_config_list', 'log_dir', 'output_home', 'refinement_feedback']:
                init_kwargs[key] = value
        
        # Create workflow instance
        workflow = cls(**init_kwargs)

        # Restore workflow state
        workflow.query_num = workflow_state.get('query_num', 0)
        workflow.results = workflow_state.get('results', [])
        workflow.report = workflow_state.get('report', '')
        workflow.cumulative_report = workflow_state.get('cumulative_report', '')
        workflow.images = workflow_state.get('images', {})
        workflow.image_counter = workflow_state.get('image_counter', 0)
        workflow.review_history = workflow_state.get('review_history', [])
        workflow.generated_reviewer_prompt = workflow_state.get('generated_reviewer_prompt', None)

        # If reviewer prompt was restored, recreate the review_optimization_llm with it
        if workflow.generated_reviewer_prompt and hasattr(workflow, 'review_optimization_llm_config'):
            review_config_with_prompt = LLMConfig(
                model_name=workflow.review_optimization_llm_config.model_name,
                api_provider=workflow.review_optimization_llm_config.api_provider,
                api_key=workflow.review_optimization_llm_config.api_key,
                temperature=workflow.review_optimization_llm_config.temperature,
                frequency_penalty=workflow.review_optimization_llm_config.frequency_penalty,
                max_tokens=workflow.review_optimization_llm_config.max_tokens,
                system_prompt=workflow.generated_reviewer_prompt
            )

            if workflow.review_search:
                workflow.review_optimization_llm = BaseLLMWithSearch(
                    llm_config=review_config_with_prompt,
                    logger=workflow.logger
                )
            else:
                workflow.review_optimization_llm = OnePassLLM(
                    llm_config=review_config_with_prompt
                )

            workflow.logger.log_action("reviewer_prompt_restored",
                f"Restored reviewer prompt from checkpoint ({len(workflow.generated_reviewer_prompt)} chars)")

        # Store refinement feedback if provided
        workflow.refinement_feedback = override_kwargs.get('refinement_feedback', None)
        
        # Restore agent states
        agents_data = checkpoint_data.get('agents', {})

        # Restore director agent state (new workflow structure)
        if 'director_agent' in agents_data and hasattr(workflow, 'director_agent'):
            agent_data = agents_data['director_agent']
            if hasattr(workflow.director_agent, 'restore_state'):
                success = workflow.director_agent.restore_state(agent_data.get('state', {}))
                if not success:
                    workflow.logger.log_error("Failed to restore director agent state")

        # Restore hypothesis generation agent state (new workflow structure)
        if 'hypothesis_gen_agent' in agents_data and hasattr(workflow, 'hypothesis_gen_agent'):
            agent_data = agents_data['hypothesis_gen_agent']
            if hasattr(workflow.hypothesis_gen_agent, 'restore_state'):
                success = workflow.hypothesis_gen_agent.restore_state(agent_data.get('state', {}))
                if not success:
                    workflow.logger.log_error("Failed to restore hypothesis generation agent state")

        # Legacy: Restore old hypothesis_agent state for backward compatibility
        if 'hypothesis_agent' in agents_data and hasattr(workflow, 'hypothesis_agent'):
            agent_data = agents_data['hypothesis_agent']
            if hasattr(workflow.hypothesis_agent, 'restore_state'):
                success = workflow.hypothesis_agent.restore_state(agent_data.get('state', {}))
                if not success:
                    workflow.logger.log_error("Failed to restore hypothesis agent state (legacy)")
        
        # Restore tool agents states
        for tag, agent in getattr(workflow, 'agents', {}).items():
            if tag in agents_data and hasattr(agent, 'restore_state'):
                agent_data = agents_data[tag]
                success = agent.restore_state(agent_data.get('state', {}))
                if not success:
                    workflow.logger.log_error(f"Failed to restore {tag} agent state")
        
        workflow.logger.log_action("workflow_restored", 
            f"Restored workflow from query {workflow.query_num} checkpoint")
        
        return workflow

    def save_checkpoint(self, query_num: Optional[int] = None) -> str:
        """
        Save a checkpoint manually.
        
        Args:
            query_num: Query number (defaults to current query_num)
            
        Returns:
            str: Path to saved checkpoint
        """
        if query_num is None:
            query_num = getattr(self, 'query_num', 1)
        
        return self.checkpoint_manager.save_checkpoint(query_num, force_full=True)

    def list_checkpoints(self):
        """List all available checkpoints."""
        return self.checkpoint_manager.list_checkpoints()

    def cleanup_old_checkpoints(self, keep_last_n: int = 5):
        """Remove old checkpoints, keeping only the most recent N."""
        return self.checkpoint_manager.cleanup_old_checkpoints(keep_last_n)

    def reset_for_new_round(self):
        """
        Reset workflow state for a new round while keeping configuration intact.

        This method clears accumulated state (results, reports, query numbers, etc.)
        but preserves all configuration and agent instances. Useful when running
        multiple rounds with the same workflow instance.

        Call this before running run() again if you want a fresh round.
        
        Note: background_report is NOT reset here as it contains accumulated knowledge
        from previous rounds that should persist. It should be updated externally
        (e.g., by HypothesisBrancher) when new accumulated knowledge is available.
        """
        # Reset query tracking
        if hasattr(self, 'query_num'):
            delattr(self, 'query_num')  # Remove so it defaults to 0 in run()

        # Reset results and reports (current round only)
        self.results = []
        self.report = ""
        self.cumulative_report = ""

        # NOTE: background_report is intentionally NOT reset - it should persist across rounds
        # It will be updated externally when new accumulated knowledge is available

        # Reset review tracking
        self.review_history = []

        # Reset image tracking
        self.images = {}
        self.image_counter = 0

        # Reset director agent conversation history
        if hasattr(self.director_agent, 'reset_conversation'):
            self.director_agent.reset_conversation()

        # Reset hypothesis generation agent conversation history
        if hasattr(self.hypothesis_gen_agent, 'reset_conversation'):
            self.hypothesis_gen_agent.reset_conversation()

        # Reset refinement feedback if it exists
        if hasattr(self, 'refinement_feedback'):
            self.refinement_feedback = None

        # Log the reset
        try:
            self.logger.log_action("workflow_reset", "Workflow state reset for new round")
        except Exception:
            pass



    def cleanup(self):
        """Clean up resources."""
        try:
            # Clean up director agent
            if hasattr(self, 'director_agent'):
                self.director_agent.cleanup()

            # Clean up hypothesis generation agent
            if hasattr(self, 'hypothesis_gen_agent'):
                self.hypothesis_gen_agent.cleanup()

            # Clean up tool agents
            if hasattr(self, 'agents'):
                for agent in self.agents.values():
                    if hasattr(agent, 'cleanup'):
                        agent.cleanup()

            self.logger.log_action("cleanup", "DirectorWorkflow cleanup completed")
        except Exception as e:
            self.logger.log_error(f"Warning: Error during cleanup: {str(e)}")
