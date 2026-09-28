#!/usr/bin/env python3
"""Command-line entry point for the deterministic source snapshot builder."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from release_builder import ReleaseBuildError, SourceRequest, build_release  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build application/ from this repository's committed canonical frontend "
            "and an explicit committed backend source."
        )
    )
    parser.add_argument(
        "--app-sha",
        required=True,
        help="DiscoveryAgent-app commit containing application/frontend",
    )
    parser.add_argument("--backend-repo", type=Path, required=True)
    parser.add_argument("--backend-sha", required=True)
    parser.add_argument("--output-dir", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--package-revision", type=int, default=0)
    parser.add_argument(
        "--backend-only", action="store_true",
        help="Development snapshot refresh only: preserve the existing frontend directory",
    )
    parser.add_argument(
        "--mode",
        choices=("development", "production"),
        required=True,
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    try:
        metadata = build_release(
            SourceRequest(REPOSITORY_ROOT, arguments.app_sha),
            SourceRequest(arguments.backend_repo, arguments.backend_sha),
            mode=arguments.mode,
            destination_root=arguments.output_dir,
            package_revision=arguments.package_revision,
            backend_only=arguments.backend_only,
        )
    except ReleaseBuildError as exc:
        print(f"release build failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "release_id": metadata["release_id"],
        "app_commit": metadata["frontend"]["commit"],
        "frontend_tree": metadata["frontend"]["tree"],
        "backend_commit": metadata["backend"]["commit"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
