"""Command-line interface for the stable DiscoveryAgent manager."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence, TextIO

from .errors import ActiveResearchError, ManagerError
from .installation import load_active_release
from .launch import launch_installed_application
from .paths import InstallationPaths, resolve_installation_root
from .state import ManagerState, load_state
from .uninstall import uninstall_installed_application
from .update import update_installed_application


@dataclass(frozen=True)
class CommandContext:
    paths: InstallationPaths
    stdout: TextIO
    stderr: TextIO
    environ: Mapping[str, str]


CommandHandler = Callable[[CommandContext], int]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="discoveryagent")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("launch", help="validate the installed application")
    subparsers.add_parser("version", help="show installed release information")
    subparsers.add_parser("update", help="install the latest stable release")
    subparsers.add_parser("uninstall", help="remove the application and preserve user data")
    return parser


def _load_manager_state(context: CommandContext) -> ManagerState:
    return load_state(context.paths.state_file)


def launch_command(context: CommandContext) -> int:
    return launch_installed_application(
        context.paths,
        stdout=context.stdout,
        stderr=context.stderr,
    )


def version_command(context: CommandContext) -> int:
    state = _load_manager_state(context)
    release = load_active_release(context.paths, state)
    metadata = release.metadata
    print(f"DiscoveryAgent {metadata.release_id}", file=context.stdout)
    print(f"Frontend: {metadata.frontend_commit[:12]}", file=context.stdout)
    print(f"Backend:  {metadata.backend_commit[:12]}", file=context.stdout)
    print(f"Python:   {metadata.required_cpython_version}", file=context.stdout)
    print(f"Manager:  {state.manager_version}", file=context.stdout)
    return 0


def update_command(context: CommandContext) -> int:
    try:
        return update_installed_application(
            context.paths,
            environ=context.environ,
            stdout=context.stdout,
        )
    except ActiveResearchError as exc:
        print(str(exc), file=context.stderr)
        return 1


def uninstall_command(context: CommandContext) -> int:
    try:
        return uninstall_installed_application(
            context.paths,
            stdout=context.stdout,
            stderr=context.stderr,
        )
    except ActiveResearchError as exc:
        print(str(exc), file=context.stderr)
        return 1


DEFAULT_HANDLERS: Mapping[str, CommandHandler] = {
    "launch": launch_command,
    "version": version_command,
    "update": update_command,
    "uninstall": uninstall_command,
}


def dispatch(
    argv: Sequence[str],
    context: CommandContext,
    *,
    handlers: Mapping[str, CommandHandler] | None = None,
) -> int:
    arguments = build_parser().parse_args(argv)
    command = arguments.command or "launch"
    selected_handlers = DEFAULT_HANDLERS if handlers is None else handlers
    return selected_handlers[command](context)


def main(
    argv: Sequence[str] | None = None,
    *,
    install_root: str | Path | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
    handlers: Mapping[str, CommandHandler] | None = None,
) -> int:
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    paths = InstallationPaths(resolve_installation_root(install_root))
    context = CommandContext(
        paths=paths,
        stdout=output,
        stderr=errors,
        environ=os.environ if environ is None else environ,
    )
    try:
        return dispatch(
            sys.argv[1:] if argv is None else argv,
            context,
            handlers=handlers,
        )
    except ManagerError as exc:
        print(f"DiscoveryAgent error: {exc}", file=errors)
        return 1
