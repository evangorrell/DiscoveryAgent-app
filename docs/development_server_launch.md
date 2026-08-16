# Running DiscoveryAgent from a local checkout

How to run the app against the code in your working tree, so edits to
`application/backend` or `application/frontend` actually take effect.

This is the **development** path. It skips the release artifact, the SHA
verification in `install.sh`, and the PATH/shell-rc integration — see
[Testing the real install path](#testing-the-real-install-path) for those.

Requirements: Apple Silicon macOS, and a checkout of this repository. The
installer brings its own uv and CPython; nothing else needs to be installed.

---

## Why you can't just edit the installed copy

`~/.local/share/discoveryagent/releases/<release-id>/application/backend` is
**not** what runs. The backend is installed into the release's virtualenv as a
non-editable package, so `import AIR` resolves from:

```
<install-root>/releases/<release-id>/envs/backend/lib/python3.11/site-packages/AIR
```

Edits to the `application/backend` tree next to it are ignored. Either rebuild
(below) or hot-patch site-packages directly.

---

## First install

Pick an install root — any absolute path outside the repository. It holds the
app, its runtime, and its own `discoveryagent` command; it does not touch your
PATH and does not disturb an existing `~/.local` install.

```bash
cd /path/to/DiscoveryAgent-app
git checkout <your-branch>          # the install copies your working tree

DEV=~/da-dev
python3 tools/bootstrap_development_install.py \
  --install-root "$DEV" \
  --data-root "$HOME/Library/Application Support/DiscoveryAgent"

"$DEV/bin/discoveryagent"
```

Takes ~30s the first time (it downloads uv and CPython 3.11.11), ~12s
afterwards. Launch behaves exactly like the production app: it prompts for
`ANTHROPIC_API_KEY` if it isn't in your environment, walks through Paperclip
login if needed, then opens the UI in your browser. `Ctrl+C` stops it.

### Choosing a data root

`--data-root` decides where projects, checkpoints, and logs live.

| Value | Effect |
| --- | --- |
| `~/Library/Application Support/DiscoveryAgent` | Shares projects and logs with the production install. Your existing projects are there, and logs land in the paths you already know. **Don't run both installs against it at once.** |
| Anything else (e.g. `~/da-dev-data`) | Fully isolated. Starts with no projects. |

The app code, virtualenvs, runtime, CLI shim, and `state.json` are always
per-install; only the data root can be shared.

---

## The edit → run loop

After changing anything under `application/`:

```bash
rm -rf "$DEV/releases"
python3 tools/bootstrap_development_install.py \
  --install-root "$DEV" \
  --data-root "$HOME/Library/Application Support/DiscoveryAgent"

"$DEV/bin/discoveryagent"
```

**The `rm -rf "$DEV/releases"` is required.** Re-running the bootstrap over an
existing release fails with `Installed application snapshot differs from
canonical files` — it byte-compares the installed snapshot against the
repository and refuses when they diverge. Clearing `releases/` keeps the
downloaded runtime, so the rebuild stays fast.

### Confirming you're running the patched build

`discoveryagent version` is **not** a reliable check: the release ID comes from
`release.json`, which doesn't change when you edit application code. Both
installs will report the same version while running different code. Grep the
package that actually gets imported instead:

```bash
grep -c <a-symbol-you-added> \
  "$DEV"/releases/*/envs/backend/lib/python3.11/site-packages/AIR/<path>.py
```

### Hot-patching a single file

To skip the rebuild while iterating on one Python file:

```bash
cp application/backend/AIR/utils/llm_diagnostics.py \
   "$DEV"/releases/*/envs/backend/lib/python3.11/site-packages/AIR/utils/
```

It takes effect on the next round launch. This leaves the install out of sync
with its snapshot — do a clean rebuild before trusting a result, and note that
the next bootstrap will refuse until you clear `releases/`.

---

## Where the logs are

Under `<data-root>/projects/<project-id>/checkpoint/`:

| File | Contents |
| --- | --- |
| `checkpoints/round_<n>/round.log` | Full trace: prompts, responses, errors |
| `checkpoints/round_<n>/live_round.log` | Same stream, written live during the round |
| `checkpoints/round_<n>/round_activity.jsonl` | Round-level events (start, early stopping, reports saved) |
| `checkpoints/round_<n>/round_report.md` | The round's output |
| `local_runner.log` | Frontend-side runner diagnostics |
| `ai_researcher.log` / `ai_researcher_actions.log` | Central agent log and its action-only view |
| `run_state.json` | Status, PID, exit code, resolved backend/python paths |

---

## Troubleshooting

**`Installed application snapshot is not byte-identical`**
Stray build artifacts in `application/` — usually `__pycache__` directories left
by running Python inside the tree. Remove them and retry:

```bash
find application -name __pycache__ -type d -exec rm -rf {} +
find application -name '*.pyc' -delete
```

**`Installed application snapshot differs from canonical files`**
You re-ran the bootstrap without clearing the old release. `rm -rf
"$DEV/releases"` and retry.

**Edits don't take effect**
You edited `releases/<id>/application/backend` instead of the repository, or
you rebuilt without clearing `releases/`. See
[Why you can't just edit the installed copy](#why-you-cant-just-edit-the-installed-copy).

**`--install-root must be an absolute path`**
Both `--install-root` and `--data-root` require absolute paths.

---

## Testing the real install path

The development install skips packaging. To exercise what an external user gets
— artifact build, checksum verification, `install.sh`, PATH integration:

```bash
python3 tools/build_install_artifact.py --output-dir "$PWD/dist"
mkdir -p /tmp/da-user && cp install.sh dist/discoveryagent-*.tar.gz /tmp/da-user/
cd /tmp/da-user && sh install.sh
```

`install.sh` prefers the artifact sitting next to it and verifies its SHA-256
against the value baked into the script, so this works offline. The build is
deterministic: an unmodified tree reproduces the committed checksum. If you
changed `application/`, the checksum won't match and you'll need to refresh the
snapshot and update `RELEASE_ID` / `PUBLIC_ARTIFACT_SHA256` in `install.sh`
(`tests/test_install_artifact.py` asserts they agree).

To rehearse that flow without touching your real `~/.local` and shell rc, run it
under a throwaway home: `HOME=/tmp/da-home sh install.sh`.

---

## Cleaning up

```bash
rm -rf "$DEV"
```

Removes the development install and its runtime. Project data under the data
root is untouched.
