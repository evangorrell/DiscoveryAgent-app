"""Immutable-artifact update flow for installed DiscoveryAgent applications."""

from __future__ import annotations

import hashlib
import os
import posixpath
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, TextIO

from .errors import ManagerError, UpdateError
from .github_releases import GITHUB_HOST, GitHubReleaseAsset, github_release_asset
from .installation import ReleaseMetadata, load_active_release
from .installer import validate_packaged_payload
from .maintenance import require_idle_research, stop_idle_frontend
from .paths import InstallationPaths
from .state import ManagerState, load_state
from .update_manifest import (
    UpdateManifest,
    parse_update_manifest,
    platform_key,
    update_manifest_url,
)


MAXIMUM_MANIFEST_BYTES = 1024 * 1024
ARTIFACT_ROOT = PurePosixPath("discoveryagent-install")


def _github_cli() -> str:
    executable = shutil.which("gh")
    if executable is None:
        raise UpdateError(
            "Private release access requires GitHub CLI. Install it, then run: gh auth login"
        )
    try:
        authentication = subprocess.run(
            [executable, "auth", "status", "--hostname", GITHUB_HOST],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise UpdateError(
            "Could not run GitHub CLI. Verify it is installed, then run: gh auth login"
        ) from exc
    if authentication.returncode != 0:
        raise UpdateError("GitHub CLI is not authenticated. Run: gh auth login")
    return executable


def _github_download_command(
    executable: str,
    asset: GitHubReleaseAsset,
    *,
    output: str,
) -> list[str]:
    command = [executable, "release", "download"]
    if asset.tag is not None:
        command.append(asset.tag)
    command.extend(
        [
            "--repo",
            asset.repository,
            "--pattern",
            asset.filename,
            "--output",
            output,
        ]
    )
    return command


def _private_release_bytes(asset: GitHubReleaseAsset) -> bytes:
    executable = _github_cli()
    try:
        result = subprocess.run(
            _github_download_command(executable, asset, output="-"),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise UpdateError(
            "Could not run GitHub CLI to download the private DiscoveryAgent release"
        ) from exc
    if result.returncode != 0:
        raise UpdateError("Could not download the private DiscoveryAgent release with GitHub CLI")
    return result.stdout


def _private_release_file(asset: GitHubReleaseAsset, destination: Path) -> None:
    executable = _github_cli()
    try:
        result = subprocess.run(
            _github_download_command(executable, asset, output=str(destination)),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise UpdateError(
            "Could not run GitHub CLI to download the private DiscoveryAgent release"
        ) from exc
    if result.returncode != 0 or not destination.is_file():
        destination.unlink(missing_ok=True)
        raise UpdateError("Could not download the private DiscoveryAgent release with GitHub CLI")


def fetch_manifest(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "DiscoveryAgent-manager"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            contents = response.read(MAXIMUM_MANIFEST_BYTES + 1)
    except (OSError, urllib.error.URLError) as exc:
        asset = github_release_asset(url)
        if asset is None:
            raise UpdateError(f"Could not download the update manifest: {exc}") from exc
        contents = _private_release_bytes(asset)
    if len(contents) > MAXIMUM_MANIFEST_BYTES:
        raise UpdateError("Update manifest is unexpectedly large")
    return contents


def download_artifact(url: str, destination: Path) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "DiscoveryAgent-manager"})
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("xb") as stream:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                stream.write(chunk)
    except (OSError, urllib.error.URLError) as exc:
        destination.unlink(missing_ok=True)
        asset = github_release_asset(url)
        if asset is None:
            raise UpdateError(f"Could not download the DiscoveryAgent update: {exc}") from exc
        _private_release_file(asset, destination)
        digest = hashlib.sha256()
        try:
            with destination.open("rb") as stream:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
        except OSError as read_error:
            raise UpdateError(
                f"Could not verify the downloaded DiscoveryAgent update: {read_error}"
            ) from read_error
    return digest.hexdigest()


def extract_install_artifact(archive: Path, destination: Path) -> Path:
    """Extract files, directories, and contained relative symlinks safely."""
    destination.mkdir(mode=0o700, parents=True)
    seen: set[PurePosixPath] = set()
    symlink_targets: dict[PurePosixPath, PurePosixPath] = {}
    try:
        with tarfile.open(archive, mode="r:gz") as bundle:
            members = bundle.getmembers()
            if not members:
                raise UpdateError("Update artifact is empty")
            for member in members:
                relative = PurePosixPath(member.name)
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or "\\" in member.name
                    or not relative.parts
                    or relative.parts[0] != ARTIFACT_ROOT.name
                    or relative in seen
                ):
                    raise UpdateError("Update artifact contains an unsafe path")
                seen.add(relative)
                if member.issym():
                    link = member.linkname
                    link_path = PurePosixPath(link)
                    if (
                        not link
                        or link_path.is_absolute()
                        or ".." in link_path.parts
                        or "\\" in link
                    ):
                        raise UpdateError("Update artifact contains an unsafe symlink")
                    resolved = PurePosixPath(
                        posixpath.normpath(
                            posixpath.join(relative.parent.as_posix(), link)
                        )
                    )
                    if not resolved.parts or resolved.parts[0] != ARTIFACT_ROOT.name:
                        raise UpdateError("Update artifact contains an unsafe symlink")
                    symlink_targets[relative] = resolved
                elif not (member.isdir() or member.isfile()):
                    raise UpdateError("Update artifact contains a hard link or special file")
            for relative in seen:
                if any(parent in symlink_targets for parent in relative.parents):
                    raise UpdateError("Update artifact contains a path beneath a symlink")
            for member in members:
                relative = PurePosixPath(member.name)
                target = destination.joinpath(*relative.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    target.chmod(0o755)
                    continue
                if member.issym():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                if source is None:
                    raise UpdateError("Update artifact contains an unreadable file")
                with source, target.open("xb") as stream:
                    shutil.copyfileobj(source, stream)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
            for member in members:
                if not member.issym():
                    continue
                relative = PurePosixPath(member.name)
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(member.linkname, target)
    except (OSError, tarfile.TarError) as exc:
        raise UpdateError(f"Could not read the update artifact: {exc}") from exc
    payload = destination / ARTIFACT_ROOT.name
    if not (payload / "tools" / "install_local_release.py").is_file():
        raise UpdateError("Update artifact is missing its installer engine")
    return payload


def run_new_installer(payload: Path, paths: InstallationPaths, state: ManagerState) -> None:
    python = Path(state.runtime.python_path)
    uv = Path(state.runtime.uv_path)
    cli = Path(state.cli.path)
    if not python.is_file() or not uv.is_file() or not cli.is_absolute():
        raise UpdateError("Installed runtime or CLI state is invalid")
    python_root = paths.runtime_root / "python"
    try:
        python.resolve().relative_to(python_root.resolve())
        uv.resolve().relative_to(paths.runtime_root.resolve())
    except ValueError as exc:
        raise UpdateError("Installed runtime paths are outside the application runtime") from exc
    command = [
        str(python),
        str(payload / "tools" / "install_local_release.py"),
        "--install-root", str(paths.root),
        "--cli-path", str(cli),
        "--data-root", state.data_root,
        "--bootstrap-uv", str(uv),
        "--bootstrap-python-root", str(python_root),
        "--bootstrap-python", str(python),
        "--quiet",
    ]
    environment = dict(os.environ)
    for name in (
        "ANTHROPIC_API_KEY",
        "PAPERCLIP_API_KEY",
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "CLAUDE_CODE_CLI_PATH",
        "NODE_PATH",
    ):
        environment.pop(name, None)
    environment["PYTHONNOUSERSITE"] = "1"
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            cwd=payload,
            env=environment,
        )
    except OSError as exc:
        raise UpdateError(f"Could not start the new installer: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "installer failed").strip()
        raise UpdateError(f"The new DiscoveryAgent installer failed: {detail}")


@dataclass(frozen=True)
class UpdateDependencies:
    manifest_fetcher: Callable[[str], bytes] = fetch_manifest
    artifact_downloader: Callable[[str, Path], str] = download_artifact
    extractor: Callable[[Path, Path], Path] = extract_install_artifact
    payload_validator: Callable[[Path], ReleaseMetadata] = validate_packaged_payload
    research_guard: Callable[..., None] = require_idle_research
    frontend_stopper: Callable[[InstallationPaths, ManagerState], bool] = stop_idle_frontend
    installer_runner: Callable[[Path, InstallationPaths, ManagerState], None] = run_new_installer


def update_installed_application(
    paths: InstallationPaths,
    *,
    environ: Mapping[str, str],
    stdout: TextIO,
    dependencies: UpdateDependencies | None = None,
) -> int:
    selected = dependencies or UpdateDependencies()
    state = load_state(paths.state_file)
    load_active_release(paths, state, require_layout=True)
    print("Checking for updates...", file=stdout, flush=True)
    manifest_url, internal = update_manifest_url(environ)
    if not internal and not manifest_url.startswith("https://"):
        raise UpdateError("Production update manifest must use HTTPS")
    manifest: UpdateManifest = parse_update_manifest(
        selected.manifest_fetcher(manifest_url),
        internal=internal,
    )
    if manifest.release_id == state.active_release_id:
        print("DiscoveryAgent is up to date.", file=stdout)
        return 0
    key = platform_key(state.platform, state.architecture)
    try:
        artifact = manifest.platforms[key]
    except KeyError as exc:
        raise UpdateError(f"Update manifest does not support {key}") from exc
    selected.research_guard(state, action="updating")

    with tempfile.TemporaryDirectory(prefix="discoveryagent-update-") as temporary_name:
        temporary = Path(temporary_name)
        archive = temporary / artifact.filename
        actual = selected.artifact_downloader(artifact.url, archive)
        if actual != artifact.sha256:
            raise UpdateError(
                f"Update artifact SHA-256 mismatch: expected {artifact.sha256}, received {actual}"
            )
        payload = selected.extractor(archive, temporary / "payload")
        try:
            metadata = selected.payload_validator(payload)
        except ManagerError as exc:
            raise UpdateError(f"Update artifact payload is invalid: {exc}") from exc
        if metadata.release_id != manifest.release_id or metadata.release_type != manifest.release_type:
            raise UpdateError("Update artifact identity does not match its manifest")
        selected.frontend_stopper(paths, state)
        print("Updating DiscoveryAgent...", file=stdout, flush=True)
        selected.installer_runner(payload, paths, state)
        updated = load_state(paths.state_file)
        if updated.active_release_id != manifest.release_id:
            raise UpdateError("The new installer did not activate the expected release")

    print(f"\n✓ DiscoveryAgent updated to {manifest.release_id}\n", file=stdout)
    print("Run `discoveryagent` to launch.", file=stdout)
    return 0
