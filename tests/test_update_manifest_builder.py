from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from release_builder.update_manifest import (
    UpdateManifestBuildError,
    build_update_manifest,
)


class UpdateManifestBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.artifact = self.root / "artifact.json"
        self.filename = "discoveryagent-prod-aaaaaaaaaaaa-bbbbbbbbbbbb-macos-arm64.tar.gz"
        self.artifact.write_text(json.dumps({
            "installer_schema_version": 1,
            "release_id": "prod-aaaaaaaaaaaa-bbbbbbbbbbbb",
            "artifact_filename": self.filename,
            "artifact_sha256": "c" * 64,
            "platform": "macos",
            "architecture": "arm64",
            "python_version": "3.11.11",
            "uv_version": "0.11.17",
        }), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_output_is_deterministic_exact_and_machine_path_free(self) -> None:
        url = f"https://github.com/evangorrell/DiscoveryAgent-app/releases/download/v1.0.0/{self.filename}"
        first = build_update_manifest(
            self.artifact,
            release_tag="v1.0.0",
            artifact_url=url,
            output=self.root / "first.json",
        )
        second = build_update_manifest(
            self.artifact,
            release_tag="v1.0.0",
            artifact_url=url,
            output=self.root / "second.json",
        )
        self.assertEqual(first.read_bytes(), second.read_bytes())
        payload = json.loads(first.read_text())
        selected = payload["platforms"]["macos-arm64"]
        self.assertEqual(selected["artifact_filename"], self.filename)
        self.assertEqual(selected["artifact_url"], url)
        self.assertEqual(selected["artifact_sha256"], "c" * 64)
        self.assertEqual(payload["release_type"], "production")
        self.assertNotIn(str(self.root), first.read_text())

    def test_malformed_artifact_manifest_is_rejected(self) -> None:
        payload = json.loads(self.artifact.read_text())
        del payload["artifact_sha256"]
        self.artifact.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(UpdateManifestBuildError):
            build_update_manifest(
                self.artifact,
                release_tag="v1",
                artifact_url="https://example.invalid/artifact.tar.gz",
                output=self.root / "output.json",
            )


if __name__ == "__main__":
    unittest.main()
