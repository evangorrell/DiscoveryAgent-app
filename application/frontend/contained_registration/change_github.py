#!/usr/bin/env python3
"""
Update the github_remote_path for an existing project key.

Locates the key entry by hashing the supplied plaintext key (SHA-256) and
updates config.github_remote_path in both project_keys.json and
auth_mirror.json.  Restart the Flask server after running this script.

Usage:
    python change_github.py --key <plaintext-key> --github-remote-path <new-url>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

AUTH_DIR = Path(__file__).parent / '.auth_submissions'
PROJECT_KEY_REGISTRY_FILE = AUTH_DIR / 'project_keys.json'
AUTH_MIRROR_FILE = AUTH_DIR / 'auth_mirror.json'


def _load_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        print(f'ERROR: Could not parse {path}: {exc}', file=sys.stderr)
        sys.exit(1)
    if isinstance(raw, dict):
        entries = raw.get('keys') or []
    elif isinstance(raw, list):
        entries = raw
    else:
        entries = []
    return entries if isinstance(entries, list) else []


def _save_json(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'keys': entries}, indent=2), encoding='utf-8')


def _update_entries(entries: list[dict], target_hash: str, new_path: str) -> tuple[int, str | None]:
    """Return (updated_count, old_value)."""
    updated = 0
    old_value: str | None = None
    for entry in entries:
        if str(entry.get('hash', '')).lower() == target_hash:
            config = entry.setdefault('config', {})
            old_value = config.get('github_remote_path')
            config['github_remote_path'] = new_path
            updated += 1
    return updated, old_value


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Update github_remote_path for an existing project key.'
    )
    parser.add_argument(
        '--key',
        required=True,
        help='Plaintext project key whose github_remote_path should be updated.',
    )
    parser.add_argument(
        '--github-remote-path',
        dest='github_remote_path',
        required=True,
        help='New GitHub remote URL (e.g. https://github.com/org/repo).',
    )
    parser.add_argument(
        '--auth-dir',
        dest='auth_dir',
        default=None,
        help='Override the default .auth_submissions directory location.',
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global AUTH_DIR, PROJECT_KEY_REGISTRY_FILE, AUTH_MIRROR_FILE

    args = parse_args(argv or sys.argv[1:])

    if args.auth_dir:
        AUTH_DIR = Path(args.auth_dir)
        PROJECT_KEY_REGISTRY_FILE = AUTH_DIR / 'project_keys.json'
        AUTH_MIRROR_FILE = AUTH_DIR / 'auth_mirror.json'

    plaintext_key = args.key.strip()
    new_github_path = args.github_remote_path.strip()

    if not plaintext_key:
        print('ERROR: --key must not be empty.', file=sys.stderr)
        return 1
    if not new_github_path:
        print('ERROR: --github-remote-path must not be empty.', file=sys.stderr)
        return 1

    target_hash = hashlib.sha256(plaintext_key.encode('utf-8')).hexdigest().lower()

    # --- Update project_keys.json ---
    registry_entries = _load_json(PROJECT_KEY_REGISTRY_FILE)
    reg_updated, old_value = _update_entries(registry_entries, target_hash, new_github_path)

    if reg_updated == 0:
        print(
            f'ERROR: No entry found with hash {target_hash} in {PROJECT_KEY_REGISTRY_FILE}.',
            file=sys.stderr,
        )
        return 1

    _save_json(PROJECT_KEY_REGISTRY_FILE, registry_entries)

    # --- Update auth_mirror.json (best-effort; may not exist for all setups) ---
    mirror_entries = _load_json(AUTH_MIRROR_FILE)
    mirror_updated, _ = _update_entries(mirror_entries, target_hash, new_github_path)
    if mirror_entries:
        _save_json(AUTH_MIRROR_FILE, mirror_entries)

    # --- Report ---
    print(f'Updated github_remote_path for key hash {target_hash}')
    if old_value is not None:
        print(f'  Old: {old_value}')
    print(f'  New: {new_github_path}')
    print(f'Registry: {PROJECT_KEY_REGISTRY_FILE}  ({reg_updated} entry updated)')
    if mirror_entries:
        print(f'Mirror:   {AUTH_MIRROR_FILE}  ({mirror_updated} entry updated)')
    print('Restart the Flask server to apply the change.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
