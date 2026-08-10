# DiscoveryAgent

DiscoveryAgent is a local application for autonomous scientific research.

This is the distribution and release repository for DiscoveryAgent. The
standalone frontend and backend repositories remain the development sources of
truth. This repository records one deterministic, committed source pair under:

* `application/frontend/` — frontend Git snapshot
* `application/backend/` — backend Git snapshot
* `release.json` — deterministic source and integrity metadata
* `release_builder/` and `tools/build_release.py` — snapshot tooling
* `manager/discoveryagent_cli/` — standard-library installed-application manager

## Build a development snapshot

The builder reads Git objects, never repository working trees. Both selected
commits must exist locally and be reachable from an already-known `origin/*`
remote-tracking ref.

```bash
python3 tools/build_release.py \
  --mode development \
  --frontend-repo /path/to/DA_selection_ui \
  --frontend-sha <commit> \
  --backend-repo /path/to/discoveryagent \
  --backend-sha <commit>
```

Production mode applies the additional guard that each selected commit must be
an ancestor of its repository's already-known `refs/remotes/origin/main`:

```bash
python3 tools/build_release.py --mode production ...
```

The builder does not fetch, checkout, switch branches, stash, install
dependencies, or modify either source repository.

## Manager foundation

The manager is intentionally separate from both application snapshots. At
runtime it derives an explicit installation root containing `state.json`,
`manager/`, `runtime/`, `releases/`, and `cache/`. `state.json` selects a
versioned release; the manager never uses a `current` symlink or falls back to
the development repository's top-level `application/` tree.

Phase 2A.1 implements schema validation, active-release resolution,
platform-aware frontend/backend interpreter paths, metadata-only version
reporting, and the complete pre-launch filesystem validation contract. It does
not install runtimes or start the frontend. Tests use an internal fixture
builder to create synthetic installed layouts in temporary directories.

## Test the distribution tooling

```bash
python3 -m unittest discover -s tests -v
```

Runtime/bootstrap installation, actual frontend launch, update/uninstall, PATH
integration, and credential onboarding are intentionally deferred to later
phases. The manager command semantics in this repository are maintainer-facing
until those installation phases are complete.
