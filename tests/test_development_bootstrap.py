from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from manager.discoveryagent_cli.bootstrap import (
    BootstrapConfig,
    EnvironmentValidation,
    bootstrap_development_install,
    install_wrapper,
    sync_environment,
)
from manager.discoveryagent_cli.errors import BootstrapError, RuntimeAcquisitionError
from manager.discoveryagent_cli.paths import InstallationPaths
from manager.discoveryagent_cli.runtime import (
    PYTHON_VERSION,
    UV_APPLE_SILICON_SHA256,
    RuntimePaths,
    acquire_uv,
    platform_identity,
    uv_artifact_for_platform,
)
from manager.discoveryagent_cli.state import load_state
from manager.discoveryagent_cli.testing import fixture_release_metadata


class DevelopmentBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        self.install = self.root / "installed"
        self.data = self.root / "durable-data"
        self._make_repository()
        self.sync_calls: list[tuple[Path, Path]] = []
        self.validation_calls: list[Path] = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _make_repository(self) -> None:
        (self.repository / "application" / "frontend").mkdir(parents=True)
        (self.repository / "application" / "backend").mkdir(parents=True)
        (self.repository / "application" / "frontend" / "frontend.txt").write_text(
            "frontend snapshot\n", encoding="utf-8"
        )
        (self.repository / "application" / "backend" / "backend.txt").write_text(
            "backend snapshot\n", encoding="utf-8"
        )
        manager = self.repository / "manager" / "discoveryagent_cli"
        manager.mkdir(parents=True)
        (manager / "__init__.py").write_text(
            'MANAGER_VERSION = "0.1.0"\n', encoding="utf-8"
        )
        (self.repository / "release.json").write_text(
            json.dumps(fixture_release_metadata(), indent=2) + "\n", encoding="utf-8"
        )

    def acquire(self, runtime_root: Path, _cache_root: Path) -> RuntimePaths:
        uv = runtime_root / "uv" / "uv"
        python = runtime_root / "python" / "managed" / "bin" / "python3.11"
        uv.parent.mkdir(parents=True, exist_ok=True)
        python.parent.mkdir(parents=True, exist_ok=True)
        uv.write_text("fake uv\n", encoding="utf-8")
        python.write_text("fake python\n", encoding="utf-8")
        return RuntimePaths(uv.resolve(), python.resolve())

    def sync(
        self,
        _uv: Path,
        _python: Path,
        project_root: Path,
        environment_root: Path,
        _cache_root: Path,
    ) -> Path:
        self.sync_calls.append((project_root, environment_root))
        python = environment_root / "bin" / "python"
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("environment python\n", encoding="utf-8")
        return python

    def validate(self, release_root: Path, _cache_root: Path) -> EnvironmentValidation:
        self.validation_calls.append(release_root)
        frontend_python = release_root / "envs" / "frontend" / "bin" / "python"
        backend_python = release_root / "envs" / "backend" / "bin" / "python"
        return EnvironmentValidation(
            frontend_python=frontend_python,
            backend_python=backend_python,
            frontend_module=release_root / "envs/frontend/site-packages/da_selection_ui",
            backend_module=release_root / "envs/backend/site-packages/AIR",
            paperclip_version="0.7.24",
            claude_executable=release_root / "envs/backend/site-packages/claude",
        )

    def run_bootstrap(self):
        return bootstrap_development_install(
            BootstrapConfig(self.repository, self.install, self.data),
            runtime_acquirer=self.acquire,
            environment_syncer=self.sync,
            release_validator=self.validate,
        )

    def test_stages_and_finalizes_release_before_state(self) -> None:
        result = self.run_bootstrap()
        release = result.paths.release_root(result.release.release_id)
        self.assertTrue((release / "complete").is_file())
        self.assertTrue((release / "application/frontend/frontend.txt").is_file())
        self.assertTrue((release / "application/backend/backend.txt").is_file())
        self.assertEqual(len(self.sync_calls), 2)
        self.assertTrue(result.paths.state_file.is_file())
        self.assertFalse(any(result.paths.releases_root.glob(".*.staging-*")))
        self.assertFalse(result.reused_release)

    def test_failed_sync_cleans_staging_and_does_not_write_state(self) -> None:
        def fail_sync(*_arguments):
            raise BootstrapError("sync failed")

        with self.assertRaisesRegex(BootstrapError, "sync failed"):
            bootstrap_development_install(
                BootstrapConfig(self.repository, self.install, self.data),
                runtime_acquirer=self.acquire,
                environment_syncer=fail_sync,
                release_validator=self.validate,
            )
        self.assertFalse((self.install / "state.json").exists())
        self.assertFalse(any((self.install / "releases").glob(".*.staging-*")))
        self.assertFalse(any((self.install / "releases").glob("dev-*")))

    def test_failed_validation_does_not_finalize_or_write_state(self) -> None:
        def fail_validation(*_arguments):
            raise BootstrapError("validation failed")

        with self.assertRaisesRegex(BootstrapError, "validation failed"):
            bootstrap_development_install(
                BootstrapConfig(self.repository, self.install, self.data),
                runtime_acquirer=self.acquire,
                environment_syncer=self.sync,
                release_validator=fail_validation,
            )
        self.assertFalse((self.install / "state.json").exists())
        self.assertFalse(any((self.install / "releases").glob("dev-*")))

    def test_failed_final_path_validation_removes_new_release_and_state(self) -> None:
        calls = 0

        def fail_after_move(release_root: Path, cache_root: Path):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise BootstrapError("final path validation failed")
            return self.validate(release_root, cache_root)

        with self.assertRaisesRegex(BootstrapError, "final path validation failed"):
            bootstrap_development_install(
                BootstrapConfig(self.repository, self.install, self.data),
                runtime_acquirer=self.acquire,
                environment_syncer=self.sync,
                release_validator=fail_after_move,
            )
        self.assertEqual(calls, 2)
        self.assertFalse((self.install / "state.json").exists())
        self.assertFalse(any((self.install / "releases").glob("dev-*")))

    def test_runtime_paths_and_data_root_are_recorded_without_source_leakage(self) -> None:
        result = self.run_bootstrap()
        state = load_state(result.paths.state_file)
        self.assertEqual(state.runtime.python_version, PYTHON_VERSION)
        self.assertEqual(state.runtime.python_path, str(result.runtimes.python))
        self.assertEqual(state.runtime.uv_path, str(result.runtimes.uv))
        self.assertEqual(state.data_root, str(self.data.resolve()))
        self.assertFalse(state.cli.path_change_added)
        serialized = result.paths.state_file.read_text(encoding="utf-8")
        self.assertNotIn(str(self.repository), serialized)

    def test_existing_identical_complete_release_is_reused(self) -> None:
        first = self.run_bootstrap()
        installed_manager = first.paths.manager_root / "discoveryagent_cli"
        cache = installed_manager / "__pycache__"
        cache.mkdir()
        (cache / "cli.cpython-311.pyc").write_bytes(b"runtime cache")
        self.sync_calls.clear()
        second = self.run_bootstrap()
        self.assertTrue(second.reused_release)
        self.assertEqual(self.sync_calls, [])
        self.assertEqual(first.paths.release_root(first.release.release_id), self.validation_calls[-1])

    def test_existing_incomplete_release_is_rejected_without_overwrite(self) -> None:
        release = self.install / "releases" / fixture_release_metadata()["release_id"]
        release.mkdir(parents=True)
        sentinel = release / "sentinel"
        sentinel.write_text("keep\n", encoding="utf-8")
        with self.assertRaisesRegex(BootstrapError, "incomplete"):
            self.run_bootstrap()
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")
        self.assertFalse((self.install / "state.json").exists())

    def test_canonical_snapshot_mismatch_rejects_existing_release(self) -> None:
        result = self.run_bootstrap()
        installed_file = (
            result.paths.release_root(result.release.release_id)
            / "application/frontend/frontend.txt"
        )
        installed_file.write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(BootstrapError, "differs"):
            self.run_bootstrap()

    def test_install_root_and_data_root_must_be_absolute(self) -> None:
        with self.assertRaisesRegex(BootstrapError, "install-root"):
            BootstrapConfig(self.repository, Path("relative"), self.data).normalized()
        with self.assertRaisesRegex(BootstrapError, "data-root"):
            BootstrapConfig(self.repository, self.install, Path("relative")).normalized()


