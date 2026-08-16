"""
Single-shot web search agent using Anthropic's web_search tool.

This module provides a minimal agent that performs a single web search round-trip
in one API call. It is intended for simple, one-off search queries.
"""

from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from pathlib import Path
from datetime import datetime

import anthropic

from ..config import LLMConfig
from ..utils.logger import Logger
from ..utils.llm_diagnostics import (
    format_api_exception,
    summarize_response_blocks as _summarize_response_blocks,
)
from .base import BaseAgent, AgentToolConfig
from .mcp_academic_utils import build_academic_mcp_servers

DEFAULT_SYSTEM_PROMPT = """
You are tasked with completing a simple web search for the user's query.
Your internal knowledge is limited, so please gather information from the web before reporting any findings or answering the user's query.
Use the available research tools to gather relevant information about the user's query. If Semantic Scholar or PubMed tools are available, prefer them. Otherwise, use the web_search tool. Then produce a concise, factual answer with source attributions when available.

IMPORTANT: You must use the web_search tool to gather information.
"""

DEFAULT_SINGLE_SEARCH_MAX_TOKENS = 16384
DEFAULT_REQUEST_TIMEOUT_SECONDS = 180.0


def _extract_text_from_anthropic_response(response: Any, debug: bool = False, only_web_search: bool = False) -> str:
    """
    Extract concatenated text blocks from an Anthropic response.
    
    Args:
        response: The Anthropic API response
        debug: If True, print debug information about response blocks
        only_web_search: If True, only include text after the first web search block
    """
    text_parts: List[str] = []
    found_web_search = False
    for i, block in enumerate(getattr(response, "content", [])):
        block_type = getattr(block, "type", None)
        if debug:
            print(f"BLOCK {i} type: {block_type}")
            if block_type == "text" and hasattr(block, "text"):
                print(f"  TEXT: {getattr(block, 'text')}")
        # Check for web_search tool use
        if only_web_search and not found_web_search:
            if getattr(block, "type", None) == "web_search_tool_result":
                found_web_search = True
            # if block_type == "tool_use" and getattr(block, "name", None) == "web_search":
            #     found_web_search = True
            continue  # Skip all blocks before and including the first web_search tool_use
        if only_web_search and not found_web_search:
            continue  # Still haven't found the web_search block
        if block_type == "text" and hasattr(block, "text"):
            text_parts.append(block.text)
    return "\n".join(part for part in text_parts if part)


def _format_anthropic_exception(
    exc: Exception,
    *,
    model_name: str,
    query: str,
    using_mcp: bool,
    include_traceback: bool = False,
) -> str:
    """Build an Anthropic SDK error string for user-facing output or logs."""
    return format_api_exception(
        exc,
        headline="Single web search failed.",
        context={
            "Model": model_name,
            "Query preview": query[:200],
            "Using MCP servers": "yes" if using_mcp else "no",
        },
        include_traceback=include_traceback,
    )


@dataclass
class SingleSearchConfig(AgentToolConfig):
    """Configuration for SingleSearchAgent."""
    llm_config: LLMConfig = None
    log_dir: str = "logs"
    max_tokens: int = DEFAULT_SINGLE_SEARCH_MAX_TOKENS
    max_web_searches: int = 3
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    only_web_search: bool = False # If True, only include text after the first web search block
    # MCP (native Claude plugins)
    mcp_servers: Optional[List[Dict[str, Any]]] = None
    use_academic_mcp: bool = False
    pubmed_server_path: Optional[str] = None
    semantic_scholar_api_key: Optional[str] = None
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS


