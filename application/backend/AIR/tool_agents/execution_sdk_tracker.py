"""ExecutionAgent SDK tracking and its public, sanitized per-query log.

The SDK hooks are the authoritative boundary for observable ExecutionAgent tool
events.  The public log intentionally excludes the application's process-wide
stdout/log stream, which also contains Director and launcher output.
"""

from __future__ import annotations

import json
import os
import re
import stat
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from AIR.utils.query_artifacts import execution_log_filename, query_directory
from AIR.utils.scientific_audit import (
    known_secret_values as _known_secret_values, sanitize_text, sanitize_record,
)

try:  # POSIX is the supported local runtime; keep imports testable elsewhere.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


EXECUTION_LOG_MAX_BYTES = 8 * 1024 * 1024
EXECUTION_LOG_TRUNCATION_MARKER = "\n[Execution log truncated at the 8 MiB persisted limit.]\n"
def sanitize_execution_output(value: Any, *, known_secrets: tuple[str, ...] = ()) -> str:
    """Use the same credential redactor as scientific audit records."""
    return sanitize_text(value, known_secrets)


class ExecutionLogWriter:
    """Serialized, size-bounded appends to one fixed canonical query artifact."""

    def __init__(self, query_dir: Path, query_num: int, *, environment=None):
        expected_dir = query_directory(query_dir.parent, query_num).absolute()
        supplied_dir = query_dir.expanduser().absolute()
        if supplied_dir != expected_dir:
            raise ValueError("execution log directory does not match its query identity")
        supplied_dir.mkdir(parents=True, exist_ok=True)
        if supplied_dir.is_symlink() or not supplied_dir.is_dir():
            raise ValueError("execution log parent must be a real directory")
        self.path = supplied_dir / execution_log_filename(query_num)
        self._lock = threading.RLock()
        self._known_secrets = _known_secret_values(environment)
        self._pending_secret_fragment = ""
        self.truncated = False
        existed = self.path.exists() or self.path.is_symlink()
        if existed:
            if self.path.is_symlink() or not self.path.is_file():
                raise ValueError("execution log target must be a regular file")
        else:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        boundary = "Execution resumed" if existed and self.path.stat().st_size else "Execution started"
        self.append(f"\n=== {boundary} @ {datetime.now().isoformat()} | query={query_num} ===\n")

    def append(self, value: Any) -> None:
        with self._lock:
            self._append_locked(value)

    def _append_locked(self, value: Any) -> None:
        raw = self._pending_secret_fragment + str(value)
        hold = 0
        for secret in self._known_secrets:
            upper = min(len(raw), len(secret) - 1)
            for length in range(upper, 0, -1):
                if raw.endswith(secret[:length]):
                    hold = max(hold, length)
                    break
        self._pending_secret_fragment = raw[-hold:] if hold else ""
        ready = raw[:-hold] if hold else raw
        sanitized = sanitize_execution_output(ready, known_secrets=self._known_secrets)
        if not sanitized:
            return
        encoded = sanitized.encode("utf-8")
        marker = EXECUTION_LOG_TRUNCATION_MARKER.encode("utf-8")
        with open(self.path, "ab", buffering=0) as stream:
            if fcntl is not None:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                current = os.fstat(stream.fileno()).st_size
                if current >= EXECUTION_LOG_MAX_BYTES or self.truncated:
                    self.truncated = True
                    return
                content_limit = EXECUTION_LOG_MAX_BYTES - len(marker)
                payload_room = max(0, content_limit - current)
                if len(encoded) > payload_room:
                    safe_prefix = encoded[:payload_room].decode("utf-8", errors="ignore").encode("utf-8")
                    stream.write(safe_prefix)
                    if current + len(safe_prefix) + len(marker) <= EXECUTION_LOG_MAX_BYTES:
                        stream.write(marker)
                    self.truncated = True
                else:
                    stream.write(encoded)
                os.fsync(stream.fileno())
            finally:
                if fcntl is not None:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def flush(self) -> None:
        """Persist a possible final secret-prefix fragment at a terminal boundary."""
        with self._lock:
            pending = self._pending_secret_fragment
            self._pending_secret_fragment = ""
            if pending:
                self._append_locked("[REDACTED]")



