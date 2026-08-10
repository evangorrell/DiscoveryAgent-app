"""Shared types and interface for deep_research retrieval backends.

Keeping this module free of any SDK-specific imports lets DeepResearchAgent
import RetrievalResult/RetrievalStatus unconditionally, even when an optional
backend (e.g. the Paperclip SDK) is not installed.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class RetrievalStatus(enum.Enum):
    OK = "ok"
    EMPTY = "empty"
    ERROR = "error"


@dataclass
class RetrievalCitation:
    title: Optional[str] = None
    url: Optional[str] = None
    result_id: Optional[str] = None


@dataclass
class RetrievalResult:
    status: RetrievalStatus
    markdown: str = ""
    citations: List[RetrievalCitation] = field(default_factory=list)
    error_message: Optional[str] = None
    audit_metadata: Dict[str, Any] = field(default_factory=dict)


class RetrievalProvider:
    """Interface implemented by deep_research retrieval backends.

    Implementations must catch their own exceptions and return a
    RetrievalResult(status=RetrievalStatus.ERROR, ...) instead of raising,
    so DeepResearchAgent.execute() never needs to handle provider exceptions.
    """

    def deep_research(self, query: str) -> RetrievalResult:
        raise NotImplementedError("Subclasses must implement deep_research()")
