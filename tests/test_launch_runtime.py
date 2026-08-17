from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from manager.discoveryagent_cli.browser_presence import (
    BrowserPresence,
    browser_presence_path,
    detect_browser_presence,
)
from manager.discoveryagent_cli.errors import LaunchRuntimeError
from manager.discoveryagent_cli.frontend_process import (
    ERROR_PREFIX,
    STARTUP_PREFIX,
    FrontendOutputCapture,
    parse_startup_record,
    readiness_is_healthy,
    wait_for_readiness,
    wait_for_startup,
)
from manager.discoveryagent_cli.installation import validate_launch_installation
from manager.discoveryagent_cli.launch import (
    INTERNAL_NO_BROWSER_ENV,
    LaunchDependencies,
    build_frontend_environment,
    frontend_command,
    launch_installed_application,
)
from manager.discoveryagent_cli.onboarding import paperclip_auth_available_locally
from manager.discoveryagent_cli.runtime_state import (
    FRONTEND_RECORD_SCHEMA_VERSION,
    FrontendLaunchLock,
    FrontendRuntimeRecord,
    load_frontend_record,
    write_frontend_record_atomic,
)
from manager.discoveryagent_cli.state import load_state
from manager.discoveryagent_cli.testing import create_fake_installation


def startup_line(pid: int = 4242, *, payload: dict | None = None) -> str:
    selected = payload or {
        "schema_version": 1,
        "pid": pid,
        "host": "127.0.0.1",
        "port": 43123,
        "url": "http://127.0.0.1:43123/",
        "readiness_url": "http://127.0.0.1:43123/api/health",
    }
    return STARTUP_PREFIX + json.dumps(selected)


class FakeCapture:
    def __init__(self, lines: list[str]) -> None:
        self.lines = list(lines)
        self.closed = False

    def get_protocol_line(self, _timeout: float) -> str | None:
        return self.lines.pop(0) if self.lines else None

    def close(self, timeout: float = 2.0) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self, *, pid: int = 4242, early_status: int | None = None) -> None:
        self.pid = pid
        self.returncode = early_status
        self.stdout = io.StringIO()
        self.terminated = False
        self.killed = False
        self.wait_hook = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.wait_hook is not None:
            self.wait_hook()
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9


