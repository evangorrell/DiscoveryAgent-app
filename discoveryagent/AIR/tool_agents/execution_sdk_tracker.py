"""
ExecutionAgent SDK tracker for per-query audit logging.

Captures:
- PreToolUse/PostToolUse hook events
- Full SDK streamed messages
- Human-readable transcript lines
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


class ExecutionSDKTracker:
    """Tracks Claude SDK actions and writes them into a query checkpoint directory."""

    def __init__(self, logger=None):
        self.logger = logger
        self.query_log_dir: Optional[Path] = None
        self.tool_log_file = None
        self.message_log_file = None
        self.transcript_file = None
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
        self.close()
        query_dir = Path(checkpoint_query_dir)
        query_dir.mkdir(parents=True, exist_ok=True)
        if log_subdir_name:
            self.query_log_dir = query_dir / log_subdir_name
        else:
            self.query_log_dir = query_dir
        self.query_log_dir.mkdir(parents=True, exist_ok=True)
        self._current_query_num = query_num

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
        return json.dumps(payload, ensure_ascii=False, default=str)

    def _write_jsonl(self, fp, payload: Dict[str, Any]):
        if fp is None:
            return
        fp.write(self._safe_json_dumps(payload) + "\n")
        fp.flush()

    def _write_transcript_line(self, line: str):
        if self.transcript_file is None:
            return
        self.transcript_file.write(line + "\n")
        self.transcript_file.flush()

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

        self._write_transcript_line(
            f"[{timestamp}] TOOL END   ({self._current_phase}) -> {tool_name} | success={error is None}"
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

