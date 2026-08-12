"""Safe, transactional macOS login-shell PATH integration."""

from __future__ import annotations

import os
import pwd
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from .errors import BootstrapError
from .state import CliState


PATH_MARKER_BEGIN = "# >>> DiscoveryAgent managed PATH >>>"
PATH_EXPORT = 'export PATH="$HOME/.local/bin:$PATH"'
PATH_MARKER_END = "# <<< DiscoveryAgent managed PATH <<<"
PATH_STANZA = (
    f"{PATH_MARKER_BEGIN}\n{PATH_EXPORT}\n{PATH_MARKER_END}\n"
).encode("utf-8")

LoginShellRunner = Callable[..., subprocess.CompletedProcess[str]]
_PATH_OUTPUT_MARKER = "__DISCOVERYAGENT_LOGIN_PATH__="


def configured_login_shell() -> Path:
    try:
        shell = pwd.getpwuid(os.getuid()).pw_shell
    except (KeyError, OSError) as exc:
        raise BootstrapError("Could not determine the current user's login shell") from exc
    if not shell:
        raise BootstrapError("The current user account has no configured login shell")
    return Path(shell)


def select_login_profile(home: Path, shell: Path) -> Path:
    name = shell.name
    if name == "zsh":
        return home / ".zprofile"
    if name == "bash":
        for filename in (".bash_profile", ".bash_login"):
            candidate = home / filename
            if os.path.lexists(candidate):
                return candidate
        return home / ".profile"
    raise BootstrapError(
        f"DiscoveryAgent does not support PATH setup for login shell {shell}"
    )


