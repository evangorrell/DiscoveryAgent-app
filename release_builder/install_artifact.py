"""Deterministic production-install artifact construction."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import posixpath
import stat
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


ARTIFACT_SCHEMA_VERSION = 1
ARTIFACT_PLATFORM = "macos"
ARTIFACT_ARCHITECTURE = "arm64"
ARTIFACT_ROOT = "discoveryagent-install"
PYTHON_VERSION = "3.11.11"
UV_VERSION = "0.11.17"
FORBIDDEN_COMPONENTS = {
    ".git",
    ".env",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".DS_Store",
    "local_disk",
}
EXCLUDED_MANAGER_FILES = {"testing.py"}


class InstallArtifactError(RuntimeError):
    """The install artifact could not be built safely."""


@dataclass(frozen=True)
class InstallArtifact:
    archive: Path
    checksum: Path
    manifest: Path
    sha256: str
    release_id: str


def _release_id(repository_root: Path) -> str:
    try:
        payload = json.loads(
            (repository_root / "release.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallArtifactError(f"Could not read release.json: {exc}") from exc
    release_id = payload.get("release_id") if isinstance(payload, dict) else None
    if (
        not isinstance(release_id, str)
        or not release_id
        or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in release_id)
    ):
        raise InstallArtifactError("release.json has an unsafe release_id")
    return release_id


def _payload_entries(repository_root: Path) -> list[tuple[Path, PurePosixPath]]:
    sources = (
        repository_root / "application",
        repository_root / "manager",
        repository_root / "release.json",
        repository_root / "tools" / "install_local_release.py",
    )
    for source in sources:
        if not source.exists():
            raise InstallArtifactError(f"Required install payload is missing: {source}")

    entries: dict[PurePosixPath, Path] = {}
    for source in sources:
        candidates = [source] if source.is_file() else [source, *source.rglob("*")]
        for path in candidates:
            relative = PurePosixPath(path.relative_to(repository_root).as_posix())
            if any(component in FORBIDDEN_COMPONENTS for component in relative.parts):
                if relative.parts[0] == "application":
                    raise InstallArtifactError(
                        f"Canonical application contains forbidden state: {relative}"
                    )
                continue
            if (
                relative.parts[:2] == ("manager", "discoveryagent_cli")
                and relative.name in EXCLUDED_MANAGER_FILES
            ):
                continue
            entries[relative] = path
    return [(path, relative) for relative, path in sorted(entries.items(), key=lambda item: item[0].as_posix())]


def _tar_info(path: Path, archive_name: str) -> tarfile.TarInfo:
    metadata = path.lstat()
    info = tarfile.TarInfo(archive_name)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.pax_headers = {}
    if stat.S_ISDIR(metadata.st_mode):
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
    elif stat.S_ISLNK(metadata.st_mode):
        target = os.readlink(path)
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(archive_name), target))
        root_prefix = f"{ARTIFACT_ROOT}/"
        if target.startswith("/") or not (resolved + "/").startswith(root_prefix):
            raise InstallArtifactError(f"Payload symlink escapes the archive: {path}")
        info.type = tarfile.SYMTYPE
        info.linkname = target
        info.mode = 0o777
    elif stat.S_ISREG(metadata.st_mode):
        info.type = tarfile.REGTYPE
        info.mode = 0o755 if metadata.st_mode & 0o111 else 0o644
        info.size = metadata.st_size
    else:
        raise InstallArtifactError(f"Unsupported payload entry: {path}")
    return info


def _archive_bytes(repository_root: Path) -> bytes:
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.PAX_FORMAT) as bundle:
        root_info = tarfile.TarInfo(ARTIFACT_ROOT)
        root_info.type = tarfile.DIRTYPE
        root_info.mode = 0o755
        root_info.uid = root_info.gid = root_info.mtime = 0
        root_info.uname = root_info.gname = ""
        bundle.addfile(root_info)
        for path, relative in _payload_entries(repository_root):
            name = f"{ARTIFACT_ROOT}/{relative.as_posix()}"
            info = _tar_info(path, name)
            if info.isfile():
                with path.open("rb") as stream:
                    bundle.addfile(info, stream)
            else:
                bundle.addfile(info)
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", filename="", mtime=0, compresslevel=9) as stream:
        stream.write(tar_buffer.getvalue())
    return compressed.getvalue()


def _write_atomic(path: Path, contents: bytes, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def verify_install_artifact(archive: Path, expected_sha256: str) -> None:
    try:
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    except OSError as exc:
        raise InstallArtifactError(f"Could not read install artifact: {exc}") from exc
    if actual != expected_sha256:
        raise InstallArtifactError(
            f"Install artifact SHA-256 mismatch: expected {expected_sha256}, received {actual}"
        )


def build_install_artifact(
    repository_root: Path,
    destination: Path,
) -> InstallArtifact:
    repository = repository_root.expanduser().resolve()
    output = destination.expanduser().resolve()
    release_id = _release_id(repository)
    filename = f"discoveryagent-{release_id}-macos-arm64.tar.gz"
    archive = output / filename
    archive_contents = _archive_bytes(repository)
    digest = hashlib.sha256(archive_contents).hexdigest()
    checksum = output / f"{filename}.sha256"
    manifest = output / f"{filename}.json"
    manifest_payload = {
        "architecture": ARTIFACT_ARCHITECTURE,
        "artifact_filename": filename,
        "artifact_sha256": digest,
        "installer_schema_version": ARTIFACT_SCHEMA_VERSION,
        "platform": ARTIFACT_PLATFORM,
        "python_version": PYTHON_VERSION,
        "release_id": release_id,
        "uv_version": UV_VERSION,
    }
    _write_atomic(archive, archive_contents)
    _write_atomic(checksum, f"{digest}  {filename}\n".encode("utf-8"))
    _write_atomic(
        manifest,
        (json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return InstallArtifact(archive, checksum, manifest, digest, release_id)
