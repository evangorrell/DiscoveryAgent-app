"""Immutable-artifact update flow for installed DiscoveryAgent applications."""

from __future__ import annotations

import hashlib
import json
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
from urllib.parse import quote, unquote, urlsplit

from .errors import ManagerError, UpdateError
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


@dataclass(frozen=True)
class _GitHubReleaseAsset:
    repository: str
    release_endpoint: str
    filename: str


def _github_release_asset(url: str) -> _GitHubReleaseAsset | None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query or parsed.fragment:
        return None
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) != 6 or parts[2] != "releases":
        return None
    owner, repository = parts[:2]
    if parts[3:5] == ["latest", "download"]:
        selector, filename = "latest", parts[5]
    elif parts[3] == "download":
        selector, filename = parts[4], parts[5]
    else:
        return None
    safe_component = lambda value: bool(value) and value not in {".", ".."} and "/" not in value and "\\" not in value
    if not all(safe_component(value) for value in (owner, repository, filename)):
        return None
    repository_name = f"{owner}/{repository}"
    if selector == "latest":
        endpoint = f"repos/{repository_name}/releases/latest"
    elif safe_component(selector) and selector != "download":
        endpoint = f"repos/{repository_name}/releases/tags/{quote(selector, safe='')}"
    else:
        return None
    return _GitHubReleaseAsset(repository_name, endpoint, filename)


def _github_cli_environment() -> dict[str, str]:
    environment = dict(os.environ)
    # Provider credentials are unrelated to GitHub release access and must
    # never be inherited by the helper process. GH_TOKEN/GITHUB_TOKEN remain
    # available for the GitHub CLI's normal session-scoped authentication.
    for name in ("ANTHROPIC_API_KEY", "PAPERCLIP_API_KEY"):
        environment.pop(name, None)
    return environment


def _run_gh_api(arguments: list[str], *, stdout=None) -> subprocess.CompletedProcess:
    command = ["gh", "api", "--method", "GET", *arguments]
    options = {
        "check": False,
        "stderr": subprocess.PIPE,
        "env": _github_cli_environment(),
    }
    if stdout is None:
        options["stdout"] = subprocess.PIPE
    else:
        options["stdout"] = stdout
    try:
        result = subprocess.run(command, **options)
    except FileNotFoundError as exc:
        raise UpdateError(
            "Private DiscoveryAgent updates require GitHub CLI. Install gh and run gh auth login."
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or b"").decode(errors="replace").strip()
        raise UpdateError(
            "Could not access the private DiscoveryAgent GitHub release. "
            "Run gh auth login with an account that can access the release"
            + (f": {detail}" if detail else ".")
        )
    return result


def _github_asset_api_endpoint(asset: _GitHubReleaseAsset) -> str:
    metadata_result = _run_gh_api([
        "-H", "Accept: application/vnd.github+json", asset.release_endpoint,
    ])
    try:
        metadata = json.loads(metadata_result.stdout)
        assets = metadata.get("assets") if isinstance(metadata, dict) else None
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise UpdateError("GitHub returned invalid private release metadata") from exc
    if not isinstance(assets, list):
        raise UpdateError("GitHub private release metadata does not contain assets")
    matches = [
        value for value in assets
        if isinstance(value, dict)
        and value.get("name") == asset.filename
        and isinstance(value.get("id"), int)
    ]
    if len(matches) != 1:
        raise UpdateError(
            f"Private GitHub release does not contain exactly one {asset.filename} asset"
        )
    return f"repos/{asset.repository}/releases/assets/{matches[0]['id']}"


def _private_github_asset_bytes(asset: _GitHubReleaseAsset, maximum_bytes: int) -> bytes:
    endpoint = _github_asset_api_endpoint(asset)
    result = _run_gh_api([
        "-H", "Accept: application/octet-stream", endpoint,
    ])
    contents = result.stdout or b""
    if len(contents) > maximum_bytes:
        raise UpdateError("Private GitHub release asset is unexpectedly large")
    return contents


def _download_private_github_asset(asset: _GitHubReleaseAsset, destination: Path) -> str:
    endpoint = _github_asset_api_endpoint(asset)
    try:
        with destination.open("xb") as stream:
            _run_gh_api([
                "-H", "Accept: application/octet-stream", endpoint,
            ], stdout=stream)
    except OSError as exc:
        raise UpdateError(f"Could not store the DiscoveryAgent update: {exc}") from exc
    digest = hashlib.sha256()
    try:
        with destination.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise UpdateError(f"Could not verify the DiscoveryAgent update: {exc}") from exc
    return digest.hexdigest()


def fetch_manifest(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "DiscoveryAgent-manager"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            contents = response.read(MAXIMUM_MANIFEST_BYTES + 1)
    except urllib.error.HTTPError as exc:
        # Public releases need no credentials or GitHub CLI. Only retry an
        # access-related response through authenticated GitHub asset access.
        exc.close()
        private_asset = _github_release_asset(url)
        if private_asset is not None and exc.code in {401, 403, 404}:
            return _private_github_asset_bytes(private_asset, MAXIMUM_MANIFEST_BYTES)
        raise UpdateError(f"Could not download the update manifest: {exc}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise UpdateError(f"Could not download the update manifest: {exc}") from exc
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
    except urllib.error.HTTPError as exc:
        exc.close()
        private_asset = _github_release_asset(url)
        if private_asset is not None and exc.code in {401, 403, 404}:
            return _download_private_github_asset(private_asset, destination)
        raise UpdateError(f"Could not download the DiscoveryAgent update: {exc}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise UpdateError(f"Could not download the DiscoveryAgent update: {exc}") from exc
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
    active = load_active_release(paths, state, require_layout=True)
    print("Checking for updates...", file=stdout, flush=True)
    manifest_url, internal = update_manifest_url(environ)
    if not internal and not manifest_url.startswith("https://"):
        raise UpdateError("Production update manifest must use HTTPS")
    manifest: UpdateManifest = parse_update_manifest(
        selected.manifest_fetcher(manifest_url),
        internal=internal,
    )
    if manifest.package_revision < active.metadata.package_revision:
        raise UpdateError("Refusing an older package revision; the installed package is newer")
    if manifest.release_id == active.metadata.release_id and manifest.package_revision == active.metadata.package_revision:
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
        if (metadata.release_id != manifest.release_id or metadata.release_type != manifest.release_type
                or metadata.package_revision != manifest.package_revision):
            raise UpdateError("Update artifact identity does not match its manifest")
        selected.frontend_stopper(paths, state)
        # Research may have started while the archive was downloading.
        selected.research_guard(state, action="updating")
        print("Updating DiscoveryAgent...", file=stdout, flush=True)
        selected.installer_runner(payload, paths, state)
        updated = load_state(paths.state_file)
        if updated.active_release_id != metadata.installation_id:
            raise UpdateError("The new installer did not activate the expected release")

    print(f"\n✓ DiscoveryAgent updated to {manifest.release_id}\n", file=stdout)
    print("Run `discoveryagent` to launch.", file=stdout)
    return 0
