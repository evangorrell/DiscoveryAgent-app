# S3 Pulling Guide

This project can prefetch checkpoints from S3 after login. The logic is intentionally simple so you can customize it later.

## Where to edit

- Core S3 sync logic: `da_selection_ui/app.py`
  - Functions: `start_checkpoint_sync()`, `_parse_s3_prefix()`, `_checkpoint_cache_dir()`
  - Login hook: `api_login()` sets `checkpoint_dir` and starts the sync
  - Initialization guard: `api_initialize()` checks for local cache presence
- Project key config: `da_selection_ui/register_project_key.py`
  - CLI flag: `--checkpoint-s3-prefix`
  - Stored under `config.checkpoint_s3_prefix`

## How it works (current)

1. Register a project key with `checkpoint_s3_prefix` pointing at an S3 prefix.
2. On login, the app:
   - sets `checkpoint_dir` to a local cache path
   - starts a background download of all objects under the prefix
3. When the user initializes, the app checks if the local cache exists.

## How to change the behavior

- Change where files are cached:
  - Edit `_checkpoint_cache_dir()` in `app.py`
  - Or set `DISCOVERY_AGENT_CACHE_DIR` env var
- Change what gets downloaded:
  - Modify `start_checkpoint_sync()` to filter keys by extension or depth
  - Add a manifest file and download only listed objects
- Add progress reporting:
  - Track counts in `S3_SYNC_STATUS`
  - Expose an `/api/sync_status` endpoint for the UI
- Make it synchronous:
  - Replace the background thread with a direct call (not recommended for large data)

## Required env vars for S3

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_REGION`
- Optional: `DISCOVERY_AGENT_S3_BUCKET` or `S3_BUCKET` (if not included in the prefix)

## Example registration

```
python -m da_selection_ui.register_project_key \
  --name "project-abc" \
  --usernames "alice,bob" \
  --checkpoint-s3-prefix "s3://my-bucket/checkpoints/project-abc/round_0/"
```

