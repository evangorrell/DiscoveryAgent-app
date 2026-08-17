# DiscoveryAgent

DiscoveryAgent is a local application for semi-autonomous scientific research that helps scientists move from a research objective and their data to AI-augmented investigation, hypothesis development, and iterative research rounds.

## Requirements

DiscoveryAgent currently supports **Apple Silicon Macs**.

To use DiscoveryAgent, you need:

- an Anthropic API key
- a Paperclip account

DiscoveryAgent installs and manages everything else it needs.

## Get started

### 1. Install and launch DiscoveryAgent

Open **Terminal** on your Mac and run:

```bash
curl -fsSL https://github.com/evangorrell/DiscoveryAgent-app/releases/latest/download/install.sh | bash && "$HOME/.local/bin/discoveryagent"
```

This downloads the latest DiscoveryAgent installer, verifies and installs the application and its required software, and then launches DiscoveryAgent.

The first installation may take a few minutes.

### 2. Complete first-time authentication

DiscoveryAgent requires an Anthropic API key to run its research agents.

If `ANTHROPIC_API_KEY` is already configured on your Mac, DiscoveryAgent uses it automatically. Otherwise, DiscoveryAgent will securely ask you to enter your API key in Terminal.

A key entered through this prompt is available only for the current DiscoveryAgent process and is not saved by DiscoveryAgent. You may therefore be asked for it again on a future launch.

DiscoveryAgent also uses **Paperclip** for scientific literature search and deep research. If Paperclip has not previously been authenticated on your Mac, DiscoveryAgent will guide you through the login process.

Paperclip stores its own authentication, so you normally only need to sign in once.

After authentication is complete, DiscoveryAgent starts locally and opens automatically in your web browser.

Keep the Terminal window open while using DiscoveryAgent.

### 3. Create a project

From the Projects page, create a new project and provide:

- a project name
- the research objective you want DiscoveryAgent to investigate
- any useful scientific context
- optionally, input data already registered under **DiscoveryAgent Data**

Project context can include experimental details, important terminology, assumptions, known limitations, or other information needed to understand the research problem and your data.

### 4. Add your data

Register one or more input folders on the **Data** page, then select the folders
or files a project may use. Registered inputs remain at their original locations
and can be shared read-only across projects; DiscoveryAgent does not copy, move,
rename, or delete them. Generated code, reports, and analysis outputs remain in
each project's own workspace.

Your projects and research files remain stored locally on your Mac.

### 5. Start Round 0

When your project is ready, start **Round 0**.

Round 0 is an exploratory investigation. DiscoveryAgent uses your research objective, context, and data to investigate the problem, analyze evidence, search relevant literature, and build an initial understanding of the research space.

You can follow the investigation as it runs in **Round Workspace**.

Once the round is complete, review the findings and continue into **Hypothesis Studio** to develop the next research direction.

## Research workflow

DiscoveryAgent organizes research into an iterative cycle:

**Investigate → Review → Develop hypotheses → Select a hypothesis → Investigate again**

### Project Home

Project Home shows the current state of the research, the latest findings, how the project has developed, and the next step in the research process.

### Round Workspace

Round Workspace lets you follow active and completed research rounds and inspect their activity, reports, figures, code, data products, and other outputs.

You can also ask questions about the work completed in a specific round.

### Hypothesis Studio

Hypothesis Studio helps you generate, discuss, revise, compare, and select hypotheses based on the research completed so far.

Once you select a hypothesis, you can start the next research round focused on that direction.

### Skills

Skills let you add project-specific instructions that DiscoveryAgent can use while conducting research.

### Audit Agent

Audit Agent lets you ask questions about the project's research, code, reports, checkpoints, and supporting evidence.

## Launch DiscoveryAgent later

After installation, launch DiscoveryAgent at any time by opening Terminal and running:

```bash
discoveryagent launch
```

If your Anthropic API key is not already configured, DiscoveryAgent will ask for it before launching.

DiscoveryAgent will then start locally and open in your browser.

## Stop DiscoveryAgent

To stop an active research round, use the **Stop** control inside DiscoveryAgent.

A stopped round may be resumable if DiscoveryAgent has a saved checkpoint. If it cannot be resumed, DiscoveryAgent will offer the option to resubmit the round.

When you are finished using DiscoveryAgent, return to the Terminal window where it is running and press:

```text
Ctrl+C
```

Your projects and completed research are preserved.

## Commands

| Command | Description |
| --- | --- |
| `discoveryagent launch` | Launch DiscoveryAgent |
| `discoveryagent version` | Show the installed DiscoveryAgent version |
| `discoveryagent uninstall` | Uninstall DiscoveryAgent |

## Project data

DiscoveryAgent stores research projects separately from the installed application.

The first launch asks where to store projects and suggests:

```text
~/DiscoveryAgent
```

You may choose another location. DiscoveryAgent persists that choice and stops
asking once the folder exists. Existing files in the selected root are left
alone; DiscoveryAgent adds only its own project directories.

This includes each project's generated code, reports, checkpoints, and other
research outputs. Input folders registered in **DiscoveryAgent Data** remain at
their original locations and are not moved into this project root.

Uninstalling DiscoveryAgent does **not** delete this project data.

For important datasets, keep an independent copy of the original files.

## Choosing models

Choose one supported Anthropic model for a project under **Advanced Settings**
when creating or editing it. DiscoveryAgent uses that model consistently across
the project's research agents. A resumed interrupted round keeps the model
already recorded in that round's configuration.

Every **Start round** action still opens settings for the query budget and for
whether deep research and web search are available in that round.

## Security and privacy

DiscoveryAgent runs its interface, project storage, and local analysis environment on your Mac.

DiscoveryAgent is not an offline application. Its research workflows use external services including Anthropic and Paperclip. Depending on the investigation, research context, queries, reports, and relevant data or excerpts may be sent to these services.

DiscoveryAgent's research agents can also generate and execute code locally within the managed project environment.

Before using confidential, regulated, identifiable, proprietary, or embargoed data, follow the applicable data-handling requirements for your organization and the external services being used.

## Technical documentation

This repository packages DiscoveryAgent for local installation and distribution.

For development and technical documentation, see:

- [DiscoveryAgent Frontend](https://github.com/owencqueen/DA_selection_ui)
- [DiscoveryAgent Backend](https://github.com/owencqueen/discoveryagent)
- [Development server launch](docs/development_server_launch.md)
