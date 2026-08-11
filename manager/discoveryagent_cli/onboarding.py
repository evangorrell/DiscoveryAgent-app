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
