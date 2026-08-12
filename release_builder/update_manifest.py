"""Deterministic public update-manifest construction."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit


class UpdateManifestBuildError(RuntimeError):
    """An artifact manifest cannot produce a safe update manifest."""


_RELEASE_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _read_artifact_manifest(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UpdateManifestBuildError(f"Could not read artifact manifest: {exc}") from exc
    if not isinstance(payload, dict):
        raise UpdateManifestBuildError("Artifact manifest must be a JSON object")
    required = {
        "installer_schema_version",
        "release_id",
        "artifact_filename",
        "artifact_sha256",
        "platform",
        "architecture",
        "python_version",
        "uv_version",
    }
    if set(payload) != required:
        raise UpdateManifestBuildError("Artifact manifest has missing or unsupported fields")
    if payload["installer_schema_version"] != 1:
        raise UpdateManifestBuildError("Unsupported install-artifact manifest schema")
    if payload["platform"] != "macos" or payload["architecture"] != "arm64":
        raise UpdateManifestBuildError("Only the macos-arm64 artifact is supported")
    return payload


def build_update_manifest(
    artifact_manifest: Path,
    *,
    release_tag: str,
    artifact_url: str,
    output: Path,
) -> Path:
    artifact = _read_artifact_manifest(artifact_manifest.expanduser().resolve())
    release_id = artifact["release_id"]
    filename = artifact["artifact_filename"]
    digest = artifact["artifact_sha256"]
    if not isinstance(release_id, str) or not release_id.startswith(("prod-", "dev-")):
        raise UpdateManifestBuildError("Artifact manifest has an unsupported release ID")
    if not isinstance(filename, str) or artifact_url.rsplit("/", 1)[-1] != filename:
        raise UpdateManifestBuildError("Artifact URL does not match its filename")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise UpdateManifestBuildError("Artifact manifest has an invalid SHA-256")
    release_type = "production" if release_id.startswith("prod-") else "development"
    if not _RELEASE_TAG.fullmatch(release_tag):
        raise UpdateManifestBuildError("Release tag is unsafe")
    if release_type == "production":
        parsed = urlsplit(artifact_url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "github.com"
            or parsed.query
            or parsed.fragment
            or unquote(parsed.path).split("/")[-3:] != ["download", release_tag, filename]
        ):
            raise UpdateManifestBuildError("Production artifact URL is not immutable")
    payload = {
        "channel": "stable",
        "platforms": {
            "macos-arm64": {
                "artifact_filename": filename,
                "artifact_sha256": digest,
                "artifact_url": artifact_url,
            }
        },
        "release_id": release_id,
        "release_tag": release_tag,
        "release_type": release_type,
        "schema_version": 1,
    }
    contents = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    destination = output.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
