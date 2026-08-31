"""Build a deterministic application snapshot from committed Git objects only."""

from __future__ import annotations

import hashlib
import io
import json
import os
import posixpath
import shutil
import stat
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Literal


SCHEMA_VERSION = 1
APPLICATION_SCHEMA_VERSION = 1
REQUIRED_CPYTHON_VERSION = "3.11.11"
PAPERCLIP_WHEEL = "vendor/gxl_paperclip-0.7.24-py3-none-any.whl"
CANONICAL_FRONTEND_PATH = "application/frontend"
FRONTEND_IMPORT_PROVENANCE = {
    "branch": "v2-ui",
    "commit": "823dc588d2eba138ecac8c00dd7da00662b83986",
    "remote_url": "https://github.com/owencqueen/DA_selection_ui",
    "repository": "DA_selection_ui",
    "tree": "4327cc97576a29c4604f0f88fc523f630f308d25",
}

FRONTEND_REQUIRED = (
    "pyproject.toml",
    "uv.lock",
    "da_selection_ui",
    "da_selection_ui/__main__.py",
    "da_selection_ui/production_server.py",
    "da_selection_ui/templates",
    "da_selection_ui/static",
)
BACKEND_REQUIRED = (
    "pyproject.toml",
    "uv.lock",
    "AIR",
    "scripts/run_hb_sherlock.py",
    PAPERCLIP_WHEEL,
)
FORBIDDEN_COMPONENTS = {
    ".git",
    ".env",
    ".venv",
    "__pycache__",
    "local_disk",
    ".auth_submissions",
    ".interaction_logs",
    ".flask_session",
}

ReleaseMode = Literal["development", "production"]


class ReleaseBuildError(RuntimeError):
    """A source or snapshot failed the deterministic release contract."""


@dataclass(frozen=True)
class SourceRequest:
    repository: Path
    sha: str


@dataclass(frozen=True)
class ResolvedSource:
    repository: Path
    repository_name: str
    remote_url: str
    commit: str
    reachable_remote_refs: tuple[str, ...]
    reachable_from_origin_main: bool


def _run_git(
    repository: Path,
    *arguments: str,
    text: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess:
    command = ["git", "-C", str(repository), *arguments]
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=text,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.strip() if text else result.stderr.decode(errors="replace").strip()
        detail = stderr or "Git command failed"
        raise ReleaseBuildError(f"{detail}: {' '.join(arguments)}")
    return result

def _repository_name(remote_url: str) -> str:
    normalized = remote_url.rstrip("/")
    tail = normalized.rsplit("/", 1)[-1]
    if ":" in tail and "/" not in remote_url:
        tail = tail.rsplit(":", 1)[-1]
    if tail.endswith(".git"):
        tail = tail[:-4]
    if not tail:
        raise ReleaseBuildError("origin URL does not contain a repository name")
    return tail


def _ref_exists(repository: Path, refname: str) -> bool:
    return _run_git(
        repository,
        "show-ref",
        "--verify",
        "--quiet",
        refname,
        check=False,
    ).returncode == 0


def _is_ancestor(repository: Path, commit: str, refname: str) -> bool:
    return _run_git(
        repository,
        "merge-base",
        "--is-ancestor",
        commit,
        refname,
        check=False,
    ).returncode == 0


def resolve_source(request: SourceRequest, mode: ReleaseMode, label: str) -> ResolvedSource:
    repository = request.repository.expanduser().resolve()
    if not repository.is_dir():
        raise ReleaseBuildError(f"{label} repository path is not a directory: {repository}")
    inside = _run_git(repository, "rev-parse", "--is-inside-work-tree", check=False)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise ReleaseBuildError(f"{label} path is not a Git work tree: {repository}")

    resolved = _run_git(
        repository,
        "rev-parse",
        "--verify",
        f"{request.sha}^{{commit}}",
        check=False,
    )
    if resolved.returncode != 0:
        raise ReleaseBuildError(
            f"{label} revision does not resolve to a commit: {request.sha}"
        )
    commit = resolved.stdout.strip()
    object_type = _run_git(repository, "cat-file", "-t", commit).stdout.strip()
    if object_type != "commit":
        raise ReleaseBuildError(f"{label} revision is not a commit: {request.sha}")

    remote = _run_git(repository, "remote", "get-url", "origin", check=False)
    if remote.returncode != 0 or not remote.stdout.strip():
        raise ReleaseBuildError(f"{label} repository has no configured origin remote")
    remote_url = remote.stdout.strip()

    refs_output = _run_git(
        repository,
        "for-each-ref",
        "--format=%(refname)",
        "refs/remotes/origin",
    ).stdout
    remote_refs = sorted(
        ref.strip()
        for ref in refs_output.splitlines()
        if ref.strip() and not ref.strip().endswith("/HEAD")
    )
    reachable_refs = tuple(
        ref for ref in remote_refs if _is_ancestor(repository, commit, ref)
    )
    if not reachable_refs:
        raise ReleaseBuildError(
            f"{label} commit exists locally but is not reachable from any known origin ref: {commit}"
        )

    main_ref = "refs/remotes/origin/main"
    on_main = _ref_exists(repository, main_ref) and _is_ancestor(
        repository, commit, main_ref
    )
    if mode == "production" and not on_main:
        raise ReleaseBuildError(
            f"{label} production commit is not reachable from origin/main: {commit}"
        )

    return ResolvedSource(
        repository=repository,
        repository_name=_repository_name(remote_url),
        remote_url=remote_url,
        commit=commit,
        reachable_remote_refs=reachable_refs,
        reachable_from_origin_main=on_main,
    )


def _safe_archive_members(members: Iterable[tarfile.TarInfo]) -> list[tarfile.TarInfo]:
    checked: list[tarfile.TarInfo] = []
    for member in members:
        name = PurePosixPath(member.name)
        if name.is_absolute() or ".." in name.parts:
            raise ReleaseBuildError(f"Git archive contains an unsafe path: {member.name}")
        if member.isdev() or member.isfifo():
            raise ReleaseBuildError(f"Git archive contains an unsupported entry: {member.name}")
        if member.issym():
            link = PurePosixPath(member.linkname)
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(member.name), member.linkname))
            if link.is_absolute() or resolved == ".." or resolved.startswith("../"):
                raise ReleaseBuildError(
                    f"Git archive symlink escapes the snapshot: {member.name}"
                )
        if member.islnk():
            link = PurePosixPath(member.linkname)
            if link.is_absolute() or ".." in link.parts:
                raise ReleaseBuildError(
                    f"Git archive hard link escapes the snapshot: {member.name}"
                )
        checked.append(member)
    return checked


