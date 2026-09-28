# Canonical backend hardening provenance

Audit date: 2026-09-28. This is a backend review record, not release approval.

## Source-of-truth boundary

`owencqueen/discoveryagent` owns backend development. The application repository
owns `application/frontend`, manager/installer code, and release tooling.
`DiscoveryAgent-app/application/backend` must be a generated snapshot of one
approved backend commit, not a second development source.

Both repositories were clean before this work. Both remotes were fetched.

- Umbrella baseline: `c8305208a829ab120dab8d58f41f86e908f47361`.
- Umbrella current main / hardening merge:
  `6b9b0b54daad019afadfda99bdb28f1e6d9d406b`.
- Merge parents: baseline above and hardening tip
  `6603098ee88a01fdfd3165eea8ac12e7c84ccf70`.
- No subsequent commits on umbrella main at audit time.
- Umbrella audit branch: `fix/persistent-audit-trail`, tip
  `79e213827739bcddd87bad634071020f283b54e8`; not merged.
- Backend remote: `https://github.com/owencqueen/discoveryagent`.
- Backend initial local branch: `v2-ui`, HEAD
  `bf3f7d5a9f61e0e8ef1dba2ddcdce4283f0d40fb`.
- Freshly fetched backend origin/main and patch baseline:
  `412b7bd78bc16c2d248402871b02691b7cceab43`.
- Existing umbrella `release.json` records that same backend commit and tree
  `265732ea82bb421de9ce10f0e31ea52be9c5b3f6`.
- All **190** baseline snapshot paths have exactly the same Git blob IDs and
  modes as backend origin/main; neither tree has extra paths. This is stronger
  evidence than comparing remembered changes or commit titles.
- Implementation branch in this backend repository: `release/public-hardening`,
  created after fast-forwarding the clean local main to origin/main.

## All merged hardening commits

| Umbrella commit | Purpose | Backend snapshot edited? |
| --- | --- | --- |
| `f0e15ec` | Local Host/Origin request boundary | No |
| `832c657` | Markdown sanitization and vendored browser sanitizers | No |
| `d0fa814` | Opaque project identities in Audit chat | No |
| `eab0289` | Audit provider errors without fabricated answers | No |
| `fcb6fb6` | Research-content terminal privacy | Yes: five Python files |
| `c987d39` | Runtime dependency updates, optional LightGBM | Yes: four files |
| `156a479` | Frontend checkout/ensurepip test fixtures | No |
| `bf457e0` | Manager ABI isolation and package revisions | No |
| `5d94341` | Anonymous installer and initial platform floor | No |
| `1703f3b` | Release provenance/artifact integrity | No direct snapshot edits; incorrect builder ownership override |
| `0d12654` | Hardening/publication documentation | No |
| `6603098` | Verified macOS 15 managed-bundle minimum | No |

## Exhaustive backend-snapshot classification

Paths below are relative to `DiscoveryAgent-app/application/backend/`.
Each distinct modification has one classification:

- A: same functional change already upstream.
- B: necessary backend-owned change missing upstream; recreate here.
- C: erroneous or application-only change; do not upstream.
- D: generated/provenance-only consequence of an actual upstream change.

No audited new modification qualifies as A. None qualifies as D either: even
the old lockfile was generated from edits made only in the umbrella, not from
an approved upstream change. Its regeneration is part of Category B below.

