"""Thin local process manager for autonomous V2 round execution.

The research workflow remains owned by discoveryagent/scripts/run_hb_sherlock.py.
This module only aligns a local V2 project with that launcher, starts it, and
records enough secret-free process metadata to reject duplicate launches and
reconcile status after a browser refresh or frontend restart.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import data_library, model_catalog
from .project_skills import (
    ROUND_SKILLS_SNAPSHOT_DIRECTORY,
    SkillImportError,
    capture_round_skills,
)


SCHEMA_VERSION = 2
EXPLORATORY_ROUND_QUERY_BUDGET = 5
HYPOTHESIS_ROUND_QUERY_BUDGET = 12
MINIMUM_ROUND_QUERIES = 1
MAXIMUM_ROUND_QUERIES = 50
DIRECTOR_AGENT_SWITCHES: dict[str, tuple[str, bool]] = {
    "deep_research": ("omit_deepresearch", True),
    "web_search": ("omit_singlesearch", True),
}
SUB_AGENT_ARCHITECTURE = True
MISSING_KEY_MESSAGE = (
    "Anthropic API key not detected\n\n"
    "Set ANTHROPIC_API_KEY in your environment and restart DiscoveryAgent before "
    "starting or resuming a round."
)


class RoundLaunchError(RuntimeError):
    """Expected, user-actionable launch error with a stable API code."""

    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


_RUNNER_LOCK = threading.RLock()
_PROCESSES: dict[str, subprocess.Popen] = {}
_PROCESS_RUN_IDS: dict[str, str] = {}
_RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


@contextmanager
def round_state_guard():
    """Serialize external project mutations with round launch transitions."""
    with _RUNNER_LOCK:
        yield


def project_has_tracked_process(project: dict[str, Any]) -> bool:
    """Check a launched process without requiring its project root to exist."""
    raw = project.get("project_root") if isinstance(project, dict) else None
    if not raw:
        return False
    key = str(Path(raw).expanduser().absolute())
    with _RUNNER_LOCK:
        process = _PROCESSES.get(key)
        return bool(process is not None and process.poll() is None)


def _anthropic_key_is_present(env: dict[str, str]) -> bool:
    """Check credential presence without exposing or validating its value."""
    return bool(str(env.get("ANTHROPIC_API_KEY") or "").strip())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _safe_analysis_name(project: dict[str, Any]) -> str:
    raw = str(project.get("url_slug") or project.get("id") or "local_project")
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")
    return normalized or "local_project"


def normalize_round_options(raw: Any) -> dict[str, Any]:
    """Validate browser-supplied round choices and map them to backend keys."""
    if not isinstance(raw, dict):
        return {}
    config: dict[str, Any] = {}
    if "num_queries" in raw and raw["num_queries"] not in (None, ""):
        value = raw["num_queries"]
        if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value).strip()):
            raise RoundLaunchError(
                "invalid_round_options", "Enter a whole number of queries for this round."
            )
        requested = int(value)
        if not MINIMUM_ROUND_QUERIES <= requested <= MAXIMUM_ROUND_QUERIES:
            raise RoundLaunchError(
                "invalid_round_options",
                f"Choose between {MINIMUM_ROUND_QUERIES} and {MAXIMUM_ROUND_QUERIES} queries for this round.",
            )
        config["num_queries"] = requested

    for switch, (config_key, negated) in DIRECTOR_AGENT_SWITCHES.items():
        if switch in raw:
            enabled = raw[switch]
            if not isinstance(enabled, bool):
                raise RoundLaunchError(
                    "invalid_round_options",
                    f"{switch.replace('_', ' ').title()} must be enabled or disabled.",
                )
            config[config_key] = (not enabled) if negated else enabled

    reasoning_raw = raw.get("reasoning_model")
    execution_raw = raw.get("execution_model")
    try:
        if reasoning_raw not in (None, ""):
            reasoning = model_catalog.validate_project_model(reasoning_raw)
            config.update({
                field: reasoning
                for field in model_catalog.REASONING_MODEL_CONFIG_FIELDS
            })
        if execution_raw not in (None, ""):
            execution = model_catalog.validate_project_model(execution_raw)
            config.update({
                field: execution
                for field in model_catalog.EXECUTION_MODEL_CONFIG_FIELDS
            })
    except model_catalog.UnsupportedProjectModel as exc:
        raise RoundLaunchError("unsupported_project_model", str(exc)) from exc

    return config


def next_round_number(project: dict[str, Any]) -> int:
    paths = _project_paths(project)
    completed = [
        index for index, directory in _native_round_dirs(paths["checkpoints_dir"])
        if (directory / "memory_report.md").is_file()
    ]
    return (completed[-1] + 1) if completed else 0


def round_option_defaults(
    round_number: int, project: dict[str, Any] | None = None
) -> dict[str, Any]:
    saved_queries = (project or {}).get("query_count")
    if saved_queries is None:
        saved_queries = (project or {}).get("num_queries")
    normalized_queries = str(saved_queries).strip().lower() if saved_queries is not None else ""
    if normalized_queries in {"", "automatic"}:
        saved_queries = HYPOTHESIS_ROUND_QUERY_BUDGET
    else:
        if isinstance(saved_queries, bool) or not re.fullmatch(r"\d+", str(saved_queries).strip()):
            raise RoundLaunchError("invalid_project_settings", "The saved project Query Count is invalid.")
        saved_queries = int(saved_queries)
        if not MINIMUM_ROUND_QUERIES <= saved_queries <= MAXIMUM_ROUND_QUERIES:
            raise RoundLaunchError("invalid_project_settings", "The saved project Query Count is invalid.")

    def enabled(name: str) -> bool:
        value = (project or {}).get(name)
        if value is None:
            return True
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise RoundLaunchError("invalid_project_settings", f"The saved {name.replace('_', ' ')} setting is invalid.")
    return {
        "num_queries": EXPLORATORY_ROUND_QUERY_BUDGET if round_number == 0 else saved_queries,
        "query_count": saved_queries,
        "minimum_queries": MINIMUM_ROUND_QUERIES,
        "maximum_queries": MAXIMUM_ROUND_QUERIES,
        "deep_research": enabled("deep_research"),
        "web_search": enabled("web_search"),
    }


def _project_model_config(project: dict[str, Any]) -> dict[str, str]:
    """Validate and expand the project-owned Anthropic model roles."""
    try:
        selected = model_catalog.resolve_project_models(project)
        return model_catalog.project_model_role_config(
            selected["reasoning_model"], selected["execution_model"]
        )
    except model_catalog.UnsupportedProjectModel as exc:
        raise RoundLaunchError("unsupported_project_model", str(exc)) from exc


def _project_round_config(
    project: dict[str, Any], round_number: int,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the project settings to snapshot into one fresh round."""
    defaults = round_option_defaults(round_number, project)
    config = {
        "num_queries": defaults["num_queries"],
        "omit_deepresearch": not defaults["deep_research"],
        "omit_singlesearch": not defaults["web_search"],
    }
    try:
        config.update(_project_model_config(project))
    except RoundLaunchError:
        supplied = overrides or {}
        required_model_fields = set(model_catalog.PROJECT_MODEL_CONFIG_FIELDS)
        if not required_model_fields.issubset(supplied):
            raise
    return config