class WrapperTests(unittest.TestCase):
    def test_arguments_and_cwd_are_forwarded_from_unrelated_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name) / "install with spaces"
            manager = root / "manager" / "discoveryagent_cli"
            manager.mkdir(parents=True)
            (manager / "__init__.py").write_text("", encoding="utf-8")
            (manager / "cli.py").write_text(
                "import json, os, sys\n"
                "def main():\n"
                "    print(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd()}))\n"
                "    return 0\n",
                encoding="utf-8",
            )
            wrapper = install_wrapper(InstallationPaths(root), Path(sys.executable))
            unrelated = Path(temporary_name) / "unrelated"
            unrelated.mkdir()
            result = subprocess.run(
                [wrapper, "version", "argument with spaces", "--literal=$HOME"],
                check=True,
                capture_output=True,
                text=True,
                cwd=unrelated,
                env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
            )
            payload = json.loads(result.stdout)
            self.assertEqual(
                payload["args"],
                ["version", "argument with spaces", "--literal=$HOME"],
            )
            self.assertEqual(Path(payload["cwd"]).resolve(), unrelated.resolve())


class RuntimeAndSyncTests(unittest.TestCase):
    def test_macos_apple_silicon_artifact_is_exactly_pinned(self) -> None:
        artifact = uv_artifact_for_platform("darwin", "arm64")
        self.assertEqual(artifact.version, "0.11.17")
        self.assertEqual(artifact.sha256, UV_APPLE_SILICON_SHA256)
        self.assertIn("/0.11.17/uv-aarch64-apple-darwin.tar.gz", artifact.url)
        self.assertEqual(platform_identity("macos", "aarch64"), ("darwin", "arm64"))

    def test_unsupported_platform_is_explicit(self) -> None:
        with self.assertRaisesRegex(RuntimeAcquisitionError, "Apple Silicon macOS"):
            uv_artifact_for_platform("win32", "amd64")

    def test_bad_download_checksum_is_rejected_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)

            def bad_download(_url: str, destination: Path) -> None:
                destination.write_bytes(b"not the official archive")

            with self.assertRaisesRegex(RuntimeAcquisitionError, "SHA-256 mismatch"):
                acquire_uv(
                    root / "runtime",
                    root / "cache",
                    downloader=bad_download,
                    runner=lambda *_args, **_kwargs: self.fail("must not execute uv"),
                )

    def test_sync_uses_lock_check_then_frozen_noneditable_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            project = root / "project"
            environment_root = root / "release" / "envs" / "frontend"
            project.mkdir()
            commands: list[tuple[list[str], dict[str, str]]] = []

            def runner(command, **kwargs):
                commands.append((command, kwargs["env"]))
                if command[1] == "sync":
                    python = environment_root / "bin" / "python"
                    python.parent.mkdir(parents=True)
                    python.symlink_to(Path(sys.executable))
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            installed_python = sync_environment(
                root / "runtime/uv/uv",
                root / "runtime/python/python",
                project,
                environment_root,
                root / "cache",
                runner=runner,
            )
            self.assertEqual(installed_python, (environment_root / "bin" / "python").absolute())
            self.assertNotEqual(installed_python, installed_python.resolve())
            self.assertEqual(commands[0][0][1:3], ["lock", "--check"])
            self.assertIn("--python", commands[0][0])
            self.assertEqual(commands[1][0][1], "sync")
            for flag in ("--frozen", "--no-dev", "--no-editable", "--python"):
                self.assertIn(flag, commands[1][0])
            self.assertEqual(
                commands[1][1]["UV_PROJECT_ENVIRONMENT"], str(environment_root)
            )
            self.assertNotIn("VIRTUAL_ENV", commands[1][1])
            self.assertNotIn("PYTHONPATH", commands[1][1])


if __name__ == "__main__":
    unittest.main()
