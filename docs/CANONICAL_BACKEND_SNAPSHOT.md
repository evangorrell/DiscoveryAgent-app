# Backend ownership correction — review record

## Approved backend source

The standalone repository is `/Users/egorr/Desktop/Stanford_AI_for_Science/discoveryagent`,
remote `https://github.com/owencqueen/discoveryagent`. Its main had not advanced
beyond `412b7bd78bc16c2d248402871b02691b7cceab43` when refreshed. The clean hardening
branch at `38f4f85db0aea93d8647c93a58b39a1e1c85ac40` was merged without conflicts
using a non-fast-forward merge and pushed to backend main.

**Approved backend commit:** `1e9d91683744c4a6d0d2d7ba7cfc3a428cc30e1f`.

- Backend tree: `92e1d94c5125236e948ca58a82fbc999dacd3231`.
- Snapshot inventory: 194 files/symlinks.
- Backend lock SHA-256:
  `860649ed6f709b9cf645c3d64ccd137dfa465acce20c1c249bd1e7e9226b8f31`.
- Full standalone non-live suite before merge: 209 passed, 0 failed, 4 live cases deselected.
- Full standalone non-live suite after merge: 209 passed, 0 failed, 4 live cases deselected.

Backend main now owns all backend hardening and audit behavior. No new backend
implementation was authored in the umbrella during this correction.

## Umbrella starting state and correction

Freshly fetched umbrella main remained
`6b9b0b54daad019afadfda99bdb28f1e6d9d406b`, with no later commits and a clean tree.
The nine direct snapshot differences from original approved backend `412b7bd`
were the previously audited changes:

| Source commit | Paths under `application/backend/` |
| --- | --- |
| `fcb6fb6` | `AIR/director_workflow.py`, `AIR/hyp_agents/hypothesis_gen_agent.py`, `AIR/memory/results_memory.py`, `AIR/tool_agents/execution_agent_cc.py`, `AIR/utils/logger.py` |
| `c987d39` | `README.md`, `pyproject.toml`, `tests/test_runtime_dependencies.py`, `uv.lock` |

`1703f3b` also introduced the incorrect `canonical_backend` branch in the release
builder and `test_canonical_backend_patches_keep_upstream_provenance`. Those are
replaced. The builder always archives the explicitly selected standalone backend
Git commit, ignoring old umbrella backend content and old umbrella backend
provenance. The metadata identifies the actual upstream commit/tree/lock hash,
with no umbrella `canonical_tree`, `canonical_commit`, or backend `source_path`.

Production clean-source, matching HEAD, refreshed origin/main reachability,
package revision, application identity, deterministic permissions, external empty
output, installer hash and package-inventory safeguards remain enforced.

`snapshot_git_tree` computes the exact Git tree from generated content, executable
modes and symlink targets. `verify_backend_snapshot` checks that tree and lock
hash during generation and again during production-package verification. Tests
reject stale content, extra/missing files, changed modes/symlinks, mismatched
metadata, and edits disguised by recomputing only the package inventory.

The development-only `--backend-only` option regenerates the backend and
`release.json` without replacing the frontend or its local `.venv`. It cannot
be used to bypass production preflight. Snapshot installation uses the existing
staging/replacement machinery; backend files are not hand-copied or hand-patched.

## Verification and persistent-audit split

Every generated backend path, Git blob and file mode was compared with
`git ls-tree -r` for the approved commit. Inventory, content, modes, tree and lock
hash match exactly, including existing dangling utility symlinks. The committed
backend subtree also equals the approved upstream tree. Repository-level tests
reject independent tracked or non-ignored untracked backend changes.

`fix/persistent-audit-trail` was not merged. Only its nine frontend files and
scientific-audit documentation were selectively retained; no backend portion
was reapplied after snapshot generation.

| Change | Owner | Final source |
| --- | --- | --- |
| Backend runtime requirements and regenerated lock | standalone backend | Approved backend commit, generated snapshot |
| Backend judge decisions, API/action details, memory failure records | standalone backend | Approved backend commit, generated snapshot |
| Backend audit writer, redaction, execution histories | standalone backend | Approved backend commit, generated snapshot |
| Studio audit adapter, helper calls, frontend judge records | umbrella frontend/app | `application/frontend/AIR` and `da_selection_ui/select_hypothesis.py` |
| Project interaction redaction and structured chat failures | umbrella frontend/app | `application/frontend/da_selection_ui/app.py` |
| Frontend audit/privacy/project-history regressions | umbrella frontend/app | Frontend tests |
| Backend/Studio behavioral contract | umbrella frontend/app | Isolated-process integration test using the actual snapshotted audit module |
| Old source-file parity assertion | discarded | Replaced with behavioral contract; separate packages may have different adapters |
| Lossy debug gates and independent umbrella backend patches | discarded | Corrected upstream implementation arrives only through regeneration |
| Builder assertion that umbrella patches are authoritative | discarded | Replaced with immutable upstream provenance checks |

Audit records remain local and default-on. Terminal details remain opt-in;
credential redaction occurs before persistence/display. Scientific judge responses
and API/action details are retained. Existing research data/history is untouched.
The app route change is only +16/-1 lines; no pre-existing app function was removed.

Two test assumptions surfaced during integration and were corrected without
changing runtime behavior: the frontend environment does not install backend
scientific dependencies, so the cross-package test loads the standalone audit
module directly; and the published installer pins an asset independently of
development `release.json`, so installer fixtures use the script's own identity.
The published `install.sh` is unchanged.

## Validation

| Suite | Passed | Failed | Skipped/deselected |
| --- | ---: | ---: | --- |
| Full frontend JavaScript | 392 | 0 | 0 |
| Full frontend Python | 1,304 | 0 | 2 skipped |
| Full generated-backend non-live | 209 | 0 | 4 live cases deselected |
| Full manager/installer/updater/release | 210 | 0 | 0 |
| Focused frontend security/audit (overlapping subset) | 95 | 0 | 0 |

The manager suite includes 23 builder tests and two repository-level snapshot
provenance tests. The frontend skips are the opt-in native panel test and a
case-sensitive-filesystem test on the local case-insensitive volume. The four
excluded backend cases are the paid SDK `TestLiveEndToEnd` sandbox tests.
Tests disable dotenv loading and remove provider keys; no live provider calls
are part of this validation. Scientific-audit subprocess tests assert the exact
loaded source path, persistent complete response text, metadata preservation,
credential redaction and quiet terminal behavior.

Both lock checks pass. Syntax validation covers 291 Python, 63 JavaScript and
20 shell files (374 total); two pre-existing backend utility symlinks are not
treated as regular Python files. Whole-branch whitespace checks pass.

Validation logs/XML on this machine are under
`/private/tmp/discoveryagent-ownership-merge.hJYDco/`.

## Release boundary

This branch is an umbrella correction for review, not an approved public release.
Only a development source snapshot was generated. Artifact-generation exercises
inside unit tests use isolated temporary fixtures, not public release output.
No license, broad README rewrite, public visibility change, GitHub Release, or
umbrella-main merge is included. The original audit branch remains unmerged.