def external_data_read_paths(project: dict[str, Any]) -> list[str]:
    if not project.get("external_data"):
        return []
    raw = project.get("data_dir")
    return [str(Path(raw).expanduser().resolve())] if raw else []


def resolve_backend_runtime(env: dict[str, str] | None = None) -> tuple[Path, Path, Path]:
    """Resolve and validate backend root, interpreter, and existing launcher."""
    current_env = os.environ if env is None else env
    default_root = Path(__file__).resolve().parents[2] / "backend"
    backend_root = Path(current_env.get("DISCOVERYAGENT_BACKEND_ROOT") or default_root).expanduser().resolve()
    if not backend_root.is_dir():
        raise RoundLaunchError(
            "backend_repository_missing",
            "The DiscoveryAgent backend repository could not be found. Set DISCOVERYAGENT_BACKEND_ROOT and restart DiscoveryAgent.",
            503,
        )

    configured_python = current_env.get("DISCOVERYAGENT_BACKEND_PYTHON")
    if configured_python:
        # Preserve a configured venv interpreter path: resolving its final
        # symlink would bypass that environment and its site-packages.
        backend_python = Path(os.path.abspath(Path(configured_python).expanduser()))
    else:
        posix_python = backend_root / ".venv" / "bin" / "python"
        windows_python = backend_root / ".venv" / "Scripts" / "python.exe"
        backend_python = posix_python if posix_python.exists() else windows_python
    if not backend_python.is_file():
        raise RoundLaunchError(
            "backend_python_missing",
            "The DiscoveryAgent backend Python environment could not be found. Set DISCOVERYAGENT_BACKEND_PYTHON and restart DiscoveryAgent.",
            503,
        )
    if not os.access(backend_python, os.X_OK):
        raise RoundLaunchError(
            "backend_python_not_executable",
            "The DiscoveryAgent backend Python environment is not executable. Repair the installation and restart DiscoveryAgent.",
            503,
        )

    launcher = backend_root / "scripts" / "run_hb_sherlock.py"
    if not launcher.is_file():
        raise RoundLaunchError(
            "backend_launcher_missing",
            "The DiscoveryAgent backend launcher could not be found in the configured backend repository.",
            503,
        )
    return backend_root, backend_python, launcher


def _project_paths(project: dict[str, Any]) -> dict[str, Path]:
    project_id = str(project.get("id") or "").strip()
    root_raw = project.get("project_root")
    code_raw = project.get("code_dir")
    checkpoint_raw = project.get("checkpoint_dir")
    if not project_id or not root_raw or not code_raw or not checkpoint_raw:
        raise RoundLaunchError(
            "invalid_local_configuration",
            "The active project does not contain the local paths required to start a round.",
        )

    registered_root = Path(root_raw).expanduser().absolute()
    if not registered_root.is_dir():
        raise RoundLaunchError(
            "project_location_unavailable",
            "DiscoveryAgent could not find this project at its saved location.",
            409,
        )
    project_root = registered_root.resolve()
    if project_root != registered_root:
        raise RoundLaunchError(
            "project_location_unavailable",
            "The saved project location is not a canonical project folder.",
            409,
        )
    code_dir = Path(code_raw).expanduser().resolve()
    configured_round = Path(checkpoint_raw).expanduser().resolve()
    try:
        code_dir.relative_to(project_root)
        configured_round.relative_to(project_root)
    except ValueError as exc:
        raise RoundLaunchError(
            "invalid_local_configuration",
            "The active project's code or checkpoint path is outside its local project directory.",
        ) from exc
    if not code_dir.is_dir() or not (code_dir / "data").is_dir():
        if project.get("external_data"):
            raise RoundLaunchError(
                "data_folder_unavailable",
                "The data folder chosen for this project is no longer available at "
                f"{project.get('data_dir')}. Reconnect the drive or restore the folder, then try again.",
                503,
            )
        raise RoundLaunchError(
            "invalid_local_configuration",
            "The active project's local code/data directory is missing.",
        )

    # V2 stores checkpoint_dir as .../checkpoint/checkpoints/round_N. Derive the
    # backend log_dir as .../checkpoint so CheckpointManager appends exactly one
    # checkpoints/ component.
    if re.fullmatch(r"round_\d+", configured_round.name) and configured_round.parent.name == "checkpoints":
        checkpoints_dir = configured_round.parent
        log_dir = checkpoints_dir.parent
    else:
        raise RoundLaunchError(
            "invalid_local_configuration",
            "The active project's checkpoint path has an unsupported layout.",
        )
    if log_dir / "checkpoints" / configured_round.name != configured_round:
        raise RoundLaunchError("invalid_local_configuration", "The checkpoint path could not be aligned with the backend.")

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        configured_round.mkdir(parents=True, exist_ok=True)
        probe = log_dir / f".round-runner-write-test-{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise RoundLaunchError(
            "checkpoint_tree_unwritable",
            "The active project's checkpoint directory is not writable.",
            503,
        ) from exc

    return {
        "project_root": project_root,
        "code_dir": code_dir,
        "log_dir": log_dir,
        "checkpoints_dir": checkpoints_dir,
        "configured_round": configured_round,
        "state_file": log_dir / "run_state.json",
        "diagnostic_log": log_dir / "local_runner.log",
    }


def _native_round_dirs(checkpoints_dir: Path) -> list[tuple[int, Path]]:
    rounds = []
    try:
        entries = checkpoints_dir.iterdir()
    except OSError:
        return rounds
    for entry in entries:
        match = re.fullmatch(r"round_(\d+)", entry.name)
        if match and entry.is_dir():
            rounds.append((int(match.group(1)), entry))
    return sorted(rounds)


def _has_resumable_checkpoint(round_dir: Path) -> bool:
    return _latest_native_checkpoint(round_dir) is not None


def _latest_native_checkpoint(round_dir: Path) -> Path | None:
    checkpoints: list[tuple[int, Path]] = []
    for query in round_dir.glob("query_*"):
        match = re.fullmatch(r"query_(\d+)", query.name)
        checkpoint = query / "checkpoint.json"
        if match and query.is_dir() and checkpoint.is_file():
            checkpoints.append((int(match.group(1)), checkpoint))
    return max(checkpoints, default=(0, None), key=lambda item: item[0])[1]


def _is_fresh_selection(round_dir: Path) -> bool:
    """Whether ``round_dir`` contains the unbound selection for its successor.

    New Studio histories record semantic UTC timestamps for both selection and
    round completion. Prefer that durable chronology because copying or
    importing a project can legitimately normalize file mtimes. Older trees
    have no such chronology, so retain the established mtime fallback.
    """
    selection = round_dir / "selected_hypothesis.md"
    report = round_dir / "round_report.md"
    if not selection.is_file():
        return False
    if report.is_file():
        selection_time = _latest_json_event_time(
            round_dir / "hypothesis_studio_history.json",
            event_key="type", event_value="hypothesis_selected",
        )
        report_time = _latest_jsonl_event_time(
            round_dir / "round_activity.jsonl",
            event_key="event_type", event_value="round_report_saved",
        )
        if selection_time is not None and report_time is not None:
            return selection_time > report_time
    try:
        return not report.exists() or selection.stat().st_mtime > report.stat().st_mtime
    except OSError:
        return False


