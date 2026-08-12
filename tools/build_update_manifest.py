#!/usr/bin/env python3
"""Build discoveryagent-update.json from an install-artifact manifest."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from release_builder import UpdateManifestBuildError, build_update_manifest  # noqa: E402


def _absolute_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("must be an absolute path")
    return path


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the stable DiscoveryAgent update manifest.")
    parser.add_argument("--artifact-manifest", type=_absolute_path, required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--artifact-url", required=True)
    parser.add_argument("--output", type=_absolute_path, required=True)
    options = parser.parse_args(arguments)
    try:
        output = build_update_manifest(
            options.artifact_manifest,
            release_tag=options.release_tag,
            artifact_url=options.artifact_url,
            output=options.output,
        )
    except UpdateManifestBuildError as exc:
        print(f"update manifest build failed: {exc}", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
