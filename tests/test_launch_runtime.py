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
from manager.discoveryagent_cli.runtime_state import (
    FRONTEND_RECORD_SCHEMA_VERSION,
    FrontendLaunchLock,
    FrontendRuntimeRecord,
    load_frontend_record,
    write_frontend_record_atomic,
)
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

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def popen(self, command, **kwargs):
        self.popen_calls.append((command, kwargs))
        return self.process

    def capture_factory(self, _stream, _log_path, **_kwargs):
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
        values = {
            "popen": self.popen,
            "health_probe": self.health,
            "browser_open": self.browser,
            "browser_presence": lambda _url: BrowserPresence.INACTIVE,
            "pid_alive": lambda _pid: True,
            "capture_factory": self.capture_factory,
            "terminate": self.terminate,
            "wall_time": lambda: 1234.5,
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

    def test_required_key_absence_stops_before_spawn(self) -> None:
        with self.assertRaisesRegex(LaunchRuntimeError, "requires ANTHROPIC_API_KEY"):
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
        self.assertFalse(self.paths.frontend_runtime_file.exists())

    def _write_existing_record(self) -> FrontendRuntimeRecord:
        record = FrontendRuntimeRecord(
            FRONTEND_RECORD_SCHEMA_VERSION,
            9001,
            self.state.active_release_id,
            "http://127.0.0.1:43123/",
            "http://127.0.0.1:43123/api/health",
            1000.0,
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

    def test_existing_healthy_frontend_without_active_tab_opens_existing_url(self) -> None:
        record = self._write_existing_record()
        result, stdout, _stderr = self.launch(
            environment={"ANTHROPIC_API_KEY": "key", "HOME": "/Users/test"},
            dependencies=self.dependencies(
                browser_presence=lambda _url: BrowserPresence.INACTIVE
            ),
        )
        self.assertEqual(result, 0)
        self.assertIn("already running", stdout)
        self.assertEqual(self.popen_calls, [])
        self.assertEqual(self.browser_calls, [record.url])
        self.assertEqual(self.terminate_calls, [])

    def test_presence_failure_opens_url_without_terminating_reused_frontend(self) -> None:
        record = self._write_existing_record()

        def failed_presence(_url):
            raise OSError("presence unavailable")

        self.launch(
            environment={"ANTHROPIC_API_KEY": "key", "HOME": "/Users/test"},
            dependencies=self.dependencies(browser_presence=failed_presence),
        )
        self.assertEqual(self.popen_calls, [])
        self.assertEqual(self.browser_calls, [record.url])
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
    def test_fresh_stale_missing_and_malformed_leases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            url = "http://127.0.0.1:43123/"
            path = browser_presence_path(url, temp_directory=temporary_name)
            self.assertIsNotNone(path)
            self.assertEqual(
                detect_browser_presence(
                    url, now=lambda: 1000.0, temp_directory=temporary_name
                ),
                BrowserPresence.INACTIVE,
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
                BrowserPresence.INACTIVE,
            )
            path.write_text("{malformed", encoding="utf-8")
            self.assertEqual(
                detect_browser_presence(
                    url, now=lambda: 1000.0, temp_directory=temporary_name
                ),
                BrowserPresence.UNKNOWN,
            )


if __name__ == "__main__":
    unittest.main()
