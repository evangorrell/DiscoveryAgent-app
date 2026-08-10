#!/usr/bin/env python3
import argparse
import os
import sys

from da_selection_ui.utils.pull_s3 import sync_checkpoint_code_from_s3_to_disk


def _parse_prefix(raw: str) -> tuple[str | None, str]:
    value = (raw or "").strip()
    if not value:
        raise ValueError("project-prefix is required")
    if value.startswith("s3://"):
        tail = value[len("s3://") :]
        if "/" not in tail:
            raise ValueError("s3:// prefix must include a bucket and path")
        bucket, prefix = tail.split("/", 1)
        return bucket.strip() or None, prefix.strip("/")
    return None, value.strip("/")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download code/checkpoint data for a project prefix into DISK_PATH.",
    )
    parser.add_argument(
        "project_prefix",
        help="Project prefix (e.g., 'checkpoints/project-abc/round_0' or 's3://bucket/checkpoints/project-abc/round_0')",
    )
    args = parser.parse_args()

    try:
        bucket, prefix = _parse_prefix(args.project_prefix)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if bucket:
        existing = os.environ.get("S3_BUCKET_NAME")
        if existing and existing != bucket:
            print(
                f"error: S3_BUCKET_NAME is set to '{existing}' but prefix bucket is '{bucket}'",
                file=sys.stderr,
            )
            return 2
        os.environ.setdefault("S3_BUCKET_NAME", bucket)

    if not os.environ.get("S3_BUCKET_NAME"):
        print("error: S3_BUCKET_NAME must be set (env or via s3://bucket/prefix)", file=sys.stderr)
        return 2
    if not os.environ.get("DISK_PATH"):
        print("error: DISK_PATH must be set (e.g., to ./s3_pulls)", file=sys.stderr)
        return 2

    sync_checkpoint_code_from_s3_to_disk(prefix)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