def _event_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_json_event_time(
    path: Path, *, event_key: str, event_value: str,
) -> datetime | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        return None
    timestamps = [
        timestamp
        for event in events
        if isinstance(event, dict) and event.get(event_key) == event_value
        if (timestamp := _event_timestamp(event.get("timestamp"))) is not None
    ]
    return max(timestamps, default=None)


def _latest_jsonl_event_time(
    path: Path, *, event_key: str, event_value: str,
) -> datetime | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    timestamps: list[datetime] = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get(event_key) != event_value:
            continue
        timestamp = _event_timestamp(event.get("timestamp"))
        if timestamp is not None:
            timestamps.append(timestamp)
    return max(timestamps, default=None)


def selection_is_fresh_for_continuation(round_dir: Path) -> bool:
    """Public read-only lifecycle predicate shared by every V2 surface."""
    return _is_fresh_selection(Path(round_dir))


def _resolve_launch(project: dict[str, Any], paths: dict[str, Path]) -> dict[str, Any]:
    """Resolve native source/target rounds; browser-facing labels never enter here."""
    rounds = _native_round_dirs(paths["checkpoints_dir"])
    completed = [(index, directory) for index, directory in rounds if (directory / "memory_report.md").is_file()]
    if not completed:
        target_round = 0
        target_dir = paths["checkpoints_dir"] / "round_0"
        target_dir.mkdir(parents=True, exist_ok=True)
        return {
            "round": target_round,
            "source_round": None,
            "source_dir": None,
            "target_dir": target_dir,
            "config_file": target_dir / "config.json",
            "selected_hypothesis_file": None,
            "resume": _has_resumable_checkpoint(target_dir),
        }

    source_round, source_dir = completed[-1]
    target_round = source_round + 1
    target_dir = paths["checkpoints_dir"] / f"round_{target_round}"
    if (target_dir / "memory_report.md").exists():
        raise RoundLaunchError(
            "round_already_completed",
            f"Native round {target_round} has already completed for this project.",
            409,
        )
    selection = source_dir / "selected_hypothesis.md"
    if not selection.is_file():
        prior = paths["checkpoints_dir"] / f"round_{source_round - 1}"
        if source_round > 0 and _is_fresh_selection(prior):
            raise RoundLaunchError(
                "round_already_completed",
                f"Native round {source_round} has already completed for this project.",
                409,
            )
        raise RoundLaunchError(
            "selected_hypothesis_missing",
            "Select a hypothesis for the next research round before starting it.",
        )
    if not _is_fresh_selection(source_dir):
        raise RoundLaunchError(
            "selected_hypothesis_missing",
            "The latest completed round does not contain a new hypothesis selection for its continuation.",
        )

    resumable = _has_resumable_checkpoint(target_dir)
    if target_dir.exists() and _round_has_activity(target_dir) and not resumable:
        raise RoundLaunchError(
            "round_already_started",
            f"Native round {target_round} contains incomplete activity that the backend cannot safely resume.",
            409,
        )
    config_file = target_dir / "config.json"
    if not config_file.is_file():
        config_file = source_dir / "config.json"
    if not config_file.is_file():
        raise RoundLaunchError(
            "invalid_local_configuration",
            f"The continuation configuration for native round {target_round} is missing.",
        )
    return {
        "round": target_round,
        "source_round": source_round,
        "source_dir": source_dir,
        "target_dir": target_dir,
        "config_file": config_file,
        "selected_hypothesis_file": selection,
        "resume": resumable,
    }


def _resolve_resume_launch(project: dict[str, Any], paths: dict[str, Path]) -> dict[str, Any]:
    """Resolve an interrupted native round without constructing a successor."""
    state = _read_state(paths["state_file"])
    if not state or state.get("project_id") != str(project["id"]):
        raise RoundLaunchError("round_not_resumable", "No interrupted round is recorded for this project.", 409)
    if state.get("status") in {"starting", "running"} and _pid_matches_state(state):
        raise RoundLaunchError("round_already_running", "This round is already running.", 409)
    if state.get("status") not in {"interrupted", "failed"}:
        raise RoundLaunchError("round_not_resumable", "The recorded round is not interrupted or failed.", 409)
    round_number = state.get("round")
    if not isinstance(round_number, int) or round_number < 0:
        raise RoundLaunchError("round_not_resumable", "The recorded round number is invalid.", 409)
    target_dir = paths["checkpoints_dir"] / f"round_{round_number}"
    if (target_dir / "memory_report.md").is_file():
        raise RoundLaunchError("round_already_completed", "A completed round cannot be resumed.", 409)
    config_file = target_dir / "config.json"
    checkpoint = _latest_native_checkpoint(target_dir)
    if not config_file.is_file() or checkpoint is None:
        raise RoundLaunchError(
            "round_not_resumable",
            "This round stopped before DiscoveryAgent wrote a native query checkpoint and cannot be resumed safely.",
            409,
        )
    return {
        "round": round_number,
        "source_round": None,
        "source_dir": None,
        "target_dir": target_dir,
        "config_file": config_file,
        "selected_hypothesis_file": None,
        "resume": True,
        "checkpoint_file": checkpoint,
    }


def _round_has_activity(round_dir: Path) -> bool:
    if any(path.is_dir() and re.fullmatch(r"query_\d+", path.name) for path in round_dir.glob("query_*")):
        return True
    return any((round_dir / name).exists() for name in (
        "round_activity.jsonl", "live_round.log", "round.log", "round_report.md", "memory_report.md"
    ))


def _read_state(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError):
        return None


def _state_run_id(state: dict[str, Any] | None) -> str | None:
    """Return a stable identity for both current and pre-run-ID state files."""
    if not isinstance(state, dict):
        return None
    explicit = str(state.get("run_id") or "").strip().lower()
    if _RUN_ID_PATTERN.fullmatch(explicit):
        return explicit
    if not state.get("project_id") or not isinstance(state.get("round"), int):
        return None
    seed = "\0".join(str(state.get(key) or "") for key in (
        "project_id", "round", "pid", "started_at", "config_file",
    ))
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _validate_run_identity(
    state: dict[str, Any], *, project_id: str,
    expected_round: int | None = None, expected_run_id: str | None = None,
) -> str | None:
    """Reject an operation aimed at a different project, round, or run."""
    if state.get("project_id") != project_id:
        raise RoundLaunchError(
            "project_identity_mismatch",
            "The requested research run does not belong to this project.",
            409,
        )
    if expected_round is not None and state.get("round") != expected_round:
        raise RoundLaunchError(
            "round_identity_mismatch",
            "The requested research round is no longer the active round.",
            409,
        )
    run_id = _state_run_id(state)
    if expected_run_id is not None and run_id != expected_run_id:
        raise RoundLaunchError(
            "run_identity_mismatch",
            "The requested research run is no longer active.",
            409,
        )
    return run_id