| Umbrella commit | Backend file / modification | Class | Reason and required action |
| --- | --- | --- | --- |
| `fcb6fb6` | `AIR/director_workflow.py` | B | Hide dataset/task/figure-description console previews; existing config/checkpoint/figure stores retain these data. Keep backend terminal policy. |
| `fcb6fb6` | `AIR/hyp_agents/hypothesis_gen_agent.py` | B | Remove judge console preview; replace the duplicate response logging introduced here with the linked durable judge records from `7b116fb`. |
| `fcb6fb6` | `AIR/memory/results_memory.py` | C | Debug-gating the only failure-text preview is not an adequate audit design. Do not recreate this intermediate gate; use the durable correction below. |
| `fcb6fb6` | `AIR/tool_agents/execution_agent_cc.py` | B | Quiet execution-query/image/message previews without removing existing per-query execution histories. |
| `fcb6fb6` | `AIR/utils/logger.py` | C | Early returns and detail gates discard file evidence. Never recreate them; replace with persistence/display separation below. |
| `c987d39` | `README.md` | B | Backend-owned installation guidance for its optional LightGBM extra. |
| `c987d39` | `pyproject.toml` | B | Backend runtime parser/HTTP dependency floors, Together compatibility, and optional OpenMP-dependent LightGBM. |
| `c987d39` | `tests/test_runtime_dependencies.py` | B | Offline backend imports, optional-dependency contract, mocked Together chat compatibility. |
| `c987d39` | `uv.lock` | B | Regenerate here from backend dependency requirements; do not copy the umbrella lock. |
| `7b116fb` | `AIR/hyp_agents/hypothesis_gen_agent.py` | B | Complete linked judge request/selection/response/fallback/error; structured prompts and OpenAI exchange metadata. |
| `7b116fb` | `AIR/hypothesis_brancher.py` | B | Wire the run logger into memory analysis before report updates. |
| `7b116fb` | `AIR/memory/results_memory.py` | B | Preserve the exact unparseable provider response and fallback in a scoped scientific record. |
| `7b116fb` | `AIR/tool_agents/execution_agent_cc.py` | B | Sanitize the direct terminal-output path that bypasses logging filters. |
| `7b116fb` | `AIR/tool_agents/execution_sdk_tracker.py` | B | Reuse centralized recursive redaction in canonical execution histories; do not duplicate SDK histories. |
| `7b116fb` | `AIR/utils/llm_diagnostics.py` | B | Persist allowlisted provider exchange/failure metadata; retain legacy diagnostic adapter behavior. |
| `7b116fb` | `AIR/utils/logger.py` | B | Persist before displaying, retain round context, filter credentials on ordinary logging paths. |
| `7b116fb` | `AIR/utils/scientific_audit.py`: durable writer, redaction, mixin | B | Backend scientific evidence requires these provider-independent facilities. |
| `7b116fb` | `AIR/utils/scientific_audit.py`: `ProjectAuditLogger` | C | Studio-specific file-only adapter has no backend caller; keep it frontend-owned. |
| `79e2138` | `tests/test_scientific_audit.py`: persistence/redaction/backend regressions | B | Port using backend fixtures with no dependency on the application checkout. |
| `79e2138` | `tests/test_scientific_audit.py`: `test_packaged_audit_implementations_match` | C | Packaging parity assumes sibling frontend/backend directories; belongs to umbrella integration testing, not standalone backend. |

The complete range changes nine snapshot files on main; the unmerged branch
adds/modifies nine more relative to main, for fourteen distinct paths overall.
The merge introduces no extra conflict-resolution backend edits.

## Backend implementation groups and validation

1. Dependency/runtime patch (source `c987d39`): `pyproject.toml`, independently
   regenerated `uv.lock`, LightGBM README note, and
   `tests/test_runtime_dependencies.py`. Backend owns its own environment.
   Exact tests: `test_default_scientific_pdf_notebook_and_sdk_imports`,
   `test_lightgbm_is_explicitly_optional`, and
   `test_together_chat_api_remains_compatible_with_patched_pillow`, plus
   `test_lock_retains_reviewed_runtime_dependency_floors`. Regeneration includes
   the original lock-only transitive updates, not just changed direct requirements.
2. Scientific audit/terminal patch (corrected combination of `fcb6fb6`,
   `7b116fb`, `79e2138`): the nine AIR paths listed above and standalone audit
   tests. Backend owns model decisions, workflow actions, memory analysis and
   execution histories. Validate all `tests/test_scientific_audit.py` cases,
   plus `test_llm_diagnostics.py`, `test_execution_live_log.py`,
   `test_refusal_workflow.py`, and the full non-live suite, including existing
   URL-blocker/sandbox, legacy-resume, query-artifact and report-summary tests.

No directory-wide backend copy is used. Individually reviewed deltas are
applied only after checking the upstream file against the original baseline.
Existing collaborator security/legacy-resume/provider functionality remains.

## Backend audit storage contract

The scientific JSONL lives in the configured run `log_dir`, next to existing
operational logs. For managed application runs that is the supplied project
checkpoint directory; for standalone runs it is normally
`DATA_PATH/LOGS/<dataset>/<analysis_name>_<timestamp>/`.

Persistence is independent of `DISCOVERYAGENT_DEBUG_PROMPTS`. The flag controls
additional sanitized terminal display only. Scientific records retain exact
returned judge text (not hidden model reasoning), structured request/status/error
metadata and scientific action details. Credentials and binary bodies are
redacted/omitted. Writes use append, locking and fsync; failures warn and raise.
Existing records are not rewritten; previously missing evidence is unrecoverable.

Standalone configured relative log directories are resolved before initializing
the absolute-path audit sink; storage inside application source remains rejected.
The normal brancher wires its run logger into memory analysis. A legacy direct
analyzer/recovery caller without that logger retains a sanitized diagnostic and
an explicit "not durably audited" warning, rather than silently losing the
failure or claiming it was stored. Backend tests cover both compatibility paths.

