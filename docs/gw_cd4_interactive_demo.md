# Interactive gw_cd4 demo

From this checkout, run:

```sh
sh tools/gw_cd4_demo.sh
```

Open <http://127.0.0.1:8767/projects/gw-cd4-demo/hypotheses>.
This is a separate project from the earlier static demo on port 8765.
The first launch copies the archived case study into
`~/DiscoveryAgent-gw-cd4-interactive-demo`; subsequent launches preserve your work.
The source archive is untouched. macOS uses APFS file clones for the approximately
5 GB trace, so the files are independent without initially duplicating their data.

## Rehearse the original Studio flow

1. Enter initial scientific guidance in the welcome screen and choose the number
   of hypotheses. Click **Initialize workspace** to generate the candidates.
2. Inspect the hypotheses, justifications, and plans. Select a card to discuss
   it, switch the composer to Edit to request a revision, or use its menu to edit
   manually, regenerate, or remove it. Each candidate retains its own conversation.
3. Select your final direction and add any overall research guidance in the
   confirmation flow. The app saves the selected hypothesis and guidance for
   Round 13. Stop here for a hypothesis-steering demo; starting the research
   round is a separate operation.

Example initial guidance:

> Focus on whether relay-gene enrichment for autoimmune genetic drivers is robust
> across Rest, Stim8hr, and Stim48hr. Propose falsifiable analyses grounded in the
> latest report and accumulated memory. Control for expression, network degree,
> and perturbation quality. Avoid unsupported causal or therapeutic claims.

Generation and conversational revisions use the normal app model configuration
(Claude Opus 4.6), with the real dataset description, Round 12 report and memory.
They require valid Anthropic credentials and send that context to the provider.
This is not an offline simulation. Optional external literature retrieval is
disabled for this demo; existing archived background remains available.

## Reset for another take

Stop the demo server with Ctrl+C, then run:

```sh
sh tools/gw_cd4_demo.sh 8767 --reset
```

Refresh the Studio. This restores the initial guidance screen, archiving the
previous candidates, conversations, selection, and next-round instructions in
`~/DiscoveryAgent-gw-cd4-interactive-demo/studio-backups/`. It keeps research
reports and query checkpoints intact. Reset refuses if Round 13 execution has
already started; prepare a new copy in a different data root in that case.

The preparation script adapts the exported paths, archives the earlier placeholder
candidates, and supplies missing historical completion files. Where available it
uses the original report summary. Round 1 has neither report nor summary, so its
file explicitly identifies itself as a compatibility marker and points to the
retained memory and queries. These files affect only the demo copy. Round 12's
scientific report and memory are preserved verbatim. Candidate sets are stored
in `round_12` because they prepare the user-facing **Round 13**.
