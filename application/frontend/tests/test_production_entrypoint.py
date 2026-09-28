"""Production process contract for ``python -m da_selection_ui``."""

from __future__ import annotations

import io
import json
import os
import socket
import sys
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from da_selection_ui import app as app_module
from da_selection_ui import local_round_runner
from da_selection_ui.production_server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    ERROR_PREFIX,
    STARTUP_PREFIX,
    LaunchConfig,
    ProductionFrontendServer,
    parse_launch_config,
    run_frontend,
)
from da_selection_ui.project_storage import get_data_root


FRONTEND_PYTHON = Path(sys.executable).absolute()


class ReturningServer:
    def __init__(self, port: int):
        self.server_port = 43123 if port == 0 else port
        self.served = False
        self.closed = False
        self.shutdown_calls = 0

    def serve_forever(self):
        self.served = True

    def shutdown(self):
        self.shutdown_calls += 1

    def server_close(self):
        self.closed = True


def test_default_launch_contract_is_fixed_loopback():
    assert parse_launch_config([], environ={}) == LaunchConfig(
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
    )


def test_internal_host_and_port_environment_overrides_are_supported():
    assert parse_launch_config([], environ={
        "DISCOVERY_AGENT_HOST": "127.0.0.2",
        "DISCOVERY_AGENT_PORT": "0",
    }) == LaunchConfig(host="127.0.0.2", port=0)


def test_production_server_disables_debug_reloader_and_browser(monkeypatch):
    calls = {}
    fake_server = ReturningServer(0)

    def factory(host, port, app, **kwargs):
        calls.update(host=host, port=port, app=app, kwargs=kwargs)
        return fake_server

    monkeypatch.setattr(
        app_module.app,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("production entry point must not call Flask.run")
        ),
    )
    monkeypatch.setattr(
        app_module.webbrowser,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("production entry point must not open a browser")
        ),
    )
    app_module.app.debug = True
    stdout = io.StringIO()

    result = run_frontend(
        LaunchConfig(host="127.0.0.1", port=0),
        app=app_module.app,
        server_factory=factory,
        install_signal_handlers=False,
        stdout=stdout,
    )

    assert result == 0
    assert app_module.app.debug is False
    assert app_module.app.config["DEBUG"] is False
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 0
    assert calls["kwargs"] == {
        "threaded": True,
        "passthrough_errors": False,
    }
    assert "use_reloader" not in calls["kwargs"]
    assert fake_server.served and fake_server.closed
    payload = json.loads(stdout.getvalue().split(STARTUP_PREFIX, 1)[1])
    assert payload["port"] == 43123
    assert payload["readiness_url"] == "http://127.0.0.1:43123/api/health"


def test_readiness_endpoint_is_fast_and_side_effect_free():
    response = app_module.app.test_client().get("/api/health")

    assert response.status_code == 200
    assert response.get_json() == {
        "service": "discoveryagent-frontend",
        "status": "ok",
    }


def test_manager_storage_and_backend_environment_values_are_honored(tmp_path):
    data_root = tmp_path / "manager-data"
    backend_root = tmp_path / "manager-backend"
    backend_python = backend_root / "runtime" / "python"
    launcher = backend_root / "scripts" / "run_hb_sherlock.py"
    backend_python.parent.mkdir(parents=True)
    launcher.parent.mkdir(parents=True)
    backend_python.write_text("", encoding="utf-8")
    backend_python.chmod(0o755)
    launcher.write_text("", encoding="utf-8")
    environment = {
        "DISCOVERYAGENT_DATA_DIR": str(data_root),
        "DISCOVERYAGENT_BACKEND_ROOT": str(backend_root),
        "DISCOVERYAGENT_BACKEND_PYTHON": str(backend_python),
    }

    assert get_data_root(environ=environment) == data_root
    assert local_round_runner.resolve_backend_runtime(environment) == (
        backend_root,
        backend_python,
        launcher,
    )


