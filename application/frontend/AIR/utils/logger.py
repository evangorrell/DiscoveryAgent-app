from AIR.utils.scientific_audit import AuditTrail, AuditLoggerMixin, RedactingFilter
import os
import datetime
import logging
import sys
from logging.handlers import RotatingFileHandler
from threading import Lock, RLock


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

    def __init__(self, analysis_name: str = "ai_researcher", log_dir: str = "logs"):
        self.audit_trail = AuditTrail(log_dir, analysis_name)
        # Ensure log directory exists
        if not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        # Central log filename (shared)
        self.log_file = os.path.join(log_dir, "ai_researcher.log")

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

                # Also add a short console handler so logs still appear in console when running interactively
                console_handler = logging.StreamHandler(sys.__stdout__)
                console_handler.setFormatter(formatter)
                # Avoid adding a console handler in non-interactive contexts could be fine, but keep it consistent
                self.logger.addHandler(console_handler)

                # Redirect stdout and stderr so existing print() calls are captured
                sys.stdout = StreamToLogger(self.logger.info, original_stream=None)
                sys.stderr = StreamToLogger(self.logger.error, original_stream=None)

                # Write a run header
                header = "\n" + "=" * 80 + f"\nRUN START: {datetime.datetime.now().isoformat()} - analysis: {analysis_name}\n" + "=" * 80
                self.logger.info(header)

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
