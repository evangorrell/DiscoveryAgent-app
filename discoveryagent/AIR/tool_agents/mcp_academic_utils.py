"""
Utilities for Academic MCP servers (Semantic Scholar + PubMed).

These are intended for Claude's native MCP client via Anthropic's `mcp_servers`.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

SEMANTIC_SCHOLAR_MCP_BASE: Dict[str, object] = {
    "name": "semantic-scholar-mcp",
    "type": "stdio",
    "command": "uv",
    "args": [
        "run",
        "--with",
        "git+https://github.com/FujishigeTemma/semantic-scholar-mcp",
        "semantic-scholar-mcp",
        "serve",
    ],
}

PUBMED_MCP_BASE: Dict[str, object] = {
    "name": "pubmed-mcp",
    "type": "stdio",
    "command": "python",
}


def build_academic_mcp_servers(
    *,
    pubmed_server_path: Optional[str] = None,
    semantic_scholar_api_key: Optional[str] = None,
    include_semantic_scholar: bool = True,
    include_pubmed: bool = True,
) -> List[Dict[str, object]]:
    """Return MCP server configs for Semantic Scholar + PubMed (stdio)."""
    servers: List[Dict[str, object]] = []

    if include_semantic_scholar:
        sem_env: Dict[str, str] = {}
        api_key = semantic_scholar_api_key or os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
        if api_key:
            sem_env["SEMANTIC_SCHOLAR_API_KEY"] = api_key
        sem_cfg = dict(SEMANTIC_SCHOLAR_MCP_BASE)
        if sem_env:
            sem_cfg["env"] = sem_env
        servers.append(sem_cfg)

    if include_pubmed:
        pubmed_path = pubmed_server_path or os.getenv("PUBMED_MCP_SERVER_PATH")
        if not pubmed_path:
            raise ValueError(
                "PUBMED_MCP_SERVER_PATH is not set; provide pubmed_server_path or set the env var."
            )
        pub_cfg = dict(PUBMED_MCP_BASE)
        pub_cfg["args"] = [pubmed_path]
        servers.append(pub_cfg)

    return servers
