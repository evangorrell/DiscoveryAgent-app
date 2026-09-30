# DiscoveryAgent

DiscoveryAgent is a multi-agent AI system for semi-autonomous scientific research. Give it your data and a research objective, and it carries out iterative scientific investigations—exploring your data, connecting findings to the scientific literature, performing computational analyses, and developing new hypotheses.

Once you select a hypothesis, a coordinated team of specialized AI agents plans the investigation, searches and synthesizes the literature, writes and executes analyses, critiques intermediate findings, and builds on what it learns. Research unfolds in rounds, with each round carrying forward the evidence and context from the last so the investigation can deepen over time.

DiscoveryAgent runs locally on your Mac as a persistent research workspace.

<details>
<summary><strong> Watch the 30-second DiscoveryAgent demo</strong></summary>

https://github.com/user-attachments/assets/0ce3b30c-fe8b-4726-8b28-6bfcd3c17de1

</details>

## Quick Start

### Requirements

- **Apple Silicon Mac, macOS 15 or newer**
- **Anthropic API key**

The installer manages the application runtime and dependencies for you. Paperclip authentication for literature research is handled during setup.

### Install

```bash
curl -fsSL https://github.com/evangorrell/DiscoveryAgent-app/releases/latest/download/install.sh | sh
```

This downloads the installer from the latest stable DiscoveryAgent release.

### Run

After installation, open a new Terminal window and run:

```bash
discoveryagent
```

This launches the local DiscoveryAgent app and opens it in your browser.

On first launch, follow the Terminal prompts for your project folder, Anthropic API key, and Paperclip sign-in. If credentials are already configured, those prompts are skipped. Keep Terminal open while using the app; press **Ctrl+C** there when finished.

### Your first investigation

1. **Add your data.** Register the files or folders you want DiscoveryAgent to work with.
2. **Create a project.** Select your data, provide a research objective, and add relevant scientific context.
3. **Run the initial exploration.** DiscoveryAgent works best when it can first explore your data and get oriented. Start **Round 0** and review its findings in **Round Workspace**.
4. **Choose the next direction.** Open **Hypothesis Studio**, initialize the workspace, discuss, edit, and revise candidate hypotheses, and select one for the next round.

## How It Works

**Data & context → Explore → Develop hypotheses → Investigate → Review & audit → Continue**

Start with an exploratory round to build an understanding of your research problem. Use those findings to develop and select a hypothesis, then launch a focused research round. Review the evidence, question the results with the Audit Agent, and choose the next direction.

## App Overview

| Area | What you can do |
| --- | --- |
| **Projects (Home)** | Create new projects and manage existing investigations. |
| **Data (Home)** | Register local files and folders, then choose which projects can access them. |
| **Hypothesis Studio** | Generate, discuss, revise, and select candidate hypotheses. |
| **Round Workspace** | Follow research activity and browse each round's code, reports, figures, and outputs. |
| **Reports** | Read a summary or full research round report and inspect supporting analyses and figures. |
| **Audit Agent** | Ask questions about the investigation and inspect its reports, code, and supporting evidence. |
| **Skills** | Import project-specific skills for execution agents. |

## Agent System

DiscoveryAgent coordinates several agent roles around an investigation. A Director agent guides the sequence of research queries; research and execution agents gather information and carry out analyses. A review agent evaluates intermediate findings and helps refine the investigation, while hypothesis-generation agents propose possible next directions. Memory and reporting components retain context across the work and assemble results for review.

These roles connect literature, analysis, and interpretation across research rounds. In the app, you set the objective, make data available, select hypotheses, and decide when to continue. Agents generate reports, code, and figures to let you examine how a result was reached.

> For more details on the technical architecture, evaluation, and scientific methodology, see the accompanying [paper](#paper).

## Skills

Skills extend the execution agent with instructions, workflows, and supporting resources. Import a `SKILL.md`, `.skill`, or `.zip` package into a project's **Skills** page. Each research round captures its skill configuration, so later imports do not change a round already underway.

See [Using Skills](docs/user-guide.md#using-skills) for package format and round behavior.

## Configuration

Choose supported Anthropic **Reasoning** and **Execution** models in a project's **Advanced Settings**. When starting a round, review the query budget and whether **Deep Research** and **Web Search** are enabled. Resuming an interrupted round keeps its recorded configuration.

An Anthropic key entered at launch is used only for that session and is not saved by DiscoveryAgent. Paperclip manages its own saved sign-in.

See [Authentication and models](docs/user-guide.md#authentication-and-models) for more details.

## CLI

| Command | Purpose |
| --- | --- |
| `discoveryagent` | Launch the app. |
| `discoveryagent version` | Show the installed DiscoveryAgent version. |
| `discoveryagent update` | Install the latest release. |
| `discoveryagent uninstall` | Remove the app while preserving project data. |

Updates preserve projects and settings. Finish or stop active research before updating or uninstalling.

See [Updating and troubleshooting](docs/user-guide.md#updating-and-troubleshooting) for more details.

## Local Data & Privacy

The interface, project files, and analysis environment run locally on your Mac. Registered input data stays at its original location; keep those files available when reopening or moving a project.

**Local does not mean offline.** Research uses external services, including Anthropic and Paperclip; relevant data, excerpts, and research context may be sent to them. Agents can generate and execute code locally. Use only data permitted by your organization's policies.

## Documentation

- [User Guide](docs/user-guide.md) — authentication, project data, models, skills, updating, and troubleshooting

## Paper

**[Paper Title](paper-url)**<br>
Authors...

<!-- PUBLICATION: PAPER AND CITATION
Replace Paper Title, paper-url, and Authors... above with the approved details.
Add the final BibTeX citation below and keep the Agent System callout pointing
to this Paper section.
Required: title, authors, public URL, and approved BibTeX. Do not infer a venue,
DOI, arXiv identifier, or publication date.
-->

### Citation

```bibtex
% TODO: Add the approved BibTeX citation.
```

<!-- PUBLICATION: Add the approved license notice and link before publication.
License and publication approval are pending; no license is asserted here. -->
