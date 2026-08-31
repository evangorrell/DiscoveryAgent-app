"""
Deep Research agent using OpenAI's o4-mini-deepresearch model.

This module provides an agent that sends a deep research query to OpenAI and
returns a structured Markdown report. It prefers the modern Responses API when
available, with a safe fallback to Chat Completions.

Fallback order when deep_research_provider == "paperclip":
  1. Paperclip SDK (auth via PAPERCLIP_API_KEY or `paperclip login` credentials)
  2. Perplexity (if PERPLEXITY_API_KEY is set and Paperclip returns a provider ERROR)
  3. Safe [RETRIEVAL: ERROR] markdown (never raises)

EMPTY results from Paperclip (valid "no relevant papers" response) do not trigger
the Perplexity fallback — only auth/import/network/provider failures do.
"""

from dataclasses import dataclass
from typing import Optional, Any, Dict

import openai
import requests
import os
import json
from pathlib import Path
from datetime import datetime

from ..config import LLMConfig
from ..utils.logger import Logger
from .base import BaseAgent, AgentToolConfig
from .retrieval import RetrievalStatus
from .retrieval.paperclip_sdk_provider import PaperclipSDKProvider


DEFAULT_SYSTEM_PROMPT = (
    "You are an expert research analyst. Conduct a comprehensive deep research "
    "investigation about the user's topic using authoritative sources. "
    "Synthesize findings into a concise, accurate Markdown report with clear sections "
    "and cite sources with titles and URLs. Avoid speculation."
)

DEFAULT_REPORT_INSTRUCTIONS = (
    "Return your answer as Markdown with these sections:\n\n"
    "# Title\n"
    "## Executive Summary\n"
    "- 3-5 bullet points with the most important findings\n\n"
    "## Key Findings\n"
    "- Provide well-structured analysis with evidence\n"
    "- Note any disagreements across sources\n\n"
    "## Methodology\n"
    "- Briefly outline how you conducted the research\n\n"
    "## Limitations\n\n"
    "## Sources\n"
    "- Use a bulleted list of sources with titles and direct URLs\n"
)


def _extract_text_from_openai_response(response: Any) -> str:
    """Robustly extract text from OpenAI Responses or Chat Completions objects."""
    # Modern Responses API often exposes an `output_text` convenience property
    try:
        text = getattr(response, "output_text", None)
        if isinstance(text, str) and text.strip():
            return text
    except Exception:
        pass

    # Responses API raw structure fallback
    try:
        output = getattr(response, "output", None)
        if output and isinstance(output, list):
            parts = []
            for item in output:
                content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
                if isinstance(content, list):
                    for c in content:
                        # SDK objects may have .type == "output_text" or generic .text
                        text_val = (
                            c.get("text") if isinstance(c, dict) else getattr(c, "text", None)
                        )
                        if isinstance(text_val, str):
                            parts.append(text_val)
            if parts:
                return "\n".join(parts)
    except Exception:
        pass

    # Chat Completions legacy fallback
    try:
        choices = getattr(response, "choices", None)
        if choices:
            msg = choices[0].message
            content = getattr(msg, "content", None)
            if isinstance(content, str):
                return content
            # Some SDKs return a list of content parts
            if isinstance(content, list):
                return "\n".join(
                    x if isinstance(x, str) else str(x) for x in content
                )
    except Exception:
        pass

    return ""


def _extract_text_from_perplexity_response(json_resp: Any) -> str:
    """Extract text from Perplexity API JSON responses in a robust way."""
    try:
        # Common Perplexity response shapes may include 'choices' or nested 'message'/'content'
        if isinstance(json_resp, dict):
            # choices -> [ { message: { content: "..." } } ]
            choices = json_resp.get("choices")
            if choices and isinstance(choices, list):
                first = choices[0]
                if isinstance(first, dict):
                    msg = first.get("message") or first.get("content") or first.get("text")
                    if isinstance(msg, dict):
                        content = msg.get("content") or msg.get("text")
                        if isinstance(content, str):
                            return content
                    if isinstance(msg, str):
                        return msg

            # fallback keys
            for key in ("output", "answer", "text", "content", "response"):
                val = json_resp.get(key)
                if isinstance(val, str) and val.strip():
                    return val

        # As a last resort, stringify the whole response
        import json as _json

        return _json.dumps(json_resp)
    except Exception:
        return ""


