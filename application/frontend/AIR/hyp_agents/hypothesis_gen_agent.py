"""
Hypothesis Generation Agent

This module contains the HypothesisGenAgent class that specializes in generating
scientific hypotheses based on evidence and findings. This agent can be augmented
with deep research capabilities (e.g., literature search, web search) in the future.

The HypothesisGenAgent is responsible for:
- Generating initial hypotheses before research begins (can generate multiple and select best)
- Refining hypotheses based on accumulated evidence
- Producing testable, scientifically grounded hypotheses
- (Future) Deep literature review and novelty assessment
"""

import re
import time
from typing import Optional, List, Dict, Any

import openai
import anthropic

from ..config import LLMConfig, HypothesisGenerationConfig, get_slurm_resource_info
from ..utils.logger import Logger
from ..prompts.hyp_generation import (
    DATASET_DESCRIPTION_PROMPT,
    DATASET_DESCRIPTION_TASK_DESCRIPTION_PROMPT,
    REPORT_MULTITURN_PROMPT,
    BACKGROUND_REPORT_PROMPT,
    HYPOTHESIS_GENERATION_PROMPT,
)
from ..utils.token_utils import (
    count_openai_tokens_messages,
    count_anthropic_tokens,
)

# Prompt for judge LLM to select most interesting hypothesis
HYPOTHESIS_JUDGE_PROMPT = """
You will be presented with multiple scientific hypotheses. Your task is to select the most interesting one.

Consider factors such as:
- Novelty and originality
- Potential scientific impact
- Testability with available data
- Clarity and specificity

Select the hypothesis that would be most valuable to pursue in a scientific investigation.
""".strip()

HYPOTHESIS_GENERATION_SYSTEM_PROMPT = f"""
You are a scientific expert who is highly skilled in analyzing scientific data, results, and making judgements about future work.
You're creative and think outside the box, but you also recognize what hypotheses are feasible and interesting to the scientitic community.

Your job is to take in a dataset description, a description of a research question you're tasked with answering, and a report from some explorations on this dataset and task.
Using this information and your broad knowledge of the scientific literature in this field, you should generate hypotheses that are interesting and feasible to pursue in a scientific investigation.
Testing such hypotheses should require only computational methods; do not propose hypotheses that require experimental work or generating new data.
This hypothesis will be tested by a computational agent that is generally capable of running computational analyses and experiments and is an expert at writing code and running analyses.

You will be asked to generate a list of hypotheses that are proposals for future research on this dataset and task.
For each hypothesis, you must clearly explain why it is interesting, plausible, and how it could be tested with computational methods only.
You MUST ground your hypotheses in specific literature or in the evidence found from previous explorations on this dataset.
Provide specific citations (literature, sources, etc.) and evidence from the background report.
You may decide to continue exploring a hypothesis that was previously proposed or explored in the background report, but make sure to propose some hypotheses that are novel and that haven't been explored.

Importantly, your hypotheses are proposals; a scientist who is an expert in the field will choose from the list.
The scientist expects a data-driven, literature-grounded justification for why you would want to explore this hypothesis, so be rigorous in your description and reasoning.
"""

BACKGROUND_LITERATURE_PROMPT = """
## Background Literature

Below is a summarization of the literature involving this dataset and other similar datasets for this task.
Read this carefully to understand previous work and what has been done before.

{background_literature}
"""

BACKGROUND_REPORT_PROMPT = """
## Background Report

Below is a report summarizing the previous research that the agentic system has conducted on this data and task.
Consider this work carefully, and specifically cite the data from this report when generating your hypothesis.

{background_report}
"""

HYPOTHESIS_FORMAT_PROMPT_BASIC = """
### Hypothesis 1
**Title**: [Concise scientific title; preferred 10-14 words, hard maximum 18 words; title-like and specific enough to compare hypotheses; not a full hypothesis sentence or the full hypothesis claim]
**Hypothesis**: [One-sentence statement of the hypothesis]  
**Justification**: [2-3 paragraphs explaining why this is plausible, novel, and interesting, citing patterns that could be present in the data]  

### Hypothesis 2
**Title**: [Concise scientific title; preferred 10-14 words, hard maximum 18 words; title-like and specific enough to compare hypotheses; not a full hypothesis sentence or the full hypothesis claim]
**Hypothesis**: [One-sentence statement]  
**Justification**: [2-3 paragraphs of reasoning]  

[Continue in this pattern until you have produced exactly {n_hypotheses} hypotheses.]
"""

HYPOTHESIS_FORMAT_PROMPT_PLAN = """
### Hypothesis 1
**Title**: [Concise scientific title; preferred 10-14 words, hard maximum 18 words; title-like and specific enough to compare hypotheses; not a full hypothesis sentence or the full hypothesis claim]
**Hypothesis**: [One-sentence statement of the hypothesis]  
**Justification**: [2-3 paragraphs explaining why this is plausible, novel, and interesting, citing patterns that could be present in the data]  
**Plan**: [Enumerated list of a plan for how to test this hypothesis. Make sure your plan is clear and executable, but you don't need to flesh out every methodological detail.]

### Hypothesis 2
**Title**: [Concise scientific title; preferred 10-14 words, hard maximum 18 words; title-like and specific enough to compare hypotheses; not a full hypothesis sentence or the full hypothesis claim]
**Hypothesis**: [One-sentence statement]  
**Justification**: [2-3 paragraphs of reasoning]  
**Plan**: [Plan for how to test this hypothesis.]

[Continue in this pattern until you have produced exactly {n_hypotheses} hypotheses.]
"""

