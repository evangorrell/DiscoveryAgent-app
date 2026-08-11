"""Frontend subprocess startup protocol, logging, health, and termination."""

from __future__ import annotations

import ipaddress
import json
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TextIO

from .errors import LaunchRuntimeError


STARTUP_PREFIX = "DISCOVERYAGENT_FRONTEND_STARTUP="
ERROR_PREFIX = "DISCOVERYAGENT_FRONTEND_ERROR="
STARTUP_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class FrontendStartup:
    schema_version: int
    pid: int
    host: str
    port: int
    url: str
    readiness_url: str


def _loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _expected_urls(host: str, port: int) -> tuple[str, str]:
    rendered_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    base = f"http://{rendered_host}:{port}/"
    return base, base + "api/health"


def parse_startup_record(line: str, *, expected_pid: int) -> FrontendStartup:
    if not line.startswith(STARTUP_PREFIX):
        raise LaunchRuntimeError("Line is not a frontend startup record")
    try:
        payload = json.loads(line[len(STARTUP_PREFIX) :])
    except json.JSONDecodeError as exc:
        raise LaunchRuntimeError("Frontend emitted malformed startup JSON") from exc
    if not isinstance(payload, dict):
        raise LaunchRuntimeError("Frontend startup record must be a JSON object")
    expected = {"schema_version", "pid", "host", "port", "url", "readiness_url"}
    if set(payload) != expected:
        raise LaunchRuntimeError("Frontend startup record has missing or extra fields")
    if payload["schema_version"] != STARTUP_SCHEMA_VERSION:
        raise LaunchRuntimeError("Unsupported frontend startup-record schema")
    pid = payload["pid"]
    port = payload["port"]
    host = payload["host"]
    url = payload["url"]
    readiness_url = payload["readiness_url"]
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or pid != expected_pid:
        raise LaunchRuntimeError("Frontend startup record has an unexpected PID")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise LaunchRuntimeError("Frontend startup record has an invalid port")
    if not isinstance(host, str) or not _loopback_host(host):
        raise LaunchRuntimeError("Frontend must bind to a loopback host")
    if not isinstance(url, str) or not isinstance(readiness_url, str):
        raise LaunchRuntimeError("Frontend startup URLs must be strings")
    expected_url, expected_readiness = _expected_urls(host, port)
    if url != expected_url or readiness_url != expected_readiness:
        raise LaunchRuntimeError("Frontend startup URLs do not match its host and port")
    return FrontendStartup(
        schema_version=STARTUP_SCHEMA_VERSION,
        pid=pid,
        host=host,
        port=port,
        url=url,
        readiness_url=readiness_url,
    )


