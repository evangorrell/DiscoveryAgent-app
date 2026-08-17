"""
CodeAct-based AIR Workflow

This module implements a CodeAct-based version of the AIR workflow where the execution agent
follows a ReAct-like framework using only code as tools. The agent produces thoughts, executes
code, and interprets outputs in an iterative loop.

Based on the CodeAct framework from codeact.py but integrated with the AIR system.
"""

import re
import inspect
import openai
import anthropic
import os
import json
import datetime
import pathlib
from pathlib import Path
import shutil

from typing import Optional, List, Dict, Any
from termcolor import colored
from .utils.logger import Logger
from .utils.llm_diagnostics import REFUSAL_ADVICE, first_refusal
from .config import LLMConfig, ExecutionAgentConfig, HypothesisGenerationConfig, get_default_execution_config, get_default_hypothesis_config
from .context_managers import NotebookManager, NotebookManagerXML, strip_code_markers
from .tool_agents.execution_agent_cc import ExecutionAgentCC
from .checkpoint_manager import CheckpointManager
# from .hyp_agents.hypothesis_agent import CodeActHypothesisAgent
# from .hyp_agents.hypothesis_agent_multiturn import CodeActHypothesisAgentMultiTurn
from .hyp_agents.director_agent import DirectorAgent
from .hyp_agents.hypothesis_gen_agent import HypothesisGenAgent
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
from .utils.git_version_control import GitVersionControl

