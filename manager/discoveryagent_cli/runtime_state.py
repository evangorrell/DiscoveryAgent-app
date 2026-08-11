"""Mutable frontend-process metadata and launch serialization."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .errors import LaunchRuntimeError


FRONTEND_RECORD_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class FrontendRuntimeRecord:
    schema_version: int
    pid: int
    active_release_id: str
    url: str
    readiness_url: str
    started_at: float
    browser_opened_at: float | None = None

    @classmethod
    def from_dict(cls, payload: object) -> "FrontendRuntimeRecord":
        if not isinstance(payload, dict):
            raise LaunchRuntimeError("Frontend runtime record must be a JSON object")
        required = {
            "schema_version",
            "pid",
            "active_release_id",
            "url",
            "readiness_url",
            "started_at",
        }
        allowed = required | {"browser_opened_at"}
        if not required.issubset(payload) or not set(payload).issubset(allowed):
            raise LaunchRuntimeError("Frontend runtime record has unsupported fields")
        if payload["schema_version"] != FRONTEND_RECORD_SCHEMA_VERSION:
            raise LaunchRuntimeError("Unsupported frontend runtime-record schema")
        pid = payload["pid"]
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise LaunchRuntimeError("Frontend runtime record has an invalid PID")
        release_id = payload["active_release_id"]
        url = payload["url"]
        readiness_url = payload["readiness_url"]
        started_at = payload["started_at"]
        browser_opened_at = payload.get("browser_opened_at")
        if not isinstance(release_id, str) or not release_id:
            raise LaunchRuntimeError("Frontend runtime record has no release ID")
        if not isinstance(url, str) or not url:
            raise LaunchRuntimeError("Frontend runtime record has no URL")
        if not isinstance(readiness_url, str) or not readiness_url:
            raise LaunchRuntimeError("Frontend runtime record has no readiness URL")
        if not isinstance(started_at, (int, float)) or isinstance(started_at, bool):
            raise LaunchRuntimeError("Frontend runtime record has an invalid start time")
        if "browser_opened_at" in payload and (
            not isinstance(browser_opened_at, (int, float))
            or isinstance(browser_opened_at, bool)
        ):
            raise LaunchRuntimeError(
                "Frontend runtime record has an invalid browser-open time"
            )
        return cls(
            schema_version=FRONTEND_RECORD_SCHEMA_VERSION,
            pid=pid,
            active_release_id=release_id,
            url=url,
            readiness_url=readiness_url,
            started_at=float(started_at),
            browser_opened_at=(
                float(browser_opened_at) if browser_opened_at is not None else None
            ),
        )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "pid": self.pid,
            "active_release_id": self.active_release_id,
            "url": self.url,
            "readiness_url": self.readiness_url,
            "started_at": self.started_at,
        }
        if self.browser_opened_at is not None:
            payload["browser_opened_at"] = self.browser_opened_at
        return payload


def load_frontend_record(path: Path) -> FrontendRuntimeRecord | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise LaunchRuntimeError(f"Could not read frontend runtime record: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LaunchRuntimeError("Frontend runtime record is malformed JSON") from exc
    return FrontendRuntimeRecord.from_dict(payload)


def write_frontend_record_atomic(path: Path, record: FrontendRuntimeRecord) -> None:
    FrontendRuntimeRecord.from_dict(record.to_dict())
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(record.to_dict(), stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def remove_frontend_record(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise LaunchRuntimeError(f"Could not remove stale frontend record: {exc}") from exc


def remove_frontend_record_if_owned(
    path: Path,
    *,
    pid: int,
    active_release_id: str,
) -> None:
    try:
        current = load_frontend_record(path)
    except LaunchRuntimeError:
        return
    if current and current.pid == pid and current.active_release_id == active_release_id:
        remove_frontend_record(path)


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class FrontendLaunchLock:
    """Small cross-platform lock using exclusive file creation."""

    def __init__(
        self,
        path: Path,
        *,
        timeout: float = 45.0,
        stale_after: float = 300.0,
        pid_alive: Callable[[int], bool] = process_is_alive,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.path = path
        self.timeout = timeout
        self.stale_after = stale_after
        self.pid_alive = pid_alive
        self.monotonic = monotonic
        self.wall_time = wall_time
        self.sleep = sleep
        self.token = uuid.uuid4().hex
        self.acquired = False

    def _owner_payload(self) -> dict[str, object] | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _is_stale(self) -> bool:
        payload = self._owner_payload()
        if payload is None:
            try:
                age = self.wall_time() - self.path.stat().st_mtime
            except OSError:
                return False
            return age >= self.stale_after
        pid = payload.get("pid")
        created_at = payload.get("created_at")
        if not isinstance(pid, int) or not isinstance(created_at, (int, float)):
            return True
        if not self.pid_alive(pid):
            return True
        return self.wall_time() - float(created_at) >= self.stale_after

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = self.monotonic() + self.timeout
        payload = json.dumps(
            {"pid": os.getpid(), "created_at": self.wall_time(), "token": self.token},
            sort_keys=True,
        ) + "\n"
        while True:
            try:
                descriptor = os.open(
                    self.path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                if self._is_stale():
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                if self.monotonic() >= deadline:
                    raise LaunchRuntimeError(
                        "Another DiscoveryAgent launch is still starting"
                    )
                self.sleep(0.1)
                continue
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception:
                self.path.unlink(missing_ok=True)
                raise
            self.acquired = True
            return

    def release(self) -> None:
        if not self.acquired:
            return
        payload = self._owner_payload()
        if payload and payload.get("token") == self.token:
            self.path.unlink(missing_ok=True)
        self.acquired = False

    def __enter__(self) -> "FrontendLaunchLock":
        self.acquire()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()
