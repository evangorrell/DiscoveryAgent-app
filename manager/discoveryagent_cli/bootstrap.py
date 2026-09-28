"""Reusable construction of a self-contained development installation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence

from .errors import BootstrapError
from .installation import ReleaseMetadata, validate_launch_installation
from .paths import InstallationPaths, release_environment_python
from .runtime import PYTHON_VERSION, RuntimePaths, acquire_runtimes
from .state import load_state


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
    """Suggest a data root the scientist can actually find and copy files into.

    Application Support is hidden from Finder by default and awkward to reach
    from a shell, which made the projects DiscoveryAgent creates effectively
    unreachable. The first packaged launch confirms this location before any
    project exists (see ``resolve_data_root``).
    """
    return (Path.home() / "DiscoveryAgent").resolve()


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


def environment_needs_rebuild(environment_root: Path) -> bool:
    """Detect interpreter/extension ABI drift without importing broken extensions."""
    if not environment_root.exists():
        return False
    if (environment_root.is_symlink() or environment_root.parent.is_symlink()
            or environment_root.parent.parent.is_symlink()
            or environment_root.parent.name != "envs"
            or environment_root.name not in {"frontend", "backend"}):
        raise BootstrapError("Refusing to rebuild an environment outside a managed envs slot")
    try:
        config = (environment_root / "pyvenv.cfg").read_text()
    except OSError:
        return True
    version = re.search(r"(?m)^version(?:_info)?\s*=\s*(\S+)", config)
    if not version or version.group(1) != PYTHON_VERSION:
        return True
    abi = "".join(PYTHON_VERSION.split(".")[:2])
    for extension in environment_root.rglob("*.so"):
        tag = re.search(r"\.cpython-(\d+)", extension.name)
        if tag and tag.group(1) != abi:
            return True
    return False


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
    backup = None
    if environment_needs_rebuild(environment_root):
        backup = environment_root.with_name(f".{environment_root.name}-abi-{uuid.uuid4().hex}")
        os.replace(environment_root, backup)
    try:
        installed = _sync_environment(uv, app_python, project_root, environment_root, environment, runner)
    except Exception:
        if backup is not None:
            if environment_root.exists():
                shutil.rmtree(environment_root)
            os.replace(backup, environment_root)
        raise
    if backup is not None:
        shutil.rmtree(backup)
    return installed


def _sync_environment(uv, app_python, project_root, environment_root, environment, runner):
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
import pydantic_core
import claude_agent_sdk

package = Path(da_selection_ui.__file__).resolve().parent
direct_url = md.distribution("da-selection-ui").read_text("direct_url.json")
print(json.dumps({
    "module": str(package),
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
import numpy
import pydantic_core
import pymupdf
import pypdf
import nbformat
import nbclient
import jupyter_client

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


def _validate_frontend_package_resources(package: Path) -> None:
    """Require the current V2 Flask package resources after wheel installation."""
    templates = package / "templates"
    static = package / "static"
    required_files = (
        templates / "base.html",
        templates / "projects.html",
        static / "style.css",
    )
    if (
        not templates.is_dir()
        or not static.is_dir()
        or any(not path.is_file() for path in required_files)
    ):
        raise BootstrapError("Frontend templates/static package resources are missing")


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
    _validate_frontend_package_resources(frontend_module)
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


def _manager_manifest(root: Path) -> dict[str, tuple[str, str]]:
    return {
        key: value
        for key, value in _tree_manifest(root).items()
        if "__pycache__" not in PurePosixPath(key).parts and not key.endswith(".pyc")
    }


def install_manager_atomic(payload_root: Path, paths: InstallationPaths) -> Path:
    """Install or refresh the out-of-release manager with rollback."""
    source = payload_root / "manager" / "discoveryagent_cli"
    source_manifest = _manager_manifest(source)
    staging = paths.root / f".manager-refresh-{uuid.uuid4().hex}"
    backup = paths.root / f".manager-backup-{uuid.uuid4().hex}"
    destination = paths.manager_root
    destination_moved = False
    try:
        shutil.copytree(
            source,
            staging / "discoveryagent_cli",
            ignore=_ignored_runtime_names,
        )
        if _manager_manifest(staging / "discoveryagent_cli") != source_manifest:
            raise BootstrapError("Installed manager copy is not byte-identical")
        if destination.exists():
            os.replace(destination, backup)
            destination_moved = True
        os.replace(staging, destination)
        if _manager_manifest(destination / "discoveryagent_cli") != source_manifest:
            raise BootstrapError("Installed manager refresh did not validate")
    except Exception:
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        if destination_moved and backup.exists():
            os.replace(backup, destination)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)
    return destination


def refresh_development_manager(repository_root: Path, install_root: Path) -> Path:
    """Atomically refresh only manager code in an existing development install."""
    repository = repository_root.expanduser().resolve()
    installation = install_root.expanduser().resolve()
    if not install_root.is_absolute():
        raise BootstrapError("--install-root must be an absolute path")
    paths = InstallationPaths(installation)
    state = load_state(paths.state_file)
    validate_launch_installation(paths, state, platform_name=state.platform)
    return install_manager_atomic(repository, paths)


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


def production_wrapper_contents(
    python: Path,
    *,
    install_root: Path,
) -> str:
    quoted_python = _shell_single_quote(str(python.resolve()))
    quoted_root = _shell_single_quote(str(install_root.resolve()))
    return f"""#!/bin/sh
