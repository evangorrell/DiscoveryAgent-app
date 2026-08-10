"""Reusable construction of a self-contained development installation."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence

from . import MANAGER_VERSION
from .errors import BootstrapError
from .installation import ReleaseMetadata
from .paths import InstallationPaths, release_environment_python
from .runtime import PYTHON_VERSION, UV_VERSION, RuntimePaths, acquire_runtimes
from .state import (
    STATE_SCHEMA_VERSION,
    CliState,
    InstalledReleaseState,
    ManagerState,
    RuntimeState,
    write_state_atomic,
)


RunCommand = Callable[..., subprocess.CompletedProcess[str]]
RuntimeAcquirer = Callable[[Path, Path], RuntimePaths]


@dataclass(frozen=True)
class BootstrapConfig:
    repository_root: Path
    install_root: Path
    data_root: Path

    def normalized(self) -> "BootstrapConfig":
        repository_root = self.repository_root.expanduser().resolve()
        install_root = self.install_root.expanduser().resolve()
        data_root = self.data_root.expanduser().resolve()
        if not self.install_root.is_absolute():
            raise BootstrapError("--install-root must be an absolute path")
        if not self.data_root.is_absolute():
            raise BootstrapError("--data-root must be an absolute path")
        return BootstrapConfig(repository_root, install_root, data_root)


@dataclass(frozen=True)
class EnvironmentValidation:
    frontend_python: Path
    backend_python: Path
    frontend_module: Path
    backend_module: Path
    paperclip_version: str
    claude_executable: Path


@dataclass(frozen=True)
class BootstrapResult:
    paths: InstallationPaths
    release: ReleaseMetadata
    runtimes: RuntimePaths
    validation: EnvironmentValidation
    state: ManagerState
    reused_release: bool


def default_data_root() -> Path:
    return (Path.home() / "Library" / "Application Support" / "DiscoveryAgent").resolve()


def _read_release_metadata(path: Path) -> ReleaseMetadata:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BootstrapError(f"Canonical release metadata is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BootstrapError(
            f"Canonical release metadata is invalid JSON at line {exc.lineno}"
        ) from exc
    metadata = ReleaseMetadata.from_dict(payload)
    if metadata.required_cpython_version != PYTHON_VERSION:
        raise BootstrapError(
            f"Release requires Python {metadata.required_cpython_version}, but the "
            f"manager pins {PYTHON_VERSION}"
        )
    return metadata


def _ignored_runtime_names(_directory: str, names: list[str]) -> set[str]:
    ignored = {"__pycache__", ".pytest_cache", ".venv", ".git", ".env", "local_disk"}
    return {name for name in names if name in ignored or name.endswith(".pyc")}


def _tree_manifest(root: Path) -> dict[str, tuple[str, str]]:
    if not root.is_dir():
        raise BootstrapError(f"Expected directory is missing: {root}")
    manifest: dict[str, tuple[str, str]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            manifest[relative] = ("symlink", os.readlink(path))
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest[relative] = ("file", digest)
        elif path.is_dir():
            manifest[relative] = ("directory", "")
        else:
            raise BootstrapError(f"Unsupported snapshot entry: {path}")
    return manifest


def _copy_canonical_release(repository_root: Path, staging_root: Path) -> None:
    source_application = repository_root / "application"
    destination_application = staging_root / "application"
    shutil.copytree(
        source_application,
        destination_application,
        symlinks=True,
        ignore=_ignored_runtime_names,
    )
    shutil.copy2(repository_root / "release.json", staging_root / "release.json")
    if _tree_manifest(source_application) != _tree_manifest(destination_application):
        raise BootstrapError("Installed application snapshot is not byte-identical")


def _validate_canonical_release_copy(
    repository_root: Path,
    release_root: Path,
    metadata: ReleaseMetadata,
) -> None:
    installed_metadata = _read_release_metadata(release_root / "release.json")
    if installed_metadata != metadata:
        raise BootstrapError("Installed release.json differs from the canonical release")
    if (repository_root / "release.json").read_bytes() != (
        release_root / "release.json"
    ).read_bytes():
        raise BootstrapError("Installed release.json is not byte-identical")
    if _tree_manifest(repository_root / "application") != _tree_manifest(
        release_root / "application"
    ):
        raise BootstrapError("Installed application snapshot differs from canonical files")


def _clean_subprocess_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "VIRTUAL_ENV",
        "PYTHONPATH",
        "PYTHONHOME",
        "UV_PROJECT_ENVIRONMENT",
        "CLAUDE_CODE_CLI_PATH",
        "NODE_PATH",
    ):
        environment.pop(name, None)
    environment["PYTHONNOUSERSITE"] = "1"
    if extra:
        environment.update(extra)
    return environment


def _run(
    arguments: Sequence[str | os.PathLike[str]],
    *,
    runner: RunCommand = subprocess.run,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [os.fspath(argument) for argument in arguments]
    try:
        return runner(
            command,
            check=True,
            capture_output=True,
            text=True,
            cwd=cwd,
            env=None if environment is None else dict(environment),
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or getattr(exc, "stdout", "") or str(exc)
        raise BootstrapError(
            f"Command failed ({' '.join(command)}): {str(detail).strip()}"
        ) from exc


def sync_environment(
    uv: Path,
    app_python: Path,
    project_root: Path,
    environment_root: Path,
    cache_root: Path,
    *,
    runner: RunCommand = subprocess.run,
) -> Path:
    environment = _clean_subprocess_environment(
        {
            "UV_PROJECT_ENVIRONMENT": str(environment_root),
            "UV_CACHE_DIR": str(cache_root / "uv"),
            "UV_NO_MODIFY_PATH": "1",
        }
    )
    _run(
        [uv, "lock", "--check", "--project", project_root, "--python", app_python],
        runner=runner,
        environment=environment,
    )
    _run(
        [
            uv,
            "sync",
            "--frozen",
            "--no-dev",
            "--no-editable",
            "--python",
            app_python,
            "--project",
            project_root,
        ],
        runner=runner,
        environment=environment,
    )
    installed_python = release_environment_python(
        environment_root.parent.parent, environment_root.name, "darwin"
    )
    if not installed_python.is_file():
        raise BootstrapError(f"Environment Python is missing: {installed_python}")
    # Keep the environment interpreter path itself. Resolving its symlink to the
    # app-owned base interpreter would bypass the environment's site-packages.
    return installed_python.absolute()


_FRONTEND_INSPECTION = r"""
import importlib.metadata as md
import json
from pathlib import Path
import da_selection_ui

