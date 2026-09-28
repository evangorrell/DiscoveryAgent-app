"""Production installation engine for a packaged DiscoveryAgent payload."""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from . import MANAGER_VERSION
from .bootstrap import (
    BootstrapResult,
    EnvironmentValidation,
    RuntimeAcquirer,
    _copy_canonical_release,
    _read_release_metadata,
    _tree_manifest,
    _validate_canonical_release_copy,
    default_data_root,
    install_cli_shim,
    install_manager_atomic,
    sync_environment,
    validate_installed_release,
)
from .errors import BootstrapError, LaunchRuntimeError, StateError
from .frontend_process import readiness_is_healthy
from .installation import ReleaseMetadata
from .launch import existing_frontend_is_healthy
from .paths import InstallationPaths
from .path_integration import PathIntegrationChange, configure_cli_path
from .runtime import (
    PYTHON_VERSION,
    UV_VERSION,
    BootstrapRuntimeSource,
    RuntimePaths,
    acquire_runtimes,
    adopt_bootstrap_runtimes,
    sha256_file,
)
from .runtime_state import load_frontend_record, process_is_alive
from .state import (
    STATE_SCHEMA_VERSION,
    CliState,
    InstalledReleaseState,
    ManagerState,
    RuntimeState,
    load_state,
    write_state_atomic,
)


EnvironmentSyncer = Callable[[Path, Path, Path, Path, Path], Path]
ReleaseValidator = Callable[[Path, Path], EnvironmentValidation]
FrontendChecker = Callable[[InstallationPaths, ManagerState | None], bool]
ManagerInstaller = Callable[[Path, InstallationPaths], Path]
CliInstaller = Callable[..., Path]
StateWriter = Callable[[Path, ManagerState], None]
PathIntegrator = Callable[[Path], PathIntegrationChange]


@dataclass(frozen=True)
class ProductionDefaults:
    install_root: Path
    cli_path: Path
    data_root: Path


def production_default_paths(home: Path | None = None) -> ProductionDefaults:
    root = (Path.home() if home is None else home).expanduser().resolve()
    data_root = default_data_root() if home is None else root / "DiscoveryAgent"
    return ProductionDefaults(
        install_root=root / ".local" / "share" / "discoveryagent",
        cli_path=root / ".local" / "bin" / "discoveryagent",
        data_root=data_root,
    )


@dataclass(frozen=True)
class ProductionInstallConfig:
    payload_root: Path
    install_root: Path | None = None
    cli_path: Path | None = None
    data_root: Path | None = None
    bootstrap_runtime: BootstrapRuntimeSource | None = None

    def normalized(self, *, home: Path | None = None) -> "ProductionInstallConfig":
        defaults = production_default_paths(home)
        selected = (
            ("payload-root", self.payload_root),
            ("install-root", self.install_root or defaults.install_root),
            ("cli-path", self.cli_path or defaults.cli_path),
            ("data-root", self.data_root or defaults.data_root),
        )
        for label, path in selected:
            if not path.expanduser().is_absolute():
                raise BootstrapError(f"--{label} must be an absolute path")
        normalized = ProductionInstallConfig(
            payload_root=self.payload_root.expanduser().resolve(),
            install_root=(self.install_root or defaults.install_root).expanduser().resolve(),
            cli_path=(self.cli_path or defaults.cli_path).expanduser().resolve(),
            data_root=(self.data_root or defaults.data_root).expanduser().resolve(),
            bootstrap_runtime=self.bootstrap_runtime,
        )
        assert normalized.install_root is not None
        assert normalized.data_root is not None
        if (
            normalized.data_root == normalized.install_root
            or normalized.data_root.is_relative_to(normalized.install_root)
            or normalized.install_root.is_relative_to(normalized.data_root)
        ):
            raise BootstrapError(
                "--data-root and --install-root must be separate, non-overlapping paths"
            )
        return normalized


