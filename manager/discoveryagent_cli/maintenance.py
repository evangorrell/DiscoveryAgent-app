"""Shared read-only research safety and idle-frontend maintenance."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable

from .errors import ActiveResearchError, LaunchRuntimeError, MaintenanceError
from .frontend_process import readiness_is_healthy
from .launch import existing_frontend_is_healthy
from .paths import InstallationPaths
from .runtime_state import (
    load_frontend_record,
    process_is_alive,
    remove_frontend_record_if_owned,
)
from .state import ManagerState


ACTIVE_ROUND_STATUSES = frozenset({"starting", "running"})


class ProcessIdentity(Enum):
    DEAD = "dead"
    MATCH = "match"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class ResearchActivity(Enum):
    IDLE = "idle"
    ACTIVE = "active"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class ResearchInspection:
    activity: ResearchActivity
    state_paths: tuple[Path, ...]
    detail: str | None = None


ProcessProbe = Callable[[int, str, str, str], ProcessIdentity]


def inspect_process_identity(
    pid: int,
    config_file: str,
    launcher: str,
    platform_name: str,
    *,
    pid_alive: Callable[[int], bool] = process_is_alive,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ProcessIdentity:
    """Match the frontend's PID/command identity check without importing it."""
    if not pid_alive(pid):
        return ProcessIdentity.DEAD
    if platform_name != "darwin":
        return ProcessIdentity.UNKNOWN
    try:
        result = runner(
            ["/bin/ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except (OSError, subprocess.SubprocessError):
        return ProcessIdentity.UNKNOWN
    if result.returncode != 0 or not result.stdout.strip():
        return ProcessIdentity.DEAD if not pid_alive(pid) else ProcessIdentity.UNKNOWN
    command = result.stdout.strip()

    def listed_argument(value: str) -> bool:
        start = 0
        while True:
            index = command.find(value, start)
            if index < 0:
                return False
            end = index + len(value)
            before = index == 0 or command[index - 1].isspace() or command[index - 1] in "'\""
            after = end == len(command) or command[end].isspace() or command[end] in "'\""
            if before and after:
                return True
            start = index + 1

    return (
        ProcessIdentity.MATCH
        if listed_argument(launcher) and listed_argument(config_file)
        else ProcessIdentity.MISMATCH
    )


def _registry_projects(path: Path) -> tuple[list[dict], bool]:
    if not path.exists():
        return [], False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], True
    projects = payload.get("projects") if isinstance(payload, dict) else None
    if not isinstance(projects, list):
        return [], True
    return [value for value in projects if isinstance(value, dict)], False


def _record_root(record: dict, *, default_root: Path) -> Path | None:
    project_id = record.get("id")
    if not isinstance(project_id, str) or not project_id or Path(project_id).name != project_id:
        return None
    raw = record.get("project_root")
    if raw in (None, "", "."):
        return default_root / project_id
    if not isinstance(raw, str):
        return None
    candidate = Path(raw).expanduser()
    return candidate if candidate.is_absolute() else default_root / candidate


def _project_roots(data_root: Path) -> tuple[tuple[Path, ...], bool]:
    managed_root = data_root / "projects"
    legacy_root = data_root / "legacy" / "local_disk"
    roots: set[Path] = set()
    ambiguous = False

    managed, invalid = _registry_projects(data_root / "v2_projects.json")
    ambiguous |= invalid
    for record in managed:
        root = _record_root(record, default_root=managed_root)
        ambiguous |= root is None
        if root is not None:
            roots.add(root.absolute())
    if managed_root.is_dir():
        try:
            roots.update(
                path.absolute()
                for path in managed_root.iterdir()
                if path.is_dir() and path.name.startswith("prj_")
            )
        except OSError:
            ambiguous = True

    legacy, invalid = _registry_projects(legacy_root / "v2_projects.json")
    ambiguous |= invalid
    for record in legacy:
        root = _record_root(record, default_root=legacy_root)
        ambiguous |= root is None
        if root is not None:
            roots.add(root.absolute())
    if legacy_root.is_dir():
        try:
            roots.update(
                path.absolute()
                for path in legacy_root.iterdir()
                if path.is_dir() and not path.is_symlink()
            )
        except OSError:
            ambiguous = True
    return tuple(sorted(roots, key=os.fspath)), ambiguous


def _round_evidence(
    project_root: Path,
    *,
    platform_name: str,
    process_probe: ProcessProbe,
) -> ResearchInspection:
    checkpoint = project_root / "checkpoint"
    state_path = checkpoint / "run_state.json"
    try:
        locks = tuple(checkpoint.glob(".round_*.lock")) if checkpoint.is_dir() else ()
    except OSError:
        return ResearchInspection(ResearchActivity.AMBIGUOUS, (state_path,), "unreadable lock state")
    if not state_path.exists():
        return ResearchInspection(
            ResearchActivity.AMBIGUOUS if locks else ResearchActivity.IDLE,
            (state_path,),
            "round lock exists without run state" if locks else None,
        )
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ResearchInspection(ResearchActivity.AMBIGUOUS, (state_path,), "unreadable run state")
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return ResearchInspection(ResearchActivity.AMBIGUOUS, (state_path,), "unsupported run state")
    status = payload.get("status")
    pid = payload.get("pid")
    config_file = payload.get("config_file")
    backend_root = payload.get("backend_root")
    if not isinstance(status, str):
        return ResearchInspection(ResearchActivity.AMBIGUOUS, (state_path,), "invalid round status")

    identity: ProcessIdentity | None = None
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        if (
            not isinstance(config_file, str)
            or not config_file
            or not isinstance(backend_root, str)
            or not backend_root
        ):
            return ResearchInspection(ResearchActivity.AMBIGUOUS, (state_path,), "missing process identity")
        launcher = str(Path(backend_root) / "scripts" / "run_hb_sherlock.py")
        identity = process_probe(pid, config_file, launcher, platform_name)
        if identity is ProcessIdentity.MATCH:
            return ResearchInspection(ResearchActivity.ACTIVE, (state_path,))
        if identity is ProcessIdentity.UNKNOWN:
            return ResearchInspection(ResearchActivity.AMBIGUOUS, (state_path,), "process identity unavailable")

    if status in ACTIVE_ROUND_STATUSES:
        if identity in {ProcessIdentity.DEAD, ProcessIdentity.MISMATCH}:
            return ResearchInspection(ResearchActivity.IDLE, (state_path,))
        return ResearchInspection(
            ResearchActivity.AMBIGUOUS if locks else ResearchActivity.IDLE,
            (state_path,),
            "active launch has no verifiable PID" if locks else None,
        )
    if locks and identity is None:
        return ResearchInspection(ResearchActivity.AMBIGUOUS, (state_path,), "unreconciled round lock")
    return ResearchInspection(ResearchActivity.IDLE, (state_path,))


def inspect_research_activity(
    data_root: Path,
    *,
    platform_name: str,
    process_probe: ProcessProbe = inspect_process_identity,
) -> ResearchInspection:
    """Read managed and durable-legacy run state without changing project data."""
    root = data_root.expanduser().absolute()
    projects, layout_ambiguous = _project_roots(root)
    inspected: list[Path] = []
    ambiguous_detail = "project registry could not be interpreted" if layout_ambiguous else None
    for project in projects:
        evidence = _round_evidence(
            project,
            platform_name=platform_name,
            process_probe=process_probe,
        )
        inspected.extend(evidence.state_paths)
        if evidence.activity is ResearchActivity.ACTIVE:
            return ResearchInspection(ResearchActivity.ACTIVE, tuple(inspected))
        if evidence.activity is ResearchActivity.AMBIGUOUS:
            ambiguous_detail = evidence.detail or "research process state is ambiguous"
    return ResearchInspection(
        ResearchActivity.AMBIGUOUS if ambiguous_detail else ResearchActivity.IDLE,
        tuple(inspected),
        ambiguous_detail,
    )


def require_idle_research(
    state: ManagerState,
    *,
    action: str,
    inspector: Callable[..., ResearchInspection] = inspect_research_activity,
) -> None:
    inspection = inspector(Path(state.data_root), platform_name=state.platform)
    if inspection.activity is ResearchActivity.IDLE:
        return
    raise ActiveResearchError(
        "DiscoveryAgent has an active research round.\n"
        f"Stop the round or wait for it to finish before {action}."
    )


def terminate_frontend_pid(
    pid: int,
    readiness_url: str,
    *,
    platform_name: str,
    pid_alive: Callable[[int], bool] = process_is_alive,
    health_probe: Callable[[str], bool] = readiness_is_healthy,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    timeout: float = 10.0,
) -> None:
    """Gracefully stop one verified frontend; Windows receives a later adapter."""
    if platform_name != "darwin":
        raise MaintenanceError(f"Frontend maintenance is unsupported on {platform_name}")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise MaintenanceError(f"Could not stop the DiscoveryAgent frontend: {exc}") from exc
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if not pid_alive(pid) and not health_probe(readiness_url):
            return
        sleep(0.05)
    raise MaintenanceError("The DiscoveryAgent frontend did not stop safely")


def stop_idle_frontend(
    paths: InstallationPaths,
    state: ManagerState,
    *,
    pid_alive: Callable[[int], bool] = process_is_alive,
    health_probe: Callable[[str], bool] = readiness_is_healthy,
    terminator: Callable[..., None] = terminate_frontend_pid,
) -> bool:
    """Stop only this installation's verified healthy frontend."""
    try:
        record = load_frontend_record(paths.frontend_runtime_file)
    except LaunchRuntimeError as exc:
        raise MaintenanceError("Frontend runtime state cannot be verified safely") from exc
    if record is None:
        return False
    if not pid_alive(record.pid):
        remove_frontend_record_if_owned(
            paths.frontend_runtime_file,
            pid=record.pid,
            active_release_id=record.active_release_id,
        )
        return False
    if record.active_release_id not in state.installed_releases or not existing_frontend_is_healthy(
        record,
        active_release_id=record.active_release_id,
        pid_alive=pid_alive,
        health_probe=health_probe,
    ):
        raise MaintenanceError("The running frontend cannot be verified safely")
    terminator(
        record.pid,
        record.readiness_url,
        platform_name=state.platform,
        pid_alive=pid_alive,
        health_probe=health_probe,
    )
    remove_frontend_record_if_owned(
        paths.frontend_runtime_file,
        pid=record.pid,
        active_release_id=record.active_release_id,
    )
    return True
