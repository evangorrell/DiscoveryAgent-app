# DiscoveryAgent Frontend

This repository contains the local web interface for DiscoveryAgent. It provides the workspace for creating research projects, developing hypotheses, following autonomous research rounds, reviewing results, importing execution-agent skills, and auditing project state.

Autonomous research execution is provided by the separate [`discoveryagent`](https://github.com/owencqueen/discoveryagent) backend repository.

## What the frontend provides

- **Projects** — create, reopen, edit, and delete local research projects and manage their context and data.
- **Project Home** — see the current research understanding, latest findings, how the research has evolved, and what needs attention next.
- **Hypothesis Studio** — generate, discuss, revise, compare, and select hypotheses after the initial exploratory round.
- **Round Workspace** — follow exact research rounds, inspect query activity and artifacts, review reports and figures, and use round-scoped chat.
- **Skills** — import project-specific instructions that are made available to the execution agent.
- **Audit Agent** — conversationally investigate project research, code, checkpoints, and supporting context.

The frontend can manage projects and browse existing research artifacts on its own. Starting, resuming, or resubmitting autonomous research rounds requires a local checkout of the DiscoveryAgent backend.

## Requirements

- Python 3.11
- [uv](https://docs.astral.sh/uv/) for Python environment and dependency management

Node.js is only required to run the frontend JavaScript tests.

## Setup

A convenient source layout places the frontend and backend beside one another:

```text
workspace/
├── DA_selection_ui/
└── discoveryagent/
```

Install the frontend dependencies:

```bash
cd /path/to/DA_selection_ui
uv sync --python 3.11
```

Configure the backend separately by following the [`discoveryagent`](https://github.com/owencqueen/discoveryagent) README.

With the sibling layout above, the frontend automatically resolves:

```text
../discoveryagent
../discoveryagent/.venv/bin/python
```

For another layout, set the backend paths before launching the frontend:

```bash
export DISCOVERYAGENT_BACKEND_ROOT="/path/to/discoveryagent"
export DISCOVERYAGENT_BACKEND_PYTHON="/path/to/discoveryagent/.venv/bin/python"
```

These settings are only required for operations that launch the autonomous backend.

## Credentials

Current workflows require `ANTHROPIC_API_KEY`:

```bash
export ANTHROPIC_API_KEY="your-key"
```

Set the key before launching the frontend and restart the frontend after changing it. The frontend passes the key to backend processes when needed but does not persist it in project files, round configuration, browser storage, or command-line arguments.

## Run the frontend

From the frontend repository:

```bash
uv run python -m da_selection_ui
```

Then open:

```text
http://127.0.0.1:8000
```

To use another port:

```bash
uv run python -m da_selection_ui --port 8766
```

## Research workflow

1. Create a project with a research objective, context, and data.
2. Start Round 0 for an exploratory investigation.
3. Follow the investigation and inspect its artifacts in Round Workspace.
4. Use Hypothesis Studio to generate and refine candidate hypotheses.
5. Select a hypothesis and start the next hypothesis-directed round.
6. Continue the hypothesis → investigation cycle as the project develops.
7. Add project-specific Skills or use Audit Agent when useful.

A running round can be stopped without discarding its artifacts. Interrupted work can be resumed when a valid checkpoint exists; otherwise the round can be resubmitted.

## Projects and local data

Set `DISCOVERYAGENT_DATA_DIR` to override the frontend's local data root.

Default locations include:

| Platform | Default data root |
|---|---|
| macOS | `~/Library/Application Support/DiscoveryAgent` |
| Linux | `${XDG_DATA_HOME}/discoveryagent`, or `~/.local/share/discoveryagent` |

Managed projects use a structure similar to:

```text
<data-root>/projects/<project-id>/
├── .discoveryagent/
├── code/
│   ├── data/
│   └── .claude/
│       └── skills/
└── checkpoint/
```

Project context and scientific inputs live under `code/data/`. Checkpoints, reports, generated code, interactions, and imported Skills remain within the managed local project.
