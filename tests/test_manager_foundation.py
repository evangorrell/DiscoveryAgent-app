from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from manager.discoveryagent_cli import MANAGER_VERSION
from manager.discoveryagent_cli.cli import main, version_command
from manager.discoveryagent_cli.errors import LaunchValidationError, ReleaseError, StateError
from manager.discoveryagent_cli.installation import (
    load_active_release,
    validate_launch_installation,
)
from manager.discoveryagent_cli.paths import (
    INTERNAL_INSTALL_ROOT_ENV,
    InstallationPaths,
    environment_python,
    release_environment_python,
    resolve_installation_root,
)
from manager.discoveryagent_cli.state import ManagerState, load_state, write_state_atomic
from manager.discoveryagent_cli.testing import (
    DEFAULT_BACKEND_COMMIT,
    DEFAULT_FRONTEND_COMMIT,
    DEFAULT_RELEASE_ID,
    create_fake_installation,
)


class ManagerFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def fixture(self, *, platform_name: str = "darwin"):
        return create_fake_installation(
            self.root / f"install-{platform_name}",
            platform_name=platform_name,
        )

    @staticmethod
    def read_json(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def write_json(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def test_valid_state_load(self) -> None:
        paths, expected = self.fixture()
        actual = load_state(paths.state_file)
        self.assertEqual(actual, expected)
        self.assertEqual(actual.schema_version, 1)
        self.assertEqual(actual.active_release_id, DEFAULT_RELEASE_ID)

    def test_malformed_json(self) -> None:
        state_path = self.root / "state.json"
        state_path.write_text("{not-json", encoding="utf-8")
        with self.assertRaisesRegex(StateError, "not valid JSON"):
            load_state(state_path)

    def test_unsupported_state_schema(self) -> None:
        paths, _state = self.fixture()
        payload = self.read_json(paths.state_file)
        payload["schema_version"] = 99
        self.write_json(paths.state_file, payload)
        with self.assertRaisesRegex(StateError, "Unsupported state schema"):
            load_state(paths.state_file)

    def test_missing_active_release(self) -> None:
        paths, _state = self.fixture()
        payload = self.read_json(paths.state_file)
        payload["active_release_id"] = ""
        self.write_json(paths.state_file, payload)
        with self.assertRaisesRegex(StateError, "active_release_id"):
            load_state(paths.state_file)

    def test_release_not_listed_as_installed(self) -> None:
        paths, _state = self.fixture()
        payload = self.read_json(paths.state_file)
        payload["installed_releases"] = {}
        self.write_json(paths.state_file, payload)
        state = load_state(paths.state_file)
        with self.assertRaisesRegex(StateError, "not listed"):
            load_active_release(paths, state)

    def test_missing_release_json(self) -> None:
        paths, state = self.fixture()
        paths.release_root(state.active_release_id).joinpath("release.json").unlink()
        with self.assertRaisesRegex(ReleaseError, "metadata is missing"):
            load_active_release(paths, state)

    def test_missing_complete_marker(self) -> None:
        paths, state = self.fixture()
        paths.release_root(state.active_release_id).joinpath("complete").unlink()
        with self.assertRaisesRegex(LaunchValidationError, "completion marker"):
            validate_launch_installation(paths, state, platform_name="darwin")

    def test_release_id_mismatch(self) -> None:
        paths, state = self.fixture()
        metadata_path = paths.release_root(state.active_release_id) / "release.json"
        payload = self.read_json(metadata_path)
        payload["release_id"] = "dev-different-release"
        self.write_json(metadata_path, payload)
        with self.assertRaisesRegex(ReleaseError, "ID mismatch"):
            load_active_release(paths, state)

    def test_missing_application_frontend_and_backend(self) -> None:
        for missing_name in ("frontend", "backend"):
            with self.subTest(missing=missing_name):
                case_root = self.root / f"missing-{missing_name}"
                paths, state = create_fake_installation(case_root)
                target = (
                    paths.release_root(state.active_release_id)
                    / "application"
                    / missing_name
                )
                for child in sorted(target.rglob("*"), reverse=True):
                    child.unlink() if child.is_file() else child.rmdir()
                target.rmdir()
                with self.assertRaisesRegex(
                    LaunchValidationError, f"{missing_name.capitalize()} application"
                ):
                    validate_launch_installation(paths, state, platform_name="darwin")

    def test_macos_environment_python_paths(self) -> None:
        environment = self.root / "envs" / "frontend"
        self.assertEqual(
            environment_python(environment, "darwin"),
            environment / "bin" / "python",
        )

    def test_future_windows_environment_python_paths(self) -> None:
        environment = self.root / "envs" / "backend"
        self.assertEqual(
            environment_python(environment, "win32"),
            environment / "Scripts" / "python.exe",
        )

    def test_missing_frontend_and_backend_environment_python(self) -> None:
        for environment_name in ("frontend", "backend"):
            with self.subTest(environment=environment_name):
                case_root = self.root / f"missing-python-{environment_name}"
                paths, state = create_fake_installation(case_root)
                release_root = paths.release_root(state.active_release_id)
                release_environment_python(
                    release_root, environment_name, "darwin"
                ).unlink()
                with self.assertRaisesRegex(
                    LaunchValidationError,
                    f"{environment_name.capitalize()} environment Python",
                ):
                    validate_launch_installation(paths, state, platform_name="darwin")

    def test_launch_rejects_runtime_python_version_mismatch(self) -> None:
        paths, state = self.fixture()
        mismatched = replace(
            state,
            runtime=replace(state.runtime, python_version="3.11.10"),
        )
        with self.assertRaisesRegex(
            LaunchValidationError, "Runtime Python version does not match"
        ):
            validate_launch_installation(paths, mismatched, platform_name="darwin")

    def test_launch_requires_production_entrypoints(self) -> None:
        cases = (
            ("da_selection_ui/__main__.py", "production module"),
            ("da_selection_ui/production_server.py", "production server"),
            ("../backend/scripts/run_hb_sherlock.py", "Backend round launcher"),
        )
        for index, (relative, expected) in enumerate(cases):
            with self.subTest(relative=relative):
                case_root = self.root / f"entrypoint-{index}"
                paths, state = create_fake_installation(case_root)
                release = paths.release_root(state.active_release_id)
                if relative.startswith("../backend/"):
                    target = release / "application" / relative[3:]
                else:
                    target = release / "application" / "frontend" / relative
                target.unlink()
                with self.assertRaisesRegex(LaunchValidationError, expected):
                    validate_launch_installation(paths, state, platform_name="darwin")

    def test_bare_and_explicit_launch_use_the_same_handler(self) -> None:
        calls = []

        def launch_handler(context):
            calls.append(context.paths.root)
            return 17

        handlers = {"launch": launch_handler, "version": lambda _context: 0}
        first = main([], install_root=self.root, handlers=handlers)
        second = main(["launch"], install_root=self.root, handlers=handlers)
        self.assertEqual((first, second), (17, 17))
        self.assertEqual(calls, [self.root.resolve(), self.root.resolve()])

    def test_version_output_does_not_validate_or_launch_runtime(self) -> None:
        paths, state = self.fixture()
        release_root = paths.release_root(state.active_release_id)
        release_environment_python(release_root, "frontend", "darwin").unlink()

        def forbidden_launch(_context):
            raise AssertionError("version must not call the launch handler")

        stdout = io.StringIO()
        stderr = io.StringIO()
        result = main(
            ["version"],
            install_root=paths.root,
            stdout=stdout,
            stderr=stderr,
            handlers={"launch": forbidden_launch, "version": version_command},
        )
        self.assertEqual(result, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(
            stdout.getvalue().splitlines(),
            [
                f"DiscoveryAgent {DEFAULT_RELEASE_ID}",
                f"Frontend: {DEFAULT_FRONTEND_COMMIT[:12]}",
                f"Backend:  {DEFAULT_BACKEND_COMMIT[:12]}",
                "Python:   3.11.11",
                f"Manager:  {MANAGER_VERSION}",
            ],
        )

    def test_noninteractive_launch_without_api_key_stops_cleanly(self) -> None:
        paths, _state = self.fixture()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True):
            result = main(
                ["launch"],
                install_root=paths.root,
                stdout=stdout,
                stderr=stderr,
            )
        self.assertEqual(result, 1)
        self.assertIn("Welcome to DiscoveryAgent", stdout.getvalue())
        self.assertIn("interactive terminal", stderr.getvalue())
        self.assertIn("Set ANTHROPIC_API_KEY", stderr.getvalue())

    def test_atomic_state_replacement(self) -> None:
        paths, state = self.fixture()
        replacement = replace(state, manager_version="0.1.1-test")
        write_state_atomic(paths.state_file, replacement)
        self.assertEqual(load_state(paths.state_file), replacement)
        self.assertFalse(list(paths.root.glob(".state.json.*.tmp")))

    def test_secret_fields_are_not_accepted_or_written(self) -> None:
        paths, state = self.fixture()
        payload = state.to_dict()
        payload["anthropic_api_key"] = "must-not-be-stored"
        with self.assertRaisesRegex(StateError, "unsupported fields"):
            ManagerState.from_dict(payload)
        serialized = paths.state_file.read_text(encoding="utf-8").lower()
        for forbidden in ("anthropic_api_key", "paperclip_api_key", "password", "token"):
            self.assertNotIn(forbidden, serialized)

    def test_install_root_explicit_and_internal_override(self) -> None:
        explicit = self.root / "explicit"
        internal = self.root / "internal"
        self.assertEqual(resolve_installation_root(explicit), explicit.resolve())
        self.assertEqual(
            resolve_installation_root(
                environ={INTERNAL_INSTALL_ROOT_ENV: str(internal)}
            ),
            internal.resolve(),
        )

    def test_invalid_data_root_is_rejected(self) -> None:
        paths, state = self.fixture()
        data_root = Path(state.data_root)
        data_root.write_text("not a directory", encoding="utf-8")
        with self.assertRaisesRegex(LaunchValidationError, "data root"):
            validate_launch_installation(paths, state, platform_name="darwin")


if __name__ == "__main__":
    unittest.main()