class SingleSearchAgent(BaseAgent):
    """
    One-shot web search agent using Anthropic's web_search tool.

    This agent makes exactly one call to the Anthropic Messages API with the
    web_search tool enabled and returns the model's final text output.
    """

    def __init__(self, config: SingleSearchConfig, logger=None):
        self.config = config
        self.llm_config = config.llm_config

        if self.llm_config.api_provider != "anthropic":
            raise ValueError(
                "SingleSearchAgent requires an Anthropic model (e.g., 'claude-3-7-sonnet-20250219')."
            )
        if not self.llm_config.api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")

        self.client = anthropic.Anthropic(
            api_key=self.llm_config.api_key,
            timeout=self.config.request_timeout_seconds,
        )
        # Use passed logger or create a new one if not provided
        self.logger = logger if logger is not None else Logger("single_search", log_dir=config.log_dir)

    def execute(self, query: str, debug: bool = False, searches_dir: Optional[str] = None) -> str:
        """
        Perform a single web search for the given query and return the answer text.

        Args:
            query: The natural language query to search for.

        Returns:
            The assistant's textual answer after performing the web search.
        """
        user_message = {"role": "user", "content": query}
        messages: List[Dict[str, Any]] = [user_message]

        self.logger.log_prompt("user", query, "Single Web Search Query")

        tools_param = [
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": self.config.max_web_searches,
            }
        ]

        try:
            mcp_servers = self.config.mcp_servers
            if not mcp_servers and self.config.use_academic_mcp:
                mcp_servers = build_academic_mcp_servers(
                    pubmed_server_path=self.config.pubmed_server_path,
                    semantic_scholar_api_key=self.config.semantic_scholar_api_key,
                )

            self.logger.log_action(
                "single_search_request",
                (
                    f"model={self.llm_config.model_name}\n"
                    f"max_tokens={self.config.max_tokens}\n"
                    f"max_web_searches={self.config.max_web_searches}\n"
                    f"timeout_seconds={self.config.request_timeout_seconds}\n"
                    f"using_mcp_servers={bool(mcp_servers)}"
                ),
            )

            api_params: Dict[str, Any] = {
                "model": self.llm_config.model_name,
                "max_tokens": self.config.max_tokens,
                "system": self.config.system_prompt,
                "messages": messages,
                "tools": tools_param,
            }
            if mcp_servers:
                api_params["mcp_servers"] = mcp_servers
                api_params["betas"] = ["mcp-client-2025-04-04"]
                response = self.client.beta.messages.create(**api_params)
            else:
                response = self.client.messages.create(**api_params)

            only_web_search = self.config.only_web_search and not mcp_servers
            result_text = _extract_text_from_anthropic_response(
                response,
                only_web_search=only_web_search,
                debug=debug,
            )
            block_summary = _summarize_response_blocks(response)
            self.logger.log_action(
                "single_search_response",
                (
                    f"stop_reason={getattr(response, 'stop_reason', None)}\n"
                    f"content_blocks={block_summary}"
                ),
            )

            if not result_text.strip():
                result_text = (
                    "Single web search completed but returned no text.\n"
                    f"Model: {self.llm_config.model_name}\n"
                    f"Stop reason: {getattr(response, 'stop_reason', None)}\n"
                    f"Content blocks: {block_summary}\n"
                    "Check the interactive log for the full response metadata."
                )
                self.logger.log_error(result_text)
            # Log the full tool result to the centralized log
            try:
                self.logger.log_response(result_text, "single_search_result")
            except Exception:
                pass

            # Persist search output if a directory was provided
            try:
                if searches_dir:
                    p = Path(searches_dir)
                    p.mkdir(parents=True, exist_ok=True)
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    model_name = (self.llm_config.model_name or "anthropic").replace('/', '_')
                    filename = p / f"single_search_{model_name}_{ts}.md"
                    with open(filename, 'w', encoding='utf-8') as f:
                        f.write(f"# Single Search Output\n\n")
                        f.write(f"## Query\n{query}\n\n")
                        f.write(f"## Model\n{model_name}\n\n")
                        f.write(result_text)
            except Exception:
                try:
                    self.logger.log_error("Failed to write single search output to disk")
                except Exception:
                    pass

            return result_text
        except Exception as e:
            error_summary = _format_anthropic_exception(
                e,
                model_name=self.llm_config.model_name,
                query=query,
                using_mcp=bool(self.config.mcp_servers or self.config.use_academic_mcp),
            )
            error_detail = _format_anthropic_exception(
                e,
                model_name=self.llm_config.model_name,
                query=query,
                using_mcp=bool(self.config.mcp_servers or self.config.use_academic_mcp),
                include_traceback=True,
            )
            self.logger.log_error(error_detail)
            raise RuntimeError(error_summary) from e


def single_web_search(query: str,
                      model_name: str = "claude-3-7-sonnet-20250219",
                      log_dir: str = "logs",
                      max_tokens: int = DEFAULT_SINGLE_SEARCH_MAX_TOKENS,
                      max_web_searches: int = 3,
                      system_prompt: Optional[str] = None,
                      use_academic_mcp: bool = False,
                      pubmed_server_path: Optional[str] = None,
                      semantic_scholar_api_key: Optional[str] = None) -> str:
    """
    Convenience function to perform a single web search using Anthropic's tool.

    Requires environment variable ANTHROPIC_API_KEY to be set.
    If use_academic_mcp is True, set PUBMED_MCP_SERVER_PATH and (optionally)
    SEMANTIC_SCHOLAR_API_KEY.
    """
    llm_config = LLMConfig(model_name=model_name)
    config = SingleSearchConfig(
        llm_config=llm_config,
        log_dir=log_dir,
        max_tokens=max_tokens,
        max_web_searches=max_web_searches,
        system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
        use_academic_mcp=use_academic_mcp,
        pubmed_server_path=pubmed_server_path,
        semantic_scholar_api_key=semantic_scholar_api_key,
    )
    agent = SingleSearchAgent(config)
    return agent.execute(query)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Single-shot web search via Anthropic web_search tool")
    parser.add_argument("query", type=str, help="Search query")
    parser.add_argument("--model", dest="model_name", type=str, default="claude-3-7-sonnet-20250219",
                        help="Anthropic model name")
    parser.add_argument("--log-dir", dest="log_dir", type=str, default="logs", help="Log directory")
    parser.add_argument("--max-tokens", dest="max_tokens", type=int, default=1200, help="Max tokens")
    parser.add_argument("--max-searches", dest="max_web_searches", type=int, default=3,
                        help="Maximum tool invocations allowed")
    args = parser.parse_args()

    text = single_web_search(
        query=args.query,
        model_name=args.model_name,
        log_dir=args.log_dir,
        max_tokens=args.max_tokens,
        max_web_searches=args.max_web_searches,
    )
    # Print to stdout for CLI use and also log the output
    try:
        Logger("single_search_cli", log_dir=args.log_dir).log_response(text, "single_search_cli")
    except Exception:
        pass
    print(text)
