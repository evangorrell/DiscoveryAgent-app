"""
AIR (AI Researcher) Package

A modular AI research framework for automated hypothesis generation,
data analysis, and report generation.
"""

__version__ = "0.1.0"

from .context_managers import NotebookManager, NotebookManagerXML, ReportManager
from .utils.logger import Logger
from .config import (
    LLMConfig,
    HypothesisGenerationConfig,
    ExecutionAgentConfig,
    get_default_hypothesis_config,
    get_default_execution_config,
)

__all__ = [
    "NotebookManager",
    "NotebookManagerXML",
    "ReportManager",
    "Logger",
    "LLMConfig",
    "HypothesisGenerationConfig",
    "ExecutionAgentConfig",
    "get_default_hypothesis_config",
    "get_default_execution_config",
]