"""Packaged DiscoveryAgent frontend launch orchestration."""

from __future__ import annotations

from .terminal import local_url_link

import os
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterator, Mapping, TextIO
from urllib.parse import urlsplit

from .browser_presence import BrowserPresence, detect_browser_presence
from .errors import LaunchRuntimeError
from .frontend_process import (
    FrontendOutputCapture,
    readiness_is_healthy,
    terminate_frontend,
    wait_for_readiness,
    wait_for_startup,
)
from .installation import ValidatedLaunch
from .onboarding import (
    paperclip_auth_available_locally,
    plain_text_input,
    resolve_anthropic_api_key,
    resolve_data_root,
    run_paperclip_login,
    secure_api_key_input,
    standard_input_is_interactive,
    verify_paperclip_login,
)
from .paths import InstallationPaths
from .runtime_state import (
    FRONTEND_RECORD_SCHEMA_VERSION,
    FrontendLaunchLock,
    FrontendRuntimeRecord,
    load_frontend_record,
    process_is_alive,
    remove_frontend_record,
    remove_frontend_record_if_owned,
    write_frontend_record_atomic,
)
from .state import ManagerState, load_state, write_state_atomic
from .installation import validate_launch_installation


INTERNAL_NO_BROWSER_ENV = "DISCOVERYAGENT_INTERNAL_NO_BROWSER"
STARTUP_TIMEOUT = 30.0
READINESS_TIMEOUT = 30.0
BROWSER_OPEN_GRACE_SECONDS = 15.0


class ManagerTerminationRequested(Exception):
    """Internal control flow for a manager SIGTERM."""


@dataclass(frozen=True)
class LaunchDependencies:
    popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen
    health_probe: Callable[[str], bool] = readiness_is_healthy
    browser_open: Callable[..., bool] = webbrowser.open
    browser_presence: Callable[[str], BrowserPresence] = detect_browser_presence
    pid_alive: Callable[[int], bool] = process_is_alive
    capture_factory: Callable[..., FrontendOutputCapture] = FrontendOutputCapture
    lock_factory: Callable[..., FrontendLaunchLock] = FrontendLaunchLock
    terminate: Callable[..., None] = terminate_frontend
    wall_time: Callable[[], float] = time.time
    secure_input: Callable[[str], str] = secure_api_key_input
    text_input: Callable[[str], str] = plain_text_input
    input_is_interactive: Callable[[], bool] = standard_input_is_interactive
    paperclip_auth_available: Callable[[Mapping[str, str]], bool] = (
        paperclip_auth_available_locally
    )
    onboarding_run: Callable[..., subprocess.CompletedProcess] = subprocess.run


_REDIRECTING_ENVIRONMENT = {
    "DISCOVERYAGENT_DATA_DIR",
    "DISCOVERYAGENT_BACKEND_ROOT",
    "DISCOVERYAGENT_BACKEND_PYTHON",
    "DISCOVERYAGENT_INTERNAL_INSTALL_ROOT",
    "DISCOVERY_AGENT_HOST",
    "DISCOVERY_AGENT_PORT",
    "PYTHONPATH",
    "PYTHONHOME",
    "VIRTUAL_ENV",
    "UV_PROJECT_ENVIRONMENT",
}

_EXTERNAL_CLAUDE_OVERRIDES = {
    "CLAUDE_CODE_CLI_PATH",
    "NODE_PATH",
    "WRITING_AGENT_CLI_PATH",
    "WRITING_AGENT_USE_USER_CLI",
}


def frontend_command(validated: ValidatedLaunch) -> list[str]:
    return [str(validated.frontend_python), "-m", "da_selection_ui", "--port", "0"]


def build_frontend_environment(
    validated: ValidatedLaunch,
    inherited: Mapping[str, str],
) -> dict[str, str]:
    environment = dict(inherited)
    for name in _REDIRECTING_ENVIRONMENT | _EXTERNAL_CLAUDE_OVERRIDES:
        environment.pop(name, None)
    environment.pop(INTERNAL_NO_BROWSER_ENV, None)
    environment["DISCOVERYAGENT_DATA_DIR"] = str(validated.data_root)
    environment["DISCOVERYAGENT_BACKEND_ROOT"] = str(validated.release.backend_root)
    environment["DISCOVERYAGENT_BACKEND_PYTHON"] = str(validated.backend_python)
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def _record_urls_are_loopback(record: FrontendRuntimeRecord) -> bool:
    try:
        parsed = urlsplit(record.url)
        readiness = urlsplit(record.readiness_url)
        host = parsed.hostname
        if parsed.scheme != "http" or readiness.scheme != "http" or host is None:
            return False
        if host not in {"localhost", "127.0.0.1", "::1"}:
            return False
        if parsed.port is None or readiness.hostname != host or readiness.port != parsed.port:
            return False
        if parsed.path != "/" or readiness.path != "/api/health":
            return False
        if parsed.query or parsed.fragment or readiness.query or readiness.fragment:
            return False
        return True
    except ValueError:
        return False


