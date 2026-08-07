# DiscoveryAgent Interactive CLI

The interactive CLI lets you work with DiscoveryAgent as a live research assistant.
You start a Claude Code session, ask questions in plain English, and the system can run analyses, search the web, and update the running research report for you.

This mode is best when you want to:
- explore a previous DiscoveryAgent run interactively
- follow up on findings from an automated run
- ask for new analyses without restarting the full pipeline
- keep an ongoing research conversation that you can come back to later

## What You Need

Before starting the CLI:
- install the project with the `interactive` extra
- install Claude Code so the `claude` command works
- have a previous DiscoveryAgent run with checkpoints available

Recommended setup:

```bash
uv sync --extra interactive
source .venv/bin/activate
claude --version
```

This is the intended CLI install path. You do not need the `acs`, `opentargets`, `bio`, `torch`, or `all` extras to use interactive mode.

You will also need:
- `ANTHROPIC_API_KEY`
- `PERPLEXITY_API_KEY` if you want to use deep research

## Quick Start

Start a new interactive session:

```bash
python -m cli.run_interactive \
  --checkpoint-dir /path/to/previous_run_logs \
  --code-dir /path/to/code_and_data
```

This loads the latest saved configuration from the previous run and brings in the accumulated research context so the session starts with prior findings already available.
The dataset description, task description, and prior research report are loaded into the Claude session automatically on every launch and resume.

## Continuing a Session

The CLI now supports simple session continuation, modeled after Claude Code:

Start a new session:

```bash
python -m cli.run_interactive \
  --checkpoint-dir /path/to/previous_run_logs \
  --code-dir /path/to/code_and_data
```

Continue the most recent session:

```bash
python -m cli.run_interactive \
  --checkpoint-dir /path/to/previous_run_logs \
  --code-dir /path/to/code_and_data \
  --continue
```

Resume a specific session:

```bash
python -m cli.run_interactive \
  --checkpoint-dir /path/to/previous_run_logs \
  --code-dir /path/to/code_and_data \
  --resume 123e4567
```

You can also give a new session a name:

```bash
python -m cli.run_interactive \
  --checkpoint-dir /path/to/previous_run_logs \
  --code-dir /path/to/code_and_data \
  --name "cell-state followup"
```

## How Sessions Work

Each launch creates a run folder, but runs can belong to the same ongoing session.

In practice this means:
- `--continue` keeps using the latest session for the same checkpoint and code directory
- `--resume` reopens a specific session by ID, short ID prefix, or session path
- the Claude conversation continues across launches
- the DiscoveryAgent research state is also restored from the latest interactive checkpoint

Use the interactive launcher to reopen these sessions:
- `python -m cli.run_interactive --continue` for the latest session
- `python -m cli.run_interactive --resume SESSION` for a specific session

Do not use `claude --resume` for DiscoveryAgent interactive sessions, even if Claude prints that suggestion when the session exits.
Do not use raw `claude --continue` either. DiscoveryAgent wraps Claude with its own session and checkpoint state, and Claude's native resume prompt can be misleading in this setup.

This gives you one continuous working conversation, even though each launch writes logs to a fresh run directory.

## What the CLI Loads Automatically

When the session starts, the CLI automatically loads:
- the latest checkpoint config from `checkpoints/round_N/config.json`
- the latest saved `memory_report.md` from the previous run
- your task description and relevant model/tool settings

This startup context is added automatically so you do not need to paste it in by hand when opening or resuming a session.

The interactive CLI does not use the ACS agent. The Census and OpenTargets agents are disabled by default but can be enabled by setting `CENSUS_AGENT_AVAILABLE=true` or `OPENTARGETS_AGENT_AVAILABLE=true` in your `.env` file (requires the corresponding optional dependencies and data access).

## What You Can Ask It To Do

Once the session is open, you can just talk to it normally. For example:

- "Summarize the main findings so far."
- "Check whether the embedding clusters line up with the labels."
- "Run a quick PCA and tell me what stands out."
- "Search for recent papers related to this result."
- "Update the research report with the key conclusion from that analysis."

You do not need to manually call tools. Ask naturally, and the system will use the right tools when needed.

## What the CLI Can Do Behind the Scenes

The interactive session can:
- run code and analyses in your working directory
- inspect files created during the run
- search the web
- run deep research queries
- generate new hypotheses
- read and update the current research report
- save checkpoints so the session can be resumed later

## Command Line Options

| Argument | What it does |
|---|---|
| `--checkpoint-dir` | Points to a previous DiscoveryAgent log directory. This is required. |
| `--code-dir` | Points to the working code/data directory. This is required. |
| `--log-dir` | Sets the output folder for the current launch. Usually you can leave this alone. |
| `--config-override` | Lets you override values from the saved checkpoint config with a YAML file. |
| `--continue` | Reopens the most recent interactive session. |
| `--resume SESSION` | Reopens a specific session by ID, ID prefix, or path. |
| `--name` | Sets a friendly name for a new session. |

## Changing Models or Tool Settings

If you want to change settings without editing the saved checkpoint, use a small YAML override file.

Example:

```yaml
execution_model: claude-opus-4-6
hypothesis_model: claude-opus-4-6
omit_deepresearch: true
```

Run with:

```bash
python -m cli.run_interactive \
  --checkpoint-dir /path/to/previous_run_logs \
  --code-dir /path/to/code_and_data \
  --config-override my_override.yaml
```

## Where Files Go

Interactive session data is stored inside your checkpoint directory:

```text
<checkpoint-dir>/
  interactive_sessions/
    <session-id>/
      session.json
      state/
        latest -> snapshots/snapshot_<N>/
        report.md
        queries/
          query_000/
            query_000.md
            execution_agent/
        snapshots/
          snapshot_<N>/
            snapshot.json
            checkpoint.json
            summary.md
      latest_run -> runs/<timestamp>/
      runs/
        <timestamp>/
          interactive_stream.log
          ai_researcher.log
```

Useful things to know:
- `session.json` stores the session metadata
- `state/` is the canonical resumable interactive state for the session
- `runs/` contains per-launch logs only
- `state/latest` points at the current resume snapshot
- each launch gets its own folder under `runs/`
- the CLI prints the active log directory when it starts

## Watching Progress

If you want to watch tool activity in another terminal:

```bash
tail -f <log-dir>/interactive_stream.log
```

## Troubleshooting

`claude: command not found`

Install Claude Code so the `claude` command is available on your machine.

`ModuleNotFoundError` when launching the CLI

Make sure you installed the `interactive` extra, not just the base environment:

```bash
uv sync --extra interactive
```

Session will not resume

Use the same `--checkpoint-dir` and `--code-dir` that were used to create the session. `--continue` only resumes sessions that match both.

Do not reopen DiscoveryAgent interactive sessions with `claude --resume`, even if Claude tells you to do so at exit. Do not use raw `claude --continue` either. Always reopen them through `python -m cli.run_interactive`.

Deep research does not work

Set `PERPLEXITY_API_KEY` in your environment.

The wrong Python is being used

Prefer the project environment:

```bash
source .venv/bin/activate
python -m cli.run_interactive ...
```
