# DiscoveryAgent Selection Website Guide

## User Guide

This guide explains how to use the DiscoveryAgent Selection UI to generate, refine, and finalize hypotheses.

---

## What Changed Recently

Major updates now reflected in the app:

- Optional **Initial Guidance** prompt before first hypothesis generation
- Better **Regeneration Guidance** support for both single-hypothesis and regenerate-all flows
- **Chat Context Import** so you can pull compact summaries from other chat threads
- More reliable **persistent chat history** during your session
- Report pages available directly from the main workflow, including **download** support
- Broader **code + checkpoint access across all agents** (hypothesis chat, report chat, audit chat), with read-only behavior
- A **Checkpoint Browser** view for inspecting checkpoint files and structure

---

## Table of Contents

1. [Overview](#overview)
2. [Session Setup](#session-setup)
3. [Reports First](#reports-first)
4. [Generate Hypotheses](#generate-hypotheses)
5. [Regenerate Hypotheses](#regenerate-hypotheses)
6. [Edit and Analyze Hypotheses](#edit-and-analyze-hypotheses)
7. [Chat Context Import](#chat-context-import)
8. [Checkpoint and Code Access](#checkpoint-and-code-access)
9. [Finalize and Feedback](#finalize-and-feedback)
10. [Tips](#tips)
11. [Help](#help)

---

## Overview

The UI is designed to help you:

- Generate candidate hypotheses from your session context
- Review background and latest-round reports before deciding
- Refine hypotheses with LLM-assisted editing or manual edits
- Ask deeper questions about reports, code behavior, and checkpoint evidence
- Submit a final hypothesis with optional research feedback

---

## Session Setup

Before generating:

- Choose the hypothesis model (recommended: Opus 4.6)
- Choose how many hypotheses to generate
- Confirm your session has loaded checkpoint/code resources

You can also open the website guide from the landing card at any time.

---

## Reports First

The main page includes **Session Reports** so you can review context early:

- **View Background Report**: full research context to date
- **View Latest Round Report**: most recent round only
- Optional **Background Literature** button may appear depending on session settings

Each report page includes:

- A report-specific chat assistant
- **Import from** and **Reset conversation** controls
- **Download Report** button

---

## Generate Hypotheses

Click **Generate Hypotheses** to create candidate hypotheses.

### Optional Initial Guidance

Before first generation, use **Optional Initial Guidance** to steer outputs (direction, constraints, priorities, or themes).

Notes:

- This is best for first-pass steering
- You can use different guidance later during regeneration

---

## Regenerate Hypotheses

You can regenerate in two ways:

- **Single hypothesis regenerate** from a card
- **Regenerate all hypotheses** from the main generate/regenerate control

Both flows support optional guidance text to steer the next generation.

Important:

- Regeneration can replace prior hypothesis state
- Regenerate-all warns that editing/chat progress for the round can be lost

---

## Edit and Analyze Hypotheses

After selecting a hypothesis, use the edit workspace.

### Modes

- **Ask with LLM Chat**: Q&A only, no direct hypothesis edits. Best for gaining understanding of the hypothesis.
- **Edit with LLM Chat**: applies requested revisions. Not used for asking questions about the hypothesis.
- **Edit Manually**: direct text editing. Best for making concrete, controllable changes to the hypothesis.

### Version History

Use left/right arrows beside the hypothesis to navigate versions.

### Chat Modes

Inside chat, switch between:

- **Hypotheses & Reports** mode
- **Code (Claude Agent)** mode

---

## Chat Context Import

Use **Import from** in chat headers to bring context from another chat thread.

How it works:

- Import adds a compact summary snapshot from the selected source chat
- It does not merge full threads
- If source chat changes later, import again to refresh

This is useful when you move between report analysis and hypothesis editing and want continuity without copying messages manually.

---

## Checkpoint and Code Access

All in-app agents are now designed to use broad session evidence:

- Reports
- Checkpoints
- Codebase context

This applies across hypothesis chat, report chat, and audit chat experiences.

Use this to ask questions like:

- What changed between rounds?
- Which file or checkpoint supports this claim?
- Where in code is this behavior implemented?

Also available:

- **Checkpoint Browser** for direct checkpoint tree/file inspection

Note: Agent access is for analysis and explanation, not direct code modification.

---

## Finalize and Feedback

When ready:

1. Click **Final Accept & Continue**
2. Review the confirmation overlay
3. Confirm selection

You can also provide **Overall Research Feedback** to guide future research rounds.

---

## Tips

- Review reports before first generation so guidance is more specific
- Use initial guidance for strategy, regeneration guidance for targeted corrections
- Use Ask mode first when uncertain, then Edit mode for concrete changes
- Use context import when changing chat scope to reduce repetition
- Ask for explicit file/checkpoint evidence when validating claims

---
