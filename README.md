# DiscoveryAgent

DiscoveryAgent is a local application for autonomous scientific research.

This is the distribution and release repository for DiscoveryAgent. The
standalone frontend and backend repositories remain the development sources of
truth. This repository records one deterministic, committed source pair under:

* `application/frontend/` — frontend Git snapshot
* `application/backend/` — backend Git snapshot
* `release.json` — deterministic source and integrity metadata
* `release_builder/` and `tools/build_release.py` — snapshot tooling

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

## Test the builder

```bash
python3 -m unittest discover -s tests -v
```

Installation, runtime environments, the public `discoveryagent` manager CLI,
launch/update/uninstall, and credential onboarding are intentionally deferred to
later phases.