class FakeResponse:
    def __init__(self, payload: object, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode("utf-8")


class StartupProtocolTests(unittest.TestCase):
    def test_valid_startup_record(self) -> None:
        record = parse_startup_record(startup_line(), expected_pid=4242)
        self.assertEqual(record.port, 43123)
        self.assertEqual(record.url, "http://127.0.0.1:43123/")

    def test_malformed_startup_json(self) -> None:
        with self.assertRaisesRegex(LaunchRuntimeError, "malformed startup JSON"):
            parse_startup_record(STARTUP_PREFIX + "{broken", expected_pid=4242)

    def test_missing_extra_and_invalid_fields(self) -> None:
        valid = json.loads(startup_line()[len(STARTUP_PREFIX) :])
        cases = []
        missing = dict(valid)
        missing.pop("url")
        cases.append(missing)
        wrong_pid = dict(valid, pid=9999)
        cases.append(wrong_pid)
        non_loopback = dict(valid, host="0.0.0.0")
        cases.append(non_loopback)
        mismatched_url = dict(valid, url="http://127.0.0.1:5000/")
        cases.append(mismatched_url)
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(LaunchRuntimeError):
                    parse_startup_record(startup_line(payload=payload), expected_pid=4242)

    def test_duplicate_startup_record_is_rejected(self) -> None:
        process = FakeProcess()
        capture = FakeCapture([startup_line(), startup_line()])
        with self.assertRaisesRegex(LaunchRuntimeError, "duplicate"):
            wait_for_startup(process, capture)

    def test_explicit_error_record_is_rejected(self) -> None:
        process = FakeProcess()
        capture = FakeCapture(
            [ERROR_PREFIX + json.dumps({"error": "startup_failed", "message": "safe"})]
        )
        with self.assertRaisesRegex(LaunchRuntimeError, "startup_failed"):
            wait_for_startup(process, capture)

    def test_early_exit_and_timeout(self) -> None:
        with self.assertRaisesRegex(LaunchRuntimeError, "exited before startup"):
            wait_for_startup(FakeProcess(early_status=7), FakeCapture([]), timeout=0.2)
        with self.assertRaisesRegex(LaunchRuntimeError, "Timed out"):
            wait_for_startup(FakeProcess(), FakeCapture([]), timeout=0)

    def test_health_success_and_malformed_failure(self) -> None:
        healthy = lambda *_args, **_kwargs: FakeResponse(
            {"status": "ok", "service": "discoveryagent-frontend"}
        )
        malformed = lambda *_args, **_kwargs: FakeResponse(b"not-json")
        wrong = lambda *_args, **_kwargs: FakeResponse(
            {"status": "ok", "service": "different"}
        )
        self.assertTrue(readiness_is_healthy("http://127.0.0.1:1/api/health", opener=healthy))
        self.assertFalse(
            readiness_is_healthy("http://127.0.0.1:1/api/health", opener=malformed)
        )
        self.assertFalse(readiness_is_healthy("http://127.0.0.1:1/api/health", opener=wrong))

    def test_readiness_failure_and_duplicate_during_readiness(self) -> None:
        process = FakeProcess()
        startup = parse_startup_record(startup_line(), expected_pid=4242)
        with self.assertRaisesRegex(LaunchRuntimeError, "Timed out"):
            wait_for_readiness(
                process,
                FakeCapture([]),
                startup,
                health_probe=lambda _url: False,
                timeout=0.01,
            )
        with self.assertRaisesRegex(LaunchRuntimeError, "duplicate"):
            wait_for_readiness(
                process,
                FakeCapture([startup_line()]),
                startup,
                health_probe=lambda _url: True,
            )

    def test_output_log_redacts_inherited_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            log = Path(temporary_name) / "frontend.log"
            capture = FrontendOutputCapture(
                io.StringIO("ordinary\nsecret-value\n"),
                log,
                secrets=("secret-value",),
            )
            capture.close()
            written = log.read_text(encoding="utf-8")
            self.assertNotIn("secret-value", written)
            self.assertIn("[REDACTED]", written)


class LaunchRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.paths, self.state = create_fake_installation(self.root / "install")
        self.validated = validate_launch_installation(
            self.paths, self.state, platform_name="darwin"
        )
        self.process = FakeProcess()
        self.capture = FakeCapture([startup_line()])
        self.popen_calls: list[tuple[list[str], dict]] = []
        self.browser_calls: list[str] = []
        self.terminate_calls: list[int] = []
        self.health_calls: list[str] = []
        self.capture_factory_calls: list[dict] = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def popen(self, command, **kwargs):
        self.popen_calls.append((command, kwargs))
        return self.process

    def capture_factory(self, _stream, _log_path, **_kwargs):
        self.capture_factory_calls.append(_kwargs)
        return self.capture

    def browser(self, url, **_kwargs):
        self.browser_calls.append(url)
        return True

    def health(self, url):
        self.health_calls.append(url)
        return True

    def terminate(self, process, **_kwargs):
        self.terminate_calls.append(process.pid)
        process.terminate()

    def dependencies(self, **overrides) -> LaunchDependencies:
        def forbidden_secure_input(_prompt):
            raise AssertionError("secure input was not expected")

        def forbidden_onboarding_run(*_args, **_kwargs):
            raise AssertionError("Paperclip onboarding was not expected")

        values = {
            "popen": self.popen,
            "health_probe": self.health,
            "browser_open": self.browser,
            "browser_presence": lambda _url: BrowserPresence.ABSENT,
            "pid_alive": lambda _pid: True,
            "capture_factory": self.capture_factory,
            "terminate": self.terminate,
            "wall_time": lambda: 1234.5,
            "secure_input": forbidden_secure_input,
            "input_is_interactive": lambda: False,
            "paperclip_auth_available": lambda _environment: True,
            "onboarding_run": forbidden_onboarding_run,
        }
        values.update(overrides)
        return LaunchDependencies(**values)

    def launch(self, *, environment=None, dependencies=None, readiness_timeout=30.0):
        stdout = io.StringIO()
        stderr = io.StringIO()
        selected_environment = {
            "ANTHROPIC_API_KEY": "real-secret-value",
            "HOME": "/Users/test",
            INTERNAL_NO_BROWSER_ENV: "1",
        }
        if environment is not None:
            selected_environment = environment
        result = launch_installed_application(
            self.paths,
            manager_environment=selected_environment,
            stdout=stdout,
            stderr=stderr,
            dependencies=dependencies or self.dependencies(),
            readiness_timeout=readiness_timeout,
        )
        return result, stdout.getvalue(), stderr.getvalue()

    def test_noninteractive_missing_key_stops_before_spawn(self) -> None:
        with self.assertRaisesRegex(LaunchRuntimeError, "interactive terminal"):
            self.launch(environment={"HOME": "/Users/test"})
        self.assertEqual(self.popen_calls, [])

    def test_exact_command_neutral_cwd_environment_and_secret_handling(self) -> None:
        environment = {
            "ANTHROPIC_API_KEY": "real-secret-value",
            "HOME": "/Users/test",
            "USER": "test",
            "HTTPS_PROXY": "http://proxy.invalid",
            "CLAUDE_CODE_CLI_PATH": "/developer/claude",
            "NODE_PATH": "/developer/node",
            "WRITING_AGENT_CLI_PATH": "/developer/writer",
            "WRITING_AGENT_USE_USER_CLI": "1",
            "DISCOVERYAGENT_BACKEND_ROOT": "/developer/backend",
            "DISCOVERYAGENT_BACKEND_PYTHON": "/developer/python",
            "DISCOVERYAGENT_DATA_DIR": "/developer/data",
            "PYTHONPATH": "/developer/source",
            "VIRTUAL_ENV": "/developer/.venv",
            INTERNAL_NO_BROWSER_ENV: "1",
        }
        result, stdout, stderr = self.launch(environment=environment)
        self.assertEqual(result, 0)
        command, kwargs = self.popen_calls[0]
        self.assertEqual(command, frontend_command(self.validated))
        self.assertEqual(kwargs["cwd"], self.paths.frontend_cwd)
        self.assertNotIn("shell", kwargs)
        child = kwargs["env"]
        self.assertEqual(child["ANTHROPIC_API_KEY"], "real-secret-value")
        self.assertEqual(child["HOME"], "/Users/test")
        self.assertEqual(child["HTTPS_PROXY"], "http://proxy.invalid")
        self.assertEqual(child["DISCOVERYAGENT_BACKEND_ROOT"], str(self.validated.release.backend_root))
        self.assertEqual(child["DISCOVERYAGENT_BACKEND_PYTHON"], str(self.validated.backend_python))
        self.assertEqual(child["DISCOVERYAGENT_DATA_DIR"], str(self.validated.data_root))
        for removed in (
            "CLAUDE_CODE_CLI_PATH",
            "NODE_PATH",
            "WRITING_AGENT_CLI_PATH",
            "WRITING_AGENT_USE_USER_CLI",
            "PYTHONPATH",
            "VIRTUAL_ENV",
            INTERNAL_NO_BROWSER_ENV,
        ):
            self.assertNotIn(removed, child)
        serialized = self.paths.state_file.read_text(encoding="utf-8") + stdout + stderr
        self.assertNotIn("real-secret-value", serialized)
        self.assertEqual(self.browser_calls, [])

    def test_prompted_key_is_session_only_and_secret_free(self) -> None:
        secret = "synthetic-prompted-secret"
        observed_runtime = ""

        def inspect_runtime_record():
            nonlocal observed_runtime
            observed_runtime = self.paths.frontend_runtime_file.read_text(encoding="utf-8")

        self.process.wait_hook = inspect_runtime_record
        environment = {"HOME": "/Users/test", INTERNAL_NO_BROWSER_ENV: "1"}
        result, stdout, stderr = self.launch(
            environment=environment,
            dependencies=self.dependencies(
                input_is_interactive=lambda: True,
                secure_input=lambda prompt: secret if prompt == "> " else "",
            ),
        )

        self.assertEqual(result, 0)
        self.assertIn("Welcome to DiscoveryAgent", stdout)
        self.assertIn("Anthropic API key", stdout)
        self.assertIn("✓ API key available for this session", stdout)
        self.assertNotIn("is missing", stdout)
        command, kwargs = self.popen_calls[0]
        self.assertEqual(kwargs["env"]["ANTHROPIC_API_KEY"], secret)
        self.assertNotIn(secret, repr(command))
        self.assertNotIn("ANTHROPIC_API_KEY", environment)
        self.assertEqual(self.capture_factory_calls[0]["secrets"], (secret,))
        persisted = (
            self.paths.state_file.read_text(encoding="utf-8")
            + observed_runtime
            + stdout
            + stderr
        )
        self.assertNotIn(secret, persisted)

    def test_new_paperclip_login_and_verification_precede_frontend_spawn(self) -> None:
        trace: list[str] = []
        onboarding_commands: list[list[str]] = []

        def onboarding_run(command, **_kwargs):
            onboarding_commands.append(command)
            trace.append("login" if "login" in command else "verify")
            return subprocess.CompletedProcess(command, 0)

        def secure_input(prompt):
            self.assertEqual(prompt, "> ")
            trace.append("key")
            return "synthetic-prompted-key"

        def popen(command, **kwargs):
            trace.append("spawn")
            return self.popen(command, **kwargs)

        def health(url):
            trace.append("ready")
            return self.health(url)

        def browser(url, **kwargs):
            trace.append("browser")
            return self.browser(url, **kwargs)

        result, stdout, _stderr = self.launch(
            environment={"HOME": "/Users/test"},
            dependencies=self.dependencies(
                input_is_interactive=lambda: True,
                secure_input=secure_input,
                paperclip_auth_available=lambda _environment: False,
                onboarding_run=onboarding_run,
                popen=popen,
                health_probe=health,
                browser_open=browser,
            ),
        )

        self.assertEqual(result, 0)
        self.assertEqual(
            trace, ["key", "login", "verify", "spawn", "ready", "browser"]
        )
        self.assertEqual(
            onboarding_commands[0][-3:],
            ["gxl_paperclip", "login", "--no-install"],
        )
        self.assertIn("✓ API key available for this session", stdout)
        self.assertIn("✓ Paperclip connected", stdout)
        self.assertIn("Launching DiscoveryAgent...", stdout)

    def test_paperclip_login_or_verification_failure_prevents_frontend(self) -> None:
        for failing_call in (1, 2):
            with self.subTest(failing_call=failing_call):
                calls = 0

                def onboarding_run(command, **_kwargs):
                    nonlocal calls
                    calls += 1
                    return subprocess.CompletedProcess(
                        command, 1 if calls == failing_call else 0
                    )

                self.popen_calls.clear()
                with self.assertRaises(LaunchRuntimeError):
                    self.launch(
                        dependencies=self.dependencies(
                            paperclip_auth_available=lambda _environment: False,
                            onboarding_run=onboarding_run,
                        )
                    )
                self.assertEqual(self.popen_calls, [])
                self.assertEqual(calls, failing_call)

    def test_malformed_paperclip_credentials_trigger_onboarding_without_persisting_tokens(
        self,
    ) -> None:
        home = self.root / "paperclip-home"
        credentials = home / ".paperclip" / "credentials.json"
        credentials.parent.mkdir(parents=True)
        credentials.write_text("{malformed", encoding="utf-8")
        calls: list[list[str]] = []

        def onboarding_run(command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        environment = {
            "ANTHROPIC_API_KEY": "synthetic-anthropic-key",
            "HOME": str(home),
        }
        self.launch(
            environment=environment,
            dependencies=self.dependencies(
                paperclip_auth_available=paperclip_auth_available_locally,
                onboarding_run=onboarding_run,
            ),
        )
        self.assertEqual(len(calls), 2)
        persisted = self.paths.state_file.read_text(encoding="utf-8")
        self.assertNotIn("refresh_token", persisted)
        self.assertNotIn("id_token", persisted)

    def test_existing_paperclip_credentials_skip_login_and_stay_external(self) -> None:
        home = self.root / "configured-paperclip-home"
        credentials = home / ".paperclip" / "credentials.json"
        credentials.parent.mkdir(parents=True)
        token = "synthetic-paperclip-refresh-token"
        credentials.write_text(
            json.dumps({"refresh_token": token, "id_token": "synthetic-id-token"}),
            encoding="utf-8",
        )

        def forbidden_onboarding(*_args, **_kwargs):
            raise AssertionError("existing credentials must skip Paperclip login")

        result, stdout, stderr = self.launch(
            environment={
                "ANTHROPIC_API_KEY": "synthetic-anthropic-key",
                "HOME": str(home),
            },
            dependencies=self.dependencies(
                paperclip_auth_available=paperclip_auth_available_locally,
                onboarding_run=forbidden_onboarding,
            ),
        )
        self.assertEqual(result, 0)
        self.assertNotIn("Connecting Paperclip", stdout)
        persisted = self.paths.state_file.read_text(encoding="utf-8") + stdout + stderr
        self.assertNotIn(token, persisted)

    def test_first_launch_data_root_choice_is_persisted_and_handed_to_the_frontend(self) -> None:
        chosen = self.root / "Science" / "DiscoveryAgent"
        configured = Path(self.state.data_root)
        # setUp validates the fixture, which creates the configured root; a
        # genuine first launch has not created one yet.
        configured.rmdir()
        self.assertFalse(chosen.exists())

        self.launch(
            dependencies=self.dependencies(
                input_is_interactive=lambda: True,
                text_input=lambda _prompt: str(chosen),
            ),
        )

        self.assertEqual(load_state(self.paths.state_file).data_root, str(chosen))
        _command, kwargs = self.popen_calls[0]
        self.assertEqual(kwargs["env"]["DISCOVERYAGENT_DATA_DIR"], str(chosen))
        self.assertTrue(chosen.is_dir())
        self.assertFalse(configured.exists())

    def test_an_established_data_root_is_used_without_prompting(self) -> None:
        Path(self.state.data_root).mkdir(parents=True, exist_ok=True)

        self.launch(
            dependencies=self.dependencies(
                input_is_interactive=lambda: True,
                text_input=lambda _prompt: (_ for _ in ()).throw(
                    AssertionError("an established data root must not be re-asked")
                ),
            ),
        )

        self.assertEqual(load_state(self.paths.state_file).data_root, self.state.data_root)

    def test_browser_opens_only_after_readiness(self) -> None:
        trace: list[str] = []

        def health(_url):
            trace.append("health")
            return True

        def browser(_url, **_kwargs):
            trace.append("browser")
            return True

        environment = {"ANTHROPIC_API_KEY": "key", "HOME": "/Users/test"}
        self.launch(
            environment=environment,
            dependencies=self.dependencies(health_probe=health, browser_open=browser),
        )
        self.assertEqual(trace, ["health", "browser"])

    def test_runtime_record_exists_while_owned_and_is_removed_on_exit(self) -> None:
        observed = {}

        def inspect_record():
            observed["record"] = load_frontend_record(self.paths.frontend_runtime_file)

        self.process.wait_hook = inspect_record
        self.launch()
        record = observed["record"]
        self.assertEqual(record.pid, self.process.pid)
        self.assertEqual(record.active_release_id, self.state.active_release_id)
        self.assertEqual(record.started_at, 1234.5)
        self.assertIsNone(record.browser_opened_at)
        self.assertFalse(self.paths.frontend_runtime_file.exists())

    def test_successful_initial_browser_open_is_recorded(self) -> None:
        observed = {}

        def inspect_record():
            observed["record"] = load_frontend_record(self.paths.frontend_runtime_file)

        self.process.wait_hook = inspect_record
        self.launch(
            environment={"ANTHROPIC_API_KEY": "key", "HOME": "/Users/test"}
        )
        self.assertEqual(observed["record"].browser_opened_at, 1234.5)
        self.assertEqual(self.browser_calls, ["http://127.0.0.1:43123/"])

    def test_failed_initial_browser_open_is_not_recorded_or_suppressed(self) -> None:
        observed = {}

        def failed_browser(_url, **_kwargs):
            return False

        def inspect_record():
            observed["record"] = load_frontend_record(self.paths.frontend_runtime_file)

        self.process.wait_hook = inspect_record
        self.launch(
            environment={"ANTHROPIC_API_KEY": "key", "HOME": "/Users/test"},
            dependencies=self.dependencies(browser_open=failed_browser),
        )
        failed_record = observed["record"]
        self.assertIsNone(failed_record.browser_opened_at)

        write_frontend_record_atomic(self.paths.frontend_runtime_file, failed_record)
        self.browser_calls.clear()
        self.launch(
            environment={"HOME": "/Users/test"},
            dependencies=self.dependencies(
                browser_presence=lambda _url: BrowserPresence.ABSENT
            ),
        )
        self.assertEqual(self.browser_calls, [failed_record.url])

    def test_old_runtime_record_without_browser_timestamp_remains_readable(self) -> None:
        self.paths.frontend_runtime_file.parent.mkdir(parents=True, exist_ok=True)
        self.paths.frontend_runtime_file.write_text(
            json.dumps(
                {
                    "schema_version": FRONTEND_RECORD_SCHEMA_VERSION,
                    "pid": 9001,
                    "active_release_id": self.state.active_release_id,
                    "url": "http://127.0.0.1:43123/",
                    "readiness_url": "http://127.0.0.1:43123/api/health",
                    "started_at": 1000.0,
                }
            ),
            encoding="utf-8",
        )
        record = load_frontend_record(self.paths.frontend_runtime_file)
        self.assertIsNotNone(record)
        self.assertIsNone(record.browser_opened_at)

    def _write_existing_record(
        self, *, browser_opened_at: float | None = None
    ) -> FrontendRuntimeRecord:
        record = FrontendRuntimeRecord(
            FRONTEND_RECORD_SCHEMA_VERSION,
            9001,
            self.state.active_release_id,
            "http://127.0.0.1:43123/",
            "http://127.0.0.1:43123/api/health",
            1000.0,
            browser_opened_at,
        )
        write_frontend_record_atomic(self.paths.frontend_runtime_file, record)
        return record

    def test_existing_healthy_frontend_with_active_tab_reuses_without_browser(self) -> None:
        record = self._write_existing_record()
        environment = {"ANTHROPIC_API_KEY": "key", "HOME": "/Users/test"}
        result, stdout, _stderr = self.launch(
            environment=environment,
            dependencies=self.dependencies(
                browser_presence=lambda _url: BrowserPresence.ACTIVE
            ),
        )
        self.assertEqual(result, 0)
        self.assertIn("already running", stdout)
        self.assertEqual(self.popen_calls, [])
        self.assertEqual(self.terminate_calls, [])
        self.assertEqual(self.browser_calls, [])
        self.assertTrue(self.paths.frontend_runtime_file.exists())

    def test_existing_healthy_frontend_reuses_before_any_onboarding(self) -> None:
        self._write_existing_record()

        def forbidden(*_args, **_kwargs):
            raise AssertionError("healthy reuse must not perform onboarding")

        result, stdout, _stderr = self.launch(
            environment={"HOME": "/Users/test"},
            dependencies=self.dependencies(
                secure_input=forbidden,
                input_is_interactive=forbidden,
                paperclip_auth_available=forbidden,
                onboarding_run=forbidden,
                browser_presence=lambda _url: BrowserPresence.ACTIVE,
            ),
        )
        self.assertEqual(result, 0)
        self.assertIn("already running", stdout)
        self.assertEqual(self.popen_calls, [])
        self.assertEqual(self.browser_calls, [])
        self.assertEqual(self.terminate_calls, [])

    def test_recent_browser_open_suppresses_absent_lease_before_onboarding(self) -> None:
        self._write_existing_record(browser_opened_at=1220.0)

        def forbidden(*_args, **_kwargs):
            raise AssertionError("healthy reuse must not perform onboarding")

        result, stdout, _stderr = self.launch(
            environment={"HOME": "/Users/test"},
            dependencies=self.dependencies(
                wall_time=lambda: 1234.5,
                secure_input=forbidden,
                input_is_interactive=forbidden,
                paperclip_auth_available=forbidden,
                onboarding_run=forbidden,
                browser_presence=lambda _url: BrowserPresence.ABSENT,
            ),
        )
        self.assertEqual(result, 0)
        self.assertIn("already running", stdout)
        self.assertEqual(self.popen_calls, [])
        self.assertEqual(self.browser_calls, [])

    def test_absent_lease_at_grace_boundary_reopens_and_records_timestamp(self) -> None:
        record = self._write_existing_record(browser_opened_at=1219.5)
        self.launch(
            environment={"HOME": "/Users/test"},
            dependencies=self.dependencies(
                wall_time=lambda: 1234.5,
                browser_presence=lambda _url: BrowserPresence.ABSENT,
            ),
        )
        self.assertEqual(self.browser_calls, [record.url])
        updated = load_frontend_record(self.paths.frontend_runtime_file)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.browser_opened_at, 1234.5)

    def test_existing_healthy_frontend_without_active_tab_opens_existing_url(self) -> None:
        record = self._write_existing_record()

        def forbidden(*_args, **_kwargs):
            raise AssertionError("healthy reuse must not perform onboarding")

        result, stdout, _stderr = self.launch(
            environment={"HOME": "/Users/test"},
            dependencies=self.dependencies(
                secure_input=forbidden,
                input_is_interactive=forbidden,
                paperclip_auth_available=forbidden,
                onboarding_run=forbidden,
                browser_presence=lambda _url: BrowserPresence.ABSENT,
            ),
        )
        self.assertEqual(result, 0)
        self.assertIn("already running", stdout)
        self.assertEqual(self.popen_calls, [])
        self.assertEqual(self.browser_calls, [record.url])
        self.assertEqual(self.terminate_calls, [])
        updated = load_frontend_record(self.paths.frontend_runtime_file)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.browser_opened_at, 1234.5)

    def test_stale_presence_avoids_duplicate_browser_and_onboarding(self) -> None:
        self._write_existing_record()

        def forbidden(*_args, **_kwargs):
            raise AssertionError("healthy reuse must not perform onboarding")

        result, stdout, _stderr = self.launch(
            environment={"HOME": "/Users/test"},
            dependencies=self.dependencies(
                secure_input=forbidden,
                input_is_interactive=forbidden,
                paperclip_auth_available=forbidden,
                onboarding_run=forbidden,
                browser_presence=lambda _url: BrowserPresence.AMBIGUOUS_STALE,
            ),
        )
        self.assertEqual(result, 0)
        self.assertIn("already running", stdout)
        self.assertEqual(self.popen_calls, [])
        self.assertEqual(self.browser_calls, [])
        self.assertEqual(self.terminate_calls, [])

    def test_unknown_or_failed_presence_avoids_duplicate_browser(self) -> None:
        record = self._write_existing_record()

        def failed_presence(_url):
            raise OSError("presence unavailable")

        for presence_probe in (
            lambda _url: BrowserPresence.UNKNOWN,
            failed_presence,
        ):
            with self.subTest(presence_probe=presence_probe):
                result, stdout, _stderr = self.launch(
                    environment={"HOME": "/Users/test"},
                    dependencies=self.dependencies(browser_presence=presence_probe),
                )
                self.assertEqual(result, 0)
                self.assertIn(record.url, stdout)
                self.assertEqual(self.popen_calls, [])
                self.assertEqual(self.browser_calls, [])
                self.assertEqual(self.terminate_calls, [])

    def test_stale_pid_and_live_pid_failed_readiness_both_spawn_new_frontend(self) -> None:
        for name, pid_alive in (("dead", False), ("unready", True)):
            with self.subTest(name=name):
                record = FrontendRuntimeRecord(
                    FRONTEND_RECORD_SCHEMA_VERSION,
                    9001,
                    self.state.active_release_id,
                    "http://127.0.0.1:43123/",
                    "http://127.0.0.1:43123/api/health",
                    1000.0,
                )
                write_frontend_record_atomic(self.paths.frontend_runtime_file, record)
                readiness_calls = 0

                def health(_url):
                    nonlocal readiness_calls
                    readiness_calls += 1
                    return readiness_calls > 1 if pid_alive else True

                self.process = FakeProcess()
                self.capture = FakeCapture([startup_line()])
                self.popen_calls.clear()
                self.launch(
                    dependencies=self.dependencies(
                        pid_alive=lambda _pid, alive=pid_alive: alive,
                        health_probe=health,
                    )
                )
                self.assertEqual(len(self.popen_calls), 1)

    def test_malformed_runtime_record_is_removed_before_new_spawn(self) -> None:
        self.paths.frontend_runtime_file.parent.mkdir(parents=True, exist_ok=True)
        self.paths.frontend_runtime_file.write_text("{malformed", encoding="utf-8")
        self.launch()
        self.assertEqual(len(self.popen_calls), 1)
        self.assertFalse(self.paths.frontend_runtime_file.exists())

    def test_ctrl_c_terminates_owned_frontend_and_removes_record(self) -> None:
        def interrupt():
            raise KeyboardInterrupt()

        self.process.wait_hook = interrupt
        result, _stdout, _stderr = self.launch()
        self.assertEqual(result, 130)
        self.assertEqual(self.terminate_calls, [self.process.pid])
        self.assertFalse(self.paths.frontend_runtime_file.exists())

    def test_failed_health_terminates_frontend_and_does_not_open_browser(self) -> None:
        with self.assertRaisesRegex(LaunchRuntimeError, "Timed out"):
            self.launch(
                dependencies=self.dependencies(health_probe=lambda _url: False),
                readiness_timeout=0.01,
            )
        self.assertEqual(self.terminate_calls, [self.process.pid])
        self.assertEqual(self.browser_calls, [])
        self.assertFalse(self.paths.frontend_runtime_file.exists())


