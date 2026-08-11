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
from discoveryagent_cli.runtime import BootstrapRuntimeSource  # noqa: E402


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
    result.add_argument("--bootstrap-uv", type=_absolute_path, help=argparse.SUPPRESS)
    result.add_argument(
        "--bootstrap-python-root", type=_absolute_path, help=argparse.SUPPRESS
    )
    result.add_argument(
        "--bootstrap-python", type=_absolute_path, help=argparse.SUPPRESS
    )
    result.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)
    return result


def main(arguments: list[str] | None = None) -> int:
    argument_parser = parser()
    options = argument_parser.parse_args(arguments)
    bootstrap_values = (
        options.bootstrap_uv,
        options.bootstrap_python_root,
        options.bootstrap_python,
    )
    if any(bootstrap_values) and not all(bootstrap_values):
        argument_parser.error("bootstrap runtime arguments must be supplied together")
    bootstrap_runtime = (
        BootstrapRuntimeSource(*bootstrap_values) if all(bootstrap_values) else None
    )
    try:
        installed = install_production_release(
            ProductionInstallConfig(
                payload_root=REPOSITORY_ROOT,
                install_root=options.install_root,
                cli_path=options.cli_path,
                data_root=options.data_root,
                bootstrap_runtime=bootstrap_runtime,
            )
        )
    except ManagerError as exc:
        print(f"DiscoveryAgent installation failed: {exc}", file=sys.stderr)
        return 1
    if not options.quiet:
        action = "Reused" if installed.reused_release else "Installed"
        print(f"{action} DiscoveryAgent release {installed.release.release_id}")
        print(f"Command: {installed.state.cli.path}")
        print(f"Data: {installed.state.data_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
