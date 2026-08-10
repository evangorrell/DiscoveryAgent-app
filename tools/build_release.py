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
        description="Build application/ from explicit committed frontend/backend sources."
    )
    parser.add_argument("--frontend-repo", type=Path, required=True)
    parser.add_argument("--frontend-sha", required=True)
    parser.add_argument("--backend-repo", type=Path, required=True)
    parser.add_argument("--backend-sha", required=True)
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
            SourceRequest(arguments.frontend_repo, arguments.frontend_sha),
            SourceRequest(arguments.backend_repo, arguments.backend_sha),
            mode=arguments.mode,
            destination_root=REPOSITORY_ROOT,
        )
    except ReleaseBuildError as exc:
        print(f"release build failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "release_id": metadata["release_id"],
        "frontend_commit": metadata["frontend"]["commit"],
        "backend_commit": metadata["backend"]["commit"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
