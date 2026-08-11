from __future__ import annotations

import hashlib
import io
import os
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


class PublicInstallerShellTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "home"
        self.tmpdir = self.root / "tmp"
        self.home.mkdir()
        self.tmpdir.mkdir()
        self.script = Path(__file__).resolve().parents[1] / "install.sh"
        self.artifact = self.root / "artifact.tar.gz"
        self.uv_archive = self.root / "uv.tar.gz"
        self._make_fake_artifact(self.artifact)
        self._make_fake_uv(self.uv_archive)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def environment(self) -> dict[str, str]:
        poison = self.root / "poison"
        poison.mkdir(exist_ok=True)
        for command in ("uv", "python", "python3"):
            executable = poison / command
            executable.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
            executable.chmod(0o755)
        environment = dict(os.environ)
        environment.update(
            {
                "HOME": str(self.home),
                "TMPDIR": str(self.tmpdir),
                "PATH": f"{poison}:/usr/bin:/bin:/usr/sbin:/sbin",
                "DISCOVERYAGENT_INTERNAL_PLATFORM": "Darwin",
                "DISCOVERYAGENT_INTERNAL_ARCHITECTURE": "arm64",
                "DISCOVERYAGENT_INTERNAL_ARTIFACT_URL": self.artifact.as_uri(),
                "DISCOVERYAGENT_INTERNAL_ARTIFACT_SHA256": self.sha256(
                    self.artifact
                ),
                "DISCOVERYAGENT_INTERNAL_UV_URL": self.uv_archive.as_uri(),
                "DISCOVERYAGENT_INTERNAL_UV_SHA256": self.sha256(self.uv_archive),
            }
        )
        environment.pop("ANTHROPIC_API_KEY", None)
        environment.pop("PAPERCLIP_API_KEY", None)
        return environment

    def run_installer(self, environment: dict[str, str] | None = None):
        return subprocess.run(
            ["/bin/sh", str(self.script)],
            env=environment or self.environment(),
            check=False,
            capture_output=True,
            text=True,
        )

    def _make_fake_artifact(self, destination: Path) -> None:
        installer = """from pathlib import Path
import json, os, sys
home = Path(os.environ["HOME"])
arguments = sys.argv[1:]
required = {"--bootstrap-uv", "--bootstrap-python-root", "--bootstrap-python", "--quiet"}
if not required.issubset(arguments):
    raise SystemExit("bootstrap arguments missing")
local_bin = home / ".local/bin"
local_bin.mkdir(parents=True, exist_ok=True)
command = local_bin / "discoveryagent"
command.write_text("#!/bin/sh\\nprintf '%s\\n' 'DiscoveryAgent fake-release'\\n", encoding="utf-8")
command.chmod(0o755)
profile = home / ".zprofile"
stanza = '# >>> DiscoveryAgent managed PATH >>>\\nexport PATH="$HOME/.local/bin:$PATH"\\n# <<< DiscoveryAgent managed PATH <<<\\n'
contents = profile.read_text(encoding="utf-8") if profile.exists() else ""
if stanza not in contents:
    profile.write_text(contents + stanza, encoding="utf-8")
count_file = home / "installer-invocations"
count = int(count_file.read_text() or "0") if count_file.exists() else 0
count_file.write_text(str(count + 1), encoding="utf-8")
(home / "state.json").write_text(json.dumps({"invoked": True}), encoding="utf-8")
"""
        with tarfile.open(destination, "w:gz") as bundle:
            for directory in (
                "discoveryagent-install",
                "discoveryagent-install/tools",
            ):
                info = tarfile.TarInfo(directory)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                bundle.addfile(info)
            payload = installer.encode("utf-8")
            info = tarfile.TarInfo(
                "discoveryagent-install/tools/install_local_release.py"
            )
            info.mode = 0o755
            info.size = len(payload)
            bundle.addfile(info, io.BytesIO(payload))

    def _make_fake_uv(self, destination: Path) -> None:
        python = shlex.quote(sys.executable)
        script = f"""#!/bin/sh
set -eu
if [ "$1" = "--version" ]; then
    echo 'uv 0.11.17'
    exit 0
fi
if [ "$1" = "python" ] && [ "$2" = "install" ]; then
    target="$UV_PYTHON_INSTALL_DIR/cpython-3.11.11-macos-aarch64-none/bin"
    mkdir -p "$target"
    cat > "$target/python3.11" <<'PYTHON_WRAPPER'
#!/bin/sh
if [ "${{1:-}}" = "--version" ]; then
    echo 'Python 3.11.11'
    exit 0
fi
exec {python} "$@"
PYTHON_WRAPPER
    chmod 755 "$target/python3.11"
    exit 0
fi
exit 2
"""
        with tarfile.open(destination, "w:gz") as bundle:
            directory = tarfile.TarInfo("uv-aarch64-apple-darwin")
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o755
            bundle.addfile(directory)
            payload = script.encode("utf-8")
            info = tarfile.TarInfo("uv-aarch64-apple-darwin/uv")
            info.mode = 0o755
            info.size = len(payload)
            bundle.addfile(info, io.BytesIO(payload))

    def assert_no_installer_temporary_directories(self) -> None:
        self.assertEqual(
            list(self.tmpdir.glob("discoveryagent-installer.*")),
            [],
        )

    def test_success_uses_verified_bootstrap_and_reinstall_is_idempotent(self) -> None:
        first = self.run_installer()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("DiscoveryAgent installed successfully.", first.stdout)
        self.assertTrue((self.home / ".local/bin/discoveryagent").is_file())
        self.assertEqual((self.home / "installer-invocations").read_text(), "1")
        profile = self.home / ".zprofile"
        self.assertEqual(
            profile.read_text().count("# >>> DiscoveryAgent managed PATH >>>"), 1
        )
        self.assert_no_installer_temporary_directories()

        second = self.run_installer()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual((self.home / "installer-invocations").read_text(), "2")
        self.assertEqual(
            profile.read_text().count("# >>> DiscoveryAgent managed PATH >>>"), 1
        )
        self.assert_no_installer_temporary_directories()

    def test_unsupported_platform_stops_before_download(self) -> None:
        environment = self.environment()
        environment["DISCOVERYAGENT_INTERNAL_PLATFORM"] = "Linux"
        environment["DISCOVERYAGENT_INTERNAL_ARTIFACT_URL"] = "https://127.0.0.1:1/no"
        result = self.run_installer(environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("supports Apple Silicon macOS", result.stderr)
        self.assertFalse((self.home / ".local").exists())

    def test_download_failure_and_sha_mismatch_clean_up(self) -> None:
        environment = self.environment()
        environment["DISCOVERYAGENT_INTERNAL_ARTIFACT_URL"] = "https://127.0.0.1:1/no"
        failed = self.run_installer(environment)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("could not download", failed.stderr)
        self.assert_no_installer_temporary_directories()

        environment = self.environment()
        environment["DISCOVERYAGENT_INTERNAL_ARTIFACT_SHA256"] = "0" * 64
        mismatch = self.run_installer(environment)
        self.assertNotEqual(mismatch.returncode, 0)
        self.assertIn("integrity verification failed", mismatch.stderr)
        self.assert_no_installer_temporary_directories()

    def test_corrupt_and_unsafe_archives_are_rejected(self) -> None:
        corrupt = self.root / "corrupt.tar.gz"
        corrupt.write_bytes(b"not a tar archive")
        environment = self.environment()
        environment["DISCOVERYAGENT_INTERNAL_ARTIFACT_URL"] = corrupt.as_uri()
        environment["DISCOVERYAGENT_INTERNAL_ARTIFACT_SHA256"] = self.sha256(corrupt)
        result = self.run_installer(environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("archive is corrupt", result.stderr)
        self.assert_no_installer_temporary_directories()

        unsafe = self.root / "unsafe.tar.gz"
        with tarfile.open(unsafe, "w:gz") as bundle:
            payload = b"unsafe"
            info = tarfile.TarInfo("../escape")
            info.size = len(payload)
            bundle.addfile(info, io.BytesIO(payload))
        environment = self.environment()
        environment["DISCOVERYAGENT_INTERNAL_ARTIFACT_URL"] = unsafe.as_uri()
        environment["DISCOVERYAGENT_INTERNAL_ARTIFACT_SHA256"] = self.sha256(unsafe)
        result = self.run_installer(environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe path", result.stderr)
        self.assertFalse((self.root / "escape").exists())
        self.assert_no_installer_temporary_directories()

        unsafe_link = self.root / "unsafe-link.tar.gz"
        with tarfile.open(unsafe_link, "w:gz") as bundle:
            directory = tarfile.TarInfo("discoveryagent-install")
            directory.type = tarfile.DIRTYPE
            bundle.addfile(directory)
            link = tarfile.TarInfo("discoveryagent-install/escape")
            link.type = tarfile.SYMTYPE
            link.linkname = "/private/tmp/escape"
            bundle.addfile(link)
        environment = self.environment()
        environment["DISCOVERYAGENT_INTERNAL_ARTIFACT_URL"] = unsafe_link.as_uri()
        environment["DISCOVERYAGENT_INTERNAL_ARTIFACT_SHA256"] = self.sha256(
            unsafe_link
        )
        result = self.run_installer(environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe symlink", result.stderr)
        self.assert_no_installer_temporary_directories()


if __name__ == "__main__":
    unittest.main()