REPORT_SUMMARY_PROMPT = """
You will be shown multiple queries and results of these queries for explorations of an AI scientists on a dataset.
Your job is to summarize the findings and main results within these queries and explorations.
You must faithfully represent and summarize the findings and explorations of the agent in its exploratory process.
You should follow standard scientific writing practices and faithfully intepret the data presented to you in order to form a narrative about the exploration thus far.
Your outputs will be read by a critic, so be sure to include all details that would be needed to properly review the work thus far.
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

class AIRWorkflowModular:
    """
    CodeAct-based AIR workflow that coordinates the hypothesis and execution agents.
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
            background_path (Optional[str]): Path to directory containing prior-work PDFs.
                This no longer injects PDFs into agents directly.
            background_report (Optional[str]): Accumulated knowledge/report from previous rounds.
                This is kept separate from the current round's report to cleanly track accumulated knowledge.
            git_tracking (bool): Enable git version control tracking of workflow changes
            report_filename (str): Filename for the final report (default: "final_report.md"). 
                Can include subdirectories (e.g., "reports/report_round1.md")
            max_tool_invocation_retries (int): Maximum number of retries for tool invocation format errors (default: 3)
        """
        self.agent_config_list = agent_config_list if agent_config_list is not None else []
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

        self.review_optimization_steps = review_optimization_steps
        self.review_optimization_llm_config = review_optimization_llm_config
        self.review_designer_llm_config = review_designer_llm_config
        self.report_summarization_llm_config = report_summarization_llm_config

        self.explicit_hypothesis_step = explicit_hypothesis_step
        self.exploratory_phase_warmup_steps = exploratory_phase_warmup_steps
        self.hold_reviews_in_exploratory_phase = hold_reviews_in_exploratory_phase

        # Determine output_home: explicit arg -> default "outputs"
        self.output_home = output_home if output_home is not None else "outputs"

        # Initialize logger first so it can be passed to agents and hypothesis agent
        self.logger = Logger("codeact_air_workflow", log_dir=log_dir)

        if background_path:
            self.logger.log_action(
                "background_path_ignored",
                "background_path no longer injects PDFs into agents in AIRWorkflowModular.",
            )
            print("ℹ️ background_path no longer injects PDFs into agents directly.")

        self._initialize_agents()

        self.hypothesis_agent_tool_prompt = tool_agent_prompt_generator(list(self.agents.values()))

        # Initialize DirectorAgent for query generation and research orchestration
        self.director_agent = DirectorAgent(
            config=hypothesis_config,
            tool_agent_prompt=self.hypothesis_agent_tool_prompt,
            logger=self.logger
        )

        # Initialize HypothesisGenAgent for hypothesis generation
        # Extract multi-hypothesis parameters from config if present
        n_hypotheses = getattr(hypothesis_config, 'n_hypotheses', 1)
        judge_llm_config = getattr(hypothesis_config, 'judge_llm_config', None)

        self.hypothesis_gen_agent = HypothesisGenAgent(
            config=hypothesis_config,
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
        
        agent = self.agents[tag]
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

        try:
            agent.cleanup()
        except Exception:
            pass
        return agent_output, query

    def run(self, init_generate_hypothesis: bool = False, final_report_step: bool = False) -> Dict[str, Any]:
        """
        Run the AIR workflow.
        
        Returns:
            Dict[str, Any]: Results of the workflow
        """
        # Determine starting point (for resume support)
        # Start from the next query number after the last successful query
        start_query = getattr(self, 'query_num', 0) + 1
        is_resuming = start_query > 1
        
        if is_resuming:
            print(f"🔄 Resuming AIR Workflow from query {start_query}")
            print(f"📊 Previous queries completed: {start_query - 1}")
        else:
            print(f"🚀 Starting AIR Workflow")
            print(f"📊 Dataset: {self.dataset_description[:100]}...")
        
        if self.task_description is not None:
            print(f"📝 Task: {self.task_description[:100]}...")
        print(f"🔍 Max queries: {self.max_queries}")
        
        # Log workflow start
        if is_resuming:
            self.logger.log_action("workflow_resumed", f"Resuming from query {start_query} | max_queries={self.max_queries}")
        else:
            self.logger.log_action("workflow_started", f"Dataset: {self.dataset_description[:100]}... | max_queries={self.max_queries}")

        # Initialize feedback - use refinement_feedback if resuming and provided
        feedback = None
        if is_resuming and hasattr(self, 'refinement_feedback') and self.refinement_feedback is not None:
            feedback = self.refinement_feedback # Apply refinement feedback to the first query after resume
            self.logger.log_action("refinement_feedback_loaded",
                f"Using refinement feedback for first query after resume: {len(feedback)} characters")
            print(f"📝 Applying refinement feedback to guide next query")

        after_hypothesis_step = False
        tool_invocation_retry_count = 0

        # Initial hypothesis generation step (before main loop)
        if init_generate_hypothesis and not is_resuming:
            print(f"\n🧠 Generating initial hypotheses before main workflow...")
            self.logger.log_action("initial_hypothesis_generation", "Calling hypothesis_gen_agent.generate_hypothesis() before main loop")

            # Call hypothesis generation agent to generate initial hypotheses
            hypothesis_output = self.hypothesis_gen_agent.generate_hypothesis(
                dataset_description=self.dataset_description,
                task_description=self.task_description,
                background_report=self.background_report,  # Consolidated knowledge
            )

            if hypothesis_output:
                self.logger.log_action("initial_hypothesis_generated", f"Generated {len(hypothesis_output)} characters of initial hypotheses")
                print(f"✓ Initial hypotheses generated ({len(hypothesis_output)} characters)")
                # The hypothesis output will be in the conversation context for subsequent queries
                
                # Save hypothesis checkpoint as hypothesis_000
                try:
                    hypothesis_checkpoint_path = self.checkpoint_manager.save_hypothesis_checkpoint(hypothesis_output)
                    if hypothesis_checkpoint_path:
                        self.logger.log_action("hypothesis_checkpoint_saved", f"Initial hypothesis saved to checkpoint at {hypothesis_checkpoint_path}")
                        print(f"💾 Initial hypothesis checkpoint saved")
                    else:
                        self.logger.log_error("Failed to save hypothesis checkpoint (empty path returned)")
                except Exception as e:
                    self.logger.log_error(f"Failed to save hypothesis checkpoint: {str(e)}")
            else:
                self.logger.log_action("initial_hypothesis_failed", "No hypothesis output generated")
                print(f"⚠️ Warning: Initial hypothesis generation returned no output")

            print()  # Add spacing before main loop

        # Use query_num for successful queries, attempt_count for loop control
        query_num = start_query
        attempt_count = 0

        while query_num <= self.max_queries:
            attempt_count += 1
            self.query_num = query_num
            self.logger.log_action("query_start", f"Query {query_num}/{self.max_queries} (attempt {attempt_count})")
            
            # Store current query number for agent context
            

            # Insert explicit hypothesis step first, if it's necessary on this query:
            if self._test_iteration_condition_with_warmup(query_num, self.explicit_hypothesis_step):
                # Combine any existing background_report with the current round's
                # partial report into a single consolidated background context.
                combined_background = ""
                if self.background_report:
                    combined_background += self.background_report.strip() + "\n\n"
                if self.report:
                    combined_background += self.report

                query = self.hypothesis_gen_agent.generate_hypothesis(  # Don't pass dataset description - only use that if we're generating hypotheses at the beginning
                    background_report=combined_background,
                )
                after_hypothesis_step = True


            # Estimate and print director context token usage before generation
            if self.multiturn_hypothesis:
                dir_tokens = self.director_agent.estimate_context_tokens()
            else:
                dir_tokens = 0 # TODO: Move this functionality back to director agent
            self.logger.log_action("director_context_tokens", f"query={query_num}, tokens={dir_tokens}")

            # Generate query from director agent
            if feedback is not None:
                self.logger.log_action("feedback_provided", f"Providing feedback to director agent for query {query_num}: {len(feedback)} characters")

            query = self.director_agent.generate_query(
                dataset_description=self.dataset_description,
                report=self.report,
                query_count=query_num,
                total_queries=self.max_queries,
                feedback=feedback,
                after_hypothesis_step = after_hypothesis_step,
                background_report=self.background_report
            )
            after_hypothesis_step = False

            if feedback is not None:
                feedback = None # Reset so it's not added next time
            
            # Check if no query was generated (agent decided to end iterations)
            if query is None:
                print(f"🛑 No query generated by hypothesis agent - ending iterations at query {query_num}")
                self.logger.log_action("query_generation", "No query generated - ending iterations")
                break  # Exit while loop
            
            self.logger.log_response(f"Generated query: {query}", "hypothesis_agent")
            
            # Reset conversation context for new query
            # TODO: reset conversation context for all agents
            
            # Execute query with CodeAct execution agent
            self.logger.log_action("execute_query", f"Executing query {query_num} with CodeAct framework")
            # Accept XML-style tool call appearing anywhere in the response
            xml_match = re.search(r'<(\w+)>([\s\S]*?)</\1>', query, re.DOTALL)
            if xml_match:
                # Use the general agent execution logic
                agent_output, parsed_query = self.execute_agent(query)
                # Reset retry counter on success
                tool_invocation_retry_count = 0
            else:
                # An API refusal, an empty completion, and a genuinely malformed
                # one all land here looking identical. Only the agent knows which.
                refusal = first_refusal(self.director_agent, self.hypothesis_gen_agent)
                self.logger.log_error(
                    f"Query {query_num}: "
                    f"{refusal or 'no XML tool invocation found in director output.'}\n"
                    f"raw_output_chars={len(query)}\n"
                    f"raw_output={query[:1000]!r}"
                )
                if refusal:
                    print(f"⚠️ {refusal}")
                # If multiturn, prompt the hypothesis agent to try again and restart loop
                if self.multiturn_hypothesis:
                    tool_invocation_retry_count += 1

                    if tool_invocation_retry_count > self.max_tool_invocation_retries:
                        error_msg = (
                            f"Refused by the API on {self.max_tool_invocation_retries} "
                            f"consecutive attempts. Gracefully exiting query loop (early "
                            f"stopping). {REFUSAL_ADVICE}"
                        ) if refusal else (
                            f"Max tool invocation retries ({self.max_tool_invocation_retries}) exceeded. Gracefully exiting query loop (early stopping)."
                        )
                        self.logger.log_error(error_msg)
                        self.logger.log_action("early_stopping", f"Query {query_num}: Max tool invocation retries exceeded - proceeding to summary generation")
                        print(f"⚠️ Max tool invocation retries exceeded - ending iterations early at query {query_num}")
                        break  # Exit while loop

                    if refusal:
                        # Nothing was malformed, so asking for a shorter query is
                        # noise. Refusals are non-deterministic; just ask again.
                        self.logger.log_error(
                            f"Retrying after refusal (attempt {tool_invocation_retry_count}/"
                            f"{self.max_tool_invocation_retries}). {REFUSAL_ADVICE}")
                        self.logger.log_action("model_refusal",
                            f"Query {query_num}: declined by the API. Retry attempt {tool_invocation_retry_count}")
                        feedback = None
                        continue

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

            if (self.review_optimization_steps is not None):
                perform_review = False
                # Note: query_num was just incremented, so we need to check the previous query number
                previous_query_num = query_num - 1
                if self.hold_reviews_in_exploratory_phase:
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
        
        self.logger.log_action("workflow_completed", f"Total queries executed: {len([r for r in self.results if not r.get('error', False)])}")
        self.logger.log_action("final_report_saved", f"Report path: {report_path}")
        
        return {
            'dataset_description': self.dataset_description,
            'max_queries': self.max_queries,
            'results': self.results,
            'report': final_report_content,  # Return actual report content, not path
            'report_path': report_path,
            'timestamp': datetime.datetime.now().isoformat()
        }
    
    def report_update(self, result: str, query: str, query_num: int):
        if self.multiturn_hypothesis:
            self.report = f"\n\n## Query {query_num}\n\n**Query**: {query}\n\n**Result**:\n{result}"
        else:
            self.report += f"\n\n## Query {query_num}\n\n**Query**: {query}\n\n**Result**:\n{result}"
        
        self.cumulative_report += f"\n\n## Query {query_num}\n\n**Query**: {query}\n\n**Result**:\n{result}"
        
        return 0

    def make_feedback_prompt(self):
        prompt = \
f"""Please generate a thorough review of the following report.

First, here is some information about the dataset:

{self.dataset_description}

And here's a background report on what we've previously explored and learned on this dataset and task:

{self.background_report}

And here is the most recent analysis. You should focus your critiques on these findings and approaches:

{self.cumulative_report}

Now generate a thorough review of these findings.
"""
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
    def from_checkpoint(cls, checkpoint_path_or_query: str, **override_kwargs) -> 'AIRWorkflowModular':
        """
        Load an AIRWorkflowModular instance from a checkpoint.

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

        # Extract workflow configuration flags from checkpoint (for proper restoration)
        # These can be overridden by explicit override_kwargs if provided
        checkpoint_git_tracking = workflow_state.get('git_tracking', False)
        checkpoint_multiturn = workflow_state.get('multiturn_hypothesis', True)
        checkpoint_review_search = workflow_state.get('review_search', False)
        checkpoint_review_design = workflow_state.get('review_design', False)

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

            self.logger.log_action("cleanup", "AIRWorkflowModular cleanup completed")
        except Exception as e:
            self.logger.log_error(f"Warning: Error during cleanup: {str(e)}")
