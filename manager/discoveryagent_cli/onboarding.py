"""First-use Anthropic and Paperclip onboarding for packaged launches."""

from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping, TextIO

from .errors import LaunchRuntimeError
from .installation import ValidatedLaunch


ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"
PAPERCLIP_CREDENTIAL_ENVIRONMENTS = (
    "PAPERCLIP_BEARER_TOKEN",
    "PAPERCLIP_API_KEY",
)
PAPERCLIP_VERIFY_CODE = (
    "from gxl_paperclip import PaperclipClient; "
    "health = PaperclipClient.from_env(timeout=15).health(timeout=15); "
    "raise SystemExit(0 if health.reachable and health.exit_code == 0 else 1)"
)


def standard_input_is_interactive() -> bool:
    return bool(sys.stdin.isatty())


def secure_api_key_input(prompt: str) -> str:
    return getpass.getpass(prompt)


def plain_text_input(prompt: str) -> str:
    return input(prompt)


DATA_ROOT_ATTEMPTS = 3


def describe_data_root_choice(candidate: Path) -> str | None:
    """Reject a data root that cannot hold projects, before anything is written.

    Returns a human-readable reason, or None when the location is usable. An
    existing directory is deliberately allowed: DiscoveryAgent only ever adds
    its own subdirectories, and never removes or rewrites what is already there.
    """
    if not candidate.is_absolute():
        return "Enter an absolute path, for example ~/DiscoveryAgent."
    if os.path.lexists(candidate) and not candidate.is_dir():
        return f"{candidate} already exists and is not a folder."
    existing_parent = candidate
    while not existing_parent.exists() and existing_parent != existing_parent.parent:
        existing_parent = existing_parent.parent
    if not os.access(existing_parent, os.W_OK):
        return f"{existing_parent} is not writable."
    return None


def resolve_data_root(
    configured: Path,
    *,
    install_root: Path,
    interactive: Callable[[], bool],
    text_input: Callable[[str], str],
    stdout: TextIO,
) -> Path:
    """Confirm where projects are kept, once, before the first one exists.

    The installer records a default rather than interrogating a `curl | sh`
    pipeline. An existing data root means the scientist already has projects
    there, so it is never second-guessed; only a first launch asks.
    """
    if configured.exists():
        return configured
    if not interactive():
        return configured

    print("Where should DiscoveryAgent keep your projects and data?\n", file=stdout)
    print(
        "This folder holds every project you create, including the data folders\n"
        "you copy files into. Pick somewhere you can reach easily.\n",
        file=stdout,
        flush=True,
    )
    for _attempt in range(DATA_ROOT_ATTEMPTS):
        try:
            entered = text_input(f"Path [{configured}]: ")
        except (EOFError, OSError):
            return configured
        raw = str(entered or "").strip()
        if not raw:
            return configured
        candidate = Path(raw).expanduser()
        reason = describe_data_root_choice(candidate)
        if reason is None:
            candidate = candidate.absolute()
            if candidate == install_root or candidate.is_relative_to(install_root):
                reason = (
                    "That is inside DiscoveryAgent's own program folder, which is "
                    "replaced on every update."
                )
            elif install_root.is_relative_to(candidate):
                reason = "That folder contains DiscoveryAgent's own program files."
        if reason is None:
            try:
                already_populated = candidate.is_dir() and any(candidate.iterdir())
            except OSError:
                already_populated = False
            if already_populated:
                print(
                    f"\nUsing the existing folder {candidate}. DiscoveryAgent only "
                    "adds new files there; nothing already in it is moved, changed, "
                    "or deleted.",
                    file=stdout,
                )
            print(f"\n✓ Projects will be kept in {candidate}\n", file=stdout)
            return candidate
        print(f"\n{reason}\n", file=stdout, flush=True)

    print(f"Keeping the default: {configured}\n", file=stdout)
    return configured


def _configured_path(raw: str, environment: Mapping[str, str]) -> Path:
    if raw == "~" or raw.startswith(f"~{os.sep}"):
        home = environment.get("HOME", "").strip()
        if home:
            suffix = raw[2:] if raw != "~" else ""
            return Path(home) / suffix
    return Path(raw).expanduser()


def paperclip_credentials_path(environment: Mapping[str, str]) -> Path:
    configured = environment.get("PAPERCLIP_CONFIG_DIR", "").strip()
    if configured:
        return _configured_path(configured, environment) / "credentials.json"
    home = environment.get("HOME", "").strip()
    root = Path(home) if home else Path.home()
    return root / ".paperclip" / "credentials.json"


