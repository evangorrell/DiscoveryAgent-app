# DA Selection UI Package

This directory contains the local DiscoveryAgent application. It bundles:
- `da_selection_ui/`: Flask app, static assets, templates, and interactive backend.
- `AIR/`: Required research agent code (config, prompts, agents, utils).
- `pyproject.toml`: Dependency definition (original AIR stack + Flask requirements).

## Install
```bash
cd DA_selection_ui
pip install -e .
```
### Install with uv in an isolated venv
Use `uv` to create an isolated local environment:
```bash
cd DA_selection_ui
uv venv .venv               # create local venv
source .venv/bin/activate   # or .venv\\Scripts\\activate on Windows
uv pip install -e .         # install the package + deps into the venv
```
Python 3.11 is required. Round execution reads `ANTHROPIC_API_KEY` from the
environment that launches DiscoveryAgent. Set or change it before starting the
application; if DiscoveryAgent is already running, restart it so the new value
is inherited.

## Run

Production local entry point (the future application manager uses the exact
frontend interpreter rather than relying on activation):

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
<frontend-python> -m da_selection_ui
```

The default URL is `http://127.0.0.1:8000/`; the process does not open a
browser. Readiness is available at `http://127.0.0.1:8000/api/health`.

PowerShell uses the same module contract:

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
<frontend-python> -m da_selection_ui
```

For the packaged public launcher, the equivalent command will be:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
discoveryagent launch
```

Do not put the key in project configuration, browser storage, command-line
arguments, or project files.

The explicit development launcher retains debug mode, browser opening, and
the legacy initialization flags:

```bash
python -m da_selection_ui.app --port 8000 --dev
```

Open `http://localhost:8000/`. The root redirects to the Projects screen.

## Local workflow

1. Install DiscoveryAgent locally with `pip install -e .` or `uv pip install -e .`.
2. Set `ANTHROPIC_API_KEY` in the environment that will launch DiscoveryAgent.
3. Launch or restart the local Flask application so it inherits the environment.
4. Create or open a project whose inputs and outputs live on this computer.
5. Run DiscoveryAgent locally from Hypothesis Studio.
6. View rounds, reports, checkpoints, figures, and other outputs in the frontend.

## Outputs
- Selected hypothesis: `<checkpoint_dir>/selected_hypothesis.md`
- Session log: `<checkpoint_dir>/interactive_hyp_gen/session_<timestamp>.json`

For detailed UI workflow and API references, see `da_selection_ui/README.md`.
