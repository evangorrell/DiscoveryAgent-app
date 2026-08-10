"""
Simplified results memory manager for AIR.

This module provides a lightweight alternative to `results_memory.py` that
performs a single LLM call to merge an existing report with a new report.
It reuses the integrator agent but skips analyzer/refiner steps for speed.
"""

import os
from typing import Optional, Dict, Any

from ..config import LLMConfig
from .results_memory import IntegratorAgent


class SimpleMemoryManager:
    """
    Minimal memory manager that relies on a single integrator LLM call.
    """

    def __init__(self, llm_config: Optional[LLMConfig] = None):
        """
        Initialize the simple memory manager.

        Args:
            llm_config: Optional LLM configuration. Defaults to Claude Sonnet
                        with generous max tokens for long reports.
        """
        if llm_config is None:
            llm_config = LLMConfig(model_name="claude-sonnet-4-5-20250929", max_tokens=18000)

        self.llm_config = llm_config
        self.integrator = IntegratorAgent(llm_config)
        self.current_report: Optional[str] = None
        self.total_cost: float = 0.0
        self.update_count: int = 0

    def update_report(
        self,
        new_report: str,
        max_tokens: Optional[int] = None,
        verbose: bool = True
    ) -> str:
        """
        Merge the new report into the stored report via a single integrator call.

        If no report exists yet, the new report is stored as-is.
        """
        self.update_count += 1

        if self.current_report is None:
            if verbose:
                print(f"[SimpleMemoryManager] Storing initial report (update #{self.update_count})")
            self.current_report = new_report
            return self.current_report

        if verbose:
            print(f"\n[SimpleMemoryManager] Update #{self.update_count}")
            print(f"  - Existing report length: {len(self.current_report)} characters")
            print(f"  - New report length: {len(new_report)} characters")
            print("  - Combining reports via integrator (single LLM call)...")

        combined_report, cost = self.integrator.integrate(
            existing_report=self.current_report,
            new_report=new_report,
            max_tokens=max_tokens
        )

        self.current_report = combined_report
        self.total_cost += cost

        if verbose:
            print(f"  - Combined report length: {len(combined_report)} characters")
            print(f"  - Integration cost: ${cost:.4f}")
            print(f"  - Total cost so far: ${self.total_cost:.4f}\n")

        return self.current_report

    def get_report(self) -> Optional[str]:
        """Return the current stored report, if any."""
        return self.current_report

    def clear_report(self):
        """Clear stored report and reset counters."""
        self.current_report = None
        self.total_cost = 0.0
        self.update_count = 0

    def save_report(self, filepath: str):
        """Persist the current report to disk."""
        if self.current_report is None:
            raise ValueError("No report exists to save")

        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            f.write(self.current_report)

    def load_report(self, filepath: str):
        """Load a report from disk into memory."""
        with open(filepath, "r") as f:
            self.current_report = f.read()

    def get_statistics(self) -> Dict[str, Any]:
        """Return simple usage statistics."""
        return {
            "update_count": self.update_count,
            "total_cost": self.total_cost,
            "has_report": self.current_report is not None,
            "report_length": len(self.current_report) if self.current_report else 0,
            "model_name": self.llm_config.model_name,
        }

