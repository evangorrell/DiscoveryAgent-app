from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path

from release_builder.install_artifact import (
    ARTIFACT_ARCHITECTURE,
    ARTIFACT_PLATFORM,
    ARTIFACT_ROOT,
    InstallArtifactError,
    build_install_artifact,
    verify_install_artifact,
    write_public_installer,
)


class InstallArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = Path(__file__).resolve().parents[1]

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        # Test an explicit payload, not incidental developer caches/environments.
        source = type(self).repository
        self.repository = self.root / "source"
        for relative in ("application", "manager", "tools"):
            shutil.copytree(source / relative, self.repository / relative, symlinks=True,
                            ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".DS_Store"))
        shutil.copy2(source / "release.json", self.repository / "release.json")
        shutil.copy2(source / "install.sh", self.repository / "install.sh")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def payload_manifest(root: Path) -> dict[str, tuple[str, bytes | str]]:
        result: dict[str, tuple[str, bytes | str]] = {}
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                result[relative] = ("symlink", os.readlink(path))
            elif path.is_file():
                result[relative] = ("file", path.read_bytes())
            elif path.is_dir():
                result[relative] = ("directory", "")
        return result

    def test_build_is_deterministic_and_manifest_records_integrity(self) -> None:
        first = build_install_artifact(self.repository, self.root / "first")
        second = build_install_artifact(self.repository, self.root / "second")
        self.assertEqual(first.archive.read_bytes(), second.archive.read_bytes())
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.checksum.read_bytes(), second.checksum.read_bytes())
        manifest = json.loads(first.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["artifact_sha256"], first.sha256)
        self.assertEqual(manifest["artifact_filename"], first.archive.name)
        self.assertEqual(manifest["platform"], ARTIFACT_PLATFORM)
        self.assertEqual(manifest["architecture"], ARTIFACT_ARCHITECTURE)
        self.assertEqual(manifest["python_version"], "3.11.11")
        self.assertEqual(manifest["uv_version"], "0.11.17")
        self.assertNotIn("built_at", manifest)
        verify_install_artifact(first.archive, first.sha256)
        # The committed installer pins a published asset, not arbitrary local edits.
        self.assertEqual(manifest["release_id"], json.loads((self.repository / "release.json").read_text())["release_id"])
        installer = write_public_installer(self.repository / 'install.sh', first, 'v1.0.0')
        self.assertIn(f'PUBLIC_ARTIFACT_SHA256="{first.sha256}"', installer.read_text())
        self.assertIn(f'ARTIFACT_NAME="{first.archive.name}"', installer.read_text())

    def test_layout_is_minimal_and_application_is_byte_identical(self) -> None:
        artifact = build_install_artifact(self.repository, self.root / "output")
        with tarfile.open(artifact.archive, "r:gz") as bundle:
            names = set(bundle.getnames())
            self.assertIn(f"{ARTIFACT_ROOT}/release.json", names)
            self.assertIn(
                f"{ARTIFACT_ROOT}/tools/install_local_release.py", names
            )
            self.assertIn(
                f"{ARTIFACT_ROOT}/manager/discoveryagent_cli/installer.py", names
            )
            self.assertIn(
                f"{ARTIFACT_ROOT}/application/frontend/uv.lock", names
            )
            self.assertIn(
                f"{ARTIFACT_ROOT}/application/backend/uv.lock", names
            )
            self.assertFalse(any("/.git/" in name for name in names))
            self.assertFalse(any("/__pycache__/" in name for name in names))
            self.assertFalse(any("/.venv/" in name for name in names))
            self.assertFalse(any("/local_disk/" in name for name in names))
            self.assertFalse(
                any(name.startswith(f"{ARTIFACT_ROOT}/tests/") for name in names)
            )
            self.assertFalse(any("/release_builder/" in name for name in names))
            self.assertNotIn(f"{ARTIFACT_ROOT}/README.md", names)
            self.assertNotIn(
                f"{ARTIFACT_ROOT}/manager/discoveryagent_cli/testing.py", names
            )
            extracted = self.root / "extracted"
            bundle.extractall(extracted)
        self.assertEqual(
            self.payload_manifest(self.repository / "application"),
            self.payload_manifest(extracted / ARTIFACT_ROOT / "application"),
        )

    def test_corruption_is_detected(self) -> None:
        artifact = build_install_artifact(self.repository, self.root / "output")
        corrupted = self.root / "corrupted.tar.gz"
        shutil.copy2(artifact.archive, corrupted)
        with corrupted.open("ab") as stream:
            stream.write(b"corruption")
        with self.assertRaisesRegex(InstallArtifactError, "SHA-256 mismatch"):
            verify_install_artifact(corrupted, artifact.sha256)


if __name__ == "__main__":
    unittest.main()
