# Contained Registration

Standalone project key management for the Discovery Agent web UI. This package has **no dependencies** on the rest of the `da_selection_ui` codebase and can be run on HPC environments independently.

## Requirements

- Python 3.9+
- No additional packages required (optional: `python-dotenv` for `.env` file support)

## Usage

### Generate a new project key

```bash
python register_project_key.py --name "my-project"
```

### With configuration options

```bash
python register_project_key.py \
    --name "hpc-experiment" \
    --checkpoint-dir /scratch/user/checkpoints \
    --code-dir /home/user/code \
    --usernames "alice,bob,charlie"
```

### Pin a specific research paper for audit

Use `--research-paper-path` to point a project key at a specific paper inside the checkpoint/log tree instead of using the latest timestamped `research_paper_*` folder:

```bash
python register_project_key.py \
    --name "hpc-experiment-audit" \
    --checkpoint-s3-prefix project-prefix \
    --github-remote-path https://github.com/org/repo \
    --audit-only \
    --research-paper-path research_paper_manual_0515
```

The path can be a directory containing `research_paper.md` / `research_paper.pdf`, or a direct path such as `research_paper_manual_0515/research_paper.md`.

### Override the auth directory location

By default, keys are stored in `.auth_submissions/` relative to the script. You can override this:

```bash
python register_project_key.py --name "my-project" --auth-dir /path/to/auth/storage
```

### Revoke keys

```bash
# Revoke by name
python register_project_key.py --revoke-name "my-project"

# Revoke by hash
python register_project_key.py --revoke-key <sha256-hash>
```

### Replace all existing keys

```bash
python register_project_key.py --name "new-key" --force
```

## All Options

| Option | Description |
|--------|-------------|
| `--name` | Label for the key (e.g., "beta testers") |
| `--project-prefix` | Alphanumeric prefix for local reference |
| `--checkpoint-dir` | Default checkpoint directory |
| `--code-dir` | Default code directory |
| `--readme-path` | Default README path |
| `--checkpoint-s3-prefix` | S3 prefix for checkpoint syncing |
| `--research-paper-path` | Specific paper file or folder within checkpoint/log roots |
| `--usernames` | Comma-separated allowed usernames |
| `--bytes` | Key length in bytes (default: 16) |
| `--force` | Replace all existing keys |
| `--revoke-name` | Remove keys by label |
| `--revoke-key` | Remove key by hash |
| `--auth-dir` | Override auth storage directory |

## Output Files

Keys are stored in two files within the auth directory:

- `project_key.hash` - SHA-256 hashes (one per line)
- `project_keys.json` - Full registry with metadata and config

## Transferring to HPC

Copy just the `contained_registration/` directory to your HPC environment:

```bash
scp -r contained_registration/ user@hpc:/path/to/destination/
```

Then run directly:

```bash
cd /path/to/destination/contained_registration
python register_project_key.py --name "hpc-key" --checkpoint-dir /scratch/checkpoints
```
