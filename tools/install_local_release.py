#!/usr/bin/env python3
"""Install the repository's packaged release into a local macOS installation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "manager"))

from discoveryagent_cli.errors import ManagerError  # noqa: E402
from discoveryagent_cli.installer import (  # noqa: E402
    ProductionInstallConfig,
    install_production_release,
)


def _absolute_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("must be an absolute path")
    return path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Install the current packaged DiscoveryAgent release locally."
    )
    result.add_argument("--install-root", type=_absolute_path)
    result.add_argument("--cli-path", type=_absolute_path)
    result.add_argument("--data-root", type=_absolute_path)
    return result


def main(arguments: list[str] | None = None) -> int:
    options = parser().parse_args(arguments)
    try:
        installed = install_production_release(
            ProductionInstallConfig(
                payload_root=REPOSITORY_ROOT,
                install_root=options.install_root,
                cli_path=options.cli_path,
                data_root=options.data_root,
            )
        )
    except ManagerError as exc:
        print(f"DiscoveryAgent installation failed: {exc}", file=sys.stderr)
        return 1
    action = "Reused" if installed.reused_release else "Installed"
    print(f"{action} DiscoveryAgent release {installed.release.release_id}")
    print(f"Command: {installed.state.cli.path}")
    print(f"Data: {installed.state.data_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
