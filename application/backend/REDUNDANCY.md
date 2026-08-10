# Redundancy Analysis — AIR Package

Traced from `scripts/run_hb_sherlock.py`, `scripts/generate_background_report.py`, and `scripts/recover_report.py`.

---

## AIR/ Files — Safe to Delete (~333 KB)

These are never imported by any reachable code path:

| File | Notes |
|------|-------|


| `AIR/prompts/execution_agent.py` | Old execution prompts; never imported |
| `AIR/prompts/claude_md_generator_command.py` | Doc generation utility; never called |
| `AIR/prompts/codebase_use.py` | Never imported |
| `AIR/tool_agents/execution_agent.py` | 45 KB legacy file; replaced by `execution_agent_cc.py` |
| `AIR/tool_agents/mcps.py` | Only referenced in OLD/test scripts |
| `AIR/utils/token_utils.py` | Only imported by the unused hypothesis agents above |

**Borderline** (check before deleting):

| File | Notes |
|------|-------|


---

## scripts/ — Safe to Delete

| File/Dir | Notes |
|----------|-------|
| `scripts/run_hb_sherlock_LEGACY.py` | Superseded by `run_hb_sherlock.py` |
| `scripts/run_air_sherlock.py` | Pre-HypothesisBrancher; superseded |
| `scripts/resume_mid_round.py` | Deprecated resume logic |

**Probably safe** (secondary/one-off tools — low priority):

`scripts/post_process.py`, `scripts/print_checkpoint.py`, `scripts/hypothesis_with_citations.py`

---

## Partially Used — Worth a Closer Look

| Module | Issue |
|--------|-------|
| `AIR/checkpoint_manager.py` | Only used in unit tests, not the main workflow |
| `AIR/tool_agents/execution_sdk_tracker.py` | Imported in `execution_agent_cc.py`; unclear if SDK tracking is active |
| `AIR/hyp_agents/__init__.py` | Exports `HypothesisAgentModular` which nothing imports |
| `AIR/utils/code_chat.py` | Only used by `scripts/code_chat_cli.py` (secondary tool) |

---

## Summary

| Category | Files | Est. Size |
|----------|-------|-----------|
| Unreachable AIR modules | ~15 | ~333 KB |
| Dead scripts/ files | ~3–6 | ~100 KB |
| **Total prunable** | **~20 files** | **~430 KB** |
