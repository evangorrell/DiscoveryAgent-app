# DiscoveryAgent User Guide

For installation and a first investigation, start with the [README](../README.md#quick-start).

## Authentication and models

Launch from Terminal with `discoveryagent`. DiscoveryAgent uses an existing
`ANTHROPIC_API_KEY` environment variable or asks for your key through a hidden-input
prompt. A key entered at the prompt is available only for that launch; it is not
saved by DiscoveryAgent. You may be prompted again next time. Never include keys
in project descriptions, skills, shared troubleshooting reports, or Git repositories.

To configure the key for the current Terminal session, run:

```bash
export ANTHROPIC_API_KEY="your-api-key"
discoveryagent
```

Replace `your-api-key` with your own key. `export` makes the key available to
programs launched from that Terminal session. Closing that Terminal ends the
session and its environment; it does not save the key for future sessions.

For future Terminal sessions, macOS uses `zsh` by default. You can manually add
this line to your shell configuration file:

```bash
export ANTHROPIC_API_KEY="your-api-key"
```

The file is:

```text
~/.zshrc
```

Then reload the shell configuration in your current Terminal:

```bash
source ~/.zshrc
```

This stores the key in your shell configuration file. Only use this approach if
you are comfortable keeping the credential there. Commands typed into Terminal
may also be saved in shell history; use DiscoveryAgent's hidden-input prompt if
you do not want to type the key into a shell command.

DiscoveryAgent also uses **Paperclip**, a literature-research service that gives
our research agents access to a broad range of scientific literature.
If no saved sign-in or configured Paperclip credential is available, launch walks
you through browser sign-in. Paperclip stores its own authentication, so you
normally sign in only once. Existing `PAPERCLIP_API_KEY` or
`PAPERCLIP_BEARER_TOKEN` credentials are also recognized. You do not need to install
a separate command-line tool to complete the app's sign-in flow.

### Choosing models and round settings

In **Create Project** or **Edit Project**, open **Advanced Settings** to choose
the **Reasoning Model** and **Execution Model** from the supported Anthropic
models. Your provider account must have access to the models you select. The
reasoning choice supports planning, hypothesis development, and interpretation;
the execution choice supports analysis work.

Project settings also include the query budget and research-tool defaults.
**Start round** lets you review the query budget and toggle **Deep Research**
and **Web Search** for that round. The query count is a maximum, not a promise
that every query will be used. A resumed round retains its recorded model and
research configuration.

## Projects and data

On first launch, DiscoveryAgent asks where to keep projects when its configured
project folder does not yet exist. The suggested location is `~/DiscoveryAgent`;
you can choose another folder. Existing files there are left alone. The app also
offers a per-project **Project Location** setting under **Advanced Settings**
when creating a project.

Each project keeps its research objective, context, rounds, generated code,
reports, checkpoints, and analysis outputs. Use the context field for relevant
experimental details, terminology, assumptions, and limitations.

**DiscoveryAgent Data** registers input files and folders without copying or
moving them. Select which inputs each project may access. These inputs are
available read-only to research execution; generated outputs go into the project
workspace.

Because registered data is referenced in place, moving a project folder alone
does not move its inputs. Keep the original paths available, or register the data
at its new location and update the project's selections. Updates and uninstall
preserve project data; uninstall also preserves Paperclip credentials.

## Running and reviewing research

A new project starts with **Round 0**, an exploratory investigation driven by
your objective and data—not by a manually selected hypothesis. Follow progress
in **Round Workspace** and review the completed report.

After Round 0 finishes, open **Hypothesis Studio** and choose **Initialize workspace**. You can provide optional guidance before generating candidates. Discuss a hypothesis in **Chat**, request changes in **Edit**, edit hypotheses manually, regenerate a candidate entirely, or remove individual hypotheses from the **•••** menu. Select a hypothesis when you are ready to investigate it in the next round.

Round Workspace keeps the round's activity, reports, figures, code, and generated files together. The **Round Report** should contain enough information to understand the investigation, but you can also review individual query reports and the sub-agents that were called. Ask the **Audit Agent** about evidence or analysis choices to understand the underlying outputs. Generated claims still require scientific review and, where appropriate, independent validation.

### Stopping and resuming

Use the app's **Stop** control to stop an active research round. Depending on its
saved checkpoint, the app may offer to resume it or resubmit it.

When finished, press **Ctrl+C** in the Terminal running DiscoveryAgent to close
the local app. Stop active research in the app first. Projects and completed
research remain on disk; `discoveryagent` opens the app again later.

## Using Skills

Open a project's **Skills** page and choose **Import Skill**. You can import a
standalone `SKILL.md` or a `.skill` / `.zip` archive containing that file and
supporting resources such as scripts or reference material. Review the recognized
name and description, then confirm the import.

A standalone file must be named `SKILL.md` and begin with single-line `name` and
`description` metadata, followed by instructions. For example:

```markdown
---
name: analysis-quality-check
description: Check assumptions and uncertainty before reporting an analysis.
---

Document missing values, justify statistical assumptions, and report uncertainty.
```

An archive should contain `SKILL.md` at its root or within one enclosing skill
folder. Import one skill per package. Only import skills you trust: their
instructions and supporting code can influence execution agents.

Skills belong to the project into which you import them. Each round captures the
installed skill set and reuses that capture when resumed. Importing a skill while
a round runs makes it available to future rounds, not the current one. Removing
skills is disabled during active research and does not rewrite earlier rounds'
captured skills. Making a skill available does not guarantee an agent will use it.

## Updating and troubleshooting

Once a new release is available, run:

```bash
discoveryagent update
```

Then launch DiscoveryAgent normally:

```bash
discoveryagent
```

The updater follows the latest stable release and verifies the
download before installation. Public downloads do not require GitHub CLI or a
GitHub login. Projects and settings are preserved. Updates and uninstall refuse
to proceed during active research; finish or stop the round first.

| Symptom | What to check |
| --- | --- |
| `discoveryagent: command not found` | Open a new Terminal after installation, or follow the PATH instruction printed by the installer. |
| Asked for an API key again | Prompt-entered keys are session-only. An existing `ANTHROPIC_API_KEY` is used automatically. |
| Authentication or model-access error | Check the provider account and selected model. Restart after changing credentials so the app receives them. |
| Hypothesis Studio asks you to finish Round 0 | Complete the initial exploration before generating candidate hypotheses. |
| A registered input is unavailable | Check that its original file/folder or external drive is still accessible. |

Use `discoveryagent version` to identify the installed release when reporting a
problem.

If you encounter a bug or unexpected behavior, please [open an issue on GitHub](https://github.com/evangorrell/DiscoveryAgent-app/issues). Include the error message and what you were doing when it occurred, but remove credentials and sensitive research content.

For everyday commands, see the [CLI table](../README.md#cli).

## Data handling

DiscoveryAgent runs locally but uses external providers for model inference and literature research. Depending on the task, relevant research context and data may leave your computer. Review your organization's data-handling requirements and the providers' terms before using confidential, identifiable, regulated, or proprietary material.