package = Path(da_selection_ui.__file__).resolve().parent
direct_url = md.distribution("da-selection-ui").read_text("direct_url.json")
print(json.dumps({
    "module": str(package),
    "templates": str(package / "templates" / "index.html"),
    "static": str(package / "static"),
    "direct_url": direct_url,
}))
"""


_BACKEND_INSPECTION = r"""
import importlib.metadata as md
import json
import os
from pathlib import Path
import AIR
import claude_agent_sdk
import gxl_paperclip

sdk = Path(claude_agent_sdk.__file__).resolve().parent
claude = sdk / "_bundled" / ("claude.exe" if os.name == "nt" else "claude")
direct_url = md.distribution("AIR").read_text("direct_url.json")
print(json.dumps({
    "module": str(Path(AIR.__file__).resolve().parent),
    "paperclip_version": md.version("gxl-paperclip"),
    "claude": str(claude),
    "direct_url": direct_url,
}))
"""


def _inspection_payload(
    python: Path,
    script: str,
    *,
    cwd: Path,
    environment: Mapping[str, str],
    runner: RunCommand,
) -> dict[str, object]:
    result = _run(
        [python, "-c", script],
        runner=runner,
        cwd=cwd,
        environment=environment,
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BootstrapError(f"Installed-package inspection returned invalid JSON: {result.stdout!r}") from exc


def _assert_noneditable(
    payload: Mapping[str, object],
    *,
    environment_root: Path,
    application_root: Path,
    label: str,
) -> Path:
    module = Path(str(payload["module"])).resolve()
    site_packages = (environment_root / "lib" / f"python{PYTHON_VERSION[:4]}" / "site-packages").resolve()
    if not module.is_relative_to(site_packages):
        raise BootstrapError(f"{label} imports outside its environment site-packages: {module}")
    if module.is_relative_to(application_root.resolve()):
        raise BootstrapError(f"{label} is installed editably from the application snapshot")
    direct_url = payload.get("direct_url")
    if isinstance(direct_url, str) and direct_url:
        try:
            direct_payload = json.loads(direct_url)
        except json.JSONDecodeError as exc:
            raise BootstrapError(f"{label} has malformed direct_url.json") from exc
        if direct_payload.get("dir_info", {}).get("editable") is True:
            raise BootstrapError(f"{label} direct_url.json declares an editable install")
    return module


def validate_installed_release(
    release_root: Path,
    cache_root: Path,
    *,
    runner: RunCommand = subprocess.run,
) -> EnvironmentValidation:
    frontend_root = release_root / "application" / "frontend"
    backend_root = release_root / "application" / "backend"
    frontend_environment = release_root / "envs" / "frontend"
    backend_environment = release_root / "envs" / "backend"
    # Do not resolve venv interpreter symlinks: Python uses the path inside the
    # environment to discover pyvenv.cfg and that environment's site-packages.
    frontend_python = release_environment_python(
        release_root, "frontend", "darwin"
    ).absolute()
    backend_python = release_environment_python(
        release_root, "backend", "darwin"
    ).absolute()
    for python, label in ((frontend_python, "Frontend"), (backend_python, "Backend")):
        if not python.is_file():
            raise BootstrapError(f"{label} environment Python is missing: {python}")

    validation_cwd = cache_root / "validation-cwd"
    validation_cwd.mkdir(parents=True, exist_ok=True)
    environment = _clean_subprocess_environment(
        {
            "DISCOVERYAGENT_DATA_DIR": str(cache_root / "validation-data"),
            "DISCOVERYAGENT_BACKEND_ROOT": str(backend_root),
            "DISCOVERYAGENT_BACKEND_PYTHON": str(backend_python),
        }
    )
    frontend = _inspection_payload(
        frontend_python,
        _FRONTEND_INSPECTION,
        cwd=validation_cwd,
        environment=environment,
        runner=runner,
    )
    frontend_module = _assert_noneditable(
        frontend,
        environment_root=frontend_environment,
        application_root=frontend_root,
        label="Frontend package",
    )
    templates = Path(str(frontend["templates"]))
    static = Path(str(frontend["static"]))
    if not templates.is_file() or not static.is_dir():
        raise BootstrapError("Frontend templates/static package resources are missing")
    _run(
        [frontend_python, "-m", "da_selection_ui", "--help"],
        runner=runner,
        cwd=validation_cwd,
        environment=environment,
    )

    backend = _inspection_payload(
        backend_python,
        _BACKEND_INSPECTION,
        cwd=validation_cwd,
        environment=environment,
        runner=runner,
    )
    backend_module = _assert_noneditable(
        backend,
        environment_root=backend_environment,
        application_root=backend_root,
        label="Backend package",
    )
    paperclip_version = str(backend["paperclip_version"])
    if paperclip_version != "0.7.24":
        raise BootstrapError(
            f"Expected gxl-paperclip 0.7.24, found {paperclip_version}"
        )
    claude = Path(str(backend["claude"])).resolve()
    if not claude.is_file() or not os.access(claude, os.X_OK):
        raise BootstrapError(f"Claude Agent SDK bundled executable is missing: {claude}")
    if not (backend_root / "scripts" / "run_hb_sherlock.py").is_file():
        raise BootstrapError("Backend run_hb_sherlock.py is missing")
    return EnvironmentValidation(
        frontend_python=frontend_python,
        backend_python=backend_python,
        frontend_module=frontend_module,
        backend_module=backend_module,
        paperclip_version=paperclip_version,
        claude_executable=claude,
    )


def _copy_manager(repository_root: Path, paths: InstallationPaths) -> None:
    source = repository_root / "manager" / "discoveryagent_cli"
    destination = paths.manager_root / "discoveryagent_cli"

    def manager_manifest(root: Path) -> dict[str, tuple[str, str]]:
        return {
            key: value
            for key, value in _tree_manifest(root).items()
            if "__pycache__" not in PurePosixPath(key).parts
            and not key.endswith(".pyc")
        }

    source_manifest = manager_manifest(source)
    if destination.exists():
        if manager_manifest(destination) != source_manifest:
            raise BootstrapError(
                "Installed manager differs from this bootstrap; refusing to overwrite it"
            )
        return
    paths.manager_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = paths.root / f".manager-staging-{uuid.uuid4().hex}"
    try:
        shutil.copytree(source, temporary / "discoveryagent_cli", ignore=_ignored_runtime_names)
        if manager_manifest(temporary / "discoveryagent_cli") != source_manifest:
            raise BootstrapError("Installed manager copy is not byte-identical")
        os.replace(temporary, paths.manager_root)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def _shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def wrapper_contents(python: Path) -> str:
    quoted_python = _shell_single_quote(str(python.resolve()))
    return f"""#!/bin/sh
