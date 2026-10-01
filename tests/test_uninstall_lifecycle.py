from __future__ import annotations

import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from manager.discoveryagent_cli.bootstrap import install_cli_shim
from manager.discoveryagent_cli.errors import ActiveResearchError, UninstallError
from manager.discoveryagent_cli.path_integration import (
    PATH_STANZA,
    PreparedPathRemoval,
    remove_managed_cli_path,
)
from manager.discoveryagent_cli.state import CliState, write_state_atomic
from manager.discoveryagent_cli.testing import create_fake_installation
from manager.discoveryagent_cli.uninstall import (
    MacOSUninstallHandoff,
    UNINSTALL_HELPER,
    UninstallDependencies,
    uninstall_installed_application,
    validate_uninstall_targets,
)


class PathRemovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.profile = self.root / ".zprofile"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exact_recorded_stanza_removed_and_unrelated_content_mode_preserved(self) -> None:
        original = b"export EDITOR=vim\n"
        self.profile.write_bytes(original + PATH_STANZA)
        self.profile.chmod(0o640)
        result = remove_managed_cli_path(
            CliState("/tmp/discoveryagent", True, str(self.profile)), uid=os.getuid()
        )
        self.assertTrue(result.removed)
        self.assertEqual(self.profile.read_bytes(), original)
        self.assertEqual(stat.S_IMODE(self.profile.stat().st_mode), 0o640)

    def test_preexisting_path_state_and_corrupt_markers_are_preserved(self) -> None:
        original = b'export PATH="$HOME/.local/bin:$PATH"\n'
        self.profile.write_bytes(original)
        result = remove_managed_cli_path(CliState("/tmp/discoveryagent", False, None))
        self.assertFalse(result.removed)
        self.assertEqual(self.profile.read_bytes(), original)

        corrupt = b"before\n# >>> DiscoveryAgent managed PATH >>>\nbroken\n"
        self.profile.write_bytes(corrupt)
        result = remove_managed_cli_path(
            CliState("/tmp/discoveryagent", True, str(self.profile)), uid=os.getuid()
        )
        self.assertFalse(result.removed)
        self.assertIsNotNone(result.warning)
        self.assertEqual(self.profile.read_bytes(), corrupt)


class UninstallFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.paths, self.state = create_fake_installation(self.root / "install")
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_active_round_refuses_before_shutdown_path_or_handoff(self) -> None:
        forbidden = lambda *_args, **_kwargs: self.fail("uninstall continued after refusal")
        dependencies = UninstallDependencies(
            research_guard=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                ActiveResearchError(
                    "DiscoveryAgent has an active research round.\n"
                    "Stop the round or wait for it to finish before uninstalling."
                )
            ),
            frontend_stopper=forbidden,
            target_validator=forbidden,
            path_preparer=forbidden,
            handoff=forbidden,
        )
        with self.assertRaisesRegex(ActiveResearchError, "before uninstalling"):
            uninstall_installed_application(
                self.paths,
                stdout=self.stdout,
                stderr=self.stderr,
                dependencies=dependencies,
            )

    def test_idle_frontend_stops_before_path_and_handoff(self) -> None:
        events = []
        dependencies = UninstallDependencies(
            research_guard=lambda *_args, **_kwargs: events.append("research"),
            target_validator=lambda *_args: (self.paths.root, Path(self.state.cli.path)),
            frontend_stopper=lambda *_args: events.append("frontend") or True,
            path_preparer=lambda _state: events.append("path") or PreparedPathRemoval(),
            handoff=lambda *_args: events.append("handoff"),
        )
        self.assertEqual(
            uninstall_installed_application(
                self.paths,
                stdout=self.stdout,
                stderr=self.stderr,
                dependencies=dependencies,
            ),
            0,
        )
        self.assertEqual(events, ["research", "frontend", "research", "path", "handoff"])
        self.assertIn("Uninstalling DiscoveryAgent", self.stdout.getvalue())

    def test_target_validation_accepts_owned_cli_and_rejects_suspicious_root(self) -> None:
        cli = self.root / "commands" / "discoveryagent"
        install_cli_shim(
            cli,
            install_root=self.paths.root,
            python=Path(self.state.runtime.python_path),
        )
        state = replace(self.state, cli=CliState(str(cli), False, None),
                        data_root=str(self.root / "durable-data"))
        write_state_atomic(self.paths.state_file, state)
        self.assertEqual(validate_uninstall_targets(self.paths, state), (self.paths.root, cli))

        suspicious_paths, suspicious_state = create_fake_installation(self.root / "data-root")
        suspicious_state = replace(suspicious_state, data_root=str(suspicious_paths.root))
        write_state_atomic(suspicious_paths.state_file, suspicious_state)
        with self.assertRaisesRegex(UninstallError, "suspicious"):
            validate_uninstall_targets(suspicious_paths, suspicious_state)

    def _owned_state_with_alias(self):
        cli = self.root / "commands" / "discoveryagent"
        install_cli_shim(cli, install_root=self.paths.root,
                         python=Path(self.state.runtime.python_path))
        alias = self.root / "directory-alias"
        alias.symlink_to(self.paths.root, target_is_directory=True)
        state = replace(self.state, cli=CliState(str(cli), False, None),
                        data_root=str(self.root / "durable-data"))
        return state, alias

    def test_data_alias_equal_to_or_inside_install_is_refused(self):
        state, alias = self._owned_state_with_alias()
        for data in (alias, alias / "projects"):
            with self.subTest(data=data):
                with self.assertRaisesRegex(UninstallError, "suspicious"):
                    validate_uninstall_targets(self.paths, replace(state, data_root=str(data)))

    def test_install_inside_aliased_data_root_is_refused(self):
        state, _ = self._owned_state_with_alias()
        alias = self.root / "data-parent-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(UninstallError, "suspicious"):
            validate_uninstall_targets(self.paths, replace(state, data_root=str(alias)))

    def test_home_alias_inside_install_is_refused(self):
        state, alias = self._owned_state_with_alias()
        for home in (alias, alias / "home"):
            with self.subTest(home=home), mock.patch.object(Path, "home", return_value=home):
                with self.assertRaisesRegex(UninstallError, "suspicious"):
                    validate_uninstall_targets(self.paths, state)

    def test_unresolvable_data_alias_fails_closed(self):
        state, _ = self._owned_state_with_alias()
        cycle = self.root / "cycle"
        cycle.symlink_to(cycle)
        with self.assertRaisesRegex(UninstallError, "resolve uninstall"):
            validate_uninstall_targets(self.paths, replace(state, data_root=str(cycle)))

    def test_separate_aliased_data_root_remains_allowed(self):
        state, _ = self._owned_state_with_alias()
        data = Path(state.data_root)
        data.mkdir()
        alias = self.root / "external-data-alias"
        alias.symlink_to(data, target_is_directory=True)
        self.assertEqual(validate_uninstall_targets(self.paths, replace(state, data_root=str(alias))),
                         (self.paths.root, Path(state.cli.path)))

    def test_cli_uninstall_preserves_data_reached_through_install_alias(self):
        state, alias = self._owned_state_with_alias()
        data = alias / "projects"
        data.mkdir()
        sentinel = data / "checkpoint.json"
        sentinel.write_text('{"resumable":true}', encoding="utf-8")
        state = replace(state, data_root=str(data))
        write_state_atomic(self.paths.state_file, state)
        home = self.root / "isolated-home"
        home.mkdir()
        manager = Path(__file__).resolve().parents[1] / "manager"
        result = subprocess.run([sys.executable, "-B", "-m", "discoveryagent_cli", "uninstall"],
            cwd=self.root, text=True, capture_output=True, timeout=15,
            env={"HOME": str(home), "PATH": os.defpath, "TMPDIR": str(self.root),
                 "PYTHONPATH": str(manager), "PYTHONDONTWRITEBYTECODE": "1",
                 "PYTHONNOUSERSITE": "1", "DISCOVERYAGENT_INTERNAL_INSTALL_ROOT": str(self.paths.root)})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Refusing to remove suspicious install root", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertTrue(self.paths.root.exists())
        self.assertTrue(Path(state.cli.path).exists())
        self.assertEqual(sentinel.read_text(), '{"resumable":true}')


class SelfRemovalHelperTests(unittest.TestCase):
    def test_helper_is_external_uses_positional_quoted_paths_and_contains_no_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            install = root / "install root 'quoted'"
            cli = root / "bin dir" / "discoveryagent"
            install.mkdir()
            cli.parent.mkdir()
            cli.write_text("fixture", encoding="utf-8")
            captured = {}

            def fake_exec(program, arguments):
                captured["program"] = program
                captured["arguments"] = arguments
                helper = Path(arguments[1])
                captured["contents"] = helper.read_text(encoding="utf-8")
                self.assertFalse(helper.is_relative_to(install))

            handoff = MacOSUninstallHandoff(execv=fake_exec, temporary_parent=root)
            with self.assertRaisesRegex(UninstallError, "returned unexpectedly"):
                handoff(install, cli, PreparedPathRemoval())
            self.assertEqual(captured["program"], "/bin/sh")
            self.assertEqual(captured["arguments"][2:4], [str(cli), str(install)])
            self.assertIn('"$cli"', captured["contents"])
            self.assertIn('"$install_root"', captured["contents"])
            for secret_name in ("ANTHROPIC_API_KEY", "PAPERCLIP_API_KEY", "token"):
                self.assertNotIn(secret_name, captured["contents"])

    def test_helper_contract_preserves_data_paperclip_and_unrelated_cli(self) -> None:
        self.assertIn('/bin/rm -f -- "$cli"', UNINSTALL_HELPER)
        self.assertIn('/bin/rm -rf -- "$install_root"', UNINSTALL_HELPER)
        self.assertNotIn("Application Support", UNINSTALL_HELPER)
        self.assertNotIn(".paperclip", UNINSTALL_HELPER)
        self.assertNotIn(".local/bin\"", UNINSTALL_HELPER)


if __name__ == "__main__":
    unittest.main()
