from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from manager.discoveryagent_cli.errors import BootstrapError
from manager.discoveryagent_cli.path_integration import (
    PATH_MARKER_BEGIN,
    PATH_MARKER_END,
    PATH_STANZA,
    configure_cli_path,
    future_login_path_contains,
    select_login_profile,
)


class PathIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name).resolve()
        self.cli = self.home / ".local/bin/discoveryagent"
        self.cli.parent.mkdir(parents=True)
        self.cli.write_text("command\n", encoding="utf-8")
        self.zsh = Path("/bin/zsh")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def runner_with_path(path_value: str):
        def runner(command, **_kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"startup output\n__DISCOVERYAGENT_LOGIN_PATH__={path_value}\n",
                stderr="",
            )

        return runner

    def test_zsh_and_bash_profile_selection(self) -> None:
        self.assertEqual(
            select_login_profile(self.home, Path("/bin/zsh")),
            self.home / ".zprofile",
        )
        self.assertEqual(
            select_login_profile(self.home, Path("/bin/bash")),
            self.home / ".profile",
        )
        (self.home / ".bash_login").write_text("login\n", encoding="utf-8")
        self.assertEqual(
            select_login_profile(self.home, Path("/bin/bash")),
            self.home / ".bash_login",
        )
        (self.home / ".bash_profile").write_text("profile\n", encoding="utf-8")
        self.assertEqual(
            select_login_profile(self.home, Path("/bin/bash")),
            self.home / ".bash_profile",
        )
        with self.assertRaisesRegex(BootstrapError, "does not support"):
            select_login_profile(self.home, Path("/bin/fish"))

    def test_existing_future_path_requires_no_profile_edit(self) -> None:
        change = configure_cli_path(
            self.cli,
            home=self.home,
            shell=self.zsh,
            runner=self.runner_with_path(f"/usr/bin:{self.cli.parent}"),
        )
        self.assertFalse(change.cli_state.path_change_added)
        self.assertIsNone(change.cli_state.modified_profile)
        self.assertFalse((self.home / ".zprofile").exists())
        self.assertTrue(
            future_login_path_contains(
                self.cli.parent,
                shell=self.zsh,
                home=self.home,
                runner=self.runner_with_path(f"/usr/bin:{self.cli.parent}"),
            )
        )

    def test_stanza_is_single_idempotent_and_preserves_unrelated_bytes(self) -> None:
        profile = self.home / ".zprofile"
        original = b"export EXISTING=value"
        profile.write_bytes(original)
        first = configure_cli_path(
            self.cli,
            home=self.home,
            shell=self.zsh,
            runner=self.runner_with_path("/usr/bin:/bin"),
        )
        expected = original + b"\n" + PATH_STANZA
        self.assertEqual(profile.read_bytes(), expected)
        self.assertTrue(first.cli_state.path_change_added)
        self.assertEqual(first.cli_state.modified_profile, str(profile))

        second = configure_cli_path(
            self.cli,
            home=self.home,
            shell=self.zsh,
            runner=self.runner_with_path(str(self.cli.parent)),
        )
        self.assertEqual(profile.read_bytes(), expected)
        self.assertEqual(profile.read_bytes().count(PATH_STANZA), 1)
        self.assertTrue(second.cli_state.path_change_added)
        self.assertEqual(second.cli_state.modified_profile, str(profile))

    def test_symlink_wrong_type_and_wrong_owner_are_refused(self) -> None:
        profile = self.home / ".zprofile"
        target = self.home / "target"
        target.write_text("target\n", encoding="utf-8")
        profile.symlink_to(target)
        with self.assertRaisesRegex(BootstrapError, "symlinked"):
            configure_cli_path(
                self.cli,
                home=self.home,
                shell=self.zsh,
                runner=self.runner_with_path("/usr/bin"),
            )
        profile.unlink()
        profile.mkdir()
        with self.assertRaisesRegex(BootstrapError, "regular file"):
            configure_cli_path(
                self.cli,
                home=self.home,
                shell=self.zsh,
                runner=self.runner_with_path("/usr/bin"),
            )
        profile.rmdir()
        profile.write_text("owned\n", encoding="utf-8")
        with self.assertRaisesRegex(BootstrapError, "not owned"):
            configure_cli_path(
                self.cli,
                home=self.home,
                shell=self.zsh,
                uid=os.getuid() + 1,
                runner=self.runner_with_path("/usr/bin"),
            )

    def test_corrupted_markers_are_refused_without_edit(self) -> None:
        profile = self.home / ".zprofile"
        corrupted = f"before\n{PATH_MARKER_BEGIN}\nafter\n".encode()
        profile.write_bytes(corrupted)
        with self.assertRaisesRegex(BootstrapError, "corrupted"):
            configure_cli_path(
                self.cli,
                home=self.home,
                shell=self.zsh,
                runner=self.runner_with_path("/usr/bin"),
            )
        self.assertEqual(profile.read_bytes(), corrupted)
        self.assertNotIn(PATH_MARKER_END.encode(), profile.read_bytes())

    def test_transaction_rollback_removes_new_profile_or_restores_existing(self) -> None:
        profile = self.home / ".zprofile"
        created = configure_cli_path(
            self.cli,
            home=self.home,
            shell=self.zsh,
            runner=self.runner_with_path("/usr/bin"),
        )
        self.assertTrue(profile.exists())
        created.rollback()
        self.assertFalse(profile.exists())

        profile.write_bytes(b"original\n")
        changed = configure_cli_path(
            self.cli,
            home=self.home,
            shell=self.zsh,
            runner=self.runner_with_path("/usr/bin"),
        )
        changed.rollback()
        self.assertEqual(profile.read_bytes(), b"original\n")


if __name__ == "__main__":
    unittest.main()