def _pid_matches_state(state: dict[str, Any]) -> bool:
    pid = state.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        import psutil

        process = psutil.Process(pid)
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return False
        command = process.cmdline()
        return str(state.get("config_file") or "") in command and any(
            item.endswith("run_hb_sherlock.py") for item in command
        )
    except Exception:
        return False


def _state_payload(
    project: dict[str, Any], paths: dict[str, Path], backend_root: Path, backend_python: Path,
    *, status: str, pid: int | None = None, exit_code: int | None = None, started_at: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    now = _utc_now()
    run_id = run_id or uuid.uuid4().hex
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": str(project["id"]),
        "round": int(paths["round"]),
        "run_id": run_id,
        "status": status,
        "pid": pid,
        "started_at": started_at or now,
        "updated_at": now,
        "exit_code": exit_code,
        "backend_root": str(backend_root),
        "python_executable": str(backend_python),
        "config_file": str(paths["config_file"]),
        "diagnostic_log": str(paths["diagnostic_log"]),
    }


def _hydrate_round_zero_config(
    project: dict[str, Any],
    paths: dict[str, Path],
    options: dict[str, Any] | None = None,
    *,
    snapshot_project_model: bool = True,
) -> dict[str, Any]:
    existing: dict[str, Any] = {}
    try:
        loaded = json.loads(paths["config_file"].read_text(encoding="utf-8"))
        candidate = loaded.get("config", loaded) if isinstance(loaded, dict) else {}
        if isinstance(candidate, dict):
            existing = candidate
    except (OSError, ValueError):
        pass

    readme_raw = project.get("readme_path")
    readme_path = Path(readme_raw).expanduser().resolve() if readme_raw else paths["code_dir"] / "README.md"
    if not readme_path.is_file():
        raise RoundLaunchError(
            "invalid_local_configuration",
            "The active project's README/context file is missing.",
        )
    task_description = str(project.get("task_description") or existing.get("task_description") or "").strip()
    if not task_description:
        raise RoundLaunchError(
            "invalid_local_configuration",
            "The active project does not have a research objective for Round 0.",
        )

    # Small, centralized autonomous Round 0 preset. Values mirror the backend
    # parser defaults except for the explicit one-round exploratory/human handoff.
    config = dict(existing)
    config.update({
        "analysis_name": _safe_analysis_name(project),
        "dataset_name": _safe_analysis_name(project),
        "working_directory": str(paths["code_dir"]),
        "existing_codebase": str(paths["code_dir"]),
        "log_dir": str(paths["log_dir"]),
        "checkpoint_base_dir": str(paths["checkpoints_dir"]),
        "task_description": task_description,
        "task_description_file": None,
        "readme_path": str(readme_path),
        "num_rounds": 1,
        "num_branches": 1,
        "num_parallel_workflows": 1,
        "num_exploration_rounds": 1,
        "num_queries": int(existing.get("num_queries") or EXPLORATORY_ROUND_QUERY_BUDGET),
        "human_selection": True,
        "selected_hypothesis_file": None,
        "no_sandbox": False,
        "deep_research_provider": existing.get("deep_research_provider") or "paperclip",
    })
    if snapshot_project_model:
        config.update(_project_round_config(project, 0, options))
        config.update(options or {})
    config["sub_agent_architecture"] = SUB_AGENT_ARCHITECTURE
    _apply_external_data_access(project, config)
    _apply_data_library_access(project, config, manifest_path=paths["target_dir"] / ".discoveryagent_input_data_manifest.md")
    _overlay_project_skills(config, project, paths["target_dir"])
    _atomic_write_json(paths["config_file"], {"config": config})
    return config


def _apply_external_data_access(project: dict[str, Any], config: dict[str, Any]) -> None:
    """Allow sandbox reads through the external data symlink without widening writes."""
    read_paths = external_data_read_paths(project)
    if not read_paths:
        return
    configured = str(config.get("allowed_read_paths") or "").strip()
    existing = [entry.strip() for entry in configured.split(",") if entry.strip()]
    for path in read_paths:
        if path not in existing:
            existing.append(path)
    config["allowed_read_paths"] = ",".join(existing)


