# Pulling User Interaction Data

This folder contains scripts to locate session IDs in the DB and fetch full-text telemetry events from S3.

## Prerequisites

- Python environment with `boto3` and `sqlalchemy` installed.
- AWS credentials configured for S3 read access to `s3://agentic-science`.
- Database URL set in one of:
  - `DISCOVERY_AGENT_DATABASE_URL`
  - `DATABASE_URL`

## 1) Find session IDs for a user

This lists sessions recorded in `event_logs` for a given username.

```bash
export TARGET_USERNAME=alice
export DISCOVERY_AGENT_DATABASE_URL=postgresql+psycopg2://...
python user_data/example_user_sessions.py
```

Output fields:
- `session_id` (use this later)
- `project_key_hash`
- `first_seen`, `last_seen`, `events`

## 2) Resolve telemetry S3 URIs for a session

This queries `event_logs` for `telemetry_event` rows and prints the exact S3 URIs.

```bash
export TARGET_SESSION_ID=...
export DISCOVERY_AGENT_DATABASE_URL=postgresql+psycopg2://...
python user_data/example_telemetry_lookup.py
```

Use the printed `s3://.../telemetry/session_logs/<project_key_hash>/session_<session_id>/...` path to confirm the correct project hash.

## 3) Pull full telemetry events from S3

Edit `PROJECT_KEY_HASH` and `SESSION_ID` in the script based on step (2), then run:

```bash
python user_data/example_user_pull.py
```

This downloads all JSON events for that session, sorts by timestamp, and prints a quick sample.

## Troubleshooting

- `Loaded 0 events` usually means the `PROJECT_KEY_HASH` is incorrect. Use step (2) to copy the exact path.
- `NoCredentialsError`: configure AWS credentials (env vars or `aws configure`).
- If you don’t see `telemetry_event` rows, the S3 upload didn’t happen for that session.
