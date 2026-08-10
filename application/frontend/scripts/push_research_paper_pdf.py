#!/usr/bin/env python3
import argparse
import os
import re
import sys
from pathlib import Path

import boto3  # type: ignore
try:  # pragma: no cover - optional dependency
    from botocore.config import Config
except Exception:  # pragma: no cover - optional dependency
    Config = None
from dotenv import load_dotenv  # type: ignore

load_dotenv()


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


def _normalize_project_prefix(project_prefix: str) -> str:
    raw = (project_prefix or "").strip().strip("/")
    if not raw:
        return raw
    for suffix in ("checkpoint", "code"):
        if raw.endswith(f"/{suffix}"):
            return raw[: -(len(suffix) + 1)]
    return raw


def _find_latest_research_paper_dirname(s3_client, bucket_name: str, normalized_prefix: str) -> str | None:
    checkpoint_prefix = f"{normalized_prefix}/checkpoint/"
    pattern = re.compile(r"^research_paper_(\d{8})_(\d{6})$")
    latest_stamp = None
    latest_dirname = None

    paginator = s3_client.get_paginator("list_objects_v2")
    try:
        pages = paginator.paginate(
            Bucket=bucket_name,
            Prefix=checkpoint_prefix,
            Delimiter="/",
        )
        for page in pages:
            for prefix_info in page.get("CommonPrefixes", []):
                full_prefix = (prefix_info.get("Prefix") or "").strip("/")
                if not full_prefix.startswith(checkpoint_prefix.rstrip("/")):
                    continue
                dirname = full_prefix[len(checkpoint_prefix.rstrip("/")) + 1 :]
                match = pattern.match(dirname)
                if not match:
                    continue
                stamp = f"{match.group(1)}_{match.group(2)}"
                if latest_stamp is None or stamp > latest_stamp:
                    latest_stamp = stamp
                    latest_dirname = dirname
    except Exception as exc:
        raise RuntimeError(f"failed to inspect checkpoint prefix in S3: {exc}") from exc

    return latest_dirname


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload a research paper PDF to an S3 project prefix.",
    )
    parser.add_argument(
        "project_prefix",
        help="Project prefix (e.g., 'checkpoints/project-abc/round_0' or 's3://bucket/checkpoints/project-abc/round_0')",
    )
    parser.add_argument(
        "pdf_path",
        help="Path to a PDF file to upload",
    )
    parser.add_argument(
        "--research-paper-dirname",
        dest="research_paper_dirname",
        help=(
            "Existing research_paper_* directory name "
            "(e.g., research_paper_20260226_111802). "
            "If omitted, the latest matching research_paper_* directory "
            "already present in S3 is used automatically."
        ),
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

    bucket_name = os.environ.get("S3_BUCKET_NAME")
    if not bucket_name:
        print("error: S3_BUCKET_NAME must be set (env or via s3://bucket/prefix)", file=sys.stderr)
        return 2

    pdf_path = Path(args.pdf_path).expanduser()
    if not pdf_path.exists() or not pdf_path.is_file():
        print(f"error: PDF path does not exist: {pdf_path}", file=sys.stderr)
        return 2
    if pdf_path.suffix.lower() != ".pdf":
        print("error: pdf_path must point to a .pdf file", file=sys.stderr)
        return 2

    normalized_prefix = _normalize_project_prefix(prefix)
    if not normalized_prefix:
        print("error: project-prefix cannot be empty", file=sys.stderr)
        return 2

    resource_config = Config(max_pool_connections=4) if Config else None
    s3 = boto3.client("s3", config=resource_config) if resource_config else boto3.client("s3")

    dirname = (args.research_paper_dirname or "").strip().strip("/")
    if dirname:
        if not dirname.startswith("research_paper_"):
            print("error: research_paper_dirname must start with 'research_paper_'", file=sys.stderr)
            return 2
    else:
        try:
            dirname = _find_latest_research_paper_dirname(s3, bucket_name, normalized_prefix) or ""
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if not dirname:
            print(
                "error: no research_paper_* directory found in S3 under "
                f"s3://{bucket_name}/{normalized_prefix}/checkpoint/; "
                "pass --research-paper-dirname explicitly if needed",
                file=sys.stderr,
            )
            return 2
        print(f"Resolved latest research paper directory: {dirname}")

    s3_key = f"{normalized_prefix}/checkpoint/{dirname}/research_paper.pdf"

    print(f"Uploading {pdf_path} to s3://{bucket_name}/{s3_key} ...")
    s3.upload_file(str(pdf_path), bucket_name, s3_key)
    print("Upload complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
