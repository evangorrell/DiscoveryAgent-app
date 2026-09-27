from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from manager.discoveryagent_cli import update


class PrivateGitHubDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.manifest = b'{"schema_version":1}\n'
        self.archive = b"synthetic private release archive"
        self.calls: list[tuple[list[str], dict]] = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def fake_run(self, command, **kwargs):
        command = list(command)
        self.calls.append((command, kwargs))
        endpoint = command[-1]
        if endpoint.endswith("/releases/latest"):
            payload = {
                "assets": [{"id": 17, "name": "discoveryagent-update.json"}],
            }
            return SimpleNamespace(
                returncode=0, stdout=json.dumps(payload).encode(), stderr=b"",
            )
        if "/releases/tags/" in endpoint:
            payload = {
                "assets": [{
                    "id": 23,
                    "name": "discoveryagent-prod-synthetic-macos-arm64.tar.gz",
                }],
            }
            return SimpleNamespace(
                returncode=0, stdout=json.dumps(payload).encode(), stderr=b"",
            )
        if endpoint.endswith("/assets/17"):
            return SimpleNamespace(returncode=0, stdout=self.manifest, stderr=b"")
        if endpoint.endswith("/assets/23"):
            output = kwargs["stdout"]
            self.assertIsNot(output, subprocess.PIPE)
            output.write(self.archive)
            return SimpleNamespace(returncode=0, stdout=None, stderr=b"")
        raise AssertionError(f"unexpected gh command: {command}")

    def assert_scoped_environments(self) -> None:
        self.assertTrue(self.calls)
        for _command, kwargs in self.calls:
            environment = kwargs["env"]
            self.assertEqual(environment["GH_TOKEN"], "synthetic-github-session")
            self.assertNotIn("ANTHROPIC_API_KEY", environment)
            self.assertNotIn("PAPERCLIP_API_KEY", environment)

    def test_private_latest_manifest_uses_authenticated_github_api(self) -> None:
        environment = {
            "GH_TOKEN": "synthetic-github-session",
            "ANTHROPIC_API_KEY": "must-not-leak",
            "PAPERCLIP_API_KEY": "must-not-leak",
        }
        url = (
            "https://github.com/evangorrell/DiscoveryAgent-app/releases/latest/"
            "download/discoveryagent-update.json"
        )
        with mock.patch.dict(update.os.environ, environment, clear=True), mock.patch(
            "manager.discoveryagent_cli.update.subprocess.run",
            side_effect=self.fake_run,
        ), mock.patch(
            "manager.discoveryagent_cli.update.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(url, 404, "Not Found", {}, None),
        ):
            contents = update.fetch_manifest(url)

        self.assertEqual(contents, self.manifest)
        self.assertIn("repos/evangorrell/DiscoveryAgent-app/releases/latest", self.calls[0][0])
        self.assertIn("Accept: application/octet-stream", self.calls[1][0])
        self.assert_scoped_environments()

    def test_private_immutable_artifact_streams_through_github_api(self) -> None:
        environment = {
            "GH_TOKEN": "synthetic-github-session",
            "ANTHROPIC_API_KEY": "must-not-leak",
            "PAPERCLIP_API_KEY": "must-not-leak",
        }
        filename = "discoveryagent-prod-synthetic-macos-arm64.tar.gz"
        url = (
            "https://github.com/evangorrell/DiscoveryAgent-app/releases/download/"
            f"v1.2.3/{filename}"
        )
        destination = self.root / filename
        with mock.patch.dict(update.os.environ, environment, clear=True), mock.patch(
            "manager.discoveryagent_cli.update.subprocess.run",
            side_effect=self.fake_run,
        ), mock.patch(
            "manager.discoveryagent_cli.update.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(url, 404, "Not Found", {}, None),
        ):
            digest = update.download_artifact(url, destination)

        self.assertEqual(destination.read_bytes(), self.archive)
        self.assertEqual(digest, hashlib.sha256(self.archive).hexdigest())
        self.assertIn(
            "repos/evangorrell/DiscoveryAgent-app/releases/tags/v1.2.3",
            self.calls[0][0],
        )
        self.assert_scoped_environments()

    def test_non_release_and_unsafe_github_urls_do_not_enter_private_path(self) -> None:
        self.assertIsNone(update._github_release_asset("https://example.com/release.tar.gz"))
        self.assertIsNone(update._github_release_asset(
            "https://github.com/evangorrell/DiscoveryAgent-app/releases/download/v1/../bad"
        ))
        self.assertIsNone(update._github_release_asset(
            "https://github.com/evangorrell/DiscoveryAgent-app/releases/download/v1/file?token=x"
        ))


if __name__ == "__main__":
    unittest.main()
