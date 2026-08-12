# DiscoveryAgent

DiscoveryAgent is a local application for autonomous scientific research.

## Install on Apple Silicon macOS

The public release URL is centralized in this command. Replace
`REPLACE_WITH_RELEASE_TAG` with the published immutable release tag when the
first release is cut:

```bash
curl -fsSL https://raw.githubusercontent.com/owencqueen/DiscoveryAgent-app/REPLACE_WITH_RELEASE_TAG/install.sh | sh \
  && export PATH="$HOME/.local/bin:$PATH" \
  && discoveryagent
```

DiscoveryAgent installs entirely for the current user. On first launch it
securely asks for an Anthropic API key if one is not already exported, then
opens Paperclip browser sign-in once if needed. Later, normal use is simply:

```bash
discoveryagent
```

The installed lifecycle commands are:

```bash
discoveryagent
discoveryagent launch
discoveryagent version
discoveryagent update
discoveryagent uninstall
```

Updates use verified immutable release artifacts and will not run while a
research round is active. Uninstall removes the application, manager, owned
runtimes, and recorded command while preserving projects under Application
Support and Paperclip credentials under `~/.paperclip`.

The terminal that owns the frontend can stop it with Ctrl-C. The installer
permanently configures future login shells when necessary; the inline `export`
above makes the command available immediately in the terminal running the
installation.

## Distribution architecture

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

The stable manager validates the selected release and its app-owned runtime,
launches or reuses the packaged frontend, and performs first-use Anthropic and
Paperclip onboarding before a new frontend starts. Anthropic keys inherited by
the command are used silently; securely prompted keys remain only in that
process tree. Paperclip continues to own its saved OAuth credentials under
`~/.paperclip`, so configured users do not need a recurring network check or
login on routine launches.

## Exercise the production installer engine

The maintainer-facing installer builds a complete local installation from only
the committed `application/`, `release.json`, and `manager/` payload:

```bash
python3 tools/install_local_release.py
```

On macOS its defaults are `~/.local/share/discoveryagent` for the application,
`~/.local/bin/discoveryagent` for the command, and
`~/Library/Application Support/DiscoveryAgent` for durable project data. Tests
can supply absolute `--install-root`, `--cli-path`, and `--data-root` paths.
Canonical installation configures future-shell PATH when needed; custom CLI
paths remain maintainer-only. Installation does not launch the frontend or
perform authentication.

Completed releases are immutable. Reinstalling the same release validates and
reuses it; installing a different release builds it beside prior releases and
publishes the new active release only after its environments, manager, and CLI
shim are complete.

## Test the distribution tooling

```bash
python3 -m unittest discover -s tests -v
```

The public shell bootstrap and `discoveryagent update` delegate installation to
the same production engine used by the local-release installer; development
bootstrap and manager-refresh tools remain maintainer-facing.

## Build the public install artifact

Build the deterministic macOS arm64 GitHub Release asset, checksum, and
machine-readable manifest with:

```bash
python3 tools/build_install_artifact.py --output-dir /absolute/output/directory
```

Build the deterministic stable update-channel manifest from that artifact
manifest without duplicating its hash:

```bash
python3 tools/build_update_manifest.py \
  --artifact-manifest /absolute/output/discoveryagent-<release>-macos-arm64.tar.gz.json \
  --release-tag <immutable-github-release-tag> \
  --artifact-url https://github.com/owencqueen/DiscoveryAgent-app/releases/download/<tag>/<artifact> \
  --output /absolute/output/discoveryagent-update.json
```

Before publication, set `RELEASE_TAG` in `install.sh`, publish the generated
archive under that immutable GitHub Release tag, verify its committed
`PUBLIC_ARTIFACT_SHA256`, and replace the matching README install tag. The shell
bootstrap requires only standard macOS utilities; it downloads its own pinned
uv and Python before handing off to the production installer engine.
