# DiscoveryAgent

DiscoveryAgent is a local application for semi-autonomous scientific research. Scientists define the research direction while a coordinated team of AI agents investigates questions, analyzes evidence, and develops research hypotheses.

## Requirements

DiscoveryAgent currently supports **Apple Silicon Macs**.

To use DiscoveryAgent, you need:

- an Anthropic API key
- a Paperclip account
- an internet connection
- access to the DiscoveryAgent GitHub repository
- GitHub CLI (`gh`), authenticated with an account that has access to this repository

DiscoveryAgent installs and manages its own Python runtime and application dependencies. You do not need to separately install Python, uv, Node.js, npm, or Claude Code.

## Install

DiscoveryAgent is currently distributed through a private GitHub repository. Authenticate GitHub CLI on your Mac before installing:

```bash
gh auth login
```

If you are already authenticated, you can verify access with:

```bash
gh auth status
```

Then install and launch DiscoveryAgent:

```bash
(installer_dir=$(mktemp -d) && trap 'rm -rf "$installer_dir"' EXIT && gh release download --repo evangorrell/DiscoveryAgent-app --pattern install.sh --dir "$installer_dir" && sh "$installer_dir/install.sh") && "$HOME/.local/bin/discoveryagent"
```

The installer downloads DiscoveryAgent, verifies the release, and installs everything required to run it locally on your Mac.

## First launch

The install command launches DiscoveryAgent automatically after installation.

### Anthropic API key

DiscoveryAgent requires an Anthropic API key to run its research agents.

If `ANTHROPIC_API_KEY` is already available in your environment, DiscoveryAgent uses it automatically.

Otherwise, DiscoveryAgent securely prompts you to enter your API key in Terminal.

A key entered through this prompt is available only to the current DiscoveryAgent process and is not persisted to disk by DiscoveryAgent. You will therefore be prompted again on a future fresh launch unless `ANTHROPIC_API_KEY` is already configured in your environment.

### Paperclip

DiscoveryAgent uses Paperclip for deep research and literature retrieval.

If Paperclip has not previously been authenticated on your Mac, DiscoveryAgent guides you through the Paperclip login process on first launch.

Paperclip manages and persists its own authentication credentials. Once authenticated, subsequent DiscoveryAgent launches normally reuse the existing Paperclip login.

### Opening DiscoveryAgent

After authentication is complete, DiscoveryAgent starts locally and automatically opens the application in your browser.

Keep the Terminal window open while using DiscoveryAgent.

To stop the local DiscoveryAgent frontend, return to Terminal and press:

```text
Ctrl+C
```

## Launch DiscoveryAgent

After installation, start DiscoveryAgent at any time with:

```bash
discoveryagent
```

This is equivalent to:

```bash
discoveryagent launch
```

If an Anthropic API key is not already available in your environment, DiscoveryAgent prompts for it before launching.

## Commands

| Command | Description |
| --- | --- |
| `discoveryagent` | Launch DiscoveryAgent |
| `discoveryagent launch` | Launch DiscoveryAgent |
| `discoveryagent version` | Show the installed DiscoveryAgent version |
| `discoveryagent update` | Update DiscoveryAgent to the latest available release |
| `discoveryagent uninstall` | Uninstall DiscoveryAgent |

DiscoveryAgent refuses an update or uninstall while an active research round is running.

## Project data

DiscoveryAgent stores research projects separately from the installed application.

On macOS, project data is stored at:

```text
~/Library/Application Support/DiscoveryAgent
```

Updating or uninstalling DiscoveryAgent does not delete this project data.

## Update

To update DiscoveryAgent:

```bash
discoveryagent update
```

DiscoveryAgent installs the new release while preserving existing research projects.

## Uninstall

To uninstall DiscoveryAgent:

```bash
discoveryagent uninstall
```

Uninstalling removes the DiscoveryAgent application, its managed runtime, and the `discoveryagent` command.

Research projects are preserved.

Paperclip authentication credentials are also preserved because they are managed separately by Paperclip.

## Troubleshooting

### GitHub authentication

If installation cannot access the DiscoveryAgent release, verify that GitHub CLI is authenticated with an account that has access to this repository:

```bash
gh auth status
```

If necessary:

```bash
gh auth login
```

Then retry the installation command.

### `discoveryagent: command not found`

The installer configures your shell so that `discoveryagent` is available in future Terminal sessions.

If necessary, open a new Terminal window and run:

```bash
discoveryagent
```

### DiscoveryAgent asks for my Anthropic API key again

This is expected if the key was entered through DiscoveryAgent's secure prompt.

DiscoveryAgent does not persist a prompted Anthropic API key. Enter it again when prompted, or independently configure `ANTHROPIC_API_KEY` in your environment.

### Paperclip asks me to authenticate

Complete the Paperclip authentication flow shown in Terminal. Once authentication succeeds, Paperclip normally reuses its saved credentials on subsequent launches.

### Stop DiscoveryAgent

Return to the Terminal window in which DiscoveryAgent is running and press:

```text
Ctrl+C
```

## Development

This repository packages DiscoveryAgent for installation and distribution.

The DiscoveryAgent application is composed from versioned frontend and research-engine releases and is installed locally on the user's machine.
