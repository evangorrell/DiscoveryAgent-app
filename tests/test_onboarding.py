from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from manager.discoveryagent_cli.errors import LaunchRuntimeError
from manager.discoveryagent_cli.installation import validate_launch_installation
from manager.discoveryagent_cli.onboarding import (
    PAPERCLIP_VERIFY_CODE,
    paperclip_auth_available_locally,
    paperclip_credentials_path,
    paperclip_login_command,
    paperclip_subprocess_environment,
    paperclip_verify_command,
    resolve_anthropic_api_key,
    resolve_data_root,
    run_paperclip_login,
    verify_paperclip_login,
)
from manager.discoveryagent_cli.testing import create_fake_installation


class AnthropicOnboardingTests(unittest.TestCase):
    def test_inherited_key_is_silent_and_secure_input_is_skipped(self) -> None:
        source = {"ANTHROPIC_API_KEY": "synthetic-inherited-key", "HOME": "/tmp/home"}
        output = io.StringIO()

        environment, key, prompted = resolve_anthropic_api_key(
            source,
            interactive=lambda: False,
            secure_input=lambda _prompt: (_ for _ in ()).throw(
                AssertionError("inherited keys must not prompt")
            ),
            stdout=output,
        )

        self.assertEqual(key, "synthetic-inherited-key")
        self.assertFalse(prompted)
        self.assertEqual(environment["ANTHROPIC_API_KEY"], key)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(source["ANTHROPIC_API_KEY"], key)

    def test_missing_key_uses_secure_prompt_and_only_updates_returned_environment(self) -> None:
        source = {"HOME": "/tmp/home"}
        prompts: list[str] = []
        output = io.StringIO()

        environment, key, prompted = resolve_anthropic_api_key(
            source,
            interactive=lambda: True,
            secure_input=lambda prompt: prompts.append(prompt) or "  synthetic-prompted-key  ",
            stdout=output,
        )

        self.assertEqual(prompts, ["> "])
        self.assertEqual(key, "synthetic-prompted-key")
        self.assertTrue(prompted)
        self.assertNotIn("ANTHROPIC_API_KEY", source)
        self.assertEqual(environment["ANTHROPIC_API_KEY"], key)
        self.assertIn("Anthropic API key", output.getvalue())
        self.assertIn("✓ API key available for this session", output.getvalue())
        self.assertNotIn(key, output.getvalue())

    def test_empty_and_noninteractive_input_fail_with_actionable_secret_free_errors(self) -> None:
        cases = (
            (
                lambda: False,
                lambda _prompt: "unused-secret",
                "interactive terminal",
            ),
            (lambda: True, lambda _prompt: "   ", "No Anthropic API key was entered"),
        )
        for interactive, secure_input, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(LaunchRuntimeError, expected) as raised:
                    resolve_anthropic_api_key(
                        {"HOME": "/tmp/home"},
                        interactive=interactive,
                        secure_input=secure_input,
                        stdout=io.StringIO(),
                    )
                self.assertNotIn("unused-secret", str(raised.exception))


class PaperclipOnboardingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.paths, self.state = create_fake_installation(self.root / "install")
        self.validated = validate_launch_installation(
            self.paths, self.state, platform_name="darwin"
        )
        backend_environment = self.validated.backend_python.parent.parent
        (backend_environment / "pyvenv.cfg").write_text(
            "home = fixture-runtime\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_local_credential_detection_is_structural_and_network_free(self) -> None:
        home = self.root / "user-home"
        environment = {"HOME": str(home)}
        credentials = paperclip_credentials_path(environment)
        self.assertEqual(credentials, home / ".paperclip" / "credentials.json")
        self.assertFalse(paperclip_auth_available_locally(environment))

        credentials.parent.mkdir(parents=True)
        for payload in ("{malformed", "{}", '{"refresh_token": "   "}'):
            credentials.write_text(payload, encoding="utf-8")
            self.assertFalse(paperclip_auth_available_locally(environment))

        credentials.write_text(
            json.dumps(
                {
                    "refresh_token": "synthetic-paperclip-refresh-token",
                    "id_token": "synthetic-paperclip-id-token",
                }
            ),
            encoding="utf-8",
        )
        self.assertTrue(paperclip_auth_available_locally(environment))
        self.assertTrue(
            paperclip_auth_available_locally(
                {"HOME": str(self.root / "missing"), "PAPERCLIP_API_KEY": "synthetic"}
            )
        )
        self.assertTrue(
            paperclip_auth_available_locally(
                {
                    "HOME": str(self.root / "missing"),
                    "PAPERCLIP_BEARER_TOKEN": "synthetic",
                }
            )
        )

    def test_custom_config_directory_is_honored_but_never_created_or_set(self) -> None:
        home = self.root / "home"
        environment = {
            "HOME": str(home),
            "PAPERCLIP_CONFIG_DIR": "~/.custom-paperclip",
        }
        expected = home / ".custom-paperclip" / "credentials.json"
        self.assertEqual(paperclip_credentials_path(environment), expected)
        self.assertFalse(expected.exists())
        child = paperclip_subprocess_environment(self.validated, {"HOME": str(home)})
        self.assertNotIn("PAPERCLIP_CONFIG_DIR", child)

    def test_login_and_verification_use_backend_python_without_secret_arguments(self) -> None:
        calls: list[tuple[list[str], dict]] = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0)

        environment = {
            "HOME": str(self.root / "home"),
            "PATH": "/usr/bin",
            "ANTHROPIC_API_KEY": "synthetic-anthropic-secret",
        }
        run_paperclip_login(self.validated, environment, runner=runner)
        verify_paperclip_login(self.validated, environment, runner=runner)

        login_command, login_kwargs = calls[0]
        verify_command, verify_kwargs = calls[1]
        self.assertEqual(login_command, paperclip_login_command(self.validated))
        self.assertEqual(
            login_command,
            [
                str(self.validated.backend_python),
                "-m",
                "gxl_paperclip",
                "login",
                "--no-install",
            ],
        )
        self.assertEqual(verify_command, paperclip_verify_command(self.validated))
        self.assertEqual(verify_command[-1], PAPERCLIP_VERIFY_CODE)
        self.assertIn("health", verify_command[-1])
        self.assertNotIn("synthetic-anthropic-secret", repr(calls))
        for _command, kwargs in calls:
            child = kwargs["env"]
            self.assertNotIn("ANTHROPIC_API_KEY", child)
            self.assertEqual(child["HOME"], environment["HOME"])
            self.assertEqual(
                child["VIRTUAL_ENV"],
                str(self.validated.backend_python.parent.parent),
            )
            self.assertEqual(
                child["PATH"].split(os.pathsep)[0],
                str(self.validated.backend_python.parent),
            )
            self.assertNotIn("PAPERCLIP_CONFIG_DIR", child)
        self.assertNotIn("stdout", login_kwargs)
        self.assertNotIn("stderr", login_kwargs)
        self.assertEqual(verify_kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(verify_kwargs["stderr"], subprocess.DEVNULL)

    def test_login_and_verification_failures_are_actionable_and_secret_free(self) -> None:
        secret = "synthetic-paperclip-token"

        def failed(command, **_kwargs):
            return subprocess.CompletedProcess(command, 1)

        with self.assertRaisesRegex(LaunchRuntimeError, "did not complete") as login_error:
            run_paperclip_login(
                self.validated,
                {"HOME": str(self.root), "PAPERCLIP_BEARER_TOKEN": secret},
                runner=failed,
            )
        with self.assertRaisesRegex(LaunchRuntimeError, "could not be verified") as verify_error:
            verify_paperclip_login(
                self.validated,
                {"HOME": str(self.root), "PAPERCLIP_BEARER_TOKEN": secret},
                runner=failed,
            )
        self.assertNotIn(secret, str(login_error.exception))
        self.assertNotIn(secret, str(verify_error.exception))


class DataRootOnboardingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.install_root = self.root / "install"
        self.install_root.mkdir()

    def resolve(self, configured: Path, answers: list[str]) -> tuple[Path, str]:
        output = io.StringIO()
        remaining = list(answers)
        chosen = resolve_data_root(
            configured,
            install_root=self.install_root,
            interactive=lambda: True,
            text_input=lambda _prompt: remaining.pop(0),
            stdout=output,
        )
        return chosen, output.getvalue()

    def test_existing_data_root_is_never_second_guessed(self) -> None:
        configured = self.root / "already-here"
        configured.mkdir()

        chosen, printed = self.resolve(
            configured,
            ["/should/not/be/read"],
        )

        self.assertEqual(chosen, configured)
        self.assertEqual(printed, "")

    def test_non_interactive_launch_keeps_the_configured_default(self) -> None:
        configured = self.root / "unwritten"
        output = io.StringIO()

        chosen = resolve_data_root(
            configured,
            install_root=self.install_root,
            interactive=lambda: False,
            text_input=lambda _prompt: (_ for _ in ()).throw(
                AssertionError("a non-interactive launch must not prompt")
            ),
            stdout=output,
        )

        self.assertEqual(chosen, configured)
        self.assertEqual(output.getvalue(), "")

    def test_empty_answer_accepts_the_default_without_creating_anything(self) -> None:
        configured = self.root / "unwritten"

        chosen, _printed = self.resolve(configured, ["   "])

        self.assertEqual(chosen, configured)
        self.assertFalse(configured.exists())

    def test_chosen_path_is_expanded_and_returned(self) -> None:
        configured = self.root / "unwritten"
        target = self.root / "Science" / "DiscoveryAgent"

        chosen, printed = self.resolve(configured, [f"  {target}  "])

        self.assertEqual(chosen, target)
        self.assertIn(str(target), printed)
        # Choosing a location must not create it; validation owns that step.
        self.assertFalse(target.exists())

    def test_populated_directory_is_accepted_with_an_explicit_non_destructive_promise(self) -> None:
        configured = self.root / "unwritten"
        target = self.root / "existing-work"
        target.mkdir()
        (target / "thesis.tex").write_text("untouched", encoding="utf-8")

        chosen, printed = self.resolve(configured, [str(target)])

        self.assertEqual(chosen, target)
        self.assertIn("nothing already in it is moved, changed, or deleted", printed)
        self.assertEqual((target / "thesis.tex").read_text(encoding="utf-8"), "untouched")

    def test_an_existing_file_or_relative_path_is_refused_and_reprompted(self) -> None:
        configured = self.root / "unwritten"
        occupied = self.root / "notes.txt"
        occupied.write_text("mine", encoding="utf-8")
        accepted = self.root / "good"

        chosen, printed = self.resolve(
            configured,
            [str(occupied), "relative/path", str(accepted)],
        )

        self.assertEqual(chosen, accepted)
        self.assertIn("already exists and is not a folder", printed)
        self.assertIn("absolute path", printed)
        self.assertEqual(occupied.read_text(encoding="utf-8"), "mine")

    def test_the_program_folder_is_refused_in_both_directions(self) -> None:
        configured = self.root / "unwritten"
        accepted = self.root / "good"

        chosen, printed = self.resolve(
            configured,
            [str(self.install_root / "releases"), str(self.root), str(accepted)],
        )

        self.assertEqual(chosen, accepted)
        self.assertIn("replaced on every update", printed)
        self.assertIn("contains DiscoveryAgent's own program files", printed)

    def test_repeated_bad_answers_fall_back_to_the_default(self) -> None:
        configured = self.root / "unwritten"

        chosen, printed = self.resolve(configured, ["a", "b", "c"])

        self.assertEqual(chosen, configured)
        self.assertIn("Keeping the default", printed)


if __name__ == "__main__":
    unittest.main()
