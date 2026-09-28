#!/usr/bin/env python3
"""Build the deterministic macOS arm64 installation release asset."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from release_builder import InstallArtifactError, build_install_artifact  # noqa: E402
from release_builder.install_artifact import write_public_installer


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the deterministic DiscoveryAgent macOS install artifact."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPOSITORY_ROOT / "dist",
    )
    parser.add_argument("--source-dir", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--release-tag", help="Also emit an install.sh bound to this exact release asset")
    options = parser.parse_args(arguments)
    if not options.output_dir.expanduser().is_absolute():
        parser.error("--output-dir must be an absolute path")
    try:
        artifact = build_install_artifact(options.source_dir, options.output_dir)
        if options.release_tag:
            write_public_installer(options.source_dir / "install.sh", artifact, options.release_tag)
    except InstallArtifactError as exc:
        print(f"artifact build failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "artifact": str(artifact.archive),
                "manifest": str(artifact.manifest),
                "release_id": artifact.release_id,
                "sha256": artifact.sha256,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
