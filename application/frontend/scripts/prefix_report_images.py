#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None

IMAGE_PATTERN = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)(\s+"[^"]*")?\)')
SCHEME_PATTERN = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.-]*:')


def _is_relative_path(path: str) -> bool:
    if not path:
        return False
    if SCHEME_PATTERN.match(path):
        return False
    if path.startswith("#"):
        return False
    return not Path(path).is_absolute()


def _prefix_image_paths(text: str, code_dir: Path) -> tuple[str, int]:
    code_dir_posix = code_dir.as_posix().rstrip("/")
    changes = 0

    def replacer(match: re.Match) -> str:
        nonlocal changes
        alt_text = match.group(1)
        path = match.group(2)
        title = match.group(3) or ""

        if not _is_relative_path(path):
            return match.group(0)

        normalized = path.replace("\\", "/")
        if normalized.startswith(code_dir_posix + "/") or normalized == code_dir_posix:
            return match.group(0)

        prefixed = (code_dir / normalized).as_posix()
        changes += 1
        return f"![{alt_text}]({prefixed}{title})"

    updated = IMAGE_PATTERN.sub(replacer, text)
    return updated, changes


def _update_report(path: Path, code_dir: Path, dry_run: bool) -> int:
    if not path.exists():
        return 0
    original = path.read_text(encoding="utf-8")
    updated, changes = _prefix_image_paths(original, code_dir)
    if changes and not dry_run:
        path.write_text(updated, encoding="utf-8")
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prefix Markdown image paths in memory_report.md and round_report.md with code_dir."
    )
    parser.add_argument("--checkpoint-dir", required=True, help="Path to the current session checkpoint directory.")
    parser.add_argument(
        "--checkpoint-s3-prefix",
        required=True,
        help="Checkpoint S3 prefix used to derive the code_dir path.",
    )
    parser.add_argument(
        "--disk-path",
        default=os.environ.get("DISK_PATH", "./local_disk"),
        help="Base disk path (defaults to DISK_PATH or ./local_disk).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Only report changes without writing files.")
    args = parser.parse_args()

    if not os.environ.get("DISK_PATH") and load_dotenv:
        load_dotenv()

    checkpoint_dir = Path(args.checkpoint_dir)
    checkpoint_s3_prefix = args.checkpoint_s3_prefix.strip("/")
    disk_path = Path(args.disk_path)
    code_dir = disk_path / checkpoint_s3_prefix / "code"

    targets = [
        checkpoint_dir / "memory_report.md",
        checkpoint_dir / "round_report.md",
    ]

    total_changes = 0
    for target in targets:
        changes = _update_report(target, code_dir, args.dry_run)
        total_changes += changes
        if changes:
            mode = "Would update" if args.dry_run else "Updated"
            print(f"{mode} {target}: {changes} image path(s) prefixed.")
        else:
            print(f"No changes for {target}.")

    return 0 if total_changes >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