def _apply_data_library_access(
    project: dict[str, Any], config: dict[str, Any], *, selection: Any = None,
    manifest_path: Path | None = None,
) -> None:
    """Resolve stable library references as extra read-only sandbox roots."""
    selected = project.get("data_sources") if selection is None else selection
    try:
        normalized = data_library.normalize_selection(selected)
        read_paths, manifest = data_library.resolve_selection(normalized)
    except data_library.DataLibraryError as exc:
        raise RoundLaunchError(exc.code, exc.message, exc.status) from exc

    data_keys = {
        "data_library_selection", "data_library_resolved_paths",
        "data_library_manifest", "data_library_manifest_paths",
    }
    if not normalized and not any(key in config for key in data_keys):
        return

    configured = str(config.get("allowed_read_paths") or "").strip()
    existing = [entry.strip() for entry in configured.split(",") if entry.strip()]
    previous = {
        entry for entry in (config.get("data_library_resolved_paths") or [])
        if isinstance(entry, str)
    }
    existing = [entry for entry in existing if entry not in previous]
    for path in read_paths:
        if path not in existing:
            existing.append(path)
    if existing:
        config["allowed_read_paths"] = ",".join(existing)
    else:
        config.pop("allowed_read_paths", None)
    config["data_library_selection"] = normalized
    config["data_library_resolved_paths"] = read_paths
    config["data_library_manifest"] = manifest
    if manifest and manifest_path is not None:
        lines = [
            "# External read-only input data",
            "",
            f"Writable project workspace: `{config.get('working_directory') or project.get('code_dir')}`",
            "",
            "The paths below are user-supplied inputs. Read them in place, but write all code, derived data, figures, and analyses only in the project workspace.",
        ]
        for source in manifest:
            lines.extend(["", f"## {source['display_name']}"])
            lines.extend(f"- Read-only path: `{path}`" for path in source["resolved_read_paths"])
            if source["excluded_paths"]:
                lines.append("- Project exclusions (manifest intent; descendants of an allowed parent cannot be hard-denied by the current sandbox): " + ", ".join(source["excluded_paths"]))
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = manifest_path.with_name(f".{manifest_path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(temporary, manifest_path)
        config["hint_txt_path"] = str(manifest_path)
    else:
        previous_manifests = config.get("data_library_manifest_paths")
        previous_manifests = previous_manifests if isinstance(previous_manifests, list) else []
        if config.get("hint_txt_path") in {
            path for path in previous_manifests if isinstance(path, str)
        }:
            config.pop("hint_txt_path", None)
    config["data_library_manifest_paths"] = [str(manifest_path)] if manifest and manifest_path is not None else []


def _hydrate_continuation_config(
    project: dict[str, Any],
    paths: dict[str, Path],
    options: dict[str, Any] | None = None,
) -> None:
    """Create the target round's local config from its native predecessor."""
    source_file = paths["config_file"]
    target_config = paths["target_dir"] / "config.json"
    # A genuinely new continuation reads the completed predecessor's config.
    # Resume and Resubmit resolve an already-existing target config instead;
    # those paths must preserve the round's historical query budget exactly.
    is_new_hypothesis_round = source_file.resolve() != target_config.resolve()
    try:
        loaded = json.loads(source_file.read_text(encoding="utf-8"))
        config = loaded.get("config", loaded) if isinstance(loaded, dict) else None
    except (OSError, ValueError):
        config = None
    if not isinstance(config, dict):
        raise RoundLaunchError("invalid_local_configuration", "The continuation configuration is not valid JSON.")

    readme_raw = project.get("readme_path")
    readme_path = Path(readme_raw).expanduser().resolve() if readme_raw else paths["code_dir"] / "README.md"
    if not readme_path.is_file():
        raise RoundLaunchError("invalid_local_configuration", "The active project's README/context file is missing.")
    task_description = str(project.get("task_description") or config.get("task_description") or "").strip()
    if not task_description:
        raise RoundLaunchError("invalid_local_configuration", "The active project does not have a research objective.")

    local_config = dict(config)
    # These are launcher/runtime provenance from the machine that produced the
    # source checkpoint, not scientific settings. The local launcher derives
    # fresh values and must not copy remote absolute paths forward.
    for runtime_key in (
        "config_file", "config_yaml", "_resume_memory_report_path",
        "background_report_path", "sherlock_data_dir",
    ):
        local_config.pop(runtime_key, None)
    local_config.update({
        "working_directory": str(paths["code_dir"]),
        "existing_codebase": str(paths["code_dir"]),
        "log_dir": str(paths["log_dir"]),
        "checkpoint_base_dir": str(paths["checkpoints_dir"]),
        "task_description": task_description,
        "task_description_file": None,
        "readme_path": str(readme_path),
        "num_rounds": 1,
        **({"num_queries": HYPOTHESIS_ROUND_QUERY_BUDGET} if is_new_hypothesis_round else {}),
        "human_selection": True,
        "selected_hypothesis_file": str(paths["selected_hypothesis_file"]),
        "generate_plan": True,
        "no_sandbox": False,
    })
    if is_new_hypothesis_round:
        round_number = paths.get("round")
        if not isinstance(round_number, int):
            match = re.fullmatch(r"round_(\d+)", paths["target_dir"].name)
            round_number = int(match.group(1)) if match else 1
        local_config.update(_project_round_config(project, round_number, options))
        local_config.update(options or {})
    local_config["sub_agent_architecture"] = SUB_AGENT_ARCHITECTURE
    _apply_external_data_access(project, local_config)
    _apply_data_library_access(project, local_config, manifest_path=paths["target_dir"] / ".discoveryagent_input_data_manifest.md")
    _overlay_project_skills(local_config, project, paths["target_dir"])
    _atomic_write_json(target_config, {"config": local_config})
    paths["config_file"] = target_config


def _overlay_project_skills(
    config: dict[str, Any], project: dict[str, Any], round_dir: Path,
) -> None:
    """Pin a round to its immutable capture of the project's installed skills."""
    try:
        snapshot, has_skills = capture_round_skills(project, round_dir)
        if has_skills:
            config["execution_skills_path"] = str(snapshot)
        else:
            config.pop("execution_skills_path", None)
    except SkillImportError as exc:
        raise RoundLaunchError("invalid_local_configuration", exc.message) from exc


def _refresh_resume_project_skills(project: dict[str, Any], config_file: Path) -> None:
    """Refresh safe live references while preserving interrupted-round choices.

    Code-location fields are runtime provenance, not scientific choices. A
    moved/migrated project must resume in its currently registered code root so
    the backend tool cwd, Round Workspace tree, and external-IDE path cannot
    diverge.
    """
    try:
        payload = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RoundLaunchError(
            "invalid_local_configuration", "The interrupted round configuration is not valid JSON."
        ) from exc
    if not isinstance(payload, dict):
        raise RoundLaunchError(
            "invalid_local_configuration", "The interrupted round configuration is not valid JSON."
        )

    nested = payload.get("config")
    if nested is not None and not isinstance(nested, dict):
        raise RoundLaunchError(
            "invalid_local_configuration", "The interrupted round configuration is not valid JSON."
        )
    config = dict(nested) if isinstance(nested, dict) else dict(payload)
    original = dict(config)
    code_raw = project.get("code_dir")
    if not isinstance(code_raw, str) or not code_raw.strip():
        raise RoundLaunchError(
            "invalid_local_configuration",
            "The active project does not contain its registered code workspace.",
        )
    code_root = str(Path(code_raw).expanduser().resolve())
    config["working_directory"] = code_root
    config["existing_codebase"] = code_root
    checkpoint_raw = project.get("checkpoint_dir")
    if not isinstance(checkpoint_raw, str) or not checkpoint_raw.strip():
        raise RoundLaunchError(
            "invalid_local_configuration",
            "The active project does not contain its registered checkpoint workspace.",
        )
    configured_round = Path(checkpoint_raw).expanduser().resolve()
    checkpoints_root = configured_round.parent
    if "checkpoint_base_dir" in config:
        config["checkpoint_base_dir"] = str(checkpoints_root)
    if "log_dir" in config:
        config["log_dir"] = str(checkpoints_root.parent)
    readme_raw = project.get("readme_path")
    if "readme_path" in config and isinstance(readme_raw, str) and readme_raw.strip():
        config["readme_path"] = str(Path(readme_raw).expanduser().resolve())
    saved_selection = (
        config.get("data_library_selection")
        if "data_library_selection" in config
        else project.get("data_sources")
    )
    _apply_external_data_access(project, config)
    _apply_data_library_access(
        project, config, selection=saved_selection,
        manifest_path=config_file.parent / ".discoveryagent_input_data_manifest.md",
    )
    _overlay_project_skills(config, project, config_file.parent)
    if config == original:
        return
    if isinstance(nested, dict):
        updated = dict(payload)
        updated["config"] = config
    else:
        updated = config
    _atomic_write_json(config_file, updated)


def _build_command(
    backend_python: Path, launcher: Path, config_file: Path,
    selected_hypothesis_file: Path | None = None,
) -> list[str]:
    command = [
        str(backend_python), str(launcher),
        "--config-file", str(config_file),
        "--num_rounds", "1",
    ]
    if selected_hypothesis_file is not None:
        command.extend([
            "--selected-hypothesis-file", str(selected_hypothesis_file),
            "--generate_plan",
        ])
    return command


_INHERITED_LOCATION_VARIABLES = (
    # Legacy runtime location; the current backend does not read it, but tools
    # launched by Claude can still observe and follow a stale value.
    "AIR_DATA_DIR",
    # ExecutionAgentCC writes these from execution_skills_path when configured.
    # Inherited values would otherwise re-enable skills from a previous run.
    "CLAUDE_CODE_SKILLS_PATH",
    "CLAUDE_CODE_SKILLS_DIR",
    "CLAUDE_SKILLS_PATH",
    # These select the frontend's backend before launch. They are not backend
    # runtime inputs and need not be exposed to the child process.
    "DISCOVERYAGENT_BACKEND_ROOT",
    "DISCOVERYAGENT_BACKEND_PYTHON",
    # A frontend-launched local round always uses the Claude Agent SDK bundled
    # runtime. Developer overrides from the frontend shell must not redirect
    # the backend to a global/npm Claude installation.
    "CLAUDE_CODE_CLI_PATH",
    "NODE_PATH",
    "WRITING_AGENT_CLI_PATH",
    "WRITING_AGENT_USE_USER_CLI",
)


def _child_environment(
    backend_root: Path, backend_python: Path, project_root: Path,
    source: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build a child-only environment whose location state matches this run."""
    child_env = dict(os.environ if source is None else source)
    for variable in _INHERITED_LOCATION_VARIABLES:
        child_env.pop(variable, None)

    # run_hb_sherlock.py still reads DATA_PATH for its data-root defaults and
    # sherlock_data_dir metadata. Explicit V2 config supplies narrower working,
    # checkpoint, and log paths; this keeps the remaining root authoritative.
    child_env["DATA_PATH"] = str(project_root)
    # Tells backend configuration that this is the public frontend path. This
    # also prevents an ignored developer .env from restoring overrides removed
    # above after the child process starts.
    child_env["DISCOVERYAGENT_FRONTEND_LOCAL_ROUND"] = "1"

    backend_bin = backend_python.parent
    inherited_virtualenv = child_env.get("VIRTUAL_ENV")
    inherited_virtualenv_bins = set()
    if inherited_virtualenv:
        inherited_virtualenv_root = Path(inherited_virtualenv).expanduser()
        inherited_virtualenv_bins = {
            str(inherited_virtualenv_root / "bin"),
            str(inherited_virtualenv_root / "Scripts"),
        }
    existing_path = child_env.get("PATH", "")
    path_entries = [
        entry for entry in existing_path.split(os.pathsep)
        if entry and entry != str(backend_bin) and entry not in inherited_virtualenv_bins
    ]
    child_env["PATH"] = os.pathsep.join([str(backend_bin), *path_entries])

    virtualenv_root = backend_bin.parent
    if (virtualenv_root / "pyvenv.cfg").is_file():
        child_env["VIRTUAL_ENV"] = str(virtualenv_root)
    else:
        child_env.pop("VIRTUAL_ENV", None)
    child_env.pop("VIRTUAL_ENV_PROMPT", None)
    # PYTHONHOME is incompatible with virtual environments and can redirect
    # stdlib discovery to the frontend interpreter even when argv[0] is right.
    child_env.pop("PYTHONHOME", None)

    existing = child_env.get("PYTHONPATH")
    child_env["PYTHONPATH"] = str(backend_root) + (os.pathsep + existing if existing else "")
    return child_env


def _monitor_process(
    project: dict[str, Any], paths: dict[str, Path], process: subprocess.Popen,
    run_id: str | None = None,
) -> None:
    exit_code = process.wait()
    with _RUNNER_LOCK:
        key = str(paths["project_root"])
        current_process = _PROCESSES.get(key)
        if current_process is process:
            _PROCESSES.pop(key, None)
        tracked_run_id = _PROCESS_RUN_IDS.get(key)
        if run_id is not None and tracked_run_id == run_id:
            _PROCESS_RUN_IDS.pop(key, None)
        current = _read_state(paths["state_file"]) or {}
        current_run_id = _state_run_id(current)
        if (
            current.get("project_id") != str(project["id"])
            or current.get("round") != int(paths["round"])
            or (run_id is not None and current_run_id != run_id)
        ):
            return
        status = "completed" if (paths["target_dir"] / "memory_report.md").exists() else "failed"
        payload = dict(current)
        if current.get("status") == "interrupted" and status != "completed":
            status = "interrupted"
        payload.update({"status": status, "exit_code": exit_code, "updated_at": _utc_now()})
        _atomic_write_json(paths["state_file"], payload)
        try:
            paths["lock_file"].unlink()
        except FileNotFoundError:
            pass


def _launch_round(
    project: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
    resume_only: bool = False,
    options: dict[str, Any] | None = None,
    preserve_existing_snapshot: bool = False,
    expected_round: int | None = None,
    expected_run_id: str | None = None,
) -> dict[str, Any]:
    source_env = os.environ if env is None else env
    if not _anthropic_key_is_present(source_env):
        raise RoundLaunchError("anthropic_api_key_missing", MISSING_KEY_MESSAGE)

    backend_root, backend_python, launcher = resolve_backend_runtime(source_env)
    paths = _project_paths(project)
    launch = _resolve_resume_launch(project, paths) if resume_only else _resolve_launch(project, paths)
    paths.update(launch)
    paths["lock_file"] = paths["log_dir"] / f".round_{paths['round']}.lock"
    key = str(paths["project_root"])
    with _RUNNER_LOCK:
        tracked_process = _PROCESSES.get(key)
        if tracked_process is not None and tracked_process.poll() is None:
            raise RoundLaunchError(
                "round_already_running",
                "A native round is already running for this project.",
                409,
            )
        if (paths["target_dir"] / "memory_report.md").exists():
            raise RoundLaunchError("round_already_completed", f"Native round {paths['round']} has already completed for this project.", 409)

        existing_state = _read_state(paths["state_file"])
        if expected_round is not None or expected_run_id is not None:
            if not existing_state:
                raise RoundLaunchError(
                    "round_not_running", "No recorded research run can be resumed.", 409,
                )
            _validate_run_identity(
                existing_state, project_id=str(project["id"]),
                expected_round=expected_round, expected_run_id=expected_run_id,
            )
        if existing_state and existing_state.get("status") in {"starting", "running"} and _pid_matches_state(existing_state):
            raise RoundLaunchError("round_already_running", f"Native round {paths['round']} is already running for this project.", 409)
        if _round_has_activity(paths["target_dir"]) and not paths["resume"]:
            raise RoundLaunchError(
                "round_already_started",
                f"Native round {paths['round']} already contains backend activity that cannot be resumed safely.",
                409,
            )

        # A process can disappear between frontend sessions and leave its
        # atomic launch marker behind. It is safe to reclaim only after the
        # durable state has failed the PID/command identity check above and no
        # native checkpoint activity exists.
        if paths["lock_file"].exists():
            try:
                paths["lock_file"].unlink()
            except OSError as exc:
                raise RoundLaunchError(
                    "round_already_running",
                    f"Native round {paths['round']} is already starting or running for this project.",
                    409,
                ) from exc

        try:
            lock_fd = os.open(paths["lock_file"], os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(lock_fd)
        except FileExistsError as exc:
            raise RoundLaunchError("round_already_running", f"Native round {paths['round']} is already starting or running for this project.", 409) from exc

        diagnostic_handle = None
        run_id = uuid.uuid4().hex
        try:
            if resume_only:
                _refresh_resume_project_skills(project, paths["config_file"])
            elif paths["round"] == 0:
                _hydrate_round_zero_config(
                    project, paths, options,
                    snapshot_project_model=not bool(paths["resume"]) and not preserve_existing_snapshot,
                )
            else:
                _hydrate_continuation_config(project, paths, options)
            command = _build_command(
                backend_python, launcher, paths["config_file"], paths["selected_hypothesis_file"]
            )
            child_env = _child_environment(
                backend_root, backend_python, paths["project_root"], source_env
            )
            initial_state = _state_payload(
                project, paths, backend_root, backend_python,
                status="starting", run_id=run_id,
            )
            _atomic_write_json(paths["state_file"], initial_state)
            diagnostic_handle = paths["diagnostic_log"].open("a", encoding="utf-8")
            process = subprocess.Popen(
                command,
                cwd=str(backend_root),
                env=child_env,
                stdout=diagnostic_handle,
                stderr=subprocess.STDOUT,
                shell=False,
                start_new_session=True,
            )
            diagnostic_handle.close()
            diagnostic_handle = None
            started = _state_payload(
                project, paths, backend_root, backend_python,
                status="starting", pid=process.pid, started_at=initial_state["started_at"],
                run_id=run_id,
            )
            _atomic_write_json(paths["state_file"], started)
            _PROCESSES[key] = process
            _PROCESS_RUN_IDS[key] = run_id
            thread = threading.Thread(
                target=_monitor_process, args=(project, paths, process, run_id), daemon=True,
                name=f"round-{paths['round']}-monitor-{project['id']}",
            )
            thread.start()
            result = {
                "project_id": str(project["id"]), "round": paths["round"],
                "run_id": run_id, "status": "starting", "pid": process.pid,
            }
            if resume_only:
                result["resumed"] = True
            return result
        except RoundLaunchError:
            raise
        except Exception as exc:
            failure = _state_payload(
                project, paths, backend_root, backend_python,
                status="failed", run_id=run_id,
            )
            failure["message"] = "The local backend process could not be started."
            _atomic_write_json(paths["state_file"], failure)
            raise RoundLaunchError(
                "subprocess_creation_failed",
                "DiscoveryAgent could not start the local research process. Check the local runner diagnostic log and try again.",
                500,
            ) from exc
        finally:
            if diagnostic_handle is not None:
                diagnostic_handle.close()
            if key not in _PROCESSES:
                if _PROCESS_RUN_IDS.get(key) == run_id:
                    _PROCESS_RUN_IDS.pop(key, None)
                try:
                    paths["lock_file"].unlink()
                except FileNotFoundError:
                    pass


def start_round(
    project: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _launch_round(project, env=env, resume_only=False, options=options)


def resume_round(
    project: dict[str, Any], *, env: dict[str, str] | None = None,
    expected_round: int | None = None, expected_run_id: str | None = None,
) -> dict[str, Any]:
    """Relaunch the recorded native round; the backend restores its latest checkpoint."""
    return _launch_round(
        project, env=env, resume_only=True,
        expected_round=expected_round, expected_run_id=expected_run_id,
    )


def _terminate_process_group(pid: int, process: subprocess.Popen | None, timeout: float = 3.0) -> int | None:
    """Terminate only the new-session process group created by this runner."""
    if os.name != "posix":
        raise RoundLaunchError("stop_unsupported", "Stopping local rounds is not supported on this platform yet.", 501)
    pgid = os.getpgid(pid)
    if pgid != pid:
        raise RoundLaunchError("process_identity_mismatch", "The recorded process is not an isolated DiscoveryAgent process group.", 409)
    os.killpg(pgid, signal.SIGTERM)
    if process is not None:
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
            return process.wait(timeout=1.0)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        time.sleep(0.05)
    os.killpg(pgid, signal.SIGKILL)
    return None


def stop_round(
    project: dict[str, Any], *, expected_round: int | None = None,
    expected_run_id: str | None = None,
) -> dict[str, Any]:
    """Stop the active verified DiscoveryAgent process group and preserve artifacts."""
    paths = _project_paths(project)
    key = str(paths["project_root"])
    with _RUNNER_LOCK:
        state = _read_state(paths["state_file"])
        if not state:
            raise RoundLaunchError("round_not_running", "No active local round is recorded.", 409)
        run_id = _validate_run_identity(
            state, project_id=str(project["id"]),
            expected_round=expected_round, expected_run_id=expected_run_id,
        )
        round_number = state.get("round")
        target_dir = paths["checkpoints_dir"] / f"round_{round_number}"
        if (target_dir / "memory_report.md").is_file() or state.get("status") == "completed":
            raise RoundLaunchError("round_already_completed", "A completed round cannot be stopped.", 409)
        if state.get("status") == "interrupted":
            return round_status(project)
        if state.get("status") not in {"starting", "running"}:
            raise RoundLaunchError("round_not_running", "This round is not currently running.", 409)

        process = _PROCESSES.get(key)
        tracked_run_id = _PROCESS_RUN_IDS.get(key)
        if process is not None and tracked_run_id is not None and tracked_run_id != run_id:
            raise RoundLaunchError(
                "run_identity_mismatch",
                "The active process belongs to a different research run and was not stopped.",
                409,
            )
        if not _pid_matches_state(state):
            if process is not None and process.poll() is None:
                raise RoundLaunchError("process_identity_mismatch", "The recorded process could not be verified and was not stopped.", 409)
            payload = dict(state)
            payload.update({"status": "interrupted", "updated_at": _utc_now()})
            if process is not None:
                payload["exit_code"] = process.poll()
            _atomic_write_json(paths["state_file"], payload)
            return round_status(project)

        exit_code = _terminate_process_group(int(state["pid"]), process)
        payload = dict(state)
        payload.update({"status": "interrupted", "exit_code": exit_code, "updated_at": _utc_now()})
        _atomic_write_json(paths["state_file"], payload)
        return round_status(project)


def _archive_round_directory(paths: dict[str, Path], round_number: int) -> Path:
    target_dir = paths["checkpoints_dir"] / f"round_{round_number}"
    if not target_dir.is_dir():
        raise RoundLaunchError("round_not_found", "The recorded round directory no longer exists.", 409)
    failed_root = paths["log_dir"] / "failed_runs"
    failed_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    archived_dir = failed_root / f"round_{round_number}_{timestamp}"
    suffix = 1
    while archived_dir.exists():
        archived_dir = failed_root / f"round_{round_number}_{timestamp}_{suffix}"
        suffix += 1
    shutil.move(str(target_dir), str(archived_dir))
    return archived_dir


def resubmit_round(
    project: dict[str, Any], *, env: dict[str, str] | None = None,
    expected_round: int | None = None, expected_run_id: str | None = None,
) -> dict[str, Any]:
    """Archive a non-resumable attempt and relaunch the same round from scratch."""
    source_env = os.environ if env is None else env
    if not _anthropic_key_is_present(source_env):
        raise RoundLaunchError("anthropic_api_key_missing", MISSING_KEY_MESSAGE)
    # Fail before moving the interrupted attempt if the configured backend
    # runtime is unavailable. _launch_round validates this again at launch.
    resolve_backend_runtime(source_env)
    paths = _project_paths(project)
    key = str(paths["project_root"])
    with _RUNNER_LOCK:
        state = _read_state(paths["state_file"])
        if not state:
            raise RoundLaunchError("round_not_resubmittable", "No interrupted round is recorded for this project.", 409)
        _validate_run_identity(
            state, project_id=str(project["id"]),
            expected_round=expected_round, expected_run_id=expected_run_id,
        )
        status = round_status(project)["status"]
        process = _PROCESSES.get(key)
        if status in {"starting", "running"} or (process is not None and process.poll() is None):
            raise RoundLaunchError("round_still_running", "Stop the round before resubmitting it.", 409)
        if status not in {"failed", "interrupted"}:
            raise RoundLaunchError("round_not_resubmittable", "Only a failed or interrupted round can be resubmitted.", 409)
        round_number = state.get("round")
        if not isinstance(round_number, int) or round_number < 0:
            raise RoundLaunchError("round_not_resubmittable", "The recorded round number is invalid.", 409)
        target_dir = paths["checkpoints_dir"] / f"round_{round_number}"
        if _latest_native_checkpoint(target_dir) is not None:
            raise RoundLaunchError(
                "round_is_resumable",
                "This round has a native checkpoint. Resume it instead of starting it over.",
                409,
            )
        archived_dir = _archive_round_directory(paths, round_number)
        target_dir.mkdir(parents=True, exist_ok=True)
        archived_config = archived_dir / "config.json"
        if archived_config.is_file():
            shutil.copy2(archived_config, target_dir / "config.json")
        archived_skills = archived_dir / ROUND_SKILLS_SNAPSHOT_DIRECTORY
        if archived_skills.is_dir() and not archived_skills.is_symlink():
            shutil.copytree(
                archived_skills,
                target_dir / ROUND_SKILLS_SNAPSHOT_DIRECTORY,
                symlinks=False,
            )
        payload = dict(state)
        payload.update({
            "status": "not_started", "pid": None, "updated_at": _utc_now(),
            "archived_round_dir": str(archived_dir),
        })
        _atomic_write_json(paths["state_file"], payload)
        try:
            (paths["log_dir"] / f".round_{round_number}.lock").unlink()
        except FileNotFoundError:
            pass

    result = _launch_round(
        project, env=source_env, resume_only=False,
        preserve_existing_snapshot=True,
    )
    result["resubmitted"] = True
    return result


def _round_status_from_paths(
    paths: dict[str, Path],
    *,
    project_id: str,
    persist_reconciled_state: bool,
) -> dict[str, Any]:
    """Derive the runner-owned lifecycle state from canonical local paths.

    Round Workspace's ``/api/rounds/status`` response and read-only consumers
    such as Project Home must agree about running/stopped/resumable state.  Keep
    that decision here so callers never reproduce the native-checkpoint rules.
    Only the execution API persists stale process reconciliation; presentation
    reads remain side-effect free.
    """
    state = _read_state(paths["state_file"])
    if state and project_id and state.get("project_id") not in {None, "", project_id}:
        raise RoundLaunchError(
            "project_identity_mismatch",
            "The recorded research state belongs to a different project.",
            409,
        )
    if state and project_id and not state.get("project_id"):
        # Pre-v2 lifecycle records are safely attributable here because the
        # caller supplied the registered project's already-contained state path.
        state = dict(state)
        state["project_id"] = project_id
    round_number = int(state.get("round", 0)) if state else 0
    target_dir = paths["checkpoints_dir"] / f"round_{round_number}"
    tracked_running = False
    if state:
        project_root = paths.get("project_root") or Path(paths["state_file"]).parent.parent
        key = str(Path(project_root).expanduser().absolute())
        with _RUNNER_LOCK:
            tracked = _PROCESSES.get(key)
            tracked_run_id = _PROCESS_RUN_IDS.get(key)
            tracked_running = bool(
                tracked is not None
                and tracked.poll() is None
                and tracked_run_id in {None, _state_run_id(state)}
            )
    if (target_dir / "memory_report.md").exists():
        status = "completed"
    elif (
        state
        and state.get("status") in {"starting", "running"}
        and (tracked_running or _pid_matches_state(state))
    ):
        status = "running"
    elif state and state.get("status") in {"starting", "running"}:
        status = "failed"
    elif state and state.get("status") in {"completed", "failed", "interrupted"}:
        status = state["status"]
    elif _round_has_activity(target_dir):
        status = "interrupted"
    else:
        status = "not_started"

    if persist_reconciled_state and state and state.get("status") != status:
        state = dict(state)
        state.update({"status": status, "updated_at": _utc_now()})
        _atomic_write_json(paths["state_file"], state)
    return {
        "project_id": project_id,
        "round": round_number,
        "run_id": _state_run_id(state),
        "status": status,
        "pid": state.get("pid") if state and status == "running" else None,
        "resumable": status in {"interrupted", "failed"} and _latest_native_checkpoint(target_dir) is not None and not (target_dir / "memory_report.md").is_file(),
    }


def inspect_round_status(
    checkpoint_dir: str | Path, *, project_id: str | None = None,
) -> dict[str, Any]:
    """Read the same lifecycle state as :func:`round_status` without writing.

    ``checkpoint_dir`` is the registered V2 ``.../checkpoints/round_N`` path.
    This intentionally accepts no browser-supplied artifact path and exposes no
    file contents; it only lets other trusted server views share the runner's
    exact process/checkpoint classification.
    """
    configured_round = Path(checkpoint_dir).expanduser().resolve()
    if not (
        re.fullmatch(r"round_\d+", configured_round.name)
        and configured_round.parent.name == "checkpoints"
    ):
        raise RoundLaunchError(
            "invalid_local_configuration",
            "The checkpoint path has an unsupported layout.",
        )
    checkpoints_dir = configured_round.parent
    state_file = checkpoints_dir.parent / "run_state.json"
    state = _read_state(state_file)
    result = _round_status_from_paths(
        {"checkpoints_dir": checkpoints_dir, "state_file": state_file},
        project_id=str(project_id or (state or {}).get("project_id") or ""),
        persist_reconciled_state=False,
    )
    # Project-facing consumers with an existing filesystem fallback need to
    # distinguish an explicitly recorded terminal state from this inspector's
    # conservative ``activity exists -> interrupted`` inference. Keep this
    # read-only provenance detail out of the public Round Workspace API.
    result["recorded_state"] = bool(state)
    return result


def round_status(project: dict[str, Any]) -> dict[str, Any]:
    paths = _project_paths(project)
    return _round_status_from_paths(
        paths,
        project_id=str(project["id"]),
        persist_reconciled_state=True,
    )


# Backward-compatible names for integrations/tests from the Round-0 milestone.
start_round_zero = start_round
round_zero_status = round_status
