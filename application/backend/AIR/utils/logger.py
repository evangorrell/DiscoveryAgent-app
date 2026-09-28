from AIR.utils.scientific_audit import AuditTrail, AuditLoggerMixin, RedactingFilter
import os
import datetime
import logging
import re
import socket
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from threading import Lock, RLock


class ActionLogFilter(logging.Filter):
    """Allow only explicit action events into the action log file."""

    _EXCLUDED_ACTIONS = {
        "generated_reviewer_prompt",
        "system_prompt",
    }
    _ACTION_NAME_RE = re.compile(r"^\[ACTION\]\s+([^\s]+)\s+-")

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:
            return False
        if not (isinstance(message, str) and message.startswith("[ACTION]")):
            return False

        match = self._ACTION_NAME_RE.match(message)
        if not match:
            return False

        action_name = match.group(1)
        if action_name in self._EXCLUDED_ACTIONS:
            return False
        if "prompt" in action_name or action_name.endswith("_query_input"):
            return False
        return True


class StreamToLogger:
    """Redirect writes to a logger instance while optionally also writing to the original stream.

    This lets existing print() calls continue to emit to the console while also being captured
    into the centralized log file for post-hoc debugging and searching.
    """
    def __init__(self, logger_func, original_stream=None):
        self.logger_func = logger_func
        self.original_stream = original_stream
        self._buffer = ""
        self._lock = RLock()  # Use RLock to allow reentrant logging calls

    def write(self, message):
        # Buffer until newline to avoid fragmented log lines
        with self._lock:
            self._buffer += message
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                if line.strip():
                    try:
                        self.logger_func(line)
                    except Exception:
                        pass
                if self.original_stream:
                    try:
                        self.original_stream.write(line + "\n")
                    except Exception:
                        pass

    def flush(self):
        with self._lock:
            if self._buffer.strip():
                try:
                    self.logger_func(self._buffer.strip())
                except Exception:
                    pass
                if self.original_stream:
                    try:
                        self.original_stream.write(self._buffer)
                    except Exception:
                        pass
            self._buffer = ""