set -eu
exec {quoted_python} -c 'import sys; from pathlib import Path; root=Path(sys.argv.pop(1)); sys.path.insert(0, str(root / "manager")); from discoveryagent_cli.cli import main; raise SystemExit(main())' {quoted_root} "$@"
"""


def _refuse_foreign_cli_path(cli_path: Path) -> None:
    """Never replace a file at --cli-path that DiscoveryAgent did not write.

    The shim is published with os.replace, which silently overwrites whatever
    occupies the destination. A scientist who points --cli-path at an existing
    tool (or a name that another package already owns) must get an error rather
    than lose that file.
    """
    if not os.path.lexists(cli_path):
        return
    if cli_path.is_symlink() or not cli_path.is_file():
        raise BootstrapError(
            f"Refusing to replace an existing non-regular file at {cli_path}"
        )
    try:
        existing = cli_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise BootstrapError(
            f"Refusing to replace an unreadable existing file at {cli_path}: {exc}"
        ) from exc
    if "discoveryagent_cli" not in existing:
        raise BootstrapError(
            f"Refusing to overwrite an existing file that DiscoveryAgent did not "
            f"create: {cli_path}. Choose another --cli-path, or move that file aside."
        )


def install_cli_shim(
    cli_path: Path,
    *,
    install_root: Path,
    python: Path,
) -> Path:
    """Atomically install the external production CLI shim."""
    _refuse_foreign_cli_path(cli_path)
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    expected = production_wrapper_contents(python, install_root=install_root)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{cli_path.name}.", suffix=".tmp", dir=cli_path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(expected)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(
            temporary.stat().st_mode
            | stat.S_IXUSR
            | stat.S_IXGRP
            | stat.S_IXOTH
        )
        os.replace(temporary, cli_path)
    finally:
        temporary.unlink(missing_ok=True)
    return cli_path.resolve()


def bootstrap_development_install(
    config: BootstrapConfig,
    *,
    reporter: Callable[[str], None] | None = None,
    runtime_acquirer: RuntimeAcquirer = acquire_runtimes,
    environment_syncer: Callable[[Path, Path, Path, Path, Path], Path] = sync_environment,
    release_validator: Callable[[Path, Path], EnvironmentValidation] = validate_installed_release,
) -> BootstrapResult:
    selected = config.normalized()
    from .installer import ProductionInstallConfig, _silent_reporter, install_production_release

    return install_production_release(
        ProductionInstallConfig(
            payload_root=selected.repository_root,
            install_root=selected.install_root,
            cli_path=selected.install_root / "bin" / "discoveryagent",
            data_root=selected.data_root,
        ),
        reporter=_silent_reporter if reporter is None else reporter,
        runtime_acquirer=runtime_acquirer,
        environment_syncer=environment_syncer,
        release_validator=release_validator,
    )
