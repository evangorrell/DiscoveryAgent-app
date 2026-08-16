# Running DiscoveryAgent from a local checkout

How to run the app against the code in your working tree, so edits to
`application/backend` or `application/frontend` actually take effect.

There are two ways:

| | [Launch from source](#launch-the-server-from-your-working-tree) | [Development install](#development-install) |
| --- | --- | --- |
| Picks up an edit | On the next server start (frontend) or next round (backend) — no rebuild | Requires a ~12s rebuild |
| Runs | Your working tree, directly | A copy of it |
| Gives you | A server on a port you choose | A real `discoveryagent` command |
| Use it for | Day-to-day iteration | Verifying the install itself, or a clean end-to-end run |

Both need the virtualenvs the development install builds, so do that once
first. Requirements: Apple Silicon macOS and a checkout of this repository —
the installer brings its own uv and CPython.

---

## Why you can't just edit the installed copy

`<install-root>/releases/<release-id>/application/backend` is **not** what runs.
The backend is installed into the release's virtualenv as a non-editable
package, so `import AIR` normally resolves from:

```
<install-root>/releases/<release-id>/envs/backend/lib/python3.11/site-packages/AIR
```

Editing the `application/backend` tree next to it changes nothing. The two
supported ways to run your own code are below.

---

## One-time setup

Build a development install. It supplies the runtime and the two virtualenvs
(third-party dependencies); the code you run comes from your checkout.

```bash
cd /path/to/DiscoveryAgent-app
git checkout <your-branch>

DEV=~/da-dev            # any absolute path outside the repository
python3 tools/bootstrap_development_install.py \
  --install-root "$DEV" \
  --data-root "$HOME/Library/Application Support/DiscoveryAgent"
```

~30s the first time (it downloads uv and CPython 3.11.11). It does not touch
your PATH and does not disturb an existing `~/.local` install.

### Choosing a data root

`--data-root` decides where projects, checkpoints, and logs live.

| Value | Effect |
| --- | --- |
| `~/Library/Application Support/DiscoveryAgent` | Shares projects and logs with the production install. Your existing projects are there. **Don't run both installs against it at once.** |
| Anything else (e.g. `~/da-dev-data`) | Fully isolated. Starts with no projects. |

Everything else — app code, virtualenvs, runtime, CLI shim, `state.json` — is
always per-install.

---

## Launch the server from your working tree

This is the normal development loop:

```bash
cd /path/to/DiscoveryAgent-app
export ANTHROPIC_API_KEY=...           # required to start a round

tools/dev_server.sh                    # or: tools/dev_server.sh 9000
```

Then open <http://127.0.0.1:8765> — unlike `discoveryagent launch`, this does
not open a browser for you. `Ctrl+C` stops the server.

The script finds the development install at `~/da-dev` (override with
`DISCOVERYAGENT_DEV_ROOT`) and runs:

```bash
DISCOVERYAGENT_BACKEND_ROOT="$PWD/application/backend" \
DISCOVERYAGENT_BACKEND_PYTHON="$DEV/releases/*/envs/backend/bin/python" \
PYTHONPATH="$PWD/application/frontend" \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONNOUSERSITE=1 \
"$DEV"/releases/*/envs/frontend/bin/python -m da_selection_ui --port 8765
```

Run that directly if you want to vary something the script doesn't expose. What
each variable does:

| Variable | Effect |
| --- | --- |
| `PYTHONPATH=…/application/frontend` | Frontend code is imported from your checkout instead of the venv's copy |
| `DISCOVERYAGENT_BACKEND_ROOT=…/application/backend` | Rounds run your checkout's `scripts/run_hb_sherlock.py`, and the runner prepends this directory to the round's `PYTHONPATH`, so `AIR` also comes from your checkout |
| `DISCOVERYAGENT_BACKEND_PYTHON` | The interpreter whose site-packages supply anthropic, pandas, and the rest |
| `PYTHONDONTWRITEBYTECODE=1` | Keeps `__pycache__` out of `application/` — see the note below |
| `PYTHONNOUSERSITE=1` | Keeps `~/.local` packages out of the run, matching production |

`DISCOVERYAGENT_DATA_DIR` is not set: on macOS it already defaults to
`~/Library/Application Support/DiscoveryAgent`. Set it only to point somewhere
else.

**Don't let the run litter the tree.** Running Python from `application/`
writes `__pycache__` directories there, and both the development bootstrap and
the artifact build compare the tree byte-for-byte and refuse when it contains
them. `PYTHONDONTWRITEBYTECODE=1` (already in the script) prevents it; if you
launch by hand without it, clean up with the `find` command under
[Troubleshooting](#troubleshooting).

**When edits take effect:** frontend changes need a server restart. Backend
changes need nothing — each round is a fresh subprocess, so the next round you
start already runs the edited code. Nothing needs rebuilding either way.

Pick any free port; `--port 0` picks one for you and prints it in the
`DISCOVERYAGENT_FRONTEND_STARTUP=` line.

### Confirming you're running your working tree

```bash
DEV=~/da-dev; REL=$(echo "$DEV"/releases/*)
PYTHONPATH="$PWD/application/frontend" \
DISCOVERYAGENT_BACKEND_ROOT="$PWD/application/backend" \
DISCOVERYAGENT_BACKEND_PYTHON="$REL/envs/backend/bin/python" \
"$REL/envs/frontend/bin/python" -c "
import da_selection_ui
from da_selection_ui.local_round_runner import resolve_backend_runtime
print('frontend:', da_selection_ui.__file__)
print('backend :', resolve_backend_runtime()[0])
"
```

Both paths must point inside your checkout. If the frontend path lands in
`site-packages`, `PYTHONPATH` didn't reach the process.

---

## Development install

Use this when you want the packaged form — a real `discoveryagent` command
running an installed copy — rather than your working tree directly.

```bash
DEV=~/da-dev
rm -rf "$DEV/releases"
python3 tools/bootstrap_development_install.py \
  --install-root "$DEV" \
  --data-root "$HOME/Library/Application Support/DiscoveryAgent"

"$DEV/bin/discoveryagent"
```

Launch behaves exactly like the production app: it prompts for
`ANTHROPIC_API_KEY` if it isn't in your environment, walks through Paperclip
login if needed, then opens the UI in your browser.

**The `rm -rf "$DEV/releases"` is required.** Re-running the bootstrap over an
existing release fails with `Installed application snapshot differs from
canonical files` — it byte-compares the installed snapshot against the
repository and refuses when they diverge. Clearing `releases/` keeps the
downloaded runtime, so the rebuild is ~12s.

### Confirming the installed copy has your changes

`discoveryagent version` is **not** a reliable check: the release ID comes from
`release.json`, which doesn't change when you edit application code. Both
installs will report the same version while running different code. Grep the
package that actually gets imported:

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
with its snapshot, so the next bootstrap will refuse until you clear
`releases/`.

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

**Backend edits don't show up**
`DISCOVERYAGENT_BACKEND_ROOT` isn't pointing at your checkout, or you're running
`$DEV/bin/discoveryagent` (which uses the installed copy — rebuild it instead).

**`The DiscoveryAgent backend repository could not be found`**
`DISCOVERYAGENT_BACKEND_ROOT` is unset or wrong. It must be the directory
containing `scripts/run_hb_sherlock.py`.

**`The DiscoveryAgent backend Python environment could not be found`**
`DISCOVERYAGENT_BACKEND_PYTHON` is unset or wrong. Point it at the dev install's
`envs/backend/bin/python`; the repository has no virtualenv of its own.

**`Installed application snapshot is not byte-identical`**, or
**`Canonical application contains forbidden state: application/…/__pycache__`**
Stray build artifacts in `application/` — usually `__pycache__` directories left
by running Python inside the tree, or a `.venv` symlink. Both the bootstrap and
`build_install_artifact.py` reject them:

```bash
find application -name __pycache__ -type d -exec rm -rf {} +
find application -name '*.pyc' -delete
```

**`Installed application snapshot differs from canonical files`**
You re-ran the bootstrap without clearing the old release. `rm -rf
"$DEV/releases"` and retry.

**`--install-root must be an absolute path`**
Both `--install-root` and `--data-root` require absolute paths.

---

## Testing the real install path

Neither mode above exercises packaging. To run what an external user gets —
artifact build, checksum verification, `install.sh`, PATH integration:

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
