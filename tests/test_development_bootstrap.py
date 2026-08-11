from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from manager.discoveryagent_cli.bootstrap import (
    BootstrapConfig,
    EnvironmentValidation,
    bootstrap_development_install,
    install_wrapper,
    refresh_development_manager,
    sync_environment,
)
from manager.discoveryagent_cli.errors import BootstrapError, RuntimeAcquisitionError
from manager.discoveryagent_cli.paths import InstallationPaths
from manager.discoveryagent_cli.runtime import (
    PYTHON_VERSION,
    UV_APPLE_SILICON_SHA256,
    RuntimePaths,
    UvArtifact,
    acquire_python,
    acquire_uv,
    platform_identity,
    uv_artifact_for_platform,
)
from manager.discoveryagent_cli.state import load_state
from manager.discoveryagent_cli.testing import fixture_release_metadata
from manager.discoveryagent_cli.testing import create_fake_installation


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
        frontend_lock = self.repository / "application" / "frontend" / "uv.lock"
        backend_lock = self.repository / "application" / "backend" / "uv.lock"
        paperclip_wheel = (
            self.repository
            / "application"
            / "backend"
            / "vendor"
            / "gxl_paperclip-0.7.24-py3-none-any.whl"
        )
        frontend_lock.write_text("frontend lock\n", encoding="utf-8")
        backend_lock.write_text("backend lock\n", encoding="utf-8")
        paperclip_wheel.parent.mkdir()
        paperclip_wheel.write_bytes(b"paperclip wheel fixture\n")
        manager = self.repository / "manager" / "discoveryagent_cli"
        manager.mkdir(parents=True)
        (manager / "__init__.py").write_text(
            'MANAGER_VERSION = "0.1.0"\n', encoding="utf-8"
        )
        metadata = fixture_release_metadata()
        metadata["frontend"]["uv_lock_sha256"] = hashlib.sha256(
            frontend_lock.read_bytes()
        ).hexdigest()
        metadata["backend"]["uv_lock_sha256"] = hashlib.sha256(
            backend_lock.read_bytes()
        ).hexdigest()
        metadata["paperclip"] = {
            "vendored_wheel": "vendor/gxl_paperclip-0.7.24-py3-none-any.whl",
            "sha256": hashlib.sha256(paperclip_wheel.read_bytes()).hexdigest(),
        }
        (self.repository / "release.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
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

    def test_invalid_owned_uv_runtime_is_repaired_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            runtime_root = root / "runtime"
            cache_root = root / "cache"
            old_uv = runtime_root / "uv" / "uv"
            old_uv.parent.mkdir(parents=True)
            old_uv.write_bytes(b"old uv")
            archive = cache_root / "fixture.tar.gz"
            cache_root.mkdir()
            with tarfile.open(archive, "w:gz") as bundle:
                payload = b"new uv"
                member = tarfile.TarInfo("uv-aarch64/uv")
                member.size = len(payload)
                member.mode = 0o755
                bundle.addfile(member, io.BytesIO(payload))
            artifact = UvArtifact(
                version="0.11.17",
                url="https://invalid.example/unused",
                sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                archive_name=archive.name,
            )

            def runner(command, **_kwargs):
                output = (
                    "uv 0.11.17\n"
                    if Path(command[0]).read_bytes() == b"new uv"
                    else "uv 0.1.0\n"
                )
                return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

            repaired = acquire_uv(
                runtime_root,
                cache_root,
                artifact=artifact,
                downloader=lambda *_args: self.fail("cached artifact must be reused"),
                runner=runner,
            )
            self.assertEqual(repaired.read_bytes(), b"new uv")
            self.assertFalse(list(runtime_root.glob(".uv.invalid-*")))

    def test_valid_owned_uv_runtime_is_reused_without_download(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            uv = root / "runtime" / "uv" / "uv"
            uv.parent.mkdir(parents=True)
            uv.write_text("existing uv\n", encoding="utf-8")

            def runner(command, **_kwargs):
                return subprocess.CompletedProcess(
                    command, 0, stdout="uv 0.11.17\n", stderr=""
                )

            selected = acquire_uv(
                root / "runtime",
                root / "cache",
                downloader=lambda *_args: self.fail("valid uv must not be downloaded"),
                runner=runner,
            )
            self.assertEqual(selected, uv.resolve())

    def test_failed_uv_repair_restores_previous_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            runtime_root = root / "runtime"
            old_uv = runtime_root / "uv" / "uv"
            old_uv.parent.mkdir(parents=True)
            old_uv.write_bytes(b"old uv")
            artifact = UvArtifact(
                version="0.11.17",
                url="https://invalid.example/failure",
                sha256="0" * 64,
                archive_name="fixture.tar.gz",
            )

            def runner(command, **_kwargs):
                return subprocess.CompletedProcess(
                    command, 0, stdout="uv 0.1.0\n", stderr=""
                )

            def fail_download(_url: str, _destination: Path) -> None:
                raise RuntimeAcquisitionError("injected download failure")

            with self.assertRaisesRegex(RuntimeAcquisitionError, "injected"):
                acquire_uv(
                    runtime_root,
                    root / "cache",
                    artifact=artifact,
                    downloader=fail_download,
                    runner=runner,
                )
            self.assertEqual(old_uv.read_bytes(), b"old uv")
            self.assertFalse(list(runtime_root.glob(".uv.invalid-*")))

    def test_wrong_owned_python_runtime_is_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            runtime_root = root / "runtime"
            python = runtime_root / "python" / "managed" / "bin" / "python3.11"
            python.parent.mkdir(parents=True)
            python.write_text("old python\n", encoding="utf-8")

            def runner(command, **_kwargs):
                if command[1:3] == ["python", "install"]:
                    python.parent.mkdir(parents=True)
                    python.write_text("new python\n", encoding="utf-8")
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                reported = (
                    "Python 3.11.11\n"
                    if Path(command[0]).read_text(encoding="utf-8") == "new python\n"
                    else "Python 3.10.0\n"
                )
                return subprocess.CompletedProcess(command, 0, stdout=reported, stderr="")

            repaired = acquire_python(
                root / "runtime/uv/uv",
                runtime_root,
                runner=runner,
            )
            self.assertEqual(repaired.read_text(encoding="utf-8"), "new python\n")
            self.assertFalse(list(runtime_root.glob(".python.invalid-*")))

    def test_valid_owned_python_runtime_is_reused_without_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            python = root / "runtime/python/managed/bin/python3.11"
            python.parent.mkdir(parents=True)
            python.write_text("existing python\n", encoding="utf-8")

            def runner(command, **_kwargs):
                self.assertEqual(command[1], "--version")
                return subprocess.CompletedProcess(
                    command, 0, stdout="Python 3.11.11\n", stderr=""
                )

            selected = acquire_python(
                root / "runtime/uv/uv",
                root / "runtime",
                runner=runner,
            )
            self.assertEqual(selected, python.resolve())

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


class DevelopmentManagerRefreshTests(unittest.TestCase):
    def test_refresh_changes_only_installed_manager(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            repository = root / "repository"
            source_manager = repository / "manager" / "discoveryagent_cli"
            source_manager.mkdir(parents=True)
            (source_manager / "__init__.py").write_text("refreshed = True\n", encoding="utf-8")
            paths, _state = create_fake_installation(root / "install")
            (paths.manager_root / "old.py").write_text("old\n", encoding="utf-8")
            release_root = paths.release_root(fixture_release_metadata()["release_id"])

            def manifest(path: Path) -> dict[str, bytes | str]:
                result = {}
                for item in sorted(path.rglob("*")):
                    relative = item.relative_to(path).as_posix()
                    if item.is_symlink():
                        result[relative] = os.readlink(item)
                    elif item.is_file():
                        result[relative] = item.read_bytes()
                return result

            before = manifest(release_root)
            refreshed = refresh_development_manager(repository, paths.root)
            after = manifest(release_root)

            self.assertEqual(before, after)
            self.assertEqual(refreshed, paths.manager_root)
            self.assertTrue((paths.manager_root / "discoveryagent_cli/__init__.py").is_file())
            self.assertFalse((paths.manager_root / "old.py").exists())
            self.assertFalse(list(paths.root.glob(".manager-*-*")))


if __name__ == "__main__":
    unittest.main()
