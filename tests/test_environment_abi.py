from pathlib import Path
import subprocess
import tempfile
import unittest

from manager.discoveryagent_cli.bootstrap import environment_needs_rebuild, sync_environment
from manager.discoveryagent_cli.errors import BootstrapError
from manager.discoveryagent_cli.runtime import PYTHON_VERSION, MINIMUM_MACOS_MAJOR


class EnvironmentAbiTests(unittest.TestCase):
    def test_healthy_uv_and_stdlib_environments_are_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / 'envs/frontend'
            env.mkdir(parents=True)
            for key in ('version', 'version_info'):
                (env / 'pyvenv.cfg').write_text(f'{key} = {PYTHON_VERSION}\n')
                self.assertFalse(environment_needs_rebuild(env))

    def test_mismatched_extensions_rebuilt_without_touching_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = root / "releases/test/envs/backend"
            env.mkdir(parents=True)
            (env / "pyvenv.cfg").write_text(f"version = {PYTHON_VERSION}\n")
            (env / "bad.cpython-312-darwin.so").write_bytes(b"stale")
            data = root / "custom-scientific-data/project.json"
            data.parent.mkdir()
            data.write_bytes(b"important project")
            self.assertTrue(environment_needs_rebuild(env))

            def runner(command, **kwargs):
                if "sync" in command:
                    self.assertFalse(env.exists())
                    (env / "bin").mkdir(parents=True)
                    (env / "bin/python").write_text("python")
                    (env / "pyvenv.cfg").write_text(f"version = {PYTHON_VERSION}\n")
                return subprocess.CompletedProcess(command, 0, "", "")

            sync_environment(root / "uv", root / "python", root / "project", env, root / "cache", runner=runner)
            self.assertFalse(environment_needs_rebuild(env))
            self.assertEqual(data.read_bytes(), b"important project")
            self.assertFalse((env / "bad.cpython-312-darwin.so").exists())

    def test_failed_rebuild_restores_previous_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = root / "envs/frontend"
            env.mkdir(parents=True)
            (env / "pyvenv.cfg").write_text("version = 3.12.0\n")
            def runner(command, **kwargs):
                if "sync" in command:
                    raise subprocess.CalledProcessError(1, command)
                return subprocess.CompletedProcess(command, 0, "", "")
            with self.assertRaises(BootstrapError):
                sync_environment(root / "uv", root / "python", root / "project", env, root / "cache", runner=runner)
            self.assertEqual((env / "pyvenv.cfg").read_text(), "version = 3.12.0\n")

    def test_unowned_or_symlink_environment_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            with self.assertRaises(BootstrapError):
                environment_needs_rebuild(data)
            env = root / "envs/backend"
            env.parent.mkdir()
            env.symlink_to(data, target_is_directory=True)
            with self.assertRaises(BootstrapError):
                environment_needs_rebuild(env)

    def test_minimum_os_matches_shell_preflight(self):
        installer = Path(__file__).resolve().parents[1] / "install.sh"
        self.assertIn(f'MINIMUM_MACOS_MAJOR={MINIMUM_MACOS_MAJOR}', installer.read_text())