class LaunchLockTests(unittest.TestCase):
    def test_concurrent_launch_is_serialized_and_stale_lock_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            path = Path(temporary_name) / "frontend.lock"
            first = FrontendLaunchLock(path, timeout=0)
            first.acquire()
            try:
                second = FrontendLaunchLock(path, timeout=0, pid_alive=lambda _pid: True)
                with self.assertRaisesRegex(LaunchRuntimeError, "still starting"):
                    second.acquire()
            finally:
                first.release()

            path.write_text(
                json.dumps({"pid": 9999, "created_at": time.time(), "token": "old"}),
                encoding="utf-8",
            )
            recovered = FrontendLaunchLock(path, timeout=0, pid_alive=lambda _pid: False)
            recovered.acquire()
            self.assertTrue(recovered.acquired)
            recovered.release()
            self.assertFalse(path.exists())


class BrowserPresenceTests(unittest.TestCase):
    def test_active_absent_stale_and_unknown_leases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            url = "http://127.0.0.1:43123/"
            path = browser_presence_path(url, temp_directory=temporary_name)
            self.assertIsNotNone(path)
            self.assertEqual(
                detect_browser_presence(
                    url, now=lambda: 1000.0, temp_directory=temporary_name
                ),
                BrowserPresence.ABSENT,
            )
            path.write_text(json.dumps({"active-token-1234": 995.0}), encoding="utf-8")
            self.assertEqual(
                detect_browser_presence(
                    url, now=lambda: 1000.0, temp_directory=temporary_name
                ),
                BrowserPresence.ACTIVE,
            )
            path.write_text(json.dumps({"stale-token-12345": 900.0}), encoding="utf-8")
            self.assertEqual(
                detect_browser_presence(
                    url, now=lambda: 1000.0, temp_directory=temporary_name
                ),
                BrowserPresence.AMBIGUOUS_STALE,
            )
            path.write_text("{malformed", encoding="utf-8")
            self.assertEqual(
                detect_browser_presence(
                    url, now=lambda: 1000.0, temp_directory=temporary_name
                ),
                BrowserPresence.UNKNOWN,
            )
            path.unlink()
            path.mkdir()
            self.assertEqual(
                detect_browser_presence(
                    url, now=lambda: 1000.0, temp_directory=temporary_name
                ),
                BrowserPresence.UNKNOWN,
            )


if __name__ == "__main__":
    unittest.main()