class InstallationLock:
    """Exclusive, token-owned lock for one installation root."""

    def __init__(
        self,
        install_root: Path,
        *,
        stale_after: float = 6 * 60 * 60,
        pid_alive: Callable[[int], bool] = process_is_alive,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        self.path = install_root.parent / f".{install_root.name}.install.lock"
        self.stale_after = stale_after
        self.pid_alive = pid_alive
        self.wall_time = wall_time
        self.token = uuid.uuid4().hex
        self.acquired = False

    def _payload(self) -> dict[str, object] | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _is_stale(self) -> bool:
        payload = self._payload()
        if payload is None:
            try:
                return self.wall_time() - self.path.stat().st_mtime >= self.stale_after
            except OSError:
                return False
        pid = payload.get("pid")
        created_at = payload.get("created_at")
        if (
            not isinstance(pid, int)
            or isinstance(pid, bool)
            or not isinstance(created_at, (int, float))
            or isinstance(created_at, bool)
        ):
            return True
        return not self.pid_alive(pid)

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "created_at": self.wall_time(),
                "token": self.token,
            },
            sort_keys=True,
        ) + "\n"
        for _attempt in range(2):
            try:
                descriptor = os.open(
                    self.path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                if not self._is_stale():
                    raise BootstrapError(
                        f"Another DiscoveryAgent installer is already using {self.path.parent}"
                    )
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                continue
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception:
                self.path.unlink(missing_ok=True)
                raise
            self.acquired = True
            return
        raise BootstrapError("Could not recover the stale DiscoveryAgent install lock")

    def release(self) -> None:
        if not self.acquired:
            return
        payload = self._payload()
        if payload and payload.get("token") == self.token:
            self.path.unlink(missing_ok=True)
        self.acquired = False

    def __enter__(self) -> "InstallationLock":
        self.acquire()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()


def _metadata_payload(payload_root: Path) -> dict[str, object]:
    path = payload_root / "release.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BootstrapError(f"Packaged release metadata is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BootstrapError(
            f"Packaged release metadata is invalid JSON at line {exc.lineno}"
        ) from exc
    if not isinstance(value, dict):
        raise BootstrapError("Packaged release.json must be a JSON object")
    return value


def _required_mapping(payload: dict[str, object], name: str) -> dict[str, object]:
    value = payload.get(name)
    if not isinstance(value, dict):
        raise BootstrapError(f"release.json {name} must be an object")
    return value


def _required_hash(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise BootstrapError(f"release.json {name} must be a SHA-256 digest")
    return value


def validate_packaged_payload(payload_root: Path) -> ReleaseMetadata:
    """Validate release identity and recorded hashes without changing the payload."""
    metadata = _read_release_metadata(payload_root / "release.json")
    raw = _metadata_payload(payload_root)
    application = payload_root / "application"
    frontend = application / "frontend"
    backend = application / "backend"
    manager = payload_root / "manager" / "discoveryagent_cli"
    for path, label in (
        (frontend, "frontend application snapshot"),
        (backend, "backend application snapshot"),
        (manager, "manager package"),
    ):
        if not path.is_dir():
            raise BootstrapError(f"Packaged {label} is missing: {path}")

    frontend_metadata = _required_mapping(raw, "frontend")
    backend_metadata = _required_mapping(raw, "backend")
    paperclip = _required_mapping(raw, "paperclip")
    checks = (
        (
            frontend / "uv.lock",
            _required_hash(frontend_metadata, "uv_lock_sha256"),
            "frontend uv.lock",
        ),
        (
            backend / "uv.lock",
            _required_hash(backend_metadata, "uv_lock_sha256"),
            "backend uv.lock",
        ),
    )
    wheel_name = paperclip.get("vendored_wheel")
    if not isinstance(wheel_name, str) or not wheel_name:
        raise BootstrapError("release.json paperclip.vendored_wheel is missing")
    wheel_relative = PurePosixPath(wheel_name)
    if wheel_relative.is_absolute() or ".." in wheel_relative.parts:
        raise BootstrapError("release.json Paperclip wheel path is unsafe")
    checks += (
        (
            backend.joinpath(*wheel_relative.parts),
            _required_hash(paperclip, "sha256"),
            "vendored Paperclip wheel",
        ),
    )
    for path, expected, label in checks:
        if not path.is_file():
            raise BootstrapError(f"Packaged {label} is missing: {path}")
        actual = sha256_file(path)
        if actual != expected:
            raise BootstrapError(
                f"Packaged {label} SHA-256 mismatch: expected {expected}, received {actual}"
            )

    prefix = "dev" if metadata.release_type == "development" else "prod"
    if metadata.release_type not in {"development", "production"}:
        raise BootstrapError(f"Unsupported release type: {metadata.release_type!r}")
    expected_release_id = (
        f"{prefix}-{metadata.frontend_tree[:12]}-{metadata.backend_commit[:12]}"
    )
    if metadata.release_id != expected_release_id:
        raise BootstrapError(
            "release.json release ID is inconsistent with its type and source identities"
        )
    if metadata.required_cpython_version != PYTHON_VERSION:
        raise BootstrapError(
            f"Release requires Python {metadata.required_cpython_version}; expected {PYTHON_VERSION}"
        )
    if metadata.package_revision and metadata.release_type == "production":
        from .package_identity import package_source_manifest
        if raw.get("package_sources") != package_source_manifest(payload_root):
            raise BootstrapError("Packaged program files differ from the approved source manifest")
    return metadata


def existing_installation_state(paths: InstallationPaths) -> ManagerState | None:
    if not paths.state_file.exists():
        return None
    try:
        return load_state(paths.state_file)
    except StateError as exc:
        raise BootstrapError(f"Existing installation state is invalid: {exc}") from exc


def installation_has_healthy_frontend(
    paths: InstallationPaths,
    state: ManagerState | None,
) -> bool:
    if not paths.frontend_runtime_file.exists():
        return False
    if state is None:
        raise BootstrapError(
            "DiscoveryAgent frontend state exists without installation state; "
            "stop DiscoveryAgent before installing"
        )
    try:
        record = load_frontend_record(paths.frontend_runtime_file)
    except LaunchRuntimeError as exc:
        raise BootstrapError(
            "DiscoveryAgent frontend state cannot be verified; stop DiscoveryAgent "
            "before installing"
        ) from exc
    if record is None:
        return False
    return existing_frontend_is_healthy(
        record,
        active_release_id=state.active_release_id,
        pid_alive=process_is_alive,
        health_probe=readiness_is_healthy,
    )


def _state_for_installation(
    paths: InstallationPaths,
    metadata: ReleaseMetadata,
    runtimes: RuntimePaths,
    data_root: Path,
    cli_state: CliState,
    previous: ManagerState | None,
) -> ManagerState:
    releases = dict(previous.installed_releases) if previous else {}
    releases[metadata.installation_id] = InstalledReleaseState(
        path=f"releases/{metadata.installation_id}"
    )
    return ManagerState(
        schema_version=STATE_SCHEMA_VERSION,
        active_release_id=metadata.installation_id,
        installed_releases=releases,
        manager_version=MANAGER_VERSION,
        runtime=RuntimeState(
            python_version=PYTHON_VERSION,
            python_path=str(runtimes.python.resolve()),
            uv_version=UV_VERSION,
            uv_path=str(runtimes.uv.resolve()),
        ),
        data_root=str(data_root),
        platform=runtimes.platform_name,
        architecture=runtimes.architecture,
        cli=cli_state,
    )


def _silent_reporter(_message: str) -> None:
    """Default progress sink for callers that do not narrate."""


def install_production_release(
    config: ProductionInstallConfig,
    *,
    reporter: Callable[[str], None] = _silent_reporter,
    runtime_acquirer: RuntimeAcquirer = acquire_runtimes,
    environment_syncer: EnvironmentSyncer = sync_environment,
    release_validator: ReleaseValidator = validate_installed_release,
    frontend_checker: FrontendChecker = installation_has_healthy_frontend,
    lock_factory: Callable[[Path], InstallationLock] = InstallationLock,
    manager_installer: ManagerInstaller = install_manager_atomic,
    cli_installer: CliInstaller = install_cli_shim,
    state_writer: StateWriter = write_state_atomic,
    path_integrator: PathIntegrator = configure_cli_path,
) -> BootstrapResult:
    selected = config.normalized()
    assert selected.install_root is not None
    assert selected.cli_path is not None
    assert selected.data_root is not None
    reporter("Verifying the release payload")
    metadata = validate_packaged_payload(selected.payload_root)
    source_release_json = (selected.payload_root / "release.json").read_bytes()
    source_manifest = _tree_manifest(selected.payload_root / "application")
    paths = InstallationPaths(selected.install_root)

    with lock_factory(paths.root):
        paths.root.mkdir(parents=True, exist_ok=True)
        paths.cache_root.mkdir(parents=True, exist_ok=True)
        paths.releases_root.mkdir(parents=True, exist_ok=True)
        staging_root = paths.root / "staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        previous = existing_installation_state(paths)
        if frontend_checker(paths, previous):
            raise BootstrapError(
                "DiscoveryAgent is currently running. Stop it before installing."
            )

        if selected.bootstrap_runtime is None:
            reporter(f"Installing Python {PYTHON_VERSION} and uv {UV_VERSION}")
            runtimes = runtime_acquirer(paths.runtime_root, paths.cache_root)
        else:
            reporter(f"Adopting the verified Python {PYTHON_VERSION} runtime")
            runtimes = adopt_bootstrap_runtimes(
                paths.runtime_root,
                paths.cache_root,
                selected.bootstrap_runtime,
            )
        if previous is not None:
            from .installation import load_active_release
            current = load_active_release(paths, previous)
            if metadata.package_revision < current.metadata.package_revision:
                raise BootstrapError("Refusing to install an older package revision")
        final_release = paths.release_root(metadata.installation_id)
        reused_release = False
        if final_release.exists():
            reporter(f"Reusing the installed release {metadata.release_id}")
            if not (final_release / "complete").is_file():
                raise BootstrapError(
                    "Existing release is incomplete and will not be overwritten: "
                    f"{final_release}"
                )
            _validate_canonical_release_copy(
                selected.payload_root, final_release, metadata
            )
            from .bootstrap import environment_needs_rebuild
            for component in ("frontend", "backend"):
                environment_root = final_release / "envs" / component
                if environment_needs_rebuild(environment_root):
                    reporter(f"Rebuilding incompatible {component} environment")
                    environment_syncer(runtimes.uv, runtimes.python,
                                       final_release / "application" / component,
                                       environment_root, paths.cache_root)
            validation = release_validator(final_release, paths.cache_root)
            reused_release = True
        else:
            staging = staging_root / f"{metadata.release_id}-{uuid.uuid4().hex}"
            created_final = False
            try:
                staging.mkdir()
                reporter("Copying the DiscoveryAgent application")
                _copy_canonical_release(selected.payload_root, staging)
                reporter("Installing the frontend environment (a few minutes)")
                environment_syncer(
                    runtimes.uv,
                    runtimes.python,
                    staging / "application" / "frontend",
                    staging / "envs" / "frontend",
                    paths.cache_root,
                )
                reporter("Installing the backend environment (a few minutes)")
                environment_syncer(
                    runtimes.uv,
                    runtimes.python,
                    staging / "application" / "backend",
                    staging / "envs" / "backend",
                    paths.cache_root,
                )
                reporter("Verifying the installed environments")
                _validate_canonical_release_copy(
                    selected.payload_root, staging, metadata
                )
                validation = release_validator(staging, paths.cache_root)
                (staging / "complete").write_text("complete\n", encoding="utf-8")
                os.replace(staging, final_release)
                created_final = True
                _validate_canonical_release_copy(
                    selected.payload_root, final_release, metadata
                )
                validation = release_validator(final_release, paths.cache_root)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                if created_final:
                    shutil.rmtree(final_release, ignore_errors=True)
                raise

        if _tree_manifest(selected.payload_root / "application") != source_manifest:
            raise BootstrapError("Packaged application payload changed during installation")
        if (selected.payload_root / "release.json").read_bytes() != source_release_json:
            raise BootstrapError("Packaged release.json changed during installation")
        reporter("Installing the discoveryagent command")
        manager_installer(selected.payload_root, paths)
        cli_path = cli_installer(
            selected.cli_path,
            install_root=paths.root,
            python=runtimes.python,
        )
        path_change = path_integrator(cli_path)
        try:
            state = _state_for_installation(
                paths,
                metadata,
                runtimes,
                selected.data_root,
                path_change.cli_state,
                previous,
            )
            state_writer(paths.state_file, state)
        except Exception:
            path_change.rollback()
            raise
        return BootstrapResult(
            paths=paths,
            release=metadata,
            runtimes=runtimes,
            validation=validation,
            state=state,
            reused_release=reused_release,
        )