@dataclass
class DeepResearchConfig(AgentToolConfig):
    """Configuration for DeepResearchAgent."""
    llm_config: LLMConfig = None
    log_dir: str = "logs"
    max_output_tokens: int = 3000
    # Timeout for deep research provider requests (seconds). Shared across both
    # the Perplexity/OpenAI path and the Paperclip SDK path, since both are
    # long-running "deep research" calls.
    timeout: int = 1800
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    report_instructions: str = DEFAULT_REPORT_INSTRUCTIONS
    # "paperclip" (default) or "perplexity". Selects the retrieval backend
    # used by DeepResearchAgent.execute(); llm_config/api_provider validation
    # below only applies to the "perplexity" (and legacy "openai") path.
    deep_research_provider: str = "paperclip"

    def __post_init__(self) -> None:
        self.deep_research_provider = self.deep_research_provider or "paperclip"


class DeepResearchAgent(BaseAgent):
    """
    Agent that performs a deep research query using OpenAI's o4-mini-deepresearch
    and returns a Markdown report.
    """

    def __init__(self, config: DeepResearchConfig, logger=None):
        self.config = config
        self.llm_config = config.llm_config
        self.deep_research_provider = (
            getattr(config, "deep_research_provider", None) or "paperclip"
        )
        # Use passed logger or create a new one if not provided
        self.logger = logger if logger is not None else Logger("deep_research", log_dir=config.log_dir)

        self.client = None
        self._paperclip_provider = None

        if self.deep_research_provider == "paperclip":
            # The Paperclip SDK is imported lazily inside PaperclipSDKProvider.deep_research(),
            # so constructing the provider remains safe in an incomplete or legacy
            # environment -- failures surface as a normal ERROR result later.
            timeout = getattr(config, "timeout", 1800)
            self._paperclip_provider = PaperclipSDKProvider(timeout=timeout)
            return

        # Support both OpenAI and Perplexity as API providers
        if self.llm_config.api_provider not in ("openai", "perplexity"):
            raise ValueError(
                "DeepResearchAgent requires an OpenAI or Perplexity model (set api_provider to 'openai' or 'perplexity')."
            )

        # If using Perplexity, read the API key from PERPLEXITY_API_KEY env var
        if self.llm_config.api_provider == "perplexity":
            env_key = os.environ.get("PERPLEXITY_API_KEY")
            if not env_key:
                raise ValueError(
                    "PERPLEXITY_API_KEY environment variable not set for Perplexity provider"
                )
            # store the key on the config for downstream use
            self.llm_config.api_key = env_key

        # For OpenAI, require the api_key to be present on the LLMConfig
        if self.llm_config.api_provider == "openai":
            if not self.llm_config.api_key:
                raise ValueError("API key not set on LLMConfig (llm_config.api_key)")

        if self.llm_config.api_provider == "openai":
            self.client = openai.OpenAI(api_key=self.llm_config.api_key)

    def execute(self, query: str, searches_dir: Optional[str] = None) -> str:
        """
        Perform deep research for the given query and return a Markdown report.

        Args:
            query: The research question or topic.
            searches_dir: Optional directory to persist the raw search/audit output.

        Returns:
            Markdown report string. Never raises for provider/auth/network failures.
        """
        if self.deep_research_provider == "paperclip":
            return self._execute_paperclip(query, searches_dir)
        return self._execute_perplexity_or_openai(query, searches_dir)

    def _execute_paperclip(self, query: str, searches_dir: Optional[str] = None) -> str:
        """Run deep research via Paperclip, with Perplexity fallback on ERROR. Never raises.

        Fallback triggers only on RetrievalStatus.ERROR (auth/import/network/provider failure).
        EMPTY results (valid "no papers found" responses) are returned as-is without fallback.
        """
        self.logger.log_prompt("user", query, "Deep Research Query (Paperclip)")

        result = None
        paperclip_error_reason: Optional[str] = None

        try:
            result = self._paperclip_provider.deep_research(query)
        except Exception as exc:
            # Safety net — PaperclipSDKProvider is expected to catch internally.
            paperclip_error_reason = str(exc)
            error_md = f"**[RETRIEVAL: ERROR]** Unexpected error calling Paperclip provider: {exc}"
            self.logger.log_error(error_md)
            self._write_paperclip_audit(
                query, searches_dir, status="error", markdown=error_md,
                audit_metadata={"provider": "paperclip", "error": paperclip_error_reason},
            )

        if result is not None and result.status != RetrievalStatus.ERROR:
            # OK or EMPTY — no fallback; return as-is.
            self._write_paperclip_audit(
                query, searches_dir,
                status=result.status.value,
                markdown=result.markdown,
                audit_metadata=result.audit_metadata,
                error_message=result.error_message,
            )
            self.logger.log_response(result.markdown, "deep_research_result")
            return result.markdown

        # Paperclip ERROR — write audit then attempt Perplexity fallback.
        if result is not None:
            paperclip_error_reason = result.error_message
            self._write_paperclip_audit(
                query, searches_dir,
                status="error",
                markdown=result.markdown,
                audit_metadata={**result.audit_metadata, "provider_attempted": "paperclip"},
                error_message=result.error_message,
            )
            self.logger.log_error(result.markdown)

        perplexity_key = os.environ.get("PERPLEXITY_API_KEY")
        if perplexity_key:
            self.logger.log_error(
                "Paperclip deep research failed "
                f"({paperclip_error_reason or 'provider error'}); falling back to Perplexity."
            )
            return self._execute_perplexity(query, perplexity_key, "sonar-deep-research", searches_dir)

        final_md = (
            "**[RETRIEVAL: ERROR]** Deep research failed. "
            "Paperclip authentication may require `paperclip login` or `PAPERCLIP_API_KEY`; "
            "Perplexity fallback requires `PERPLEXITY_API_KEY`."
        )
        self.logger.log_error(final_md)
        return final_md

    def _write_paperclip_audit(
        self,
        query: str,
        searches_dir: Optional[str],
        status: str,
        markdown: str,
        audit_metadata: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Persist the rendered Markdown plus a JSON audit sidecar under searches_dir.

        Never includes secrets (e.g. PAPERCLIP_API_KEY) or environment dumps --
        only the structured audit_metadata the provider returned.
        """
        if not searches_dir:
            return
        try:
            search_dir_path = Path(searches_dir)
            search_dir_path.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = f"deep_research_paperclip_{ts}"

            md_path = search_dir_path / f"{base_name}.md"
            with open(md_path, "w", encoding="utf-8") as f:
                f.write("# Deep Research Output (Paperclip)\n\n")
                f.write(f"## Query\n{query}\n\n")
                f.write(markdown)

            sidecar = {
                "provider": "paperclip",
                "query": query,
                "status": status,
                "timestamp": datetime.now().isoformat(),
                "error_message": error_message,
            }
            if audit_metadata:
                for key in (
                    "sdk_version",
                    "search_result_id",
                    "search_count",
                    "map_result_id",
                    "reduce_result_id",
                    "citations",
                    "provider_attempted",
                ):
                    if key in audit_metadata:
                        sidecar[key] = audit_metadata[key]

            json_path = search_dir_path / f"{base_name}.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(sidecar, f, indent=2, default=str)
        except Exception:
            try:
                self.logger.log_error("Failed to write Paperclip deep research audit output to disk")
            except Exception:
                pass

    def _execute_perplexity(
        self,
        query: str,
        api_key: str,
        model_name: str,
        searches_dir: Optional[str] = None,
    ) -> str:
        """Shared Perplexity HTTP call used by both the direct Perplexity path and
        the Paperclip-failure fallback. Never raises.

        Args:
            query: Raw research query (report_instructions are appended internally).
            api_key: Perplexity API key — passed explicitly so callers never need to
                     mutate self.llm_config to make this work.
            model_name: Perplexity model (e.g. "sonar-deep-research").
            searches_dir: Optional directory to persist the Markdown output.

        Returns:
            Markdown report string, or an error string on failure.
        """
        user_prompt = query.strip() + f"\n\n{self.config.report_instructions}"
        self.logger.log_prompt("user", user_prompt, "Deep Research Query (Perplexity)")

        response_text = ""
        error: Optional[Exception] = None

        try:
            url = "https://api.perplexity.ai/chat/completions"
            payload = {
                "model": model_name or "sonar-deep-research",
                "messages": [{"role": "user", "content": user_prompt}],
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            timeout = getattr(self.config, "timeout", 300)
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if resp.ok:
                response_text = _extract_text_from_perplexity_response(resp.json())
                error = None
            else:
                error = Exception(f"Perplexity API returned status {resp.status_code}: {resp.text}")
        except Exception as e:
            error = e

        if error is not None and not response_text:
            err_msg = f"Error during deep research: {str(error)}"
            self.logger.log_error(err_msg)
            return err_msg

        # Strip <think>...</think> blocks
        if response_text:
            think_start = response_text.find("<think>")
            while think_start != -1:
                think_end = response_text.find("</think>", think_start)
                if think_end != -1:
                    response_text = response_text[:think_start] + response_text[think_end + 8:]
                    think_start = response_text.find("<think>")
                else:
                    break

        if response_text and ("# " not in response_text):
            response_text = "# Research Report\n\n" + response_text.strip()

        try:
            if searches_dir:
                p = Path(searches_dir)
                p.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_model = (model_name or "perplexity").replace("/", "_")
                filename = p / f"deep_research_{safe_model}_{ts}.md"
                with open(filename, "w", encoding="utf-8") as f:
                    f.write("# Deep Research Output\n\n")
                    f.write(f"## Query\n{query}\n\n")
                    f.write(f"## Model\n{safe_model}\n\n")
                    f.write(response_text)
        except Exception:
            try:
                self.logger.log_error("Failed to write deep research search output to disk")
            except Exception:
                pass

        self.logger.log_response(response_text, "deep_research_result")
        return response_text

    def _execute_perplexity_or_openai(self, query: str, searches_dir: Optional[str] = None) -> str:
        """Existing OpenAI/Perplexity deep research path (unchanged behavior)."""
        if self.llm_config.api_provider == "perplexity":
            return self._execute_perplexity(
                query, self.llm_config.api_key, self.llm_config.model_name, searches_dir
            )

        # OpenAI path
        user_prompt = query.strip() + f"\n\n{self.config.report_instructions}"
        self.logger.log_prompt("user", user_prompt, "Deep Research Query")

        response_text = ""
        error: Optional[Exception] = None

        try:
            response = self.client.responses.create(
                model=self.llm_config.model_name,
                input=user_prompt,
                max_output_tokens=self.config.max_output_tokens,
                # For reasoning models, option may be supported; ignore if unsupported
                # reasoning={"effort": "high"},
                system_instruction=self.config.system_prompt,
            )
            response_text = _extract_text_from_openai_response(response)
        except Exception as e:
            error = e

        if not response_text:
            try:
                chat_resp = self.client.chat.completions.create(
                    model=self.llm_config.model_name,
                    messages=[
                        {"role": "system", "content": self.config.system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=self.config.max_output_tokens,
                )
                response_text = _extract_text_from_openai_response(chat_resp)
                error = None
            except Exception as e2:
                error = e2

        if error is not None and not response_text:
            err_msg = f"Error during deep research: {str(error)}"
            self.logger.log_error(err_msg)
            return err_msg

        # Filter out any <think>...</think> blocks from the response
        if response_text:
            think_start = response_text.find("<think>")
            while think_start != -1:
                think_end = response_text.find("</think>", think_start)
                if think_end != -1:
                    response_text = response_text[:think_start] + response_text[think_end + 8:]
                    think_start = response_text.find("<think>")
                else:
                    break

        # Ensure some minimal Markdown structure if the model did not follow instructions
        if response_text and ("# " not in response_text):
            response_text = "# Research Report\n\n" + response_text.strip()

        # Persist search output if a directory was provided
        try:
            if searches_dir:
                p = Path(searches_dir)
                p.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                model_name = (self.llm_config.model_name or "openai").replace('/', '_')
                filename = p / f"deep_research_{model_name}_{ts}.md"
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(f"# Deep Research Output\n\n")
                    f.write(f"## Query\n{query}\n\n")
                    f.write(f"## Model\n{model_name}\n\n")
                    f.write(response_text)
        except Exception:
            # Don't fail the agent if persistence fails; just log
            try:
                self.logger.log_error("Failed to write deep research search output to disk")
            except Exception:
                pass

        self.logger.log_response(response_text, "deep_research_result")
        return response_text
