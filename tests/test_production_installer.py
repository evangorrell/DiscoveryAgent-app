from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from manager.discoveryagent_cli.bootstrap import EnvironmentValidation, install_cli_shim
from manager.discoveryagent_cli.errors import BootstrapError
from manager.discoveryagent_cli.installer import (
    InstallationLock,
    ProductionInstallConfig,
    install_production_release,
    production_default_paths,
)
from manager.discoveryagent_cli.path_integration import configure_cli_path
from manager.discoveryagent_cli.runtime import RuntimePaths
from manager.discoveryagent_cli.state import load_state, write_state_atomic
from manager.discoveryagent_cli.testing import (
    DEFAULT_BACKEND_COMMIT,
    DEFAULT_FRONTEND_COMMIT,
)


SECOND_FRONTEND_COMMIT = "a" * 40


class ProductionInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.payload = self.root / "packaged-payload"
        self.install = self.root / "installed"
        self.cli = self.root / "commands" / "discoveryagent"
        self.data = self.root / "durable data"
        self.sync_calls: list[tuple[Path, Path]] = []
        self._make_payload(self.payload)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _make_payload(
        self,
        root: Path,
        *,
        frontend_commit: str = DEFAULT_FRONTEND_COMMIT,
        marker: str = "first",
    ) -> None:
        frontend = root / "application" / "frontend"
        backend = root / "application" / "backend"
        frontend.mkdir(parents=True)
        (backend / "vendor").mkdir(parents=True)
        (frontend / "uv.lock").write_text(
            f"frontend lock {marker}\n", encoding="utf-8"
        )
        (backend / "uv.lock").write_text(
            f"backend lock {marker}\n", encoding="utf-8"
        )
        wheel = backend / "vendor" / "gxl_paperclip-0.7.24-py3-none-any.whl"
        wheel.write_bytes(b"paperclip wheel fixture\n")
        (frontend / "snapshot.txt").write_text(marker + "\n", encoding="utf-8")
        (backend / "snapshot.txt").write_text(marker + "\n", encoding="utf-8")
        source_manager = Path(__file__).resolve().parents[1] / "manager" / "discoveryagent_cli"
        shutil.copytree(
            source_manager,
            root / "manager" / "discoveryagent_cli",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        prefix = "dev"
        metadata = {
            "schema_version": 1,
            "release_id": (
                f"{prefix}-{frontend_commit[:12]}-{DEFAULT_BACKEND_COMMIT[:12]}"
            ),
            "release_type": "development",
            "required_cpython_version": "3.11.11",
            "application_schema_version": 1,
            "frontend": {
                "commit": frontend_commit,
                "uv_lock_sha256": self._sha256(frontend / "uv.lock"),
            },
            "backend": {
                "commit": DEFAULT_BACKEND_COMMIT,
                "uv_lock_sha256": self._sha256(backend / "uv.lock"),
            },
            "paperclip": {
                "vendored_wheel": wheel.relative_to(backend).as_posix(),
                "sha256": self._sha256(wheel),
            },
        }
        (root / "release.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def acquire(self, runtime_root: Path, _cache_root: Path) -> RuntimePaths:
        uv = runtime_root / "uv" / "uv"
        uv.parent.mkdir(parents=True, exist_ok=True)
        uv.write_text("fixture uv\n", encoding="utf-8")
        return RuntimePaths(uv=uv.resolve(), python=Path(sys.executable).resolve())

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
        python.parent.mkdir(parents=True)
        python.symlink_to(Path(sys.executable).resolve())
        return python

    @staticmethod
    def validate(release_root: Path, _cache_root: Path) -> EnvironmentValidation:
        return EnvironmentValidation(
            frontend_python=release_root / "envs/frontend/bin/python",
            backend_python=release_root / "envs/backend/bin/python",
            frontend_module=release_root / "envs/frontend/site-packages/da_selection_ui",
            backend_module=release_root / "envs/backend/site-packages/AIR",
            paperclip_version="0.7.24",
            claude_executable=release_root / "envs/backend/site-packages/claude",
        )

    def config(self, payload: Path | None = None) -> ProductionInstallConfig:
        return ProductionInstallConfig(
            payload_root=payload or self.payload,
            install_root=self.install,
            cli_path=self.cli,
            data_root=self.data,
        )

    def install_release(self, payload: Path | None = None, **overrides):
        parameters = {
            "runtime_acquirer": self.acquire,
            "environment_syncer": self.sync,
            "release_validator": self.validate,
            "frontend_checker": lambda _paths, _state: False,
        }
        parameters.update(overrides)
        return install_production_release(self.config(payload), **parameters)

    def test_production_defaults_and_absolute_override_contract(self) -> None:
        home = (self.root / "home").resolve()
        defaults = production_default_paths(home)
        self.assertEqual(
            defaults.install_root, home / ".local/share/discoveryagent"
        )
        self.assertEqual(defaults.cli_path, home / ".local/bin/discoveryagent")
        # Reachable from Finder and from a shell, unlike Application Support.
        self.assertEqual(defaults.data_root, home / "DiscoveryAgent")
        with self.assertRaisesRegex(BootstrapError, "--install-root"):
            ProductionInstallConfig(
                self.payload,
                install_root=Path("relative"),
                cli_path=self.cli,
                data_root=self.data,
            ).normalized()
        normalized = self.config().normalized()
        self.assertEqual(normalized.payload_root, self.payload.resolve())
        self.assertEqual(normalized.install_root, self.install.resolve())
        self.assertEqual(normalized.cli_path, self.cli.resolve())
        self.assertEqual(normalized.data_root, self.data.resolve())
        with self.assertRaisesRegex(BootstrapError, "non-overlapping"):
            ProductionInstallConfig(
                self.payload,
                install_root=self.install,
                cli_path=self.cli,
                data_root=self.install / "user-data",
            ).normalized()

    def test_first_install_is_complete_and_external_cli_is_repository_independent(self) -> None:
        paperclip = self.root / "home" / ".paperclip"
        paperclip.mkdir(parents=True)
        paperclip_sentinel = paperclip / "credentials.json"
        paperclip_sentinel.write_text("untouched\n", encoding="utf-8")
        before = {
            path.relative_to(self.payload).as_posix(): path.read_bytes()
            for path in self.payload.rglob("*")
            if path.is_file()
        }

        result = self.install_release()

        release = result.paths.release_root(result.release.release_id)
        self.assertTrue((release / "complete").is_file())
        self.assertTrue((release / "envs/frontend/bin/python").exists())
        self.assertTrue((release / "envs/backend/bin/python").exists())
        self.assertTrue(self.cli.is_file())
        self.assertTrue(os.access(self.cli, os.X_OK))
        self.assertFalse(list(self.cli.parent.glob(".discoveryagent.*.tmp")))
        self.assertFalse((self.payload / ".git").exists())
        self.assertFalse(self.data.exists())
        self.assertEqual(paperclip_sentinel.read_text(encoding="utf-8"), "untouched\n")
        after = {
            path.relative_to(self.payload).as_posix(): path.read_bytes()
            for path in self.payload.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)
        wrapper = self.cli.read_text(encoding="utf-8")
        self.assertNotIn(str(self.payload), wrapper)
        unrelated = self.root / "unrelated cwd"
        unrelated.mkdir()
        completed = subprocess.run(
            [self.cli, "version"],
            cwd=unrelated,
            env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn(result.release.release_id, completed.stdout)
        state_text = result.paths.state_file.read_text(encoding="utf-8")
        self.assertNotIn(str(self.payload), state_text)
        self.assertNotIn("ANTHROPIC_API_KEY", state_text)
        self.assertNotIn("PAPERCLIP", state_text)

    def test_preexisting_durable_data_is_not_changed(self) -> None:
        self.data.mkdir(parents=True)
        sentinel = self.data / "existing-project.json"
        sentinel.write_text("user data\n", encoding="utf-8")
        before = sentinel.stat().st_mtime_ns
        self.install_release()
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "user data\n")
        self.assertEqual(sentinel.stat().st_mtime_ns, before)

    def test_identical_release_reuses_completed_release_without_sync(self) -> None:
        first = self.install_release()
        state_before = first.paths.state_file.read_bytes()
        self.sync_calls.clear()
        second = self.install_release()
        self.assertTrue(second.reused_release)
        self.assertEqual(self.sync_calls, [])
        self.assertEqual(state_before, second.paths.state_file.read_bytes())

    def test_different_release_is_side_by_side_and_becomes_active(self) -> None:
        first = self.install_release()
        second_payload = self.root / "second-payload"
        self._make_payload(
            second_payload,
            frontend_commit=SECOND_FRONTEND_COMMIT,
            marker="second",
        )
        second = self.install_release(second_payload)
        state = load_state(second.paths.state_file)
        self.assertNotEqual(first.release.release_id, second.release.release_id)
        self.assertEqual(state.active_release_id, second.release.release_id)
        self.assertEqual(
            set(state.installed_releases),
            {first.release.release_id, second.release.release_id},
        )
        self.assertTrue(first.paths.release_root(first.release.release_id).is_dir())
        self.assertTrue(second.paths.release_root(second.release.release_id).is_dir())

    def test_failed_new_release_leaves_old_release_active(self) -> None:
        first = self.install_release()
        state_before = first.paths.state_file.read_bytes()
        second_payload = self.root / "second-payload"
        self._make_payload(
            second_payload,
            frontend_commit=SECOND_FRONTEND_COMMIT,
            marker="second",
        )

        def fail_sync(*_arguments):
            raise BootstrapError("injected environment failure")

        with self.assertRaisesRegex(BootstrapError, "injected environment failure"):
            self.install_release(second_payload, environment_syncer=fail_sync)
        self.assertEqual(state_before, first.paths.state_file.read_bytes())
        self.assertEqual(
            load_state(first.paths.state_file).active_release_id,
            first.release.release_id,
        )
        self.assertFalse(
            first.paths.release_root(
                f"dev-{SECOND_FRONTEND_COMMIT[:12]}-{DEFAULT_BACKEND_COMMIT[:12]}"
            ).exists()
        )
        self.assertEqual(list((self.install / "staging").iterdir()), [])

    def test_payload_tamper_is_rejected_before_install_root_creation(self) -> None:
        (self.payload / "application/frontend/uv.lock").write_text(
            "tampered\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(BootstrapError, "SHA-256 mismatch"):
            self.install_release()
        self.assertFalse(self.install.exists())
        self.assertFalse(self.cli.exists())

    def test_healthy_frontend_refuses_before_runtime_or_manager_changes(self) -> None:
        first = self.install_release()
        manager_before = {
            path.relative_to(first.paths.manager_root): path.read_bytes()
            for path in first.paths.manager_root.rglob("*")
            if path.is_file()
        }

        def must_not_acquire(*_arguments):
            self.fail("runtime acquisition must not run")

        with self.assertRaisesRegex(BootstrapError, "currently running"):
            self.install_release(
                runtime_acquirer=must_not_acquire,
                frontend_checker=lambda _paths, _state: True,
            )
        manager_after = {
            path.relative_to(first.paths.manager_root): path.read_bytes()
            for path in first.paths.manager_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(manager_before, manager_after)

    def test_state_is_written_only_after_manager_and_cli(self) -> None:
        events: list[str] = []

        def manager_installer(_payload: Path, paths) -> Path:
            events.append("manager")
            paths.manager_root.mkdir(parents=True)
            return paths.manager_root

        def cli_installer(path: Path, **_arguments) -> Path:
            events.append("cli")
            path.parent.mkdir(parents=True)
            path.write_text("fixture\n", encoding="utf-8")
            return path

        def state_writer(path, state) -> None:
            events.append("state")
            write_state_atomic(path, state)

        self.install_release(
            manager_installer=manager_installer,
            cli_installer=cli_installer,
            state_writer=state_writer,
        )
        self.assertEqual(events, ["manager", "cli", "state"])

    def test_cli_failure_does_not_publish_state(self) -> None:
        def fail_cli(*_arguments, **_keywords):
            release_id = (
                f"dev-{DEFAULT_FRONTEND_COMMIT[:12]}-{DEFAULT_BACKEND_COMMIT[:12]}"
            )
            self.assertTrue(
                (self.install / "releases" / release_id / "complete").is_file()
            )
            raise BootstrapError("injected CLI failure")

        with self.assertRaisesRegex(BootstrapError, "injected CLI failure"):
            self.install_release(cli_installer=fail_cli)
        self.assertFalse((self.install / "state.json").exists())

    def test_existing_manager_is_replaced_without_stale_files(self) -> None:
        first = self.install_release()
        stale = first.paths.manager_root / "discoveryagent_cli" / "stale.py"
        stale.write_text("stale\n", encoding="utf-8")
        self.install_release()
        self.assertFalse(stale.exists())
        self.assertFalse(list(self.install.glob(".manager-*-*")))

    def test_canonical_path_change_is_recorded_in_state(self) -> None:
        home = self.root / "isolated-home"
        home.mkdir()
        cli = home / ".local/bin/discoveryagent"
        config = ProductionInstallConfig(
            payload_root=self.payload,
            install_root=self.install,
            cli_path=cli,
            data_root=self.data,
        )

        def no_path_runner(command, **_kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="__DISCOVERYAGENT_LOGIN_PATH__=/usr/bin:/bin\n",
                stderr="",
            )

        result = install_production_release(
            config,
            runtime_acquirer=self.acquire,
            environment_syncer=self.sync,
            release_validator=self.validate,
            frontend_checker=lambda _paths, _state: False,
            path_integrator=lambda path: configure_cli_path(
                path,
                home=home,
                shell=Path("/bin/zsh"),
                runner=no_path_runner,
            ),
        )
        profile = home.resolve() / ".zprofile"
        self.assertTrue(result.state.cli.path_change_added)
        self.assertEqual(result.state.cli.modified_profile, str(profile))
        self.assertEqual(load_state(result.paths.state_file).cli, result.state.cli)

    def test_state_failure_rolls_back_new_path_profile(self) -> None:
        home = self.root / "isolated-home"
        home.mkdir()
        cli = home / ".local/bin/discoveryagent"
        config = ProductionInstallConfig(
            payload_root=self.payload,
            install_root=self.install,
            cli_path=cli,
            data_root=self.data,
        )

        def no_path_runner(command, **_kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="__DISCOVERYAGENT_LOGIN_PATH__=/usr/bin:/bin\n",
                stderr="",
            )

        with self.assertRaisesRegex(BootstrapError, "state failure"):
            install_production_release(
                config,
                runtime_acquirer=self.acquire,
                environment_syncer=self.sync,
                release_validator=self.validate,
                frontend_checker=lambda _paths, _state: False,
                path_integrator=lambda path: configure_cli_path(
                    path,
                    home=home,
                    shell=Path("/bin/zsh"),
                    runner=no_path_runner,
                ),
                state_writer=lambda *_args: (_ for _ in ()).throw(
                    BootstrapError("state failure")
                ),
            )
        self.assertFalse((home / ".zprofile").exists())
        self.assertFalse((self.install / "state.json").exists())


class CliShimSafetyTests(unittest.TestCase):
    """--cli-path is user-supplied, so publishing the shim must never clobber."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.install_root = self.root / "installed"
        self.python = self.root / "python"
        self.python.write_text("#!/bin/sh\n", encoding="utf-8")

    def shim(self, cli_path: Path) -> Path:
        return install_cli_shim(
            cli_path,
            install_root=self.install_root,
            python=self.python,
        )

    def test_unrelated_existing_command_is_preserved(self) -> None:
        cli = self.root / "bin" / "discoveryagent"
        cli.parent.mkdir()
        cli.write_text("#!/bin/sh\necho someone else's tool\n", encoding="utf-8")

        with self.assertRaisesRegex(BootstrapError, "did not create"):
            self.shim(cli)

        self.assertEqual(
            cli.read_text(encoding="utf-8"), "#!/bin/sh\necho someone else's tool\n"
        )

    def test_symlink_at_the_cli_path_is_refused_without_following_it(self) -> None:
        target = self.root / "important"
        target.write_text("irreplaceable", encoding="utf-8")
        cli = self.root / "link"
        cli.symlink_to(target)

        with self.assertRaisesRegex(BootstrapError, "non-regular file"):
            self.shim(cli)

        self.assertEqual(target.read_text(encoding="utf-8"), "irreplaceable")
        self.assertTrue(cli.is_symlink())

    def test_reinstall_replaces_a_shim_discoveryagent_wrote(self) -> None:
        cli = self.root / "bin" / "discoveryagent"

        first = self.shim(cli)
        second = self.shim(cli)

        self.assertEqual(first, second)
        self.assertIn("discoveryagent_cli", cli.read_text(encoding="utf-8"))


class InstallationLockTests(unittest.TestCase):
    def test_concurrent_lock_is_refused_and_owner_can_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            install = Path(temporary_name) / "installed"
            first = InstallationLock(install, pid_alive=lambda _pid: True)
            first.acquire()
            try:
                with self.assertRaisesRegex(BootstrapError, "already using"):
                    InstallationLock(install, pid_alive=lambda _pid: True).acquire()
            finally:
                first.release()
            self.assertFalse(first.path.exists())

    def test_dead_owner_lock_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            install = Path(temporary_name) / "installed"
            lock_path = install.parent / f".{install.name}.install.lock"
            lock_path.write_text(
                json.dumps({"pid": 999999, "created_at": 1, "token": "old"}),
                encoding="utf-8",
            )
            lock = InstallationLock(install, pid_alive=lambda _pid: False)
            lock.acquire()
            self.assertTrue(lock.path.exists())
            lock.release()
            self.assertFalse(lock.path.exists())


if __name__ == "__main__":
    unittest.main()
