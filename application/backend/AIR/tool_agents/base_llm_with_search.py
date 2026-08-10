"""
General Claude-based LLM wrapper that supports Anthropic tool use (web_search).

This module provides a lightweight class `BaseLLMWithSearch` which wraps an
Anthropic client and exposes a `forward` method. The forward method will call
the Anthropic Messages API and enable the `web_search` tool so the model can
invoke web searches internally. The class is intentionally general and not an
agent with specialized reasoning loops; it focuses on providing a reusable
interface for other modules in the codebase to call the model with tool use.
"""

from typing import Any, Dict, Optional, List
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime

import anthropic
import requests

from ..config import LLMConfig
from ..utils.logger import Logger
from ..base_llm import anthropic_api_calculate_cost

PERPLEXITY_TIMEOUT = 180 # In seconds


class BaseLLMWithSearch:
    """Claude-based LLM wrapper that enables the `web_search` tool.

    Example:
        cfg = BaseLLMWithSearchConfig(LLMConfig(model_name="claude-3-7-sonnet-20250219"))
        llm = BaseLLMWithSearch(cfg)
        out = llm.forward("What is the latest on X?", searches_dir="./outputs")
    """

    def __init__(
        self,
        llm_config: LLMConfig,
        log_dir: str = "logs",
        max_tokens: int = None,
        max_web_searches: int = 3,
        system_prompt: Optional[str] = None,
        perplexity_timeout: int = PERPLEXITY_TIMEOUT,
        logger=None,
    ):
        self.llm_config = llm_config
        # Use passed logger or create a new one if not provided
        self.logger = logger if logger is not None else Logger("base_llm_with_search", log_dir=log_dir)

        # Determine backend based on LLMConfig.model_name. If the model name
        # contains "claude-" we use Anthropic; if it contains "sonar" or
        # "perplexity" we use Perplexity's Sonar reasoning via simple HTTP.
        model_lower = (self.llm_config.model_name or "").lower()
        if "claude-" in model_lower:
            self.backend = "anthropic"
            if not self.llm_config.api_key:
                raise ValueError("ANTHROPIC_API_KEY environment variable not set or provided in LLMConfig")
            self.client = anthropic.Anthropic(api_key=self.llm_config.api_key)
        elif "sonar" in model_lower or "perplexity" in model_lower:
            self.backend = "perplexity"
            if not self.llm_config.api_key:
                raise ValueError("PERPLEXITY_API_KEY environment variable not set or provided in LLMConfig")
        else:
            raise ValueError("Unsupported model for BaseLLMWithSearch: must be Claude or Perplexity Sonar")

        # Defaults - use config's max_tokens if available, otherwise fallback
        self.model_name = self.llm_config.model_name
        # Priority: explicit parameter > config value > default of 1200
        if max_tokens is not None:
            self.max_tokens = max_tokens
        elif hasattr(self.llm_config, 'max_tokens') and self.llm_config.max_tokens is not None:
            self.max_tokens = self.llm_config.max_tokens
        else:
            self.max_tokens = 1200
        self.max_web_searches = max_web_searches
        if system_prompt is not None:
            self.system_prompt = system_prompt
        else:
            self.system_prompt = self.llm_config.system_prompt or ""
        # Timeout (seconds) for Perplexity HTTP requests; configurable for slow networks
        self.perplexity_timeout = perplexity_timeout


    def forward(self, input: str, max_tokens: int = None, response_format = None, get_cost: bool = False):
        """Aligns with `OnePassLLM.forward` signature in `AIR/base_llm.py`.

        Args:
            input: User prompt string.
            max_tokens: Maximum tokens for the completion. If None, uses self.max_tokens.
            response_format: Structured response schema (not fully supported here).
            get_cost: If True, attempt to return (output, cost).

        Returns:
            output string or (output, cost) when get_cost is True.
        """
        # Use instance max_tokens if not explicitly provided
        if max_tokens is None:
            max_tokens = self.max_tokens

        self.logger.log_prompt("user", input, "base_llm_with_search_query")

        # Route to the appropriate backend
        result_text = ""
        response = None
        if self.backend == "anthropic":
            tools_param = [
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": self.max_web_searches,
                }
            ]

            api_params: Dict[str, Any] = {
                "model": self.model_name,
                "max_tokens": max_tokens,
                "system": self.system_prompt,
                "messages": [{"role": "user", "content": input}],
                "tools": tools_param,
            }

            try:
                response = self.client.messages.create(**api_params)

                # Extract textual parts from the response content blocks.
                text_parts: List[str] = []
                found_web_search = False
                for i, block in enumerate(getattr(response, "content", [])):
                    btype = getattr(block, "type", None)
                    if btype == "web_search_tool_result":
                        found_web_search = True
                        continue
                    if found_web_search and btype == "text" and hasattr(block, "text"):
                        text_parts.append(block.text)
                    elif not found_web_search and btype == "text" and hasattr(block, "text"):
                        text_parts.append(block.text)

                result_text = "\n".join(p for p in text_parts if p)

            except Exception as e:
                err = f"Error during Anthropic messages.create: {str(e)}"
                self.logger.log_error(err)
                raise

        else:  # perplexity/sonar
            # Build Perplexity request using a minimal chat completions POST.
            url = "https://api.perplexity.ai/chat/completions"
            payload = {
                "model": self.model_name,
                "messages": [{"role": "user", "content": input}],
            }
            headers = {
                "Authorization": f"Bearer {self.llm_config.api_key}",
                "Content-Type": "application/json",
            }

            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=self.perplexity_timeout)
                resp.raise_for_status()
                json_resp = resp.json()
                # Attempt to extract a sensible text field. Perplexity's
                # response shape may vary; try common keys.
                if isinstance(json_resp, dict):
                    if "choices" in json_resp and json_resp.get("choices"):
                        choice = json_resp["choices"][0]
                        msg = choice.get("message") or choice.get("text") or {}
                        if isinstance(msg, dict) and "content" in msg:
                            result_text = msg["content"]
                        elif isinstance(msg, str):
                            result_text = msg
                        else:
                            result_text = str(json_resp)
                    else:
                        result_text = str(json_resp)
                else:
                    result_text = str(json_resp)

            except Exception as e:
                err = f"Error during Perplexity Sonar request: {str(e)}"
                self.logger.log_error(err)
                raise

        # Log
        try:
            self.logger.log_response(result_text, "base_llm_with_search_result")
        except Exception:
            pass

        if get_cost:
            if self.backend == "anthropic":
                try:
                    cost = anthropic_api_calculate_cost(response, model=self.model_name)
                except Exception:
                    cost = 0.0
            else:
                cost = 0.0
            return result_text, cost
        else:
            return result_text