def test_startup_failure_returns_nonzero_with_machine_readable_error():
    def failing_factory(*_args, **_kwargs):
        raise SystemExit(9)

    stderr = io.StringIO()
    result = run_frontend(
        LaunchConfig(host="127.0.0.1", port=8000),
        server_factory=failing_factory,
        install_signal_handlers=False,
        stdout=io.StringIO(),
        stderr=stderr,
    )

    assert result == 9
    payload = json.loads(stderr.getvalue().split(ERROR_PREFIX, 1)[1])
    assert payload == {"error": "startup_failed", "exit_code": 9}


def test_programmatic_shutdown_stops_server_thread():
    class BlockingServer(ReturningServer):
        def __init__(self):
            super().__init__(8000)
            self.started = threading.Event()
            self.stopped = threading.Event()

        def serve_forever(self):
            self.started.set()
            self.stopped.wait(timeout=3)

        def shutdown(self):
            self.shutdown_calls += 1
            self.stopped.set()

    fake_server = BlockingServer()
    server = ProductionFrontendServer(
        LaunchConfig(host="127.0.0.1", port=8000),
        server_factory=lambda *_args, **_kwargs: fake_server,
        install_signal_handlers=False,
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    assert fake_server.started.wait(timeout=1)

    server.shutdown()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert fake_server.closed


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((DEFAULT_HOST, 0))
        except PermissionError:
            pytest.skip("environment does not permit loopback socket binding")
        return int(probe.getsockname()[1])


def test_module_returns_nonzero_when_port_is_already_in_use(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind((DEFAULT_HOST, 0))
        except PermissionError:
            pytest.skip("environment does not permit loopback socket binding")
        listener.listen()
        port = int(listener.getsockname()[1])
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.update({
            "DISCOVERYAGENT_DATA_DIR": str(tmp_path / "manager-data"),
            "PYTHONUNBUFFERED": "1",
        })
        completed = subprocess.run(
            [str(FRONTEND_PYTHON), "-m", "da_selection_ui", "--port", str(port)],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )

    assert completed.returncode != 0
    assert ERROR_PREFIX in completed.stderr


def test_module_smoke_from_unrelated_working_directory(tmp_path):
    port = _available_port()
    launch_directory = tmp_path / "unrelated-cwd"
    data_root = tmp_path / "manager-data"
    launch_directory.mkdir()
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.update({
        "DISCOVERYAGENT_DATA_DIR": str(data_root),
        "DISCOVERYAGENT_BACKEND_ROOT": str(tmp_path / "manager-backend"),
        "DISCOVERYAGENT_BACKEND_PYTHON": str(tmp_path / "manager-python"),
        "PYTHONUNBUFFERED": "1",
    })
    process = subprocess.Popen(
        [str(FRONTEND_PYTHON), "-m", "da_selection_ui", "--port", str(port)],
        cwd=launch_directory,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    health_url = f"http://{DEFAULT_HOST}:{port}/api/health"
    page_url = f"http://{DEFAULT_HOST}:{port}/"
    deadline = time.monotonic() + 60
    health_payload = None
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(
                    f"frontend exited before readiness ({process.returncode})\n{stdout}\n{stderr}"
                )
            try:
                with urllib.request.urlopen(health_url, timeout=0.5) as response:
                    health_payload = json.load(response)
                break
            except OSError:
                time.sleep(0.05)
        assert health_payload == {
            "service": "discoveryagent-frontend",
            "status": "ok",
        }
        with urllib.request.urlopen(page_url, timeout=2) as response:
            assert response.status == 200
            assert response.read(256)
    finally:
        if process.poll() is None:
            process.terminate()
        stdout, stderr = process.communicate(timeout=10)

    assert process.returncode == 0, stderr
    startup_lines = [
        line for line in stdout.splitlines()
        if line.startswith(STARTUP_PREFIX)
    ]
    assert len(startup_lines) == 1
    startup = json.loads(startup_lines[0][len(STARTUP_PREFIX):])
    assert startup["host"] == DEFAULT_HOST
    assert startup["port"] == port
    assert startup["readiness_url"] == health_url
    assert (data_root / "runtime" / "flask_session").is_dir()
    assert not (launch_directory / ".flask_session").exists()
    assert not (launch_directory / "local_disk").exists()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        assert probe.connect_ex((DEFAULT_HOST, port)) != 0
