#!/usr/bin/env python3
"""Refresh only manager code in an existing development installation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "manager"))

from discoveryagent_cli.bootstrap import refresh_development_manager  # noqa: E402
from discoveryagent_cli.errors import ManagerError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh manager code without rebuilding an installed release."
    )
    parser.add_argument("--install-root", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        destination = refresh_development_manager(
            REPOSITORY_ROOT, arguments.install_root
        )
    except ManagerError as exc:
        print(f"DiscoveryAgent manager-refresh error: {exc}", file=sys.stderr)
        return 1
    print(f"Refreshed installed manager at {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
