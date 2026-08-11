"""Pinned, app-owned runtime acquisition for DiscoveryAgent installations."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence

from .errors import RuntimeAcquisitionError


UV_VERSION = "0.11.17"
PYTHON_VERSION = "3.11.11"
UV_APPLE_SILICON_ARTIFACT = "uv-aarch64-apple-darwin.tar.gz"
UV_APPLE_SILICON_URL = (
    "https://releases.astral.sh/github/uv/releases/download/"
    f"{UV_VERSION}/{UV_APPLE_SILICON_ARTIFACT}"
)
UV_APPLE_SILICON_SHA256 = (
    "2a162f6b90ff3691a2f9cae1622e066a3ce592e110f66670cdcc841324b28226"
)


RunCommand = Callable[..., subprocess.CompletedProcess[str]]
Download = Callable[[str, Path], None]


@dataclass(frozen=True)
class UvArtifact:
    version: str
    url: str
    sha256: str
    archive_name: str
    executable_name: str = "uv"


@dataclass(frozen=True)
class RuntimePaths:
    uv: Path
    python: Path
    platform_name: str = "darwin"
    architecture: str = "arm64"


@dataclass(frozen=True)
class BootstrapRuntimeSource:
    uv: Path
    python_root: Path
    python: Path


def platform_identity(
    platform_name: str | None = None,
    machine: str | None = None,
) -> tuple[str, str]:
    selected_platform = (platform_name or platform.system()).lower()
    selected_machine = (machine or platform.machine()).lower()
    if selected_platform in {"darwin", "macos"} and selected_machine in {
        "arm64",
        "aarch64",
    }:
        return "darwin", "arm64"
    raise RuntimeAcquisitionError(
        "Phase 2A.2 runtime acquisition supports only Apple Silicon macOS; "
        f"found {selected_platform}/{selected_machine}"
    )


def uv_artifact_for_platform(
    platform_name: str | None = None,
    machine: str | None = None,
) -> UvArtifact:
    """Return the single platform artifact supported by Phase 2A.2."""
    platform_identity(platform_name, machine)
    return UvArtifact(
        version=UV_VERSION,
        url=UV_APPLE_SILICON_URL,
        sha256=UV_APPLE_SILICON_SHA256,
        archive_name=UV_APPLE_SILICON_ARTIFACT,
    )


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": f"DiscoveryAgent-manager/{UV_VERSION}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            with destination.open("wb") as stream:
                shutil.copyfileobj(response, stream)
    except OSError as exc:
        raise RuntimeAcquisitionError(f"Could not download {url}: {exc}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_archive_member(member: tarfile.TarInfo) -> PurePosixPath:
    relative = PurePosixPath(member.name)
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeAcquisitionError(
            f"uv archive contains an unsafe path: {member.name!r}"
        )
    if not (member.isfile() or member.isdir()):
        raise RuntimeAcquisitionError(
            f"uv archive contains an unsupported entry: {member.name!r}"
        )
    return relative


def _extract_uv(archive: Path, destination: Path, artifact: UvArtifact) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    wanted_suffix = PurePosixPath(artifact.executable_name)
    found: tarfile.TarInfo | None = None
    try:
        with tarfile.open(archive, mode="r:gz") as bundle:
            for member in bundle.getmembers():
                relative = _validated_archive_member(member)
                if member.isfile() and relative.name == wanted_suffix.name:
                    if found is not None:
                        raise RuntimeAcquisitionError(
                            "uv archive contains multiple uv executables"
                        )
                    found = member
            if found is None:
                raise RuntimeAcquisitionError("uv archive does not contain uv")
            source = bundle.extractfile(found)
            if source is None:
                raise RuntimeAcquisitionError("Could not read uv from its archive")
            temporary = destination / ".uv.tmp"
            with temporary.open("wb") as stream:
                shutil.copyfileobj(source, stream)
    except (OSError, tarfile.TarError) as exc:
        raise RuntimeAcquisitionError(f"Could not extract uv archive: {exc}") from exc
    executable = destination / artifact.executable_name
    temporary.chmod(temporary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    os.replace(temporary, executable)
    return executable.resolve()


def _run_checked(
    arguments: Sequence[str | os.PathLike[str]],
    *,
    runner: RunCommand = subprocess.run,
    environ: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [os.fspath(argument) for argument in arguments]
    try:
        return runner(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=None if environ is None else dict(environ),
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or getattr(exc, "stdout", "") or str(exc)
        raise RuntimeAcquisitionError(
            f"Command failed ({' '.join(command)}): {str(detail).strip()}"
        ) from exc


def verify_uv(executable: Path, *, runner: RunCommand = subprocess.run) -> None:
    result = _run_checked([executable, "--version"], runner=runner)
    reported = result.stdout.strip()
    if not reported.startswith(f"uv {UV_VERSION}"):
        raise RuntimeAcquisitionError(
            f"Expected uv {UV_VERSION}, but {executable} reported {reported!r}"
        )


def _quarantine_runtime_directory(root: Path) -> Path:
    backup = root.parent / f".{root.name}.invalid-{uuid.uuid4().hex}"
    os.replace(root, backup)
    return backup


def _restore_runtime_directory(root: Path, backup: Path | None) -> None:
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    if backup is not None and backup.exists():
        os.replace(backup, root)


def _relocate_copied_runtime_symlinks(staging: Path, source_root: Path) -> None:
    for path in staging.rglob("*"):
        if not path.is_symlink():
            continue
        link = Path(os.readlink(path))
        if not link.is_absolute():
            continue
        try:
            relative_target = link.resolve().relative_to(source_root)
        except ValueError as exc:
            raise RuntimeAcquisitionError(
                f"Bootstrap Python contains an external absolute symlink: {path}"
            ) from exc
        staged_target = staging / relative_target
        relative_link = os.path.relpath(staged_target, path.parent)
        path.unlink()
        path.symlink_to(relative_link, target_is_directory=staged_target.is_dir())


def acquire_uv(
    runtime_root: Path,
    cache_root: Path,
    *,
    artifact: UvArtifact | None = None,
    downloader: Download = _download,
    runner: RunCommand = subprocess.run,
) -> Path:
    selected = artifact or uv_artifact_for_platform()
    uv_root = runtime_root / "uv"
    executable = uv_root / selected.executable_name
    invalid_backup: Path | None = None
    if executable.is_file():
        try:
            verify_uv(executable, runner=runner)
        except RuntimeAcquisitionError:
            invalid_backup = _quarantine_runtime_directory(uv_root)
        else:
            return executable.resolve()
    elif uv_root.exists():
        invalid_backup = _quarantine_runtime_directory(uv_root)

    try:
        cache_root.mkdir(parents=True, exist_ok=True)
        cached_archive = cache_root / selected.archive_name
        if not cached_archive.is_file() or sha256_file(cached_archive) != selected.sha256:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{selected.archive_name}.", suffix=".download", dir=cache_root
            )
            os.close(descriptor)
            temporary = Path(temporary_name)
            try:
                downloader(selected.url, temporary)
                actual = sha256_file(temporary)
                if actual != selected.sha256:
                    raise RuntimeAcquisitionError(
                        "uv artifact SHA-256 mismatch: "
                        f"expected {selected.sha256}, received {actual}"
                    )
                os.replace(temporary, cached_archive)
            finally:
                temporary.unlink(missing_ok=True)
        executable = _extract_uv(cached_archive, uv_root, selected)
        verify_uv(executable, runner=runner)
    except Exception:
        _restore_runtime_directory(uv_root, invalid_backup)
        raise
    else:
        if invalid_backup is not None:
            shutil.rmtree(invalid_backup, ignore_errors=True)
        return executable


def _python_environment(python_root: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("VIRTUAL_ENV", None)
    environment.pop("PYTHONPATH", None)
    environment["UV_PYTHON_INSTALL_DIR"] = str(python_root)
    environment["UV_NO_MODIFY_PATH"] = "1"
    return environment


def _python_candidates(python_root: Path) -> list[Path]:
    candidates: list[Path] = []
    for pattern in ("*/bin/python3.11", "*/bin/python", "*/python.exe"):
        candidates.extend(python_root.glob(pattern))
    return sorted({candidate.resolve() for candidate in candidates if candidate.is_file()})


def verify_python(executable: Path, *, runner: RunCommand = subprocess.run) -> None:
    result = _run_checked([executable, "--version"], runner=runner)
    reported = (result.stdout or result.stderr).strip()
    if reported != f"Python {PYTHON_VERSION}":
        raise RuntimeAcquisitionError(
            f"Expected Python {PYTHON_VERSION}, but {executable} reported {reported!r}"
        )


def acquire_python(
    uv: Path,
    runtime_root: Path,
    *,
    runner: RunCommand = subprocess.run,
) -> Path:
    python_root = runtime_root / "python"
    existing = _python_candidates(python_root)
    invalid_backup: Path | None = None
    if existing:
        for candidate in existing:
            try:
                verify_python(candidate, runner=runner)
            except RuntimeAcquisitionError:
                continue
            return candidate
        invalid_backup = _quarantine_runtime_directory(python_root)
    elif python_root.exists():
        invalid_backup = _quarantine_runtime_directory(python_root)

    try:
        python_root.mkdir(parents=True, exist_ok=True)
        environment = _python_environment(python_root)
        _run_checked(
            [
                uv,
                "python",
                "install",
                PYTHON_VERSION,
                "--install-dir",
                python_root,
                "--no-bin",
                "--no-registry",
                "--managed-python",
            ],
            runner=runner,
            environ=environment,
        )
        candidates = _python_candidates(python_root)
        if not candidates:
            raise RuntimeAcquisitionError(
                f"uv installed Python but no interpreter was found under {python_root}"
            )
        for candidate in candidates:
            try:
                verify_python(candidate, runner=runner)
            except RuntimeAcquisitionError:
                continue
            if invalid_backup is not None:
                shutil.rmtree(invalid_backup, ignore_errors=True)
            return candidate
        raise RuntimeAcquisitionError(
            f"uv did not install the required Python {PYTHON_VERSION} under {python_root}"
        )
    except Exception:
        _restore_runtime_directory(python_root, invalid_backup)
        raise


def acquire_runtimes(
    runtime_root: Path,
    cache_root: Path,
    *,
    downloader: Download = _download,
    runner: RunCommand = subprocess.run,
) -> RuntimePaths:
    platform_name, architecture = platform_identity()
    uv = acquire_uv(
        runtime_root,
        cache_root,
        downloader=downloader,
        runner=runner,
    )
    python = acquire_python(uv, runtime_root, runner=runner)
    return RuntimePaths(
        uv=uv,
        python=python,
        platform_name=platform_name,
        architecture=architecture,
    )


def adopt_bootstrap_runtimes(
    runtime_root: Path,
    _cache_root: Path,
    source: BootstrapRuntimeSource,
    *,
    runner: RunCommand = subprocess.run,
) -> RuntimePaths:
    """Validate and atomically adopt shell-bootstrapped runtimes."""
    platform_name, architecture = platform_identity()
    source_uv = source.uv.expanduser().resolve()
    source_python_root = source.python_root.expanduser().resolve()
    source_python = source.python.expanduser().resolve()
    verify_uv(source_uv, runner=runner)
    verify_python(source_python, runner=runner)
    try:
        python_relative = source_python.relative_to(source_python_root)
    except ValueError as exc:
        raise RuntimeAcquisitionError(
            "Bootstrap Python is outside its declared runtime root"
        ) from exc

    uv_root = runtime_root / "uv"
    installed_uv = uv_root / "uv"
    uv_backup: Path | None = None
    selected_uv: Path | None = None
    if installed_uv.is_file():
        try:
            verify_uv(installed_uv, runner=runner)
        except RuntimeAcquisitionError:
            uv_backup = _quarantine_runtime_directory(uv_root)
        else:
            selected_uv = installed_uv.resolve()
    elif uv_root.exists():
        uv_backup = _quarantine_runtime_directory(uv_root)
    if selected_uv is None:
        staging_uv = runtime_root / f".uv.adopt-{uuid.uuid4().hex}"
        try:
            staging_uv.mkdir(parents=True)
            staged_uv = staging_uv / "uv"
            shutil.copy2(source_uv, staged_uv)
            verify_uv(staged_uv, runner=runner)
            os.replace(staging_uv, uv_root)
            selected_uv = installed_uv.resolve()
        except Exception:
            shutil.rmtree(staging_uv, ignore_errors=True)
            _restore_runtime_directory(uv_root, uv_backup)
            raise
        else:
            if uv_backup is not None:
                shutil.rmtree(uv_backup, ignore_errors=True)

    python_root = runtime_root / "python"
    existing = _python_candidates(python_root)
    selected_python: Path | None = None
    python_backup: Path | None = None
    for candidate in existing:
        try:
            verify_python(candidate, runner=runner)
        except RuntimeAcquisitionError:
            continue
        selected_python = candidate
        break
    if selected_python is None:
        if python_root.exists():
            python_backup = _quarantine_runtime_directory(python_root)
        staging_python = runtime_root / f".python.adopt-{uuid.uuid4().hex}"
        try:
            shutil.copytree(source_python_root, staging_python, symlinks=True)
            _relocate_copied_runtime_symlinks(staging_python, source_python_root)
            staged_python = staging_python / python_relative
            if not staged_python.is_file():
                raise RuntimeAcquisitionError(
                    "Bootstrap Python copy does not contain its interpreter"
                )
            verify_python(staged_python, runner=runner)
            os.replace(staging_python, python_root)
            selected_python = (python_root / python_relative).resolve()
        except Exception:
            shutil.rmtree(staging_python, ignore_errors=True)
            _restore_runtime_directory(python_root, python_backup)
            raise
        else:
            if python_backup is not None:
                shutil.rmtree(python_backup, ignore_errors=True)

    assert selected_python is not None
    return RuntimePaths(
        uv=selected_uv,
        python=selected_python,
        platform_name=platform_name,
        architecture=architecture,
    )
