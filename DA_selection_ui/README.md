# DA Selection UI Package

This directory contains the local DiscoveryAgent application. It bundles:
- `da_selection_ui/`: Flask app, static assets, templates, and interactive backend.
- `AIR/`: Required research agent code (config, prompts, agents, utils).
- `pyproject.toml`: Dependency definition (original AIR stack + Flask requirements).

## Install
```bash
cd DA_selection_ui
pip install -e .
mkdir -p .flask_session  # optional local session store
```
### Install with uv in an isolated venv
Use `uv` to create an isolated local environment:
```bash
cd DA_selection_ui
uv venv .venv               # create local venv
source .venv/bin/activate   # or .venv\\Scripts\\activate on Windows
uv pip install -e .         # install the package + deps into the venv
mkdir -p .flask_session
```
Python 3.11 is required. Configure the Anthropic API key from Settings after launching the application; it is stored in the operating system credential manager.

## Run
```bash
python -m da_selection_ui.app --port 8000
# or: python da_selection_ui/app.py --port 8000
# add --dev to enable the developer UI (advanced controls); default is user-facing simplified UI
# python -m da_selection_ui.app --port 8000 --dev
```

Open `http://localhost:8000/`. The root redirects to the Projects screen.

## Local workflow

1. Install DiscoveryAgent locally with `pip install -e .` or `uv pip install -e .`.
2. Launch the local Flask application.
3. Open Settings → Model provider and save the Anthropic API key in your operating system credential manager.
4. Create or open a project whose inputs and outputs live on this computer.
5. Run DiscoveryAgent locally from Hypothesis Studio.
6. View rounds, reports, checkpoints, figures, and other outputs in the frontend.

## Outputs
- Selected hypothesis: `<checkpoint_dir>/selected_hypothesis.md`
- Session log: `<checkpoint_dir>/interactive_hyp_gen/session_<timestamp>.json`

For detailed UI workflow and API references, see `da_selection_ui/README.md`.
