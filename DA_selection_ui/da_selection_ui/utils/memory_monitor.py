"""
Memory monitoring utilities for debugging OOM issues.

Usage:
    from da_selection_ui.utils.memory_monitor import (
        get_memory_mb,
        log_memory,
        memory_guard,
        MemoryTracker,
    )

    # Simple logging
    log_memory("before expensive operation")

    # Context manager for tracking
    with MemoryTracker("report_chat"):
        do_expensive_thing()

    # Decorator with optional threshold warning
    @memory_guard(warn_threshold_mb=400)
    def my_endpoint():
        ...
"""

import functools
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Callable, Optional

# Try to use psutil for accurate memory measurement, fall back to resource module
try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False
    try:
        import resource
        _HAS_RESOURCE = True
    except ImportError:
        _HAS_RESOURCE = False


def get_memory_mb() -> float:
    """
    Get current process memory usage in MB.

    Returns RSS (Resident Set Size) which is the actual physical memory used.
    """
    if _HAS_PSUTIL:
        process = psutil.Process(os.getpid())
        return process.memory_info().rss / (1024 * 1024)
    elif _HAS_RESOURCE:
        # resource.getrusage returns memory in KB on Linux, bytes on macOS
        usage = resource.getrusage(resource.RUSAGE_SELF)
        if sys.platform == 'darwin':
            return usage.ru_maxrss / (1024 * 1024)  # bytes to MB
        else:
            return usage.ru_maxrss / 1024  # KB to MB
    else:
        return -1.0  # Unable to measure


def get_memory_info() -> dict:
    """
    Get detailed memory information.

    Returns a dict with:
        - rss_mb: Resident Set Size in MB (physical memory)
        - vms_mb: Virtual Memory Size in MB (total allocated)
        - percent: Percentage of system memory used by this process
        - available_mb: Available system memory in MB
    """
    info = {
        'rss_mb': -1.0,
        'vms_mb': -1.0,
        'percent': -1.0,
        'available_mb': -1.0,
        'timestamp': datetime.utcnow().isoformat() + 'Z',
    }

    if _HAS_PSUTIL:
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        info['rss_mb'] = mem_info.rss / (1024 * 1024)
        info['vms_mb'] = mem_info.vms / (1024 * 1024)
        info['percent'] = process.memory_percent()

        try:
            sys_mem = psutil.virtual_memory()
            info['available_mb'] = sys_mem.available / (1024 * 1024)
        except Exception:
            pass
    else:
        info['rss_mb'] = get_memory_mb()

    return info


def log_memory(label: str, level: str = "info") -> float:
    """
    Log current memory usage with a descriptive label.

    Args:
        label: Description of what's happening (e.g., "before s3 sync")
        level: Log level - "info", "warn", or "error"

    Returns:
        Current memory usage in MB
    """
    mem_mb = get_memory_mb()
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    prefix = {
        "info": "[MEM]",
        "warn": "[MEM WARNING]",
        "error": "[MEM ERROR]",
    }.get(level, "[MEM]")

    if mem_mb >= 0:
        print(f"{prefix} {timestamp} | {mem_mb:.1f} MB | {label}")
    else:
        print(f"{prefix} {timestamp} | (unavailable) | {label}")

    return mem_mb


def check_memory_threshold(threshold_mb: float = 450.0) -> tuple[bool, float]:
    """
    Check if memory usage is approaching a dangerous threshold.

    Args:
        threshold_mb: Memory threshold in MB (default 450 for 512MB limit)

    Returns:
        Tuple of (is_above_threshold, current_memory_mb)
    """
    mem_mb = get_memory_mb()
    return mem_mb >= threshold_mb, mem_mb


class MemoryTracker:
    """
    Context manager for tracking memory usage during an operation.

    Usage:
        with MemoryTracker("expensive_operation") as tracker:
            do_something()
        # Automatically logs start, end, and delta

    Or access values directly:
        with MemoryTracker("op", log=False) as tracker:
            do_something()
        print(f"Used {tracker.delta_mb} MB")
    """

    def __init__(
        self,
        label: str,
        log: bool = True,
        warn_threshold_mb: Optional[float] = 400.0,
    ):
        self.label = label
        self.log = log
        self.warn_threshold_mb = warn_threshold_mb
        self.start_mb: float = 0.0
        self.end_mb: float = 0.0
        self.delta_mb: float = 0.0
        self.start_time: float = 0.0
        self.elapsed_sec: float = 0.0

    def __enter__(self):
        self.start_mb = get_memory_mb()
        self.start_time = time.time()
        if self.log:
            log_memory(f"START {self.label}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_mb = get_memory_mb()
        self.delta_mb = self.end_mb - self.start_mb
        self.elapsed_sec = time.time() - self.start_time

        if self.log:
            level = "info"
            if self.warn_threshold_mb and self.end_mb >= self.warn_threshold_mb:
                level = "warn"

            delta_str = f"+{self.delta_mb:.1f}" if self.delta_mb >= 0 else f"{self.delta_mb:.1f}"
            log_memory(
                f"END {self.label} | delta={delta_str} MB | elapsed={self.elapsed_sec:.2f}s",
                level=level,
            )

        return False  # Don't suppress exceptions


def memory_guard(
    warn_threshold_mb: float = 400.0,
    abort_threshold_mb: Optional[float] = None,
    label: Optional[str] = None,
) -> Callable:
    """
    Decorator to monitor memory usage around a function.

    Args:
        warn_threshold_mb: Log warning if memory exceeds this after execution
        abort_threshold_mb: Raise MemoryError if memory exceeds this BEFORE execution
                           (useful to prevent starting expensive operations when low on memory)
        label: Custom label for logs (defaults to function name)

    Usage:
        @memory_guard(warn_threshold_mb=400, abort_threshold_mb=450)
        def expensive_endpoint():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            func_label = label or func.__name__

            # Pre-execution check
            if abort_threshold_mb is not None:
                current_mb = get_memory_mb()
                if current_mb >= abort_threshold_mb:
                    log_memory(
                        f"ABORT {func_label} - memory too high to start safely",
                        level="error",
                    )
                    raise MemoryError(
                        f"Memory usage ({current_mb:.1f} MB) exceeds safe threshold "
                        f"({abort_threshold_mb:.1f} MB) - aborting {func_label}"
                    )

            # Execute with tracking
            with MemoryTracker(func_label, warn_threshold_mb=warn_threshold_mb):
                return func(*args, **kwargs)

        return wrapper
    return decorator


@contextmanager
def memory_checkpoint(label: str):
    """
    Simple context manager that just logs memory at entry and exit.

    Usage:
        with memory_checkpoint("loading data"):
            data = load_large_file()
    """
    log_memory(f">>> {label}")
    try:
        yield
    finally:
        log_memory(f"<<< {label}")


# Utility for periodic memory logging (useful for long-running operations)
class PeriodicMemoryLogger:
    """
    Logs memory usage periodically during long operations.

    Usage:
        logger = PeriodicMemoryLogger("sync_operation", interval_sec=5)
        logger.start()
        try:
            do_long_operation()
        finally:
            logger.stop()
    """

    def __init__(self, label: str, interval_sec: float = 10.0):
        self.label = label
        self.interval_sec = interval_sec
        self._stop = False
        self._thread = None

    def start(self):
        import threading
        self._stop = False
        self._thread = threading.Thread(target=self._log_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True
        if self._thread:
            self._thread.join(timeout=1.0)

    def _log_loop(self):
        count = 0
        while not self._stop:
            count += 1
            log_memory(f"{self.label} [tick {count}]")
            time.sleep(self.interval_sec)
