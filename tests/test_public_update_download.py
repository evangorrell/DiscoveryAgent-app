from __future__ import annotations

import hashlib
import io
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from manager.discoveryagent_cli import update
from manager.discoveryagent_cli.errors import UpdateError
from manager.discoveryagent_cli.update_manifest import PUBLIC_UPDATE_MANIFEST_URL


ARTIFACT_URL = (
    "https://github.com/evangorrell/DiscoveryAgent-app/releases/download/"
    "v1.0.0/discoveryagent-prod-synthetic-macos-arm64.tar.gz"
)


class PublicUpdateDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.destination = Path(temporary.name) / "app.tar.gz"
        self.no_gh = mock.patch.object(
            update.subprocess, "run", side_effect=AssertionError("Public download invoked gh")
        ).start()
        self.addCleanup(mock.patch.stopall)

    def download(self, kind: str, url: str):
        if kind == "manifest":
            return update.fetch_manifest(url)
        return update.download_artifact(url, self.destination)

    def test_public_downloads_need_neither_gh_nor_credentials(self) -> None:
        manifest = b'{"schema_version": 1}\n'
        archive = b"archive" * 200_000  # Exercise multiple streamed chunks.
        with mock.patch.dict(update.os.environ, {}, clear=True), mock.patch.object(
            update.urllib.request, "urlopen", side_effect=[io.BytesIO(manifest), io.BytesIO(archive)]
        ) as network:
            self.assertEqual(update.fetch_manifest(PUBLIC_UPDATE_MANIFEST_URL), manifest)
            self.assertEqual(
                update.download_artifact(ARTIFACT_URL, self.destination),
                hashlib.sha256(archive).hexdigest(),
            )
        self.assertEqual(self.destination.read_bytes(), archive)
        self.assertEqual(network.call_args_list[0].kwargs["timeout"], 30)
        self.assertEqual(network.call_args_list[1].kwargs["timeout"], 60)
        self.no_gh.assert_not_called()

    def test_public_requests_do_not_send_available_credentials(self) -> None:
        credentials = {name: "not-for-public-http" for name in (
            "GH_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY", "PAPERCLIP_API_KEY"
        )}
        with mock.patch.dict(update.os.environ, credentials, clear=True), mock.patch.object(
            update.urllib.request, "urlopen", side_effect=[io.BytesIO(b"{}"), io.BytesIO(b"archive")]
        ) as network:
            update.fetch_manifest(PUBLIC_UPDATE_MANIFEST_URL)
            update.download_artifact(ARTIFACT_URL, self.destination)
        for call in network.call_args_list:
            self.assertEqual(dict(call.args[0].header_items()), {"User-agent": "DiscoveryAgent-manager"})

    def test_access_errors_allow_private_fallback_for_github_release_urls_only(self) -> None:
        for kind, url, helper in (
            ("manifest", PUBLIC_UPDATE_MANIFEST_URL, "_private_github_asset_bytes"),
            ("artifact", ARTIFACT_URL, "_download_private_github_asset"),
        ):
            for status in (401, 403, 404):
                with self.subTest(kind=kind, status=status), mock.patch.object(
                    update.urllib.request, "urlopen",
                    side_effect=urllib.error.HTTPError(url, status, "Access denied", {}, None),
                ), mock.patch.object(update, helper, return_value="fallback") as fallback:
                    self.assertEqual(self.download(kind, url), "fallback")
                    self.assertEqual(fallback.call_args.args[0].repository, "evangorrell/DiscoveryAgent-app")
                    fallback.assert_called_once()

    def test_network_and_server_failures_do_not_prompt_for_github_login(self) -> None:
        for kind, url in (("manifest", PUBLIC_UPDATE_MANIFEST_URL), ("artifact", ARTIFACT_URL)):
            for error in (
                urllib.error.URLError("TLS verification failed"),
                TimeoutError("timed out"),
                urllib.error.HTTPError(url, 429, "Too many requests", {}, None),
                urllib.error.HTTPError(url, 503, "Unavailable", {}, None),
            ):
                with self.subTest(kind=kind, error=str(error)), mock.patch.object(
                    update.urllib.request, "urlopen", side_effect=error
                ), self.assertRaises(UpdateError):
                    self.download(kind, url)
        self.no_gh.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_non_github_access_errors_never_use_github_credentials(self) -> None:
        for kind in ("manifest", "artifact"):
            for url in ("https://example.com/file", "https://github.com/owner/repo/blob/main/file"):
                with self.subTest(kind=kind, url=url), mock.patch.object(
                    update.urllib.request, "urlopen",
                    side_effect=urllib.error.HTTPError(url, 404, "Not Found", {}, None),
                ), self.assertRaises(UpdateError):
                    self.download(kind, url)
        self.no_gh.assert_not_called()

    def test_public_manifest_size_limit_remains_enforced(self) -> None:
        with mock.patch.object(update.urllib.request, "urlopen", return_value=io.BytesIO(
            b"x" * (update.MAXIMUM_MANIFEST_BYTES + 1)
        )), self.assertRaisesRegex(UpdateError, "unexpectedly large"):
            update.fetch_manifest(PUBLIC_UPDATE_MANIFEST_URL)

    def test_existing_destination_is_not_overwritten_or_retried_with_auth(self) -> None:
        self.destination.write_bytes(b"preserve")
        with mock.patch.object(update.urllib.request, "urlopen", return_value=io.BytesIO(b"new")):
            with self.assertRaises(UpdateError):
                update.download_artifact(ARTIFACT_URL, self.destination)
        self.assertEqual(self.destination.read_bytes(), b"preserve")
        self.no_gh.assert_not_called()

    def test_missing_gh_after_private_response_has_actionable_error(self) -> None:
        for kind, url in (("manifest", PUBLIC_UPDATE_MANIFEST_URL), ("artifact", ARTIFACT_URL)):
            with self.subTest(kind=kind), mock.patch.object(
                update.urllib.request, "urlopen",
                side_effect=urllib.error.HTTPError(url, 404, "Not Found", {}, None),
            ), mock.patch.object(update.subprocess, "run", side_effect=FileNotFoundError):
                with self.assertRaisesRegex(UpdateError, "gh auth login"):
                    self.download(kind, url)

    def test_real_http_redirect_and_streaming_download_without_auth(self) -> None:
        manifest = b'{"schema_version":1}\n'
        archive = b"real streamed response" * 80_000
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get("Authorization")))
                if self.path == "/latest":
                    self.send_response(302)
                    self.send_header("Location", "/manifest")
                    self.end_headers()
                    return
                contents = manifest if self.path == "/manifest" else archive
                self.send_response(200)
                self.send_header("Content-Length", str(len(contents)))
                self.end_headers()
                self.wfile.write(contents)

            def log_message(self, *_args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                self.assertEqual(update.fetch_manifest(base + "/latest"), manifest)
                self.assertEqual(
                    update.download_artifact(base + "/archive", self.destination),
                    hashlib.sha256(archive).hexdigest(),
                )
            finally:
                server.shutdown()
                thread.join(timeout=5)
        self.assertEqual(self.destination.read_bytes(), archive)
        self.assertEqual(seen, [("/latest", None), ("/manifest", None), ("/archive", None)])
        self.no_gh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