def _tree_id(source: ResolvedSource, source_path: str | None = None) -> str:
    treeish = f"{source.commit}:{source_path}" if source_path else f"{source.commit}^{{tree}}"
    resolved = _run_git(
        source.repository,
        "rev-parse",
        "--verify",
        treeish,
        check=False,
    )
    if resolved.returncode != 0:
        label = source_path or source.commit
        raise ReleaseBuildError(
            f"{source.repository_name} commit does not contain the required tree: {label}"
        )
    tree = resolved.stdout.strip()
    if _run_git(source.repository, "cat-file", "-t", tree).stdout.strip() != "tree":
        raise ReleaseBuildError(f"Required source path is not a tree: {source_path}")
    return tree


def archive_commit(
    source: ResolvedSource,
    destination: Path,
    *,
    source_path: str | None = None,
) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    treeish = f"{source.commit}:{source_path}" if source_path else source.commit
    archive = _run_git(
        source.repository,
        "archive",
        "--format=tar",
        treeish,
        text=False,
    ).stdout
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
            members = _safe_archive_members(stream.getmembers())
            stream.extractall(destination, members=members)
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseBuildError(
            f"Could not extract {source.repository_name} Git archive: {exc}"
        ) from exc


def _require_snapshot_paths(root: Path, required: Iterable[str], label: str) -> None:
    missing = [relative for relative in required if not (root / relative).exists()]
    if missing:
        raise ReleaseBuildError(
            f"{label} snapshot is missing required paths: {', '.join(missing)}"
        )


def _validate_forbidden_paths(root: Path, label: str) -> None:
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(component in FORBIDDEN_COMPONENTS for component in relative.parts):
            raise ReleaseBuildError(
                f"{label} snapshot contains forbidden path: {relative.as_posix()}"
            )
    if label == "frontend" and (root / "checkpoint").exists():
        raise ReleaseBuildError("frontend snapshot contains forbidden checkout checkpoint data")


def validate_snapshot(frontend: Path, backend: Path) -> None:
    _require_snapshot_paths(frontend, FRONTEND_REQUIRED, "frontend")
    _require_snapshot_paths(backend, BACKEND_REQUIRED, "backend")
    _validate_forbidden_paths(frontend, "frontend")
    _validate_forbidden_paths(backend, "backend")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_metadata(
    source: ResolvedSource,
    lockfile: Path,
    *,
    source_path: str | None = None,
    provenance: dict | None = None,
) -> dict:
    metadata = {
        "repository": source.repository_name,
        "remote_url": source.remote_url,
        "commit": source.commit,
        "tree": _tree_id(source, source_path),
        "uv_lock_sha256": _sha256(lockfile),
        "validation": {
            "commit_exists": True,
            "reachable_from_known_remote": True,
            "reachable_remote_refs": list(source.reachable_remote_refs),
            "reachable_from_origin_main": source.reachable_from_origin_main,
        },
    }
    if source_path is not None:
        metadata["source_path"] = source_path
    if provenance is not None:
        metadata["provenance"] = provenance
    return metadata


