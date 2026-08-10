# Function-Level Redundancy Analysis — AIR Package

Traced from `scripts/run_hb_sherlock.py`, `scripts/generate_background_report.py`, `scripts/recover_report.py`.
`AIR/OLD/` excluded from analysis.

---

## Unused Functions & Classes — Safe to Remove

| File | Symbol | Notes |
|------|--------|-------|
| `AIR/base_llm.py` | `BestofKAgent` | Never instantiated anywhere in the active codebase |
| `AIR/config.py` | `ContextManagerConfig` | Never instantiated |
| `AIR/air_workflow_modular.py` | `run_codeact_workflow()` | Legacy function, not called from any active path |
| `AIR/director_workflow.py` | `run_codeact_workflow()` | Same — duplicate legacy function in both workflow files |

---

## Minimal / Borderline Use — Review Before Removing

| File | Symbol | Notes |
|------|--------|-------|
| `AIR/base_llm.py` | `AnthropicCitationsLLM` | Only instantiated in `scripts/tests/hypothesis_with_citations.py` |
| `AIR/utils/git_version_control.py` | `GitVersionControl` | Only instantiated in specific optional branches, not on the main path |
| `AIR/tool_agents/base_llm_with_search.py` | `BaseLLMWithSearch` | Class is defined but not instantiated in any active workflow; appears kept for future use |

---

## Active Codebase is Otherwise Clean

All other functions and classes in the active modules are reachable:

- `AIR/base_llm.py` — all cost calculation functions, `OnePassLLM` ✓
- `AIR/config.py` — `LLMConfig`, `HypothesisGenerationConfig`, `ExecutionAgentConfig`, `AIRWorkflowConfig`, slurm/GPU helpers ✓
- `AIR/hypothesis_brancher.py` — `HypothesisBrancher` ✓
- `AIR/director_workflow.py` — `DirectorWorkflow`, `_generate_background_report()` ✓
- `AIR/hyp_agents/` — `DirectorAgent`, `HypothesisGenAgent` ✓
- `AIR/tool_agents/execution_agent_cc.py` — `ExecutionAgentCC` ✓
- `AIR/tool_agents/agent_directory.py` — `STRING_TO_AGENT_MAP` ✓
- `AIR/tool_agents/single_search.py`, `deep_research.py` — config + agent classes ✓
- `AIR/tool_agents/security/` — all hooks used in execution agents ✓
- `AIR/tool_agents/specialized/` — `ACSDataAgent`, `CensusDataAgent`, `OpenTargetsAgent` ✓
- `AIR/utils/` — `Logger`, `arg_parsers`, `config_loader`, `pdf_loader` ✓
- `AIR/audit/` — `CheckpointDiscovery`, `DataExtractor` ✓
- `AIR/memory/results_memory.py` — `ResultsMemoryManager` and sub-agents ✓
- `AIR/context_managers/` — `NotebookManager`, `NotebookManagerXML`, `ReportManager` ✓
- `AIR/prompts/` — all active prompt constants imported and used ✓
