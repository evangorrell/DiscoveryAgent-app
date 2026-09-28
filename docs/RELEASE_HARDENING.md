# Technical release-hardening contract

## Runtime and privacy

Apple Silicon macOS **15 or newer** is required by the current default bundle.
Actual Mach-O load-command inspection found: Python/uv target 11, the picker and
bundled Claude executable target 13, and bundled Pandoc/debugpy attachment library
target **15**. Pandoc's
wheel metadata alone understates that requirement, so the installer uses the real
binary floor. NumPy/SciPy have compatible macOS 11/12 and optimized macOS 14 builds.
Supporting 13/14 would require a separately validated compatible Pandoc build;
it must not be claimed from wheel tags alone. Clean-machine macOS 15 acceptance
remains a final-artifact check, not a claim established by unit tests.

LightGBM is not referenced by product code and is no longer a default dependency.
Its optional `lightgbm` extra requires separately supplied OpenMP on macOS.
Normal scientific, PDF, notebook and SDK imports are checked during installation.
Managed environments with a mismatched Python version or CPython extension ABI
are rebuilt inside their release's `envs` directory. A failed sync restores the
previous environment. Scientific projects and custom data roots are not rebuilt.

### Scientific audit storage versus terminal display

Scientific audit details are retained locally **by default**, independently of
`DISCOVERYAGENT_DEBUG_PROMPTS`. That flag controls verbose developer display only;
normal output remains operational summaries. Turning it off never disables audit
persistence. This corrects the earlier privacy gate in commit `fcb6fb6`, which
returned before file logging and unintentionally discarded audit evidence.

`AIR/utils/scientific_audit.py` provides the shared redactor and append-only JSONL
adapter (identical packaged copies in frontend/backend, checked by tests). Storage:

- Backend: `<project>/checkpoint/scientific_audit.jsonl`, using the existing
  managed runner's absolute `log_dir`. Records carry agent, UTC timestamp, run/event
  IDs, append offset, and round/query context as the workflow supplies it.
- Studio: `<checkpoint_dir>/interactive_generation_logs/scientific_audit.jsonl`.
  Each generator owns its sink; simultaneous projects cannot redirect each other's
  scientific records through a process-global logger.
- Audit chats: existing `<project>/.discoveryagent/interactions/audit_interactions.jsonl`.
  New interaction payloads are redacted; provider failures are explicit events,
  not assistant answers.
- Execution: existing per-query `execution_agent/messages.jsonl`, `tool_calls.jsonl`,
  `transcript.txt`, and public execution log. These are reused, not copied into the
  scientific audit sidecar. They use the same credential redactor.

The progress stream (`round_activity.jsonl`) remains a UI state contract, not a
full scientific trace. The interaction stream is HTTP/project scoped and unavailable
to standalone backend agents; the additive checkpoint sidecar fills that gap without
changing either existing schema. Loggers require an explicit absolute data directory
and reject source-checkout locations; there is no cwd/global/temp fallback. Tests use
isolated temporary projects, never the user's project directories.

| Reviewed information | Classification and treatment |
| --- | --- |
| Judge prompt/candidates, provider/model, complete returned justification and selection | Must persist: linked request/decision/failure events; evaluation ID joins them. No invented/private chain-of-thought. |
| Prompt/response/action detail suppressed by `DEBUG_PROMPTS` | Must persist: structured audit events, irrespective of display verbosity. |
| Successful API diagnostics and provider failures | Must persist: allowlisted settings, model, source, status, usage/request IDs and error metadata; retry/fallback events retained. Raw SDK objects are not dumped. |
| Studio one-shot helper responses outside conversation history | Must persist: explicit request/response/exchange events. |
| Memory analyzer's unparseable response | Must persist: complete returned text and the empty-redundancy fallback in the workflow's scoped audit. |
| Dataset/task previews, image-description previews | Already represented by project/config/checkpoints and figure metadata; no duplicate preview record. |
| Execution SDK display strings | Already represented by sanitized SDK histories; the display gate remains. |
| `debug_print_prompts` generator attribute | Compatibility/display setting; not a persistence gate. |
| Raw client objects, terminal controls, binary/base64 bodies | Developer noise or existing artifacts: omit objects; binary metadata/hash replaces embedded bytes. Retain document titles/references. |
| Authentication material | Never intentionally retained: redact before persistence or verbose display. |

Redaction covers nested API keys, authorization/bearer tokens, OAuth access/refresh
tokens, passwords, cookies/session secrets, private keys/auth blobs, credential URL
userinfo/query assignments, and known credential values from the environment (including
copies echoed elsewhere in a payload). Token counts, scientific prompts/queries,
citations, parameters, and substantive returned justifications are preserved. Unknown
SDK objects are represented only by type, not repr/auth-bearing object dumps.

