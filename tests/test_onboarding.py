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


if __name__ == "__main__":
    unittest.main()
