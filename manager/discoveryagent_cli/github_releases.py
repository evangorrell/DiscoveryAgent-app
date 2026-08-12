"""Recognize DiscoveryAgent assets hosted by the configured GitHub repository."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urlsplit


GITHUB_HOST = "github.com"
GITHUB_RELEASE_REPOSITORY = "evangorrell/DiscoveryAgent-app"


@dataclass(frozen=True)
class GitHubReleaseAsset:
    repository: str
    tag: str | None
    filename: str


def github_release_asset(url: str) -> GitHubReleaseAsset | None:
    """Return authenticated-download metadata for an exact release-asset URL."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != GITHUB_HOST
        or parsed.query
        or parsed.fragment
    ):
        return None
    parts = unquote(parsed.path).strip("/").split("/")
    repository_parts = GITHUB_RELEASE_REPOSITORY.split("/")
    prefix = [*repository_parts, "releases"]
    if parts[: len(prefix)] != prefix:
        return None
    remainder = parts[len(prefix) :]
    if len(remainder) == 3 and remainder[:2] == ["latest", "download"]:
        tag = None
        filename = remainder[2]
    elif len(remainder) == 3 and remainder[0] == "download":
        tag = remainder[1]
        filename = remainder[2]
    else:
        return None
    if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename:
        return None
    return GitHubReleaseAsset(GITHUB_RELEASE_REPOSITORY, tag, filename)
