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

Research prompt/response/code debugging is off by default. Set
`DISCOVERYAGENT_DEBUG_PROMPTS=1` deliberately to enable it. Operational events
remain logged. Project-owned chat history, reports, notebooks, and sanitized
execution activity are scientific records, not debug-log copies, and remain intact.

## Identity and update compatibility

The application ID remains `prod-{frontend_tree[:12]}-{upstream_backend_commit[:12]}`.
It never incorporates the metadata-bearing umbrella commit. The standalone
`owencqueen/discoveryagent` repository is the only canonical backend source.
`application/backend` is a generated snapshot, never a backend development tree.
Metadata records the selected upstream commit, Git tree and lockfile hash.

Production packages additionally require a positive `package_revision`. Increase
this **global release sequence** for every published package, including manager-only
and backend-only changes. Do not reuse a revision for different contents.
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
   Output must be new/empty and outside the source checkout. Backend files come
   only from the selected standalone Git commit, never umbrella backend edits.
   The generated backend must reproduce its upstream Git tree (content, inventory,
   executable modes and symlink targets) and lock hash; no dirty working files ship.
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

For a reviewed development snapshot refresh, use the same builder with
`--mode development --backend-only --app-sha APP_SHA --backend-repo BACKEND_CHECKOUT
--backend-sha APPROVED_BACKEND_SHA --output-dir UMBRELLA_CHECKOUT`.
This replaces only the generated backend and `release.json`, preserving the frontend
directory and local environment. It is not an artifact build; the backend-only
option is rejected in production mode. Commit the generated snapshot and metadata
together. Never cherry-pick independent changes into `application/backend`.

`verify_backend_snapshot` verifies a snapshot against the recorded upstream tree
and lock hash. Production package verification repeats this check independently
of its package file inventory. Provenance approval still relies on the builder's
immutable Git-source and origin/main checks, not on trusting arbitrary metadata.

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
