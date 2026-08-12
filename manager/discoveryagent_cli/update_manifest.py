"""Schema-v1 public update-channel contract."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from .errors import UpdateError
from .github_releases import GITHUB_HOST, GITHUB_RELEASE_REPOSITORY
from .paths import validate_release_id


UPDATE_SCHEMA_VERSION = 1
PUBLIC_UPDATE_MANIFEST_URL = (
    f"https://{GITHUB_HOST}/{GITHUB_RELEASE_REPOSITORY}/releases/latest/download/"
    "discoveryagent-update.json"
)
INTERNAL_UPDATE_MANIFEST_URL_ENV = "DISCOVERYAGENT_INTERNAL_UPDATE_MANIFEST_URL"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_RELEASE_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class UpdateArtifact:
    filename: str
    url: str
    sha256: str


@dataclass(frozen=True)
class UpdateManifest:
    schema_version: int
    channel: str
    release_id: str
    release_type: str
    release_tag: str
    platforms: dict[str, UpdateArtifact]


def update_manifest_url(environ: Mapping[str, str]) -> tuple[str, bool]:
    override = str(environ.get(INTERNAL_UPDATE_MANIFEST_URL_ENV) or "").strip()
    return (override, True) if override else (PUBLIC_UPDATE_MANIFEST_URL, False)


def _exact_keys(payload: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(payload) != expected:
        raise UpdateError(f"{label} has missing or unsupported fields")


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UpdateError(f"{label} must be a non-empty string")
    return value


def _validate_immutable_url(url: str, *, tag: str, filename: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != GITHUB_HOST:
        raise UpdateError("Production update artifacts must use GitHub HTTPS URLs")
    if parsed.query or parsed.fragment or unquote(parsed.path).split("/")[-3:] != [
        "download",
        tag,
        filename,
    ]:
        raise UpdateError("Production update artifact URL must be immutable")


def parse_update_manifest(
    raw: bytes | str,
    *,
    internal: bool = False,
) -> UpdateManifest:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise UpdateError("Update manifest is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise UpdateError("Update manifest must be a JSON object")
    _exact_keys(
        payload,
        {"schema_version", "channel", "release_id", "release_type", "release_tag", "platforms"},
        "Update manifest",
    )
    if payload["schema_version"] != UPDATE_SCHEMA_VERSION:
        raise UpdateError("Unsupported update-manifest schema version")
    channel = _string(payload["channel"], "channel")
    if channel != "stable":
        raise UpdateError("Update manifest is not on the stable channel")
    try:
        release_id = validate_release_id(_string(payload["release_id"], "release_id"))
    except Exception as exc:
        raise UpdateError("Update manifest has an invalid release ID") from exc
    release_type = _string(payload["release_type"], "release_type")
    if release_type not in {"production", "development"}:
        raise UpdateError("Update manifest has an unsupported release type")
    if release_type != "production" and not internal:
        raise UpdateError("Production updates cannot install development releases")
    release_tag = _string(payload["release_tag"], "release_tag")
    if not _RELEASE_TAG.fullmatch(release_tag):
        raise UpdateError("Update manifest has an unsafe release tag")
    raw_platforms = payload["platforms"]
    if not isinstance(raw_platforms, dict) or not raw_platforms:
        raise UpdateError("Update manifest platforms must be a non-empty object")
    platforms: dict[str, UpdateArtifact] = {}
    for platform_key, value in raw_platforms.items():
        if not isinstance(platform_key, str) or not platform_key or not isinstance(value, dict):
            raise UpdateError("Update manifest contains invalid platform metadata")
        _exact_keys(value, {"artifact_filename", "artifact_url", "artifact_sha256"}, platform_key)
        filename = _string(value["artifact_filename"], f"{platform_key}.artifact_filename")
        if filename != filename.rsplit("/", 1)[-1] or "\\" in filename or not filename.endswith(".tar.gz"):
            raise UpdateError("Update artifact filename is unsafe")
        url = _string(value["artifact_url"], f"{platform_key}.artifact_url")
        digest = _string(value["artifact_sha256"], f"{platform_key}.artifact_sha256")
        if not _SHA256.fullmatch(digest):
            raise UpdateError("Update artifact SHA-256 is invalid")
        if not internal:
            _validate_immutable_url(url, tag=release_tag, filename=filename)
        platforms[platform_key] = UpdateArtifact(filename, url, digest)
    return UpdateManifest(
        UPDATE_SCHEMA_VERSION,
        channel,
        release_id,
        release_type,
        release_tag,
        platforms,
    )


def platform_key(platform_name: str, architecture: str) -> str:
    if platform_name == "darwin" and architecture == "arm64":
        return "macos-arm64"
    raise UpdateError(f"Updates are not available for {platform_name}/{architecture}")