class Logger(AuditLoggerMixin):
    """Centralized logger for the AIR codebase.

    Writes structured entries to a single rotating log file located at
    <log_dir>/ai_researcher.log. Also redirects stdout/stderr to the
    centralized logger so existing `print()` calls are captured without
    having to modify every module.
    """

    # Module-level shared logger initialization flag and lock
    _initialized = False
    _init_lock = Lock()
    _round_handler: "logging.FileHandler | None" = None
    _round_log_path: "Path | None" = None
    _round_lock = Lock()

    def __init__(self, analysis_name: str = "ai_researcher", log_dir: str = "logs"):
        # Standalone callers historically supply relative run directories.
        # Resolve that configured destination, never fall back to another sink.
        log_dir = str(Path(log_dir).expanduser().resolve())
        self.audit_trail = AuditTrail(log_dir, analysis_name)
        # Ensure log directory exists
        if not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        # Central log filename (shared)
        self.log_file = os.path.join(log_dir, "ai_researcher.log")
        self.action_log_file = os.path.join(log_dir, "ai_researcher_actions.log")

        # Create or get the shared logger
        self.logger = logging.getLogger("ai_researcher")
        self.logger.setLevel(logging.INFO)
        if not any(isinstance(f, RedactingFilter) for f in self.logger.filters):
            self.logger.addFilter(RedactingFilter())
        # Prevent propagation to root logger to avoid deadlock with libraries
        # that configure the root logger with handlers using sys.stdout (e.g., RichHandler)
        self.logger.propagate = False

        # Make initialization thread-safe and idempotent
        with Logger._init_lock:
            if not Logger._initialized:
                # Clear existing handlers to avoid duplicate logs in long-running processes
                if self.logger.handlers:
                    for h in list(self.logger.handlers):
                        try:
                            self.logger.removeHandler(h)
                        except Exception:
                            pass

                # Rotating file handler
                file_handler = RotatingFileHandler(
                    self.log_file,
                    maxBytes=50 * 1024 * 1024,
                    backupCount=5,
                    encoding="utf-8",
                )

                # Clean and searchable formatter
                formatter = logging.Formatter(
                    "%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
                )
                file_handler.setFormatter(formatter)
                self.logger.addHandler(file_handler)

                # Action-only rotating file handler for high-level debugging
                action_file_handler = RotatingFileHandler(
                    self.action_log_file,
                    maxBytes=20 * 1024 * 1024,
                    backupCount=5,
                    encoding="utf-8",
                )
                action_file_handler.setFormatter(formatter)
                action_file_handler.addFilter(ActionLogFilter())
                self.logger.addHandler(action_file_handler)

                # Also add a short console handler so logs still appear in console when running interactively
                console_handler = logging.StreamHandler(sys.__stdout__)
                console_handler.setFormatter(formatter)
                # Avoid adding a console handler in non-interactive contexts could be fine, but keep it consistent
                self.logger.addHandler(console_handler)

                # Redirect stdout and stderr so existing print() calls are captured.
                # original_stream is intentionally None: the console_handler above already
                # writes to sys.__stdout__, so passing it here would double-emit every line
                # (once formatted via the handler, once raw via original_stream.write).
                sys.stdout = StreamToLogger(self.logger.info, original_stream=None)
                sys.stderr = StreamToLogger(self.logger.error, original_stream=None)

                # Write a run header
                header = "\n" + "=" * 80 + f"\nRUN START: {datetime.datetime.now().isoformat()} - analysis: {analysis_name}\n" + "=" * 80
                self.logger.info(header)
                self.logger.info(
                    f"[ACTION] action_log_initialized - {analysis_name}\npath={self.action_log_file}"
                )

                Logger._initialized = True

        # Keep analysis_name for contextual log entries
        self.analysis_name = analysis_name

    def format_traceback(self, error_name, error_value, traceback):
        return f"ERROR: {error_name}: {error_value}\n\n{traceback}"

    def log_analysis_start(self, analysis_idx, hypothesis=None):
        self.log_action("analysis_start", f"analysis_idx={analysis_idx}\nhypothesis={hypothesis}")

    def log_analysis_end(self, analysis_idx, status, notebook_path=None):
        details = f"analysis_idx={analysis_idx}\nstatus={status}"
        if notebook_path:
            details += f"\nnotebook_path={notebook_path}"
        self.log_action("analysis_end", details)

    def log_iteration(self, analysis_idx, iteration_idx):
        self.log_action("iteration", f"analysis_idx={analysis_idx}\niteration_idx={iteration_idx}")

    def get_log_path(self) -> str:
        return self.log_file

    def get_action_log_path(self) -> str:
        return self.action_log_file

    def attach_round_log_handler(self, round_dir, resume_marker: bool = True) -> None:
        """Attach a per-round FileHandler that writes live_round.log inside round_dir."""
        round_dir = Path(round_dir)
        self.audit_trail.context["round_dir"] = str(round_dir)
        round_dir.mkdir(parents=True, exist_ok=True)
        log_path = round_dir / "live_round.log"
        with Logger._round_lock:
            if Logger._round_handler is not None:
                try:
                    self.logger.removeHandler(Logger._round_handler)
                    Logger._round_handler.close()
                except Exception:
                    pass
            handler = logging.FileHandler(str(log_path), mode="a", encoding="utf-8")
            formatter = logging.Formatter(
                "%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            Logger._round_handler = handler
            Logger._round_log_path = log_path
        if resume_marker and log_path.exists() and log_path.stat().st_size > 0:
            self.logger.info(f"[ROUND_LOG] resumed host={socket.gethostname()} path={log_path}")
        else:
            self.logger.info(f"[ROUND_LOG] started host={socket.gethostname()} path={log_path}")

    def detach_round_log_handler(self) -> None:
        """Detach and flush the per-round FileHandler."""
        with Logger._round_lock:
            if Logger._round_handler is None:
                return
            try:
                self.logger.info(f"[ROUND_LOG] closing path={Logger._round_log_path}")
                Logger._round_handler.flush()
                self.logger.removeHandler(Logger._round_handler)
                Logger._round_handler.close()
            except Exception:
                pass
            Logger._round_handler = None
            Logger._round_log_path = None