HELPER_AGENTS_PLAN_PROMPT = """
## Helper Agents Available

The DirectorAgent can call these helper agents to execute your plan steps. When outlining the plan, mention which helper agent is responsible for key actions when relevant.

{helper_agents_prompt}
"""

FORMAT_PROMPT = """You are working with a specific dataset and research task.

## Dataset Description

{dataset_description}

{task_section}

You have also been provided with a background report summarizing accumulated knowledge
from the workflow (including exploratory rounds and previous findings, when available).

Use this background report as the primary evidence for your hypotheses. Refer explicitly
to concrete findings, patterns, and results described in this report.

{background_literature_prompt}

{background_report}

Given all the evidence in this background report, and in light of the dataset and task
described above, propose {n_hypotheses} distinct hypotheses that are novel and scientifically impactful
and that could be investigated using this dataset and task.
You should focus on generating hypotheses that are data-driven, testable, and non-trivial.
When possible, make the hypotheses mutually distinct, i.e., that test different aspects
of the dataset and task.

{helper_agents_section}

Your output format MUST be:

{hypothesis_format_prompt}

Do not include any content outside of this structured list of hypotheses.
"""


class HypothesisGenAgent:
    """
    Hypothesis generation agent that specializes in creating scientifically sound
    hypotheses based on evidence, prior knowledge, and accumulated findings.

    This agent focuses purely on hypothesis generation and can be augmented with
    deep research tools (literature review, web search) in the future.

    For query generation and research orchestration, use DirectorAgent.
    """

    def __init__(
        self,
        config: HypothesisGenerationConfig,
        background_pdf_blocks: list = None,
        background_report_path: str = None,
        logger=None,
        n_hypotheses: int = 1,
        judge_llm_config: Optional[LLMConfig] = None,
        helper_agents_prompt: Optional[str] = None,
        citation_documents: Optional[List[dict]] = None,
    ):
        """
        Initialize the hypothesis generation agent.

        Args:
            config (HypothesisGenerationConfig): Full configuration object
            background_pdf_blocks (list, optional): List of PDF content blocks for background reading
            logger (Logger, optional): Shared logger instance. If None, creates a new one.
            n_hypotheses (int): Number of hypotheses to generate. If > 1, a judge LLM selects the best.
                Default is 1 (single hypothesis, no judging).
            judge_llm_config (LLMConfig, optional): Configuration for the judge LLM that selects
                the most interesting hypothesis. If None and n_hypotheses > 1, uses same config
                as hypothesis generation.
            helper_agents_prompt (str, optional): Description of helper agents available to
                the DirectorAgent; used to inform plan generation when generate_plan is enabled.
        """
        self.config = config

        self.llm_config = getattr(self.config, "llm_config", None)
        self.log_dir = getattr(self.config, "log_dir", None)
        self.system_prompt = getattr(self.config, "system_prompt", None)
        self.background_pdf_blocks = background_pdf_blocks
        self.background_report_path = background_report_path
        self.background_literature_content = None
        self.generate_plan = getattr(self.config, "generate_plan", False)
        self.helper_agents_prompt = helper_agents_prompt
        self.enable_citations = getattr(self.config, "enable_citations", False)
        self.cache_documents = getattr(self.config, "cache_documents", False)
        self.citation_documents: List[dict] = citation_documents or []
        self.last_response_citations: List[dict] = []
        self.last_response_documents: List[dict] = []

        # Multi-hypothesis generation parameters
        self.n_hypotheses = n_hypotheses
        self.judge_llm_config = judge_llm_config if judge_llm_config is not None else self.llm_config

        # Build system prompt focused on hypothesis generation
        hypothesis_system_prompt = self.system_prompt

        # Add note about background reading to system prompt if PDFs provided
        if background_pdf_blocks:
            hypothesis_system_prompt += "\n\n# Background Reading\n\nYou have been provided with background reading materials in PDF format at the start of this conversation. These documents contain domain-specific knowledge, research papers, or reference materials that should inform your hypothesis generation. Reference these materials as needed when generating hypotheses."

        # Add resource information if running on SLURM
        resource_info = get_slurm_resource_info()
        if resource_info:
            hypothesis_system_prompt += f"\n\n{resource_info}"

        if self.enable_citations and self.llm_config.api_provider == 'anthropic':
            hypothesis_system_prompt += "\n\nWhen citing evidence or background, include Claude citations so the user can trace claims back to provided documents."

        # Add hypothesis-specific instructions
        hypothesis_system_prompt += """

# Hypothesis Generation Guidelines

When generating hypotheses, you should:
1. Base hypotheses on evidence from the data and previous findings
2. Ensure hypotheses are testable and falsifiable
3. Consider novelty - look for unexplored aspects or novel insights
4. Ground hypotheses in scientific reasoning and domain knowledge
5. Provide clear rationale for each hypothesis
6. Consider multiple competing hypotheses when appropriate

Your hypotheses should advance scientific understanding and be actionable through further experimentation or analysis.
"""

        self.system_prompt = hypothesis_system_prompt

        # Use passed logger or create a new one if not provided
        self.logger = logger if logger is not None else Logger("hypothesis_gen_agent", log_dir=self.log_dir)

        # Log the system prompt for debugging and transparency
        try:
            self.logger.log_action("system_prompt_initialized", f"System prompt length: {len(self.system_prompt)} characters")
            self.logger.log_prompt("system", self.system_prompt, "hypothesis_gen_agent_system_prompt")
        except Exception:
            print(f"📝 Hypothesis generation agent system prompt initialized: {len(self.system_prompt)} characters")

        # Initialize client based on API provider
        if self.llm_config.api_provider == 'openai':
            if not self.llm_config.api_key:
                raise ValueError("OPENAI_API_KEY environment variable not set")
            self.client = openai.OpenAI(api_key=self.llm_config.api_key)
            self.logger.log_action("llm_client_init", f"Using OpenAI API with model: {self.llm_config.model_name}")
        else:  # anthropic
            if not self.llm_config.api_key:
                raise ValueError("ANTHROPIC_API_KEY environment variable not set")
            self.client = anthropic.Anthropic(api_key=self.llm_config.api_key)
            self.logger.log_action("llm_client_init", f"Using Anthropic API with model: {self.llm_config.model_name}")

        # Initialize judge client if generating multiple hypotheses
        self.judge_client = None
        if self.n_hypotheses > 1:
            if self.judge_llm_config.api_provider == 'openai':
                if not self.judge_llm_config.api_key:
                    raise ValueError("OPENAI_API_KEY environment variable not set for judge")
                self.judge_client = openai.OpenAI(api_key=self.judge_llm_config.api_key)
                self.logger.log_action("judge_client_init", f"Using OpenAI API for judge with model: {self.judge_llm_config.model_name}")
            else:  # anthropic
                if not self.judge_llm_config.api_key:
                    raise ValueError("ANTHROPIC_API_KEY environment variable not set for judge")
                self.judge_client = anthropic.Anthropic(api_key=self.judge_llm_config.api_key)
                self.logger.log_action("judge_client_init", f"Using Anthropic API for judge with model: {self.judge_llm_config.model_name}")

        # Persistent conversation messages for hypothesis refinement
        # For OpenAI, include the system message in the messages list.
        # For Anthropic, system prompt is passed separately.
        self.messages: List[Dict[str, Any]] = []
        if self.llm_config.api_provider == 'openai':
            self.messages.append({"role": "system", "content": self.system_prompt})

        self.background_report_read = False # Relates to background report used internally
        # Ensure citation documents have citations enabled + optional cache flag
        if self.citation_documents and self.enable_citations:
            for doc in self.citation_documents:
                if isinstance(doc, dict) and doc.get("type") == "document":
                    doc.setdefault("citations", {"enabled": True})
                    if self.cache_documents:
                        doc.setdefault("cache_control", {"type": "ephemeral"})

        if self.background_report_path:
            try:
                with open(self.background_report_path, 'r') as f:
                    self.background_literature_content = f.read()
                self.logger.log_action(
                    "background_report_loaded",
                    f"Loaded background report from {self.background_report_path}"
                )
            except Exception as e:
                self.logger.log_error(
                    f"Failed to load background report from {self.background_report_path}: {e}"
                )
                self.background_literature_content = None

        # Initialize conversation with background PDFs if provided
        if self.background_pdf_blocks and self.llm_config.api_provider == 'anthropic':
            # Add PDFs as the first user message
            initial_content = self.background_pdf_blocks.copy()
            initial_content.append({
                "type": "text",
                "text": "I have reviewed the background reading materials provided above. I'm ready to generate hypotheses for the scientific analysis."
            })

            self.messages.append({
                "role": "user",
                "content": initial_content
            })

            # Add assistant acknowledgment
            self.messages.append({
                "role": "assistant",
                "content": "Thank you for providing the background reading materials. I have reviewed the documents and will use this information to generate well-informed, scientifically grounded hypotheses."
            })

            self.logger.log_action("background_pdfs_initialized",
                f"Added {len(self.background_pdf_blocks) // 2} PDF documents to conversation initialization")
        elif self.background_pdf_blocks and self.llm_config.api_provider == 'openai':
            self.logger.log_action("background_pdfs_skipped",
                "Background PDFs provided but OpenAI API does not support PDF document blocks. Consider using Anthropic API for full PDF support.")

    def estimate_context_tokens(self) -> int:
        """Estimate input tokens for the current conversation state."""
        try:
            if self.llm_config.api_provider == 'openai':
                tokens = count_openai_tokens_messages(self.llm_config.model_name, self.messages)
            else:
                count_info = count_anthropic_tokens(
                    client=self.client,
                    model_name=self.llm_config.model_name,
                    messages=self.messages,
                    system=self.system_prompt,
                )
                tokens = int(count_info.get("input_tokens", 0))
            self.logger.log_action("token_estimate", f"Estimated input tokens: {tokens}")
            return tokens
        except Exception as e:
            self.logger.log_error(f"Token estimation failed: {str(e)}")
            return 0

    def _extract_text_from_anthropic_response(self, response) -> str:
        """Safely extract concatenated text blocks from an Anthropic response, even when tools are used."""
        text_parts = []
        try:
            for block in getattr(response, "content", []):
                if getattr(block, "type", None) == "text" and hasattr(block, "text"):
                    text_parts.append(block.text)
            return "\n".join(part for part in text_parts if part)
        except Exception:
            return ""

    def _extract_text_and_citations(self, response) -> (str, List[dict]):
        """
        Extract concatenated text blocks and associated citations from an Anthropic response.
        """
        text_parts = []
        citations: List[dict] = []
        try:
            for block in getattr(response, "content", []):
                if getattr(block, "type", None) == "text" and hasattr(block, "text"):
                    text_parts.append(block.text)
                    block_citations = []
                    block_cits = getattr(block, "citations", None)
                    if block_cits:
                        for c in block_cits:
                            if hasattr(c, "to_dict"):
                                block_citations.append(c.to_dict())
                            elif isinstance(c, dict):
                                block_citations.append(c)
                    if block_citations:
                        citations.append({
                            "text": block.text,
                            "citations": block_citations,
                        })
            return "\n".join(part for part in text_parts if part), citations
        except Exception:
            return "", []

    def _audit_exchange(self, params, response=None, text=None, error=None, attempt=None):
        from AIR.utils.scientific_audit import provider_exchange_details
        logger = getattr(self, "logger", None)
        if logger is not None:
            logger.log_action("api_exchange", provider_exchange_details(
                "HypothesisGenAgent", params, response, text=text, error=error,
                provider=self.llm_config.api_provider, attempt=attempt))

    def _call_api_with_retry(self, api_params: dict, max_retries: int = 5, wait_time: int = 60, track_citations: bool = False, documents: Optional[List[dict]] = None) -> str:
        """
        Call the LLM API with retry logic for handling transient errors.

        Args:
            api_params: Parameters to pass to the API
            max_retries: Maximum number of retry attempts (default: 5)
            wait_time: Time to wait between retries in seconds (default: 60)

        Returns:
            str: The response text from the API

        Raises:
            Exception: If all retry attempts fail
        """
        last_error = None

        if not track_citations:
            self.last_response_citations = []
            self.last_response_documents = documents or []

        for attempt in range(max_retries + 1):  # +1 for initial attempt
            try:
                if self.llm_config.api_provider == 'openai':
                    response = self.client.chat.completions.create(
                        model=self.llm_config.model_name,
                        messages=self.messages,
                    )
                    if track_citations:
                        self.last_response_citations = []
                        self.last_response_documents = documents or []
                    text = response.choices[0].message.content
                    self._audit_exchange({"model": self.llm_config.model_name,
                                          "messages": self.messages}, response, text, attempt=attempt)
                    return text
                else:  # anthropic
                    response = self._stream_anthropic_message(self.client, api_params)
                    if track_citations:
                        text, citations = self._extract_text_and_citations(response)
                        self.last_response_citations = citations
                        self.last_response_documents = documents or []
                        self._audit_exchange(api_params, response, text, attempt=attempt)
                        return text
                    text = self._extract_text_from_anthropic_response(response)
                    self._audit_exchange(api_params, response, text, attempt=attempt)
                    return text

            except (anthropic.InternalServerError, anthropic.APIError, openai.APIError) as e:
                last_error = e
                error_msg = str(e)
                self._audit_exchange(api_params, error=e, attempt=attempt)

                # Check if this is a retryable error
                if "Overloaded" in error_msg or "500" in error_msg or "503" in error_msg or "429" in error_msg:
                    if attempt < max_retries:
                        self.logger.log_action(
                            "api_retry",
                            f"API error (attempt {attempt + 1}/{max_retries + 1}): {error_msg}. "
                            f"Waiting {wait_time} seconds before retry..."
                        )
                        time.sleep(wait_time)
                        continue
                    else:
                        self.logger.log_error(f"Max retries ({max_retries}) reached. Last error: {error_msg}")
                        raise Exception(f"API call failed after {max_retries} retries: {error_msg}")
                else:
                    # Non-retryable error, raise immediately
                    raise

            except Exception as e:
                # For unexpected errors, raise immediately without retry
                self._audit_exchange(api_params, error=e, attempt=attempt)
                last_error = e
                raise

        # If we somehow exit the loop without returning or raising
        if last_error:
            raise Exception(f"API call failed: {str(last_error)}")
        raise Exception("API call failed for unknown reason")

    def call_llm_once(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        max_retries: int = 3,
        wait_time: int = 30,
    ) -> str:
        """
        Call the LLM once with an isolated system/user prompt (no conversation state).

        This is useful for helper steps like summarization without mutating self.messages.
        """
        temperature = self.llm_config.temperature if temperature is None else temperature
        if max_tokens is None:
            max_tokens = self.config.max_tokens

        logger = getattr(self, "logger", None)
        if logger is not None:
            logger.log_action("hypothesis_helper_request", {
                "model": self.llm_config.model_name, "provider": self.llm_config.api_provider,
                "system_prompt": system_prompt, "user_prompt": user_prompt,
                "max_tokens": max_tokens, "temperature": temperature,
            })
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                if self.llm_config.api_provider == 'openai':
                    response = self.client.chat.completions.create(
                        model=self.llm_config.model_name,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                    text = response.choices[0].message.content
                    if logger is not None:
                        logger.log_response(text, "hypothesis_helper")
                    self._audit_exchange({"model": self.llm_config.model_name,
                                          "max_tokens": max_tokens, "temperature": temperature},
                                         response, text, attempt=attempt)
                    return text

                response = self._stream_anthropic_message(self.client, {
                    "model": self.llm_config.model_name,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                })
                text = self._extract_text_from_anthropic_response(response)
                if logger is not None:
                    logger.log_response(text, "hypothesis_helper")
                self._audit_exchange({"model": self.llm_config.model_name,
                                      "max_tokens": max_tokens, "temperature": temperature},
                                     response, text, attempt=attempt)
                return text

            except (anthropic.InternalServerError, anthropic.APIError, openai.APIError) as e:
                last_error = e
                error_msg = str(e)
                self._audit_exchange({"model": self.llm_config.model_name}, error=e, attempt=attempt)
                if "Overloaded" in error_msg or "500" in error_msg or "503" in error_msg or "429" in error_msg:
                    if attempt < max_retries:
                        self.logger.log_action(
                            "api_retry",
                            f"API error (attempt {attempt + 1}/{max_retries + 1}): {error_msg}. "
                            f"Waiting {wait_time} seconds before retry..."
                        )
                        time.sleep(wait_time)
                        continue
                    self.logger.log_error(f"Max retries ({max_retries}) reached. Last error: {error_msg}")
                    raise Exception(f"API call failed after {max_retries} retries: {error_msg}")
                raise
            except Exception as e:
                self._audit_exchange({"model": self.llm_config.model_name}, error=e, attempt=attempt)
                last_error = e
                raise

        if last_error:
            raise Exception(f"API call failed: {str(last_error)}")
        raise Exception("API call failed for unknown reason")

    @staticmethod
    def _stream_anthropic_message(client, api_params: dict):
        """Consume an Anthropic SSE response and return its final Message.

        Hypothesis responses can take several minutes. Streaming lets Anthropic
        send lifecycle and content events during that interval, avoiding idle
        connection limits while retaining the same response object used for
        text, citations, usage, and stop-reason handling.
        """
        with client.messages.stream(**api_params) as stream:
            return stream.get_final_message()

    def add_user_message(self, content) -> None:
        """Append a user message (string or structured content) to the conversation and log it."""
        self.messages.append({"role": "user", "content": content})
        # Log stringified preview to avoid huge dumps
        try:
            self.logger.log_prompt("user", content, "Hypothesis Generation")
        except Exception:
            pass

    def add_user_message_with_documents(self, text: str, documents: Optional[List[dict]] = None) -> None:
        """
        Append a user message that includes document blocks for citations (Anthropic).
        """
        if documents and self.llm_config.api_provider == 'anthropic':
            content = list(documents) + [{"type": "text", "text": text}]
        else:
            content = text
        self.add_user_message(content)

    def add_assistant_message(self, content: str) -> None:
        """Append an assistant message to the conversation and log it."""
        self.messages.append({"role": "assistant", "content": content})
        self.logger.log_response(content, "hypothesis_generation")

    def generate_hypothesis(
        self,
        dataset_description: str = None,
        task_description: str = None,
        init_hypothesis: bool = False,
        background_report: str = None,
        use_judge: bool = True,
        format_prompt_override: str = None,
    ) -> Optional[str]:
        """
        Generate hypothesis text using the conversation context.

        Behavior:
        - If self.n_hypotheses == 1: generate a single hypothesis.
        - If self.n_hypotheses  > 1: generate multiple hypotheses in a *single* LLM call,
          parse individual hypotheses from the markdown output, and optionally use a
          judge LLM to select the best one.

        Args:
            dataset_description (str): Description of the dataset
            task_description (str): Description of the task
            init_hypothesis (bool): Whether this is an initial hypothesis generation
            background_report (str): Consolidated report / accumulated knowledge to condition on

        Returns:
            Optional[str]: Generated hypothesis text from the response. If multiple
                hypotheses were generated, returns the judge-selected best hypothesis.
        """
        # If only generating one hypothesis, use the original logic
        if self.n_hypotheses == 1:
            return self._generate_single_hypothesis(
                dataset_description=dataset_description,
                task_description=task_description,
                init_hypothesis=init_hypothesis,
                background_report=background_report,
            )

        # Generate multiple hypotheses (single LLM call + markdown parsing)
        return self._generate_multiple_hypotheses_with_judge(
            dataset_description=dataset_description,
            task_description=task_description,
            init_hypothesis=init_hypothesis,
            background_report=background_report,
            use_judge=use_judge,
            format_prompt_override=format_prompt_override,
        )

    def _build_hypothesis_prefix(
        self,
        dataset_description: str = None,
        task_description: str = None,
        background_report: str = None,
    ) -> str:
        """
        Build the common prefix prompt used for both single- and multi-hypothesis generation.

        This includes:
        - Dataset / task description OR prior report
        - Optional background_report (only once per agent instance)
        - Instructions to first produce an **Interpretation** of the results
        """
        prefix_prompt = ""

        # Add dataset/task description (if provided)
        if dataset_description is not None:
            if task_description is not None:
                prefix_prompt += DATASET_DESCRIPTION_TASK_DESCRIPTION_PROMPT.format(
                    dataset_description=dataset_description,
                    task_description=task_description
                )
            else:
                prefix_prompt += DATASET_DESCRIPTION_PROMPT.format(
                    dataset_description=dataset_description
                )

        # Add background report if available (accumulated knowledge from previous rounds)
        if background_report and (not self.background_report_read):
            prefix_prompt += "\n\n" + BACKGROUND_REPORT_PROMPT.format(
                background_report=background_report
            ) + "\n\n"
            self.background_report_read = True

        if prefix_prompt != "":
            prefix_prompt += \
"""
Produce first an interpretation of these results, which should look like this:

**Interpretation**: [Your interpretation of the report from the previous query]

Then you should focus on generating a hypothesis.

"""

        return prefix_prompt

    def _generate_single_hypothesis(
        self,
        dataset_description: str = None,
        task_description: str = None,
        init_hypothesis: bool = False,
        background_report: str = None,
    ) -> Optional[str]:
        """
        Generate a single hypothesis (original implementation).

        Args:
            dataset_description (str): Description of the dataset
            task_description (str): Description of the task
            init_hypothesis (bool): Whether this is an initial hypothesis generation
            background_report (str): Consolidated report / accumulated knowledge

        Returns:
            Optional[str]: Generated hypothesis text from the response
        """

        prefix_prompt = self._build_hypothesis_prefix(
            dataset_description=dataset_description,
            task_description=task_description,
            background_report=background_report,
        )

        hypothesis_prompt = prefix_prompt + HYPOTHESIS_GENERATION_PROMPT

        # Add as a new user turn
        documents = self.citation_documents if (self.enable_citations and self.llm_config.api_provider == 'anthropic') else None
        if documents:
            self.add_user_message_with_documents(hypothesis_prompt, documents=documents)
        else:
            self.add_user_message(hypothesis_prompt)

        try:
            # Prepare API call parameters
            api_params = {
                "model": self.llm_config.model_name,
                "max_tokens": self.config.max_tokens,
                "system": self.system_prompt,
                "messages": self.messages,
            }

            # Call API with retry logic
            track_citations = bool(documents)
            result_text = self._call_api_with_retry(
                api_params,
                track_citations=track_citations,
                documents=documents,
            )

            # Add assistant turn to the conversation and log
            self.add_assistant_message(result_text)

            return result_text

        except Exception as e:
            error_msg = f"Error generating hypothesis: {str(e)}"
            self.logger.log_error(error_msg)
            raise Exception(error_msg)

    def _generate_multiple_hypotheses_with_judge(
        self,
        dataset_description: str = None,
        task_description: str = None,
        report: str = "",
        init_hypothesis: bool = False,
        background_report: str = None,
        format_prompt_override: str = None,
        use_judge: bool = True,
    ) -> Optional[str]:
        """
        Generate multiple hypotheses in a single LLM call and optionally use a judge
        LLM to select the best one.

        Args:
            dataset_description (str): Description of the dataset
            task_description (str): Description of the task
            report (str): Report from previous queries in the current round
            init_hypothesis (bool): Whether this is an initial hypothesis generation
            background_report (str): Accumulated knowledge from previous rounds

        Returns:
            Optional[str]: The judge-selected best hypothesis
        """
        self.logger.log_action(
            "multi_hypothesis_start",
            f"Generating up to {self.n_hypotheses} hypotheses in a single LLM call for judging",
        )
        print(f"🧠 Generating up to {self.n_hypotheses} candidate hypotheses (single LLM call)...")

        # 1) Build shared prefix (dataset/task/report/background + interpretation instructions)
        prefix_prompt = self._build_hypothesis_prefix(
            dataset_description=dataset_description,
            task_description=task_description,
            background_report=background_report,
        )

        # Build optional task section (header + content) only when provided
        task_section = ""
        if task_description:
            task_section = "## Task Description\n\n" + task_description + "\n"

        # 2) Build multi-hypothesis generation instructions + strict formatting,
        #    using dataset/task description and the background_report as the
        #    consolidated source of evidence.
        background_literature_prompt = ""
        if self.background_literature_content:
            background_literature_prompt = BACKGROUND_LITERATURE_PROMPT.format(
                background_literature=self.background_literature_content
            )

        background_report_prompt = ""
        if background_report:
            background_report_prompt = BACKGROUND_REPORT_PROMPT.format(
                background_report=background_report
            )

        helper_agents_section = ""
        if self.generate_plan and self.helper_agents_prompt:
            helper_agents_section = HELPER_AGENTS_PLAN_PROMPT.format(
                helper_agents_prompt=self.helper_agents_prompt.strip()
            )

        if format_prompt_override:
            if "{n_hypotheses}" in format_prompt_override:
                hypothesis_format_prompt = format_prompt_override.format(
                    n_hypotheses=self.n_hypotheses
                )
            else:
                hypothesis_format_prompt = format_prompt_override
        else:
            if self.generate_plan:
                hypothesis_format_prompt = HYPOTHESIS_FORMAT_PROMPT_PLAN.format(
                    n_hypotheses=self.n_hypotheses
                )
            else:
                hypothesis_format_prompt = HYPOTHESIS_FORMAT_PROMPT_BASIC.format(
                    n_hypotheses=self.n_hypotheses
                )

        multi_hypothesis_instructions = FORMAT_PROMPT.format(
            n_hypotheses=self.n_hypotheses,
            dataset_description=dataset_description or "",
            task_section=task_section,
            background_report=background_report_prompt,
            background_literature_prompt=background_literature_prompt,
            hypothesis_format_prompt=hypothesis_format_prompt,
            helper_agents_section=helper_agents_section,
        )

        hypothesis_prompt = prefix_prompt + multi_hypothesis_instructions

        # 3) Call the LLM once to generate all candidate hypotheses
        documents = self.citation_documents if (self.enable_citations and self.llm_config.api_provider == 'anthropic') else None
        if documents:
            self.add_user_message_with_documents(hypothesis_prompt, documents=documents)
        else:
            self.add_user_message(hypothesis_prompt)

        try:
            api_params = {
                "model": self.llm_config.model_name,
                "max_tokens": self.config.max_tokens,
                "system": self.system_prompt,
                "messages": self.messages,
            }

            track_citations = bool(documents)
            result_text = self._call_api_with_retry(
                api_params,
                track_citations=track_citations,
                documents=documents,
            )

            # Persist assistant turn
            self.add_assistant_message(result_text)

        except Exception as e:
            error_msg = f"Error generating multiple hypotheses: {str(e)}"
            self.logger.log_error(error_msg)
            raise Exception(error_msg)

        # 4) Parse individual hypotheses from markdown output
        hypotheses = self._parse_hypotheses_from_markdown(result_text)

        if not hypotheses:
            error_msg = "Failed to parse any hypotheses from model output"
            self.logger.log_error(error_msg)
            print("❌ No hypotheses could be parsed from LLM output")
            raise Exception(error_msg)

        # Respect requested limit (in case the model produced extra sections)
        if len(hypotheses) > self.n_hypotheses:
            self.logger.log_action(
                "multi_hypothesis_truncate",
                f"Parsed {len(hypotheses)} hypotheses, truncating to {self.n_hypotheses}",
            )
            hypotheses = hypotheses[: self.n_hypotheses]

        if len(hypotheses) < self.n_hypotheses:
            self.logger.log_action(
                "partial_hypothesis_generation",
                f"Only {len(hypotheses)}/{self.n_hypotheses} hypotheses parsed from output",
            )
            print(f"⚠️ Only {len(hypotheses)}/{self.n_hypotheses} hypotheses parsed from output")

        # Optionally skip judge (or when only one hypothesis is parsed)
        if (not use_judge) or len(hypotheses) == 1 or self.n_hypotheses == 1:
            self.logger.log_action(
                "multi_hypothesis_no_judge",
                f"Returning hypotheses without judge (count={len(hypotheses)})",
            )
            return hypotheses[0]

        # 5) Use judge LLM to select the best hypothesis
        print(f"\n⚖️ Using judge LLM to select best hypothesis...")
        self.logger.log_action(
            "judge_selection_start",
            f"Judging {len(hypotheses)} hypotheses",
        )

        selected_hypothesis = self._judge_hypotheses(hypotheses)

        return selected_hypothesis

    def _parse_hypotheses_from_markdown(self, text: str) -> List[str]:
        """
        Parse individual hypotheses from a markdown string.

        Expected structure (enforced via FORMAT_PROMPT):

        ### Hypothesis 1
        **Hypothesis**: ...
        **Justification**: ...

        ### Hypothesis 2
        ...

        Returns a list of markdown blocks, one per hypothesis (header + body).
        """
        hypotheses: List[str] = []

        try:
            # Find all "### Hypothesis N" section headers
            pattern = r"(?m)^###\s+Hypothesis\s+\d+\s*"
            matches = list(re.finditer(pattern, text))

            if not matches:
                # Fallback: if no structured headers, treat entire text as one hypothesis
                stripped = text.strip()
                return [stripped] if stripped else []

            for idx, match in enumerate(matches):
                start = match.start()
                end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
                block = text[start:end].strip()
                if block:
                    hypotheses.append(block)

            return hypotheses

        except Exception as e:
            self.logger.log_error(f"Error parsing hypotheses from markdown: {str(e)}")
            # Return the whole response when structured parsing fails.
            stripped = text.strip()
            return [stripped] if stripped else []

    def _judge_hypotheses(self, hypotheses: List[str]) -> str:
        """
        Use a judge LLM to select the most interesting hypothesis.

        Args:
            hypotheses: List of hypothesis strings to judge

        Returns:
            str: The selected best hypothesis
        """
        # Build the judge prompt with all hypotheses
        judge_prompt = HYPOTHESIS_JUDGE_PROMPT + "\n\n"
        judge_prompt += "Here are the candidate hypotheses:\n\n"

        for i, hyp in enumerate(hypotheses, 1):
            judge_prompt += f"## Hypothesis {i}\n\n{hyp}\n\n"
            judge_prompt += "---\n\n"

        judge_prompt += f"""
Please select the most interesting hypothesis from the {len(hypotheses)} candidates above.

Respond with:
1. The number of your selected hypothesis (1-{len(hypotheses)})
2. A brief justification (2-3 sentences) for your selection

Format your response as:
**Selected Hypothesis**: [number]
**Justification**: [your reasoning]
"""

        from uuid import uuid4
        evaluation_id = uuid4().hex
        judge_identity = {
            "evaluation_id": evaluation_id,
            "agent": "hypothesis_judge",
            "provider": self.judge_llm_config.api_provider,
            "model": self.judge_llm_config.model_name,
        }
        self.logger.log_action("hypothesis_judge_requested", {
            **judge_identity, "status": "started", "candidates": hypotheses,
            "system_prompt": HYPOTHESIS_JUDGE_PROMPT, "prompt": judge_prompt,
            "max_tokens": 2000,
        })

        try:
            # Call judge LLM
            if self.judge_llm_config.api_provider == 'openai':
                response = self.judge_client.chat.completions.create(
                    model=self.judge_llm_config.model_name,
                    messages=[
                        {"role": "system", "content": HYPOTHESIS_JUDGE_PROMPT},
                        {"role": "user", "content": judge_prompt}
                    ],
                    max_tokens=2000,
                )
                judge_response = response.choices[0].message.content
            else:  # anthropic
                response = self._stream_anthropic_message(self.judge_client, {
                    "model": self.judge_llm_config.model_name,
                    "max_tokens": 2000,
                    "system": HYPOTHESIS_JUDGE_PROMPT,
                    "messages": [
                        {"role": "user", "content": judge_prompt}
                    ],
                })
                judge_response = self._extract_text_from_anthropic_response(response)

            # Parse the selected hypothesis number
            selected_idx = self._parse_judge_selection(judge_response, len(hypotheses))
            self.logger.log_action("hypothesis_judge_decision", {
                **judge_identity,
                "status": "success" if selected_idx is not None else "fallback",
                "response": judge_response,
                "selected_index": selected_idx if selected_idx is not None else 0,
                "selected_hypothesis": hypotheses[selected_idx if selected_idx is not None else 0],
                "fallback_reason": None if selected_idx is not None else "unparseable_selection",
            })

            if selected_idx is not None:
                selected_hypothesis = hypotheses[selected_idx]
                self.logger.log_action("hypothesis_selected",
                    f"Judge selected hypothesis {selected_idx + 1}")
                print(f"✓ Judge selected hypothesis {selected_idx + 1}")

                # Log the full selected hypothesis
                self.logger.log_action("selected_hypothesis_full",
                    f"Full selected hypothesis:\n{selected_hypothesis}")

                return selected_hypothesis
            else:
                # Fallback: return first hypothesis if parsing fails
                self.logger.log_error("Failed to parse judge selection, using first hypothesis")
                print(f"⚠️ Failed to parse judge selection, using first hypothesis")
                return hypotheses[0]

        except Exception as e:
            self.logger.log_action("hypothesis_judge_failure", {
                **judge_identity, "status": "error", "error_type": type(e).__name__,
                "error": str(e), "http_status": getattr(e, "status_code", None),
                "request_id": getattr(e, "request_id", None),
                "fallback_reason": "provider_failure", "selected_index": 0,
                "selected_hypothesis": hypotheses[0],
            })
            error_msg = "Error during hypothesis judging; details saved in scientific audit"
            self.logger.log_error(error_msg)
            print(f"⚠️ {error_msg}, using first hypothesis")
            # Fallback: return first hypothesis on error
            return hypotheses[0]

    def _parse_judge_selection(self, judge_response: str, n_hypotheses: int) -> Optional[int]:
        """
        Parse the judge's response to extract the selected hypothesis number.

        Args:
            judge_response: The judge LLM's response text
            n_hypotheses: Total number of hypotheses

        Returns:
            Optional[int]: 0-indexed hypothesis number, or None if parsing fails
        """
        import re

        # Try to find "Selected Hypothesis: N" pattern
        pattern = r"\*\*Selected Hypothesis\*\*:\s*(\d+)"
        match = re.search(pattern, judge_response, re.IGNORECASE)

        if match:
            selected_num = int(match.group(1))
            # Convert to 0-indexed and validate
            if 1 <= selected_num <= n_hypotheses:
                return selected_num - 1

        # Fallback: try to find any number at the beginning
        pattern = r"^\s*(\d+)"
        match = re.search(pattern, judge_response, re.MULTILINE)

        if match:
            selected_num = int(match.group(1))
            if 1 <= selected_num <= n_hypotheses:
                return selected_num - 1

        # Fallback: search for "Hypothesis N" mentions in the text
        for i in range(1, n_hypotheses + 1):
            pattern = rf"Hypothesis\s+{i}\b"
            if re.search(pattern, judge_response, re.IGNORECASE):
                return i - 1

        return None

    def reset_conversation(self) -> None:
        """Reset the conversation to the initial state (preserving the system prompt)."""
        self.messages = []
        self.background_report_read = False
        if self.llm_config.api_provider == 'openai':
            self.messages.append({"role": "system", "content": self.system_prompt})
        self.logger.log_action("conversation_reset", "Conversation reset for hypothesis generation agent")

    def save_state(self) -> dict:
        """
        Save complete conversation state for checkpointing.

        Returns:
            dict: Complete agent state including full conversation history
        """
        return {
            'agent_type': type(self).__name__,
            'messages': self.messages.copy(),  # Full conversation history
            'system_prompt': self.system_prompt,
            'conversation_length': len(self.messages),
            'background_report_read': self.background_report_read,
            'estimated_tokens': self.estimate_context_tokens(),
            'llm_config': {
                'model_name': self.llm_config.model_name,
                'api_provider': self.llm_config.api_provider,
                'temperature': getattr(self.llm_config, 'temperature', None),
                'frequency_penalty': getattr(self.llm_config, 'frequency_penalty', None),
                'api_key_hash': hash(self.llm_config.api_key) if self.llm_config.api_key else None
            },
            'config_data': {
                'log_dir': self.config.log_dir,
                'max_tokens': self.config.max_tokens,
                'system_prompt': self.config.system_prompt,
            },
        }

    def restore_state(self, state: dict) -> bool:
        """
        Restore complete conversation state from checkpoint.

        Args:
            state: State dictionary from checkpoint

        Returns:
            bool: True if restoration was successful
        """
        try:
            # Restore conversation messages
            self.messages = state.get('messages', []).copy()

            # Verify conversation integrity for OpenAI
            if self.llm_config.api_provider == 'openai':
                # Ensure system message is first
                if not self.messages or self.messages[0].get('role') != 'system':
                    self.messages.insert(0, {"role": "system", "content": self.system_prompt})

            # Validate message structure
            for i, msg in enumerate(self.messages):
                if not isinstance(msg, dict) or 'role' not in msg or 'content' not in msg:
                    self.logger.log_error(f"Invalid message structure at index {i}")
                    return False

            # Restore other state if needed (most is already set during initialization)
            self.background_report_read = state.get('background_report_read', False)
            restored_tokens = state.get('estimated_tokens', 0)
            current_tokens = self.estimate_context_tokens()

            # Log restoration details
            self.logger.log_action("state_restored",
                f"Restored {len(self.messages)} messages, "
                f"tokens: {restored_tokens} -> {current_tokens}")

            # Verify conversation makes sense
            conversation_length = state.get('conversation_length', 0)
            if len(self.messages) != conversation_length:
                self.logger.log_action("conversation_length_mismatch",
                    f"Expected {conversation_length}, got {len(self.messages)}")

            return True

        except Exception as e:
            self.logger.log_error(f"Failed to restore hypothesis generation agent state: {str(e)}")
            return False

    def get_state_schema(self) -> dict:
        """
        Get schema description for state validation.

        Returns:
            dict: Schema describing state fields
        """
        return {
            'agent_type': 'Class name of the hypothesis generation agent',
            'messages': 'Complete conversation history (list of message dicts)',
            'system_prompt': 'System prompt used for the agent',
            'conversation_length': 'Number of messages in conversation',
            'estimated_tokens': 'Estimated token count for conversation',
            'llm_config': 'LLM configuration details',
            'config_data': 'Agent configuration data',
        }

    def cleanup(self):
        """Clean up resources."""
        try:
            self.logger.log_action("cleanup", "HypothesisGenAgent cleanup completed")
        except Exception as e:
            # Use centralized logger for cleanup errors
            try:
                self.logger.log_error(f"Warning: Error during cleanup: {str(e)}")
            except Exception:
                import logging
                logging.getLogger("ai_researcher").error(f"Warning: Error during cleanup: {str(e)}")