def paperclip_auth_available_locally(environment: Mapping[str, str]) -> bool:
    """Recognize locally usable auth without refreshing or contacting Paperclip."""
    if any(
        str(environment.get(name) or "").strip()
        for name in PAPERCLIP_CREDENTIAL_ENVIRONMENTS
    ):
        return True
    path = paperclip_credentials_path(environment)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and bool(
        str(payload.get("refresh_token") or "").strip()
    )


def resolve_anthropic_api_key(
    environment: Mapping[str, str],
    *,
    interactive: Callable[[], bool],
    secure_input: Callable[[str], str],
    stdout: TextIO,
) -> tuple[dict[str, str], str, bool]:
    """Return a process-local launch environment and its Anthropic key."""
    launch_environment = dict(environment)
    inherited = str(launch_environment.get(ANTHROPIC_API_KEY_ENV) or "")
    if inherited.strip():
        return launch_environment, inherited, False
    if not interactive():
        raise LaunchRuntimeError(
            "Anthropic API key setup needs an interactive terminal. Set "
            "ANTHROPIC_API_KEY in your environment and run discoveryagent again."
        )

    print("Anthropic API key", file=stdout)
    print("Enter your API key:", file=stdout, flush=True)
    try:
        entered = secure_input("> ")
    except (EOFError, OSError) as exc:
        raise LaunchRuntimeError(
            "Secure Anthropic API key input was unavailable. Set "
            "ANTHROPIC_API_KEY in your environment and run discoveryagent again."
        ) from exc
    key = str(entered or "").strip()
    if not key:
        raise LaunchRuntimeError(
            "No Anthropic API key was entered. Run discoveryagent again, or set "
            "ANTHROPIC_API_KEY in your environment."
        )
    launch_environment[ANTHROPIC_API_KEY_ENV] = key
    print("\n✓ API key available for this session", file=stdout)
    return launch_environment, key, True


def paperclip_subprocess_environment(
    validated: ValidatedLaunch,
    environment: Mapping[str, str],
) -> dict[str, str]:
    """Use the backend environment while preserving Paperclip-owned config lookup."""
    child = dict(environment)
    for name in (
        ANTHROPIC_API_KEY_ENV,
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "VIRTUAL_ENV_PROMPT",
        "UV_PROJECT_ENVIRONMENT",
    ):
        child.pop(name, None)

    backend_bin = validated.backend_python.parent
    path_entries = [
        entry
        for entry in child.get("PATH", "").split(os.pathsep)
        if entry and entry != str(backend_bin)
    ]
    child["PATH"] = os.pathsep.join([str(backend_bin), *path_entries])
    backend_environment = backend_bin.parent
    if (backend_environment / "pyvenv.cfg").is_file():
        child["VIRTUAL_ENV"] = str(backend_environment)
    child["PYTHONNOUSERSITE"] = "1"
    return child


def paperclip_login_command(validated: ValidatedLaunch) -> list[str]:
    return [
        str(validated.backend_python),
        "-m",
        "gxl_paperclip",
        "login",
        "--no-install",
    ]


def paperclip_verify_command(validated: ValidatedLaunch) -> list[str]:
    return [str(validated.backend_python), "-c", PAPERCLIP_VERIFY_CODE]


def run_paperclip_login(
    validated: ValidatedLaunch,
    environment: Mapping[str, str],
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> None:
    try:
        result = runner(
            paperclip_login_command(validated),
            cwd=validated.release.backend_root,
            env=paperclip_subprocess_environment(validated, environment),
            check=False,
        )
    except OSError as exc:
        raise LaunchRuntimeError(
            "Paperclip sign-in could not be started. Run discoveryagent again to retry."
        ) from exc
    if result.returncode != 0:
        raise LaunchRuntimeError(
            "Paperclip sign-in did not complete. Run discoveryagent again to retry."
        )


def verify_paperclip_login(
    validated: ValidatedLaunch,
    environment: Mapping[str, str],
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> None:
    try:
        result = runner(
            paperclip_verify_command(validated),
            cwd=validated.release.backend_root,
            env=paperclip_subprocess_environment(validated, environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LaunchRuntimeError(
            "Paperclip sign-in could not be verified. Run discoveryagent again to retry."
        ) from exc
    if result.returncode != 0:
        raise LaunchRuntimeError(
            "Paperclip sign-in could not be verified. Run discoveryagent again to retry."
        )
