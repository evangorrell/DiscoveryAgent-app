# Audit UI Guide

Welcome to the Audit UI for the DiscoveryAgent project! This guide will walk you through the basic capabilities of the system and how to use it effectively.

## Purpose

The Audit UI is a read-only review workspace for understanding and validating what the Discovery Agent did during a research run.

It is designed for auditors, reviewers, and collaborators who need to inspect the agent's reasoning, outputs, and evidence without changing the underlying run.

## What This UI Represents

The Audit UI represents a single project session. During the iteration of DiscoveryAgent, it produces several artifacts:

- **Checkpoints**: saved reports, intermediate files, and generated outputs from the run. Think of this as the results found during the research.
- **Code**: The project codebase used to produce those outputs. Think of this as the methods used to produce the results.

When you ask questions in Audit Chat, the agent answers by grounding responses in these artifacts and code context.

## Using the Audit UI

- Ask the Audit Chat to explain findings, assumptions, methods, and decisions.
- Request pointers to specific evidence in reports, checkpoints, or source files.
- Ask the Audit Agent to inspect supporting context such as the README, research paper (if configured), code, and checkpoints.
- Export the full audit chat transcript for documentation and handoff.

### What You Cannot Do Here

- You cannot run new experiments from this screen.
- You cannot edit the research or research paper. This is done through other mechanisms in the DiscoveryAgent project.

## How To Use It Effectively

1. Start with orientation:
Ask the Audit Agent to summarize the README and (if available) the research paper to understand project scope.

2. Build a quick mental model:
Ask the chat for a summary of goals, methods, and key outcomes.

3. Verify specific claims:
Ask for direct references to report sections, checkpoint files, or code paths.

4. Trace decision logic:
Use follow-up questions like "what evidence led to this conclusion?" and "what alternatives were considered?"

### Suggested First Questions

- "Give me a 5-bullet summary of what this run accomplished."
- "Which findings are best supported by concrete evidence?"
- "What are the biggest uncertainties or limitations in this run?"
- "Show me where {insert decision here} was made in the research process - cite a specific round and query."
- "Which script implemented {insert decision here}? Provide exact references."