class ExecutionSDKTracker:
    """Tracks Claude SDK actions and writes them into a query checkpoint directory."""

    def __init__(self, logger=None):
        self.logger = logger
        self.query_log_dir: Optional[Path] = None
        self.tool_log_file = None
        self.message_log_file = None
        self.transcript_file = None
        self.execution_log: Optional[ExecutionLogWriter] = None
        self._tool_records: Dict[str, Dict[str, Any]] = {}
        self._current_phase = "analysis"
        self._current_query_num: Optional[int] = None

    def set_query_log_dir(
        self,
        checkpoint_query_dir: str,
        query_num: Optional[int] = None,
        log_subdir_name: Optional[str] = "execution_agent",
    ):
        """
        Set and initialize the output directory for the current query.

        Args:
            checkpoint_query_dir: Path to checkpoints/.../query_YYY
            query_num: Optional query number for context in records
        """
        query_dir = Path(checkpoint_query_dir).expanduser().absolute()
        expected_public_path = query_dir / execution_log_filename(query_num)
        if self.execution_log is not None and self.execution_log.path == expected_public_path:
            return
        self.close()
        query_dir.mkdir(parents=True, exist_ok=True)
        if log_subdir_name:
            self.query_log_dir = query_dir / log_subdir_name
        else:
            self.query_log_dir = query_dir
        self.query_log_dir.mkdir(parents=True, exist_ok=True)
        self._current_query_num = query_num
        self.execution_log = ExecutionLogWriter(query_dir, query_num)

        # Use append mode so retries/re-entries in the same query keep a single log.
        self.tool_log_file = open(self.query_log_dir / "tool_calls.jsonl", "a", encoding="utf-8")
        self.message_log_file = open(self.query_log_dir / "messages.jsonl", "a", encoding="utf-8")
        self.transcript_file = open(self.query_log_dir / "transcript.txt", "a", encoding="utf-8")

        self._write_transcript_line(
            f"\n=== Session start @ {datetime.now().isoformat()} | query={query_num} ==="
        )

    def set_phase(self, phase: str):
        self._current_phase = phase

    def _safe_json_dumps(self, payload: Dict[str, Any]) -> str:
        secrets = self.execution_log._known_secrets if self.execution_log else _known_secret_values()
        return json.dumps(sanitize_record(payload, secrets), ensure_ascii=False)

    def _write_jsonl(self, fp, payload: Dict[str, Any]):
        if fp is None:
            return
        fp.write(self._safe_json_dumps(payload) + "\n")
        fp.flush()

    def _write_transcript_line(self, line: str):
        if self.transcript_file is None:
            return
        secrets = self.execution_log._known_secrets if self.execution_log else _known_secret_values()
        self.transcript_file.write(
            sanitize_execution_output(line, known_secrets=secrets) + "\n"
        )
        self.transcript_file.flush()

    def log_observable(self, message: Any) -> None:
        """Append output already suitable for the public ExecutionAgent display."""
        if self.execution_log is not None:
            self.execution_log.append(f"{message}\n")

    def log_skill_configuration(self, frozen_paths: list[str], skill_names: list[str]) -> None:
        """Record configured metadata without persisting skill contents."""
        timestamp = datetime.now().isoformat()
        self._write_jsonl(
            self.message_log_file,
            {
                "event": "execution_skills_configured",
                "timestamp": timestamp,
                "phase": self._current_phase,
                "query_num": self._current_query_num,
                "frozen_paths": list(frozen_paths),
                "skill_names": list(skill_names),
            },
        )
        self._write_transcript_line(
            f"[{timestamp}] SKILLS CONFIGURED: {', '.join(skill_names) or 'none'}"
        )
        self.log_observable(
            f"Configured execution skills: {', '.join(skill_names) or 'none'}"
        )

    def log_skill_discovery(self, runtime_names: list[str]) -> None:
        """Record the CLI init event confirming runtime discovery."""
        timestamp = datetime.now().isoformat()
        self._write_jsonl(
            self.message_log_file,
            {
                "event": "execution_skills_runtime_discovered",
                "timestamp": timestamp,
                "phase": self._current_phase,
                "query_num": self._current_query_num,
                "runtime_names": list(runtime_names),
            },
        )
        self._write_transcript_line(
            f"[{timestamp}] SKILLS DISCOVERED: {', '.join(runtime_names)}"
        )
        self.log_observable(f"Execution skill discovery confirmed: {', '.join(runtime_names)}")

    def log_execution_skill_event(
        self, event: str, runtime_name: str, context_name: str, reason: str | None = None,
    ) -> None:
        """Record an authoritative required-skill lifecycle event."""
        allowed_events = {
            "execution_skill_requested",
            "execution_skill_loaded",
            "execution_skill_failed",
        }
        if event not in allowed_events:
            raise ValueError(f"Unsupported execution skill event: {event}")
        timestamp = datetime.now().isoformat()
        payload = {
            "event": event,
            "timestamp": timestamp,
            "phase": self._current_phase,
            "query_num": self._current_query_num,
            "runtime_name": runtime_name,
            "context": context_name,
        }
        if reason:
            payload["reason"] = reason
        self._write_jsonl(self.message_log_file, payload)
        detail = f"{event}: {runtime_name}"
        if context_name != "primary":
            detail += f" (context={context_name})"
        self._write_transcript_line(f"[{timestamp}] {detail}")
        self.log_observable(detail)

    def log_missing_required_skill_load(
        self, runtime_names: list[str], context_name: str, attempt: int,
    ) -> None:
        """Record that configured discovery has not produced required loads."""
        timestamp = datetime.now().isoformat()
        names = list(runtime_names)
        self._write_jsonl(
            self.message_log_file,
            {
                "event": "execution_skills_missing_required_load",
                "timestamp": timestamp,
                "phase": self._current_phase,
                "query_num": self._current_query_num,
                "runtime_names": names,
                "context": context_name,
                "attempt": attempt,
            },
        )
        detail = (
            "execution_skills_missing_required_load: "
            f"{', '.join(names)} (context={context_name}, attempt={attempt})"
        )
        self._write_transcript_line(f"[{timestamp}] {detail}")
        self.log_observable(detail)

    @staticmethod
    def _compact_observable(value: Any, limit: int = 64 * 1024) -> str:
        if isinstance(value, (dict, list, tuple)):
            rendered = json.dumps(value, ensure_ascii=False, default=str)
        else:
            rendered = str(value)
        if len(rendered) > limit:
            return rendered[:limit] + "\n[Tool event shortened for display.]"
        return rendered

    async def pre_tool_use_hook(self, hook_input, tool_use_id, context):
        tool_name = hook_input.get("tool_name") if isinstance(hook_input, dict) else "unknown"
        tool_input = hook_input.get("tool_input") if isinstance(hook_input, dict) else None
        timestamp = datetime.now().isoformat()

        self._tool_records[tool_use_id] = {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "timestamp_start": timestamp,
            "phase": self._current_phase,
            "query_num": self._current_query_num,
        }

        self._write_transcript_line(f"[{timestamp}] TOOL START ({self._current_phase}) -> {tool_name}")
        self.log_observable(f"[{timestamp}] Tool start ({self._current_phase}): {tool_name}")
        if tool_input is not None and tool_input != "" and tool_input != {}:
            self.log_observable(f"Input: {self._compact_observable(tool_input)}")

        self._write_jsonl(
            self.tool_log_file,
            {
                "event": "tool_call_start",
                "timestamp": timestamp,
                "tool_use_id": tool_use_id,
                "phase": self._current_phase,
                "query_num": self._current_query_num,
                "tool_name": tool_name,
                "tool_input": tool_input,
            },
        )
        return {"continue_": True}

    async def post_tool_use_hook(self, hook_input, tool_use_id, context):
        tool_response = hook_input.get("tool_response") if isinstance(hook_input, dict) else None
        timestamp = datetime.now().isoformat()
        record = self._tool_records.get(tool_use_id, {})
        tool_name = record.get("tool_name", "unknown")
        error = None

        if isinstance(tool_response, dict) and "error" in tool_response:
            error = tool_response.get("error")
        if isinstance(tool_response, dict) and (
            tool_response.get("is_error") is True
            or tool_response.get("success") is False
        ):
            error = error or "tool reported failure"

        self._write_transcript_line(
            f"[{timestamp}] TOOL END   ({self._current_phase}) -> {tool_name} | success={error is None}"
        )
        if tool_response is not None:
            if isinstance(tool_response, dict):
                emitted = False
                for key, label in (("stdout", "stdout"), ("stderr", "stderr"), ("output", "output"), ("content", "output"), ("error", "error")):
                    if key in tool_response and tool_response[key] is not None and tool_response[key] != "":
                        self.log_observable(f"{label}: {self._compact_observable(tool_response[key])}")
                        emitted = True
                if not emitted:
                    self.log_observable(f"output: {self._compact_observable(tool_response)}")
            else:
                self.log_observable(f"output: {self._compact_observable(tool_response)}")
        self.log_observable(
            f"[{timestamp}] Tool end ({self._current_phase}): {tool_name} | success={error is None}"
        )

        self._write_jsonl(
            self.tool_log_file,
            {
                "event": "tool_call_complete",
                "timestamp": timestamp,
                "tool_use_id": tool_use_id,
                "phase": self._current_phase,
                "query_num": self._current_query_num,
                "tool_name": tool_name,
                "success": error is None,
                "error": error,
                "output_size": len(str(tool_response)) if tool_response is not None else 0,
                "output": tool_response,
            },
        )

        self._tool_records.pop(tool_use_id, None)
        return {"continue_": True}

    async def post_tool_use_failure_hook(self, hook_input, tool_use_id, context):
        """Record SDK tool failures delivered through PostToolUseFailure."""
        timestamp = datetime.now().isoformat()
        record = self._tool_records.get(tool_use_id, {})
        tool_name = record.get("tool_name", "unknown")
        error = hook_input.get("error") if isinstance(hook_input, dict) else None
        error = str(error or "tool execution failed")

        self._write_transcript_line(
            f"[{timestamp}] TOOL END   ({self._current_phase}) -> {tool_name} | success=False"
        )
        self.log_observable(f"error: {self._compact_observable(error)}")
        self.log_observable(
            f"[{timestamp}] Tool end ({self._current_phase}): {tool_name} | success=False"
        )
        self._write_jsonl(
            self.tool_log_file,
            {
                "event": "tool_call_complete",
                "timestamp": timestamp,
                "tool_use_id": tool_use_id,
                "phase": self._current_phase,
                "query_num": self._current_query_num,
                "tool_name": tool_name,
                "success": False,
                "error": error,
                "output_size": 0,
                "output": None,
            },
        )
        self._tool_records.pop(tool_use_id, None)
        return {"continue_": True}


    def log_stream_message(self, message: Any, phase: Optional[str] = None):
        active_phase = phase or self._current_phase
        timestamp = datetime.now().isoformat()
        message_type = type(message).__name__
        message_str = str(message)

        self._write_jsonl(
            self.message_log_file,
            {
                "event": "sdk_message",
                "timestamp": timestamp,
                "phase": active_phase,
                "query_num": self._current_query_num,
                "message_type": message_type,
                "message": message_str,
            },
        )

        # Keep transcript readable while retaining complete details in JSONL.
        compact = message_str.replace("\n", " ")
        if len(compact) > 200:
            compact = compact[:200] + "..."
        self._write_transcript_line(f"[{timestamp}] MSG ({active_phase}) {message_type}: {compact}")

    def close(self):
        for fp in [self.tool_log_file, self.message_log_file, self.transcript_file]:
            try:
                if fp is not None and not fp.closed:
                    fp.close()
            except Exception:
                pass
        self.tool_log_file = None
        self.message_log_file = None
        self.transcript_file = None
        if self.execution_log is not None:
            self.execution_log.flush()
        self.execution_log = None
