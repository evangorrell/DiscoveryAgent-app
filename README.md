# DiscoveryAgent

DiscoveryAgent is a local application for semi-autonomous scientific research. Bring your data and research question, develop hypotheses, and run iterative investigations while staying in control of what gets studied and how findings are interpreted.

[Quick Start](#quick-start) · [How It Works](#how-it-works) · [User Guide](docs/user-guide.md) · [Paper](#paper)

<!-- PUBLICATION: DEMO VIDEO
Place the approximately 40-second launch video here, immediately below the introduction.
Upload the final video to GitHub and paste its actual hosted attachment URL on its
own line. Check playback in GitHub's rendered README. If it does not embed there,
add a repository-hosted preview image linked to that same video URL, with alt text
"Watch the DiscoveryAgent demo (40 seconds)". No final asset or URL is available yet.
-->

## Quick Start

### Requirements

- **Apple Silicon Mac, macOS 15 or newer**
- Internet access, an **Anthropic API key**, and **Paperclip authentication** for literature research

The installer manages the application runtime and dependencies for you.

### Install

The public installer is being prepared. The one-line installation command will appear here when the public release is available.

<!-- PUBLICATION: INSTALL COMMAND
Replace the status sentence above with the fenced command below only after the
public stable release has its generated install.sh, matching application archive,
and update manifest attached. Verify an unauthenticated download and a clean install.
The repository-root install.sh is a template still bound to an earlier release;
do not substitute a raw main-branch URL. The latest-release asset URL below follows
the repository/channel used by the updater; it is NOT a verified live installer yet.

```bash
curl -fsSL https://github.com/evangorrell/DiscoveryAgent-app/releases/latest/download/install.sh | sh
```
-->

### Run

After installation, open a new Terminal window and run:

```bash
discoveryagent
```

This launches the local DiscoveryAgent app and opens it in your browser.

On first launch, follow the Terminal prompts for your project folder, Anthropic API key, and Paperclip sign-in. If credentials are already configured, those prompts are skipped. Keep Terminal open while using the app; press **Ctrl+C** there when finished.

### Your first investigation

1. **Create a project.** Give it a research objective and relevant scientific context.
2. **Select your data.** Add files or folders in **DiscoveryAgent Data** and choose what the project can access.
3. **Run the initial exploration.** Start **Round 0** and review its findings in **Round Workspace**.
4. **Choose the next direction.** Open **Hypothesis Studio**, initialize the workspace, discuss or edit candidate hypotheses, and select one for the next round.

## What is DiscoveryAgent?

DiscoveryAgent brings literature research, hypothesis development, and computational analysis into a persistent research workspace. It can investigate a question over multiple steps, generate code and figures, and produce reports that you can inspect before deciding what to investigate next.

You provide the scientific context and choose the direction. Treat generated findings as research outputs to evaluate—not as independently validated conclusions.

## How It Works

**Data & context → Explore → Develop hypotheses → Investigate → Review & audit → Continue**

Start with an exploratory round to build an understanding of your research problem. Use those findings to develop and select a hypothesis, then launch a focused research round. Review the evidence, question the results with the Audit Agent, and choose the next direction.

## App Overview

| Area | What you can do |
| --- | --- |
| **Projects / Home** | Organize investigations and see their progress, latest findings, and next steps. |
| **Data** | Register local files and folders, then choose which projects can access them. |
| **Hypothesis Studio** | Generate, compare, discuss, edit, and select candidate hypotheses. |
| **Round Workspace** | Follow research activity and browse each round's code, reports, figures, and outputs. |
| **Reports** | Read a summary or full round report and inspect supporting analyses and figures. |
| **Audit Agent** | Ask questions about the investigation and inspect its reports, code, and supporting evidence. |
| **Skills** | Import project-specific instructions and supporting resources for execution agents. |

## Agent System

DiscoveryAgent coordinates several agent roles around an investigation. A Director guides the sequence of research queries; research and execution agents gather information and carry out analyses. Review roles evaluate intermediate findings and help refine the investigation, while hypothesis-generation roles propose possible next directions. Memory and reporting components retain context across the work and assemble results for review.

These roles connect literature, analysis, and interpretation across research rounds. In the app, you set the objective, make data available, select hypotheses, and decide when to continue. Reports, generated code, figures, and recorded activity let you examine how a result was reached rather than relying on a final answer alone.

<!-- PUBLICATION: Optional high-level architecture figure goes here when approved.
No new figure is generated for this README. -->

> For the technical architecture, evaluation, and scientific methodology, see the accompanying [paper](#paper).

## Skills

Skills extend execution agents with instructions, workflows, and supporting resources. Import a `SKILL.md`, `.skill`, or `.zip` package from a project's **Skills** page. Each research round captures its skill configuration, so later imports do not change a round already underway.

See [Using Skills](docs/user-guide.md#using-skills) for package format and round behavior.

## Configuration

Choose supported Anthropic **Reasoning** and **Execution** models in a project's **Advanced Settings**. When starting a round, review the query budget and whether **Deep Research** and **Web Search** are enabled. Resuming an interrupted round keeps its recorded configuration.

An Anthropic key entered at launch is used only for that session and is not saved by DiscoveryAgent. Paperclip manages its own saved sign-in. See [Authentication and models](docs/user-guide.md#authentication-and-models).

## CLI

| Command | Purpose |
| --- | --- |
| `discoveryagent` | Launch the app. |
| `discoveryagent launch` | Explicit alias for launching the app. |
| `discoveryagent version` | Show installed release information. |
| `discoveryagent update` | Install the latest public stable release once available; excludes prereleases. |
| `discoveryagent uninstall` | Remove the app while preserving project data and Paperclip credentials. |

Updates preserve projects and settings. Finish or stop active research before updating or uninstalling. See [Updating and troubleshooting](docs/user-guide.md#updating-and-troubleshooting).

## Local Data & Privacy

The interface, project files, and analysis environment run on your Mac. Registered input data stays at its original location; keep those files available when reopening or moving a project.

**Local does not mean offline.** Research uses external services, including Anthropic and Paperclip; relevant data, excerpts, and research context may be sent to them. Agents can generate and execute code locally. Use only data permitted by your organization's policies and the services' terms, and keep independent backups.

## Documentation

- [User guide](docs/user-guide.md): authentication, project data, models, and stopping or resuming work
- [Using Skills](docs/user-guide.md#using-skills)
- [Updating and troubleshooting](docs/user-guide.md#updating-and-troubleshooting)

## Paper

The accompanying paper and citation will be linked here when available.

<!-- PUBLICATION: PAPER AND CITATION
Replace the sentence above with the approved paper title, author list, and URL.
Add the final BibTeX citation beneath it. Link the title here and keep the top
navigation and Agent System callout pointing to this section.
Required: title, authors, public URL, and approved BibTeX. Do not infer a venue,
DOI, arXiv identifier, or publication date.
-->

<!-- PUBLICATION: Add the approved license notice and link before publication.
License and publication approval are pending; no license is asserted here. -->
