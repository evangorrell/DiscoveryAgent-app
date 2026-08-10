"""
AIR (AI Researcher) Package

A comprehensive AI research framework for automated data analysis, 
notebook generation, and report management.
"""

__version__ = "1.0.0"
__author__ = "AI Research Team"

# Expose only the configuration types used by da_selection_ui.
from .config import LLMConfig, HypothesisGenerationConfig, get_default_hypothesis_config

__all__ = [
    "LLMConfig",
    "HypothesisGenerationConfig",
    "get_default_hypothesis_config",
]
