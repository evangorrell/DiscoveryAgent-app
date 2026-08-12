"""Safe macOS self-uninstall for the installed DiscoveryAgent manager."""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TextIO

from .bootstrap import production_wrapper_contents
from .errors import UninstallError
from .installation import load_active_release
from .maintenance import require_idle_research, stop_idle_frontend
from .path_integration import PreparedPathRemoval, prepare_managed_cli_path_removal
from .paths import InstallationPaths
from .state import ManagerState, load_state


UNINSTALL_HELPER = """#!/bin/sh
set -u
cli=$1
install_root=$2
helper_root=$3
profile=${4:-}
expected_profile=${5:-}
replacement_profile=${6:-}
failed=0

/bin/rm -f -- "$cli" || failed=1
/bin/rm -rf -- "$install_root" || failed=1
if [ -e "$cli" ] || [ -e "$install_root" ]; then
    if [ -e "$cli" ]; then
        printf '%s\n' "DiscoveryAgent uninstall failed; remaining path: $cli" >&2
    fi
    if [ -e "$install_root" ]; then
        printf '%s\n' "DiscoveryAgent uninstall failed; remaining path: $install_root" >&2
    fi
    failed=1
else
    if [ -n "$profile" ]; then
        if /usr/bin/cmp -s -- "$profile" "$expected_profile"; then
            /bin/mv -f -- "$replacement_profile" "$profile" || \
                printf '%s\n' "DiscoveryAgent warning: the managed PATH entry was left in place" >&2
        else
            printf '%s\n' "DiscoveryAgent warning: the managed PATH entry was left in place because the profile changed" >&2
        fi
    fi
    printf '\n%s\n\n' "✓ DiscoveryAgent removed"
    printf '%s\n' "Your projects and Paperclip credentials were preserved."
fi
/bin/rm -f -- "$expected_profile" "$replacement_profile"
/bin/rm -f -- "$0"
/bin/rmdir -- "$helper_root" 2>/dev/null || :
exit "$failed"
"""


def validate_uninstall_targets(paths: InstallationPaths, state: ManagerState) -> tuple[Path, Path]:
    root = paths.root.expanduser().absolute()
    data_root = Path(state.data_root).expanduser().absolute()
    home = Path.home().expanduser().absolute()
    cli = Path(state.cli.path).expanduser().absolute()
    if (
        not root.is_absolute()
        or root == Path(root.anchor)
        or root == home
        or home.is_relative_to(root)
        or root == data_root
        or data_root.is_relative_to(root)
        or root.is_relative_to(data_root)
    ):
        raise UninstallError(f"Refusing to remove suspicious install root: {root}")
    try:
        metadata = root.lstat()
    except OSError as exc:
        raise UninstallError(f"Could not inspect install root: {exc}") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        raise UninstallError(f"Refusing to remove suspicious install root: {root}")
    if not paths.state_file.is_file() or not paths.manager_root.is_dir():
        raise UninstallError("Installation ownership markers are missing")
    if not cli.is_absolute() or not os.path.lexists(cli):
        raise UninstallError("Recorded DiscoveryAgent CLI is missing")
    cli_metadata = cli.lstat()
    if (
        stat.S_ISLNK(cli_metadata.st_mode)
        or not stat.S_ISREG(cli_metadata.st_mode)
        or cli_metadata.st_uid != os.getuid()
    ):
        raise UninstallError("Recorded DiscoveryAgent CLI ownership cannot be verified")
    expected = production_wrapper_contents(
        Path(state.runtime.python_path),
        install_root=root,
    ).encode("utf-8")
    try:
        actual = cli.read_bytes()
    except OSError as exc:
        raise UninstallError(f"Could not read recorded DiscoveryAgent CLI: {exc}") from exc
    if actual != expected:
        raise UninstallError("Recorded DiscoveryAgent CLI does not belong to this installation")
    return root, cli


@dataclass(frozen=True)
class MacOSUninstallHandoff:
    execv: Callable[[str, list[str]], None] = os.execv
    temporary_parent: Path | None = None

    def __call__(
        self,
        install_root: Path,
        cli: Path,
        prepared_path: PreparedPathRemoval,
    ) -> None:
        parent = self.temporary_parent or Path(tempfile.gettempdir())
        helper_root = Path(
            tempfile.mkdtemp(prefix="discoveryagent-uninstall-", dir=parent)
        )
        helper_root.chmod(0o700)
        if helper_root == install_root or helper_root.is_relative_to(install_root):
            helper_root.rmdir()
            raise UninstallError("Could not create uninstall helper outside the installation")
        helper = helper_root / "remove-discoveryagent.sh"
        descriptor = os.open(helper, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(UNINSTALL_HELPER)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            helper.unlink(missing_ok=True)
            helper_root.rmdir()
            raise
        arguments = [
            "/bin/sh",
            str(helper),
            str(cli),
            str(install_root),
            str(helper_root),
            str(prepared_path.profile or ""),
            str(prepared_path.expected or ""),
            str(prepared_path.replacement or ""),
        ]
        try:
            self.execv("/bin/sh", arguments)
        except OSError as exc:
            prepared_path.cleanup()
            helper.unlink(missing_ok=True)
            helper_root.rmdir()
            raise UninstallError(f"Could not start the uninstall helper: {exc}") from exc
        prepared_path.cleanup()
        helper.unlink(missing_ok=True)
        helper_root.rmdir()
        raise UninstallError("Uninstall helper returned unexpectedly")


@dataclass(frozen=True)
class UninstallDependencies:
    research_guard: Callable[..., None] = require_idle_research
    frontend_stopper: Callable[[InstallationPaths, ManagerState], bool] = stop_idle_frontend
    target_validator: Callable[[InstallationPaths, ManagerState], tuple[Path, Path]] = validate_uninstall_targets
    path_preparer: Callable[[object], PreparedPathRemoval] = prepare_managed_cli_path_removal
    handoff: Callable[[Path, Path, PreparedPathRemoval], None] = MacOSUninstallHandoff()


def uninstall_installed_application(
    paths: InstallationPaths,
    *,
    stdout: TextIO,
    stderr: TextIO,
    dependencies: UninstallDependencies | None = None,
) -> int:
    selected = dependencies or UninstallDependencies()
    state = load_state(paths.state_file)
    load_active_release(paths, state, require_layout=True)
    selected.research_guard(state, action="uninstalling")
    install_root, cli = selected.target_validator(paths, state)
    selected.frontend_stopper(paths, state)
    prepared_path = selected.path_preparer(state.cli)
    if prepared_path.warning:
        print(f"DiscoveryAgent warning: {prepared_path.warning}", file=stderr)
    print("Uninstalling DiscoveryAgent...", file=stdout, flush=True)
    selected.handoff(install_root, cli, prepared_path)
    return 0