New scientific audit files use mode 0600, locked append plus fsync, and ordered byte
offsets across restarts/concurrent writers. There is no automatic rotation/deletion
of these scientific records. Growth is linear in recorded text/actions; binary bodies
and existing execution histories are not duplicated. Full prompts/answers can contain
sensitive research: protect/backup the project directory accordingly. A write failure
raises and emits a credential-free warning that the operation is not fully audited;
it must not be mistaken for a successful audit write.

Existing reports, notebooks, figures, saved chats/conversations, and round memory
are neither replaced nor migrated. Existing records are not retrospectively scrubbed
or deleted. Older projects do not need the new sidecar to load. Evidence never saved
by an earlier run cannot be reconstructed by this correction. This is a local audit
trail, not a cryptographically tamper-evident archive or a guarantee of identical
future provider/model output.

## Identity and update compatibility

The application ID remains `prod-{frontend_tree[:12]}-{upstream_backend_commit[:12]}`.
It never incorporates the metadata-bearing umbrella commit. Reviewed patches to
the canonical backend are retained; metadata records both its upstream provenance
and the exact canonical backend tree/commit.

Production packages additionally require a positive `package_revision`. Increase
this **global release sequence** for every published package, including manager-only
and canonical-backend-only changes. Do not reuse a revision for different contents.
Install slots and archive names include `-pkgN`; the application ID does not.
Identical app/revision is up to date; a greater revision offers an update; a lower
revision is rejected. New application identities update normally. Missing revisions
in legacy metadata are read as zero.

Pre-hardening beta managers reject unknown update-manifest fields. Those existing
beta users need a one-time installation using the new release's `install.sh` (or
its colocated artifact). Thereafter `discoveryagent update` understands package-only
updates. The stable endpoint follows public stable releases, not drafts/prereleases.

## Production build procedure (after review and merge approval)

1. Use clean app and backend checkouts whose HEADs match the requested commits.
   Production preflight fetches origin, then requires both approved commits to be
   reachable from refreshed `origin/main`. This hardening branch is not approved
   for production merely because its tests pass.
2. Run `tools/build_release.py --mode production --app-sha APP_SHA
   --backend-repo BACKEND_CHECKOUT --backend-sha UPSTREAM_BACKEND_SHA
   --package-revision N --output-dir EMPTY_EXTERNAL_DIRECTORY`.
   Output must be new/empty and outside the source checkout. The canonical backend
   provenance must match the selected upstream commit; no dirty working files ship.
3. Run `tools/build_install_artifact.py --source-dir SNAPSHOT_DIRECTORY
   --output-dir ASSET_DIRECTORY --release-tag TAG`.
   This verifies the source inventory, app identity, lock/wheel hashes and installer
   template. It emits an archive, checksum, internal artifact manifest, and an
   `install.sh` bound to that exact filename/checksum/tag. Do not upload the old
   development-pinned installer directly from the repository.
4. Generate `discoveryagent-update.json` with `tools/build_update_manifest.py`,
   using the emitted artifact manifest and immutable GitHub release URL.
5. Perform clean-machine and upgrade acceptance before publication. This pass
   neither builds a final public release nor publishes/merges anything.

Archive modes normalize to 0755 executable/directories and 0644 ordinary files.
The package inventory excludes release metadata itself, so there is no circular
hash. It covers application files, manager, and installer entry point and is checked
again after extraction. Public downloads use HTTPS and verify SHA-256; colocated
artifacts remain supported, with authenticated fallback for private releases.

## Validation

Run frontend tests with the frontend `test` extra and backend tests with the
backend `dev` extra, using pinned Python 3.11.11. Runtime installation uses neither.
From the repository root:

```sh
node --test application/frontend/tests/*.test.js
PYTHONPATH=application/frontend FRONTEND_PYTHON -m pytest application/frontend/tests
PYTHONPATH=application/backend BACKEND_PYTHON -m pytest application/backend/tests -k 'not TestLiveEndToEnd'
PINNED_PYTHON -B -m unittest discover -s tests -p 'test_*.py'
```

The four excluded tests are paid/live SDK sandbox tests in
`test_sandbox_enforcement.py::TestLiveEndToEnd`:
`test_out_of_sandbox_write_is_blocked_end_to_end_with_tracker_present`,
`test_in_sandbox_write_still_succeeds`,
`test_edit_of_pre_existing_outside_sandbox_file_is_rejected_and_unchanged`, and
`test_relative_escape_write_is_blocked_end_to_end`.
No real provider response is represented as validated by the offline tests.