def _release_id(mode: ReleaseMode, app_sha: str, backend_sha: str) -> str:
    prefix = "dev" if mode == "development" else "prod"
    return f"{prefix}-{app_sha[:12]}-{backend_sha[:12]}"


def create_release_metadata(
    mode: ReleaseMode,
    app: ResolvedSource,
    backend: ResolvedSource,
    application_root: Path,
) -> dict:
    wheel = application_root / "backend" / PAPERCLIP_WHEEL
    return {
        "schema_version": SCHEMA_VERSION,
        "release_id": _release_id(mode, app.commit, backend.commit),
        "release_type": mode,
        "required_cpython_version": REQUIRED_CPYTHON_VERSION,
        "application_schema_version": APPLICATION_SCHEMA_VERSION,
        "frontend": _source_metadata(
            app,
            application_root / "frontend" / "uv.lock",
            source_path=CANONICAL_FRONTEND_PATH,
            provenance={
                "relationship": "historical_import",
                **FRONTEND_IMPORT_PROVENANCE,
            },
        ),
        "backend": _source_metadata(
            backend, application_root / "backend" / "uv.lock"
        ),
        "paperclip": {
            "vendored_wheel": PAPERCLIP_WHEEL,
            "sha256": _sha256(wheel),
        },
    }


def _write_release_json(path: Path, metadata: dict) -> None:
    path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _remove_generated_target(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _replace_snapshot(staging: Path, destination_root: Path) -> None:
    target_application = destination_root / "application"
    target_release = destination_root / "release.json"
    staged_application = staging / "application"
    staged_release = staging / "release.json"
    backup = staging / "previous"
    backup.mkdir()
    old_application = backup / "application"
    old_release = backup / "release.json"
    installed_application = False
    installed_release = False
    try:
        if target_application.exists() or target_application.is_symlink():
            os.replace(target_application, old_application)
        if target_release.exists() or target_release.is_symlink():
            os.replace(target_release, old_release)
        os.replace(staged_application, target_application)
        installed_application = True
        os.replace(staged_release, target_release)
        installed_release = True
    except Exception:
        if installed_release:
            _remove_generated_target(target_release)
        if installed_application:
            _remove_generated_target(target_application)
        if old_application.exists() or old_application.is_symlink():
            os.replace(old_application, target_application)
        if old_release.exists() or old_release.is_symlink():
            os.replace(old_release, target_release)
        raise


def build_release(
    app_request: SourceRequest,
    backend_request: SourceRequest,
    *,
    mode: ReleaseMode,
    destination_root: Path,
) -> dict:
    if mode not in ("development", "production"):
        raise ReleaseBuildError(f"Unsupported release mode: {mode}")
    destination_root = destination_root.expanduser().resolve()
    destination_root.mkdir(parents=True, exist_ok=True)

    app = resolve_source(app_request, mode, "app frontend")
    backend = resolve_source(backend_request, mode, "backend")

    staging_path = Path(
        tempfile.mkdtemp(prefix=".release-build-", dir=destination_root)
    )
    try:
        application = staging_path / "application"
        application.mkdir()
        archive_commit(
            app,
            application / "frontend",
            source_path=CANONICAL_FRONTEND_PATH,
        )
        archive_commit(backend, application / "backend")
        validate_snapshot(application / "frontend", application / "backend")
        metadata = create_release_metadata(mode, app, backend, application)
        _write_release_json(staging_path / "release.json", metadata)
        _replace_snapshot(staging_path, destination_root)
        return metadata
    except ReleaseBuildError:
        raise
    except Exception as exc:
        raise ReleaseBuildError(f"Could not replace application snapshot: {exc}") from exc
    finally:
        shutil.rmtree(staging_path, ignore_errors=True)


def snapshot_manifest(root: Path) -> list[tuple[str, str, int, str]]:
    """Return a deterministic content/mode manifest used by tests and audits."""
    result: list[tuple[str, str, int, str]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            result.append((relative, "symlink", mode, os.readlink(path)))
        elif path.is_file():
            result.append((relative, "file", mode, _sha256(path)))
    return result