def _validated_profile_contents(profile: Path, *, uid: int) -> bytes:
    if not os.path.lexists(profile):
        return b""
    try:
        metadata = profile.lstat()
    except OSError as exc:
        raise BootstrapError(f"Could not inspect shell profile {profile}: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise BootstrapError(f"Refusing to modify symlinked shell profile: {profile}")
    if not stat.S_ISREG(metadata.st_mode):
        raise BootstrapError(f"Shell profile is not a regular file: {profile}")
    if metadata.st_uid != uid:
        raise BootstrapError(f"Shell profile is not owned by the current user: {profile}")
    try:
        return profile.read_bytes()
    except OSError as exc:
        raise BootstrapError(f"Could not read shell profile {profile}: {exc}") from exc


def _has_exact_managed_stanza(contents: bytes, profile: Path) -> bool:
    begin = contents.count(PATH_MARKER_BEGIN.encode("utf-8"))
    end = contents.count(PATH_MARKER_END.encode("utf-8"))
    exact = contents.count(PATH_STANZA)
    if begin == end == exact == 0:
        return False
    if begin == end == exact == 1:
        return True
    raise BootstrapError(
        f"DiscoveryAgent PATH markers are incomplete or corrupted in {profile}"
    )


def future_login_path(
    shell: Path,
    *,
    home: Path,
    environ: Mapping[str, str] | None = None,
    runner: LoginShellRunner = subprocess.run,
) -> tuple[Path, ...]:
    environment = dict(os.environ if environ is None else environ)
    environment["HOME"] = str(home)
    environment.pop("ZDOTDIR", None)
    try:
        result = runner(
            [
                str(shell),
                "-l",
                "-i",
                "-c",
                f'printf "\\n{_PATH_OUTPUT_MARKER}%s\\n" "$PATH"',
            ],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise BootstrapError(
            f"Could not inspect the future {shell.name} login PATH: {detail.strip()}"
        ) from exc
    path_output = next(
        (
            line.removeprefix(_PATH_OUTPUT_MARKER)
            for line in reversed(result.stdout.splitlines())
            if line.startswith(_PATH_OUTPUT_MARKER)
        ),
        None,
    )
    if path_output is None:
        raise BootstrapError(
            f"Could not identify the future {shell.name} login PATH"
        )
    return tuple(
        Path(component).expanduser()
        for component in path_output.split(os.pathsep)
        if component
    )


def future_login_path_contains(
    directory: Path,
    *,
    shell: Path | None = None,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    runner: LoginShellRunner = subprocess.run,
) -> bool:
    selected_home = (Path.home() if home is None else home).expanduser().resolve()
    selected_shell = configured_login_shell() if shell is None else shell
    wanted = directory.expanduser().resolve()
    return any(
        candidate.resolve() == wanted
        for candidate in future_login_path(
            selected_shell,
            home=selected_home,
            environ=environ,
            runner=runner,
        )
    )


def _write_profile_atomic(profile: Path, contents: bytes, *, mode: int) -> None:
    profile.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{profile.name}.", suffix=".tmp", dir=profile.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, profile)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class PathIntegrationChange:
    cli_state: CliState
    profile: Path | None = None
    previous_contents: bytes | None = None
    installed_contents: bytes | None = None
    previous_mode: int = 0o600
    applied: bool = False

    def rollback(self) -> None:
        if not self.applied or self.profile is None or self.installed_contents is None:
            return
        try:
            current = self.profile.read_bytes()
        except FileNotFoundError:
            current = None
        if current != self.installed_contents:
            raise BootstrapError(
                f"Shell profile changed during installation; refusing rollback: {self.profile}"
            )
        if self.previous_contents is None:
            self.profile.unlink(missing_ok=True)
        else:
            _write_profile_atomic(
                self.profile,
                self.previous_contents,
                mode=self.previous_mode,
            )
        self.applied = False


def configure_cli_path(
    cli_path: Path,
    *,
    home: Path | None = None,
    shell: Path | None = None,
    uid: int | None = None,
    environ: Mapping[str, str] | None = None,
    runner: LoginShellRunner = subprocess.run,
) -> PathIntegrationChange:
    selected_home = (Path.home() if home is None else home).expanduser().resolve()
    canonical_cli_directory = selected_home / ".local" / "bin"
    resolved_cli = cli_path.expanduser().resolve()
    if resolved_cli.parent != canonical_cli_directory:
        return PathIntegrationChange(
            CliState(str(resolved_cli), False, None)
        )

    selected_shell = configured_login_shell() if shell is None else shell
    selected_uid = os.getuid() if uid is None else uid
    profile = select_login_profile(selected_home, selected_shell)
    profile_existed = os.path.lexists(profile)
    contents = _validated_profile_contents(profile, uid=selected_uid)
    if _has_exact_managed_stanza(contents, profile):
        return PathIntegrationChange(
            CliState(str(resolved_cli), True, str(profile))
        )
    if future_login_path_contains(
        canonical_cli_directory,
        shell=selected_shell,
        home=selected_home,
        environ=environ,
        runner=runner,
    ):
        return PathIntegrationChange(
            CliState(str(resolved_cli), False, None)
        )

    separator = b"" if not contents or contents.endswith(b"\n") else b"\n"
    installed = contents + separator + PATH_STANZA
    previous_mode = (
        stat.S_IMODE(profile.stat().st_mode) if profile_existed else 0o600
    )
    _write_profile_atomic(profile, installed, mode=previous_mode)
    return PathIntegrationChange(
        cli_state=CliState(str(resolved_cli), True, str(profile)),
        profile=profile,
        previous_contents=contents if profile_existed else None,
        installed_contents=installed,
        previous_mode=previous_mode,
        applied=True,
    )


@dataclass(frozen=True)
class PathRemovalResult:
    removed: bool
    warning: str | None = None


@dataclass
class PreparedPathRemoval:
    profile: Path | None = None
    expected: Path | None = None
    replacement: Path | None = None
    warning: str | None = None

    def cleanup(self) -> None:
        for path in (self.expected, self.replacement):
            if path is not None:
                path.unlink(missing_ok=True)


def _prepared_profile_file(profile: Path, contents: bytes, *, mode: int, label: str) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{profile.name}.discoveryagent-{label}.", dir=profile.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def prepare_managed_cli_path_removal(
    cli_state: CliState,
    *,
    uid: int | None = None,
) -> PreparedPathRemoval:
    """Prepare same-filesystem profile files without changing the live profile."""
    if not cli_state.path_change_added:
        return PreparedPathRemoval()
    if not cli_state.modified_profile:
        return PreparedPathRemoval(
            warning="the managed PATH entry was left in place because its profile is not recorded"
        )
    profile = Path(cli_state.modified_profile)
    selected_uid = os.getuid() if uid is None else uid
    try:
        contents = _validated_profile_contents(profile, uid=selected_uid)
        if not _has_exact_managed_stanza(contents, profile):
            return PreparedPathRemoval(
                warning=f"the managed PATH entry was left in place because {profile} changed"
            )
        mode = stat.S_IMODE(profile.stat().st_mode)
        expected = _prepared_profile_file(profile, contents, mode=mode, label="expected")
        try:
            replacement = _prepared_profile_file(
                profile,
                contents.replace(PATH_STANZA, b"", 1),
                mode=mode,
                label="replacement",
            )
        except Exception:
            expected.unlink(missing_ok=True)
            raise
    except (BootstrapError, OSError) as exc:
        return PreparedPathRemoval(
            warning=f"the managed PATH entry was left in place: {exc}"
        )
    return PreparedPathRemoval(profile, expected, replacement)


def remove_managed_cli_path(
    cli_state: CliState,
    *,
    uid: int | None = None,
) -> PathRemovalResult:
    """Remove only the exact recorded stanza, preserving unsafe profiles."""
    prepared = prepare_managed_cli_path_removal(cli_state, uid=uid)
    if prepared.warning:
        return PathRemovalResult(False, prepared.warning)
    if prepared.profile is None or prepared.expected is None or prepared.replacement is None:
        return PathRemovalResult(False)
    try:
        if prepared.profile.read_bytes() != prepared.expected.read_bytes():
            return PathRemovalResult(
                False,
                f"the managed PATH entry was left in place because {prepared.profile} changed",
            )
        os.replace(prepared.replacement, prepared.profile)
        prepared.replacement = None
    except OSError as exc:
        return PathRemovalResult(
            False,
            f"the managed PATH entry was left in place: {exc}",
        )
    finally:
        prepared.cleanup()
    return PathRemovalResult(True)
