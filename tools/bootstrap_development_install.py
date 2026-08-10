#!/usr/bin/env python3
"""Construct a genuine local installation from committed app snapshots."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "manager"))

from discoveryagent_cli.bootstrap import (  # noqa: E402
    BootstrapConfig,
    bootstrap_development_install,
    default_data_root,
)
from discoveryagent_cli.errors import ManagerError  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a self-contained DiscoveryAgent development installation."
    )
    parser.add_argument(
        "--install-root",
        type=Path,
        required=True,
        help="Absolute destination for the disposable installation",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=default_data_root(),
        help="Durable DiscoveryAgent project-data root",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    try:
        result = bootstrap_development_install(
            BootstrapConfig(
                repository_root=REPOSITORY_ROOT,
                install_root=arguments.install_root,
                data_root=arguments.data_root,
            )
        )
    except ManagerError as exc:
        print(f"DiscoveryAgent bootstrap error: {exc}", file=sys.stderr)
        return 1
    print(f"Installed {result.release.release_id} at {result.paths.root}")
    print(f"uv: {result.runtimes.uv}")
    print(f"Python: {result.runtimes.python}")
    print(f"CLI: {result.state.cli.path}")
    print(f"Release reused: {'yes' if result.reused_release else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
