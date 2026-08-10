# Accessing Audit User Data

This guide documents the current audit logging representation and the recommended workflow for reconstructing complete user-chat context for analysis.

---

## Canonical Source for Full Chat Reconstruction

Use **S3 telemetry events** as the canonical source.

Why:
- S3 events include full payloads per interaction (including full conversation snapshots).
- Database `event_logs` is an index and is intentionally compact/sanitized.
- Local JSONL is a useful fallback mirror, but S3 is the source of truth for post-study reconstruction.

---

## Where Data is Saved in S3

Each telemetry event is saved as a separate JSON object at:

```text
s3://<bucket>/<telemetry_prefix>/session_logs/<project_key_hash>/session_<session_id>/<YYYYMMDD>/event_<YYYYMMDDTHHMMSSZ>_<event_id>.json
```

### Bucket and prefix resolution

- Bucket:
  1. `DISCOVERY_AGENT_TELEMETRY_BUCKET`
  2. `DISCOVERY_AGENT_LOG_BUCKET`
  3. `S3_BUCKET_NAME`
  4. `DISCOVERY_AGENT_S3_BUCKET`
  5. default: `agentic-science`
- Prefix:
  - `DISCOVERY_AGENT_TELEMETRY_PREFIX`
  - default: `telemetry`

So with defaults, paths look like:

```text
s3://agentic-science/telemetry/session_logs/<project_key_hash>/session_<session_id>/...
```

---

## Session Identifier You Should Use

For S3 reconstruction, a session is identified by the pair:

1. `project_key_hash`
2. `session_id`

You can also provide any telemetry S3 URI and parse both from it.

---

## What is Captured (Audit-Relevant Event Types)

The key telemetry `event_type` values for chat reconstruction are:

| Event Type | Purpose | Key payload fields |
|---|---|---|
| `chat` | Main `/api/chat` exchanges (includes Audit UI chat) | `message`, `reply`, `source`, `chat_scope`, `thinking`, `conversation_messages` |
| `report_chat` | `/api/report_chat` exchanges | `message`, `reply`, `report_type`, `report_view`, `thinking`, `conversation_messages` |
| `ask` | `/api/ask` exchanges | `question`, `answer`, `conversation_messages` |
| `chat_reset` | Reset boundary marker | `scope`, `report_type` (if report), `conversation_messages` (pre-reset snapshot) |
| `audit_chat_restore` | Audit restore boundary marker | `report_key`, `conversation_messages` |

### `conversation_messages` format

`conversation_messages` is a full snapshot of conversation state at the moment of the event:

```json
[
  {"role": "user", "content": "..."},
  {"role": "assistant", "content": "..."}
]
```

This is the field you should use to reconstruct LLM-ready context.

---

## Access Workflow

### 1) Find candidate sessions

From DB by username:

```bash
python user_data/example_user_sessions.py <username>
```

This gives `session_id` and `project_key_hash`.

### 2) Find telemetry URIs for a session (optional but useful)

```bash
TARGET_SESSION_ID=<session_id> python user_data/example_telemetry_lookup.py
```

This prints S3 URIs from DB `telemetry_event` rows.

### 3) Pull raw events from S3 (optional inspection)

```bash
python user_data/example_user_pull.py \
  --project <project_key_hash> \
  --session <session_id> \
  --output /tmp/raw_events.json
```

Or:

```bash
python user_data/example_user_pull.py \
  --s3-uri "s3://.../session_logs/<project_key_hash>/session_<session_id>/...json" \
  --output /tmp/raw_events.json
```

### 4) Reconstruct LLM-ready chat context

Use:

```bash
python user_data/reconstruct_iteractions.py \
  --project <project_key_hash> \
  --session <session_id> \
  --output /tmp/reconstructed_conversations.json
```

or:

```bash
python user_data/reconstruct_iteractions.py \
  --s3-uri "s3://.../session_logs/<project_key_hash>/session_<session_id>/...json" \
  --output /tmp/reconstructed_conversations.json
```

Useful options:
- `--scope hypothesis`
- `--scope background:summary`
- `--scope report:background`
- `--scope report:round`
- `--latest-only` (emit only latest per scope)

---

## Output Shape for LLM Replay

`reconstruct_iteractions.py` returns, per scope, `latest_llm_messages` and per-reset `segments[*].llm_messages` in API-style message format:

```json
[
  {"role": "user", "content": "..."},
  {"role": "assistant", "content": "..."}
]
```

This is directly suitable as conversation history for Claude-style chat APIs.

---

## Notes and Caveats

- Resets are explicit (`chat_reset`) and include a pre-reset snapshot so you can preserve pre/post reset boundaries.
- Audit restore events (`audit_chat_restore`) are explicit and include restored transcript snapshots.
- DB `event_logs` should be treated as metadata/indexing for discovery, not full transcript storage.
- Local JSONL (`da_selection_ui/.interaction_logs/audit_interactions.jsonl`) remains useful as backup, but use S3 for complete reconstruction.