def existing_frontend_is_healthy(
    record: FrontendRuntimeRecord,
    *,
    active_release_id: str,
    pid_alive: Callable[[int], bool],
    health_probe: Callable[[str], bool],
) -> bool:
    return (
        record.active_release_id == active_release_id
        and _record_urls_are_loopback(record)
        and pid_alive(record.pid)
        and health_probe(record.readiness_url)
    )


def open_frontend_url(
    url: str,
    *,
    manager_environment: Mapping[str, str],
    browser_open: Callable[..., bool],
    stderr: TextIO,
) -> bool:
    if manager_environment.get(INTERNAL_NO_BROWSER_ENV) == "1":
        return False
    try:
        opened = browser_open(url, new=2)
    except Exception as exc:
        print(f"DiscoveryAgent is ready, but the browser could not be opened: {exc}", file=stderr)
        return False
    if not opened:
        print("DiscoveryAgent is ready, but the browser did not open automatically.", file=stderr)
        return False
    return True


def existing_frontend_needs_browser(
    url: str,
    *,
    presence_probe: Callable[[str], BrowserPresence],
    browser_opened_at: float | None,
    now: float,
) -> bool:
    try:
        presence = presence_probe(url)
    except Exception:
        return False
    if presence is not BrowserPresence.ABSENT:
        return False
    if browser_opened_at is None:
        return True
    age = now - browser_opened_at
    return not 0.0 <= age < BROWSER_OPEN_GRACE_SECONDS


