from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from manager.discoveryagent_cli.cli import build_parser, main
from manager.discoveryagent_cli.errors import ActiveResearchError


class LifecycleCliTests(unittest.TestCase):
    def test_update_and_uninstall_commands_parse(self) -> None:
        self.assertEqual(build_parser().parse_args(["update"]).command, "update")
        self.assertEqual(build_parser().parse_args(["uninstall"]).command, "uninstall")

    def test_active_round_refusal_copy_is_exact_without_manager_prefix(self) -> None:
        cases = (
            (
                "update",
                "DiscoveryAgent has an active research round.\n"
                "Stop the round or wait for it to finish before updating.\n",
                "manager.discoveryagent_cli.cli.update_installed_application",
            ),
            (
                "uninstall",
                "DiscoveryAgent has an active research round.\n"
                "Stop the round or wait for it to finish before uninstalling.\n",
                "manager.discoveryagent_cli.cli.uninstall_installed_application",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary_name:
            for command, expected, patch_target in cases:
                with self.subTest(command=command):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with mock.patch(
                        patch_target,
                        side_effect=ActiveResearchError(expected.rstrip("\n")),
                    ):
                        result = main(
                            [command],
                            install_root=Path(temporary_name) / command,
                            stdout=stdout,
                            stderr=stderr,
                            environ={},
                        )
                    self.assertEqual(result, 1)
                    self.assertEqual(stderr.getvalue(), expected)
                    self.assertNotIn("DiscoveryAgent error:", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