## Application-owned work not moved here

Host/Origin checks, browser Markdown sanitization, opaque project/chat IDs,
Audit/code-chat HTTP error handling, frontend Studio initialization/logging,
frontend dependencies/tests, installer/updater/ABI/platform policy, package
revision/manifest integrity, release tooling, and publication documentation.
The `eab0289` provider-error fix is in the frontend's Python service, not this
standalone scientific backend. The similarly named backend API diagnostics
from `7b116fb` do belong here.

## Required later umbrella correction (not performed here)

1. Review this backend branch and approve its tested commit. Merge to backend
   main only after separate authorization; refresh origin/main. If a squash or
   rebase changes the SHA, use the resulting approved main commit, not an old SHA.
2. On a separate umbrella correction branch, fix `release_builder/builder.py`:
   remove the `1703f3b` `canonical_backend` override and always extract the
   explicitly resolved backend repository commit using `archive_commit(backend,
   application / "backend")`. Drop its umbrella `canonical_tree`,
   `canonical_commit`, and `source_path` backend metadata additions. Retain the
   independent package revision, integrity, clean-source and approval gates.
3. Replace `test_canonical_backend_patches_keep_upstream_provenance` with tests
   proving an altered/stale umbrella snapshot cannot override explicit upstream
   source, metadata matches that source, and repeated snapshots are identical.
   Correct the ownership assertions in `docs/RELEASE_HARDENING.md`.
4. Use the corrected existing snapshot builder to regenerate from the approved
   frontend commit and approved backend commit, initially in a new staging
   destination. Review/commit its generated backend and metadata in the umbrella
   correction branch. Do not hand-merge individual backend files or use the
   currently incorrect builder to attempt restoration.
5. Verify every resulting backend path/blob/mode against the backend commit,
   lock/wheel hashes, frontend preservation, app integration, manager/release
   tests, and full source validation. The nine main-only and nine audit-branch
   file deltas must no longer exist as independent umbrella-only patches.

The invariant is equality to an explicit backend commit, not a zero historical
diff against `412b7bd`: legitimate fixes remain, with upstream ownership.

## Unmerged persistent-audit branch handling

Do not merge `fix/persistent-audit-trail` wholesale. From `7b116fb`, retain the
frontend hypothesis agent, logger, scientific-audit module, application routes,
Studio adapter and corrected technical documentation. From `79e2138`, retain
frontend audit-viewer/project-interaction/privacy/scientific-audit tests.
Its backend portions must arrive through the new upstream snapshot instead.
Adapt the app-side parity test to compare common backend/frontend contracts
without requiring a Studio-only adapter to exist in the backend. Correct the
documentation's source-of-truth language when integrating these app changes.

No umbrella source edits, snapshot replacement, merge, artifact build, or
release publication are performed by this backend patch series.

## Final validation in the standalone repository

Fresh isolated environment, CPython 3.11.11, regenerated lock, default + `dev`
extra. `AIR` imports were verified to resolve to this standalone checkout.
LightGBM is absent from that normal environment. The regenerated lock retains
the reviewed 351 package/version entries (including the PyPy-specific cffi fork);
resolver marker formatting differs, so byte identity is not claimed.

| Suite | Passed | Failed | Skipped / deselected |
| --- | ---: | ---: | --- |
| Full backend non-live | 209 | 0 | 0 skipped; 4 live tests deselected |
| Focused audit/runtime/provider/execution/security (subset) | 95 | 0 | 0 skipped; 4 live tests deselected |
| Python syntax | 138 regular files | 0 | 2 existing utility symlinks excluded |

`uv lock --check --offline`, `uv sync --check --locked --extra dev --offline`,
and `git diff --check` pass. The sync check would make no environment changes.
The existing sandbox, URL-blocker, config/legacy-resume and checkpoint source
files have no diff from backend origin/main.

Test execution removes provider credentials, disables pytest plugin autoload and
uses `PYTHON_DOTENV_DISABLED=1` to isolate the repository's local machine-specific
configuration. An earlier focused run without that setting failed collection on
a stale `/nfs/.../claude` setting in local configuration; no user configuration
was edited. Both final suites pass with configuration isolation.

The only excluded tests are the four paid `TestLiveEndToEnd` cases in
`tests/test_sandbox_enforcement.py`. No live provider or release acceptance is
claimed. The two pre-existing tracked utility symlinks point to absent
`internal_utils` targets and are not modified by this patch series.

Final run logs/XML on the validation machine:
`/private/tmp/discoveryagent-upstream-hardening.s9xy2n/review-full.*` and
`review-focused.*`. Focused counts overlap the full suite and must not be added.