def parse_frontend_error(line: str) -> None:
    try:
        payload = json.loads(line[len(ERROR_PREFIX) :])
    except json.JSONDecodeError as exc:
        raise LaunchRuntimeError("Frontend emitted malformed error JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("error"), str):
        raise LaunchRuntimeError("Frontend emitted an invalid error record")
    raise LaunchRuntimeError(
        f"Frontend reported {payload['error']}; see the frontend runtime log"
    )


class FrontendOutputCapture:
    """Continuously drain child output to a bounded log and protocol queue."""

    def __init__(
        self,
        stream: TextIO,
        log_path: Path,
        *,
        secrets: tuple[str, ...] = (),
        maximum_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        self.stream = stream
        self.log_path = log_path
        self.secrets = tuple(secret for secret in secrets if secret)
        self.maximum_bytes = maximum_bytes
        self.protocol_lines: queue.Queue[str] = queue.Queue()
        self._thread = threading.Thread(
            target=self._drain,
            daemon=True,
            name="frontend-output",
        )
        self._thread.start()

    def _redact(self, line: str) -> str:
        for secret in self.secrets:
            line = line.replace(secret, "[REDACTED]")
        return line

    def _rotate(self) -> None:
        previous = self.log_path.with_suffix(self.log_path.suffix + ".1")
        previous.unlink(missing_ok=True)
        if self.log_path.exists():
            os.replace(self.log_path, previous)

    def _drain(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.log_path.open("a", encoding="utf-8")
        try:
            for raw_line in self.stream:
                line = self._redact(raw_line)
                if line.startswith(STARTUP_PREFIX) or line.startswith(ERROR_PREFIX):
                    self.protocol_lines.put(line.rstrip("\r\n"))
                stream.write(line)
                stream.flush()
                if stream.tell() >= self.maximum_bytes:
                    stream.close()
                    self._rotate()
                    stream = self.log_path.open("a", encoding="utf-8")
        finally:
            stream.close()

    def get_protocol_line(self, timeout: float) -> str | None:
        try:
            return self.protocol_lines.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None

    def close(self, timeout: float = 2.0) -> None:
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            try:
                self.stream.close()
            except OSError:
                pass
            self._thread.join(timeout=timeout)


def wait_for_startup(
    process: subprocess.Popen[str],
    output: FrontendOutputCapture,
    *,
    timeout: float = 30.0,
    monotonic: Callable[[], float] = time.monotonic,
) -> FrontendStartup:
    deadline = monotonic() + timeout
    startup: FrontendStartup | None = None
    while monotonic() < deadline:
        line = output.get_protocol_line(min(0.1, max(0.0, deadline - monotonic())))
        if line is not None:
            if line.startswith(ERROR_PREFIX):
                parse_frontend_error(line)
            if line.startswith(STARTUP_PREFIX):
                if startup is not None:
                    raise LaunchRuntimeError("Frontend emitted duplicate startup records")
                startup = parse_startup_record(line, expected_pid=process.pid)
                # Drain records already queued by a misbehaving frontend.
                duplicate = output.get_protocol_line(0.0)
                if duplicate is not None:
                    if duplicate.startswith(ERROR_PREFIX):
                        parse_frontend_error(duplicate)
                    if duplicate.startswith(STARTUP_PREFIX):
                        raise LaunchRuntimeError("Frontend emitted duplicate startup records")
                return startup
        if process.poll() is not None:
            raise LaunchRuntimeError(
                f"Frontend exited before startup with status {process.returncode}"
            )
    raise LaunchRuntimeError("Timed out waiting for frontend startup")


def readiness_is_healthy(
    url: str,
    *,
    opener: Callable[..., object] = urllib.request.urlopen,
    timeout: float = 1.0,
) -> bool:
    try:
        with opener(url, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    return isinstance(payload, dict) and payload == {
        "status": "ok",
        "service": "discoveryagent-frontend",
    }


def wait_for_readiness(
    process: subprocess.Popen[str],
    output: FrontendOutputCapture,
    startup: FrontendStartup,
    *,
    health_probe: Callable[[str], bool] = readiness_is_healthy,
    timeout: float = 30.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        line = output.get_protocol_line(0.0)
        if line is not None:
            if line.startswith(ERROR_PREFIX):
                parse_frontend_error(line)
            if line.startswith(STARTUP_PREFIX):
                raise LaunchRuntimeError("Frontend emitted duplicate startup records")
        if process.poll() is not None:
            raise LaunchRuntimeError(
                f"Frontend exited before readiness with status {process.returncode}"
            )
        if health_probe(startup.readiness_url):
            # A duplicate emitted while the health request ran is still a failure.
            line = output.get_protocol_line(0.0)
            if line is not None and line.startswith(STARTUP_PREFIX):
                raise LaunchRuntimeError("Frontend emitted duplicate startup records")
            return
        sleep(0.1)
    raise LaunchRuntimeError("Timed out waiting for frontend readiness")


def terminate_frontend(
    process: subprocess.Popen[str],
    *,
    platform_name: str,
    timeout: float = 10.0,
) -> None:
    """Terminate one owned frontend; Windows will receive a later adapter."""
    if process.poll() is not None:
        return
    # On macOS Popen.terminate() sends SIGTERM, which the frontend handles.
    # Keeping this boundary isolated avoids POSIX process-group assumptions.
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)
