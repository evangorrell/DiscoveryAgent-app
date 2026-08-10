# DA Selection UI

A local Flask application for creating DiscoveryAgent projects, developing hypotheses, running research, and reviewing local outputs.

## What's Included
- Full hypothesis selection web UI (templates and static assets).
- Backend API powered by `InteractiveHypothesisGenerator` from `select_hypothesis.py`.
- Bundled `AIR` package (config, prompts, agents, utilities) so the UI can run without the rest of the discoveryagent repo.
- Command-line entry point to start the Flask server.

## Installation

```bash
cd DA_selection_ui
pip install -e .
# Optional: keep Flask session files local
mkdir -p .flask_session
```

Requires Python 3.11. Set `ANTHROPIC_API_KEY` in the environment that launches
DiscoveryAgent. Restart the application after changing it so the new value is
inherited.

## Usage

Start the server (module mode preferred for package-relative imports):
```bash
cd DA_selection_ui
python -m da_selection_ui.app --port 8000
# add --dev to enable advanced/developer controls; default is user-facing simplified UI
# python -m da_selection_ui.app --port 8000 --dev
```
or equivalently:
```bash
python da_selection_ui/app.py --port 8000
```

Open `http://localhost:8000/` (or your chosen local port). The root redirects to Projects. Create or open a local project, use Hypothesis Studio, and review local results in Round Workspace. Outputs remain in the project directories on this computer.

## File Structure

```
DA_selection_ui/
├── pyproject.toml          # Package/dependency definition (includes Flask + AIR deps)
├── AIR/                    # Bundled AIR research agent code
└── da_selection_ui/
    ├── app.py              # Flask backend + API endpoints
    ├── select_hypothesis.py# InteractiveHypothesisGenerator backend
    ├── static/             # JS/CSS assets
    └── templates/          # HTML templates
```

## API Endpoints
- `POST /api/initialize` - Initialize session with checkpoint directory
- `POST /api/generate` - Generate candidate hypotheses
- `POST /api/ask` - Ask agent a question (Q&A mode)
- `POST /api/edit` - Edit a hypothesis with feedback
- `POST /api/regenerate` - Regenerate a hypothesis with feedback
- `POST /api/get_report` - Get background or round report
- `GET /api/get_qa_history` - Get Q&A conversation history
- `POST /api/select` - Save selected hypothesis

## Session Management
- Each browser session gets a unique session ID.
- Session state stored in memory (for production, switch Flask-Session to Redis/DB).
- Session logs saved under `<checkpoint_dir>/interactive_hyp_gen/session_<timestamp>.json`.

## Notes
- Background assets: background report images are served from `<checkpoint_dir>/images`.
- Code chat: available when `code_dir` is provided (uses `AIR.utils.code_chat`).
- Local project inputs, checkpoints, figures, reports, and generated outputs remain on this computer.