set -eu
install_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
exec {quoted_python} -c 'import sys; from pathlib import Path; root=Path(sys.argv.pop(1)); sys.path.insert(0, str(root / "manager")); from discoveryagent_cli.cli import main; raise SystemExit(main())' "$install_root" "$@"
"""


def install_wrapper(paths: InstallationPaths, python: Path) -> Path:
    paths.root.joinpath("bin").mkdir(parents=True, exist_ok=True)
    wrapper = paths.root / "bin" / "discoveryagent"
    expected = wrapper_contents(python)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".discoveryagent.", suffix=".tmp", dir=wrapper.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(expected)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(temporary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.replace(temporary, wrapper)
    finally:
        temporary.unlink(missing_ok=True)
    return wrapper.resolve()


def _state_for_installation(
    paths: InstallationPaths,
    metadata: ReleaseMetadata,
    runtimes: RuntimePaths,
    data_root: Path,
    wrapper: Path,
) -> ManagerState:
    return ManagerState(
        schema_version=STATE_SCHEMA_VERSION,
        active_release_id=metadata.release_id,
        installed_releases={
            metadata.release_id: InstalledReleaseState(
                path=f"releases/{metadata.release_id}"
            )
        },
        manager_version=MANAGER_VERSION,
        runtime=RuntimeState(
            python_version=PYTHON_VERSION,
            python_path=str(runtimes.python.resolve()),
            uv_version=UV_VERSION,
            uv_path=str(runtimes.uv.resolve()),
        ),
        data_root=str(data_root.resolve()),
        platform=runtimes.platform_name,
        architecture=runtimes.architecture,
        cli=CliState(
            path=str(wrapper),
            path_change_added=False,
            modified_profile=None,
        ),
    )


def bootstrap_development_install(
    config: BootstrapConfig,
    *,
    runtime_acquirer: RuntimeAcquirer = acquire_runtimes,
    environment_syncer: Callable[[Path, Path, Path, Path, Path], Path] = sync_environment,
    release_validator: Callable[[Path, Path], EnvironmentValidation] = validate_installed_release,
) -> BootstrapResult:
    selected = config.normalized()
    paths = InstallationPaths(selected.install_root)
    metadata = _read_release_metadata(selected.repository_root / "release.json")
    source_application = selected.repository_root / "application"
    if not (source_application / "frontend").is_dir() or not (
        source_application / "backend"
    ).is_dir():
        raise BootstrapError("Canonical frontend/backend application snapshots are missing")

    paths.root.mkdir(parents=True, exist_ok=True)
    paths.cache_root.mkdir(parents=True, exist_ok=True)
    paths.releases_root.mkdir(parents=True, exist_ok=True)
    runtimes = runtime_acquirer(paths.runtime_root, paths.cache_root)
    final_release = paths.release_root(metadata.release_id)
    reused_release = False

    if final_release.exists():
        if not (final_release / "complete").is_file():
            raise BootstrapError(
                f"Existing release is incomplete and will not be overwritten: {final_release}"
            )
        _validate_canonical_release_copy(selected.repository_root, final_release, metadata)
        validation = release_validator(final_release, paths.cache_root)
        reused_release = True
    else:
        staging = paths.releases_root / f".{metadata.release_id}.staging-{uuid.uuid4().hex}"
        try:
            staging.mkdir()
            _copy_canonical_release(selected.repository_root, staging)
            environment_syncer(
                runtimes.uv,
                runtimes.python,
                staging / "application" / "frontend",
                staging / "envs" / "frontend",
                paths.cache_root,
            )
            environment_syncer(
                runtimes.uv,
                runtimes.python,
                staging / "application" / "backend",
                staging / "envs" / "backend",
                paths.cache_root,
            )
            _validate_canonical_release_copy(selected.repository_root, staging, metadata)
            validation = release_validator(staging, paths.cache_root)
            (staging / "complete").write_text("complete\n", encoding="utf-8")
            os.replace(staging, final_release)
            try:
                _validate_canonical_release_copy(
                    selected.repository_root, final_release, metadata
                )
                validation = release_validator(final_release, paths.cache_root)
            except Exception:
                # This release was created by this call and cannot be active:
                # state publication has not happened yet.
                shutil.rmtree(final_release, ignore_errors=True)
                raise
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    _copy_manager(selected.repository_root, paths)
    wrapper = install_wrapper(paths, runtimes.python)
    state = _state_for_installation(
        paths, metadata, runtimes, selected.data_root, wrapper
    )
    write_state_atomic(paths.state_file, state)
    return BootstrapResult(
        paths=paths,
        release=metadata,
        runtimes=runtimes,
        validation=validation,
        state=state,
        reused_release=reused_release,
    )