@contextmanager
def manager_termination_handler(platform_name: str) -> Iterator[None]:
    """Convert macOS SIGTERM into cleanup-aware manager control flow."""
    if (
        platform_name != "darwin"
        or not hasattr(signal, "SIGTERM")
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return
    previous = signal.getsignal(signal.SIGTERM)

    def request_shutdown(_signum, _frame):
        raise ManagerTerminationRequested()

    signal.signal(signal.SIGTERM, request_shutdown)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _load_or_remove_runtime_record(path: Path) -> FrontendRuntimeRecord | None:
    try:
        return load_frontend_record(path)
    except LaunchRuntimeError:
        remove_frontend_record(path)
        return None


def launch_installed_application(
    paths: InstallationPaths,
    *,
    manager_environment: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    dependencies: LaunchDependencies | None = None,
    startup_timeout: float = STARTUP_TIMEOUT,
    readiness_timeout: float = READINESS_TIMEOUT,
) -> int:
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    inherited = os.environ if manager_environment is None else manager_environment
    selected = dependencies or LaunchDependencies()
    state: ManagerState = load_state(paths.state_file)

    # Confirm the data root before validation creates it, and before anything
    # else can write below it.
    chosen_data_root = resolve_data_root(
        Path(state.data_root).expanduser(),
        install_root=paths.root,
        interactive=selected.input_is_interactive,
        text_input=selected.text_input,
        stdout=output,
    )
    if str(chosen_data_root) != state.data_root:
        state = replace(state, data_root=str(chosen_data_root))
        write_state_atomic(paths.state_file, state)

    validated = validate_launch_installation(paths, state, platform_name=state.platform)
    paths.frontend_cwd.mkdir(parents=True, exist_ok=True)
    process: subprocess.Popen[str] | None = None
    capture: FrontendOutputCapture | None = None
    owned_record: FrontendRuntimeRecord | None = None
    interrupted_exit: int | None = None

    try:
        with manager_termination_handler(state.platform):
            lock = selected.lock_factory(
                paths.frontend_launch_lock,
                pid_alive=selected.pid_alive,
            )
            with lock:
                existing = _load_or_remove_runtime_record(paths.frontend_runtime_file)
                if existing is not None:
                    if existing_frontend_is_healthy(
                        existing,
                        active_release_id=state.active_release_id,
                        pid_alive=selected.pid_alive,
                        health_probe=selected.health_probe,
                    ):
                        if existing_frontend_needs_browser(
                            existing.url,
                            presence_probe=selected.browser_presence,
                            browser_opened_at=existing.browser_opened_at,
                            now=selected.wall_time(),
                        ):
                            if open_frontend_url(
                                existing.url,
                                manager_environment=inherited,
                                browser_open=selected.browser_open,
                                stderr=errors,
                            ):
                                existing = replace(
                                    existing,
                                    browser_opened_at=selected.wall_time(),
                                )
                                write_frontend_record_atomic(
                                    paths.frontend_runtime_file, existing
                                )
                        print(f"DiscoveryAgent is already running at {local_url_link(existing.url, output, inherited)}", file=output)
                        return 0
                    remove_frontend_record(paths.frontend_runtime_file)

                paperclip_ready = selected.paperclip_auth_available(inherited)
                key_ready = bool(str(inherited.get("ANTHROPIC_API_KEY") or "").strip())
                if not key_ready or not paperclip_ready:
                    print("Welcome to DiscoveryAgent\n", file=output, flush=True)
                launch_environment, api_key, _prompted = resolve_anthropic_api_key(
                    inherited,
                    interactive=selected.input_is_interactive,
                    secure_input=selected.secure_input,
                    stdout=output,
                )
                if not paperclip_ready:
                    print("\nConnecting Paperclip...\n", file=output)
                    print(
                        "Your browser will open to complete sign-in.\n",
                        file=output,
                        flush=True,
                    )
                    run_paperclip_login(
                        validated,
                        launch_environment,
                        runner=selected.onboarding_run,
                    )
                    verify_paperclip_login(
                        validated,
                        launch_environment,
                        runner=selected.onboarding_run,
                    )
                    print("✓ Paperclip connected\n", file=output)

                print("Launching DiscoveryAgent...", file=output, flush=True)
                child_environment = build_frontend_environment(
                    validated, launch_environment
                )
                command = frontend_command(validated)
                try:
                    process = selected.popen(
                        command,
                        cwd=paths.frontend_cwd,
                        env=child_environment,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                    )
                except OSError as exc:
                    raise LaunchRuntimeError(f"Could not start packaged frontend: {exc}") from exc
                if process.stdout is None:
                    raise LaunchRuntimeError("Frontend output capture was not created")
                capture = selected.capture_factory(
                    process.stdout,
                    paths.frontend_log_file,
                    secrets=(api_key,),
                )
                startup = wait_for_startup(
                    process,
                    capture,
                    timeout=startup_timeout,
                )
                wait_for_readiness(
                    process,
                    capture,
                    startup,
                    health_probe=selected.health_probe,
                    timeout=readiness_timeout,
                )
                owned_record = FrontendRuntimeRecord(
                    schema_version=FRONTEND_RECORD_SCHEMA_VERSION,
                    pid=startup.pid,
                    active_release_id=state.active_release_id,
                    url=startup.url,
                    readiness_url=startup.readiness_url,
                    started_at=selected.wall_time(),
                )
                write_frontend_record_atomic(paths.frontend_runtime_file, owned_record)
                if open_frontend_url(
                    startup.url,
                    manager_environment=inherited,
                    browser_open=selected.browser_open,
                    stderr=errors,
                ):
                    owned_record = replace(
                        owned_record,
                        browser_opened_at=selected.wall_time(),
                    )
                    write_frontend_record_atomic(
                        paths.frontend_runtime_file, owned_record
                    )
                print(f"DiscoveryAgent is ready at {local_url_link(startup.url, output, inherited)}", file=output, flush=True)

            return_code = process.wait()
            return int(return_code or 0)
    except KeyboardInterrupt:
        interrupted_exit = 130
    except ManagerTerminationRequested:
        interrupted_exit = 143
    finally:
        if process is not None:
            if interrupted_exit is not None or process.poll() is None:
                selected.terminate(process, platform_name=state.platform)
            if capture is not None:
                capture.close()
            if owned_record is not None:
                remove_frontend_record_if_owned(
                    paths.frontend_runtime_file,
                    pid=owned_record.pid,
                    active_release_id=owned_record.active_release_id,
                )
    return 0 if interrupted_exit is None else interrupted_exit
