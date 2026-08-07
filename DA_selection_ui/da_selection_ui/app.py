#!/usr/bin/env python3
"""
Web UI for Interactive Hypothesis Generation and Selection Tool

This Flask application provides a thin API layer over the CLI script's
InteractiveHypothesisGenerator class. All logic lives in the CLI script.
"""

import argparse
from collections import deque
import concurrent.futures
import copy
import hashlib
import hmac
import json
import os
import re
import shutil
import sys
import signal
import time
import threading
import uuid
import urllib.error
import urllib.request
import webbrowser
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta
from datetime import timezone
from functools import wraps
from pathlib import Path, PurePosixPath
from time import perf_counter

from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory, abort, make_response, g, has_request_context
from markupsafe import Markup
from flask_session import Session
from sqlalchemy import func

boto3 = None
Config = None
TransferConfig = None

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency
    load_dotenv = None

if load_dotenv:
    load_dotenv()

from .db import (
    db_enabled,
    init_db,
    db_session_scope,
    ProjectKey,
    ProjectProgress,
    RegistrationSubmission,
    EventLog,
)
from . import credential_store
from .utils.memory_monitor import (
    get_memory_mb,
    get_memory_info,
    log_memory,
    MemoryTracker,
    check_memory_threshold,
)

try:
    # Prefer package-relative imports when installed
    from .select_hypothesis import InteractiveHypothesisGenerator, display_card_title
    from . import hypothesis_history
except Exception:
    # Fallback for running as a loose script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from select_hypothesis import InteractiveHypothesisGenerator, display_card_title
    import hypothesis_history

from AIR.utils.code_chat import CodeChatConversation, run_code_chat

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24).hex())
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_DIR'] = './.flask_session/'
Session(app)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
if os.environ.get('DISCOVERY_AGENT_SESSION_COOKIE_SECURE', '0') == '1':
    app.config['SESSION_COOKIE_SECURE'] = True
SESSION_LIFETIME_HOURS = max(1, int(os.environ.get('DISCOVERY_AGENT_SESSION_LIFETIME_HOURS', '8')))
app.permanent_session_lifetime = timedelta(hours=SESSION_LIFETIME_HOURS)


def _clear_session_cookie(response):
    """Ensure client-side session cookies are cleared (forces new session on next login)."""
    cookie_name = app.config.get('SESSION_COOKIE_NAME', 'session')
    response.delete_cookie(cookie_name)
    return response
application = app

DB_AVAILABLE = db_enabled()
if DB_AVAILABLE:
    try:
        init_db()
    except Exception as exc:
        DB_AVAILABLE = False
        print(f"⚠️  Failed to initialize database: {exc}")

# Global storage for session generators (in production, use Redis or database)
GENERATORS = {}
GENERATION_STATE = {}
REGENERATION_STATE = {}
GENERATION_LOCK = threading.Lock()
REPORT_CHAT_CANCEL_STATE = {}
REPORT_CHAT_CANCEL_LOCK = threading.Lock()
HYPOTHESIS_THREAD_CANCEL_STATE = {}
HYPOTHESIS_THREAD_CANCEL_LOCK = threading.Lock()
SESSION_OPERATION_LOCKS: dict[str, threading.RLock] = {}
SESSION_OPERATION_LOCKS_GUARD = threading.Lock()
AUDIT_CHAT_JOBS: dict[str, dict] = {}
AUDIT_CHAT_JOBS_LOCK = threading.Lock()
AUDIT_CHAT_JOB_TTL_SECS = int(os.environ.get('DISCOVERY_AGENT_AUDIT_CHAT_JOB_TTL_SECS', '7200'))
# Serializes read-modify-write of memory_report.md (scientist feedback ledger)
# so two concurrent saves can't interleave and lose an entry.
_MEMORY_REPORT_LOCK = threading.Lock()

# Session eviction: tracks the last time each session was touched so idle
# sessions can be purged from GENERATORS without waiting for a logout event.
_GENERATOR_LAST_USED: dict[str, float] = {}
_GENERATOR_LAST_USED_LOCK = threading.Lock()
SESSION_EVICT_IDLE_SECS = int(os.environ.get('DISCOVERY_AGENT_SESSION_EVICT_SECS', '3600'))  # 1 hour
_SESSION_EVICT_CHECK_INTERVAL = 300  # check every 5 minutes


def _evict_idle_sessions() -> None:
    """Background loop: evict generator state for sessions idle past the threshold."""
    while True:
        time.sleep(_SESSION_EVICT_CHECK_INTERVAL)
        try:
            cutoff = time.time() - SESSION_EVICT_IDLE_SECS
            with _GENERATOR_LAST_USED_LOCK:
                stale = [sid for sid, t in _GENERATOR_LAST_USED.items() if t < cutoff]
            if not stale:
                continue
            for sid in stale:
                GENERATORS.pop(sid, None)
                with GENERATION_LOCK:
                    GENERATION_STATE.pop(sid, None)
                    REGENERATION_STATE.pop(sid, None)
                with REPORT_CHAT_CANCEL_LOCK:
                    REPORT_CHAT_CANCEL_STATE.pop(sid, None)
                with HYPOTHESIS_THREAD_CANCEL_LOCK:
                    HYPOTHESIS_THREAD_CANCEL_STATE.pop(sid, None)
                with SESSION_OPERATION_LOCKS_GUARD:
                    SESSION_OPERATION_LOCKS.pop(sid, None)
                with _GENERATOR_LAST_USED_LOCK:
                    _GENERATOR_LAST_USED.pop(sid, None)
            print(f"[session-evict] Evicted {len(stale)} idle session(s): {stale}")
        except Exception as exc:
            print(f"[session-evict] Error during eviction: {exc}")


_eviction_thread = threading.Thread(target=_evict_idle_sessions, daemon=True)
_eviction_thread.start()

# Default models used wherever the CLI/session doesn't specify one.
DEFAULT_HYPOTHESIS_MODEL = 'claude-opus-4-6'
DEFAULT_CHAT_MODEL = 'claude-sonnet-4-6'

# Global config from command line arguments
CLI_CHECKPOINT_DIR = None
CLI_HYPOTHESIS_MODEL = None
CLI_CODE_CHAT_MODEL = None
CLI_LEGACY_HYP_GEN = False
CLI_NUM_HYPOTHESES = None
CLI_CODE_DIR = None
CLI_PROJECT_KEY_HASHES = set()
CLI_INCLUDE_BACKGROUND_LITERATURE = False
CLI_GENERATE_PLAN = False
CLI_AUTO_INITIALIZE = False
CLI_README_PATH = None
CLI_GITHUB_URL = None
CLI_DRY_RUN = False
CLI_ENABLE_CITATIONS = False
CLI_CACHE_DOCUMENTS = False
CLI_DEV_MODE = False
DEFAULT_REGENERATE_FEEDBACK = (
    "Please generate a completely new and different hypothesis. "
    "Create an entirely fresh hypothesis with a new angle or approach, "
    "not a revision of the existing one."
)
CHAT_RESTORE_MAX_MESSAGES = 120
CHAT_RESTORE_MAX_CHARS = 220000
CHAT_RESTORE_MESSAGE_MAX_CHARS = 5000
# Timeout for Claude Agent SDK code-chat calls. Set below the gunicorn worker
# timeout so that heavy queries return a clean error instead of a 500.
CODE_CHAT_TIMEOUT_SECS = int(os.environ.get('DISCOVERY_AGENT_CODE_CHAT_TIMEOUT', '720'))
APP_MODE_SELECTION = 'selection'
APP_MODE_AUDIT = 'audit'
APP_MODE_AUDIT_ONLY = 'audit_only'
ALLOWED_FIGURE_EXTENSIONS = frozenset({'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp'})
AUTO_CLEANUP_ENABLED = os.environ.get('DISCOVERY_AGENT_AUTO_CLEANUP', '1') != '0'
DISABLE_S3_SYNC = os.environ.get('DISCOVERY_AGENT_DISABLE_S3_SYNC', '0') == '1'
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL')
NTFY_URL = os.environ.get('NTFY_URL')
NTFY_TOPIC = os.environ.get('NTFY_TOPIC')
NTFY_SERVER = os.environ.get('NTFY_SERVER', 'https://ntfy.sh')
NTFY_TOKEN = os.environ.get('NTFY_TOKEN')
NTFY_TITLE = os.environ.get('NTFY_TITLE', 'Discovery Agent')
NTFY_PRIORITY = os.environ.get('NTFY_PRIORITY')
NTFY_MAX_LEN = int(os.environ.get('NTFY_MAX_LEN', '4000'))
ADMIN_TOKEN = os.environ.get('DISCOVERY_AGENT_ADMIN_TOKEN')
CLIENT_EVENT_RATE_LIMIT = max(10, int(os.environ.get('DISCOVERY_AGENT_CLIENT_EVENT_RATE_LIMIT', '120')))
CLIENT_EVENT_RATE_WINDOW_S = max(5, int(os.environ.get('DISCOVERY_AGENT_CLIENT_EVENT_RATE_WINDOW_S', '60')))
CLIENT_EVENT_MAX_BYTES = max(1024, int(os.environ.get('DISCOVERY_AGENT_CLIENT_EVENT_MAX_BYTES', '12000')))
ALLOWED_CLIENT_EVENT_TYPES = {
    'page_view',
    'ui_click',
    'form_submit',
    'client_error',
    'visibility_change',
    'page_unload',
}
CLIENT_EVENT_RATE_STATE: dict[str, deque[float]] = {}
CLIENT_EVENT_RATE_LOCK = threading.Lock()

_interaction_log_dir = Path(
    os.environ.get('DISCOVERY_AGENT_INTERACTION_LOG_DIR', '')
) if os.environ.get('DISCOVERY_AGENT_INTERACTION_LOG_DIR') else Path(__file__).parent / '.interaction_logs'
_interaction_log_dir.mkdir(parents=True, exist_ok=True)
INTERACTION_LOG_PATH = _interaction_log_dir / 'audit_interactions.jsonl'

_auth_dir = Path(__file__).parent / '.auth_submissions'
_default_registration_log = _auth_dir / 'registrations.jsonl'
REGISTRATION_LOG_PATH = Path(os.environ.get('DISCOVERY_AGENT_REGISTRATION_LOG', _default_registration_log))
REGISTRATION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
PROJECT_KEY_HASH_FILE = _auth_dir / 'project_key.hash'
PROJECT_KEY_HASHES = set()
PLAINTEXT_PROJECT_KEYS = set()
PROJECT_KEY_REGISTRY_FILE = _auth_dir / 'project_keys.json'
PROJECT_KEY_REGISTRY = {}
PROJECT_KEY_CACHE = {'hash_mtime': None, 'registry_mtime': None, 'last_db_refresh': None}
PROJECT_KEY_LOCK = threading.Lock()
PROJECT_PROGRESS_FILE = _auth_dir / 'project_progress.json'
PROJECT_PROGRESS = {}

S3_SYNC_STATUS = {}
S3_SYNC_LOCK = threading.Lock()
REGISTRATION_CACHE = {'mtime': None, 'usernames': set()}
IMAGE_PATTERN = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)(\s+"[^"]*")?\)')
IMG_SRC_PATTERN = re.compile(r'(<img\b[^>]*?\bsrc=)(["\'])(.+?)\2', re.IGNORECASE)
SCHEME_PATTERN = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.-]*:')
def _dir_has_files(path: Path | None) -> bool:
    if not path:
        return False
    try:
        if not path.exists() or not path.is_dir():
            return False
        return any(entry.is_file() for entry in path.rglob('*'))
    except Exception:
        return False


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat() + 'Z'


def _parse_utc_iso(raw: str | None) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    if candidate.endswith('Z'):
        candidate = candidate[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sync_completion_marker_path(checkpoint_s3_prefix: str) -> Path:
    return _disk_root_for_prefix(checkpoint_s3_prefix) / '.s3sync_complete.json'


def _write_sync_marker(checkpoint_s3_prefix: str, state: str, error: str | None = None) -> None:
    if not checkpoint_s3_prefix:
        return
    marker_path = _sync_completion_marker_path(checkpoint_s3_prefix)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    now = _utcnow_iso()
    payload = {
        'state': state,
        'updated_at': now,
        'error': error,
    }
    if state == 'success':
        payload['finished_at'] = now
    tmp_path = marker_path.with_suffix('.tmp')
    tmp_path.write_text(json.dumps(payload), encoding='utf-8')
    tmp_path.replace(marker_path)


def _read_sync_marker(checkpoint_s3_prefix: str | None) -> dict:
    if not checkpoint_s3_prefix:
        return {}
    marker_path = _sync_completion_marker_path(checkpoint_s3_prefix)
    try:
        payload = json.loads(marker_path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _sync_completion_seen_after(checkpoint_s3_prefix: str | None, requested_at: str | None) -> bool:
    if not checkpoint_s3_prefix or not requested_at:
        return False
    requested_dt = _parse_utc_iso(requested_at)
    if requested_dt is None:
        return False
    payload = _read_sync_marker(checkpoint_s3_prefix)
    finished_dt = _parse_utc_iso(payload.get('finished_at'))
    if finished_dt is None:
        return False
    return finished_dt >= requested_dt


def _checkpoint_has_config(path: Path | None) -> bool:
    if not path:
        return False
    try:
        if not path.exists() or not path.is_dir():
            return False
        resolved = _latest_round_checkpoint_dir(path)
        return resolved.is_dir() and (resolved / 'config.json').is_file()
    except Exception:
        return False


def _project_assets_ready(project_cfg: dict) -> bool:
    checkpoint_dir = project_cfg.get('checkpoint_dir')
    code_dir = project_cfg.get('code_dir')
    checkpoint_ready = _checkpoint_has_config(Path(checkpoint_dir)) if checkpoint_dir else False
    code_ready = _dir_has_files(Path(code_dir)) if code_dir else False
    return checkpoint_ready and code_ready


def _get_disk_path() -> Path:
    disk_path = os.environ.get('DISK_PATH')
    if not disk_path and load_dotenv:
        try:
            load_dotenv()
        except Exception:
            pass
        disk_path = os.environ.get('DISK_PATH')
    return Path(disk_path or './local_disk').expanduser().resolve()


def _normalize_project_prefix(checkpoint_s3_prefix: str) -> str:
    raw = (checkpoint_s3_prefix or "").strip()
    if raw.startswith("s3://"):
        raw = raw[len("s3://") :]
        raw = raw.split("/", 1)[1] if "/" in raw else ""
    raw = raw.strip("/")
    if not raw:
        return raw
    for suffix in ("checkpoint", "code"):
        if raw.endswith(f"/{suffix}"):
            return raw[: -(len(suffix) + 1)]
    return raw


def _disk_root_for_prefix(checkpoint_s3_prefix: str) -> Path:
    normalized = _normalize_project_prefix(checkpoint_s3_prefix)
    return _get_disk_path() / normalized


def _checkpoint_sync_root(checkpoint_s3_prefix: str) -> Path:
    """Return the local checkpoint root for a project prefix."""
    return _disk_root_for_prefix(checkpoint_s3_prefix) / "checkpoint"


def _checkpoint_prefix_from_raw(raw_prefix: str | None) -> tuple[str | None, str | None]:
    """
    Normalize a user/project prefix into a checkpoint-only S3 prefix.

    Accepts raw prefixes like:
    - s3://bucket/project
    - s3://bucket/project/checkpoint
    - project
    - project/checkpoint
    and returns (bucket, "<project>/checkpoint/").
    """
    bucket, prefix = _parse_s3_prefix(raw_prefix)
    if not prefix:
        return bucket, prefix
    prefix = prefix.rstrip("/")
    for suffix in ("/checkpoint", "/code"):
        if prefix.endswith(suffix):
            prefix = prefix[: -len(suffix)]
            break
    prefix = f"{prefix}/checkpoint/"
    return bucket, prefix


def _cleanup_disk_cache(checkpoint_s3_prefix: str) -> tuple[bool, str | None]:
    if not AUTO_CLEANUP_ENABLED:
        return False, "disabled"
    if not checkpoint_s3_prefix:
        return False, "missing_prefix"
    disk_root = _get_disk_path().resolve()
    target_root = _disk_root_for_prefix(checkpoint_s3_prefix).resolve()
    try:
        target_root.relative_to(disk_root)
    except ValueError:
        return False, "unsafe_path"
    if not target_root.exists():
        return False, "missing_path"
    shutil.rmtree(target_root)
    return True, None


def _latest_round_checkpoint_dir(checkpoint_root: Path) -> Path:
    """Resolve to the latest round checkpoint directory if present."""
    round_root = checkpoint_root / "checkpoints"
    if not round_root.exists():
        return checkpoint_root
    round_dirs = []
    for entry in round_root.iterdir():
        if entry.is_dir() and entry.name.startswith("round_"):
            suffix = entry.name.split("round_", 1)[-1]
            if suffix.isdigit():
                round_dirs.append((int(suffix), entry))
    if not round_dirs:
        return checkpoint_root
    round_dirs.sort(key=lambda item: item[0])
    return round_dirs[-1][1]


def _list_query_dirs(round_dir: Path) -> list[Path]:
    """Return query_* subdirectories of round_dir, sorted by numeric suffix when possible."""
    if not round_dir.exists() or not round_dir.is_dir():
        return []
    entries = [p for p in round_dir.iterdir() if p.is_dir() and p.name.startswith("query_")]

    def _sort_key(p: Path):
        suffix = p.name.split("query_", 1)[-1]
        return (0, int(suffix)) if suffix.isdigit() else (1, p.name)

    entries.sort(key=_sort_key)
    return entries


def _previous_round_dir(active_round_dir: Path) -> Path | None:
    """Return the round_{N-1} sibling of active_round_dir, if it exists."""
    name = active_round_dir.name
    if not name.startswith("round_"):
        return None
    suffix = name.split("round_", 1)[-1]
    if not suffix.isdigit() or int(suffix) <= 0:
        return None
    candidate = active_round_dir.parent / f"round_{int(suffix) - 1}"
    return candidate if candidate.exists() else None


def _load_latest_round_report_content(
    checkpoint_dir: Path,
) -> tuple[str | None, str | None, Path | None]:
    """
    Load (full_text, summary_text, report_dir) for the latest round that has
    a round_report.md, reusing _latest_round_checkpoint_dir for the scan.

    checkpoint_dir is expected to be a round_N directory; its grandparent is
    the checkpoint root that _latest_round_checkpoint_dir expects (the dir
    that contains the 'checkpoints/' subdirectory).

    If the highest-numbered round has no round_report.md yet (still running),
    returns (None, None, None) so callers can fall back to generator attributes
    loaded from the session's checkpoint at startup.
    """
    # Derive the root that _latest_round_checkpoint_dir expects.
    if checkpoint_dir.name.startswith("round_"):
        checkpoint_root = checkpoint_dir.parent.parent
    else:
        checkpoint_root = checkpoint_dir.parent

    latest_round = _latest_round_checkpoint_dir(checkpoint_root)
    report_path = latest_round / "round_report.md"
    if not report_path.exists():
        return None, None, None

    try:
        raw = report_path.read_text(encoding='utf-8', errors='replace').strip()
    except OSError:
        return None, None, None

    prepared_full = prepare_generated_report_for_display(
        'round', round_number, 'round_report.md', raw,
    )
    prepared_title = f'# Round {round_number} Report'
    full_text = (
        prepared_full[len(prepared_title):].lstrip()
        if prepared_full.startswith(prepared_title)
        else prepared_full
    )

    summary_text: str | None = None
    summary_path = latest_round / "round_report_summary.md"
    if summary_path.exists():
        try:
            summary_text = summary_path.read_text(encoding='utf-8', errors='replace').strip() or None
        except OSError:
            pass

    return full_text, summary_text, latest_round


# --- Scientist feedback helpers -------------------------------------------

_SCIENTIST_FEEDBACK_FILE = "scientist_feedback.md"

# Outer ledger block — one per memory_report.md, holds all per-round entries.
_FEEDBACK_LEDGER_START = "<!-- SCIENTIST_FEEDBACK_LEDGER_START -->"
_FEEDBACK_LEDGER_END = "<!-- SCIENTIST_FEEDBACK_LEDGER_END -->"
_FEEDBACK_LEDGER_HEADING = "# Scientist Feedback Ledger"


def _feedback_markers(round_name: str) -> tuple[str, str]:
    """Return (start_marker, end_marker) for one round's entry inside the ledger."""
    return (
        f"<!-- SCIENTIST_FEEDBACK_START {round_name} -->",
        f"<!-- SCIENTIST_FEEDBACK_END {round_name} -->",
    )


def _build_ledger(entries: list[str]) -> str:
    """
    Wrap a list of per-round entry strings inside the ledger block.
    Each entry string must already include its own round-specific markers.
    Returns an empty string when entries is empty (caller removes the block).
    """
    if not entries:
        return ""
    inner = "\n\n".join(entries)
    return (
        f"{_FEEDBACK_LEDGER_START}\n"
        f"{_FEEDBACK_LEDGER_HEADING}\n\n"
        f"{inner}\n"
        f"{_FEEDBACK_LEDGER_END}"
    )


def _parse_ledger_entries(ledger_body: str) -> dict[str, str]:
    """
    Parse the text between LEDGER_START and LEDGER_END into an ordered dict
    {round_name: full_entry_string} where full_entry_string includes the
    round-specific start/end markers.  Order matches appearance in the file.
    """
    entries: dict[str, str] = {}
    for m in re.finditer(
        r'(<!-- SCIENTIST_FEEDBACK_START (\S+) -->.*?<!-- SCIENTIST_FEEDBACK_END \2 -->)',
        ledger_body,
        re.DOTALL,
    ):
        entries[m.group(2)] = m.group(1)
    return entries


def _resolve_latest_completed_round_dir(generator) -> Path | None:
    """
    Return the latest round directory that contains BOTH round_report.md
    (the round is complete) and memory_report.md (the injection target for
    the scientist feedback ledger), using the same _latest_round_checkpoint_dir
    logic as the report page. Returns None when either file is missing, so
    feedback can never be saved without reaching next-round memory.
    """
    checkpoint_dir = getattr(generator, 'checkpoint_dir', None)
    if not checkpoint_dir:
        return None
    checkpoint_dir = Path(checkpoint_dir)
    checkpoint_root = (
        checkpoint_dir.parent.parent
        if checkpoint_dir.name.startswith("round_")
        else checkpoint_dir.parent
    )
    latest = _latest_round_checkpoint_dir(checkpoint_root)
    if (latest / "round_report.md").exists() and (latest / "memory_report.md").exists():
        return latest
    return None


def _write_memory_report_atomic(path: Path, content: str) -> None:
    """Write via tmp file + rename so a crash mid-write can't truncate memory_report.md."""
    tmp_path = path.with_name(path.name + '.tmp')
    tmp_path.write_text(content, encoding='utf-8')
    tmp_path.replace(path)


def _inject_feedback_into_memory_report(round_dir: Path, feedback_section: str) -> bool:
    """
    Idempotently insert or update this round's scientist feedback entry inside
    the single Scientist Feedback Ledger block at the end of memory_report.md.

    Structure written/maintained in memory_report.md:

        <!-- SCIENTIST_FEEDBACK_LEDGER_START -->
        # Scientist Feedback Ledger

        <!-- SCIENTIST_FEEDBACK_START round_1 -->
        ## Scientist Feedback on Round 1
        ...
        <!-- SCIENTIST_FEEDBACK_END round_1 -->

        <!-- SCIENTIST_FEEDBACK_START round_2 -->
        ## Scientist Feedback on Round 2
        ...
        <!-- SCIENTIST_FEEDBACK_END round_2 -->
        <!-- SCIENTIST_FEEDBACK_LEDGER_END -->

    Rules enforced:
    - Exactly one ledger block per memory_report.md.
    - Each round has at most one entry inside the ledger.
    - Saving for round_N updates only the round_N entry; other entries are
      preserved in their original positions.
    - Returns True when the file was updated, False when memory_report.md does
      not yet exist (injection is skipped; caller shows a status note).
    """
    memory_report_path = round_dir / "memory_report.md"
    if not memory_report_path.exists():
        return False

    try:
        content = memory_report_path.read_text(encoding='utf-8')
    except OSError:
        return False

    start_marker, end_marker = _feedback_markers(round_dir.name)
    round_entry = f"{start_marker}\n{feedback_section}\n{end_marker}"

    ledger_match = re.search(
        re.escape(_FEEDBACK_LEDGER_START) + r'(.*?)' + re.escape(_FEEDBACK_LEDGER_END),
        content,
        re.DOTALL,
    )
    if ledger_match:
        # Ledger exists — update or insert this round's entry within it.
        entries = _parse_ledger_entries(ledger_match.group(1))
        entries[round_dir.name] = round_entry  # insert or replace; preserves other rounds
        new_ledger = _build_ledger(list(entries.values()))
        before = content[:ledger_match.start()].rstrip()
        after = content[ledger_match.end():]
        content = before + f"\n\n{new_ledger}\n" + after
    else:
        # No ledger yet — create one with just this entry, appended to the end.
        new_ledger = _build_ledger([round_entry])
        content = content.rstrip() + f"\n\n{new_ledger}\n"

    try:
        _write_memory_report_atomic(memory_report_path, content)
        return True
    except OSError:
        return False


def _remove_feedback_from_memory_report(round_dir: Path) -> bool:
    """
    Remove this round's entry from the Scientist Feedback Ledger in
    memory_report.md.  Other rounds' entries are preserved.  If the ledger
    becomes empty after removal, the entire ledger block is also removed.
    Returns True on success or when there is nothing to remove.
    """
    memory_report_path = round_dir / "memory_report.md"
    if not memory_report_path.exists():
        return True

    try:
        content = memory_report_path.read_text(encoding='utf-8')
    except OSError:
        return False

    ledger_match = re.search(
        re.escape(_FEEDBACK_LEDGER_START) + r'(.*?)' + re.escape(_FEEDBACK_LEDGER_END),
        content,
        re.DOTALL,
    )
    if not ledger_match:
        return True  # nothing to remove

    entries = _parse_ledger_entries(ledger_match.group(1))
    entries.pop(round_dir.name, None)

    before = content[:ledger_match.start()].rstrip()
    after = content[ledger_match.end():]

    if entries:
        new_ledger = _build_ledger(list(entries.values()))
        content = before + f"\n\n{new_ledger}\n" + after
    else:
        # Ledger is now empty — strip the entire block cleanly.
        content = before.rstrip('\n') + '\n' + after.lstrip('\n')

    try:
        _write_memory_report_atomic(memory_report_path, content.rstrip('\n') + '\n')
        return True
    except OSError:
        return False


def _locate_selected_hypothesis_context(active_round_dir: Path) -> Path | None:
    """
    Best-effort, display-only lookup of selected_hypothesis.md for context.

    Search order: round_{N-1}/selected_hypothesis.md (where selection for the
    active round is typically recorded), then active_round_dir itself as a
    fallback. Returns None if neither exists -- this must never block Round
    Monitor routing, which depends only on query_* dirs + round_report.md.
    """
    previous = _previous_round_dir(active_round_dir)
    if previous:
        candidate = previous / "selected_hypothesis.md"
        if candidate.exists():
            return candidate
    candidate = active_round_dir / "selected_hypothesis.md"
    if candidate.exists():
        return candidate
    return None


_HYPOTHESIS_LABEL_RE = re.compile(
    r'(?:^|\n)\s*#{0,6}\s*\**\s*Hypothesis\s*\**\s*:?\s*',
    re.IGNORECASE,
)
_JUSTIFICATION_LABEL_RE = re.compile(
    r'(?:^|\n)\s*#{0,6}\s*\**\s*Justification\s*\**\s*:?',
    re.IGNORECASE,
)
_TITLE_LABEL_LINE_RE = re.compile(
    r'(?:^|\n)\s*#{0,6}\s*\**\s*Title\s*\**\s*:?[^\n]*\n?',
    re.IGNORECASE,
)
_LEADING_LABEL_RE = re.compile(r'^\s*#{1,6}\s*|^\s*\**\s*[A-Za-z][A-Za-z ]*\**\s*:\s*')


def _extract_hypothesis_display_text(raw_text: str | None) -> str:
    """
    Extract just the hypothesis statement from a selected_hypothesis.md-style
    document for display purposes: drop the 'Hypothesis'/'Title' labels,
    stop before any Justification section, never truncate the remainder.

    selected_hypothesis.md is written by save_selection()/save_selection_raw()
    in select_hypothesis.py, normally starting with "**Hypothesis**: <text>"
    followed by a blank line and "**Justification**: ...". This handles that
    format directly, plus heading-style ("## Hypothesis") and label-less
    fallback documents (e.g. raw manually-edited text).
    """
    if not raw_text or not raw_text.strip():
        return ''

    text = raw_text.strip()

    hyp_match = _HYPOTHESIS_LABEL_RE.search(text)
    if hyp_match:
        remainder = text[hyp_match.end():]
        justification_match = _JUSTIFICATION_LABEL_RE.search(remainder)
        if justification_match:
            remainder = remainder[:justification_match.start()]
        result = remainder.strip()
        if result:
            return result

    # Fallback: no usable "Hypothesis" label found. Drop any Title line and
    # anything from "Justification" onward, then take the first substantial
    # paragraph as the best-effort hypothesis text.
    no_title = _TITLE_LABEL_LINE_RE.sub('', text)
    justification_match = _JUSTIFICATION_LABEL_RE.search(no_title)
    if justification_match:
        no_title = no_title[:justification_match.start()]

    for para in re.split(r'\n\s*\n', no_title):
        cleaned = para.strip()
        cleaned = _LEADING_LABEL_RE.sub('', cleaned, count=1).strip()
        if len(cleaned) >= 20:
            return cleaned

    return text


# Generic headings/titles that carry no actual content -- e.g. "Query 1
# Details", "Details", "Results", "Report Prompt". These must never be
# returned as a query description, even after markdown heading markers are
# stripped. Intentionally generic/structural -- no dataset, gene, or
# project-specific terms.
_GENERIC_HEADING_PATTERNS = [
    re.compile(r'^query\s*#?\s*\d*\s*details?$', re.IGNORECASE),
    re.compile(r'^query\s*#?\s*\d+$', re.IGNORECASE),
    re.compile(r'^analysis\s*details?$', re.IGNORECASE),
    re.compile(r'^details?$', re.IGNORECASE),
    re.compile(r'^results?$', re.IGNORECASE),
    re.compile(r'^summary$', re.IGNORECASE),
    re.compile(r'^report$', re.IGNORECASE),
    re.compile(r'^report\s+prompt$', re.IGNORECASE),
    re.compile(r'^overview$', re.IGNORECASE),
    re.compile(r'^description$', re.IGNORECASE),
    re.compile(r'^conclusion$', re.IGNORECASE),
    re.compile(r'^(?:input|output|metadata)$', re.IGNORECASE),
]

_BOILERPLATE_PHRASE_RE = re.compile(
    r'\bdataset documentation\b|\bavailable files\b|\bdirectory listing\b'
    r'|\bsee below\b|\bthe following files\b|\bfile listing\b|\bno additional (?:notes|details)\b'
    r'|\bhere is a description of the dataset\b|\bhere is a description of\b'
    r'|\b(?:all\s+)?data\s+(?:is|are)\s+located\s+at\b'
    r'|\bdataset description\b|\btask description\b|\btask prompt\b'
    r'|\bproject bundle\b|\bdataset overview\b',
    re.IGNORECASE,
)

_FILENAME_LIKE_RE = re.compile(
    r'^[\w\-./\\]+\.(?:md|txt|json|py|csv|tsv|ipynb|png|jpg|pdf|log|yaml|yml)$', re.IGNORECASE
)

# Explicit labeled fields, checked before generic paragraph scanning -- this
# is priority 1 of the description extraction order. Matches a label at the
# start of a (possibly heading-marked) line, followed by ": <value>" on the
# same line.
_LABELED_FIELD_RE = re.compile(
    r'(?:^|\n)\s*#{0,6}\s*\**\s*'
    r'(?:Query|Research\s+question|Objective|Task|Analysis|Finding|Result|Conclusion|Summary|Rationale)'
    r'\s*\**\s*:\s*([^\n]+)',
    re.IGNORECASE,
)


def _is_meaningful_description_candidate(candidate: str) -> bool:
    """
    Defensive filter applied before accepting any extracted text as a query
    description. Rejects generic headings, boilerplate, filenames, and
    anything too short or too punctuation-heavy to actually describe what
    happened -- the caller should keep searching (or fall back) on reject.
    """
    text = candidate.strip()
    if len(text) < 20:
        return False
    heading_check = text.rstrip('.:').strip()
    if any(pattern.match(heading_check) for pattern in _GENERIC_HEADING_PATTERNS):
        return False
    if _BOILERPLATE_PHRASE_RE.search(text):
        return False
    if _FILENAME_LIKE_RE.match(text):
        return False
    letters = sum(1 for ch in text if ch.isalpha())
    if letters < max(8, len(text) * 0.4):
        return False
    return True


def _first_meaningful_sentence(
    text: str | None,
    max_len: int = 220,
    exclude: set[str] | None = None,
) -> str | None:
    """
    Pull the first meaningful, non-heading, non-boilerplate sentence/value
    out of a markdown-ish snippet, in priority order:
      1. An explicit labeled field (Objective:, Result:, etc).
      2. The first non-heading paragraph/bullet line that passes
         _is_meaningful_description_candidate.
    A line that IS just a heading/title (e.g. "# Query 1 Details") is never
    returned -- it's filtered by _is_meaningful_description_candidate after
    its markdown markup is stripped, not just destarred and passed through.
    `exclude` lets the caller reject specific strings that have already been
    used (e.g. identical boilerplate repeated verbatim across many queries).
    """
    if not text:
        return None
    exclude = exclude or set()

    for match in _LABELED_FIELD_RE.finditer(text):
        candidate = match.group(1).strip()
        candidate = candidate[:max_len] + ('...' if len(candidate) > max_len else '')
        if candidate not in exclude and _is_meaningful_description_candidate(candidate):
            return candidate

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        cleaned = re.sub(r'^#{1,6}\s*', '', line)
        cleaned = re.sub(r'^\**', '', cleaned)
        cleaned = re.sub(r'\**$', '', cleaned)
        cleaned = cleaned.lstrip('-*').strip()
        if not cleaned:
            continue
        candidate = cleaned[:max_len] + ('...' if len(cleaned) > max_len else '')
        if candidate not in exclude and _is_meaningful_description_candidate(candidate):
            return candidate
    return None


def _infer_query_statuses(
    query_dirs: list[Path],
    has_round_report: bool,
    log_text: str | None,
) -> dict[str, str]:
    """
    Best-effort completed/in_progress status per query_* dir.

    Heuristics (filesystem/log evidence only, never raised as an error):
      - If round_report.md exists, every query is 'completed'.
      - Otherwise, the highest-numbered query dir is 'in_progress' and every
        earlier one is 'completed' (a later query existing is itself evidence
        the earlier one finished).
      - If the log contains an explicit completion marker for a given query
        number, or an explicit "starting query_{N+1}" marker, that upgrades
        the corresponding query to 'completed' even if it's the latest.
      - Non-numeric query_* dir names (no reliable ordering) get 'discovered'.
    """
    statuses: dict[str, str] = {}
    numeric_dirs: list[tuple[int, Path]] = []
    for qd in query_dirs:
        suffix = qd.name.split("query_", 1)[-1]
        if suffix.isdigit():
            numeric_dirs.append((int(suffix), qd))
        else:
            statuses[qd.name] = 'discovered'

    if not numeric_dirs:
        return statuses

    if has_round_report:
        for _, qd in numeric_dirs:
            statuses[qd.name] = 'completed'
        return statuses

    numeric_dirs.sort(key=lambda item: item[0])
    for i, (_, qd) in enumerate(numeric_dirs):
        statuses[qd.name] = 'completed' if i < len(numeric_dirs) - 1 else 'in_progress'

    if log_text:
        for num, qd in numeric_dirs:
            padded = f"{num:03d}"
            completion_patterns = (
                rf'\bquery[_\s]?(?:0*{num}|{padded})\b[^\n]{{0,60}}?\b(completed|finished|done|saved\s+checkpoint)\b',
                rf'\b(completed|finished|done|saved\s+checkpoint)\b[^\n]{{0,60}}?\bquery[_\s]?(?:0*{num}|{padded})\b',
            )
            if any(re.search(pat, log_text, re.IGNORECASE) for pat in completion_patterns):
                statuses[qd.name] = 'completed'

            next_num = num + 1
            next_padded = f"{next_num:03d}"
            starting_next_pattern = (
                rf'\bstarting\b[^\n]{{0,30}}?\bquery[_\s]?(?:0*{next_num}|{next_padded})\b'
            )
            if re.search(starting_next_pattern, log_text, re.IGNORECASE):
                statuses[qd.name] = 'completed'

    return statuses


_QUERY_DESCRIPTION_FALLBACK = "Query artifacts created; objective not yet summarized."


def _infer_query_description(
    qd: Path,
    log_text: str | None,
    exclude: set[str] | None = None,
) -> str:
    """
    Best-effort one-line description of what a query_* dir is/was doing.

    Priority: query_NNN/query_NNN.md labeled field or first meaningful
    paragraph -> nearby log mentions -> generic fallback. Generic headings
    ("Query 1 Details"), boilerplate, and filenames are rejected as
    candidates (see _is_meaningful_description_candidate) rather than ever
    being returned as the description. Always capped, never reads large
    files in full. `exclude` lets a caller iterating over many queries avoid
    accepting the exact same boilerplate-like text repeated across all of
    them (see _build_query_activity_events).
    """
    md_path = qd / f"{qd.name}.md"
    if md_path.exists():
        try:
            with open(md_path, 'r', encoding='utf-8', errors='replace') as f:
                snippet = f.read(4000)
            sentence = _first_meaningful_sentence(snippet, exclude=exclude)
            if sentence:
                return sentence
        except OSError:
            pass

    if log_text:
        idx = log_text.find(qd.name)
        if idx != -1:
            window = log_text[idx: idx + 500]
            sentence = _first_meaningful_sentence(window, exclude=exclude)
            if sentence:
                return sentence

    return _QUERY_DESCRIPTION_FALLBACK


# --- Round Activity extraction -------------------------------------------
# Deterministic, regex-based, cheap on every poll -- never an LLM call.
#
# AIR.utils.logger.Logger (AIR/utils/logger.py) is the one concretely known
# log format in this codebase: each entry is written via Python `logging`
# with formatter "%(asctime)s - %(levelname)s - %(message)s" (date format
# "%Y-%m-%d %H:%M:%S"), and high-level events are tagged with `[ACTION] ...`
# (log_action) or `[ERROR] ...` (log_error). A run's startup banner contains
# "RUN START: <timestamp> - analysis: <name>". `[PROMPT]`/`[RESPONSE]`/`[CODE]`
# entries carry full prompts/code/model output and are intentionally never
# surfaced here -- only [ACTION]/[ERROR] entries (plus the run-start banner)
# make it into the Activity feed, which is what keeps giant prompts, READMEs,
# and directory dumps out by construction rather than by post-hoc filtering.
#
# round.log/live_round.log are not guaranteed to share this exact format
# (their writer lives in a separate backend repo), so a handful of generic
# keyword fallbacks are also included for resilience.

_LOG_ENTRY_HEADER_RE = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - (?P<level>\w+) - (?P<rest>.*)$'
)
_RUN_START_RE = re.compile(r'RUN START:.*?-\s*analysis:\s*(.+)', re.IGNORECASE)
_ACTION_TAG_RE = re.compile(r'^\[ACTION\]\s*([A-Za-z0-9_]+)\s*(?:-\s*(.*))?$')

# Low-value/noisy content that should never become a feed row, regardless of
# which tag or generic pattern it would otherwise match -- intentionally
# generic (operational/security/bookkeeping terms, no dataset/project-
# specific terms) so it applies to any DiscoveryAgent run.
_ACTIVITY_NOISE_RE = re.compile(
    r'\btoken[\s_]*estimate\b|\btoken[s]?\b.{0,15}\b(?:used|remaining|budget)\b'
    r'|\bbudget\b.{0,20}\b(?:remaining|used|exceeded|limit|check)\b'
    r'|\bhelper[\s_-]?agent\b'
    r'|\bpre[\s_-]?execution\b.{0,20}\bdirector(?:y)?\b'
    r'|\bdirectory\s+(?:state|listing|dump)\b'
    r'|\bsandbox\b'
    r'|\bfile\s+safety\s+check\b|\bsafety\s+check\b'
    r'|\bwrite\s+deny\b|\baccess\s+deny\b'
    r'|\b(?:report|query)\s+(?:query\s+)?input\b'
    r'|\bnew\s+files?\s+created\b'
    r'|\bhelper\s+agent\s+completed\b'
    r'|\breport\s+prompt\b'
    r'|\bquery\s+executed\b',
    re.IGNORECASE,
)
# Action *names* (the [ACTION] <name> tag itself) that are pure boilerplate
# and should never produce a row even if their detail text doesn't match the
# noise regex above (e.g. "[ACTION] token_estimate" with no other text).
_ACTIVITY_NOISE_ACTION_NAMES = {'token_estimate', 'budget_check', 'budget_update'}
# Substrings within an action *name* that mark it as internal/operational
# bookkeeping rather than scientist-facing progress -- substring (not exact
# match) so it catches variants like "sandbox_bash_write_deny",
# "pre_execution_directory_state", "report_query_input", "file_safety_check"
# without needing to enumerate every exact action name a backend might emit.
_ACTIVITY_NOISE_ACTION_SUBSTRINGS = (
    'sandbox', 'safety_check', 'directory_state', 'directory_listing',
    'query_input', 'pre_execution', 'write_deny', 'access_deny',
    'file_check', 'new_files_created', 'helper_agent',
    'report_prompt', 'query_executed',
)

# Query-specific events (started/completed/checkpoint-saved) come only from
# _build_query_activity_events; this list is limited to round-level patterns.
# Deep-research mentions that can't be classified as started/completed are
# suppressed rather than shown as an uninformative "noted".
_GENERIC_ACTIVITY_PATTERNS = [
    (re.compile(r'\bsession\b[^\n]{0,20}?\breset\b|\breset\b[^\n]{0,20}?\bsession\b', re.I), 'session_reset'),
    (re.compile(r'\b(?:deep research|literature (?:check|review|search))\b[^\n]{0,30}?\b(?:started|launched|beginning)\b', re.I), 'deep_research_started'),
    (re.compile(r'\b(?:deep research|literature (?:check|review|search))\b[^\n]{0,30}?\b(?:completed|finished|done)\b', re.I), 'deep_research_completed'),
    (re.compile(r'\breport\s+writing\b[^\n]{0,20}?\bstart(?:ed|ing)\b', re.I), 'report_writing_started'),
    (re.compile(r'\bround[_\s]?report\b[^\n]{0,30}?\b(?:saved|written|writing|generat\w*)\b', re.I), 'round_report_saved'),
    (re.compile(r'\bmemory[_\s]?report\b[^\n]{0,30}?\b(?:saved|written|writing)\b', re.I), 'memory_report_saved'),
    (re.compile(r'\bworkflow\b[^\n]{0,20}?\b(?:completed|finished|done)\b', re.I), 'workflow_completed'),
    (re.compile(r'\b(?:execution agent|code execution)\b[^\n]{0,30}?\b(?:started|launched|running)\b', re.I), 'code_execution_started'),
    (re.compile(r'\b(?:execution agent|code execution)\b[^\n]{0,30}?\b(?:completed|finished|done)\b', re.I), 'code_execution_completed'),
    (re.compile(r'\banalysis\b[^\n]{0,30}?\b(?:completed|finished|done)\b', re.I), 'analysis_completed'),
]

# Optional nearby query-number capture for deep-research mentions, so e.g.
# "deep research started for query_004" can read as "query_004
# literature/deep-research step started." instead of a generic message --
# checked independently of _GENERIC_ACTIVITY_PATTERNS above since it needs
# its own capture group semantics.
_DEEP_RESEARCH_QUERY_RE = re.compile(r'\bquery[_\s]?0*(\d+)\b', re.IGNORECASE)

_GENERIC_ACTIVITY_LABELS = {
    'session_reset': 'Session reset.',
    'deep_research_started': 'Literature / deep-research step started.',
    'deep_research_completed': 'Literature / deep-research step completed.',
    'report_writing_started': 'Report writing started.',
    'round_report_saved': 'Round report saved.',
    'memory_report_saved': 'Memory report saved.',
    'workflow_completed': 'Workflow completed.',
    'code_execution_started': 'Code execution started.',
    'code_execution_completed': 'Code execution completed: inspected available project files and run artifacts.',
    'analysis_completed': 'Analysis step completed; query artifacts were saved.',
}

# Used only to rank which event "wins" when multiple land on the same
# timestamp (lower number = kept in preference to higher numbers).
# 'query_event' (from the query-centric builder) intentionally ranks highest
# after error/run_start -- it's the primary signal this feed exists to show.
_EVENT_KIND_PRIORITY = {
    'error': 0,
    'run_start': 1,
    'session_reset': 2,
    'query_event': 3,
    'round_activity_jsonl': 4,
    'workflow_completed': 5,
    'report_writing_started': 6,
    'round_report_saved': 7,
    'memory_report_saved': 8,
    'deep_research_started': 9,
    'deep_research_completed': 10,
    'analysis_completed': 11,
    'code_execution_completed': 12,
    'code_execution_started': 13,
    'action_generic': 14,
}

# Action *name* keyword groups used to classify otherwise-unrecognized
# [ACTION] tags into report-writing-related events without hardcoding any
# exact action name -- a real backend might log this as
# "final_report_generated", "report_writing_complete", etc.
_REPORT_ACTION_COMPLETE_KEYWORDS = ('generat', 'writ', 'sav', 'complet', 'final')
_REPORT_ACTION_START_KEYWORDS = ('start', 'begin')


def _split_log_into_entries(log_text: str) -> list[tuple[str | None, str]]:
    """Group raw log lines into (timestamp, full_entry_text) blocks at each new timestamped header line."""
    entries: list[tuple[str | None, str]] = []
    current_ts: str | None = None
    current_lines: list[str] = []

    def _flush():
        if current_lines:
            entries.append((current_ts, "\n".join(current_lines).strip()))

    for raw_line in log_text.splitlines():
        header_match = _LOG_ENTRY_HEADER_RE.match(raw_line)
        if header_match:
            _flush()
            current_ts = header_match.group('ts')
            current_lines = [header_match.group('rest')]
        else:
            if raw_line.strip():
                current_lines.append(raw_line)
    _flush()
    return entries


def _format_action_event(action: str, remainder: str) -> tuple[str | None, str | None, str]:
    """
    Turn a non-query Logger.log_action() entry into a short, domain-general
    message + kind. analysis_start/analysis_end/iteration are handled
    entirely by the query-centric builder now (see _build_query_activity_events)
    and are filtered out by the caller before this is ever invoked.
    """
    details: dict[str, str] = {}
    for line in remainder.splitlines():
        if '=' in line:
            key, _, value = line.partition('=')
            details[key.strip()] = value.strip()

    # Unknown/custom action tag mentioning "report": classify by keyword
    # rather than hardcoding an exact action name, since the real backend's
    # naming isn't documented here (e.g. "final_report_generated",
    # "report_writing_complete" should both read as report-writing progress).
    action_lower = action.lower()
    if 'report' in action_lower:
        if any(kw in action_lower for kw in _REPORT_ACTION_COMPLETE_KEYWORDS):
            return ("Round report saved.", None, 'round_report_saved')
        if any(kw in action_lower for kw in _REPORT_ACTION_START_KEYWORDS):
            return ("Report writing started.", None, 'report_writing_started')

    # Any other unrecognized/custom action tag: still surface it, generically.
    return (f"{action.replace('_', ' ').capitalize()}.", None, 'action_generic')


def _safe_mtime(path: Path) -> float | None:
    """Filesystem mtime, or None if the path doesn't exist/can't be stat'd. Never raises."""
    try:
        if path.exists():
            return path.stat().st_mtime
    except OSError:
        pass
    return None


def _format_mtime(mtime: float) -> str:
    """
    Format a filesystem mtime to match log timestamp formatting
    ("%Y-%m-%d %H:%M:%S") so it sorts/displays consistently alongside
    log-derived timestamps. This is always a fallback APPROXIMATION of when
    something happened, never an authoritative event time -- callers should
    only reach for this once log evidence has been checked and found absent.
    """
    return datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')


def _mtime_fallback(qd: Path) -> str | None:
    """Last-resort timestamp for a query dir: its query_status.json, then .md, then the dir itself."""
    mtime = (
        _safe_mtime(qd / "query_status.json")
        or _safe_mtime(qd / f"{qd.name}.md")
        or _safe_mtime(qd)
    )
    return _format_mtime(mtime) if mtime is not None else None


# --- query_status.json: structured per-query progress instrumentation ----
# The backend now writes checkpoints/round_N/query_NNN/query_status.json during 
# a run. When present, this is the primary source of truth for that query's progress

_QUERY_STATUS_FALLBACK_TEXT = "specific objective not yet written to query status or query files."

_QUERY_STEP_LABELS = {
    'planning': 'planning',
    'executing_code': 'executing code',
    'running_literature_check': 'running literature check',
    'analyzing_results': 'analyzing results',
    'writing_query_report': 'writing query report',
    'completed': 'completed',
    'failed': 'failed',
}


def _read_query_status(qd: Path) -> dict | None:
    """
    Read query_NNN/query_status.json. Returns None if missing, unreadable,
    malformed, or not a JSON object -- never raises, so a corrupt/partial
    write from a live run can never break the Activity feed or Round
    Directory; callers simply fall back to the legacy inference path.
    """
    path = qd / "query_status.json"
    if not path.exists():
        return None
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _summarize_query_status(status_data: dict) -> dict:
    """
    Normalize + clean a raw query_status.json dict into the fields every
    consumer (Round Directory, Activity feed, Audit Agent Chat context)
    needs.
    """
    raw_status = status_data.get('status')
    status = raw_status if raw_status in ('running', 'completed', 'failed') else 'unknown'

    current_step = status_data.get('current_step')
    current_step = current_step.strip() if isinstance(current_step, str) and current_step.strip() else None

    def _clean(value):
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value if value and _is_meaningful_description_candidate(value) else None

    objective = _clean(status_data.get('objective'))
    summary = _clean(status_data.get('summary'))

    error = status_data.get('error')
    if isinstance(error, str) and error.strip():
        # Only the first line -- never let a dumped traceback through.
        error = error.strip().splitlines()[0][:200]
    else:
        error = None

    def _ts(key):
        value = status_data.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    artifacts = status_data.get('artifacts_written')
    artifacts = [a for a in artifacts if isinstance(a, str)] if isinstance(artifacts, list) else []

    if status == 'failed':
        description = f"failed: {error}" if error else "failed without a recorded error message."
    elif status == 'completed':
        description = summary or objective or _QUERY_STATUS_FALLBACK_TEXT
    else:  # running or unknown
        description = objective or summary or _QUERY_STATUS_FALLBACK_TEXT

    return {
        'status': status,
        'current_step': current_step,
        'objective': objective,
        'summary': summary,
        'error': error,
        'description': description,
        'started_at': _ts('started_at'),
        'updated_at': _ts('updated_at'),
        'completed_at': _ts('completed_at'),
        'artifacts_written': artifacts,
    }


def _summarize_artifacts_written(artifacts: list) -> str | None:
    """
    Build a short, non-noisy artifact note from artifacts_written, e.g.
    "Artifacts saved: query markdown, checkpoint." Returns None if nothing
    recognizable so callers don't dump a raw file list into the feed.
    """
    if not artifacts:
        return None
    found = set()
    for item in artifacts:
        if not isinstance(item, str):
            continue
        lowered = item.lower()
        if 'checkpoint' in lowered:
            found.add('checkpoint')
        elif lowered.endswith('.md'):
            found.add('query markdown')
    if not found:
        return None
    ordered = [label for label in ('query markdown', 'checkpoint') if label in found]
    return f"Artifacts saved: {', '.join(ordered)}."


_QUERY_SUMMARY_CACHE_FILE = "query_summary.txt"
_QUERY_SUMMARY_MAX_CHARS = 140
_QUERY_SUMMARY_PLACEHOLDER = "Query objective pending"

# Patterns that disqualify a string from being a meaningful query summary.
# Generic status words, report headings, and artifact notes must not be
# displayed as query summaries.
_WEAK_QUERY_SUMMARY_RE = re.compile(
    r'comprehensive\s+analysis\s+report'
    r'|artifacts?\s+saved'
    r'|query\s+markdown'
    r'|\bcheckpoints?\s+(?:saved|written|created)\b'
    r'|^execute\s+quer'
    r'|^(?:running|completed|failed|processing|started|pending|finished|executing|done)[.\s]*$'
    r'|^query\s+(?:running|complete|status)[.\s]*$',
    re.IGNORECASE,
)


def _is_meaningful_query_summary(text: str | None) -> bool:
    """
    True only when *text* is worth caching in query_summary.txt and displaying
    as a query-row description in Round Monitor. Rejects:
    - empty / None
    - markdown headings (start with '#')
    - generic status-only lines ('running', 'completed', 'processing', …)
    - noisy artifact / report headings ('Comprehensive Analysis Report', …)
    - fewer than 6 alpha-bearing words
    - anything that fails the baseline _is_meaningful_description_candidate check

    Weak summaries are ignored in favor of deterministic metadata extraction.
    """
    if not text:
        return False
    text = text.strip()
    if not text or text.startswith('#'):
        return False
    if _WEAK_QUERY_SUMMARY_RE.search(text):
        return False
    alpha_words = [w for w in text.split() if any(c.isalpha() for c in w)]
    if len(alpha_words) < 6:
        return False
    return _is_meaningful_description_candidate(text)


def _clip_query_summary(text: str) -> str:
    """Word-boundary truncation to _QUERY_SUMMARY_MAX_CHARS with an ellipsis."""
    if len(text) <= _QUERY_SUMMARY_MAX_CHARS:
        return text
    return text[:_QUERY_SUMMARY_MAX_CHARS].rsplit(' ', 1)[0] + '…'


def _fallback_query_summary(query_dir: Path) -> str:
    """
    Deterministically derive a concise summary from query artifacts.

    Priority: structured fields from query_status.json (objective, title,
    description, instruction, prompt, summary) → **Query**: field in the
    query markdown → _first_meaningful_sentence from markdown → generic fallback.
    Never returns a raw report heading or artifact note.
    """
    # 1. Structured fields from query_status.json (cleanest)
    status_data = _read_query_status(query_dir)
    if status_data:
        for field in ('objective', 'title', 'description', 'instruction', 'prompt', 'summary'):
            val = status_data.get(field)
            if isinstance(val, str):
                val = re.sub(r'\s+', ' ', val).strip()
                if val and _is_meaningful_description_candidate(val):
                    return _clip_query_summary(val)

    # 2. **Query**: field from query_NNN.md
    md_path = query_dir / f"{query_dir.name}.md"
    if md_path.exists():
        try:
            with open(md_path, 'r', encoding='utf-8', errors='replace') as f:
                snippet = f.read(3000)
            m = re.search(r'\*\*Query\*\*:\s*(.+?)(?:\n|$)', snippet)
            if m:
                val = re.sub(r'\*+', '', m.group(1)).strip()
                if val and _is_meaningful_description_candidate(val):
                    return _clip_query_summary(val)
            sentence = _first_meaningful_sentence(snippet)
            if sentence:
                return _clip_query_summary(sentence)
        except OSError:
            pass

    return _QUERY_DESCRIPTION_FALLBACK


def _get_query_summary(query_dir: Path) -> str:
    """
    Return a deterministic, concise summary of what query_dir is testing.

    Existing meaningful query_summary.txt artifacts remain authoritative.
    Otherwise, derive the text from structured query metadata or markdown and
    use a stable placeholder until those artifacts contain a useful objective.
    """
    cache_path = query_dir / _QUERY_SUMMARY_CACHE_FILE

    # Preserve meaningful summaries produced as part of the research run.
    if cache_path.exists():
        try:
            cached = cache_path.read_text(encoding='utf-8').strip()
            if _is_meaningful_query_summary(cached):
                return cached
        except OSError:
            pass

    fallback = _fallback_query_summary(query_dir)
    if _is_meaningful_query_summary(fallback):
        return fallback

    return _QUERY_SUMMARY_PLACEHOLDER


def _build_query_status_events(qd: Path, num: int, status_data: dict, description_counts: dict[str, int]) -> list[dict]:
    """
    Build the Activity row(s) for one query directly from query_status.json
    -- the primary source when present. One row per query reflecting its
    current snapshot (running/completed/failed).
    """
    info = _summarize_query_status(status_data)
    repeated = {text for text, count in description_counts.items() if count >= 2}

    def _accept(text: str | None) -> str | None:
        if not text or text in repeated:
            return None
        description_counts[text] = description_counts.get(text, 0) + 1
        return text

    qd_name = qd.name
    timestamp = info['completed_at'] or info['updated_at'] or info['started_at'] or _mtime_fallback(qd)

    if info['status'] == 'failed':
        body = _accept(info['error']) or "failed without a recorded error message."
        message = f"{qd_name} failed: {body}"
    elif info['status'] == 'running':
        body = _accept(_get_query_summary(qd)) or _QUERY_STATUS_FALLBACK_TEXT
        step_label = _QUERY_STEP_LABELS.get(info['current_step'])
        prefix = f"{qd_name} running" + (f" — {step_label}" if step_label else "")
        message = f"{prefix}: {body}"
    else:
        # 'completed' or 'unknown' -- LLM/cached summary preferred; fallback to honest placeholder.
        body = _accept(_get_query_summary(qd)) or _QUERY_STATUS_FALLBACK_TEXT
        message = f"{qd_name} completed: {body}" if info['status'] == 'completed' else f"{qd_name}: {body}"
        if info['status'] == 'completed':
            artifact_note = _summarize_artifacts_written(info['artifacts_written'])
            if artifact_note:
                message += f" {artifact_note}"

    return [{'timestamp': timestamp, 'message': message, 'query': str(num), 'kind': 'query_event'}]


# --- round_activity.jsonl: optional round-level event stream -------------

ROUND_ACTIVITY_JSONL_MAX_LINES = 500

_ROUND_ACTIVITY_EVENT_LABELS = {
    'round_started': 'Round started.',
    'session_reset': 'Session reset.',
    'round_report_started': 'Report writing started.',
    'round_report_saved': 'Round report saved.',
    'memory_report_saved': 'Memory report saved.',
}


def _read_round_activity_jsonl(round_dir: Path, max_lines: int = ROUND_ACTIVITY_JSONL_MAX_LINES) -> list[dict]:
    """
    Safely parse checkpoints/round_N/round_activity.jsonl, if present. Caps
    the number of lines read and skips malformed lines individually rather
    than failing the whole file.
    """
    path = round_dir / "round_activity.jsonl"
    if not path.exists():
        return []
    events: list[dict] = []
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for i, raw_line in enumerate(f):
                if i >= max_lines:
                    break
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    events.append(obj)
    except OSError:
        return []
    return events


def _build_round_activity_jsonl_events(round_dir: Path, max_events: int = 150) -> list[dict]:
    """
    Turn round_activity.jsonl entries into Activity rows for a known,
    deliberately small set of round-level lifecycle event_types.
    """
    events: list[dict] = []
    seen: set[tuple] = set()
    for obj in _read_round_activity_jsonl(round_dir, max_lines=max_events * 2):
        event_type = obj.get('event_type')
        ts = obj.get('timestamp')
        ts = ts.strip() if isinstance(ts, str) and ts.strip() else None
        query_name = obj.get('query_name')
        query_name = query_name.strip() if isinstance(query_name, str) and query_name.strip() else None
        message_field = obj.get('message')
        message_field = message_field.strip() if isinstance(message_field, str) else None

        if event_type in _ROUND_ACTIVITY_EVENT_LABELS:
            message = _ROUND_ACTIVITY_EVENT_LABELS[event_type]
        elif (
            event_type == 'query_step' and query_name and message_field
            and _is_meaningful_description_candidate(message_field)
        ):
            message = f"{query_name}: {message_field}"
        else:
            continue

        dedup_key = (ts, query_name, event_type, message)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        query_num = None
        if query_name:
            suffix = query_name.split('query_', 1)[-1]
            if suffix.isdigit():
                query_num = str(int(suffix))

        events.append({'timestamp': ts, 'message': message, 'query': query_num, 'kind': 'round_activity_jsonl'})
        if len(events) >= max_events:
            break
    return events


def _scan_log_for_query_timing(log_text: str | None) -> dict[int, dict]:
    """
    Single forward pass over the log producing best-effort timing evidence
    per numeric query index: {'started_at', 'completed_at', 'had_checkpoint',
    'had_markdown'}. This is the sole source of "WHEN" for query events; the
    query directories/files themselves (see _infer_query_description) supply
    "WHAT" -- the query-centric builder merges both.
    """
    timing: dict[int, dict] = {}

    def _entry(num: int) -> dict:
        return timing.setdefault(num, {
            'started_at': None, 'completed_at': None,
            'had_checkpoint': False, 'had_markdown': False,
        })

    if not log_text:
        return timing

    current_query: int | None = None
    query_mention_re = re.compile(r'\bquery[_\s]?0*(\d+)\b', re.IGNORECASE)
    started_re = re.compile(r'\bquery[_\s]?0*(\d+)\b[^\n]{0,40}?\b(?:started|beginning|launching)\b', re.IGNORECASE)
    completed_re = re.compile(r'\bquery[_\s]?0*(\d+)\b[^\n]{0,40}?\b(?:completed|finished|done)\b', re.IGNORECASE)
    checkpoint_re = re.compile(r'\bcheckpoint\b[^\n]{0,30}?\bsaved\b', re.IGNORECASE)
    markdown_re = re.compile(r'\b(?:query\s+)?markdown\b[^\n]{0,20}?\bsaved\b', re.IGNORECASE)

    for ts, body in _split_log_into_entries(log_text):
        first_line, _, remainder = body.partition('\n')
        first_line = first_line.strip()

        action_match = _ACTION_TAG_RE.match(first_line)
        if action_match:
            action = action_match.group(1)
            if action in ('analysis_start', 'analysis_end'):
                details = {}
                for line in remainder.splitlines():
                    if '=' in line:
                        key, _, value = line.partition('=')
                        details[key.strip()] = value.strip()
                idx_str = details.get('analysis_idx')
                if idx_str and idx_str.isdigit():
                    num = int(idx_str)
                    entry = _entry(num)
                    if action == 'analysis_start' and entry['started_at'] is None:
                        entry['started_at'] = ts
                    elif action == 'analysis_end':
                        entry['completed_at'] = ts
                    current_query = num
                continue
            # Other action tags carry no query-timing signal here.
            continue

        start_match = started_re.search(body)
        if start_match:
            num = int(start_match.group(1))
            entry = _entry(num)
            if entry['started_at'] is None:
                entry['started_at'] = ts
            current_query = num
            continue

        complete_match = completed_re.search(body)
        if complete_match:
            num = int(complete_match.group(1))
            entry = _entry(num)
            entry['completed_at'] = ts
            current_query = num
            continue

        has_checkpoint = bool(checkpoint_re.search(body))
        has_markdown = bool(markdown_re.search(body))
        if has_checkpoint or has_markdown:
            mention = query_mention_re.search(body)
            target = int(mention.group(1)) if mention else current_query
            if target is not None:
                entry = _entry(target)
                if has_checkpoint:
                    entry['had_checkpoint'] = True
                if has_markdown:
                    entry['had_markdown'] = True
                if entry['completed_at'] is None:
                    entry['completed_at'] = ts

    return timing


def _resolve_query_timestamps(
    num: int,
    qd: Path,
    log_timing: dict[int, dict],
    has_round_report: bool,
    round_report_path: Path,
    has_later_query: bool,
) -> dict:
    """
    Apply the documented started/completed timestamp source-priority chains:

    started_at: (1) explicit log "query_N started" marker -> (2) first log
    mention of query_N at all [folded into (1) by the scanner, which records
    the earliest sighting when no explicit "started" keyword exists] ->
    (3) query directory/query_N.md mtime -> (4) None.

    completed_at: (1) explicit log "completed"/checkpoint-saved/markdown-saved
    marker for query_N -> (2) if a later query exists, this query's
    directory/.md mtime as an approximation -> (3) if round_report.md exists,
    this query's mtime if it's not implausibly *after* the report was
    written -> (4) None.

    File mtimes are always fallback APPROXIMATIONS, never authoritative.
    """
    entry = log_timing.get(num, {})
    started_at = entry.get('started_at')
    started_source = 'log' if started_at else None

    if not started_at:
        mtime = _safe_mtime(qd) or _safe_mtime(qd / f"{qd.name}.md")
        if mtime is not None:
            started_at = _format_mtime(mtime)
            started_source = 'mtime_fallback'

    completed_at = entry.get('completed_at')
    completed_source = 'log' if completed_at else None

    if not completed_at and has_later_query:
        mtime = _safe_mtime(qd / f"{qd.name}.md") or _safe_mtime(qd)
        if mtime is not None:
            completed_at = _format_mtime(mtime)
            completed_source = 'mtime_fallback'

    if not completed_at and has_round_report:
        qd_mtime = _safe_mtime(qd / f"{qd.name}.md") or _safe_mtime(qd)
        report_mtime = _safe_mtime(round_report_path)
        if qd_mtime is not None and (report_mtime is None or qd_mtime <= report_mtime):
            completed_at = _format_mtime(qd_mtime)
            completed_source = 'mtime_fallback'

    return {
        'started_at': started_at,
        'started_source': started_source,
        'completed_at': completed_at,
        'completed_source': completed_source,
        'had_checkpoint': bool(entry.get('had_checkpoint')),
        'had_markdown': bool(entry.get('had_markdown')),
    }


def _build_query_activity_events(round_dir: Path, log_text: str | None) -> list[dict]:
    """
    Query-centric event builder -- the core of the Activity feed.

    Principle: query_NNN folders/query_NNN.md tell us WHAT the agent was
    doing; logs and file mtimes tell us WHEN. This builds one rich event
    object per numeric query_* dir (query_name, query_num, status,
    description, started_at, completed_at, started_source, completed_source,
    had_checkpoint, had_markdown) and flattens it into one or two
    scientist-friendly {timestamp, message, query} rows -- never inventing a
    scientific objective when none has been written yet. Non-numeric
    query_* dirs (no reliable ordering) are intentionally skipped here; the
    Round Directory panel is the right place to surface those, not this feed.
    """
    query_dirs = _list_query_dirs(round_dir)
    numeric_dirs = []
    for qd in query_dirs:
        suffix = qd.name.split("query_", 1)[-1]
        if suffix.isdigit():
            numeric_dirs.append((int(suffix), qd))
    if not numeric_dirs:
        return []
    numeric_dirs.sort(key=lambda item: item[0])

    has_round_report = (round_dir / "round_report.md").exists()
    round_report_path = round_dir / "round_report.md"
    # Note: this intentionally does NOT call _infer_query_statuses (the
    # function Round Directory uses) -- that one only recognizes plain-text
    # "query_N ..." log mentions, not the documented analysis_idx=N
    # ACTION-tag format _scan_log_for_query_timing already understands more
    # completely. Status here is instead derived directly from the same
    # timing evidence used for timestamps (see below), which is a strict
    # superset of what _infer_query_statuses can detect. Round Directory's
    # own status display is untouched.
    log_timing = _scan_log_for_query_timing(log_text)

    # Tracks how many times each exact description string has already been
    # accepted across queries in this round -- once something has repeated
    # twice, it's treated as boilerplate for any further query and excluded
    # so _infer_query_description keeps searching (or falls back) instead of
    # parroting the same templated text for every query.
    description_counts: dict[str, int] = {}

    events: list[dict] = []
    for i, (num, qd) in enumerate(numeric_dirs):
        # query_status.json (the new structured instrumentation) is the
        # primary source of truth when present -- it fully supersedes the
        # legacy log/mtime/markdown inference below for that query.
        status_data = _read_query_status(qd)
        if status_data is not None:
            events.extend(_build_query_status_events(qd, num, status_data, description_counts))
            continue

        has_later_query = i < len(numeric_dirs) - 1
        timing = _resolve_query_timestamps(
            num, qd, log_timing, has_round_report, round_report_path, has_later_query,
        )
        # Status rules (per spec): round_report.md present -> all completed;
        # a later query existing -> this one completed; explicit log
        # completion evidence -> completed; otherwise (latest query, no
        # evidence) -> in_progress. This is checked directly rather than via
        # timing['completed_at'] because that value can legitimately be None
        # even when status is 'completed' -- e.g. round_report.md exists but
        # the mtime fallback was judged unsafe (qd_mtime after report_mtime);
        # status must not depend on a timestamp being available.
        explicit_log_completion = log_timing.get(num, {}).get('completed_at') is not None
        status = 'completed' if (has_round_report or has_later_query or explicit_log_completion) else 'in_progress'

        repeated_boilerplate = {text for text, count in description_counts.items() if count >= 2}
        description = _get_query_summary(qd)
        if description in repeated_boilerplate:
            description = _QUERY_DESCRIPTION_FALLBACK
        if description == _QUERY_DESCRIPTION_FALLBACK:
            description = None
        else:
            description_counts[description] = description_counts.get(description, 0) + 1
        objective_text = description or _QUERY_STATUS_FALLBACK_TEXT

        if timing['had_checkpoint'] and timing['had_markdown']:
            saved_suffix = "; query markdown and checkpoint were saved."
        elif timing['had_markdown']:
            saved_suffix = "; query markdown was also saved."
        elif timing['had_checkpoint']:
            saved_suffix = "; checkpoint was also saved."
        else:
            saved_suffix = ""

        started_at = timing['started_at']
        completed_at = timing['completed_at']

        if status == 'in_progress':
            events.append({
                'timestamp': started_at,
                'message': f"{qd.name} started: {objective_text}",
                'query': str(num),
                'kind': 'query_event',
            })
            continue

        # status is 'completed' (the only other status numeric dirs get).
        # Only show a separate "started" row if start/completion are both
        # known AND meaningfully distinct -- if they're the same instant (or
        # start is unknown), a single completion row is clearer per spec.
        if started_at and completed_at and started_at != completed_at:
            events.append({
                'timestamp': started_at,
                'message': f"{qd.name} started: {objective_text}",
                'query': str(num),
                'kind': 'query_event',
            })

        events.append({
            'timestamp': completed_at or started_at,
            'message': f"{qd.name} completed: {objective_text}{saved_suffix}",
            'query': str(num),
            'kind': 'query_event',
        })

    return events


def _extract_round_activity_events(
    log_text: str | None,
    round_dir: Path | None = None,
    max_events: int = 150,
) -> list[dict]:
    """
    Deterministic, domain-general round Activity feed -- never an LLM call,
    never project/dataset-specific wording.

    Three sources are merged:
      1. Query-centric events (_build_query_activity_events): prefers each
         query's query_status.json (the new structured instrumentation) when
         present; falls back to query_NNN.md/logs/mtimes for older rounds.
         Works even with no log at all, which is what lets this feed show
         *something* useful immediately.
      2. round_activity.jsonl (_build_round_activity_jsonl_events): optional
         round-level lifecycle events (round started, report lifecycle),
         deduplicated against query-centric/log-derived events below.
      3. Non-query log events: only [ACTION]/[ERROR]-tagged entries (Logger's
         format) and the run-start banner become events; [PROMPT]/[RESPONSE]/
         [CODE] entries, raw query/checkpoint/markdown log lines, and known
         operational boilerplate (token estimates, sandbox/safety checks,
         directory dumps, etc.) are all excluded by construction so they can
         never dominate the feed.
    All three are merged in chronological order, then deduplicated/merged
    (same-timestamp collisions, consecutive duplicates) and capped.
    """
    events: list[dict] = []

    if round_dir is not None:
        events.extend(_build_query_activity_events(round_dir, log_text))
        events.extend(_build_round_activity_jsonl_events(round_dir, max_events=max_events))

    if log_text:
        for ts, body in _split_log_into_entries(log_text):
            if _ACTIVITY_NOISE_RE.search(body):
                continue

            first_line, _, remainder = body.partition('\n')
            first_line = first_line.strip()

            # The run-start banner is multi-line (Logger writes a leading blank
            # line plus "="*80 separators around it), so RUN START often isn't
            # on the entry's first physical line -- search the whole body.
            run_start = _RUN_START_RE.search(body)
            if run_start:
                events.append({
                    'timestamp': ts,
                    'message': f"DiscoveryAgent run started (analysis: {run_start.group(1).strip()}).",
                    'query': None,
                    'kind': 'run_start',
                })
                continue

            action_match = _ACTION_TAG_RE.match(first_line)
            if action_match:
                action = action_match.group(1)
                action_lower = action.lower()
                if (
                    action in ('analysis_start', 'analysis_end', 'iteration')
                    or action_lower in _ACTIVITY_NOISE_ACTION_NAMES
                    or any(substr in action_lower for substr in _ACTIVITY_NOISE_ACTION_SUBSTRINGS)
                ):
                    # analysis_start/analysis_end are fully handled by the
                    # query-centric builder above; iteration is too granular.
                    continue
                message, query, kind = _format_action_event(action, remainder)
                if message:
                    events.append({'timestamp': ts, 'message': message, 'query': query, 'kind': kind})
                continue

            if first_line.startswith('[ERROR]'):
                error_text = remainder.splitlines()[0].strip() if remainder.strip() else first_line
                events.append({
                    'timestamp': ts,
                    'message': f"Error encountered: {error_text[:200]}",
                    'query': None,
                    'kind': 'error',
                })
                continue

            if first_line.startswith('[PROMPT]') or first_line.startswith('[RESPONSE]') or first_line.startswith('[CODE]'):
                # Deliberately excluded: full prompts/model output/code blocks are noise here.
                continue

            for pattern, kind in _GENERIC_ACTIVITY_PATTERNS:
                match = pattern.search(body)
                if match:
                    message = _GENERIC_ACTIVITY_LABELS.get(kind, 'Round activity detected.')
                    query = None
                    if kind in ('deep_research_started', 'deep_research_completed'):
                        # Upgrade to a query-tied message when a query
                        # number appears nearby -- still suppressed for
                        # ungrounded mentions only if neither verb matched
                        # at all (handled by simply not matching above).
                        query_match = _DEEP_RESEARCH_QUERY_RE.search(body)
                        if query_match:
                            query = query_match.group(1)
                            verb = 'started' if kind == 'deep_research_started' else 'completed'
                            message = f"query_{int(query):03d} literature/deep-research step {verb}."
                    events.append({
                        'timestamp': ts,
                        'message': message,
                        'query': query,
                        'kind': kind,
                    })
                    break

    # Merge both sources into chronological order (stable sort preserves
    # relative order for equal/missing timestamps) before the cleanup pass.
    # Sort on normalized timestamps: raw values mix ISO 'T' separators
    # (query_status.json) with spaces (log/mtime), which compare wrongly.
    events.sort(key=lambda e: (
        e['timestamp'] is None,
        _normalize_activity_timestamp(e['timestamp']) or '',
    ))
    return _dedupe_and_merge_activity_events(events, max_events)


def _dedupe_and_merge_activity_events(raw: list[dict], max_events: int) -> list[dict]:
    """
    Deterministic cleanup pass: collapse consecutive identical messages,
    keep only the single most-informative event per shared timestamp (merging
    a same-timestamp checkpoint_saved into the kept event's message rather
    than dropping it silently), then cap to the newest max_events.
    """
    # 1) Collapse consecutive duplicate messages (e.g. repeated "Checkpoint
    #    saved." lines) down to one, keeping the most recent timestamp.
    collapsed: list[dict] = []
    for event in raw:
        if collapsed and collapsed[-1]['message'] == event['message']:
            collapsed[-1] = event
        else:
            collapsed.append(event)

    # 2) Group by identical (timestamp, query); within a group, keep the
    #    single most informative event (lowest priority number) and fold a
    #    same-group checkpoint_saved into its message instead of showing it
    #    separately. Grouping by query too (not just timestamp) matters now
    #    that query timestamps can fall back to second-resolution file
    #    mtimes -- two *different* queries can legitimately share a
    #    timestamp and must both still be shown, not collapsed into one.
    merged: list[dict] = []
    i = 0
    while i < len(collapsed):
        ts = collapsed[i]['timestamp']
        query = collapsed[i]['query']
        group = [collapsed[i]]
        j = i + 1
        while (
            j < len(collapsed) and ts is not None
            and collapsed[j]['timestamp'] == ts and collapsed[j]['query'] == query
        ):
            group.append(collapsed[j])
            j += 1

        if len(group) == 1:
            merged.append(group[0])
        else:
            group.sort(key=lambda e: _EVENT_KIND_PRIORITY.get(e.get('kind', 'action_generic'), 50))
            best = dict(group[0])
            kinds_in_rest = {e.get('kind') for e in group[1:]}
            had_checkpoint = 'checkpoint_saved' in kinds_in_rest and best.get('kind') != 'checkpoint_saved'
            had_markdown = 'markdown_saved' in kinds_in_rest and best.get('kind') != 'markdown_saved'
            if had_checkpoint and had_markdown:
                best['message'] = best['message'].rstrip('.') + "; query markdown and checkpoint were saved."
            elif had_markdown:
                best['message'] = best['message'].rstrip('.') + "; query markdown was also saved."
            elif had_checkpoint and 'checkpoint' not in best['message'].lower():
                best['message'] = best['message'].rstrip('.') + "; checkpoint was also saved."
            merged.append(best)
        i = j

    final = [{'timestamp': e['timestamp'], 'message': e['message'], 'query': e['query']} for e in merged]
    return final[-max_events:]


# --- DiscoveryAgent Activity timeline (normalized, collapsible-friendly) ---
# Deterministic, regex/filesystem-based, never an LLM call -- this is a
# second, richer view over the exact same evidence _extract_round_activity_events
# already reads (query_status.json, round_activity.jsonl, live_round.log/
# round.log, query markdown/JSON). It groups everything by query into one
# parent row per query (kind='query') with a short summary plus a
# director_activity list -- what the Director called/did inside this query,
# from structured evidence only (see _build_director_activity) -- and keeps
# round-level events (kind='round_event') as simple rows. /api/round_activity
# returns this alongside the legacy flat 'events' list so existing consumers
# are unaffected.

_ISO_TZ_SUFFIX_RE = re.compile(r'(?:Z|[+-]\d{2}:?\d{2})$')
_SPACE_TIMESTAMP_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})')


def _normalize_activity_timestamp(raw: str | float | None) -> str | None:
    """
    Best-effort reformat of a timestamp already produced elsewhere in this
    file (log-derived "%Y-%m-%d %H:%M:%S" strings, mtime fallbacks, or
    whatever a query_status.json/round_activity.jsonl writer used) into an
    ISO-8601-shaped string for the frontend. This is a syntactic reformat
    only -- no timezone is actually known here, so a trailing 'Z' is added
    rather than a real UTC conversion. Never raises; passes through anything
    that doesn't look like a recognizable timestamp.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.utcfromtimestamp(raw).strftime('%Y-%m-%dT%H:%M:%SZ')
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if _ISO_TZ_SUFFIX_RE.search(text):
        # Already carries an explicit timezone -- never overwrite real tz
        # info with an assumed 'Z'; just normalize a space separator to 'T'.
        return text[:10] + 'T' + text[11:] if len(text) > 10 and text[10] == ' ' else text
    full_match = _SPACE_TIMESTAMP_RE.match(text)
    if full_match and full_match.end() == len(text):
        return f"{full_match.group(1)}T{full_match.group(2)}Z"
    if 'T' in text:
        return text + 'Z'
    return text


_QUERY_ID_NUM_RE = re.compile(r'\bq(?:uery)?[\s_-]?0*(\d+)\b', re.IGNORECASE)


def _canonical_query_id(raw: str | int | None) -> str | None:
    """
    Normalize any generic query reference -- 'query_001', 'query_1',
    'query 1', 'q1', a bare query number, a query_* directory name, an int,
    ... -- to a canonical 'query_NNN' id, so mentions of the same query from
    different sources (directory name, round_activity.jsonl query_name, the
    legacy flat event pipeline's bare-numeric-string query field, log text)
    group together. Returns None when no query number can be found; never
    hardcodes a specific query number/name.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    match = _QUERY_ID_NUM_RE.search(text)
    if match:
        return f"query_{int(match.group(1)):03d}"
    if text.isdigit():
        return f"query_{int(text):03d}"
    return None


_GENERIC_QUERY_METADATA_NAMES = ('query.json', 'metadata.json', 'status.json')


def _read_generic_query_metadata(qd: Path) -> dict | None:
    """
    Read the first present top-level query-metadata JSON file, checked in
    priority order: {qname}.json, query.json, metadata.json, status.json.
    query_status.json itself is handled separately (_read_query_status) and
    is not re-read here. Never raises; skips malformed/non-object files.
    """
    for name in (f"{qd.name}.json",) + _GENERIC_QUERY_METADATA_NAMES:
        path = qd / name
        if not path.exists():
            continue
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _extract_title_from_metadata(data: dict | None) -> str | None:
    """Pull a clean title/objective/summary/description string out of a generic query metadata dict, if any."""
    if not data:
        return None
    for field in ('title', 'objective', 'summary', 'description'):
        val = data.get(field)
        if isinstance(val, str):
            val = val.strip()
            if val and _is_meaningful_description_candidate(val):
                return val
    return None


def _humanize_current_step(step: str | None) -> str | None:
    """
    Present-tense label for a query_status.json current_step, e.g.
    'writing_query_report' -> 'Writing query report.'. Reuses the existing
    _QUERY_STEP_LABELS phrasing where known; otherwise generically
    de-slugifies the step name. Used only as a parent-summary fallback (see
    _build_query_timeline_summary), never for director_activity entries.
    """
    if not isinstance(step, str) or not step.strip():
        return None
    label = _QUERY_STEP_LABELS.get(step.strip()) or step.strip().replace('_', ' ')
    label = label.strip().rstrip('.')
    if not label:
        return None
    return label[0].upper() + label[1:] + '.'


# Substrings that mark an artifacts_written entry as the boilerplate every
# query writes (its own report markdown, its checkpoint) -- never worth
# surfacing as a director-activity entry because it's true for virtually
# every query.
_STANDARD_ARTIFACT_MARKERS = ('checkpoint',)


def _has_notable_query_artifacts(info: dict | None, qd: Path) -> bool:
    """
    True only when artifacts_written names a file beyond this query's own
    standard report markdown + checkpoint -- i.e. genuinely query-specific
    evidence (e.g. a distinct output file), not the boilerplate nearly every
    query writes. Gates the one artifact-kind director_activity entry that's
    ever synthesized from mere file presence (see _build_director_activity),
    so it appears only for the minority of queries where it's actually
    informative, never as a blanket completed-query fact.
    """
    if not info:
        return False
    own_markdown = f"{qd.name}.md".lower()
    for item in info.get('artifacts_written') or []:
        if not isinstance(item, str):
            continue
        lowered = item.strip().lower()
        if not lowered or lowered == own_markdown:
            continue
        if any(marker in lowered for marker in _STANDARD_ARTIFACT_MARKERS):
            continue
        return True
    return False


# --- director_activity: what the Director called/did inside a query -----
# Answers one narrow, well-evidenced question: which subagent did the
# Director invoke for this query, and what did it do? Every entry comes from
# a genuinely structured signal -- never a keyword match against free prose
# or a markdown heading.
#
# Evidence sources, in the order _build_director_activity checks them:
#   1. round_activity.jsonl entries for this query -- both a future explicit
#      'subagent'/'agent' field on subagent_called/subagent_completed/
#      subagent_failed events, and today's already-emitted controlled
#      event_type values (code_execution, deep_research, ...; see
#      _SUBAGENT_LABELS) via AIR/utils/progress_recorder.py's
#      write_round_event/write_query_step.
#   2. query_status.json's current_step -- ONLY while the query is actively
#      running (a snapshot of current, not historical, activity; showing it
#      for an already-completed/failed query would misrepresent stale state
#      as a real recorded action).
#   3. artifacts_written, gated by _has_notable_query_artifacts (explicit,
#      structured field; only when it names something beyond the standard
#      report+checkpoint every query writes).
#   4. status_bucket == 'failed' (an explicit, structured status value).
#
# The DiscoveryAgent backend (discoveryagent/AIR, a separate repo from this
# UI) does not yet emit per-step subagent_called/subagent_completed events;
# the natural insertion point for that is AIR/director_workflow.py's
# execute_agent(), alongside its existing write_query_step calls. Until then,
# most director_activity comes from today's coarser controlled event_type/
# current_step values, and old rounds with neither may correctly end up
# with director_activity: [].
_SUBAGENT_LABELS: dict[str, tuple[str, str]] = {
    # identifier (current_step, event_type, or an explicit subagent/agent
    # field value) -> (display label, generic default summary text used
    # only when the event/step itself carries no summary of its own).
    'execution': ('Execution Agent', 'Ran code to test the query.'),
    'code_execution': ('Execution Agent', 'Ran code to test the query.'),
    'executing_code': ('Execution Agent', 'Ran code to test the query.'),
    'execution_agent': ('Execution Agent', 'Ran code to test the query.'),
    'analyzing_results': ('Execution Agent', 'Analyzed computational results.'),
    'deep_research': ('Deep Research Agent', 'Reviewed external literature relevant to this query.'),
    'literature_review': ('Deep Research Agent', 'Reviewed external literature relevant to this query.'),
    'literature': ('Deep Research Agent', 'Reviewed external literature relevant to this query.'),
    'running_literature_check': ('Deep Research Agent', 'Reviewed external literature relevant to this query.'),
    'validation': ('Validation Agent', 'Ran a validation or cross-check.'),
    'cross_check': ('Validation Agent', 'Ran a validation or cross-check.'),
    'synthesis': ('Synthesis/Report Writer', 'Updated the query report.'),
    'report_update': ('Synthesis/Report Writer', 'Updated the query report.'),
    'report_writer': ('Synthesis/Report Writer', 'Updated the query report.'),
    'writing_query_report': ('Synthesis/Report Writer', 'Updated the query report.'),
}

_SUBAGENT_EVENT_TYPES = frozenset({'subagent_called', 'subagent_completed', 'subagent_failed'})
_DIRECTOR_ENTRY_STATUSES = frozenset({'completed', 'running', 'failed', 'started'})

_QUERY_ACTIVITY_TYPES: dict[str, tuple[str, str, str]] = {
    'planning': ('research_planning', 'Research planning', 'Planned the work for this query.'),
    'running_literature_check': ('literature_search', 'Literature search', 'Searched relevant publications and reviewed supporting evidence.'),
    'deep_research': ('literature_search', 'Literature search', 'Searched relevant publications and reviewed supporting evidence.'),
    'literature_review': ('literature_review', 'Literature review', 'Reviewed relevant publications and supporting evidence.'),
    'executing_code': ('computational_analysis', 'Computational analysis', 'Ran a computational analysis for this query.'),
    'execution': ('computational_analysis', 'Computational analysis', 'Ran a computational analysis for this query.'),
    'code_execution': ('computational_analysis', 'Computational analysis', 'Ran a computational analysis for this query.'),
    'analyzing_results': ('result_validation', 'Result validation', 'Reviewed and checked the analysis results.'),
    'validation': ('result_validation', 'Result validation', 'Checked the results and supporting evidence.'),
    'cross_check': ('result_validation', 'Result validation', 'Cross-checked the results and supporting evidence.'),
    'synthesis': ('evidence_synthesis', 'Evidence synthesis', 'Synthesized the findings and supporting evidence.'),
    'writing_query_report': ('report_writing', 'Report writing', 'Documented the query findings and supporting evidence.'),
    'report_update': ('report_writing', 'Report writing', 'Documented the query findings and supporting evidence.'),
    'report_writer': ('report_writing', 'Report writing', 'Documented the query findings and supporting evidence.'),
    'report_verification': ('report_verification', 'Report verification', 'Verified the report content and referenced evidence.'),
    'artifact_saved': ('figure_generation', 'Figure generation', 'Saved a trusted research artifact from the query.'),
}

_QUERY_ACTIVITY_PRECEDENCE = {
    'report_verification': 90, 'evidence_synthesis': 80, 'report_writing': 70,
    'result_validation': 60, 'figure_generation': 55, 'computational_analysis': 50,
    'literature_review': 45, 'literature_search': 40, 'research_planning': 10,
}

_HELPER_AGENT_ACTION_RE = re.compile(r'^\[ACTION\]\s+helper_agent_called\b', re.I)
_CONTROLLED_DETAIL_RE = re.compile(r'^(tag|agent_type|query_num)=([^\r\n]+)$', re.M)


def _resolve_subagent_label(identifier: str | None) -> tuple[str, str] | None:
    """Map a subagent/action identifier to (display label, generic default summary) via _SUBAGENT_LABELS, or None if unrecognized."""
    if not identifier or not isinstance(identifier, str):
        return None
    return _SUBAGENT_LABELS.get(identifier.strip().lower())


def _helper_calls_by_query(log_text: str | None) -> dict[str, list[dict]]:
    """Extract genuine Director helper calls from the logger's controlled action."""
    grouped: dict[str, list[dict]] = {}
    if not log_text:
        return grouped
    for timestamp, body in _split_log_into_entries(log_text):
        if not _HELPER_AGENT_ACTION_RE.match(body.partition('\n')[0].strip()):
            continue
        fields = {key: value.strip() for key, value in _CONTROLLED_DETAIL_RE.findall(body)}
        query_id = _canonical_query_id(fields.get('query_num'))
        resolved = _resolve_subagent_label(fields.get('tag'))
        if not query_id or not resolved:
            continue
        label, summary = resolved
        grouped.setdefault(query_id, []).append(_make_director_entry(
            'subagent', fields.get('tag'), label, summary, 'started', timestamp,
        ))
    return grouped


def _clean_event_summary_text(val) -> str | None:
    """A short structured event's own summary/message text, if it's a non-empty string that isn't itself a raw phase token."""
    if not isinstance(val, str):
        return None
    val = val.strip()
    if not val or _looks_like_raw_phase_token(val):
        return None
    return val


def _make_director_entry(
    kind: str, subagent: str | None, label: str, summary: str, status: str, timestamp,
) -> dict:
    return {
        'kind': kind,
        'subagent': subagent,
        'label': label,
        'summary': summary,
        'status': status,
        'timestamp': _normalize_activity_timestamp(timestamp),
    }


def _director_entries_from_jsonl(jsonl_objs: list[dict]) -> list[dict]:
    """
    Build director_activity entries from this query's round_activity.jsonl
    entries. Every entry here is keyed off a structured event_type (a closed,
    machine-emitted vocabulary) -- an event's own free-text 'summary'/
    'message' field is only ever used to enrich the bullet text of an
    already-confirmed structured event, never to decide whether one exists.
    """
    entries: list[dict] = []
    for obj in jsonl_objs:
        event_type = obj.get('event_type')
        event_type = event_type.strip().lower() if isinstance(event_type, str) else None
        if not event_type:
            continue
        own_summary = _clean_event_summary_text(obj.get('summary') or obj.get('message'))
        timestamp = obj.get('timestamp')

        if event_type == 'error':
            entries.append(_make_director_entry(
                'error', None, 'Error',
                own_summary or 'Encountered an error during query execution.', 'failed', timestamp,
            ))
            continue

        if event_type == 'artifact_saved':
            entries.append(_make_director_entry(
                'artifact', None, 'Artifacts',
                own_summary or 'Saved additional query-specific artifacts.', 'completed', timestamp,
            ))
            continue

        if event_type in _SUBAGENT_EVENT_TYPES:
            resolved = _resolve_subagent_label(obj.get('subagent') or obj.get('agent'))
        else:
            resolved = _resolve_subagent_label(event_type)
        if not resolved:
            continue
        label, default_summary = resolved

        status = obj.get('status')
        if not isinstance(status, str) or status.strip().lower() not in _DIRECTOR_ENTRY_STATUSES:
            if event_type.endswith('_failed'):
                status = 'failed'
            elif event_type.endswith('_called'):
                status = 'started'
            else:
                status = 'completed'
        else:
            status = status.strip().lower()

        subagent_id = obj.get('subagent') or obj.get('agent') or event_type
        entries.append(_make_director_entry(
            'subagent', subagent_id if isinstance(subagent_id, str) else event_type,
            label, own_summary or default_summary, status, timestamp,
        ))
    return entries


def _director_entry_from_current_step(info: dict | None, status_bucket: str) -> dict | None:
    """
    A single director_activity entry reflecting the query's CURRENT phase --
    only while it's actively running, never for a completed/failed query
    (current_step is a snapshot of the last write, not a history; showing it
    for a finished query would misrepresent stale state as a real action, and
    would also put the same "wrote the report" entry on nearly every
    completed query, which is exactly the boilerplate this replaces).
    """
    if not info or status_bucket in _TERMINAL_QUERY_STATUSES:
        return None
    current_step = info.get('current_step')
    resolved = _resolve_subagent_label(current_step) if isinstance(current_step, str) else None
    if not resolved:
        return None
    label, default_summary = resolved
    return _make_director_entry(
        'subagent', current_step.strip().lower(), label, default_summary, 'running', info.get('updated_at'),
    )


def _build_director_activity(
    info: dict | None,
    jsonl_objs: list[dict],
    has_notable_artifacts: bool,
    status_bucket: str,
    helper_calls: list[dict] = (),
) -> list[dict]:
    """
    Build this query's director_activity list -- what the Director called or
    did inside this query -- deterministically, from reasonably structured
    evidence only (round_activity.jsonl event_type/subagent fields, the
    query's own live current_step, and explicit artifacts_written/status
    fields). Deliberately never scans free-text prose or markdown headings
    for keywords -- see the module comment above _SUBAGENT_LABELS. Returns
    [] when no such evidence exists, which is expected and acceptable for
    old rounds; never invents an action. Deduplicated by (kind, label) and
    capped to 5 entries.
    """
    seen_keys: list[tuple[str, str]] = []
    entries: list[dict] = []

    def _add(entry: dict) -> None:
        key = (entry['kind'], entry['label'])
        if key not in seen_keys:
            seen_keys.append(key)
            entries.append(entry)

    for entry in _director_entries_from_jsonl(jsonl_objs):
        _add(entry)

    for entry in helper_calls:
        call = dict(entry)
        if call.get('status') == 'started':
            call['status'] = 'completed' if status_bucket == 'completed' else (
                'failed' if status_bucket == 'failed' else 'running'
            )
        _add(call)

    live_entry = _director_entry_from_current_step(info, status_bucket)
    if live_entry:
        _add(live_entry)

    if has_notable_artifacts:
        _add(_make_director_entry(
            'artifact', None, 'Artifacts', 'Saved additional query-specific artifacts.',
            'completed', (info or {}).get('completed_at'),
        ))

    if status_bucket == 'failed':
        error_text = _clean_event_summary_text((info or {}).get('error'))
        _add(_make_director_entry(
            'error', None, 'Error', error_text or 'Encountered an error during query execution.',
            'failed', (info or {}).get('completed_at'),
        ))

    return entries[:5]


def _normalized_query_steps(
    info: dict | None,
    jsonl_objs: list[dict],
    director_activity: list[dict],
    status_bucket: str,
) -> list[dict]:
    """Group controlled phase events into stable, ordered activity steps."""
    candidates: list[tuple[str, str | None, str]] = []
    for obj in jsonl_objs:
        token = obj.get('event_type')
        token = token.strip().lower() if isinstance(token, str) else ''
        if token == 'query_step':
            message = obj.get('message')
            token = message.strip().lower() if isinstance(message, str) else ''
        if token in _QUERY_ACTIVITY_TYPES:
            candidates.append((token, obj.get('timestamp'), 'event'))
    if info and status_bucket not in _TERMINAL_QUERY_STATUSES:
        token = info.get('current_step')
        if isinstance(token, str) and token.strip().lower() in _QUERY_ACTIVITY_TYPES:
            candidates.append((token.strip().lower(), info.get('updated_at'), 'snapshot'))
    for entry in director_activity:
        token = entry.get('subagent')
        if isinstance(token, str) and token.strip().lower() in _QUERY_ACTIVITY_TYPES:
            candidates.append((token.strip().lower(), entry.get('timestamp'), entry.get('kind', 'event')))

    steps: list[dict] = []
    positions: dict[str, int] = {}
    for token, timestamp, evidence_kind in candidates:
        kind, label, description = _QUERY_ACTIVITY_TYPES[token]
        if kind in positions:
            if evidence_kind == 'snapshot':
                steps[positions[kind]]['status'] = 'running'
            continue
        positions[kind] = len(steps)
        steps.append({
            'id': f"step-{kind}",
            'kind': kind,
            'label': label,
            'description': description,
            'status': 'running' if evidence_kind == 'snapshot' else 'completed',
            'sequence': len(steps) + 1,
            'artifact_refs': [],
            'timestamp': _normalize_activity_timestamp(timestamp),
        })
    return steps


def _select_query_activity(steps: list[dict], status_bucket: str) -> tuple[str, str]:
    """Select collapsed copy solely from normalized operational evidence."""
    if not steps:
        return 'Research investigation', 'Investigating the current research objective.'
    if status_bucket not in _TERMINAL_QUERY_STATUSES:
        chosen = next((step for step in reversed(steps) if step['status'] == 'running'), steps[-1])
    else:
        chosen = max(
            steps,
            key=lambda step: (_QUERY_ACTIVITY_PRECEDENCE.get(step['kind'], 0), step['sequence']),
        )
    return chosen['label'], chosen['description']


_TERMINAL_QUERY_STATUSES = {'completed', 'failed'}

# A bare machine-identifier shape ("writing_query_report", "executing_code",
# ...): all lowercase, snake_case, no spaces/punctuation. No legitimate
# human-written (or LLM-written) query summary sentence looks like this --
# it can only be a current_step/phase token that leaked into a field meant
# to hold a natural-language description. Checked defensively against every
# summary candidate below, regardless of which field it came from, so a
# stray "current_step" value can never surface as the parent row's summary.
_PHASE_TOKEN_SHAPE_RE = re.compile(r'^[a-z][a-z0-9]*(?:_[a-z0-9]+){1,}$')


def _looks_like_raw_phase_token(text: str | None) -> bool:
    """True for bare snake_case machine-identifier-shaped strings (see above), or an exact known step name."""
    if not text or not isinstance(text, str):
        return False
    candidate = text.strip()
    if not candidate:
        return False
    return candidate in _QUERY_STEP_LABELS or bool(_PHASE_TOKEN_SHAPE_RE.match(candidate))


# _is_meaningful_query_summary doesn't reject these exact sentinel strings
# (they're prose-shaped, not the short status words it screens for), so a
# cache written while a query had no real objective yet can contain one of
# these verbatim -- excluded here by direct equality so it's never mistaken
# for a genuine Round-Directory-quality summary.
_KNOWN_SUMMARY_PLACEHOLDERS = frozenset({
    _QUERY_DESCRIPTION_FALLBACK, _QUERY_STATUS_FALLBACK_TEXT, _QUERY_SUMMARY_PLACEHOLDER,
})


def _is_weak_summary_candidate(text: str | None) -> bool:
    """
    Combined reject-filter applied to every summary candidate in
    _build_query_timeline_summary, regardless of which source it came from:
    a bare current_step-shaped token, or one of this module's own internal
    "not yet summarized" placeholder strings leaking through as if it were
    real content.
    """
    if not text or not isinstance(text, str):
        return True
    return _looks_like_raw_phase_token(text) or text.strip() in _KNOWN_SUMMARY_PLACEHOLDERS


def _read_cached_query_summary(qd: Path) -> str | None:
    """
    Read the query_summary.txt cache Round Directory's _get_query_summary
    (see above) already populates for this query dir -- WITHOUT ever calling
    the LLM itself. This is the "same high-quality summary Round Directory
    shows" source: once Round Directory (or any other caller) has populated
    the cache, DiscoveryAgent Activity can reuse it for free, deterministically.
    Purely read-only -- never writes or deletes the cache file; that
    lifecycle stays owned by _get_query_summary.
    """
    cache_path = qd / _QUERY_SUMMARY_CACHE_FILE
    if not cache_path.exists():
        return None
    try:
        cached = cache_path.read_text(encoding='utf-8').strip()
    except OSError:
        return None
    if cached and _is_meaningful_query_summary(cached) and not _is_weak_summary_candidate(cached):
        return cached
    return None


# Exact suffixes _build_query_activity_events appends after a query's own
# objective text when reporting completion -- stripped back off when pulling
# the objective out of an already-built "... completed: <objective>..."
# message, so the extracted text reads as a summary, not a full activity log
# line with artifact bookkeeping tacked on.
_KNOWN_SAVED_MESSAGE_SUFFIXES = (
    "; query markdown and checkpoint were saved.",
    "; query markdown was also saved.",
    "; checkpoint was also saved.",
)

_COMPLETED_MESSAGE_RE = re.compile(r'\bcompleted\s*:\s*(.+)', re.IGNORECASE | re.DOTALL)


def _extract_completed_message_text(text: str | None) -> str | None:
    """
    Pull the objective text out of an already-built "... completed: <text>"
    activity message (round_activity.jsonl or the legacy flat log-derived
    messages), e.g. 'query_004 completed: <objective>; checkpoint was also
    saved.' -> '<objective>'. Returns None if the text doesn't contain a
    "completed:" marker at all.
    """
    if not isinstance(text, str):
        return None
    match = _COMPLETED_MESSAGE_RE.search(text)
    if not match:
        return None
    candidate = match.group(1).strip()
    for suffix in _KNOWN_SAVED_MESSAGE_SUFFIXES:
        if candidate.endswith(suffix):
            candidate = candidate[: -len(suffix)].strip()
            break
    return candidate or None


def _build_query_timeline_summary(
    qd: Path,
    info: dict | None,
    jsonl_objs: list[dict],
    status_bucket: str,
    log_derived_messages: list[str] = (),
) -> str:
    """
    Deterministic parent-row summary text, in priority order:
      a. Round Directory's own query summary source (query_summary.txt
         cache -- see _read_cached_query_summary), if available. This is
         the same high-quality one-liner Round Directory already shows for
         this query, reused here for free (no LLM call from this feed).
      b. The objective text following "completed:" on this query's own
         round_activity.jsonl entries or legacy log-derived messages.
      c/d. query_status.json/other query metadata JSON title-or-description,
         then the first clean heading/title in the query markdown (both via
         _fallback_query_summary, then the generic metadata files it
         doesn't check).
      e. Humanized current-phase label -- ONLY while the query is NOT in a
         terminal state (current_step is a snapshot of what the query was
         doing at its last write; showing it for an already-completed/failed
         query would present a stale "in progress" phase as if it still
         described the query's present state).
      f. A short, status-aware generic fallback.
    Never an LLM call. Every candidate is checked with
    _is_weak_summary_candidate before being accepted, so a bare current_step
    value or an internal "not yet summarized" placeholder that leaked into
    any of these fields (e.g. a query_status.json field literally holding
    "writing_query_report", or a stale cache holding this module's own
    fallback sentinel text) can never win regardless of which source it
    came from.
    """
    cached = _read_cached_query_summary(qd)
    if cached:
        return cached

    if info:
        normalized_summary = info.get('summary')
        if (
            isinstance(normalized_summary, str)
            and _is_meaningful_description_candidate(normalized_summary)
            and not _is_weak_summary_candidate(normalized_summary)
        ):
            return normalized_summary

    for obj in jsonl_objs:
        for field in ('summary', 'description', 'message'):
            val = obj.get(field)
            if isinstance(val, str):
                val = val.strip()
                if val and _is_meaningful_description_candidate(val) and not _is_weak_summary_candidate(val):
                    return val

    jsonl_messages = [o.get('message') for o in jsonl_objs if isinstance(o.get('message'), str)]
    for text in jsonl_messages + list(log_derived_messages):
        completed_text = _extract_completed_message_text(text)
        if completed_text and _is_meaningful_description_candidate(completed_text) and not _is_weak_summary_candidate(completed_text):
            return completed_text

    candidate = _fallback_query_summary(qd)
    if candidate != _QUERY_DESCRIPTION_FALLBACK and not _is_weak_summary_candidate(candidate):
        return candidate

    meta_title = _extract_title_from_metadata(_read_generic_query_metadata(qd))
    if meta_title and not _is_weak_summary_candidate(meta_title):
        return meta_title

    if info and status_bucket not in _TERMINAL_QUERY_STATUSES:
        phase_label = _humanize_current_step(info.get('current_step'))
        if phase_label:
            return phase_label

    if status_bucket == 'completed':
        return "Query completed."
    if status_bucket == 'failed':
        return "Query failed."
    return "Query activity."


_EARLY_QUERY_PHASE_STEPS = {'planning'}


def _resolve_query_activity_status(
    info: dict | None,
    has_round_report: bool,
    has_later_query: bool,
    explicit_log_completion: bool,
    has_started_evidence: bool,
) -> str:
    """
    One of: completed, in progress, running, failed, started, unknown.
    query_status.json (info), when present, is authoritative; otherwise the
    same filesystem/log evidence _build_query_activity_events already uses
    (a later query existing, an explicit log completion marker, or
    round_report.md) decides completed vs in-progress vs unknown. Completed
    is always preferred once any completion evidence exists.
    """
    if info:
        if info['status'] == 'completed':
            return 'completed'
        if info['status'] == 'failed':
            return 'failed'
        if info['status'] == 'running':
            current_step = (info.get('current_step') or '').lower()
            return 'started' if current_step in _EARLY_QUERY_PHASE_STEPS else 'running'
    if has_round_report or has_later_query or explicit_log_completion:
        return 'completed'
    if has_started_evidence:
        return 'in progress'
    return 'unknown'


def _group_round_activity_jsonl_by_query(round_dir: Path) -> dict[str, list[dict]]:
    """
    Group round_activity.jsonl entries by canonical query_id. Entries with no
    resolvable query reference are omitted here -- they're round-level and
    already covered by the flat 'events' pipeline that feeds the round-event
    rows in _build_discovery_activity_timeline.
    """
    groups: dict[str, list[dict]] = {}
    for obj in _read_round_activity_jsonl(round_dir):
        query_ref = obj.get('query_name') or obj.get('query_id') or obj.get('query')
        query_id = _canonical_query_id(query_ref)
        if query_id:
            groups.setdefault(query_id, []).append(obj)
    return groups


_PERSISTED_HELPER_AGENT_LABELS = {
    'execution_agent': 'Execution Agent',
    'web_search': 'Web Search Agent',
    'deep_research': 'Deep Research Agent',
    'acs_data_agent': 'ACS Data Agent',
    'census_data_agent': 'Census Data Agent',
    'opentargets_agent': 'OpenTargets Agent',
}


_DELEGATED_TASK_SECTION_RE = re.compile(
    r'^(?:context|background|prior(?:[-\s]+analysis)?|inputs?|datasets?|'
    r'requirements?|detailed\s+steps?|steps?|files?|paths?|analysis\s+to\s+perform|'
    r'implementation|instructions?|metadata)\s*:',
    re.IGNORECASE,
)
_DELEGATED_TASK_INTERNAL_RE = re.compile(
    r'\b(?:system\s+prompt|internal\s+(?:prompt|directive)|follow\s+these\s+instructions|'
    r'you\s+are\s+(?:an?|the)\s+\w+\s+agent)\b',
    re.IGNORECASE,
)
_ABSOLUTE_PATH_RE = re.compile(
    r'(?<![\w.])(?:/[^\s`;,]+(?:/[^\s`;,]+)+|[A-Za-z]:\\(?:[^\\\s]+\\)+[^\\\s]+)'
)
_RELATIVE_FILE_PATH_RE = re.compile(
    r'(?<![\w.])(?:\.{0,2}/)?(?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]{1,10}'
)
_DELEGATED_OPERATION_SENTENCE_RE = re.compile(
    r'^(?:load|read|open|locate|use|save|write|build\s+on|context\b|background\b|'
    r'prior\s+(?:analysis|step|findings|results)\b)',
    re.IGNORECASE,
)


def _delegated_path_filename(match: re.Match) -> str:
    """Keep a referenced artifact's filename without exposing path noise."""
    raw = match.group(0)
    trailing = ''
    while raw and raw[-1] in '.,;:)':
        trailing = raw[-1] + trailing
        raw = raw[:-1]
    filename = PurePosixPath(raw.replace('\\', '/')).name
    return filename + trailing


def _humanize_delegated_task_summary(text: str) -> str:
    """Turn a safe extracted assignment into concise scientist-facing copy."""
    text = _ABSOLUTE_PATH_RE.sub(_delegated_path_filename, text)
    text = _RELATIVE_FILE_PATH_RE.sub(_delegated_path_filename, text)
    text = re.sub(r'^\s*(?:please\s+)?quick\s+analysis\s+to\s+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^\s*append\b', 'Add', text, flags=re.IGNORECASE)
    text = re.sub(
        r'\bthe\s+existing\s+([\w.-]+)\s+file\s+at\s+\1\b',
        r'\1',
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r'\s+', ' ', text).strip()
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if (
        len(sentences) > 1
        and len(sentences[0]) >= 30
        and _DELEGATED_OPERATION_SENTENCE_RE.match(sentences[1])
    ):
        text = sentences[0]
    if text:
        text = text[0].upper() + text[1:]
    return text


def _plain_delegated_task_text(value: object, max_chars: int = 240) -> str | None:
    """Extract only the leading, scientist-safe assignment from a delegated task."""
    if not isinstance(value, str):
        return None

    kept: list[str] = []
    in_code_block = False
    for raw_line in value.replace('\r\n', '\n').splitlines():
        stripped = raw_line.strip()
        if stripped.startswith('```') or stripped.startswith('~~~'):
            in_code_block = not in_code_block
            continue
        if in_code_block or not stripped:
            continue

        boundary_text = re.sub(r'^[#>*+\-\s]+', '', stripped)
        boundary_text = re.sub(r'^[*_]{1,3}|[*_]{1,3}$', '', boundary_text).strip()
        if kept and (
            stripped.startswith('#')
            or _DELEGATED_TASK_SECTION_RE.match(boundary_text)
            or re.match(r'^\d+[.)]\s+', stripped)
        ):
            break
        if not kept and (
            _DELEGATED_TASK_SECTION_RE.match(boundary_text)
            or re.match(r'^\d+[.)]\s+', stripped)
        ):
            continue

        line = re.sub(r'^(?:#{1,6}\s*|>\s*|[-*+]\s+)', '', stripped)
        line = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', line)
        line = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', line)
        line = re.sub(r'`{1,3}([^`]+)`{1,3}', r'\1', line)
        line = re.sub(r'(\*\*|__|\*)', '', line)
        line = _ABSOLUTE_PATH_RE.sub(_delegated_path_filename, line)
        line = _RELATIVE_FILE_PATH_RE.sub(_delegated_path_filename, line)
        line = re.sub(r'\[(?:truncated|…)\]', '', line, flags=re.IGNORECASE)
        line = re.sub(r'\s+', ' ', line).strip(' -:;,')
        if len(line) < 8 or _DELEGATED_TASK_INTERNAL_RE.search(line):
            continue
        kept.append(line)
        if len(' '.join(kept)) >= max_chars:
            break

    text = re.sub(r'\s+', ' ', ' '.join(kept)).strip()
    if not text:
        return None

    sentences = re.split(r'(?<=[.!?])\s+', text)
    text = ' '.join(sentences[:2]).strip()
    if len(text) > max_chars:
        clipped = text[:max_chars + 1]
        sentence_end = max(clipped.rfind('. '), clipped.rfind('? '), clipped.rfind('! '))
        if sentence_end >= max_chars // 3:
            text = clipped[:sentence_end + 1].strip()
        else:
            text = clipped[:max_chars].rsplit(' ', 1)[0].rstrip(' ,;:') + '…'
    return _humanize_delegated_task_summary(text) or None


def _delegated_task_summary(
    value: object,
    fallback_summary: str | None = None,
    max_chars: int = 240,
) -> str | None:
    """Return a concise delegated assignment, never the complete raw prompt."""
    summary = _plain_delegated_task_text(value, max_chars=max_chars)
    if summary:
        return summary
    return _plain_delegated_task_text(fallback_summary, max_chars=max_chars)


def _persisted_subagent_calls(
    qd: Path,
    query_num: int | None,
    started_at: object = None,
    fallback_summary: str | None = None,
) -> list[dict]:
    """Extract helper invocations from the query's authoritative checkpoint.

    DirectorWorkflow persists each returned helper call in workflow_state.results
    with its query number, XML agent tag, parsed task, result, and completion
    timestamp. Newer append-only records may additionally provide an invocation
    ID, status, and per-call start time. Merely mentioning an agent in prose
    never qualifies.
    """
    if query_num is None:
        return []
    checkpoint = qd / 'checkpoint.json'
    try:
        if checkpoint.is_symlink() or checkpoint.stat().st_size > 50 * 1024 * 1024:
            return []
        payload = json.loads(checkpoint.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    workflow_state = payload.get('workflow_state')
    results = workflow_state.get('results') if isinstance(workflow_state, dict) else None
    if not isinstance(results, list):
        return []

    calls: list[dict] = []
    seen_invocations: set[str] = set()
    seen_records: set[str] = set()
    for result in results:
        if not isinstance(result, dict) or result.get('query_num') != query_num:
            continue
        raw = result.get('query_raw')
        match = re.search(r'<(\w+)>([\s\S]*?)</\1>', raw, re.DOTALL) if isinstance(raw, str) else None
        if not match:
            continue
        tag = match.group(1).strip().lower()
        agent_role = _PERSISTED_HELPER_AGENT_LABELS.get(tag)
        explicit_task = next((
            result.get(field)
            for field in ('task_summary', 'task_title', 'title')
            if isinstance(result.get(field), str) and result.get(field).strip()
        ), None)
        task_summary = _delegated_task_summary(
            explicit_task or result.get('query_parsed') or match.group(2),
            fallback_summary=fallback_summary,
        )
        status = result.get('status')
        status = status.strip().lower() if isinstance(status, str) else None
        if status not in {'started', 'running', 'completed', 'failed'}:
            status = 'completed' if result.get('result') else None
        invocation_id = next((
            str(result.get(field)).strip()
            for field in ('invocation_id', 'call_id', 'event_id', 'tool_use_id')
            if result.get(field) is not None and str(result.get(field)).strip()
        ), None)
        started_at_value = _normalize_activity_timestamp(result.get('started_at') or started_at)
        completed_at = _normalize_activity_timestamp(result.get('timestamp'))
        if (
            not agent_role
            or not task_summary
            or not status
            or (status in {'completed', 'failed'} and not completed_at)
        ):
            continue

        exact_record = json.dumps({
            'query_num': query_num,
            'tag': tag,
            'task': result.get('query_parsed') or match.group(2),
            'status': status,
            'started_at': started_at_value,
            'completed_at': completed_at,
            'result': result.get('result'),
            'error': result.get('error'),
        }, sort_keys=True, ensure_ascii=False, default=str)
        exact_fingerprint = hashlib.sha256(exact_record.encode('utf-8')).hexdigest()
        if invocation_id:
            if invocation_id in seen_invocations:
                continue
            seen_invocations.add(invocation_id)
        elif exact_fingerprint in seen_records:
            continue
        seen_records.add(exact_fingerprint)

        sequence = len(calls) + 1
        stable_value = (
            f'{qd.name}:{query_num}:invocation:{invocation_id}'
            if invocation_id
            else f'{qd.name}:{query_num}:{tag}:{started_at_value}:{completed_at}:{sequence}'
        ).encode('utf-8')
        calls.append({
            'id': f"call_{hashlib.sha256(stable_value).hexdigest()[:16]}",
            'agent_role': agent_role,
            'task_summary': task_summary,
            'status': status,
            'sequence': sequence,
            'started_at': started_at_value,
            'completed_at': completed_at if status in {'completed', 'failed'} else None,
        })
    return calls


def _build_discovery_query_item(
    qd: Path,
    num: int | None,
    log_timing: dict[int, dict],
    jsonl_query_groups: dict[str, list[dict]],
    has_round_report: bool,
    round_report_path: Path,
    has_later_query: bool,
    log_name: str | None,
    flat_messages: list[str] = (),
    helper_calls: list[dict] = (),
) -> dict:
    """
    One normalized 'kind': 'query' timeline row for a single query_* dir.
    flat_messages carries this query's own messages already produced by the
    legacy flat event pipeline (_build_query_activity_events) -- used only
    for the PARENT summary's "completed: ..." extraction (see
    _build_query_timeline_summary), not for director_activity, which draws
    solely from structured query_status.json/round_activity.jsonl evidence.
    """
    query_id = _canonical_query_id(qd.name) or qd.name
    status_data = _read_query_status(qd)
    info = _summarize_query_status(status_data) if status_data else None
    jsonl_objs = jsonl_query_groups.get(query_id, [])

    if num is not None:
        timing = _resolve_query_timestamps(
            num, qd, log_timing, has_round_report, round_report_path, has_later_query,
        )
        explicit_log_completion = log_timing.get(num, {}).get('completed_at') is not None
    else:
        # Non-numeric query_* dir (no reliable ordering evidence) -- fall
        # back to mtime-only timing, same as the rest of this file does.
        mtime_ts = _mtime_fallback(qd)
        timing = {
            'started_at': mtime_ts,
            'started_source': None,
            'completed_at': mtime_ts if has_round_report else None,
            'completed_source': None,
            'had_checkpoint': False,
            'had_markdown': (qd / f"{qd.name}.md").exists(),
        }
        explicit_log_completion = False

    status_bucket = _resolve_query_activity_status(
        info, has_round_report, has_later_query, explicit_log_completion,
        has_started_evidence=timing['started_at'] is not None,
    )
    summary = _build_query_timeline_summary(
        qd, info, jsonl_objs, status_bucket, log_derived_messages=flat_messages,
    )
    director_activity = _build_director_activity(
        info, jsonl_objs, _has_notable_query_artifacts(info, qd), status_bucket,
        helper_calls=helper_calls,
    )
    steps = _normalized_query_steps(info, jsonl_objs, director_activity, status_bucket)
    activity_category, activity_summary = _select_query_activity(steps, status_bucket)
    subagent_calls = _persisted_subagent_calls(
        qd, num, (info or {}).get('started_at') or timing.get('started_at'),
        fallback_summary=summary,
    )
    query_objective = _delegated_task_summary(
        (info or {}).get('objective'),
        fallback_summary=summary,
    ) or summary

    # Use creation/start time for the parent row so polling updates never
    # reorder queries after their status or completion timestamp changes.
    timestamp_raw = (
        (info.get('started_at') if info else None)
        or timing['started_at']
        or (info.get('updated_at') if info else None)
        or timing['completed_at']
        or (info.get('completed_at') if info else None)
        or _mtime_fallback(qd)
    )

    # Attribute provenance to the actual log file only when a timestamp was
    # really read from it -- a timing value present via mtime fallback (e.g.
    # round_report.md's mtime, used when round completion is otherwise known)
    # must not be mislabeled as log-derived.
    used_log_evidence = timing.get('started_source') == 'log' or timing.get('completed_source') == 'log'
    if info:
        source = 'query_status.json'
    elif jsonl_objs:
        source = 'round_activity.jsonl'
    elif log_name and used_log_evidence:
        source = log_name
    else:
        source = 'filesystem'

    return {
        'kind': 'query',
        'timestamp': _normalize_activity_timestamp(timestamp_raw),
        'query_id': query_id,
        'status': status_bucket,
        'query_summary': summary,
        'query_objective': query_objective,
        # Compatibility aliases for older activity consumers.
        'summary': summary,
        'objective': query_objective,
        'activity_category': activity_category,
        'activity_summary': activity_summary,
        'steps': steps,
        'subagent_calls': subagent_calls,
        'detail_heading': 'Sub-agent call' if subagent_calls else None,
        'director_activity': director_activity,
        'source': source,
    }


def _infer_round_event_source(message: str, log_name: str | None, round_dir: Path) -> str:
    """Best-effort provenance label for a round-level (non-query) timeline row."""
    if message in _ROUND_ACTIVITY_EVENT_LABELS.values() and (round_dir / 'round_activity.jsonl').exists():
        return 'round_activity.jsonl'
    if log_name:
        return log_name
    if (round_dir / 'round_activity.jsonl').exists():
        return 'round_activity.jsonl'
    return 'filesystem'


def _activity_milestone_type(message: str | None) -> str | None:
    """Map curated scientist-facing round events to stable milestone types."""
    normalized = re.sub(r'\s+', ' ', (message or '').strip()).lower()
    if not normalized:
        return None
    if normalized == 'round started.' or normalized.startswith('discoveryagent run started'):
        return 'round_started'
    if normalized == 'session reset.':
        return 'session_reset'
    if normalized == 'report writing started.':
        return 'report_writing_started'
    if normalized == 'round report saved.':
        return 'round_report_saved'
    if normalized == 'memory report saved.':
        return 'memory_report_saved'
    if normalized == 'workflow completed.':
        return 'workflow_completed'
    if normalized.startswith('error encountered:'):
        return 'round_error'
    return None


_COLLAPSED_ACTIVITY_MILESTONES = frozenset({
    'report_writing_started', 'round_report_saved', 'memory_report_saved',
})


def _build_discovery_activity_timeline(
    round_dir: Path,
    log_text: str | None,
    log_name: str | None,
    flat_events: list[dict],
) -> list[dict]:
    """
    Build the normalized DiscoveryAgent Activity timeline for the Round
    Monitor UI: one grouped row per query_* dir (kind='query', with a
    deterministic summary plus a director_activity list -- what the Director
    called/did inside this query, from structured evidence only; see
    _build_director_activity) plus simple rows for round-level events
    (kind='round_event', reusing the already-deduplicated flat 'events' list
    for those). Every row this function can produce is grounded in
    query_status.json, round_activity.jsonl, live_round.log/round.log, or
    query markdown/JSON evidence -- deterministic and cheap enough to call on
    every poll; never an LLM call, never dataset/query-specific hardcoding.
    """
    query_dirs = _list_query_dirs(round_dir)
    has_round_report = (round_dir / "round_report.md").exists()
    round_report_path = round_dir / "round_report.md"
    log_timing = _scan_log_for_query_timing(log_text)
    jsonl_query_groups = _group_round_activity_jsonl_by_query(round_dir)
    helper_query_groups = _helper_calls_by_query(log_text)

    # This query's own already-classified messages from the legacy flat
    # event pipeline -- used only for _build_query_timeline_summary's
    # "completed: ..." extraction (priority b), not for director_activity.
    flat_messages_by_query: dict[str, list[str]] = {}
    for event in flat_events:
        qid = _canonical_query_id(event.get('query'))
        message = event.get('message')
        if qid and isinstance(message, str):
            flat_messages_by_query.setdefault(qid, []).append(message)

    numeric_dirs: list[tuple[int, Path]] = []
    other_dirs: list[Path] = []
    for qd in query_dirs:
        suffix = qd.name.split("query_", 1)[-1]
        if suffix.isdigit():
            numeric_dirs.append((int(suffix), qd))
        else:
            other_dirs.append(qd)
    numeric_dirs.sort(key=lambda item: item[0])

    items: list[dict] = []
    for i, (num, qd) in enumerate(numeric_dirs):
        has_later_query = i < len(numeric_dirs) - 1
        query_id = _canonical_query_id(qd.name) or qd.name
        items.append(_build_discovery_query_item(
            qd, num, log_timing, jsonl_query_groups, has_round_report,
            round_report_path, has_later_query, log_name,
            flat_messages=flat_messages_by_query.get(query_id, []),
            helper_calls=helper_query_groups.get(query_id, []),
        ))
    for qd in other_dirs:
        query_id = _canonical_query_id(qd.name) or qd.name
        items.append(_build_discovery_query_item(
            qd, None, log_timing, jsonl_query_groups, has_round_report,
            round_report_path, False, log_name,
            flat_messages=flat_messages_by_query.get(query_id, []),
            helper_calls=helper_query_groups.get(query_id, []),
        ))

    for event in flat_events:
        if event.get('query'):
            continue
        message = event.get('message')
        milestone_type = _activity_milestone_type(message)
        if not milestone_type:
            continue
        items.append({
            'kind': 'milestone',
            'milestone_type': milestone_type,
            'timestamp': _normalize_activity_timestamp(event.get('timestamp')),
            'query_id': None,
            'status': None,
            'summary': message,
            'director_activity': [],
            'source': _infer_round_event_source(message, log_name, round_dir),
        })

    items.sort(key=lambda it: (it['timestamp'] is None, it['timestamp'] or ''))
    # Repeated report writes are backend noise rather than distinct
    # scientist-facing states. Keep the latest meaningful occurrence while
    # preserving its true chronological position. Session resets are not
    # collapsed because separate resets are meaningful milestones.
    last_collapsed_index: dict[str, int] = {}
    for index, item in enumerate(items):
        milestone_type = item.get('milestone_type')
        if milestone_type in _COLLAPSED_ACTIVITY_MILESTONES:
            last_collapsed_index[milestone_type] = index
    return [
        item for index, item in enumerate(items)
        if item.get('milestone_type') not in _COLLAPSED_ACTIVITY_MILESTONES
        or last_collapsed_index.get(item.get('milestone_type')) == index
    ]


def _inspect_latest_round(round_dir: Path) -> dict:
    """
    Inspect the latest round directory only (never older rounds) for the
    filesystem evidence the Round Monitor / hypothesis-restore flows need.
    """
    query_dirs = _list_query_dirs(round_dir)
    selected_path = round_dir / "selected_hypothesis.md"
    generated_path = round_dir / "generated_hypotheses.json"
    round_report_path = round_dir / "round_report.md"
    memory_report_path = round_dir / "memory_report.md"

    latest_query_name = None
    if query_dirs:
        latest_query_name = query_dirs[-1].name

    last_modified = None
    try:
        if round_dir.exists():
            mtimes = [round_dir.stat().st_mtime]
            for p in (selected_path, generated_path, round_report_path, memory_report_path):
                if p.exists():
                    mtimes.append(p.stat().st_mtime)
            for qd in query_dirs:
                mtimes.append(qd.stat().st_mtime)
            last_modified = max(mtimes)
    except OSError:
        last_modified = None

    return {
        'round_dir': round_dir,
        'has_selected_hypothesis': selected_path.exists(),
        'has_generated_hypotheses': generated_path.exists(),
        'has_round_report': round_report_path.exists(),
        'has_memory_report': memory_report_path.exists(),
        'query_count': len(query_dirs),
        'latest_query_name': latest_query_name,
        'last_modified': last_modified,
    }


def _classify_round_state(round_dir: Path) -> str:
    """
    Classify the latest round folder into a UI state. Only ever inspects
    round_dir itself -- never scans older round_N folders.
    """
    info = _inspect_latest_round(round_dir)

    if info['has_round_report']:
        return 'round_complete_needs_review'
    if info['query_count'] > 0:
        # query_* dirs are sufficient evidence the backend launched this round
        # from a selection -- selected_hypothesis.md's presence/location is
        # display-only context and must never gate this state.
        return 'round_monitor'
    if info['has_generated_hypotheses'] and not info['has_selected_hypothesis']:
        return 'needs_hypothesis_selection'
    if not info['has_generated_hypotheses'] and not info['has_selected_hypothesis']:
        return 'needs_hypothesis_generation'
    if info['has_selected_hypothesis'] and info['query_count'] == 0:
        # Selected but the backend hasn't started writing queries yet.
        # Deliberately NOT routed to Round Monitor for this MVP.
        return 'selected_waiting_for_backend'
    return 'error_or_unknown'


def _resolve_active_monitor_round(session_round_dir: Path) -> Path:
    """
    Given a session checkpoint dir (which may be a round_N directory), scan sibling
    round_* directories for any whose state classifies as 'round_monitor'. Returns
    the highest-numbered such sibling, or session_round_dir if none is found.

    This fixes the case where the session still points at round_N but the backend
    has already started writing round_{N+1}: /round_monitor and /api/round_status
    should switch to the active sibling rather than reporting stale state.
    """
    import re as _re
    if not _re.match(r'^round_\d+$', session_round_dir.name):
        return session_round_dir  # not a round_N dir; cannot scan siblings

    base = session_round_dir.parent
    monitor_rounds: list[tuple[int, Path]] = []
    try:
        for entry in base.iterdir():
            if not entry.is_dir():
                continue
            m = _re.match(r'^round_(\d+)$', entry.name)
            if not m:
                continue
            try:
                state = _classify_round_state(entry)
            except Exception:
                continue
            if state == 'round_monitor':
                monitor_rounds.append((int(m.group(1)), entry))
    except OSError:
        return session_round_dir

    if not monitor_rounds:
        print(f"Round Monitor: no active monitor round found; using session round")
        return session_round_dir

    monitor_rounds.sort(key=lambda x: x[0])
    best = monitor_rounds[-1][1]
    if best.name != session_round_dir.name:
        print(
            f"Round Monitor: session round {session_round_dir.name!r} not active; "
            f"using latest active monitor round {best.name!r}"
        )
    return best


def _get_completed_rounds_count(checkpoint_dir: Path | str | None) -> int:
    """
    Get the number of completed research rounds from the checkpoint path.

    Looks for round_N directories and returns highest N + 1 (since round_0 = 1 round completed).
    Also checks if the checkpoint_dir itself is a round_N directory.
    """
    if not checkpoint_dir:
        return 1

    checkpoint_path = Path(checkpoint_dir) if isinstance(checkpoint_dir, str) else checkpoint_dir

    # Check if the checkpoint_dir itself is a round_N directory
    if checkpoint_path.name.startswith("round_"):
        suffix = checkpoint_path.name.split("round_", 1)[-1]
        if suffix.isdigit():
            return int(suffix) + 1

    # Check parent directories for round_N pattern
    for parent in checkpoint_path.parents:
        if parent.name.startswith("round_"):
            suffix = parent.name.split("round_", 1)[-1]
            if suffix.isdigit():
                return int(suffix) + 1

    # Look for checkpoints subdirectory with round_N folders
    checkpoints_dir = checkpoint_path / "checkpoints"
    if checkpoints_dir.exists():
        max_round = -1
        for entry in checkpoints_dir.iterdir():
            if entry.is_dir() and entry.name.startswith("round_"):
                suffix = entry.name.split("round_", 1)[-1]
                if suffix.isdigit():
                    max_round = max(max_round, int(suffix))
        if max_round >= 0:
            return max_round + 1

    return 1


def _resolve_checkpoint_root(checkpoint_path: Path) -> Path:
    """Return the checkpoint root directory that contains the checkpoints/round_* folders."""
    if checkpoint_path.name == "checkpoint":
        return checkpoint_path
    for parent in checkpoint_path.parents:
        if parent.name == "checkpoint":
            return parent
    return checkpoint_path


def _get_latest_round_index(round_root: Path) -> int | None:
    """Return the largest round_N index under round_root, or None if none exist."""
    if not round_root.exists():
        return None
    max_round = None
    for entry in round_root.iterdir():
        if entry.is_dir() and entry.name.startswith("round_"):
            suffix = entry.name.split("round_", 1)[-1]
            if suffix.isdigit():
                value = int(suffix)
                max_round = value if max_round is None else max(max_round, value)
    return max_round


def _get_project_round_from_disk() -> int:
    """
    Resolve the current research round from disk by scanning
    DISK_PATH/<project>/checkpoint/checkpoints/round_N and returning max(N) + 1.
    """
    project_cfg = session.get('project_config') or {}
    checkpoint_prefix = session.get('checkpoint_s3_prefix') or project_cfg.get('checkpoint_s3_prefix')
    if checkpoint_prefix:
        checkpoint_root = _disk_root_for_prefix(checkpoint_prefix) / "checkpoint"
        max_round = _get_latest_round_index(checkpoint_root / "checkpoints")
        return (max_round + 1) if max_round is not None else 1

    checkpoint_dir = project_cfg.get('checkpoint_dir') or _get_default_config_value('checkpoint_dir', CLI_CHECKPOINT_DIR)
    if checkpoint_dir:
        checkpoint_root = _resolve_checkpoint_root(Path(checkpoint_dir))
        max_round = _get_latest_round_index(checkpoint_root / "checkpoints")
        return (max_round + 1) if max_round is not None else 1

    return 1


def _format_github_url(github_remote_path: str | None) -> str | None:
    if not github_remote_path:
        return None
    raw = github_remote_path.strip()
    if not raw:
        return None
    return raw


def _clean_path_value(raw_path: str | None) -> str | None:
    """Normalize user/config-supplied path values."""
    if raw_path is None:
        return None
    value = str(raw_path).strip()
    if not value:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    return value or None


def _derive_project_config(
    checkpoint_s3_prefix: str,
    github_remote_path: str | None = None,
    readme_path: str | None = None,
) -> dict:
    if not checkpoint_s3_prefix:
        return {}
    root = _disk_root_for_prefix(checkpoint_s3_prefix)
    checkpoint_root = root / 'checkpoint'
    checkpoint_dir = _latest_round_checkpoint_dir(checkpoint_root)
    github_url = _format_github_url(github_remote_path)
    code_dir = root / 'code'

    normalized_readme = _clean_path_value(readme_path)
    if normalized_readme:
        configured_readme = Path(normalized_readme).expanduser()
        configured_candidates: list[Path] = []
        if configured_readme.is_absolute():
            configured_candidates.append(configured_readme.resolve())
        else:
            configured_candidates.append((code_dir / configured_readme).resolve())
            # Handle values that already include a "code/" prefix in registry config.
            parts = configured_readme.parts
            if parts and parts[0] == 'code':
                configured_candidates.append((root / Path(*parts)).resolve())
        resolved_readme = next((path for path in configured_candidates if path.exists() and path.stat().st_size > 0), None)
        if resolved_readme is None:
            readme_candidates = (
                code_dir / 'data' / 'README.txt',
                code_dir / 'data' / 'README.md',
                code_dir / 'README.txt',
            )
            resolved_readme = next((path for path in readme_candidates if path.exists() and path.stat().st_size > 0), configured_candidates[0])
    else:
        readme_candidates = (
            code_dir / 'data' / 'README.txt',
            code_dir / 'data' / 'README.md',
            code_dir / 'README.txt',
        )
        resolved_readme = next((path for path in readme_candidates if path.exists() and path.stat().st_size > 0), readme_candidates[0])

    return {
        'checkpoint_dir': str(checkpoint_dir),
        'data_path': str(root),
        'code_dir': str(code_dir),
        'readme_path': str(resolved_readme),
        'checkpoint_s3_prefix': checkpoint_s3_prefix,
        'github_remote_path': github_remote_path,
        'github_url': github_url,
    }


def _split_values(raw_value):
    if not raw_value:
        return []
    if isinstance(raw_value, (list, tuple, set)):
        iterator = raw_value
    else:
        iterator = str(raw_value).replace('\n', ',').split(',')
    return [item.strip() for item in iterator if isinstance(item, str) and item.strip()]


def add_project_key_hashes(values):
    for digest in _split_values(values):
        PROJECT_KEY_HASHES.add(digest.lower())


def _reseed_project_key_hashes() -> None:
    PROJECT_KEY_HASHES.clear()
    add_project_key_hashes(os.environ.get('DISCOVERY_AGENT_PROJECT_KEY_HASH'))
    for digest in CLI_PROJECT_KEY_HASHES:
        PROJECT_KEY_HASHES.add(digest)


def add_plaintext_project_keys(values):
    for value in _split_values(values):
        PLAINTEXT_PROJECT_KEYS.add(value)


def load_project_key_registry():
    """Load project key registry with per-key config from JSON."""
    if DB_AVAILABLE:
        try:
            with db_session_scope() as db:
                entries = (
                    db.query(ProjectKey)
                    .filter(ProjectKey.revoked_at.is_(None))
                    .all()
                )
        except Exception as exc:
            print(f"⚠️  Failed to load project key registry from DB: {exc}")
            return

        for entry in entries:
            digest = str(entry.hash or '').strip().lower()
            if not digest:
                continue
            cfg = dict(entry.config or {})
            audit_flag = cfg.get('audit')
            app_mode = cfg.get('app_mode')
            checkpoint_s3_prefix = (
                cfg.get('checkpoint_s3_prefix')
                or entry.project_prefix
            )
            github_remote_path = cfg.get('github_remote_path')
            readme_path = cfg.get('readme_path')
            if checkpoint_s3_prefix:
                derived = _derive_project_config(checkpoint_s3_prefix, github_remote_path, readme_path=readme_path)
                cfg.update(derived)
            if audit_flag is not None:
                cfg['audit'] = bool(audit_flag)
            if app_mode:
                cfg['app_mode'] = app_mode
            PROJECT_KEY_REGISTRY[digest] = {
                'hash': digest,
                'label': entry.label,
                'checkpoint_s3_prefix': checkpoint_s3_prefix,
                'config': cfg,
            }
            PROJECT_KEY_HASHES.add(digest)
        return

    if not PROJECT_KEY_REGISTRY_FILE.exists():
        return
    try:
        with PROJECT_KEY_REGISTRY_FILE.open('r', encoding='utf-8') as handle:
            raw_data = json.load(handle)
    except Exception as exc:
        print(f"⚠️  Failed to load project key registry {PROJECT_KEY_REGISTRY_FILE}: {exc}")
        return

    entries = []
    if isinstance(raw_data, dict):
        entries = raw_data.get('keys') or []
    elif isinstance(raw_data, list):
        entries = raw_data

    if not isinstance(entries, list):
        print(f"⚠️  Invalid registry format in {PROJECT_KEY_REGISTRY_FILE}")
        return

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        digest = str(entry.get('hash', '')).strip().lower()
        if not digest:
            continue
        cfg = dict(entry.get('config', {}) or {})
        if entry.get('research_paper_path') and not cfg.get('research_paper_path'):
            cfg['research_paper_path'] = entry.get('research_paper_path')
        audit_flag = cfg.get('audit')
        app_mode = cfg.get('app_mode')
        checkpoint_s3_prefix = (
            entry.get('checkpoint_s3_prefix')
            or entry.get('project_prefix')
            or cfg.get('checkpoint_s3_prefix')
            or cfg.get('project_prefix')
        )
        if checkpoint_s3_prefix:
            github_remote_path = (
                entry.get('github_remote_path')
                or cfg.get('github_remote_path')
            )
            readme_path = entry.get('readme_path') or cfg.get('readme_path')
            derived = _derive_project_config(checkpoint_s3_prefix, github_remote_path, readme_path=readme_path)
            cfg.update(derived)
            if audit_flag is not None:
                cfg['audit'] = bool(audit_flag)
            if app_mode:
                cfg['app_mode'] = app_mode
        elif not cfg and any(
            entry.get(k)
            for k in ('checkpoint_dir', 'code_dir', 'readme_path', 'data_path')
        ):
            cfg = {
                'checkpoint_dir': entry.get('checkpoint_dir'),
                'code_dir': entry.get('code_dir'),
                'readme_path': entry.get('readme_path'),
                'data_path': entry.get('data_path'),
            }
        PROJECT_KEY_REGISTRY[digest] = {
            'hash': digest,
            'label': entry.get('label'),
            'checkpoint_s3_prefix': checkpoint_s3_prefix,
            'config': cfg,
        }
        PROJECT_KEY_HASHES.add(digest)


def load_saved_project_key_hashes():
    if DB_AVAILABLE:
        return
    try:
        if not PROJECT_KEY_HASH_FILE.exists():
            return
        with PROJECT_KEY_HASH_FILE.open('r', encoding='utf-8') as handle:
            for line in handle:
                digest = line.strip()
                if digest:
                    PROJECT_KEY_HASHES.add(digest.lower())
    except OSError:
        pass


def _project_key_file_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _refresh_project_keys_if_needed(force: bool = False) -> None:
    with PROJECT_KEY_LOCK:
        if DB_AVAILABLE:
            now = datetime.utcnow()
            last_refresh = PROJECT_KEY_CACHE.get('last_db_refresh')
            if not force and last_refresh and (now - last_refresh).total_seconds() < 5:
                return
            _reseed_project_key_hashes()
            PROJECT_KEY_REGISTRY.clear()
            load_project_key_registry()
            load_saved_project_key_hashes()
            PROJECT_KEY_CACHE['last_db_refresh'] = now
            return

        registry_mtime = _project_key_file_mtime(PROJECT_KEY_REGISTRY_FILE)
        hash_mtime = _project_key_file_mtime(PROJECT_KEY_HASH_FILE)
        cache_registry = PROJECT_KEY_CACHE.get('registry_mtime')
        cache_hash = PROJECT_KEY_CACHE.get('hash_mtime')

        if not force and registry_mtime == cache_registry and hash_mtime == cache_hash:
            return

        _reseed_project_key_hashes()
        PROJECT_KEY_REGISTRY.clear()
        load_saved_project_key_hashes()
        load_project_key_registry()
        PROJECT_KEY_CACHE['registry_mtime'] = registry_mtime
        PROJECT_KEY_CACHE['hash_mtime'] = hash_mtime


add_project_key_hashes(os.environ.get('DISCOVERY_AGENT_PROJECT_KEY_HASH'))
add_plaintext_project_keys(os.environ.get('DISCOVERY_AGENT_PROJECT_KEY'))
load_saved_project_key_hashes()
load_project_key_registry()
_refresh_project_keys_if_needed(force=True)


def load_project_progress():
    """Load per-project submission counters from disk."""
    global PROJECT_PROGRESS
    if DB_AVAILABLE:
        PROJECT_PROGRESS = {}
        return
    if not PROJECT_PROGRESS_FILE.exists():
        PROJECT_PROGRESS = {}
        return
    try:
        with PROJECT_PROGRESS_FILE.open('r', encoding='utf-8') as handle:
            data = json.load(handle)
            PROJECT_PROGRESS = data if isinstance(data, dict) else {}
    except Exception as exc:
        print(f"⚠️  Failed to load project progress {PROJECT_PROGRESS_FILE}: {exc}")
        PROJECT_PROGRESS = {}


def _save_project_progress():
    """Persist the submission counters to disk with an atomic replace."""
    if DB_AVAILABLE:
        return
    try:
        PROJECT_PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = PROJECT_PROGRESS_FILE.with_suffix('.tmp')
        tmp_path.write_text(json.dumps(PROJECT_PROGRESS, indent=2), encoding='utf-8')
        tmp_path.replace(PROJECT_PROGRESS_FILE)
    except Exception as exc:
        print(f"⚠️  Failed to save project progress to {PROJECT_PROGRESS_FILE}: {exc}")


def get_project_progress(digest: str | None) -> dict:
    """Return submission stats for a project key digest (defaults to zeros)."""
    if not digest:
        return {'submission_count': 0}
    digest = str(digest).strip().lower()
    if DB_AVAILABLE:
        try:
            with db_session_scope() as db:
                entry = db.get(ProjectProgress, digest)
                if not entry:
                    return {'submission_count': 0}
                return {
                    'submission_count': entry.submission_count or 0,
                    'last_submitted_at': entry.last_submitted_at.isoformat() + 'Z'
                    if entry.last_submitted_at else None,
                    'last_submitted_by': entry.last_submitted_by,
                    'label': entry.label,
                }
        except Exception as exc:
            print(f"⚠️  Failed to fetch project progress from DB: {exc}")
            return {'submission_count': 0}
    entry = PROJECT_PROGRESS.get(digest) if isinstance(PROJECT_PROGRESS, dict) else {}
    if not isinstance(entry, dict):
        entry = {}
    count = entry.get('submission_count', 0)
    if not isinstance(count, int) or count < 0:
        count = 0
    return {
        'submission_count': count,
        'last_submitted_at': entry.get('last_submitted_at'),
        'last_submitted_by': entry.get('last_submitted_by'),
        'label': entry.get('label'),
    }


def get_total_completed_rounds() -> int:
    """Return total completed rounds across all projects."""
    if DB_AVAILABLE:
        try:
            with db_session_scope() as db:
                total = db.query(func.sum(ProjectProgress.submission_count)).scalar()
            return int(total or 0)
        except Exception as exc:
            print(f"⚠️  Failed to fetch total rounds from DB: {exc}")
            return 0
    if not isinstance(PROJECT_PROGRESS, dict):
        return 0
    total = 0
    for entry in PROJECT_PROGRESS.values():
        if not isinstance(entry, dict):
            continue
        count = entry.get('submission_count', 0)
        if isinstance(count, int) and count > 0:
            total += count
    return total


def increment_project_submission(digest: str | None, username: str | None = None, label: str | None = None) -> dict:
    """Increment and persist the submission counter for the given project digest."""
    if not digest:
        return {'submission_count': 0}
    digest = str(digest).strip().lower()
    if DB_AVAILABLE:
        try:
            with db_session_scope() as db:
                entry = db.get(ProjectProgress, digest)
                if not entry:
                    entry = ProjectProgress(project_key_hash=digest, submission_count=0)
                    db.add(entry)
                entry.submission_count = (entry.submission_count or 0) + 1
                entry.last_submitted_at = datetime.utcnow()
                if username:
                    entry.last_submitted_by = username
                if label and not entry.label:
                    entry.label = label
                return {
                    'submission_count': entry.submission_count,
                    'last_submitted_at': entry.last_submitted_at.isoformat() + 'Z'
                    if entry.last_submitted_at else None,
                    'last_submitted_by': entry.last_submitted_by,
                    'label': entry.label,
                }
        except Exception as exc:
            print(f"⚠️  Failed to update project progress in DB: {exc}")
            return {'submission_count': 0}
    entry = get_project_progress(digest)
    submission_count = entry.get('submission_count', 0) + 1
    entry['submission_count'] = submission_count
    entry['last_submitted_at'] = datetime.utcnow().isoformat() + 'Z'
    if username:
        entry['last_submitted_by'] = username
    if label and not entry.get('label'):
        entry['label'] = label
    PROJECT_PROGRESS[digest] = entry
    _save_project_progress()
    return entry


load_project_progress()


def is_project_key_configured() -> bool:
    """Return True when the server has at least one configured project key."""
    _refresh_project_keys_if_needed()
    return bool(PROJECT_KEY_HASHES or PLAINTEXT_PROJECT_KEYS or PROJECT_KEY_REGISTRY)


def sanitize_field(value: str, max_length: int = 256) -> str:
    """Trim and cap user-supplied strings to avoid log pollution."""
    if not isinstance(value, str):
        return ''
    value = value.strip()
    if len(value) > max_length:
        value = value[:max_length]
    return value


def _sanitize_event_payload(payload, *, max_depth: int = 3, max_items: int = 50, max_str: int = 500):
    if max_depth <= 0:
        return {}
    if isinstance(payload, dict):
        sanitized = {}
        for idx, (key, value) in enumerate(payload.items()):
            if idx >= max_items:
                break
            safe_key = sanitize_field(str(key), 80)
            sanitized[safe_key] = _sanitize_event_payload(
                value,
                max_depth=max_depth - 1,
                max_items=max_items,
                max_str=max_str
            )
        return sanitized
    if isinstance(payload, (list, tuple)):
        return [
            _sanitize_event_payload(item, max_depth=max_depth - 1, max_items=max_items, max_str=max_str)
            for item in list(payload)[:max_items]
        ]
    if isinstance(payload, str):
        return sanitize_field(payload, max_str)
    if isinstance(payload, (int, float, bool)) or payload is None:
        return payload
    return sanitize_field(str(payload), max_str)


def _client_event_identity() -> str:
    """Build a stable rate-limit key for client telemetry events."""
    session_id = session.get('session_id')
    if session_id:
        return f"session:{session_id}"
    ip = (request.headers.get('X-Forwarded-For', request.remote_addr) or 'unknown').split(',')[0].strip()
    ua = sanitize_field(request.headers.get('User-Agent', ''), 120)
    return f"anon:{ip}:{ua}"


def _client_event_rate_limited(identity: str) -> bool:
    """Sliding-window limiter for /api/log_event."""
    now = perf_counter()
    window_start = now - CLIENT_EVENT_RATE_WINDOW_S
    with CLIENT_EVENT_RATE_LOCK:
        bucket = CLIENT_EVENT_RATE_STATE.get(identity)
        if bucket is None:
            bucket = deque()
            CLIENT_EVENT_RATE_STATE[identity] = bucket
        while bucket and bucket[0] < window_start:
            bucket.popleft()
        if len(bucket) >= CLIENT_EVENT_RATE_LIMIT:
            return True
        bucket.append(now)
        return False


def verify_project_key(candidate: str) -> str | None:
    """Compare a provided project key against configured secrets; return matching digest or None."""
    _refresh_project_keys_if_needed()
    if not candidate:
        return None
    candidate = candidate.strip()
    if not candidate:
        return None

    # Plaintext dev keys
    for plain_key in PLAINTEXT_PROJECT_KEYS:
        if hmac.compare_digest(candidate, plain_key):
            return hashlib.sha256(candidate.encode('utf-8')).hexdigest().lower()

    # Hashed keys (registry or flat hash file)
    digest = hashlib.sha256(candidate.encode('utf-8')).hexdigest().lower()
    with PROJECT_KEY_LOCK:
        for stored_hash in PROJECT_KEY_HASHES:
            if hmac.compare_digest(digest, stored_hash):
                return digest
    return None


def record_registration_submission(entry: dict) -> None:
    """Append sanitized registration submissions to disk."""
    if DB_AVAILABLE:
        with db_session_scope() as db:
            db.add(RegistrationSubmission(
                name=entry.get('name', ''),
                affiliation=entry.get('affiliation', ''),
                email=entry.get('email', ''),
                username=entry.get('username', ''),
            ))
        return
    payload = entry.copy()
    payload['submitted_at'] = datetime.utcnow().isoformat() + 'Z'
    with REGISTRATION_LOG_PATH.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(payload))
        handle.write('\n')


def _load_registration_usernames() -> set[str]:
    if not REGISTRATION_LOG_PATH.exists():
        return set()
    try:
        mtime = REGISTRATION_LOG_PATH.stat().st_mtime
    except OSError:
        return set()
    cached_mtime = REGISTRATION_CACHE.get('mtime')
    if cached_mtime == mtime:
        return REGISTRATION_CACHE.get('usernames', set())
    usernames = set()
    try:
        with REGISTRATION_LOG_PATH.open('r', encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict):
                    username = entry.get('username', '')
                    if username:
                        usernames.add(username.strip().lower())
    except OSError:
        return set()
    REGISTRATION_CACHE['mtime'] = mtime
    REGISTRATION_CACHE['usernames'] = usernames
    return usernames


def is_username_registered(username: str) -> tuple[bool, str | None]:
    """Return (registered, error) for the provided username."""
    normalized = (username or '').strip().lower()
    if not normalized:
        return False, None
    if DB_AVAILABLE:
        try:
            with db_session_scope() as db:
                exists = (
                    db.query(RegistrationSubmission)
                    .filter(func.lower(RegistrationSubmission.username) == normalized)
                    .first()
                )
            return exists is not None, None
        except Exception as exc:
            print(f"⚠️  Failed to check registration status: {exc}")
            return False, "Registration lookup failed"
    try:
        return normalized in _load_registration_usernames(), None
    except Exception as exc:
        print(f"⚠️  Failed to check registration log: {exc}")
        return False, "Registration lookup failed"


_interaction_log_lock = threading.Lock()


def _write_interaction_log(event_type: str, payload: dict) -> None:
    """Append one JSON record to the local interaction log file.

    This is the primary durable storage for research data – it works even when
    S3 and the database are not configured.  Each line is a self-contained JSON
    object with full session context plus the interaction payload.
    """
    try:
        ctx = {}
        try:
            ctx = {
                'session_id': session.get('session_id'),
                'username': session.get('username'),
                'project_label': session.get('project_label'),
                'project_key_hash': session.get('project_key_digest'),
                'app_mode': session.get('app_mode'),
            }
        except Exception:
            pass

        record = {
            'timestamp': datetime.utcnow().replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z'),
            'event_type': event_type,
            **ctx,
            **payload,
        }
        line = json.dumps(record, ensure_ascii=True, default=str) + '\n'
        with _interaction_log_lock:
            with open(INTERACTION_LOG_PATH, 'a', encoding='utf-8') as fh:
                fh.write(line)
    except Exception as exc:
        print(f"⚠️  Failed to write interaction log ({event_type}): {exc}")


def log_event(event_type: str, payload: dict | None = None) -> None:
    if not DB_AVAILABLE:
        return
    try:
        event_type = sanitize_field(event_type, 80)
        if not event_type:
            return
        session_id = session.get('session_id')
        if not session_id:
            session_id = os.urandom(16).hex()
            session['session_id'] = session_id
        safe_payload = _sanitize_event_payload(payload or {})
        with db_session_scope() as db:
            db.add(EventLog(
                project_key_hash=session.get('project_key_digest'),
                username=session.get('username'),
                session_id=session_id,
                event_type=event_type,
                payload=safe_payload,
            ))
    except Exception as exc:
        print(f"⚠️  Failed to log event {event_type}: {exc}")


def _get_telemetry_bucket() -> str | None:
    return (
        os.environ.get('DISCOVERY_AGENT_TELEMETRY_BUCKET')
        or os.environ.get('DISCOVERY_AGENT_LOG_BUCKET')
        or os.environ.get('S3_BUCKET_NAME')
        or os.environ.get('DISCOVERY_AGENT_S3_BUCKET')
        or 'agentic-science'
    )


def _build_telemetry_s3_key(
    *,
    project_id: str,
    session_id: str,
    date_prefix: str,
    event_id: str,
    timestamp: str,
) -> str:
    base_prefix = os.environ.get('DISCOVERY_AGENT_TELEMETRY_PREFIX', 'telemetry').strip('/')
    safe_project = _sanitize_s3_component(project_id, 'unknown')
    safe_session = _sanitize_s3_component(session_id, 'unknown')
    return (
        f"{base_prefix}/session_logs/{safe_project}/session_{safe_session}/"
        f"{date_prefix}/event_{timestamp}_{event_id}.json"
    )


def _build_telemetry_context() -> dict:
    project_key_hash = session.get('project_key_digest')
    project_label = session.get('project_label')
    checkpoint_prefix = session.get('checkpoint_s3_prefix')
    return {
        'session_id': session.get('session_id'),
        'username': session.get('username'),
        'project_key_hash': project_key_hash,
        'project_label': project_label,
        'checkpoint_s3_prefix': checkpoint_prefix,
        'project_submission_count': session.get('project_submission_count'),
        'project_round': _get_project_round_from_disk(),
    }


def log_full_event_to_s3(
    event_type: str,
    payload: dict,
    *,
    hypothesis_index: int | None = None,
    source: str | None = None,
) -> str | None:
    """Compatibility no-op: the local release never exports telemetry."""
    return None
    if boto3 is None:
        return None
    bucket = _get_telemetry_bucket()
    if not bucket:
        return None
    try:
        credentials = boto3.Session().get_credentials()
    except Exception:
        credentials = None
    if credentials is None:
        if os.environ.get('DISCOVERY_AGENT_WARN_MISSING_AWS_CREDENTIALS', '0') == '1':
            print(f"⚠️  Skipping telemetry event {event_type}: AWS credentials not configured")
        return None
    session_id = session.get('session_id')
    if not session_id:
        session_id = os.urandom(16).hex()
        session['session_id'] = session_id
    event_id = uuid.uuid4().hex
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    timestamp = now.strftime('%Y%m%dT%H%M%SZ')
    date_prefix = now.strftime('%Y%m%d')
    context = _build_telemetry_context()
    project_id = context.get('project_key_hash') or context.get('project_label') or 'unknown'
    key = _build_telemetry_s3_key(
        project_id=project_id,
        session_id=session_id,
        date_prefix=date_prefix,
        event_id=event_id,
        timestamp=timestamp,
    )
    event = {
        'event_id': event_id,
        'timestamp': now.isoformat().replace('+00:00', 'Z'),
        'event_type': event_type,
        **context,
        'hypothesis_index': hypothesis_index,
        'source': source,
        'payload': payload,
    }
    try:
        body = json.dumps(event, ensure_ascii=True, default=str)
        client = boto3.client('s3')
        client.put_object(Bucket=bucket, Key=key, Body=body.encode('utf-8'), ContentType='application/json')
        s3_uri = f"s3://{bucket}/{key}"
        log_event('telemetry_event', {
            'event_type': event_type,
            's3_uri': s3_uri,
            'hypothesis_index': hypothesis_index,
            'project_round': context.get('project_round'),
        })
        return s3_uri
    except Exception as exc:
        print(f"⚠️  Failed to upload telemetry event {event_type}: {exc}")
        return None


def _parse_s3_prefix(raw_prefix: str | None) -> tuple[str | None, str | None]:
    if not raw_prefix:
        return None, None
    raw_prefix = raw_prefix.strip()
    if raw_prefix.startswith('s3://'):
        raw_prefix = raw_prefix[len('s3://'):]
        if '/' in raw_prefix:
            bucket, prefix = raw_prefix.split('/', 1)
        else:
            bucket, prefix = raw_prefix, ''
    else:
        bucket = None
        prefix = raw_prefix
    if prefix and not prefix.endswith('/'):
        prefix += '/'
    if not bucket:
        bucket = (
            os.environ.get('DISCOVERY_AGENT_S3_BUCKET')
            or os.environ.get('S3_BUCKET_NAME')
            or os.environ.get('S3_BUCKET')
        )
    return bucket, prefix


def _sanitize_s3_component(value: str, fallback: str) -> str:
    cleaned = re.sub(r'[^a-zA-Z0-9._-]+', '-', (value or '').strip().lower())
    cleaned = cleaned.strip('-._')
    return cleaned or fallback


def _resolve_ntfy_url() -> str | None:
    raw = (NTFY_URL or NTFY_TOPIC or '').strip()
    if not raw:
        return None
    if raw.startswith('http://') or raw.startswith('https://'):
        return raw
    server = (NTFY_SERVER or 'https://ntfy.sh').strip().rstrip('/')
    topic = raw.lstrip('/')
    return f"{server}/{topic}"


def _truncate_ntfy_body(message: str) -> str:
    if not message:
        return message
    if len(message) <= NTFY_MAX_LEN:
        return message
    return message[:NTFY_MAX_LEN - 1].rstrip() + "…"


def send_admin_selection_email(
    *,
    username: str | None,
    project_prefix: str,
    hypothesis_text: str,
) -> dict:
    return send_admin_selection_notification(
        username=username,
        project_prefix=project_prefix,
        hypothesis_text=hypothesis_text,
        s3_uri=None,
        round_num=None,
    )


def send_admin_selection_notification(
    *,
    username: str | None,
    project_prefix: str,
    hypothesis_text: str,
    s3_uri: str | None,
    round_num: int | None,
) -> dict:
    ntfy_url = _resolve_ntfy_url()
    if not ntfy_url:
        log_event('admin_notify_skipped', {
            'reason': 'missing_ntfy_configuration',
            'has_ntfy_url': bool(NTFY_URL),
            'has_ntfy_topic': bool(NTFY_TOPIC),
        })
        print("⚠️  Admin notification skipped: missing NTFY configuration.")
        return {
            'status': 'skipped',
            'reason': 'missing_ntfy_configuration',
            'target': None,
        }

    safe_username = username or 'Unknown user'
    safe_project = project_prefix or 'unknown'
    header_lines = [
        f"User {safe_username} has completed their selection for {safe_project}.",
    ]
    if round_num is not None:
        header_lines.append(f"Project round: {round_num}.")
    if s3_uri:
        header_lines.append(f"Selected hypothesis S3 URI: {s3_uri}")
    else:
        header_lines.append("Selected hypothesis S3 URI: (not available)")
    body = _truncate_ntfy_body(
        "\n".join(header_lines)
        + "\n\nHere is their submitted hypothesis:\n\n"
        + f"{hypothesis_text}\n"
    )

    try:
        headers = {
            'Content-Type': 'text/plain; charset=utf-8',
            'Title': f"{NTFY_TITLE}: Selection completed by {safe_username}",
        }
        if NTFY_PRIORITY:
            headers['Priority'] = str(NTFY_PRIORITY)
        if NTFY_TOKEN:
            headers['Authorization'] = f"Bearer {NTFY_TOKEN}"
        request_obj = urllib.request.Request(
            ntfy_url,
            data=body.encode('utf-8'),
            headers=headers,
            method='POST',
        )
        with urllib.request.urlopen(request_obj, timeout=10) as response:
            response.read()
        log_event('admin_notify_sent', {'target': ntfy_url})
        return {'status': 'sent', 'target': ntfy_url}
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode('utf-8', errors='ignore') if exc.fp else ''
        error_msg = f"HTTP {exc.code}: {exc.reason}"
        if error_body:
            error_msg = f"{error_msg} - {error_body.strip()}"
        print(f"⚠️  Admin notification failed: {error_msg}")
        log_event('admin_notify_failed', {'error': error_msg})
        return {'status': 'failed', 'target': ntfy_url, 'error': error_msg}
    except Exception as exc:
        print(f"⚠️  Admin notification failed: {exc}")
        log_event('admin_notify_failed', {'error': str(exc)})
        return {'status': 'failed', 'target': ntfy_url, 'error': str(exc)}


def _is_relative_image_path(path: str) -> bool:
    if not path:
        return False
    if SCHEME_PATTERN.match(path):
        return False
    if path.startswith("#"):
        return False
    return not Path(path).is_absolute()


def _prefix_report_images_in_checkpoint(checkpoint_dir: Path, code_dir: Path) -> int:
    """Prefix relative image paths in report markdown with the code_dir."""
    targets = [
        checkpoint_dir / 'memory_report.md',
        checkpoint_dir / 'round_report.md',
        checkpoint_dir / 'round_report_summary.md',
    ]
    code_dir_posix = code_dir.resolve().as_posix().rstrip("/")
    total_changes = 0

    for target in targets:
        if not target.exists():
            continue
        try:
            original = target.read_text(encoding='utf-8')
        except OSError:
            continue

        def _replace(match: re.Match) -> str:
            nonlocal total_changes
            alt_text = match.group(1)
            path = match.group(2)
            title = match.group(3) or ""

            if not _is_relative_image_path(path):
                return match.group(0)

            normalized = path.replace("\\", "/")
            if normalized.startswith(code_dir_posix + "/") or normalized == code_dir_posix:
                return match.group(0)

            prefixed = (code_dir / normalized).as_posix()
            total_changes += 1
            return f"![{alt_text}]({prefixed}{title})"

        updated = IMAGE_PATTERN.sub(_replace, original)
        if updated != original:
            try:
                target.write_text(updated, encoding='utf-8')
            except OSError:
                continue
    return total_changes


def _build_session_log_s3_key(checkpoint_s3_prefix: str, round_num: int, timestamp: str) -> str:
    return f"logs/{checkpoint_s3_prefix}/round_{round_num}_{timestamp}.json"


def upload_session_log_to_s3(log_path: str, checkpoint_s3_prefix: str, round_num: int) -> str | None:
    if boto3 is None:
        return None
    bucket = (
        os.environ.get('S3_BUCKET_NAME')
        or os.environ.get('DISCOVERY_AGENT_LOG_BUCKET')
        or 'agentic-science'
    )
    if not bucket:
        return None
    path = Path(log_path)
    if not path.exists():
        return None
    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    safe_prefix = _sanitize_s3_component(checkpoint_s3_prefix, 'unknown')
    key = _build_session_log_s3_key(safe_prefix, round_num, timestamp)
    client = boto3.client('s3')
    client.upload_file(str(path), bucket, key)
    return f"s3://{bucket}/{key}"


def _build_selected_hypothesis_s3_key(output_path: Path, checkpoint_s3_prefix: str) -> str:
    disk_root = Path(os.environ.get('DISK_PATH', './local_disk')).resolve()
    try:
        relative = output_path.resolve().relative_to(disk_root)
        return relative.as_posix()
    except ValueError:
        safe_prefix = checkpoint_s3_prefix.rstrip('/')
        return f"{safe_prefix}/checkpoint/{output_path.name}"


def upload_selected_hypothesis_to_s3(output_path: str, checkpoint_s3_prefix: str) -> str | None:
    if boto3 is None:
        return None
    bucket = os.environ.get('S3_BUCKET_NAME')
    if not bucket:
        return None
    path = Path(output_path)
    if not path.exists():
        return None
    if not checkpoint_s3_prefix:
        return None
    key = _build_selected_hypothesis_s3_key(path, checkpoint_s3_prefix)
    client = boto3.client('s3')
    client.upload_file(str(path), bucket, key)
    return f"s3://{bucket}/{key}"


def _record_sync_status(token: str, state: str, error: str | None = None) -> None:
    payload = {
        'state': state,
        'error': error,
        'updated_at': _utcnow_iso(),
    }
    with S3_SYNC_LOCK:
        existing = S3_SYNC_STATUS.get(token) or {}
        payload['started_at'] = existing.get('started_at') or payload['updated_at']
        if state in ('success', 'error'):
            payload['finished_at'] = payload['updated_at']
        S3_SYNC_STATUS[token] = payload


def _get_s3_sync_limits() -> tuple[int, int, int]:
    def _env_int(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    max_workers = max(1, _env_int('S3_SYNC_MAX_WORKERS', 4))
    max_in_flight = max(1, _env_int('S3_SYNC_MAX_IN_FLIGHT', max_workers * 4))
    max_pool = max(4, _env_int('S3_SYNC_MAX_POOL_CONNECTIONS', max_workers * 4))
    return max_workers, max_in_flight, max_pool


def _run_checkpoint_sync(bucket: str, prefix: str, target_root: str) -> None:
    os.makedirs(target_root, exist_ok=True)
    max_workers, max_in_flight, max_pool = _get_s3_sync_limits()
    client_config = Config(max_pool_connections=max_pool) if Config else None
    client = boto3.client('s3', config=client_config) if client_config else boto3.client('s3')
    transfer_config = TransferConfig(max_concurrency=1) if TransferConfig else None
    paginator = client.get_paginator('list_objects_v2')
    metadata_path = os.path.join(target_root, '.s3sync.json')
    metadata = {}
    try:
        if os.path.exists(metadata_path):
            with open(metadata_path, 'r', encoding='utf-8') as handle:
                metadata = json.load(handle) or {}
    except Exception:
        metadata = {}
    def _download_one(key: str, local_path: str, remote_mtime: datetime | None) -> str:
        if transfer_config:
            client.download_file(bucket, key, local_path, Config=transfer_config)
        else:
            client.download_file(bucket, key, local_path)
        if remote_mtime is not None:
            remote_ts = remote_mtime.astimezone(timezone.utc).timestamp()
            os.utime(local_path, (remote_ts, remote_ts))
        return key

    pending_meta: dict[str, dict] = {}
    in_flight: set[concurrent.futures.Future[str]] = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get('Contents', []):
                key = item.get('Key')
                if not key or key.endswith('/'):
                    continue
                rel_path = key[len(prefix):] if prefix and key.startswith(prefix) else key
                local_path = os.path.join(target_root, rel_path)
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                remote_size = item.get('Size')
                remote_mtime = item.get('LastModified')
                remote_etag = (item.get('ETag') or '').strip('"')
                if os.path.exists(local_path) and remote_size is not None and remote_mtime is not None:
                    local_size = os.path.getsize(local_path)
                    local_mtime = os.path.getmtime(local_path)
                    remote_ts = remote_mtime.astimezone(timezone.utc).timestamp()
                    cached = metadata.get(key) or {}
                    if (
                        local_size == remote_size
                        and (cached.get('etag') == remote_etag or cached.get('last_modified') == remote_ts)
                    ):
                        continue
                    if local_size == remote_size and local_mtime >= remote_ts:
                        continue

                pending_meta[key] = {
                    'etag': remote_etag,
                    'size': remote_size,
                    'last_modified': remote_mtime.astimezone(timezone.utc).timestamp()
                    if remote_mtime
                    else None,
                }
                future = executor.submit(_download_one, key, local_path, remote_mtime)
                in_flight.add(future)
                if len(in_flight) >= max_in_flight:
                    done, in_flight = concurrent.futures.wait(
                        in_flight,
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                    for finished in done:
                        finished_key = finished.result()
                        meta = pending_meta.pop(finished_key, None)
                        if meta is not None:
                            metadata[finished_key] = meta

        if in_flight:
            done, _ = concurrent.futures.wait(in_flight)
            for finished in done:
                finished_key = finished.result()
                meta = pending_meta.pop(finished_key, None)
                if meta is not None:
                    metadata[finished_key] = meta
    try:
        with open(metadata_path, 'w', encoding='utf-8') as handle:
            json.dump(metadata, handle)
    except Exception:
        pass


def start_checkpoint_sync(digest: str, s3_prefix: str, target_root: str | None = None) -> None:
    if not digest or not s3_prefix:
        return
    if boto3 is None:
        _record_sync_status(digest, 'error', 'boto3 not installed')
        return
    with S3_SYNC_LOCK:
        status = S3_SYNC_STATUS.get(digest) or {}
        if status.get('state') == 'running':
            return
        S3_SYNC_STATUS[digest] = {
            'state': 'running',
            'started_at': _utcnow_iso(),
        }

    bucket, prefix = _parse_s3_prefix(s3_prefix)
    if not bucket:
        _record_sync_status(digest, 'error', 'missing bucket')
        return

    if not target_root:
        _record_sync_status(digest, 'error', 'missing target root')
        return

    def _worker():
        try:
            _run_checkpoint_sync(bucket, prefix, target_root)
            _record_sync_status(digest, 'success')
            log_event('checkpoint_sync_complete', {'bucket': bucket, 'prefix': prefix})
        except Exception as exc:
            _record_sync_status(digest, 'error', str(exc))
            log_event('checkpoint_sync_error', {'error': str(exc), 'bucket': bucket, 'prefix': prefix})

    log_event('checkpoint_sync_start', {'bucket': bucket, 'prefix': prefix, 'target_root': target_root})
    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()


def start_checkpoint_prefix_sync(digest: str, checkpoint_s3_prefix: str) -> None:
    """
    Kick off a background sync of code/checkpoints for the given project prefix.

    Uses the pull_s3 helper (bucket from S3_BUCKET_NAME, root from DISK_PATH).
    Status is recorded under a distinct token to avoid colliding with the
    checkpoint_s3_prefix sync status.
    """
    if not digest or not checkpoint_s3_prefix:
        return
    if boto3 is None:
        _record_sync_status(f"{digest}:pull_s3", 'error', 'boto3 not installed')
        return

    token = f"{digest}:pull_s3"
    with S3_SYNC_LOCK:
        status = S3_SYNC_STATUS.get(token) or {}
        if status.get('state') == 'running':
            return
        S3_SYNC_STATUS[token] = {
            'state': 'running',
            'started_at': _utcnow_iso(),
        }

    def _worker():
        try:
            sync_checkpoint_code_from_s3_to_disk(checkpoint_s3_prefix)
            disk_root = _disk_root_for_prefix(checkpoint_s3_prefix)
            checkpoint_dir = _latest_round_checkpoint_dir(disk_root / 'checkpoint')
            _prefix_report_images_in_checkpoint(
                checkpoint_dir,
                disk_root / 'code',
            )
            _write_sync_marker(checkpoint_s3_prefix, 'success')
            _record_sync_status(token, 'success')
            log_event('project_prefix_sync_complete', {'project_prefix': checkpoint_s3_prefix})
        except Exception as exc:  # pragma: no cover - log only
            _write_sync_marker(checkpoint_s3_prefix, 'error', str(exc))
            _record_sync_status(token, 'error', str(exc))
            log_event('project_prefix_sync_error', {'error': str(exc), 'project_prefix': checkpoint_s3_prefix})
            print(f"⚠️  S3 sync failed for project_prefix={checkpoint_s3_prefix}: {exc}")

    log_event('project_prefix_sync_start', {'project_prefix': checkpoint_s3_prefix})
    _write_sync_marker(checkpoint_s3_prefix, 'running')
    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()


def start_sequential_checkpoint_sync(digest: str, checkpoint_s3_prefix: str, target_root: str | None = None) -> None:
    if not digest or not checkpoint_s3_prefix:
        return
    if DISABLE_S3_SYNC:
        _record_sync_status(digest, 'success')
        _record_sync_status(f"{digest}:pull_s3", 'success')
        log_event('checkpoint_sync_skipped', {'reason': 'disabled', 'checkpoint_s3_prefix': checkpoint_s3_prefix})
        return
    if boto3 is None:
        _record_sync_status(digest, 'error', 'boto3 not installed')
        _record_sync_status(f"{digest}:pull_s3", 'error', 'boto3 not installed')
        return

    token_pull = f"{digest}:pull_s3"
    with S3_SYNC_LOCK:
        if S3_SYNC_STATUS.get(digest, {}).get('state') == 'running' or S3_SYNC_STATUS.get(token_pull, {}).get('state') == 'running':
            return
        S3_SYNC_STATUS[digest] = {
            'state': 'running',
            'started_at': _utcnow_iso(),
        }
        S3_SYNC_STATUS[token_pull] = {
            'state': 'running',
            'started_at': _utcnow_iso(),
        }

    bucket, prefix = _checkpoint_prefix_from_raw(checkpoint_s3_prefix)
    if not bucket:
        _record_sync_status(digest, 'error', 'missing bucket')
        _record_sync_status(token_pull, 'error', 'missing bucket')
        return
    if not target_root:
        _record_sync_status(digest, 'error', 'missing target root')
        _record_sync_status(token_pull, 'error', 'missing target root')
        return

    log_event('checkpoint_sync_start', {'bucket': bucket, 'prefix': prefix, 'target_root': target_root})
    log_event('project_prefix_sync_start', {'project_prefix': checkpoint_s3_prefix})
    log_event('checkpoint_s3_prefix', {'checkpoint_s3_prefix': checkpoint_s3_prefix})
    _write_sync_marker(checkpoint_s3_prefix, 'running')
    print(f"[s3-sync] checkpoint_s3_prefix={checkpoint_s3_prefix}")
    resolved_target = Path(target_root).expanduser().resolve()
    resolved_disk = _get_disk_path().resolve()
    print(
        "[s3-sync] "
        f"cwd={Path.cwd()} "
        f"DISK_PATH={os.environ.get('DISK_PATH')} "
        f"resolved_disk={resolved_disk} "
        f"target_root={target_root} "
        f"resolved_target={resolved_target}"
    )
    log_event('checkpoint_sync_diag', {
        'cwd': str(Path.cwd()),
        'disk_path_env': os.environ.get('DISK_PATH'),
        'resolved_disk': str(resolved_disk),
        'target_root': target_root,
        'resolved_target': str(resolved_target),
        'checkpoint_s3_prefix': checkpoint_s3_prefix,
    })

    def _worker():
        log_memory("s3_sync_worker: starting checkpoint sync")
        try:
            _run_checkpoint_sync(bucket, prefix, target_root)
            _record_sync_status(digest, 'success')
            log_event('checkpoint_sync_complete', {'bucket': bucket, 'prefix': prefix})
            log_memory("s3_sync_worker: checkpoint sync complete")
        except Exception as exc:
            _record_sync_status(digest, 'error', str(exc))
            _record_sync_status(token_pull, 'error', f"checkpoint sync failed: {exc}")
            log_event('checkpoint_sync_error', {'error': str(exc), 'bucket': bucket, 'prefix': prefix})
            log_memory("s3_sync_worker: checkpoint sync failed", level="error")
            return
        try:
            log_memory("s3_sync_worker: starting project prefix sync")
            # Checkpoint content is already synced in phase 1 (_run_checkpoint_sync);
            # phase 2 only needs project code to avoid duplicate checkpoint downloads.
            sync_checkpoint_code_from_s3_to_disk(
                checkpoint_s3_prefix,
                include_code=True,
                include_checkpoint=False,
            )
            log_memory("s3_sync_worker: project prefix sync complete")
            disk_root = _disk_root_for_prefix(checkpoint_s3_prefix)
            checkpoint_dir = _latest_round_checkpoint_dir(disk_root / 'checkpoint')
            _prefix_report_images_in_checkpoint(
                checkpoint_dir,
                disk_root / 'code',
            )
            _write_sync_marker(checkpoint_s3_prefix, 'success')
            _record_sync_status(token_pull, 'success')
            log_event('project_prefix_sync_complete', {'project_prefix': checkpoint_s3_prefix})
        except Exception as exc:
            _write_sync_marker(checkpoint_s3_prefix, 'error', str(exc))
            _record_sync_status(token_pull, 'error', str(exc))
            log_event('project_prefix_sync_error', {'error': str(exc), 'project_prefix': checkpoint_s3_prefix})
            log_memory(f"s3_sync_worker: project prefix sync failed - {exc}", level="error")
            print(f"⚠️  S3 sync failed for project_prefix={checkpoint_s3_prefix}: {exc}")

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()


def clear_session_state() -> None:
    """Remove session-scoped generator bindings when logging out."""
    session_id = session.get('session_id')
    if session_id:
        GENERATORS.pop(session_id, None)
        with GENERATION_LOCK:
            GENERATION_STATE.pop(session_id, None)
            REGENERATION_STATE.pop(session_id, None)
        with REPORT_CHAT_CANCEL_LOCK:
            REPORT_CHAT_CANCEL_STATE.pop(session_id, None)
        with HYPOTHESIS_THREAD_CANCEL_LOCK:
            HYPOTHESIS_THREAD_CANCEL_STATE.pop(session_id, None)
        with SESSION_OPERATION_LOCKS_GUARD:
            SESSION_OPERATION_LOCKS.pop(session_id, None)
        with _GENERATOR_LAST_USED_LOCK:
            _GENERATOR_LAST_USED.pop(session_id, None)


def _session_debug_id() -> str | None:
    """Return a short stable identifier for the current session for debug traces."""
    session_id = session.get('session_id')
    if not session_id:
        return None
    return hashlib.sha256(session_id.encode('utf-8')).hexdigest()[:12]


def _audit_chat_storage_scope() -> str:
    """Return a stable storage scope for audit chat keyed by project + username."""
    project_key = sanitize_field(
        session.get('project_key_digest') or session.get('project_label') or 'project',
        120,
    )
    username = sanitize_field(session.get('username') or 'anonymous', 120).lower()
    material = f"{project_key}:{username}"
    return hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]


def _get_generation_id() -> int:
    """Return the current hypothesis-generation revision id for this session."""
    try:
        value = int(session.get('generation_id', 0))
    except (TypeError, ValueError):
        value = 0
    if value < 0:
        value = 0
    session['generation_id'] = value
    return value


def _increment_generation_id() -> int:
    """Bump and return the current hypothesis-generation revision id."""
    current = _get_generation_id() + 1
    session['generation_id'] = current
    return current


def _new_hypothesis_state_id() -> str:
    """Return a new opaque state id for a hypothesis revision."""
    return uuid.uuid4().hex


def _new_hypothesis_thread_id() -> str:
    """Return a new opaque STABLE thread id for a hypothesis's refinement thread.

    Unlike state_id (which rotates on every revision), a thread id stays with a
    hypothesis across revisions and index shifts, so a per-hypothesis chat thread
    always restores to the correct hypothesis after a worker restart.
    """
    return 'thr_' + uuid.uuid4().hex


def _ensure_hypothesis_thread_ids(generator: InteractiveHypothesisGenerator) -> list[str]:
    """Ensure the generator has one STABLE thread id per current hypothesis,
    preserving existing ids by position (they must never rotate on edit)."""
    hypotheses = list(getattr(generator, 'hypotheses', []) or [])
    existing = getattr(generator, 'hypothesis_thread_ids', None)
    if not isinstance(existing, list):
        existing = []
    normalized: list[str] = []
    for idx, _ in enumerate(hypotheses):
        candidate = existing[idx] if idx < len(existing) else None
        if isinstance(candidate, str) and candidate.strip():
            normalized.append(candidate.strip()[:80])
        else:
            normalized.append(_new_hypothesis_thread_id())
    generator.hypothesis_thread_ids = normalized
    return normalized


def _reset_hypothesis_thread_ids(generator: InteractiveHypothesisGenerator) -> list[str]:
    """Assign fresh thread ids for every hypothesis (used after a full
    regeneration, which produces an entirely new candidate set)."""
    hypotheses = list(getattr(generator, 'hypotheses', []) or [])
    generator.hypothesis_thread_ids = [_new_hypothesis_thread_id() for _ in hypotheses]
    return generator.hypothesis_thread_ids


def _rotate_hypothesis_thread_id(generator: InteractiveHypothesisGenerator, idx: int) -> str | None:
    """Assign a new thread id for one hypothesis (used on single-hypothesis
    regeneration: the regenerated hypothesis starts a clean thread, while the
    old durable thread remains addressable by its historical result event)."""
    thread_ids = _ensure_hypothesis_thread_ids(generator)
    if idx < 1 or idx > len(thread_ids):
        return None
    thread_ids[idx - 1] = _new_hypothesis_thread_id()
    generator.hypothesis_thread_ids = thread_ids
    return thread_ids[idx - 1]


def _hypothesis_thread_key_for_index(generator: InteractiveHypothesisGenerator, idx: int) -> str | None:
    """The stable thread key for the hypothesis currently at 1-based idx."""
    thread_ids = _ensure_hypothesis_thread_ids(generator)
    if idx < 1 or idx > len(thread_ids):
        return None
    return thread_ids[idx - 1]


def _activate_hypothesis_thread_for_result(generator, idx: int, thread_id: str | None) -> tuple[str, bool]:
    """Activate a trusted historical result's thread for one current slot.

    Legacy snapshots without a recoverable association receive a fresh isolated
    thread. The previously current thread is retained so later historical
    navigation can still restore it.
    """
    thread_ids = _ensure_hypothesis_thread_ids(generator)
    if idx < 1 or idx > len(thread_ids):
        raise ValueError('Invalid hypothesis index')
    recovered = bool(_valid_hypothesis_thread_id(thread_id))
    target = _valid_hypothesis_thread_id(thread_id) or _new_hypothesis_thread_id()
    thread_ids[idx - 1] = target
    generator.hypothesis_thread_ids = thread_ids
    return target, recovered


def _finalize_restored_hypotheses(generator: InteractiveHypothesisGenerator, source: str) -> list[dict]:
    """Ensure state/thread ids and return the parsed shape /api/generate returns.
    Assumes generator.hypotheses (and any known state ids) are already set."""
    state_ids = _ensure_hypothesis_state_ids(generator)
    _ensure_hypothesis_thread_ids(generator)
    parsed_hypotheses = []
    for i, hyp in enumerate(generator.hypotheses, 1):
        sections = generator.parse_hypothesis_sections(hyp)
        parsed_hypotheses.append({
            'index': i,
            'state_id': state_ids[i - 1] if i - 1 < len(state_ids) else None,
            'title': sections.get('title'),
            'full_text': hyp,
            'hypothesis': sections['hypothesis'],
            'justification': sections['justification'],
            'interpretation': sections['interpretation'],
            'plan': sections.get('plan'),
        })
    print(f"[restore] Restored {len(parsed_hypotheses)} generated hypotheses from {source}")
    return parsed_hypotheses


def _restore_generated_hypotheses_from_history(
    generator: InteractiveHypothesisGenerator, checkpoint_dir: Path,
) -> list[dict] | None:
    """Fallback restore: reconstruct the active candidate set from the round's
    durable Studio history when generated_hypotheses.json is absent.

    This keeps generator.hypotheses matching the cards the scientist sees (which
    the frontend renders from the same durable history), so regenerate/edit
    operate on the real hypotheses without depending on request_hypothesis
    recovery. State ids are taken from the history snapshots so they align with
    the client's cards; missing ones are backfilled.
    """
    active = _v2_active_hypotheses_from_history(checkpoint_dir)
    if not active:
        return None
    generator.hypotheses = [(a.get('full_text') or a.get('hypothesis') or '') for a in active]
    generator.hypothesis_state_ids = [a.get('state_id') for a in active]
    generator.hypothesis_thread_ids = [a.get('hypothesis_thread_id') for a in active]
    return _finalize_restored_hypotheses(generator, f"durable history at {checkpoint_dir}")


def _restore_generated_hypotheses_if_available(generator: InteractiveHypothesisGenerator) -> list[dict] | None:
    """
    Restore the current Studio round's candidate hypotheses into the generator,
    returning them in the same parsed shape /api/generate returns (or None when
    there is nothing to restore).

    Preferred source is generated_hypotheses.json in the generator's active
    Studio checkpoint_dir. When that operational cache is missing (e.g. a studio
    reset removed it), fall back to the round's durable hypothesis_studio_history
    so the generator still matches the visible cards. In both cases a finalized
    selection (selected_hypothesis.md) means the round is done — nothing to
    restore for editing.
    """
    try:
        checkpoint_dir = generator.checkpoint_dir
        generated_path = checkpoint_dir / "generated_hypotheses.json"
        selected_path = checkpoint_dir / "selected_hypothesis.md"
        if selected_path.exists():
            return None
        if not generated_path.exists():
            return _restore_generated_hypotheses_from_history(generator, checkpoint_dir)

        with open(generated_path, 'r', encoding='utf-8') as f:
            payload = json.load(f)

        hypotheses = payload.get('hypotheses') or []
        if not hypotheses:
            return _restore_generated_hypotheses_from_history(generator, checkpoint_dir)

        generator.hypotheses = list(hypotheses)
        saved_state_ids = payload.get('hypothesis_state_ids')
        generator.hypothesis_state_ids = list(saved_state_ids) if isinstance(saved_state_ids, list) else []
        state_ids = _ensure_hypothesis_state_ids(generator)
        # Restore stable thread ids + the generation-time baseline snapshot so
        # per-hypothesis refinement threads rebuild against the correct
        # hypothesis and from the same grounding after a worker restart.
        saved_thread_ids = payload.get('hypothesis_thread_ids')
        generator.hypothesis_thread_ids = list(saved_thread_ids) if isinstance(saved_thread_ids, list) else []
        _ensure_hypothesis_thread_ids(generator)
        saved_baseline = payload.get('refinement_baseline')
        if isinstance(saved_baseline, str) and saved_baseline.strip() and hasattr(generator, '_refinement_baseline_snapshot'):
            generator._refinement_baseline_snapshot = saved_baseline

        parsed_hypotheses = []
        for i, hyp in enumerate(generator.hypotheses, 1):
            sections = generator.parse_hypothesis_sections(hyp)
            parsed_hypotheses.append({
                'index': i,
                'state_id': state_ids[i - 1] if i - 1 < len(state_ids) else None,
                'title': sections.get('title'),
                'full_text': hyp,
                'hypothesis': sections['hypothesis'],
                'justification': sections['justification'],
                'interpretation': sections['interpretation'],
                'plan': sections.get('plan'),
            })
        print(f"[restore] Restored {len(parsed_hypotheses)} generated hypotheses from {generated_path}")
        return parsed_hypotheses
    except Exception as exc:
        print(f"⚠️ Warning: Failed to restore generated hypotheses: {exc}")
        return None


def _ensure_hypothesis_state_ids(generator: InteractiveHypothesisGenerator) -> list[str]:
    """Ensure the generator has one stable state id per current hypothesis."""
    hypotheses = list(getattr(generator, 'hypotheses', []) or [])
    existing = getattr(generator, 'hypothesis_state_ids', None)
    if not isinstance(existing, list):
        existing = []

    normalized: list[str] = []
    for idx, _ in enumerate(hypotheses):
        candidate = existing[idx] if idx < len(existing) else None
        if isinstance(candidate, str):
            candidate = candidate.strip()
        if candidate:
            normalized.append(candidate[:64])
        else:
            normalized.append(_new_hypothesis_state_id())

    generator.hypothesis_state_ids = normalized
    return normalized


def _reset_hypothesis_state_ids(generator: InteractiveHypothesisGenerator) -> list[str]:
    """Assign new state ids for all hypotheses (used after full regeneration)."""
    hypotheses = list(getattr(generator, 'hypotheses', []) or [])
    state_ids = [_new_hypothesis_state_id() for _ in hypotheses]
    generator.hypothesis_state_ids = state_ids
    return state_ids


def _rotate_hypothesis_state_id(generator: InteractiveHypothesisGenerator, idx: int) -> str | None:
    """Assign a new state id for a single hypothesis index (1-based)."""
    state_ids = _ensure_hypothesis_state_ids(generator)
    if idx < 1 or idx > len(state_ids):
        return None
    new_state_id = _new_hypothesis_state_id()
    state_ids[idx - 1] = new_state_id
    generator.hypothesis_state_ids = state_ids
    return new_state_id


def _resolve_hypothesis_index(
    generator: InteractiveHypothesisGenerator,
    raw_index,
    raw_state_id: str | None = None,
) -> tuple[int | None, str | None]:
    """
    Resolve a 1-based hypothesis index from an optional raw index and optional state id.

    Returns:
        (resolved_index, error_code)
        error_code is one of: missing_index, invalid_index, stale_state
    """
    state_ids = _ensure_hypothesis_state_ids(generator)
    max_index = len(state_ids)
    state_id = sanitize_field(raw_state_id, 64) if raw_state_id is not None else ''
    state_id = state_id.strip() if isinstance(state_id, str) else ''

    parsed_index: int | None = None
    if raw_index is not None:
        try:
            parsed_index = int(raw_index)
        except (TypeError, ValueError):
            parsed_index = None

    if parsed_index is not None and 1 <= parsed_index <= max_index:
        if not state_id:
            return parsed_index, None
        current_state_id = state_ids[parsed_index - 1] if parsed_index - 1 < len(state_ids) else None
        if current_state_id and current_state_id == state_id:
            return parsed_index, None
        if state_id in state_ids:
            return state_ids.index(state_id) + 1, None
        return None, 'stale_state'

    if state_id:
        if state_id in state_ids:
            return state_ids.index(state_id) + 1, None
        return None, 'stale_state'

    if raw_index is None:
        return None, 'missing_index'
    return None, 'invalid_index'


def _resolve_hypothesis_index_for_regenerate(
    generator: InteractiveHypothesisGenerator,
    raw_index,
    raw_state_id: str | None = None,
) -> tuple[int | None, str | None, bool]:
    """
    Resolve hypothesis index for regenerate flows.

    If a stale state id is provided but the explicit index is still valid, fall back
    to the index so users can regenerate immediately after a full regeneration.
    """
    idx, idx_error = _resolve_hypothesis_index(generator, raw_index, raw_state_id)
    if idx_error != 'stale_state':
        return idx, idx_error, False

    fallback_idx, fallback_error = _resolve_hypothesis_index(generator, raw_index, None)
    if fallback_error is None and fallback_idx is not None:
        return fallback_idx, None, True

    return idx, idx_error, False


def _resolve_hypothesis_index_for_edit(
    generator: InteractiveHypothesisGenerator,
    raw_index,
    raw_state_id: str | None = None,
    *,
    allow_stale_fallback: bool = False,
) -> tuple[int | None, str | None, bool]:
    """
    Resolve hypothesis index for edit flows.

    If a stale state id is provided but the request also includes the full draft
    text, fall back to the explicit index so users can continue editing without
    re-opening the editor after their last successful change.
    """
    idx, idx_error = _resolve_hypothesis_index(generator, raw_index, raw_state_id)
    if idx_error != 'stale_state' or not allow_stale_fallback:
        return idx, idx_error, False

    fallback_idx, fallback_error = _resolve_hypothesis_index(generator, raw_index, None)
    if fallback_error is None and fallback_idx is not None:
        return fallback_idx, None, True

    return idx, idx_error, False


def _seed_hypothesis_from_request_draft(
    generator: InteractiveHypothesisGenerator,
    raw_index,
    raw_state_id: str | None,
    manual_hypothesis: str,
) -> bool:
    """Restore a request's hypothesis draft into a restored/empty generator."""
    if not manual_hypothesis:
        return False

    try:
        idx = int(raw_index)
    except (TypeError, ValueError):
        return False
    if idx < 1:
        return False

    hypotheses = list(getattr(generator, 'hypotheses', []) or [])
    if idx <= len(hypotheses) and str(hypotheses[idx - 1] or '').strip():
        return False

    while len(hypotheses) < idx:
        hypotheses.append('')
    hypotheses[idx - 1] = generator._sanitize_hypothesis_text(manual_hypothesis)
    generator.hypotheses = hypotheses

    state_ids = _ensure_hypothesis_state_ids(generator)
    state_id = sanitize_field(raw_state_id, 64) if raw_state_id is not None else ''
    state_id = state_id.strip() if isinstance(state_id, str) else ''
    if state_id and idx <= len(state_ids):
        state_ids[idx - 1] = state_id
        generator.hypothesis_state_ids = state_ids
    return True


def _parse_optional_generation_id(raw_generation_id) -> tuple[int | None, str | None]:
    """
    Parse optional generation id sent by the client.

    Returns:
        (generation_id_or_none, error_code)
        error_code is one of: invalid_generation_id
    """
    if raw_generation_id is None:
        return None, None
    if isinstance(raw_generation_id, str) and not raw_generation_id.strip():
        return None, None
    try:
        parsed = int(raw_generation_id)
    except (TypeError, ValueError):
        return None, 'invalid_generation_id'
    if parsed < 0:
        return None, 'invalid_generation_id'
    return parsed, None


def _get_session_operation_lock(session_id: str) -> threading.RLock:
    """Get (or create) the per-session lock that serializes mutating chat operations."""
    with SESSION_OPERATION_LOCKS_GUARD:
        lock = SESSION_OPERATION_LOCKS.get(session_id)
        if lock is None:
            lock = threading.RLock()
            SESSION_OPERATION_LOCKS[session_id] = lock
        return lock


@contextmanager
def _session_operation_context(operation_name: str):
    """
    Attach operation-level debug metadata and serialize stateful operations per session.
    """
    session_id = session.get('session_id')
    debug_payload = {
        'request_id': uuid.uuid4().hex[:12],
        'operation': operation_name,
        'session_debug_id': _session_debug_id(),
        'lock_wait_ms': 0.0,
    }
    lock = None
    wait_started = perf_counter()
    if session_id:
        lock = _get_session_operation_lock(session_id)
        lock.acquire()
        debug_payload['lock_wait_ms'] = (perf_counter() - wait_started) * 1000.0

    g.operation_debug = debug_payload
    op_started = perf_counter()
    print(
        f"[op-start] op={operation_name} req={debug_payload['request_id']} "
        f"session={debug_payload['session_debug_id']} wait_ms={debug_payload['lock_wait_ms']:.1f}"
    )
    try:
        yield debug_payload
    except Exception as exc:
        debug_payload['error_type'] = type(exc).__name__
        print(
            f"[op-error] op={operation_name} req={debug_payload['request_id']} "
            f"session={debug_payload['session_debug_id']} error={type(exc).__name__}: {exc}"
        )
        raise
    finally:
        held_ms = (perf_counter() - op_started) * 1000.0
        debug_payload['held_ms'] = held_ms
        print(
            f"[op-end] op={operation_name} req={debug_payload['request_id']} "
            f"session={debug_payload['session_debug_id']} held_ms={held_ms:.1f}"
        )
        g.operation_debug = debug_payload
        if lock:
            lock.release()


def with_session_operation_lock(operation_name: str):
    """Decorator for endpoints that mutate per-session generator/chat state."""
    def _decorator(func):
        @wraps(func)
        def _wrapped(*args, **kwargs):
            with _session_operation_context(operation_name):
                return func(*args, **kwargs)
        return _wrapped
    return _decorator


def with_generation_operation_lock(func):
    """Expose generation as pending before it waits for the session lock.

    Studio navigation can create a fresh page while a generation request is
    queued behind another session operation. Marking it in progress first lets
    /api/generation_status resume that request instead of rendering the welcome
    screen and enqueueing a duplicate generation.
    """
    @wraps(func)
    def _wrapped(*args, **kwargs):
        session_id = session.get('session_id')
        if not session_id:
            # Preserve route-level lifecycle/readiness errors that intentionally
            # precede the session requirement (notably pristine Round 0).
            g.generation_token = None
            return func(*args, **kwargs)
        token = _begin_generation(session_id)
        if not token:
            return jsonify({'success': False, 'error': 'Generation already in progress'})
        g.generation_token = token
        try:
            with _session_operation_context('api_generate'):
                return func(*args, **kwargs)
        finally:
            _end_generation(session_id, token)
    return _wrapped


def _ensure_generation_state(session_id: str) -> dict:
    state = GENERATION_STATE.get(session_id)
    if not state:
        state = {
            'in_progress': False,
            'cancel_requested': False,
            'token': None,
        }
        GENERATION_STATE[session_id] = state
    return state


def _begin_generation(session_id: str) -> str | None:
    with GENERATION_LOCK:
        state = _ensure_generation_state(session_id)
        if state.get('in_progress'):
            return None
        token = os.urandom(8).hex()
        state.update({
            'in_progress': True,
            'cancel_requested': False,
            'token': token,
        })
        return token


def _request_generation_cancel(session_id: str) -> None:
    with GENERATION_LOCK:
        state = _ensure_generation_state(session_id)
        state['cancel_requested'] = True


def _is_generation_cancelled(session_id: str, token: str | None) -> bool:
    with GENERATION_LOCK:
        state = GENERATION_STATE.get(session_id)
        if not state:
            return False
        if token and state.get('token') != token:
            return False
        return bool(state.get('cancel_requested'))


def _end_generation(session_id: str, token: str | None) -> None:
    with GENERATION_LOCK:
        state = GENERATION_STATE.get(session_id)
        if not state:
            return
        if token and state.get('token') != token:
            return
        state['in_progress'] = False
        state['cancel_requested'] = False
        state['token'] = None


def _generation_status_for_session(session_id: str | None) -> dict:
    if not session_id:
        return {
            'in_progress': False,
            'cancel_requested': False,
        }
    with GENERATION_LOCK:
        state = GENERATION_STATE.get(session_id) or {}
        return {
            'in_progress': bool(state.get('in_progress')),
            'cancel_requested': bool(state.get('cancel_requested')),
        }



def _begin_hypothesis_regeneration(session_id: str | None, index: int) -> str | None:
    if not session_id:
        return None
    with GENERATION_LOCK:
        token = os.urandom(8).hex()
        REGENERATION_STATE[session_id] = {
            'in_progress': True,
            'index': int(index),
            'token': token,
            'started_at': time.time(),
            'cancel_requested': False,
        }
        return token


def _request_hypothesis_regeneration_cancel(session_id: str | None, index: int | None = None) -> bool:
    if not session_id:
        return False
    with GENERATION_LOCK:
        state = REGENERATION_STATE.get(session_id)
        if not state or not state.get('in_progress'):
            return False
        if index is not None and int(index) != int(state.get('index') or 0):
            return False
        state['cancel_requested'] = True
        return True


def _is_hypothesis_regeneration_cancelled(session_id: str | None, token: str | None) -> bool:
    if not session_id:
        return False
    with GENERATION_LOCK:
        state = REGENERATION_STATE.get(session_id)
        if not state:
            return False
        if token and state.get('token') != token:
            return False
        return bool(state.get('cancel_requested'))


def _end_hypothesis_regeneration(session_id: str | None, token: str | None) -> None:
    if not session_id:
        return
    with GENERATION_LOCK:
        state = REGENERATION_STATE.get(session_id)
        if not state:
            return
        if token and state.get('token') != token:
            return
        REGENERATION_STATE.pop(session_id, None)


def _hypothesis_regeneration_status_for_session(session_id: str | None) -> dict:
    if not session_id:
        return {'in_progress': False, 'index': None}
    with GENERATION_LOCK:
        state = REGENERATION_STATE.get(session_id) or {}
        return {
            'in_progress': bool(state.get('in_progress')),
            'index': state.get('index'),
            'started_at': state.get('started_at'),
            'cancel_requested': bool(state.get('cancel_requested')),
        }


def _ensure_report_chat_cancel_state(session_id: str) -> dict:
    state = REPORT_CHAT_CANCEL_STATE.get(session_id)
    if not state:
        state = {
            'active_request_id': None,
            'cancel_requested': False,
        }
        REPORT_CHAT_CANCEL_STATE[session_id] = state
    return state


def _begin_report_chat_request(session_id: str, request_id: str) -> None:
    with REPORT_CHAT_CANCEL_LOCK:
        state = _ensure_report_chat_cancel_state(session_id)
        state['active_request_id'] = request_id
        state['cancel_requested'] = False


def _request_report_chat_cancel(session_id: str, request_id: str | None = None) -> bool:
    with REPORT_CHAT_CANCEL_LOCK:
        state = REPORT_CHAT_CANCEL_STATE.get(session_id)
        if not state:
            return False
        active_request_id = state.get('active_request_id')
        if not active_request_id:
            return False
        if request_id and request_id != active_request_id:
            return False
        state['cancel_requested'] = True
        return True


def _is_report_chat_cancelled(session_id: str, request_id: str | None) -> bool:
    with REPORT_CHAT_CANCEL_LOCK:
        state = REPORT_CHAT_CANCEL_STATE.get(session_id)
        if not state:
            return False
        if request_id and state.get('active_request_id') != request_id:
            return False
        return bool(state.get('cancel_requested'))


def _end_report_chat_request(session_id: str, request_id: str | None) -> None:
    with REPORT_CHAT_CANCEL_LOCK:
        state = REPORT_CHAT_CANCEL_STATE.get(session_id)
        if not state:
            return
        if request_id and state.get('active_request_id') != request_id:
            return
        state['active_request_id'] = None
        state['cancel_requested'] = False


def _ensure_hypothesis_thread_cancel_state(session_id: str) -> dict:
    state = HYPOTHESIS_THREAD_CANCEL_STATE.get(session_id)
    if not state:
        state = {
            'active_request_id': None,
            'active_index': None,
            'active_kind': None,
            'cancel_requested': False,
        }
        HYPOTHESIS_THREAD_CANCEL_STATE[session_id] = state
    return state


def _begin_hypothesis_thread_request(session_id: str | None, request_id: str | None,
                                     index=None, kind: str | None = None) -> None:
    if not session_id or not request_id:
        return
    with HYPOTHESIS_THREAD_CANCEL_LOCK:
        state = _ensure_hypothesis_thread_cancel_state(session_id)
        state['active_request_id'] = request_id
        state['active_index'] = index
        state['active_kind'] = kind
        state['cancel_requested'] = False


def _request_hypothesis_thread_cancel(session_id: str | None, request_id: str | None = None,
                                      index=None) -> bool:
    if not session_id:
        return False
    with HYPOTHESIS_THREAD_CANCEL_LOCK:
        state = HYPOTHESIS_THREAD_CANCEL_STATE.get(session_id)
        if not state:
            return False
        active_request_id = state.get('active_request_id')
        if not active_request_id:
            return False
        if request_id and request_id != active_request_id:
            return False
        if index is not None and state.get('active_index') is not None:
            try:
                if int(index) != int(state.get('active_index')):
                    return False
            except (TypeError, ValueError):
                return False
        state['cancel_requested'] = True
        return True


def _is_hypothesis_thread_cancelled(session_id: str | None, request_id: str | None) -> bool:
    if not session_id or not request_id:
        return False
    with HYPOTHESIS_THREAD_CANCEL_LOCK:
        state = HYPOTHESIS_THREAD_CANCEL_STATE.get(session_id)
        if not state or state.get('active_request_id') != request_id:
            return False
        return bool(state.get('cancel_requested'))


def _end_hypothesis_thread_request(session_id: str | None, request_id: str | None) -> None:
    if not session_id or not request_id:
        return
    with HYPOTHESIS_THREAD_CANCEL_LOCK:
        state = HYPOTHESIS_THREAD_CANCEL_STATE.get(session_id)
        if not state or state.get('active_request_id') != request_id:
            return
        state['active_request_id'] = None
        state['active_index'] = None
        state['active_kind'] = None
        state['cancel_requested'] = False


def get_generator() -> InteractiveHypothesisGenerator:
    """Get the generator for current session."""
    if 'session_id' not in session:
        return None

    session_id = session['session_id']
    gen = GENERATORS.get(session_id)
    if gen is not None:
        with _GENERATOR_LAST_USED_LOCK:
            _GENERATOR_LAST_USED[session_id] = time.time()
    return gen


def get_or_restore_generator() -> InteractiveHypothesisGenerator:
    """
    Get the generator for current session, attempting to restore it if missing.

    If the generator is missing (e.g., after server restart) but the session has
    valid project config, this will attempt to recreate and reinitialize the generator.
    """
    generator = get_generator()
    if generator:
        expected_checkpoint = _v2_resolve_current_studio_checkpoint_dir_for_project()
        current_checkpoint = getattr(generator, 'checkpoint_dir', None)
        if expected_checkpoint is not None and current_checkpoint:
            try:
                if Path(current_checkpoint).resolve() != expected_checkpoint.resolve():
                    session_id = session.get('session_id')
                    if session_id:
                        GENERATORS.pop(session_id, None)
                    print(
                        "[restore] Discarding stale Studio generator: "
                        f"{current_checkpoint} != {expected_checkpoint}"
                    )
                    generator = None
                else:
                    return generator
            except OSError:
                return generator
        else:
            return generator

    # Try to restore from session config
    print(f"[restore] Attempting to restore generator for session {session.get('session_id')}")

    project_cfg = _get_project_config()

    def _checkpoint_candidates() -> list[Path]:
        candidates: list[Path] = []
        raw_checkpoint = project_cfg.get('checkpoint_dir')
        if raw_checkpoint:
            candidates.append(Path(raw_checkpoint))
        data_path = project_cfg.get('data_path')
        if data_path:
            candidates.append(Path(data_path))
            candidates.append(Path(data_path) / 'checkpoint')
        if CLI_CHECKPOINT_DIR:
            candidates.append(Path(CLI_CHECKPOINT_DIR))
        # De-dup while preserving order
        seen: set[str] = set()
        ordered: list[Path] = []
        for cand in candidates:
            key = str(cand)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(cand)
        return ordered

    def _resolve_checkpoint_dir() -> Path | None:
        for cand in _checkpoint_candidates():
            checkpoint_path = cand.expanduser()
            if not checkpoint_path.is_absolute():
                if not checkpoint_path.exists():
                    # Try relative to DISK_PATH
                    disk_path = _get_disk_path()
                    cand_str = str(checkpoint_path)
                    disk_name = disk_path.name + os.sep
                    if cand_str.startswith(disk_name):
                        checkpoint_path = disk_path / Path(cand_str[len(disk_name):])
                    else:
                        checkpoint_path = disk_path / cand_str
            if not checkpoint_path.exists():
                continue
            resolved = _v2_resolve_current_studio_checkpoint_dir(checkpoint_path)
            if resolved.exists():
                return resolved
        return None

    resolved_checkpoint = _resolve_checkpoint_dir()
    if not resolved_checkpoint:
        print(
            "[restore] Failed: No valid checkpoint dir found. "
            f"Config keys: {list(project_cfg.keys())}, "
            f"candidates: {[str(p) for p in _checkpoint_candidates()]}"
        )
        return None

    original_checkpoint_dir = str(resolved_checkpoint)
    checkpoint_dir = str(resolved_checkpoint)
    print(f"[restore] Checkpoint dir: {original_checkpoint_dir} -> {checkpoint_dir}")

    try:
        # Recreate generator with project config
        code_dir = project_cfg.get('code_dir') or CLI_CODE_DIR
        readme_path = project_cfg.get('readme_path') or CLI_README_PATH
        research_paper_path = _get_configured_research_paper_path(project_cfg)
        print(f"[restore] Creating generator with code_dir={code_dir}, readme_path={readme_path}")

        # Prefer the settings saved at initialize time; sessions from before
        # those keys existed fall back to the previous hardcoded defaults.
        restored_model = project_cfg.get('hypothesis_model') or DEFAULT_HYPOTHESIS_MODEL
        restored_include_bg = project_cfg.get('include_background_literature')
        restored_generate_plan = project_cfg.get('generate_plan')
        generator = create_generator(
            checkpoint_dir,
            hypothesis_model=restored_model,
            code_dir=code_dir,
            include_background_literature=True if restored_include_bg is None else bool(restored_include_bg),
            generate_plan=True if restored_generate_plan is None else bool(restored_generate_plan),
            readme_path=readme_path,
            enable_citations=False,
            cache_documents=CLI_CACHE_DOCUMENTS,
            research_paper_path=research_paper_path,
        )

        # Load checkpoint data
        print("[restore] Loading checkpoint...")
        if not generator.load_checkpoint():
            print("[restore] Failed: load_checkpoint() returned False")
            return None

        project_task = (project_cfg.get('task_description') or '').strip()
        if project_task:
            generator.task_description = project_task

        # Initialize the hypothesis generation agent (required for chat)
        generator.initialize_agent()
        _restore_generated_hypotheses_if_available(generator)

        print(f"✓ Restored generator for session {session.get('session_id')}")
        return generator
    except Exception as exc:
        import traceback
        print(f"⚠️ Failed to restore generator: {exc}")
        traceback.print_exc()
        return None


def _get_project_config() -> dict:
    """Return config associated with the authenticated project key."""
    cfg = session.get('project_config')
    if not isinstance(cfg, dict):
        cfg = {}
    else:
        cfg = dict(cfg)

    checkpoint_s3_prefix = cfg.get('checkpoint_s3_prefix') or session.get('checkpoint_s3_prefix')
    if not checkpoint_s3_prefix:
        return cfg

    needs_rederive = False
    if not cfg.get('checkpoint_dir') or not cfg.get('code_dir'):
        needs_rederive = True
    else:
        readme_value = _clean_path_value(cfg.get('readme_path'))
        if not readme_value:
            needs_rederive = True
        else:
            try:
                readme_path = Path(readme_value).expanduser()
                if not readme_path.is_absolute():
                    code_dir_value = _clean_path_value(cfg.get('code_dir'))
                    if code_dir_value:
                        readme_path = Path(code_dir_value).expanduser() / readme_path
                if not readme_path.exists() or readme_path.is_dir() or readme_path.stat().st_size == 0:
                    needs_rederive = True
            except Exception:
                needs_rederive = True

    if not needs_rederive:
        derived = _derive_project_config(
            checkpoint_s3_prefix,
            cfg.get('github_remote_path'),
            readme_path=cfg.get('readme_path'),
        )
        if derived:
            current_checkpoint = _clean_path_value(cfg.get('checkpoint_dir'))
            current_code = _clean_path_value(cfg.get('code_dir'))
            current_readme = _clean_path_value(cfg.get('readme_path'))
            derived_checkpoint = _clean_path_value(derived.get('checkpoint_dir'))
            derived_code = _clean_path_value(derived.get('code_dir'))
            derived_readme = _clean_path_value(derived.get('readme_path'))
            if (
                current_checkpoint != derived_checkpoint
                or current_code != derived_code
                or current_readme != derived_readme
            ):
                needs_rederive = True

    if not needs_rederive:
        return cfg

    derived = _derive_project_config(
        checkpoint_s3_prefix,
        cfg.get('github_remote_path'),
        readme_path=cfg.get('readme_path'),
    )
    merged = dict(cfg)
    merged.update(derived)
    session['project_config'] = merged
    return merged


def _get_app_mode() -> str:
    """Return the current app mode ('selection', 'audit', or 'audit_only')."""
    mode = session.get('app_mode') or APP_MODE_SELECTION
    mode = str(mode).strip().lower()
    if mode not in (APP_MODE_SELECTION, APP_MODE_AUDIT, APP_MODE_AUDIT_ONLY):
        return APP_MODE_SELECTION
    return mode


def _is_audit_mode() -> bool:
    return _get_app_mode() == APP_MODE_AUDIT


def _is_audit_only_mode() -> bool:
    return _get_app_mode() == APP_MODE_AUDIT_ONLY


def _reject_audit_only(action: str = 'This action'):
    return jsonify({'success': False, 'error': f'{action} is disabled in audit-only mode.'}), 403


def _get_default_config_value(key: str, cli_value=None):
    """Resolve a config value, preferring the project-specific setting."""
    project_cfg = _get_project_config()
    value = project_cfg.get(key)
    if value is None and key == 'checkpoint_dir':
        data_path = project_cfg.get('data_path')
        if data_path is not None:
            return data_path
    return value if value is not None else cli_value


def _resolve_code_dir(generator: InteractiveHypothesisGenerator | None = None) -> Path | None:
    """Resolve the best available code_dir, preferring the active generator then project config."""
    if generator and getattr(generator, 'code_dir', None):
        return Path(generator.code_dir)
    code_dir = _get_default_config_value('code_dir', CLI_CODE_DIR)
    return Path(code_dir) if code_dir else None


def _resolve_code_chat_model(generator: InteractiveHypothesisGenerator | None = None) -> str:
    """Resolve the model to use for code chat, preferring config overrides."""
    model = _get_default_config_value('code_chat_model', CLI_CODE_CHAT_MODEL)
    if model:
        return model
    return DEFAULT_CHAT_MODEL


def _is_code_chat_available(code_dir: Path | None) -> bool:
    """Return True when code_dir is set and points to an existing directory."""
    if not code_dir:
        return False
    try:
        return code_dir.exists() and code_dir.is_dir()
    except Exception:
        return False


def _get_project_progress_payload() -> dict:
    """Return submission count + disk-derived round number for the current session project."""
    progress = get_project_progress(session.get('project_key_digest'))
    submission_count = progress.get('submission_count', 0)
    project_round = _get_project_round_from_disk()
    return {
        'project_submission_count': submission_count,
        'project_round': project_round,
    }


# =====================================================================
# V2 Project Home — canonical product-state adapter
# ---------------------------------------------------------------------
# Single server-side interpretation layer for Project Home. It REUSES the
# existing round-state / config / hypothesis helpers defined above rather
# than re-deriving that logic anywhere:
#   - _get_project_config()                (active project config)
#   - _get_latest_round_dir_for_session()  (latest/active round + sibling scan)
#   - _classify_round_state()              (round-state classification)
#   - _inspect_latest_round()              (per-round filesystem evidence)
#   - _locate_selected_hypothesis_context()(cross-round selected-hyp lookup)
#   - _extract_hypothesis_display_text()   (selected-hyp display parsing)
#   - _SCIENTIST_FEEDBACK_FILE             (durable review signal)
#   - unified_report route                 (full report destinations)
# No LLM is used anywhere here; every preview is deterministic. The browser
# never classifies state or infers folder relationships — it renders this.
# =====================================================================

# Markers separating a report body from its leading metadata/scaffolding.
_MEMORY_REPORT_BODY_MARKER = "## Accumulated Report"
_ROUND_REPORT_BODY_MARKER = "## Round Report"
# Scientist-feedback ledger appended inside memory_report.md; excluded from previews.
_SCIENTIST_LEDGER_MARKER = "<!-- SCIENTIST_FEEDBACK_LEDGER_START -->"

# Section headings (case-insensitive) preferred as a round's "outcome".
# Finding-bearing report section headings, in PRIORITY order. Only these are
# treated as findings -- there is deliberately NO background/first-paragraph
# fallback (background is not a finding).
_V2_FINDING_SECTION_PRIORITY = (
    "central finding", "central findings",
    "key finding", "key findings", "summary of key findings",
    "principal finding", "principal findings",
    "summary of principal finding", "summary of principal findings",
    "findings", "results", "conclusion", "verdict", "final synthesis",
)

# Minimum combined length of the first two understanding paragraphs before a
# third substantive paragraph is appended (deterministic; never mid-paragraph).
_UNDERSTANDING_MIN_CHARS = 350


def _v2_round_dirs_root(checkpoint_dir: Path) -> Path:
    """Return the directory that directly contains the round_* folders.

    project_config['checkpoint_dir'] is normally .../checkpoints/round_N; its
    parent is the round root. Falls back to a checkpoints/ subdirectory or the
    path itself for other shapes.
    """
    checkpoint_dir = Path(checkpoint_dir)
    if checkpoint_dir.name.startswith("round_"):
        return checkpoint_dir.parent
    sub = checkpoint_dir / "checkpoints"
    if sub.exists():
        return sub
    return checkpoint_dir


def _v2_round_dirs_root_from_any_checkpoint_path(checkpoint_dir: Path) -> Path:
    """Resolve the round_* parent from any project/checkpoint/round path shape."""
    checkpoint_dir = Path(checkpoint_dir)
    if checkpoint_dir.name.startswith("round_"):
        return checkpoint_dir.parent
    if checkpoint_dir.name == "checkpoints":
        return checkpoint_dir
    if (checkpoint_dir / "checkpoints").exists():
        return checkpoint_dir / "checkpoints"
    if (checkpoint_dir / "checkpoint" / "checkpoints").exists():
        return checkpoint_dir / "checkpoint" / "checkpoints"
    for parent in checkpoint_dir.parents:
        if parent.name == "checkpoints":
            return parent
        if parent.name == "checkpoint" and (parent / "checkpoints").exists():
            return parent / "checkpoints"
    return _v2_round_dirs_root(checkpoint_dir)


def _v2_iter_round_dirs(root: Path) -> list[tuple[int, Path]]:
    """Return [(index, dir), ...] for round_N folders under root, ascending."""
    out: list[tuple[int, Path]] = []
    try:
        for entry in Path(root).iterdir():
            if entry.is_dir() and entry.name.startswith("round_"):
                suffix = entry.name.split("round_", 1)[-1]
                if suffix.isdigit():
                    out.append((int(suffix), entry))
    except OSError:
        return []
    out.sort(key=lambda item: item[0])
    return out


def _v2_latest_round_dir_with_file(root: Path, filename: str) -> tuple[int | None, Path | None]:
    """(index, dir) for the highest-numbered round containing filename.

    Unlike _resolve_latest_completed_round_dir (which only checks the single
    latest round) this scans every round, so a completed earlier round is still
    found when the latest round is mid-flight.
    """
    for index, round_dir in reversed(_v2_iter_round_dirs(root)):
        if (round_dir / filename).exists():
            return index, round_dir
    return None, None


def _v2_no_round_has_started(rounds_root: Path) -> bool:
    """True only for a pristine project where NO autonomous round has ever run
    and no hypothesis work exists yet — i.e. the "start your first exploratory
    round" state that precedes the whole DiscoveryAgent lifecycle.

    Reuses the existing per-round evidence (_inspect_latest_round): a project is
    past this initial gate the moment ANY round shows research activity (query_*
    dirs, round_report.md, memory_report.md) OR any hypothesis-cycle artifact
    (generated_hypotheses.json, selected_hypothesis.md). This is the single
    canonical predicate the three screens share; it invents no new flag and
    never writes anything.

    Lifecycle note: Round 0 is an autonomous exploratory round with no
    user-selected hypothesis, so this state is exited by starting Round 0 (which
    produces query/report evidence), never by a Studio action.
    """
    try:
        rounds = _v2_iter_round_dirs(Path(rounds_root))
    except OSError:
        return False
    for _index, round_dir in rounds:
        info = _inspect_latest_round(round_dir)
        if (info['query_count'] > 0
                or info['has_round_report']
                or info['has_memory_report']
                or info['has_generated_hypotheses']
                or info['has_selected_hypothesis']):
            return False
    return True


def _v2_round_index(round_dir: Path | None) -> int | None:
    if round_dir is None:
        return None
    name = Path(round_dir).name
    if name.startswith("round_"):
        suffix = name.split("round_", 1)[-1]
        if suffix.isdigit():
            return int(suffix)
    return None


def _v2_read_research_goal(checkpoint_dir: Path) -> str | None:
    """Deterministically read the research goal (task description) from disk.

    Mirrors InteractiveHypothesisGenerator.load_checkpoint()'s resolution order
    (task_description_file, then config['task_description']) by reading the
    checkpoint's config.json directly — no generator restore, no LLM. Returns
    None when no goal is configured (the UI omits it rather than inventing one).
    """
    cfg_file = Path(checkpoint_dir) / "config.json"
    if not cfg_file.exists():
        return None
    try:
        data = json.loads(cfg_file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    cfg = data.get('config', data) if isinstance(data, dict) else {}
    if not isinstance(cfg, dict):
        return None
    task_desc_file = cfg.get('task_description_file')
    if task_desc_file:
        try:
            candidate = Path(task_desc_file)
            if candidate.exists():
                text = candidate.read_text(encoding='utf-8').strip()
                if text:
                    return text
        except OSError:
            pass
    goal = cfg.get('task_description')
    if goal and str(goal).strip():
        return str(goal).strip()
    return None


def _v2_strip_report_scaffolding(raw_text: str, body_marker: str) -> str:
    """Drop leading metadata (up to the body marker) and the trailing
    scientist-feedback ledger, returning just the report body."""
    text = raw_text or ""
    idx = text.find(body_marker)
    if idx != -1:
        text = text[idx + len(body_marker):]
    ledger_idx = text.find(_SCIENTIST_LEDGER_MARKER)
    if ledger_idx != -1:
        text = text[:ledger_idx]
    return text.strip()


def _v2_is_substantive_paragraph(block: str) -> bool:
    """Deterministic filter: True only for real scientific prose paragraphs.

    Rejects headings, horizontal rules, metadata lines, reference entries,
    standalone images, bare bold labels, pure list/table blocks, known
    boilerplate, and anything too short to be a sentence.
    """
    s = (block or "").strip()
    if not s:
        return False
    if s.startswith("#"):
        return False
    if set(s) <= set("-*_ ") and "-" in s:
        return False
    if re.match(r"^\*\*(timestamp|round|date|round number)\*\*", s, re.IGNORECASE):
        return False
    if re.match(r"^(timestamp|round)\s*:", s, re.IGNORECASE):
        return False
    if re.match(r"^\[\d+\]", s):
        return False
    if re.match(r"^!\[", s):
        return False
    if re.match(r"^\*\*[^*\n]+\*\*\s*:?\s*$", s):
        return False
    lines = [ln for ln in s.split("\n") if ln.strip()]
    if lines and all(
        re.match(r"^\s*([-*+]|\d+\.)\s+", ln) or ln.lstrip().startswith("|")
        for ln in lines
    ):
        return False
    if _BOILERPLATE_PHRASE_RE.search(s):
        return False
    plain = re.sub(r"[#*`>\[\]()!|]", "", s)
    if len(plain.split()) < 12:
        return False
    if "." not in plain:
        return False
    return True


def _v2_substantive_paragraphs(raw_text: str, body_marker: str, section_hints=None) -> list[str]:
    """Return the ordered list of substantive prose paragraphs in a report body.

    When section_hints is given, extraction starts at the first matching section
    heading (used to prefer a round's Findings/Results/Conclusion prose)."""
    body = _v2_strip_report_scaffolding(raw_text, body_marker)
    if not body:
        return []
    if section_hints:
        best_start = None
        best_end = None
        for hint in section_hints:
            pattern = re.compile(r"(?m)^#{1,6}\s*" + re.escape(hint) + r"\s*$", re.IGNORECASE)
            match = pattern.search(body)
            if match and (best_start is None or match.start() < best_start):
                best_start = match.start()
                best_end = match.end()
        if best_end is None:
            return []  # section hint(s) requested but none matched -> no section
        body = body[best_end:]
    picked: list[str] = []
    for para in re.split(r"\n\s*\n", body):
        cleaned = para.strip()
        if _v2_is_substantive_paragraph(cleaned):
            picked.append(cleaned)
    return picked


def _v2_understanding_preview(raw_text: str) -> str | None:
    """Deterministic Current Research Understanding preview from a memory_report.

    Takes the first two substantive paragraphs; if their combined length is
    under _UNDERSTANDING_MIN_CHARS, appends a third when available. Paragraphs
    are never truncated mid-paragraph. Inline Markdown is preserved.
    """
    paragraphs = _v2_substantive_paragraphs(raw_text, _MEMORY_REPORT_BODY_MARKER)
    if not paragraphs:
        return None
    picked = paragraphs[:2]
    if len("\n\n".join(picked)) < _UNDERSTANDING_MIN_CHARS and len(paragraphs) >= 3:
        picked = paragraphs[:3]
    return "\n\n".join(picked)


def _v2_understanding_full(raw_text: str) -> str | None:
    """Return the complete readable memory body for inline expansion."""
    body = _v2_strip_report_scaffolding(raw_text, _MEMORY_REPORT_BODY_MARKER)
    return body or None


# Stable central-finding lead phrases some round reports open with. When present
# verbatim they are the strongest finding sentence; preferred deterministically.
_V2_CENTRAL_FINDING_RE = re.compile(r'^\**\s*The central finding\b', re.IGNORECASE)


def _v2_round_outcome_preview(raw_text: str) -> str | None:
    """First substantive scientific finding paragraph of a round_report, VERBATIM.
    Deterministic only (no LLM, no rewriting): (1) an explicit paragraph opening
    with a stable central-finding phrase if the report wrote one, else (2) the
    first paragraph under a finding-bearing section (priority order). Returns
    None when no finding-bearing section exists (background is never a finding)."""
    # 1. explicit central-finding sentence, if the report wrote one.
    for para in _v2_substantive_paragraphs(raw_text, _ROUND_REPORT_BODY_MARKER):
        if _V2_CENTRAL_FINDING_RE.match(para):
            return para
    # 2. finding-bearing section, in priority order (findings ONLY; no background
    #    / first-substantive-paragraph fallback -- background is not a finding).
    for hint in _V2_FINDING_SECTION_PRIORITY:
        paras = _v2_substantive_paragraphs(
            raw_text, _ROUND_REPORT_BODY_MARKER, section_hints=(hint,)
        )
        if paras:
            return paras[0]
    return None  # no reliable finding -> omit (never mislabel background)


def _v2_first_sentences(text: str | None, max_chars: int = 320) -> str | None:
    """First whole sentence(s) of text up to ~max_chars. Never cuts mid-sentence:
    the first sentence is always returned whole even if it alone exceeds max_chars;
    later sentences are appended only while staying within the budget."""
    text = " ".join((text or "").split())
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    out = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        candidate = (out + " " + sentence).strip() if out else sentence
        if out and len(candidate) > max_chars:
            break
        out = candidate
        if len(out) >= max_chars:
            break
    return out or text


def _v2_round_finding(raw_text: str) -> str | None:
    """Concise central finding for a completed round (Latest Findings): the
    deterministic outcome preview compacted to one claim (~2-4 desktop lines),
    verbatim from round_report.md. No LLM, no summary artifact."""
    preview = _v2_round_outcome_preview(raw_text)
    if not preview:
        return None
    return _v2_first_sentences(preview, max_chars=320)


def _v2_latest_finding_display_preview(text: str | None) -> str | None:
    """Project Home Latest Findings display text only: remove inline emphasis
    markers and end every non-empty preview with exactly three periods."""
    if not text:
        return None
    display = re.sub(r"\*\*([^*\n]+?)\*\*", r"\1", text)
    display = re.sub(r"__([^_\n]+?)__", r"\1", display)
    display = re.sub(r"(?<!\*)\*(?!\*)([^*\n]+?)(?<!\*)\*(?!\*)", r"\1", display)
    display = re.sub(r"(?<!\w)_(?!_)([^_\n]+?)(?<!_)_(?!\w)", r"\1", display)
    display = re.sub(r"[\s.:…]+$", "", display.rstrip())
    if not display:
        return None
    return display + "..."


# Explicit "**Title**: ..." field written into some selected_hypothesis.md docs.
def _v2_first_sentence(text: str | None) -> str | None:
    """First complete substantive sentence of text, VERBATIM (no length cap, no
    mid-sentence cut). Visual clamping is left to the frontend."""
    text = " ".join((text or "").split())
    if not text:
        return None
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0].strip()
    return first or None


def _v2_evolution_direction(round_dir: Path, index: int | None) -> str | None:
    """Timeline direction for a round.

    Later rounds are driven by the selected hypothesis persisted after the
    previous round. Round 0 first uses its own selected hypothesis when present;
    "Initial data exploration" is only the fallback for a round_0 with no
    selected hypothesis artifact.
    """
    if index == 0:
        driver_path = round_dir / "selected_hypothesis.md"
        if not driver_path.exists():
            return "Initial data exploration"
    else:
        driver_path = None
        if index is not None and index >= 1:
            prev = _previous_round_dir(round_dir)
            if prev is not None:
                cand = prev / "selected_hypothesis.md"
                if cand.exists():
                    driver_path = cand
        if driver_path is None:
            return None
    try:
        raw = driver_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "Initial data exploration" if index == 0 else None
    return _v2_first_sentence(_extract_hypothesis_display_text(raw) or None)

def _v2_round_selected_context(round_dir: Path) -> tuple[str | None, str | None]:
    """(raw_selected_text, direction_text) for a round, via the existing
    cross-round lookup. Reuses _locate_selected_hypothesis_context (which checks
    round_{N-1} first, then round_N) so the browser never infers folder layout;
    and _extract_hypothesis_display_text (strips labels, stops before
    justification/plan)."""
    selected_path = _locate_selected_hypothesis_context(round_dir)
    if selected_path is None:
        return None, None
    try:
        raw = selected_path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None, None
    direction = _extract_hypothesis_display_text(raw) or None
    return raw, direction


def _v2_selected_hypothesis_text(round_dir: Path) -> str | None:
    """Display text of the hypothesis selected FOR round_dir, read from round_dir's
    OWN selected_hypothesis.md (self, not the previous round).

    This is the correct resolution for the 'hypothesis selected, round not started'
    state: save_selection() writes the fresh selection into the current (latest)
    round dir, and _classify_round_state's selected_waiting_for_backend is defined
    by that same dir holding selected_hypothesis.md with no queries. Justification
    and plan are stripped by _extract_hypothesis_display_text."""
    path = Path(round_dir) / "selected_hypothesis.md"
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    return _extract_hypothesis_display_text(raw) or None


def _v2_next_round_selection(completed_dir: Path) -> str | None:
    """Display text of a hypothesis selected for the NEXT round after a round has
    completed, per DiscoveryAgent's real lifecycle.

    save_selection() writes the next round's hypothesis into the latest existing
    round dir (the just-completed one), and the next round_N dir is not created
    until the backend runner starts it. A fresh next-round selection is therefore
    a selected_hypothesis.md in completed_dir that is NEWER than that round's
    round_report.md; an older file is that round's own launcher (written before it
    ran) and is NOT a new selection. Returns the stripped hypothesis text or None.
    """
    completed_dir = Path(completed_dir)
    report = completed_dir / "round_report.md"
    selected = completed_dir / "selected_hypothesis.md"
    if not report.exists() or not selected.exists():
        return None
    try:
        if selected.stat().st_mtime <= report.stat().st_mtime:
            return None  # this round's own launcher, not a fresh next-round selection
    except OSError:
        return None
    return _v2_selected_hypothesis_text(completed_dir)


def _v2_round_status(info: dict) -> str:
    """Single-round status from its filesystem evidence (never scans siblings)."""
    if info['has_round_report']:
        return 'completed'
    if info['query_count'] > 0:
        return 'running'
    if info['has_selected_hypothesis']:
        return 'selected'
    if info['has_generated_hypotheses']:
        return 'exploring'
    return 'pending'


def _v2_round_destination(
    index: int,
    status: str,
    latest_index: int | None,
    round_url,
    workspace_url: str | None = None,
) -> str | None:
    """Pick a click destination for a round.

    Completed rounds open their report directly inside the new Round Workspace.
    The numbered project URL remains only as a compatibility fallback.
    """
    if status == 'completed':
        if workspace_url:
            separator = '&' if '?' in workspace_url else '?'
            return f'{workspace_url}{separator}round={index}&review=1'
        return round_url(index)
    if status == 'running' and index == latest_index:
        return workspace_url or round_url(index)
    return None


# ---- Attention copy (canonical; server is the single source of truth) --------

def _v2_pre_research_attention(state: str, urls: dict) -> dict:
    """Focused pre-research state block (the whole body beneath project identity
    when no completed research understanding exists yet)."""
    table = {
        'start_first_round': {
            'label': 'NEEDS YOUR ATTENTION',
            'headline': 'Start your first round',
            'body': (
                "DiscoveryAgent works best when it begins with an exploratory "
                "round to understand your data. Afterwards, you will be able "
                "to generate hypotheses and steer the research direction."
            ),
            'cta_label': 'Start round',
            # This CTA is an action (launch the first exploratory round), not a
            # navigation: the frontend binds all three screens' buttons to the
            # same round-start action via this identifier. No destination URL.
            'cta_action': 'start_round',
            'cta_destination': None,
        },
        'explore_hypotheses': {
            'label': 'EXPLORE HYPOTHESES',
            'headline': 'DiscoveryAgent has not completed a research round yet.',
            'body': (
                "Begin by generating and comparing possible hypotheses. You can discuss "
                "them with the Hypothesis Agent, refine promising candidates, and choose "
                "what DiscoveryAgent should investigate first."
            ),
            'cta_label': 'Explore Hypotheses',
            'cta_destination': urls['studio'],
        },
        'continue_exploring_hypotheses': {
            'label': 'CONTINUE EXPLORING HYPOTHESES',
            'headline': None,
            'body': (
                "You have generated candidate hypotheses but have not yet selected which "
                "one DiscoveryAgent should investigate."
            ),
            'cta_label': 'Continue Exploring Hypotheses',
            'cta_destination': urls['studio'],
        },
        'hypothesis_selected': {
            'label': 'HYPOTHESIS SELECTED',
            'headline': None,
            'body': (
                "Your hypothesis has been selected and is ready for final review before "
                "research begins."
            ),
            'cta_label': 'Start Research Round',
            'cta_destination': urls['studio'],
        },
        'research_in_progress': {
            'label': 'RESEARCH IN PROGRESS',
            'headline': None,
            'body': "The current research round is underway.",
            'cta_label': 'Open Round Workspace',
            'cta_destination': urls['workspace'],
        },
    }
    block = dict(table.get(state, table['explore_hypotheses']))
    block['state'] = state
    block['status_pill'] = None
    block.setdefault('cta_action', None)  # only start_first_round is action-based
    return block


def _v2_briefing_attention(
    state: str, urls: dict,
    running_round: int | None,
    review_round: int | None,
    review_context: str | None,
    selected_round: int | None = None,
    selected_hypothesis: str | None = None,
    running_hypothesis: str | None = None,
) -> dict:
    """Needs-Your-Attention copy for Research Briefing mode. Exactly one CTA.

    Deterministic narrative framing = FIXED product language (`body_lead`) +
    EXACT artifact-derived scientific content (`body`). No LLM, no rewriting: the
    lead-in is UI copy and the hypothesis / finding text is verbatim from the
    resolver / report. A colon-based lead-in is used (safer than concatenating a
    sentence around arbitrary hypothesis text). When the artifact content is
    unavailable, the lead-in is dropped and a plain fallback line is shown.
    """
    running_n = running_round if running_round is not None else 0
    review_n = review_round if review_round is not None else 0
    selected_n = selected_round if selected_round is not None else 0
    table = {
        'research_in_progress': {
            'label': 'RESEARCH IN PROGRESS',
            'status_pill': 'Research in progress',
            'headline': f"Round {running_n} is currently underway.",
            'body_lead': "DiscoveryAgent is investigating:" if running_hypothesis else None,
            'body': running_hypothesis or "DiscoveryAgent is actively investigating the selected hypothesis.",
            'cta_label': 'Open Round Workspace',
            'cta_destination': urls['workspace'],
        },
        'hypothesis_selected': {
            'label': 'HYPOTHESIS SELECTED',
            'status_pill': 'Hypothesis selected',
            'headline': f"A hypothesis has been selected for Round {selected_n}.",
            'body_lead': "The selected hypothesis is:" if selected_hypothesis else None,
            # Exact selected hypothesis text (justification/plan stripped upstream).
            'body': selected_hypothesis or "A hypothesis has been selected for the next research round.",
            'cta_label': f"Start Round {selected_n}",
            'cta_destination': urls['studio'],
        },
        'continue_exploring_hypotheses': {
            'label': 'HYPOTHESES READY',
            'status_pill': 'Hypotheses ready',
            'headline': "New hypotheses are ready for review.",
            'body_lead': None,
            'body': (
                "Compare the generated hypotheses and choose the direction "
                "DiscoveryAgent should investigate next."
            ),
            'cta_label': 'Return to Hypothesis Studio',
            'cta_destination': urls['studio'],
        },
        'round_ready_for_review': {
            'label': f"ROUND {review_n} COMPLETE",
            'status_pill': f"Round {review_n} complete",
            'headline': f"Round {review_n} is complete and ready for review.",
            'body_lead': None,
            # Deterministic finding/outcome preview from the completed round report
            # (verbatim; no LLM); falls back to a neutral line.
            'body': review_context or "The latest research round is ready for your review.",
            'cta_label': f"Review Round {review_n}",
            'cta_destination': urls['latest_round_report'],
        },
        'explore_hypotheses': {
            'label': 'EXPLORE HYPOTHESES',
            'status_pill': 'Ready to explore',
            'headline': "The project is ready for its next hypothesis.",
            'body_lead': None,
            'body': "Generate and develop hypotheses for DiscoveryAgent to investigate.",
            'cta_label': 'Explore Hypotheses',
            'cta_destination': urls['studio'],
        },
    }
    block = dict(table.get(state, table['explore_hypotheses']))
    block['state'] = state
    block.setdefault('cta_action', None)  # shape-consistent with pre-research attention
    return block


def _v2_build_project_state() -> dict:
    """Canonical Project Home product state for the SESSION's active project."""
    project_cfg = _get_project_config()
    checkpoint_dir_raw = project_cfg.get('checkpoint_dir') or CLI_CHECKPOINT_DIR
    title = session.get('project_label') or 'DiscoveryAgent Project'
    research_goal = project_cfg.get('task_description') or (_v2_read_research_goal(checkpoint_dir_raw) if checkpoint_dir_raw else None)
    project_slug = session.get('v2_active_project_slug') or ''
    return _v2_project_state_for(checkpoint_dir_raw, title, research_goal, project_slug)


def _v2_active_round_dir_for(checkpoint_dir: Path) -> Path:
    """Session-independent equivalent of _get_latest_round_dir_for_session():
    resolve the active/latest round from a checkpoint dir, scanning siblings for
    a running round (reuses the existing helpers)."""
    try:
        return _resolve_active_monitor_round(_latest_round_checkpoint_dir(Path(checkpoint_dir)))
    except OSError:
        return Path(checkpoint_dir)


def _v2_project_state_for(checkpoint_dir_raw, title: str, research_goal: str | None, project_slug: str) -> dict:
    """Assemble the canonical product state for a project by its checkpoint dir.
    Pure/deterministic and free of session reads, so it can serve both Project
    Home (active project) and the Projects list (per project).

    project_slug (the project's stable, human-readable url_slug -- not the
    internal id) is required to build canonical /projects/<slug>/... URLs:
    round reports and exact historical content resolve to legacy round URLs,
    while the active Round Workspace lives at /rounds/."""
    round_url = lambda n: url_for('legacy_project_round', project_slug=project_slug, round_number=n)
    urls = {
        'studio': url_for('project_hypotheses', project_slug=project_slug),
        'workspace': url_for('project_hypotheses', project_slug=project_slug),
        # Background report is session/generator-scoped, not per-round; out of
        # scope for this round-number canonicalization.
        'memory_report': url_for('unified_report', type='background', view='full'),
    }

    project = {
        'title': title or 'DiscoveryAgent Project',
        'research_goal': research_goal,
    }

    def _pre_research(state: str) -> dict:
        return {
            'mode': 'pre_research',
            'project': project,
            'attention': _v2_pre_research_attention(state, urls),
            'research_understanding': None,
            'evolution': [],
            'recent_rounds': [],
        }

    if not checkpoint_dir_raw:
        return _pre_research('explore_hypotheses')

    checkpoint_dir = Path(checkpoint_dir_raw)
    rounds_root = _v2_round_dirs_root(checkpoint_dir)

    # --- Current action state via the existing round-state classification. -----
    active_round_dir = _v2_active_round_dir_for(checkpoint_dir)
    active_index = _v2_round_index(active_round_dir)
    urls['workspace'] = url_for('rounds_workspace') if active_index is not None else urls['studio']
    try:
        round_state = _classify_round_state(active_round_dir)
    except OSError:
        round_state = 'error_or_unknown'

    all_rounds = _v2_iter_round_dirs(rounds_root)
    latest_index = all_rounds[-1][0] if all_rounds else None

    # Latest completed round (round_report.md is the completion signal) + its
    # durable review signal (scientist_feedback.md presence).
    completed_index, completed_dir = _v2_latest_round_dir_with_file(rounds_root, 'round_report.md')
    urls['latest_round_report'] = (
        url_for('rounds_workspace', round=completed_index, review=1)
        if completed_index is not None else urls['studio']
    )
    review_pending = False
    if completed_dir is not None:
        feedback_path = completed_dir / _SCIENTIST_FEEDBACK_FILE
        feedback_saved = False
        if feedback_path.exists():
            try:
                feedback_saved = bool(feedback_path.read_text(encoding='utf-8').strip())
            except OSError:
                feedback_saved = False
        review_pending = not feedback_saved

    # Contextual selected-hypothesis resolution. The hypothesis TEXT and the round
    # NUMBER are resolved separately (they come from different state): the fresh
    # selection is stored in the just-completed round dir (cross-round convention),
    # while the round that runs NEXT is the canonical continuation (completed + 1).
    selected_round = None
    selected_hypothesis = None
    running_hypothesis = None

    # Attention-state precedence (locked): running > selected-next-hypothesis >
    # generated-none-selected > completed-awaiting-review > default exploration.
    if round_state == 'round_monitor':
        attention_state = 'research_in_progress'
        # The hypothesis driving the running round (launcher, resolved via the
        # existing cross-round lookup: round_{N-1} first, then round_N).
        _raw_run, running_hypothesis = _v2_round_selected_context(active_round_dir)
    elif round_state == 'selected_waiting_for_backend':
        # A round dir already exists with a fresh selection but has not run yet;
        # that dir IS the round about to run.
        attention_state = 'hypothesis_selected'
        selected_round = active_index
        selected_hypothesis = _v2_selected_hypothesis_text(active_round_dir)
    elif round_state == 'needs_hypothesis_selection':
        attention_state = 'continue_exploring_hypotheses'
    elif round_state == 'round_complete_needs_review':
        # Real lifecycle: after a round completes, the NEXT round's hypothesis is
        # selected into the just-completed round's dir (newer than round_report.md)
        # before the next round_N dir is created. A genuinely newer selection
        # overrides the review state, and the round that runs next is completed + 1.
        next_selection = _v2_next_round_selection(completed_dir) if completed_dir is not None else None
        if next_selection:
            attention_state = 'hypothesis_selected'
            selected_round = (completed_index + 1) if completed_index is not None else None
            selected_hypothesis = next_selection
        elif review_pending:
            attention_state = 'round_ready_for_review'
        else:
            attention_state = 'explore_hypotheses'
    else:  # needs_hypothesis_generation / error_or_unknown
        attention_state = 'explore_hypotheses'

    # Lifecycle gate: before ANY autonomous round has ever started, the project
    # is in the initial-exploration state (Round 0 is autonomous and directed by
    # no user hypothesis). This precedes every hypothesis-cycle state above, so
    # it overrides the classification-derived attention. Reuses the single
    # canonical predicate shared with the Studio and Round Workspace.
    if _v2_no_round_has_started(rounds_root):
        attention_state = 'start_first_round'

    # --- Display mode: completed authoritative memory report exists? -----------
    mem_index, mem_dir = _v2_latest_round_dir_with_file(rounds_root, 'memory_report.md')

    if mem_dir is None:
        # No completed research understanding -> focused pre-research screen.
        # round_ready_for_review cannot occur without a completed report; clamp
        # the rare "round_report but no memory_report" case to explore.
        pre_state = attention_state
        if pre_state == 'round_ready_for_review':
            pre_state = 'explore_hypotheses'
        return _pre_research(pre_state)

    # --- Research Briefing mode ------------------------------------------------
    # Contextual attention: the selected/current hypothesis (resolved above) and
    # the completed round's finding preview surface only inside Needs Your
    # Attention (no permanent Current Research Direction).
    review_context = None
    if attention_state == 'round_ready_for_review' and completed_dir is not None:
        # Deterministic concise finding preview from the completed round report.
        try:
            rr_raw = (completed_dir / 'round_report.md').read_text(encoding='utf-8', errors='replace')
            review_context = _v2_round_outcome_preview(rr_raw)
        except OSError:
            review_context = None

    attention = _v2_briefing_attention(
        attention_state, urls,
        running_round=active_index,
        review_round=completed_index,
        review_context=review_context,
        selected_round=selected_round,
        selected_hypothesis=selected_hypothesis,
        running_hypothesis=running_hypothesis,
    )

    # Current Research Understanding (preview + complete inline memory body).
    try:
        mem_raw = (mem_dir / 'memory_report.md').read_text(encoding='utf-8', errors='replace')
        preview = _v2_understanding_preview(mem_raw)
        full = _v2_understanding_full(mem_raw)
    except OSError:
        preview = None
        full = None
    research_understanding = {
        'preview_markdown': preview,
        'full_markdown': full,
        'source_round': mem_index,
    }

    # How the research evolved (chronological) + recent completed rounds.
    evolution: list[dict] = []
    completed_rounds: list[dict] = []
    for index, round_dir in all_rounds:
        info = _inspect_latest_round(round_dir)
        status = _v2_round_status(info)
        # Timeline: ONE consistent source -- selected-hypothesis content, first
        # sentence (never Title/heading for some rounds and prose for others).
        direction = _v2_evolution_direction(round_dir, index)
        display_title = direction
        destination = _v2_round_destination(index, status, latest_index, round_url, urls['workspace'])
        evolution.append({
            'round_index': index,
            'direction': direction,
            'display_title': display_title,
            'status': status,
            'is_latest': index == latest_index,
            'destination': destination,
        })
        if info['has_round_report']:
            outcome = None
            try:
                rr_raw = (round_dir / 'round_report.md').read_text(encoding='utf-8', errors='replace')
                # Latest Findings shows a CONCISE central finding (not the whole
                # report paragraph); the direction/title lives in the timeline above.
                outcome = _v2_latest_finding_display_preview(_v2_round_finding(rr_raw))
            except OSError:
                outcome = None
            completed_rounds.append({
                'round_index': index,
                'outcome_preview': outcome,
                'status': 'complete',
                'destination': destination,
            })
    recent_rounds = list(reversed(completed_rounds))[:3]

    return {
        'mode': 'research_briefing',
        'project': project,
        'attention': attention,
        'research_understanding': research_understanding,
        'evolution': evolution,
        'recent_rounds': recent_rounds,
    }


@app.route('/home')
def project_home():
    """Compatibility redirect only. The canonical Project Home URL is
    /projects/<url_slug>; this never serves a second independent Home page."""
    active_slug = session.get('v2_active_project_slug')
    active_id = session.get('v2_active_project_id')
    if active_slug and active_id and _v2_get_project(active_id):
        return redirect(url_for('project_home_canonical', project_slug=active_slug))
    return redirect(url_for('projects'))


@app.route('/api/project_state', methods=['GET'])
def api_project_state():
    """Canonical V2 product-state adapter for Project Home. No LLM; no AIR."""
    try:
        payload = _v2_build_project_state()
        payload['success'] = True
        return jsonify(payload)
    except Exception as exc:  # pragma: no cover - defensive guard
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(exc)}), 500


# =====================================================================
# V2 Hypothesis Studio — round history (read-only)
# ---------------------------------------------------------------------
# Surfaces the hypothesis-generation cycle behind each autonomous round from
# artifacts that ALREADY EXIST on disk (generated_hypotheses.json and
# selected_hypothesis.md, both written by the existing select/generate flow).
# "Round N" is the candidate set + selected hypothesis that directed round N's
# autonomous research, i.e. the round_N directory's own files. No new backend
# storage is invented here, and nothing is fabricated: fields are simply omitted
# when their source file is absent. Read-only: these endpoints never mutate a
# completed round.
# =====================================================================

def _parse_hypothesis_sections_standalone(text: str | None) -> dict:
    """Session/generator-free equivalent of
    InteractiveHypothesisGenerator.parse_hypothesis_sections (which is pure but
    an instance method). Kept in sync with that method's field markers so the
    read-only history view can parse stored hypothesis text without the cost or
    side effects of restoring a generator."""
    text = text or ''

    def _grab(pattern):
        m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        return m.group(1).strip() if m else None

    label = r'(?:\*\*)?{name}(?:\*\*)?\s*:'
    return {
        'title': _grab(
            r'(?:^|\n)\s*' + label.format(name='Title')
            + r'(.*?)(?=\n\s*(?:\*\*)?(?:Primary factor|Hypothesis)(?:\*\*)?\s*:|$)'
        ),
        'interpretation': _grab(
            r'(?:^|\n)\s*' + label.format(name='Interpretation')
            + r'(.*?)(?=\n\s*(?:\*\*)?Hypothesis(?:\*\*)?\s*:|$)'
        ),
        'hypothesis': _grab(
            r'(?:^|\n)\s*' + label.format(name='Hypothesis')
            + r'(.*?)(?=\n\s*(?:\*\*)?(?:Justification|Plan)(?:\*\*)?\s*:|$)'
        ),
        'justification': _grab(
            r'(?:^|\n)\s*' + label.format(name='Justification')
            + r'(.*?)(?=\n\s*(?:\*\*)?Plan(?:\*\*)?\s*:|$)'
        ),
        'plan': _grab(r'(?:^|\n)\s*' + label.format(name='Plan') + r'(.*?)$'),
    }


def _v2_studio_checkpoint_dir() -> Path | None:
    checkpoint_dir = _get_project_config().get('checkpoint_dir')
    return Path(checkpoint_dir) if checkpoint_dir else None


def _v2_selected_hypothesis_path_for_round(round_dir: Path) -> Path | None:
    """Resolve the selected hypothesis associated with a round's studio cycle.

    The V2 web flow writes selected_hypothesis.md into the active (own) round
    dir, so that is preferred. For older / autonomous-run layouts where the
    hypothesis that directed a round was recorded under the *previous* round
    (the cross-round convention), fall back to the canonical
    _locate_selected_hypothesis_context helper rather than inventing path
    arithmetic. This is the single mapping used by history reads.
    """
    round_dir = Path(round_dir)
    own = round_dir / 'selected_hypothesis.md'
    if own.exists():
        return own
    # No own selection. Only borrow the cross-round directing hypothesis when
    # this round has no studio cycle of its own (no generated set) -- i.e. a
    # pure autonomous round directed by the previous round's selection. If the
    # round DOES have its own generated set, a missing own selection just means
    # "not selected yet", and we must not misattribute a prior round's choice.
    if not (round_dir / 'generated_hypotheses.json').exists():
        return _locate_selected_hypothesis_context(round_dir)
    return None


def _v2_hypothesis_title_or_fallback(title: str | None, hypothesis_text: str | None) -> str | None:
    """Prefer an explicit generated title; otherwise use the hypothesis opening.

    Older selected_hypothesis.md artifacts often predate title persistence and
    contain only Hypothesis/Justification sections. The V2 card needs a scanning
    title, so use the first complete hypothesis sentence instead of surfacing an
    "Untitled" placeholder.
    """
    if title and str(title).strip():
        return str(title).strip()
    return _v2_first_sentence(hypothesis_text)


def _v2_read_selected_hypothesis(round_dir: Path) -> dict | None:
    path = _v2_selected_hypothesis_path_for_round(round_dir)
    if not path or not path.exists():
        return None
    try:
        raw = path.read_text(encoding='utf-8', errors='replace').strip()
    except OSError:
        return None
    if not raw:
        return None
    sections = _parse_hypothesis_sections_standalone(raw)
    hypothesis_text = sections['hypothesis'] or _extract_hypothesis_display_text(raw)
    return {
        'title': _v2_hypothesis_title_or_fallback(sections['title'], hypothesis_text),
        'full_text': raw,
        'hypothesis': hypothesis_text,
        'justification': sections['justification'],
        'plan': sections['plan'],
    }


def _v2_read_generated_hypotheses(round_dir: Path) -> list[dict]:
    generated: list[dict] = []
    generated_path = Path(round_dir) / 'generated_hypotheses.json'
    if not generated_path.exists():
        return generated
    try:
        payload = json.loads(generated_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return generated
    saved_ids = payload.get('hypothesis_state_ids')
    for i, hyp in enumerate(payload.get('hypotheses') or [], 1):
        sections = _parse_hypothesis_sections_standalone(hyp)
        generated.append({
            'index': i,
            'state_id': saved_ids[i - 1] if isinstance(saved_ids, list) and i - 1 < len(saved_ids) else None,
            'title': sections['title'],
            'full_text': hyp,
            'hypothesis': sections['hypothesis'],
            'justification': sections['justification'],
            'interpretation': sections['interpretation'],
            'plan': sections['plan'],
        })
    return generated


def _v2_normalize_hypothesis_snapshot(item, fallback_index: int | None = None) -> dict:
    """Return a render-ready hypothesis snapshot.

    Durable history should normally store parsed fields, but repaired/legacy
    events may contain only full_text. In that case parse the scientific sections
    here so the transcript renders only the hypothesis statement up front and
    keeps justification/plan behind the existing "View justification" affordance.
    """
    if isinstance(item, str):
        raw = item
        snap: dict = {}
    elif isinstance(item, dict):
        snap = dict(item)
        raw = snap.get('full_text') or snap.get('formatted_output') or ''
    else:
        snap = {}
        raw = ''

    if not raw and isinstance(snap.get('hypothesis'), str):
        raw = snap['hypothesis']

    parsed = _parse_hypothesis_sections_standalone(raw)
    if fallback_index is not None and snap.get('index') is None:
        snap['index'] = fallback_index
    if raw and not snap.get('full_text'):
        snap['full_text'] = raw
    for key in ('title', 'hypothesis', 'justification', 'interpretation', 'plan'):
        if not snap.get(key) and parsed.get(key):
            snap[key] = parsed[key]
    if not snap.get('hypothesis') and raw:
        snap['hypothesis'] = _extract_hypothesis_display_text(raw)
    if not snap.get('title'):
        snap['title'] = _v2_hypothesis_title_or_fallback(None, snap.get('hypothesis'))

    # Display-title safety net for historical / already-persisted sets whose
    # titles were stored before the card-title contract (e.g. a full hypothesis
    # sentence written as Title). This only rewrites the display label; the
    # scientific claim in `hypothesis` and the verbatim `full_text` are
    # untouched. `raw_title` preserves the original for debugging when changed.
    original_title = snap.get('title')
    display_title = display_card_title(original_title, snap.get('hypothesis'))
    if display_title and display_title != (original_title or '').strip():
        snap['raw_title'] = original_title
        snap['title'] = display_title
    return snap


def _v2_active_hypotheses_from_history(round_dir: Path) -> list[dict]:
    """Reconstruct the CURRENT active candidate set for a Studio round from its
    durable history (hypothesis_studio_history.json).

    The active set is the newest set-defining event (a full generation, or a
    removal carrying the re-indexed survivors) with any later per-hypothesis
    revisions/regenerations applied — the same active-version rule the frontend
    uses. This is the authoritative record when generated_hypotheses.json is
    absent (e.g. after a studio reset removed the operational cache but left the
    durable history). Returns [] when the round has no generated set in history.
    """
    try:
        history = hypothesis_history.load_history(round_dir)
    except Exception:
        return []
    events = history.get('events') or []

    set_pos = -1
    for i in range(len(events) - 1, -1, -1):
        etype = events[i].get('type')
        if etype == hypothesis_history.TYPE_HYPOTHESIS_SET_GENERATED:
            set_pos = i
            break
        if etype == hypothesis_history.TYPE_HYPOTHESIS_REMOVED and isinstance(events[i].get('hypotheses'), list):
            set_pos = i
            break
    if set_pos == -1:
        return []

    active: dict[int, dict] = {}
    for j, hyp in enumerate(events[set_pos].get('hypotheses') or [], 1):
        snap = _v2_normalize_hypothesis_snapshot(hyp, j)
        idx = snap.get('index') or j
        snap['index'] = idx
        active[idx] = snap

    for i in range(set_pos + 1, len(events)):
        etype = events[i].get('type')
        if etype in (hypothesis_history.TYPE_HYPOTHESIS_REVISED,
                     hypothesis_history.TYPE_HYPOTHESIS_REGENERATED):
            idx = events[i].get('hypothesis_index')
            after = events[i].get('after')
            if idx in active and isinstance(after, dict):
                snap = _v2_normalize_hypothesis_snapshot(after, idx)
                snap['index'] = idx
                active[idx] = snap

    return [active[k] for k in sorted(active.keys())]


def _valid_hypothesis_thread_id(value) -> str | None:
    """Return a normalized durable thread id without accepting arbitrary text."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 80 or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        return None
    return value


def _valid_history_result_id(value) -> str | None:
    """Normalize a browser-supplied durable event identity (never a thread id)."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 128 or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        return None
    return value


def _studio_regeneration_history_for_slot(generator, index: int) -> dict:
    """Resolve slot-local regeneration results from the latest set boundary.

    Each result is keyed by the durable set/regeneration event id and carries
    the latest edit of that result plus its associated thread when available.
    Full-set generation establishes a new boundary. Removal deletes only that
    lineage and reindexes survivors without discarding their prior results.
    """
    round_dir = _studio_history_round_dir(generator)
    if round_dir is None:
        return {'results': [], 'active_position': -1}
    events = hypothesis_history.load_history(round_dir).get('events') or []
    boundary = -1
    for pos in range(len(events) - 1, -1, -1):
        event = events[pos]
        if event.get('type') == hypothesis_history.TYPE_HYPOTHESIS_SET_GENERATED:
            boundary = pos
            break
    if boundary < 0:
        return {'results': [], 'active_position': -1}

    boundary_event = events[boundary]
    lineages: list[dict | None] = []
    for position, raw in enumerate(boundary_event.get('hypotheses') or [], 1):
        snapshot = _v2_normalize_hypothesis_snapshot(raw, position)
        slot = int(snapshot.get('index') or position)
        while len(lineages) < slot:
            lineages.append(None)
        lineages[slot - 1] = {
            'results': [{'result_event_id': boundary_event.get('id'), 'snapshot': snapshot}],
            'active_position': 0,
        }
    if not boundary_event.get('id'):
        return {'results': [], 'active_position': -1}

    for event in events[boundary + 1:]:
        event_type = event.get('type')
        if event_type == hypothesis_history.TYPE_HYPOTHESIS_REMOVED:
            removed_index = int(event.get('hypothesis_index') or 0)
            if 0 < removed_index <= len(lineages):
                lineages.pop(removed_index - 1)
                # Retain each surviving lineage and all of its versions; only
                # refresh the active snapshot with its new display index/state.
                for position, raw in enumerate(event.get('hypotheses') or [], 1):
                    snapshot = _v2_normalize_hypothesis_snapshot(raw, position)
                    slot = int(snapshot.get('index') or position)
                    if not (0 < slot <= len(lineages)) or not lineages[slot - 1]:
                        continue
                    lineage = lineages[slot - 1]
                    active_position = lineage['active_position']
                    if 0 <= active_position < len(lineage['results']):
                        lineage['results'][active_position] = {
                            'result_event_id': lineage['results'][active_position]['result_event_id'],
                            'snapshot': snapshot,
                        }
            continue
        event_index = int(event.get('hypothesis_index') or 0)
        if not (0 < event_index <= len(lineages)) or not lineages[event_index - 1]:
            continue
        lineage = lineages[event_index - 1]
        results = lineage['results']
        active_position = lineage['active_position']
        after = event.get('after')
        if event_type == hypothesis_history.TYPE_HYPOTHESIS_REGENERATED and isinstance(after, dict):
            # A legacy generated-set snapshot may predate durable thread ids.
            # The regeneration's `before` snapshot is the authoritative final
            # state of the prior result, so use it to recover that association.
            before = event.get('before') or {}
            before_thread_id = _valid_hypothesis_thread_id(
                before.get('hypothesis_thread_id')
            )
            if (before_thread_id and 0 <= active_position < len(results)
                    and not _valid_hypothesis_thread_id(
                        results[active_position]['snapshot'].get('hypothesis_thread_id')
                    )):
                prior_snapshot = dict(results[active_position]['snapshot'])
                prior_snapshot['hypothesis_thread_id'] = before_thread_id
                results[active_position] = {
                    'result_event_id': results[active_position]['result_event_id'],
                    'snapshot': prior_snapshot,
                }
            if event.get('id'):
                results.append({
                    'result_event_id': event['id'],
                    'snapshot': _v2_normalize_hypothesis_snapshot(after, event_index),
                })
                active_position = len(results) - 1
                lineage['active_position'] = active_position
            continue
        if event_type != hypothesis_history.TYPE_HYPOTHESIS_REVISED or not isinstance(after, dict):
            continue
        if event.get('action') == 'edit_regeneration_version':
            source_id = event.get('source_result_event_id')
            matched = next((i for i, result in enumerate(results)
                            if result['result_event_id'] == source_id), None)
            if matched is None:
                # Older activation events used only a position. Keep them
                # readable, but new browser requests must use a durable id.
                requested = event.get('regeneration_position')
                if isinstance(requested, int) and 0 <= requested < len(results):
                    matched = requested
            if matched is not None:
                active_position = matched
                lineage['active_position'] = active_position
        if 0 <= active_position < len(results):
            results[active_position] = {
                'result_event_id': results[active_position]['result_event_id'],
                'snapshot': _v2_normalize_hypothesis_snapshot(after, event_index),
            }
    if not (0 < int(index) <= len(lineages)) or not lineages[int(index) - 1]:
        return {'results': [], 'active_position': -1}
    return lineages[int(index) - 1]


def _studio_resolve_regeneration_result(generator, index: int, result_event_id: str) -> dict | None:
    result_event_id = _valid_history_result_id(result_event_id)
    if not result_event_id:
        return None
    history = _studio_regeneration_history_for_slot(generator, index)
    for position, result in enumerate(history['results']):
        if result['result_event_id'] != result_event_id:
            continue
        snapshot = dict(result['snapshot'])
        return {
            'result_event_id': result_event_id,
            'regeneration_position': position,
            'snapshot': snapshot,
            'hypothesis_thread_id': _valid_hypothesis_thread_id(
                snapshot.get('hypothesis_thread_id')
            ),
            'thread_recoverable': bool(_valid_hypothesis_thread_id(
                snapshot.get('hypothesis_thread_id')
            )),
            'active': position == history['active_position'],
        }
    return None


_HYPOTHESIS_VERSION_REFERENCE_RE = re.compile(r'\bversion\s*#?\s*(\d+)\b', re.IGNORECASE)


def _hypothesis_version_reference_context(generator, index: int, message: str) -> str | None:
    """Resolve explicit user-facing ``version N`` references for chat.

    Regeneration deliberately gives each result an isolated thread, so an older
    result is not otherwise present in the current result's model context. Keep
    ordinary prompts small: attach stored hypothesis text only when the user
    explicitly names a version, and resolve the number against the durable
    slot-local regeneration order rather than an internal candidate index.
    """
    requested: list[int] = []
    for raw in _HYPOTHESIS_VERSION_REFERENCE_RE.findall(message or ''):
        number = int(raw)
        if number not in requested:
            requested.append(number)
        if len(requested) >= 4:
            break
    if not requested:
        return None

    history = _studio_regeneration_history_for_slot(generator, int(index))
    results = history.get('results') or []
    active_position = int(history.get('active_position', -1))
    lines = [
        'The user explicitly referenced stored regeneration versions of this hypothesis. '
        'Use the exact mappings below; do not reconstruct or guess an unavailable version.'
    ]
    remaining_chars = 24000
    for number in requested:
        position = number - 1
        if position < 0 or position >= len(results):
            lines.append(f'\nVersion {number}: Not available in stored history.')
            continue
        if position == active_position:
            lines.append(
                f'\nVersion {number} (current): This is the current hypothesis already supplied above.'
            )
            continue
        snapshot = results[position].get('snapshot') or {}
        text = str(snapshot.get('full_text') or snapshot.get('formatted_output') or '').strip()
        if not text:
            fields = []
            for label, key in (
                ('Title', 'title'), ('Hypothesis', 'hypothesis'),
                ('Justification', 'justification'), ('Interpretation', 'interpretation'),
                ('Plan', 'plan'),
            ):
                value = str(snapshot.get(key) or '').strip()
                if value:
                    fields.append(f'**{label}**: {value}')
            text = '\n\n'.join(fields)
        if not text:
            lines.append(f'\nVersion {number}: Stored, but its hypothesis text is unavailable.')
            continue
        excerpt = text[:remaining_chars]
        lines.append(f'\nVersion {number} (stored exact text):\n{excerpt}')
        remaining_chars -= len(excerpt)
        if remaining_chars <= 0:
            break
    return '\n'.join(lines)


def _v2_public_hypothesis_snapshot(item, fallback_index=None) -> dict:
    snapshot = _v2_normalize_hypothesis_snapshot(item, fallback_index)
    snapshot['thread_recoverable'] = bool(
        _valid_hypothesis_thread_id(snapshot.get('hypothesis_thread_id'))
    )
    snapshot.pop('hypothesis_thread_id', None)
    return snapshot


def _v2_public_history_events(events: list[dict]) -> list[dict]:
    """Return API-safe history without internal stable thread identifiers."""
    def clean(value):
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {
                key: clean(item)
                for key, item in value.items()
                if key not in {'thread_id', 'hypothesis_thread_id'}
            }
        return value
    return [clean(event) for event in events]


def _v2_result_id_by_thread(events: list[dict]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    active_result_by_index: dict[int, str] = {}
    for event in events:
        event_type = event.get('type')
        if event_type == hypothesis_history.TYPE_HYPOTHESIS_SET_GENERATED:
            active_result_by_index = {}
            for position, raw in enumerate(event.get('hypotheses') or [], 1):
                idx = int((raw or {}).get('index') or position) if isinstance(raw, dict) else position
                if event.get('id'):
                    active_result_by_index[idx] = event['id']
                thread_id = _valid_hypothesis_thread_id(
                    raw.get('hypothesis_thread_id') if isinstance(raw, dict) else None
                )
                if thread_id and event.get('id'):
                    mapping[thread_id] = event['id']
        elif event_type == hypothesis_history.TYPE_HYPOTHESIS_REMOVED:
            removed_index = int(event.get('hypothesis_index') or 0)
            if removed_index > 0:
                active_result_by_index = {
                    (idx if idx < removed_index else idx - 1): result_id
                    for idx, result_id in active_result_by_index.items()
                    if idx != removed_index
                }
            # Surviving snapshots retain stable thread ids. Map those ids to
            # their existing result identities rather than the removal event.
            for position, raw in enumerate(event.get('hypotheses') or [], 1):
                idx = int((raw or {}).get('index') or position) if isinstance(raw, dict) else position
                thread_id = _valid_hypothesis_thread_id(
                    raw.get('hypothesis_thread_id') if isinstance(raw, dict) else None
                )
                result_id = active_result_by_index.get(idx)
                if thread_id and result_id:
                    mapping[thread_id] = result_id
        elif event_type == hypothesis_history.TYPE_HYPOTHESIS_REGENERATED:
            idx = int(event.get('hypothesis_index') or 0)
            before = event.get('before') or {}
            before_thread_id = _valid_hypothesis_thread_id(
                before.get('hypothesis_thread_id')
            )
            prior_result_id = active_result_by_index.get(idx)
            if before_thread_id and prior_result_id:
                mapping[before_thread_id] = prior_result_id
            after = event.get('after') or {}
            thread_id = _valid_hypothesis_thread_id(after.get('hypothesis_thread_id'))
            if thread_id and event.get('id'):
                mapping[thread_id] = event['id']
            if idx and event.get('id'):
                active_result_by_index[idx] = event['id']
        elif event_type == hypothesis_history.TYPE_HYPOTHESIS_REVISED:
            idx = int(event.get('hypothesis_index') or 0)
            if event.get('action') == 'edit_regeneration_version':
                source_id = _valid_history_result_id(event.get('source_result_event_id'))
                if idx and source_id:
                    active_result_by_index[idx] = source_id
            after = event.get('after') or {}
            thread_id = _valid_hypothesis_thread_id(after.get('hypothesis_thread_id'))
            active_result_id = active_result_by_index.get(idx)
            if thread_id and active_result_id:
                mapping[thread_id] = active_result_id
    return mapping


def _v2_events_to_transcript(events: list[dict]) -> list[dict]:
    """Project durable history events onto the frontend transcript vocabulary
    (message / hypotheses / selection), passing richer mutation events through
    so a future UI can render them (the current UI safely ignores unknown kinds)."""
    transcript = []
    result_id_by_thread = _v2_result_id_by_thread(events)
    for ev in events:
        etype = ev.get('type')
        if etype in (hypothesis_history.TYPE_USER_MESSAGE, hypothesis_history.TYPE_ASSISTANT_MESSAGE):
            role = 'assistant' if etype == hypothesis_history.TYPE_ASSISTANT_MESSAGE else 'user'
            item = {'kind': 'message', 'role': role, 'content': ev.get('content') or ''}
            if ev.get('phase'):
                item['phase'] = ev['phase']
            if ev.get('hypothesis_index') is not None:
                item['hypothesis_index'] = ev['hypothesis_index']
            result_event_id = result_id_by_thread.get(ev.get('thread_id'))
            if result_event_id:
                item['result_event_id'] = result_event_id
            transcript.append(item)
        elif etype == hypothesis_history.TYPE_HYPOTHESIS_SET_GENERATED:
            transcript.append({
                'kind': 'hypotheses',
                'result_event_id': ev.get('id'),
                'hypotheses': [
                    _v2_public_hypothesis_snapshot(h, i)
                    for i, h in enumerate(ev.get('hypotheses') or [], 1)
                ],
            })
        elif etype == hypothesis_history.TYPE_HYPOTHESIS_REVISED:
            item = {'kind': 'hypothesis_revised', 'hypothesis_index': ev.get('hypothesis_index'),
                    'before': _v2_public_hypothesis_snapshot(ev.get('before')),
                    'after': _v2_public_hypothesis_snapshot(ev.get('after'))}
            after_thread_id = (ev.get('after') or {}).get('hypothesis_thread_id')
            result_event_id = result_id_by_thread.get(after_thread_id)
            if result_event_id:
                item['result_event_id'] = result_event_id
            if ev.get('action'):
                item['action'] = ev['action']
            if ev.get('regeneration_position') is not None:
                item['regeneration_position'] = ev['regeneration_position']
            if ev.get('source_result_event_id'):
                item['source_result_event_id'] = ev['source_result_event_id']
            transcript.append(item)
        elif etype == hypothesis_history.TYPE_HYPOTHESIS_REGENERATED:
            transcript.append({'kind': 'hypothesis_regenerated', 'hypothesis_index': ev.get('hypothesis_index'),
                               'result_event_id': ev.get('id'),
                               'before': _v2_public_hypothesis_snapshot(ev.get('before')),
                               'after': _v2_public_hypothesis_snapshot(ev.get('after'))})
        elif etype == hypothesis_history.TYPE_HYPOTHESIS_REMOVED:
            transcript.append({
                'kind': 'hypothesis_removed',
                'hypothesis_index': ev.get('hypothesis_index'),
                'result_event_id': ev.get('id'),
                'removed': _v2_public_hypothesis_snapshot(ev.get('removed')),
                # Re-indexed surviving set snapshot: projection never has to
                # reconstruct index shifts itself.
                'hypotheses': [
                    _v2_public_hypothesis_snapshot(h, i)
                    for i, h in enumerate(ev.get('hypotheses') or [], 1)
                ],
            })
        elif etype == hypothesis_history.TYPE_HYPOTHESIS_SELECTED:
            transcript.append({
                'kind': 'selection',
                # Do not invent index 1 for a durable selection whose index was
                # not stored. The frontend can match its exact text/state id
                # against generated, revised, or post-removal card snapshots.
                'hypothesis': _v2_public_hypothesis_snapshot(ev.get('hypothesis') or {}),
            })
    return transcript


def _v2_synthesized_events(round_dir: Path, generated: list[dict], selected: dict | None) -> list[dict]:
    """Backward-compatible event list for rounds with no durable history log:
    the generated set + the closing selection. If a legacy round retained only its selected artifact,
    surface that real artifact as its sole card so the completed-round UI can
    still identify the final choice. Never fabricates chat turns. Marked partial
    by callers."""
    events: list[dict] = []
    seq = 1
    if not generated and selected:
        selected = _v2_normalize_hypothesis_snapshot(selected, 1)
        generated = [selected]
    elif selected and len(generated) == 1 and selected.get('index') is None:
        # A one-card legacy set is unambiguous, so retain its historical index.
        selected = _v2_normalize_hypothesis_snapshot(
            selected, generated[0].get('index') or 1
        )
    if generated:
        events.append({'type': hypothesis_history.TYPE_HYPOTHESIS_SET_GENERATED, 'sequence': seq,
                       'phase': hypothesis_history.PHASE_HYPOTHESIS_DISCUSSION, 'hypotheses': generated})
        seq += 1
    if selected:
        events.append({'type': hypothesis_history.TYPE_HYPOTHESIS_SELECTED, 'sequence': seq,
                       'phase': hypothesis_history.PHASE_HYPOTHESIS_DISCUSSION, 'hypothesis': selected})
        seq += 1
    return events


def _v2_target_round_number(storage_round_index: int) -> int:
    """Map a Hypothesis Studio storage directory index to the product-facing
    autonomous round number the cycle's selected hypothesis directs.

    Proven from the autonomous runner's convention (AIR HypothesisBrancher):
    at autonomous round_idx (> 0) it reads ``round_{round_idx - 1}/selected_hypothesis.md``
    as the directing hypothesis, and round_idx 0 never reads a human selection.
    Therefore the selection a studio cycle writes into ``round_K`` directs
    autonomous round ``K + 1``. So the conversation stored in round_K is, in
    product terms, "Round K+1". This is a proven +1 for studio cycles — NOT an
    assumption — and checkpoint directory indices are never shown as labels.

    The current cycle (before its autonomous round has started) uses the same
    rule: it is labelled by the round it is preparing to direct.
    """
    return storage_round_index + 1


def _v2_latest_completed_round_index(rounds_root: Path) -> int | None:
    """Storage index of the latest completed autonomous round.

    Completion is research-output based (round_report.md), matching the Project
    Home/Round Monitor interpretation. Hypothesis Studio history artifacts never
    decide which cycle is current.
    """
    for index, round_dir in reversed(_v2_iter_round_dirs(rounds_root)):
        if _inspect_latest_round(round_dir)['has_round_report']:
            return index
    return None


def _v2_current_studio_round_index(rounds_root: Path, checkpoint_dir: Path) -> int | None:
    """Current Studio storage index: latest completed autonomous round.

    Product label is still target = storage + 1, so completed autonomous
    round_3 means the current Hypothesis Studio cycle is "Round 4" stored in
    round_3. For non-pristine edge states without a completed report, fall back
    to the active checkpoint so older in-progress local data remains readable.
    """
    completed_index = _v2_latest_completed_round_index(rounds_root)
    if completed_index is not None:
        return completed_index
    return _v2_round_index(_v2_active_round_dir_for(checkpoint_dir))


def _v2_resolve_current_studio_checkpoint_dir(checkpoint_dir: Path) -> Path:
    """Resolve the round dir that should own the current Hypothesis Studio cycle.

    V2 project records can legitimately keep ``checkpoint_dir`` at the original
    round_0 directory while later autonomous rounds accumulate on disk. Studio
    state, however, must be stored beside the latest completed autonomous round:
    completed round_3 owns the Studio cycle preparing product-facing Round 4.
    """
    checkpoint_dir = Path(checkpoint_dir)
    rounds_root = _v2_round_dirs_root_from_any_checkpoint_path(checkpoint_dir)
    active_index = _v2_current_studio_round_index(rounds_root, checkpoint_dir)
    if active_index is not None:
        return rounds_root / f'round_{active_index}'
    try:
        return _latest_round_checkpoint_dir(_resolve_checkpoint_root(checkpoint_dir))
    except OSError:
        return checkpoint_dir


def _v2_resolve_current_studio_checkpoint_dir_for_project() -> Path | None:
    project_cfg = _get_project_config()
    raw = project_cfg.get('checkpoint_dir') or CLI_CHECKPOINT_DIR
    if not raw:
        return None
    try:
        return _v2_resolve_current_studio_checkpoint_dir(Path(raw))
    except Exception:
        return None


def _v2_round_activity_signature(round_dir: Path) -> str | None:
    """Stable-ish client-visible marker for unseen Studio activity.

    Only substantive DiscoveryAgent-side activity counts: generated sets,
    assistant replies, mutations, removals, and selections. Plain scientist
    user turns should not create an unread dot for the same scientist.
    """
    interesting_types = {
        hypothesis_history.TYPE_ASSISTANT_MESSAGE,
        hypothesis_history.TYPE_HYPOTHESIS_SET_GENERATED,
        hypothesis_history.TYPE_HYPOTHESIS_REVISED,
        hypothesis_history.TYPE_HYPOTHESIS_REGENERATED,
        hypothesis_history.TYPE_HYPOTHESIS_REMOVED,
        hypothesis_history.TYPE_HYPOTHESIS_SELECTED,
    }
    history = hypothesis_history.load_history(round_dir)
    events = [e for e in history.get('events', []) if e.get('type') in interesting_types]
    if events:
        latest = events[-1]
        seq = latest.get('sequence') if latest.get('sequence') is not None else len(events)
        stamp = latest.get('timestamp') or ''
        return f"history:{seq}:{stamp}"

    candidates = []
    for name in ('generated_hypotheses.json', 'selected_hypothesis.md'):
        path = Path(round_dir) / name
        try:
            if path.exists():
                candidates.append((name, path.stat().st_mtime_ns))
        except OSError:
            continue
    if not candidates:
        return None
    name, mtime = max(candidates, key=lambda item: item[1])
    return f"artifact:{name}:{mtime}"


def _v2_history_round_entry(index: int, round_dir: Path, active_index: int | None) -> dict:
    info = _inspect_latest_round(round_dir)
    has_durable = (Path(round_dir) / hypothesis_history.HISTORY_FILENAME).exists()
    target = _v2_target_round_number(index)
    return {
        # Storage addressing key (the checkpoint round_* dir index). Kept for
        # backward compatibility: the detail endpoint is addressed by this.
        'round_index': index,
        'storage_round_index': index,
        # Product-facing autonomous round this cycle directs, and its label.
        'target_round_number': target,
        'label': f'Round {target}',
        'status': _v2_round_status(info),
        'is_current': index == active_index,
        'has_generated': info['has_generated_hypotheses'],
        'has_selected': info['has_selected_hypothesis'],
        # Additive: whether ANY chronology exists for this round (durable log or
        # reconstructable artifacts), and whether the autonomous round completed.
        'has_history': has_durable or info['has_generated_hypotheses'] or info['has_selected_hypothesis'],
        'is_complete': info['has_round_report'],
        'activity_signature': _v2_round_activity_signature(round_dir),
    }


@app.route('/api/hypothesis_history', methods=['GET'])
def api_hypothesis_history():
    """List each round's hypothesis cycle for the Hypothesis Studio sidebar.
    Read-only; sourced entirely from existing round_* directories."""
    try:
        checkpoint_dir = _v2_studio_checkpoint_dir()
        if checkpoint_dir is None:
            return jsonify({'success': True, 'rounds': [], 'current_round_index': None})
        root = _v2_round_dirs_root(checkpoint_dir)
        # Before the first exploratory round has ever started there are no Studio
        # cycles yet (Round 0 is autonomous, not a conversation). Return an empty
        # sidebar and the initial-exploration signal so the Studio shows the
        # "Start your first round" callout instead of a phantom "Round 1" row.
        if _v2_no_round_has_started(root):
            return jsonify({
                'success': True, 'rounds': [], 'current_round_index': None,
                'current_target_round_number': None, 'initial_exploration': True,
            })
        active_index = _v2_current_studio_round_index(root, checkpoint_dir)
        rounds = [
            _v2_history_round_entry(index, round_dir, active_index)
            for index, round_dir in _v2_iter_round_dirs(root)
        ]
        # The live cycle's directory may not exist yet (the backend runner
        # creates round_N lazily); still show it as the current, interactive row.
        if active_index is not None and not any(r['round_index'] == active_index for r in rounds):
            target = _v2_target_round_number(active_index)
            rounds.append({
                'round_index': active_index, 'storage_round_index': active_index,
                'target_round_number': target, 'label': f'Round {target}',
                'status': 'needs_hypothesis_generation', 'is_current': True,
                'has_generated': False, 'has_selected': False,
                'has_history': False, 'is_complete': False,
                'activity_signature': _v2_round_activity_signature(root / f'round_{active_index}'),
            })
        # Order by the product-facing target autonomous round number (ascending);
        # equivalent to storage-index order since target = storage + 1.
        rounds.sort(key=lambda r: r['target_round_number'])
        return jsonify({
            'success': True,
            'rounds': rounds,
            'current_round_index': active_index,
            'current_target_round_number': (
                _v2_target_round_number(active_index) if active_index is not None else None
            ),
            'initial_exploration': False,
        })
    except Exception as exc:  # pragma: no cover - defensive guard
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/hypothesis_history/<int:round_index>', methods=['GET'])
def api_hypothesis_history_round(round_index):
    """Return one round's Hypothesis Studio conversation as an ordered event
    history (read-only unless it is the current cycle).

    Source preference, deterministic and truthful:
      1. The durable per-round event log (hypothesis_studio_history.json) when
         present — the authoritative full chronology going forward.
      2. Otherwise a best-effort reconstruction from artifacts that DO exist
         (generated_hypotheses.json and the canonically
         resolved selected hypothesis), marked partial_history=True. Never
         fabricates scientist/DiscoveryAgent messages.

    Read-only: no LLM call, no generator restore, no active-round switch, no
    Flask state mutation.
    """
    try:
        checkpoint_dir = _v2_studio_checkpoint_dir()
        if checkpoint_dir is None:
            return jsonify({'success': False, 'error': 'No active project'}), 404
        root = _v2_round_dirs_root(checkpoint_dir)
        round_dir = root / f'round_{round_index}'
        if not round_dir.exists():
            return jsonify({'success': False, 'error': 'Round not found'}), 404

        active_index = _v2_current_studio_round_index(root, checkpoint_dir)
        is_current = round_index == active_index
        info = _inspect_latest_round(round_dir)

        history = hypothesis_history.load_history(round_dir)
        durable_events = history.get('events') or []

        # Flat backward-compatible fields (existing tests/consumers read these).
        generated = _v2_read_generated_hypotheses(round_dir)
        selected = _v2_read_selected_hypothesis(round_dir)
        if (is_current
                and not generated
                and not (round_dir / 'selected_hypothesis.md').exists()
                and not durable_events):
            # The current Studio cycle after a completed autonomous round may be
            # completely empty. Do not borrow the previous round's directing
            # hypothesis via the cross-round legacy fallback in that state.
            selected = None

        if durable_events:
            events = durable_events
            partial_history = False
        else:
            # Backward-compatible reconstruction from generated/selected artifacts.
            events = _v2_synthesized_events(round_dir, generated, selected)
            partial_history = bool(generated or selected)
            # Historical rounds read straight from files carry pre-contract
            # titles; give the flat fields the same display-safe titles the
            # transcript path applies (the durable branch already normalizes).
            generated = [
                _v2_normalize_hypothesis_snapshot(h, i)
                for i, h in enumerate(generated or [], 1)
            ]
            if selected:
                selected = _v2_normalize_hypothesis_snapshot(selected)

        # Derive flat generated/selected from durable events when files were
        # replaced by a later cycle but the event snapshot is authoritative.
        if durable_events:
            # Removal events carry the re-indexed surviving set, so they define
            # the flat "generated" list the same way a full set event does.
            gen_events = [e for e in durable_events
                          if e.get('type') in (hypothesis_history.TYPE_HYPOTHESIS_SET_GENERATED,
                                               hypothesis_history.TYPE_HYPOTHESIS_REMOVED)]
            if gen_events and gen_events[-1].get('hypotheses') is not None:
                generated = [
                    _v2_public_hypothesis_snapshot(h, i)
                    for i, h in enumerate(gen_events[-1]['hypotheses'] or [], 1)
                ]
            sel_events = [e for e in durable_events
                          if e.get('type') == hypothesis_history.TYPE_HYPOTHESIS_SELECTED]
            if sel_events:
                selected = _v2_public_hypothesis_snapshot(
                    sel_events[-1].get('hypothesis') or selected
                )

        transcript = _v2_events_to_transcript(events)

        target = _v2_target_round_number(round_index)
        return jsonify({
            'success': True,
            # Storage addressing key preserved (this endpoint is addressed by the
            # checkpoint round_* dir index); product label is target-based.
            'round_index': round_index,
            'storage_round_index': round_index,
            'target_round_number': target,
            'label': f'Round {target}',
            'status': _v2_round_status(info),
            'is_current': is_current,
            # Selection closes the Studio cycle. It remains durable, read-only
            # history even while this is still the filesystem's current round.
            'read_only': (not is_current) or bool(selected),
            'has_history': bool(durable_events) or bool(generated) or bool(selected),
            'partial_history': partial_history,
            # Internal stable thread ids stay server-side. The transcript uses
            # durable result-event identities to associate discussion safely.
            'events': _v2_public_history_events(events),
            'generated_hypotheses': generated,
            'selected_hypothesis': selected,
            'transcript': transcript,
            'activity_signature': _v2_round_activity_signature(round_dir),
        })
    except Exception as exc:  # pragma: no cover - defensive guard
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(exc)}), 500


# =====================================================================
# V2 Projects — local project registry, listing, creation, activation
# ---------------------------------------------------------------------
# Additive layer above the existing single-project lifecycle. Projects live
# under the existing DISK_PATH data root (no AIR_DATA_DIR exists); the registry
# is DISK_PATH/v2_projects.json (atomic writes, serialized mutation). Project
# state/status is NEVER re-derived here — it reuses _v2_project_state_for()
# (the Slice 1 canonical interpretation). The frontend infers nothing.
# =====================================================================

_V2_PROJECTS_LOCK = threading.Lock()
# Artifacts that count as real research activity for status / "Updated" time.
_V2_ACTIVITY_FILES = (
    'generated_hypotheses.json', 'selected_hypothesis.md',
    'round_report.md', 'memory_report.md', 'scientist_feedback.md',
)


def _v2_data_root() -> Path:
    """Local data root for V2 project storage."""
    return _get_disk_path()


def _v2_projects_registry_path() -> Path:
    return _v2_data_root() / "v2_projects.json"


def _v2_load_projects() -> list[dict]:
    path = _v2_projects_registry_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    projects = data.get('projects') if isinstance(data, dict) else None
    if not isinstance(projects, list):
        return []
    return [p for p in projects if isinstance(p, dict)]


def _v2_save_projects(projects: list[dict]) -> None:
    """Atomic registry write (tmp file + rename). Callers hold _V2_PROJECTS_LOCK."""
    path = _v2_projects_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    tmp.write_text(json.dumps({'projects': projects}, indent=2, ensure_ascii=False), encoding='utf-8')
    tmp.replace(path)


def _v2_get_project(project_id: str) -> dict | None:
    for p in _v2_load_projects():
        if p.get('id') == project_id:
            return p
    return None


def _v2_slugify(name: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', (name or '').strip().lower()).strip('-')
    return slug[:48] or 'project'


def _v2_new_project_id(display_name: str) -> str:
    base = _v2_slugify(display_name)
    root = _v2_data_root()
    for _ in range(50):
        candidate = f"{base}-{uuid.uuid4().hex[:6]}"
        if not (root / candidate).exists() and _v2_get_project(candidate) is None:
            return candidate
    return f"{base}-{uuid.uuid4().hex}"


def _v2_sanitize_filename(filename: str) -> str | None:
    """Return a safe basename (no directories, no traversal), or None."""
    name = os.path.basename((filename or '').replace('\\', '/')).strip()
    name = name.replace('/', '').replace('\x00', '')
    if name in ('', '.', '..'):
        return None
    name = re.sub(r'[^A-Za-z0-9._-]', '_', name)
    name = name.lstrip('.') or name  # avoid hidden/".."-style names
    return name[:200] or None


def _v2_unique_target(directory: Path, filename: str) -> Path:
    """A path inside directory that won't overwrite an existing file."""
    target = directory / filename
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for i in range(1, 1000):
        candidate = directory / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem}_{uuid.uuid4().hex[:6]}{suffix}"


def _v2_sanitize_relative_upload_path(relative_path: str | None, fallback_filename: str | None = None) -> Path | None:
    """Safe project-data relative path, preserving selected-folder structure."""
    if not relative_path:
        safe = _v2_sanitize_filename(fallback_filename or '')
        return Path(safe) if safe else None
    raw = relative_path.replace('\\', '/').strip()
    if not raw:
        return None
    raw = raw.lstrip('/')
    parts = []
    for part in raw.split('/'):
        if part in ('', '.', '..'):
            return None
        safe = _v2_sanitize_filename(part)
        if not safe:
            return None
        parts.append(safe)
    return Path(*parts) if parts else None


def _v2_save_project_upload(project: dict, storage, relative_path: str | None = None) -> Path:
    """Save one uploaded file under the project's data dir without reading it."""
    files_dir = _v2_project_files_dir(project)
    if not files_dir:
        raise ValueError('Project data directory is not configured.')
    safe_rel = _v2_sanitize_relative_upload_path(relative_path, getattr(storage, 'filename', None))
    if not safe_rel:
        raise ValueError('Upload path is invalid.')

    files_root = Path(files_dir)
    target_dir = files_root / safe_rel.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    root_resolved = files_root.resolve()
    target_dir_resolved = target_dir.resolve()
    if not target_dir_resolved.is_relative_to(root_resolved):
        raise ValueError('Upload path escapes the project data directory.')

    target = _v2_unique_target(target_dir, safe_rel.name)
    if target.resolve().parent != target_dir_resolved:
        raise ValueError('Upload path escapes the project data directory.')
    storage.save(str(target))
    return target


def _v2_project_task_description(project: dict | None) -> str:
    """Project-level research objective stored as DiscoveryAgent task_description."""
    if not isinstance(project, dict):
        return ''
    for key in ('task_description', 'research_objective'):
        value = project.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def _normalize_task_description(value) -> str:
    """Canonical whitespace normalization for the project Research Objective."""
    return " ".join(str(value or '').split())


def _v2_unique_default_project_name(existing_projects: list[dict], exclude_id: str | None = None) -> str:
    """Return a user-safe untitled project name that does not collide."""
    taken = {
        str(p.get('display_name') or '').strip().lower()
        for p in existing_projects
        if p.get('id') != exclude_id and str(p.get('display_name') or '').strip()
    }
    base = 'Untitled project'
    if base.lower() not in taken:
        return base
    n = 2
    while f'{base} {n}'.lower() in taken:
        n += 1
    return f'{base} {n}'


def _v2_project_files_dir(project: dict) -> Path | None:
    """Return where user-uploaded research files live for this project.

    New projects use code/data because DiscoveryAgent's autonomous runner treats
    working_directory/data as the dataset location. Older projects did not have
    data_dir and stored uploads directly in code_dir, so keep that fallback.
    """
    data_dir = project.get('data_dir') if isinstance(project, dict) else None
    if data_dir:
        return Path(data_dir)
    code_dir = project.get('code_dir') if isinstance(project, dict) else None
    return Path(code_dir) if code_dir else None


def _v2_write_round_config(round_dir: Path, updates: dict) -> None:
    """Merge deterministic V2 project fields into a round config.json."""
    cfg_path = Path(round_dir) / 'config.json'
    payload: dict = {}
    if cfg_path.exists():
        try:
            loaded = json.loads(cfg_path.read_text(encoding='utf-8'))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, ValueError):
            payload = {}
    config = payload.get('config') if isinstance(payload.get('config'), dict) else payload
    if not isinstance(config, dict):
        config = {}
    config.update(updates)
    cfg_path.write_text(json.dumps({'config': config}, indent=2), encoding='utf-8')


def _v2_sync_project_round_config(record: dict) -> None:
    """Propagate project-level fields into round_0 config deterministically."""
    code_dir = record.get('code_dir')
    checkpoint_dir = record.get('checkpoint_dir')
    if not code_dir or not checkpoint_dir:
        return
    _v2_write_round_config(Path(checkpoint_dir), {
        'working_directory': str(code_dir),
        'task_description': _v2_project_task_description(record),
        'readme_path': record.get('readme_path') or None,
        'hypothesis_model': record.get('hypothesis_model') or DEFAULT_HYPOTHESIS_MODEL,
        'dataset_name': record.get('display_name') or 'Untitled project',
    })


def _v2_sync_active_project_session(record: dict) -> None:
    """Keep active Flask session config aligned with the project registry."""
    if session.get('v2_active_project_id') != record.get('id'):
        return
    session['project_label'] = record.get('display_name')
    existing_cfg = session.get('project_config') if isinstance(session.get('project_config'), dict) else {}
    synced_cfg = dict(existing_cfg)
    synced_cfg.update({
        'code_dir': record.get('code_dir'),
        'data_dir': record.get('data_dir'),
        'readme_path': record.get('readme_path'),
        'task_description': _v2_project_task_description(record),
    })
    session['project_config'] = synced_cfg


def _active_project_record() -> dict | None:
    project_id = session.get('v2_active_project_id')
    if not project_id:
        return None
    return _v2_get_project(project_id)


def _resolve_canonical_task_description(generator=None) -> str:
    """Resolve canonical project Research Objective for generation gates."""
    record = _active_project_record()
    task_description = _normalize_task_description(_v2_project_task_description(record)) if record else ''
    if not task_description:
        project_cfg = _get_project_config()
        task_description = _normalize_task_description(project_cfg.get('task_description'))
    if not task_description and generator is not None:
        task_description = _normalize_task_description(getattr(generator, 'task_description', ''))
    if task_description and generator is not None:
        generator.task_description = task_description
    return task_description


def _missing_task_description_response():
    return jsonify({
        'success': False,
        'code': 'missing_task_description',
        'error': 'missing_task_description',
        'message': 'DiscoveryAgent needs a research objective before it can propose research directions.',
    }), 400


def _resolve_canonical_project_context(generator=None) -> str:
    """Resolve the canonical, MEANINGFUL Project Context for readiness gates.

    Project Context is the canonical README (the project registry's
    dataset_context, mirrored into code/README.md). Only genuine, human/
    project-authored context counts: the generator's synthesized
    "Dataset: <name>" fallback (select_hypothesis.load_checkpoint) is NOT a
    meaningful context and is deliberately excluded here.

    Resolution order: active project record's dataset_context, then the actual
    README file content, then the generator's dataset_description only when it
    is not the synthetic fallback.
    """
    record = _active_project_record()
    if record:
        ctx = (record.get('dataset_context') or '').strip()
        if ctx:
            return ctx

    project_cfg = _get_project_config()
    readme_path = project_cfg.get('readme_path')
    if readme_path:
        content, _err = _load_readme_content(readme_path)
        if content and content.strip():
            return content.strip()

    if generator is not None:
        dataset_desc = (getattr(generator, 'dataset_description', '') or '').strip()
        dataset_name = ''
        cfg = getattr(generator, 'config', None)
        if isinstance(cfg, dict):
            dataset_name = str(cfg.get('dataset_name') or '').strip()
        synthetic = f"Dataset: {dataset_name}".strip() if dataset_name else ''
        # Exclude the synthetic "Dataset: <name>" fallback; anything else is
        # real README content the generator loaded.
        if dataset_desc and dataset_desc != synthetic and not dataset_desc.startswith('Dataset: '):
            return dataset_desc

    return ''


def _check_project_readiness(generator=None) -> dict:
    """The readiness rule for hypothesis generation.

    A hypothesis cycle is ready only when BOTH a meaningful Research Objective
    AND a meaningful Project Context are present. Returns the resolved values
    plus a 'missing' field naming the first absent one (objective first, so the
    existing missing_task_description contract keeps precedence).
    """
    task_description = _resolve_canonical_task_description(generator)
    project_context = _resolve_canonical_project_context(generator)
    if not task_description:
        missing = 'task_description'
    elif not project_context:
        missing = 'project_context'
    else:
        missing = None
    return {
        'task_description': task_description,
        'project_context': project_context,
        'missing': missing,
    }


def _missing_project_context_response():
    return jsonify({
        'success': False,
        'code': 'missing_project_context',
        'error': 'missing_project_context',
        'message': 'DiscoveryAgent needs project context (the dataset/README) before it can propose research directions.',
    }), 400


def _missing_readiness_response(readiness: dict):
    """Structured missing-field response matching V2's readiness-flow contract."""
    if readiness.get('missing') == 'project_context':
        return _missing_project_context_response()
    return _missing_task_description_response()


def _project_setup_incomplete_response():
    """Friendly legacy-only guard for hypothesis mutation paths.

    V2 project creation now REQUIRES the Research Objective and Project Context,
    so a normal V2 project always satisfies this invariant and never sees this
    response — there is intentionally no duplicate objective/context readiness
    gate at generation time. It fires only for a legacy/incomplete project that
    somehow reaches generation without those fields, and it says so plainly
    instead of surfacing the old technical missing_* codes.
    """
    return jsonify({
        'success': False,
        'code': 'project_setup_incomplete',
        'error': 'project_setup_incomplete',
        'message': ('Project setup is incomplete: this project is missing its research '
                    'objective or project context. Please finish setting up the project, '
                    'then try again.'),
    }), 400


def _active_v2_project_pre_round_0() -> bool:
    """True only when the ACTIVE V2 project is still in the pristine pre-Round-0
    state (no autonomous round has started). This is the same canonical predicate
    and checkpoint resolution the Studio page uses for the blocking overlay.

    Returns False for non-V2 / legacy sessions (no active project record), so the
    guard is a no-op outside the V2 project lifecycle.
    """
    record = _active_project_record()
    if not record:
        return False
    checkpoint_dir = record.get('checkpoint_dir') or _get_project_config().get('checkpoint_dir')
    if not checkpoint_dir:
        return False
    try:
        return _v2_no_round_has_started(_v2_round_dirs_root(Path(checkpoint_dir)))
    except OSError:
        return False


def _initial_exploration_required_response():
    """Lifecycle guard for hypothesis generation before exploratory Round 0.

    Distinct from project_setup_incomplete: this is NOT a setup error (the
    project may be fully configured) — it is a lifecycle error. Round 0 is the
    autonomous exploratory round DiscoveryAgent uses to understand the project
    before Hypothesis Studio begins; generation is not available until it has
    started. Never surfaces the old missing_task_description/missing_project_context
    codes.
    """
    return jsonify({
        'success': False,
        'code': 'initial_exploration_required',
        'message': ('Start the first exploratory round before generating hypotheses. '
                    'DiscoveryAgent uses Round 0 to understand your project context and '
                    'data before Hypothesis Studio begins.'),
    }), 400


def _v2_overview_excerpt(text: str | None, limit: int = 200) -> str | None:
    """Short single-line overview excerpt for a Projects row."""
    if not text:
        return None
    collapsed = " ".join(str(text).split())
    if not collapsed:
        return None
    if len(collapsed) <= limit:
        return collapsed
    cut = collapsed[:limit].rsplit(' ', 1)[0] or collapsed[:limit]
    return cut.rstrip(' .,;:') + '…'


def _v2_round_metadata(checkpoint_dir_raw) -> tuple[str, bool]:
    """Return (round_metadata_label, is_new)."""
    if not checkpoint_dir_raw:
        return "New project", True
    rounds = _v2_iter_round_dirs(_v2_round_dirs_root(Path(checkpoint_dir_raw)))
    if not rounds:
        return "New project", True
    has_activity = any(
        any((rd / f).exists() for f in _V2_ACTIVITY_FILES) or _list_query_dirs(rd)
        for _idx, rd in rounds
    )
    if not has_activity:
        return "New project", True
    count = len(rounds)
    return (f"{count} rounds" if count != 1 else "1 round"), False


def _v2_last_activity_ts(checkpoint_dir_raw, fallback_iso: str | None) -> str | None:
    """ISO (UTC) timestamp of the newest meaningful artifact, else fallback."""
    newest = None
    if checkpoint_dir_raw:
        for _idx, rd in _v2_iter_round_dirs(_v2_round_dirs_root(Path(checkpoint_dir_raw))):
            for p in [rd / f for f in _V2_ACTIVITY_FILES] + _list_query_dirs(rd):
                try:
                    if p.exists():
                        m = p.stat().st_mtime
                        newest = m if newest is None else max(newest, m)
                except OSError:
                    pass
    if newest is None:
        return fallback_iso
    return datetime.fromtimestamp(newest, tz=timezone.utc).isoformat()


def _v2_latest_completed_number(state: dict) -> int | None:
    recent = state.get('recent_rounds') or []
    if recent:
        return recent[0].get('round_index')
    ru = state.get('research_understanding')
    if ru and ru.get('source_round') is not None:
        return ru.get('source_round')
    return None


def _v2_status_for(state: dict) -> tuple[str, str]:
    """(status_label, status_tone) derived from the canonical attention state."""
    attn = state['attention']['state']
    if attn == 'research_in_progress':
        return "In Progress", "active"
    if attn == 'hypothesis_selected':
        return "Hypothesis Selected", "neutral"
    if attn == 'continue_exploring_hypotheses':
        return "Exploring Hypotheses", "neutral"
    completed_n = _v2_latest_completed_number(state)
    if attn == 'round_ready_for_review':
        return (f"Round {completed_n} Complete" if completed_n is not None else "Round Complete"), "review"
    # explore_hypotheses: completed-and-reviewed (briefing) vs never started
    if state.get('mode') == 'research_briefing' and completed_n is not None:
        return f"Round {completed_n} Complete", "review"
    return "Not Started", "neutral"


def _v2_project_summary(project_id, display_name, overview_source, checkpoint_dir_raw,
                        research_goal, created_iso, updated_iso, url_slug: str | None = None) -> dict:
    """Assemble one Projects-row summary. Reuses the canonical state for status.
    Every project is a V2-created, editable project (no legacy distinction).

    url_slug is the project's stable, human-readable routing identity; id
    remains the internal/storage identity only. Both are returned so the
    frontend never has to reconstruct one from the other; home_url is the
    server-generated canonical Project Home link (avoids duplicating route
    construction in JavaScript)."""
    try:
        state = _v2_project_state_for(checkpoint_dir_raw, display_name, research_goal, url_slug or '')
        status_label, status_tone = _v2_status_for(state)
    except Exception:
        status_label, status_tone = "Not Started", "neutral"
    round_metadata, is_new = _v2_round_metadata(checkpoint_dir_raw)
    updated_at = _v2_last_activity_ts(checkpoint_dir_raw, updated_iso or created_iso)
    # A project counts as "touched" (show "Updated ...") once it has research
    # activity OR its registry metadata has been edited since creation.
    edited = bool(created_iso and updated_iso and updated_iso != created_iso)
    show_updated = (not is_new) or edited
    return {
        'id': project_id,
        'url_slug': url_slug,
        'home_url': url_for('project_home_canonical', project_slug=url_slug) if url_slug else None,
        'display_name': display_name or 'DiscoveryAgent Project',
        'overview': _v2_overview_excerpt(overview_source),
        'status_label': status_label,
        'status_tone': status_tone,
        'round_metadata': round_metadata,
        'is_new': is_new,
        'updated_at': updated_at,
        'created_at': created_iso,
        'show_updated': show_updated,
    }


def _v2_activate_v2_project(project: dict) -> None:
    """Bind the session to a locally-created V2 project."""
    session['project_config'] = {
        'checkpoint_dir': project.get('checkpoint_dir'),
        'code_dir': project.get('code_dir'),
        'data_dir': project.get('data_dir'),
        'readme_path': project.get('readme_path'),
        'data_path': project.get('project_root'),
        'task_description': _v2_project_task_description(project),
        'hypothesis_model': project.get('hypothesis_model') or DEFAULT_HYPOTHESIS_MODEL,
    }
    session['project_label'] = project.get('display_name')
    session['v2_active_project_id'] = project.get('id')
    session['v2_active_project_slug'] = project.get('url_slug')
    session.pop('checkpoint_s3_prefix', None)  # local project: no S3 sync
    session['app_mode'] = APP_MODE_SELECTION


def _v2_unique_url_slug(display_name: str, existing_projects: list[dict]) -> str:
    """Deterministic, human-readable URL slug from display_name (hyphens
    only; reuses _v2_slugify's normalization: lowercase, collapse
    whitespace/underscores/punctuation to single hyphens, strip leading/
    trailing hyphens). Collisions resolve to the smallest available numeric
    suffix (-2, -3, ...), checked against both existing url_slug values and
    internal ids so a slug can never collide with any project's identity."""
    base = _v2_slugify(display_name)
    taken = {p['url_slug'] for p in existing_projects if p.get('url_slug')}
    taken |= {p['id'] for p in existing_projects if p.get('id')}
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _v2_ensure_url_slugs_backfilled() -> None:
    """One-time-per-registry-state migration: assign a deterministic url_slug
    to any V2 registry record created before this field existed (this repo's
    only pre-existing local registry). Idempotent and cheap once every record
    has a slug -- checks first and returns without writing when nothing is
    missing, so this never rewrites the registry on every page view. Never
    touches created_at/updated_at/id/directories -- routing identity only.
    V1/legacy registries and .auth_submissions are untouched."""
    with _V2_PROJECTS_LOCK:
        projects = _v2_load_projects()
        if all(p.get('url_slug') for p in projects):
            return
        taken = {p['url_slug'] for p in projects if p.get('url_slug')}
        taken |= {p['id'] for p in projects if p.get('id')}
        for p in projects:
            if p.get('url_slug'):
                continue
            base = _v2_slugify(p.get('display_name') or '')
            slug = base
            n = 2
            while slug in taken:
                slug = f"{base}-{n}"
                n += 1
            p['url_slug'] = slug
            taken.add(slug)
        _v2_save_projects(projects)


def _v2_resolve_project_by_slug_or_id(value: str) -> tuple[dict | None, bool]:
    """Resolve a project-scoped canonical route's path segment. Priority:
    (1) exact url_slug match (canonical), (2) exact internal id match (narrow
    compatibility for pre-slug links/bookmarks -- the second return value
    flags this so the caller redirects to the canonical slug URL instead of
    serving a second URL for the same project), (3) no match -> caller 404s.
    Runs the one-time slug backfill first so legacy registries resolve
    correctly without a separate migration step."""
    _v2_ensure_url_slugs_backfilled()
    projects = _v2_load_projects()
    for p in projects:
        if p.get('url_slug') == value:
            return p, False
    for p in projects:
        if p.get('id') == value:
            return p, True
    return None, False


def _v2_activate_project(project: dict) -> dict:
    """Shared entry point for every project-scoped canonical route
    (/projects/<slug>, /projects/<slug>/hypotheses, /projects/<slug>/rounds/<n>):
    bind the session to an already-resolved V2 project record and stamp
    last_opened_at so the Projects list sort (most recently viewed/opened)
    reflects real navigation, not just an explicit Continue click. Returns
    the up-to-date record (post-stamp) for callers that need it."""
    with _V2_PROJECTS_LOCK:
        projects = _v2_load_projects()
        record = next((p for p in projects if p.get('id') == project.get('id')), project)
        record['last_opened_at'] = datetime.now(tz=timezone.utc).isoformat()
        _v2_save_projects(projects)
    _v2_activate_v2_project(record)
    return record


@app.route('/projects')
def projects():
    """V2 Projects screen (cross-project entry point; simplified shell)."""
    return render_template('projects.html', bare_shell=True, page_class='v2-page-projects')


def _settings_render_context(page_class: str, settings_section: str) -> dict:
    """Shared V2 shell context for every Settings page."""
    settings_context = request.args.get('context', '').strip().lower()
    if settings_context not in {'project', 'projects'}:
        settings_context = (
            'project' if session.get('v2_active_project_slug') else 'projects'
        )

    project_slug = ''
    if settings_context == 'project':
        project_slug = (
            request.args.get('project_slug', '').strip()
            or session.get('v2_active_project_slug')
            or ''
        )
    project_section = request.args.get('project_section', '').strip().lower()
    if project_section not in {'home', 'studio', 'workspace', 'audit'}:
        project_section = 'home'
    project = None
    if project_slug:
        project, matched_by_id = _v2_resolve_project_by_slug_or_id(project_slug)
        if project and matched_by_id:
            project_slug = project.get('url_slug') or project_slug
    return {
        'bare_shell': not bool(project_slug),
        'nav_active': project_section if settings_context == 'project' else '',
        'settings_active': True,
        'page_class': page_class,
        'settings_section': settings_section,
        'project_slug': project_slug,
        'settings_context': settings_context,
        'project_section': project_section,
        'show_settings_back': settings_context == 'projects',
        'round_workspace_url': (
            _v2_round_workspace_nav_url(project, project_slug)
            if project else (url_for('rounds_workspace') if project_slug else None)
        ),
    }

def _anthropic_credential_is_configured() -> bool:
    """Return only credential presence; never expose credential material."""
    try:
        return credential_store.has_anthropic_api_key()
    except credential_store.CredentialStoreError:
        return False


@app.route('/settings/general')
def general_settings():
    return render_template(
        'general_settings.html',
        **_settings_render_context('v2-page-general-settings', 'general'),
    )


@app.route('/settings/model-provider')
def model_provider_settings():
    context = _settings_render_context(
        'v2-page-model-provider-settings', 'model_provider',
    )
    context['anthropic_configured'] = _anthropic_credential_is_configured()
    return render_template('model_provider_settings.html', **context)


@app.route('/api/settings/model-provider/credentials/anthropic', methods=['GET', 'PUT', 'DELETE'])
def api_model_provider_anthropic_credential():
    try:
        if request.method == 'GET':
            return jsonify({'configured': credential_store.has_anthropic_api_key()})

        if request.method == 'PUT':
            payload = request.get_json(silent=True)
            api_key = payload.get('api_key') if isinstance(payload, dict) else None
            if not isinstance(api_key, str) or not api_key.strip():
                return jsonify({'error': 'Enter an Anthropic API key before saving.'}), 400
            api_key = api_key.strip()
            if len(api_key) > 4096:
                return jsonify({'error': 'The Anthropic API key is too long.'}), 400
            credential_store.set_anthropic_api_key(api_key)
            return jsonify({'configured': True})

        credential_store.delete_anthropic_api_key()
        return jsonify({'configured': False})
    except credential_store.CredentialStoreError:
        return jsonify({
            'error': (
                'Secure credential storage is unavailable. Configure this computer’s '
                'operating system credential manager and try again.'
            )
        }), 503
def _v2_round_workspace_nav_url(project: dict, project_slug: str) -> str | None:
    """Top-nav 'Round Workspace' target for any active V2 project that has
    a checkpoint root. The short /rounds/ route owns the user-facing workspace
    screen, including the empty/pre-round state."""
    if project.get('checkpoint_dir'):
        return url_for('rounds_workspace')
    return None


def _v2_active_round_workspace_url_for_session() -> str | None:
    """User-facing /rounds/ URL for the SESSION's active V2 project's
    currently running round, or None for legacy/non-V2 sessions."""
    if not session.get('v2_active_project_slug'):
        return None
    checkpoint_dir = _get_project_config().get('checkpoint_dir')
    if not checkpoint_dir:
        return None
    try:
        active_round_dir = _v2_active_round_dir_for(Path(checkpoint_dir))
        if _classify_round_state(active_round_dir) != 'round_monitor':
            return None
        if _v2_round_index(active_round_dir) is None:
            return None
    except OSError:
        return None
    return url_for('rounds_workspace')


@app.route('/projects/<project_slug>')
def project_home_canonical(project_slug):
    """Canonical V2 Project Home deep link, addressed by the project's stable
    human-readable url_slug. Works directly in a fresh tab: resolves +
    activates the project from the registry (never relies only on whatever
    project happened to already be active in the session), then renders the
    same Project Home template/state machinery used everywhere. A URL that
    happens to match an internal project id (a pre-slug bookmark) redirects
    to the canonical slug URL rather than serving a second URL for the
    project."""
    project, matched_by_id = _v2_resolve_project_by_slug_or_id(project_slug)
    if project is None:
        abort(404)
    if matched_by_id:
        return redirect(url_for('project_home_canonical', project_slug=project['url_slug']))
    project = _v2_activate_project(project)
    return render_template(
        'project_home.html', nav_active='home', page_class='v2-page-home',
        project_slug=project_slug,
        round_workspace_url=_v2_round_workspace_nav_url(project, project_slug),
    )


@app.route('/projects/<project_slug>/hypotheses')
def project_hypotheses(project_slug):
    """Canonical V2 Hypothesis Studio deep link, addressed by url_slug.

    Activates the project via the same shared helper as Project Home, then
    renders the V2 Hypothesis Studio (a new, permanent template -- NOT the
    legacy index.html workspace). The redesigned page reuses every existing
    hypothesis backend API (initialize/generate/chat/edit/regenerate/select).
    A pre-slug internal-id URL redirects to the canonical
    slug URL. The existing restoration behavior is preserved: if a research
    round is already running for this project, we redirect to the Round
    Workspace exactly as app_interface() does, rather than showing the studio.
    """
    project, matched_by_id = _v2_resolve_project_by_slug_or_id(project_slug)
    if project is None:
        abort(404)
    if matched_by_id:
        return redirect(url_for('project_hypotheses', project_slug=project['url_slug']))
    project = _v2_activate_project(project)

    # Audit mode is a legacy, reviewer-only concept that V2 projects never use;
    # defer to the legacy interface if it is somehow active rather than showing
    # a studio that would reject every mutation.
    if _is_audit_mode():
        return app_interface()

    # Keep Studio available during an autonomous run as read-only history.
    # The browser receives the running round number and bypasses every
    # initialize/generation mutation path.
    checkpoint_dir = project.get('checkpoint_dir')
    initial_exploration = False
    running_round_index = None
    if checkpoint_dir:
        try:
            latest_round_dir = _latest_round_checkpoint_dir(Path(checkpoint_dir))
            active_round_dir = _resolve_active_monitor_round(latest_round_dir)
            if active_round_dir.exists() and _classify_round_state(active_round_dir) == 'round_monitor':
                running_round_index = _v2_round_index(active_round_dir)
        except OSError:
            pass
        # Before ANY autonomous round has ever started, the Studio must show the
        # pre-Round-0 blocking overlay instead of the welcome / generation UI.
        # Round 0 is autonomous and hypothesis-less; the first Studio workspace
        # (Round 1) only becomes available once Round 0 has activity. Same
        # canonical predicate as Project Home and Round Workspace, so all three
        # agree.
        try:
            initial_exploration = _v2_no_round_has_started(_v2_round_dirs_root(Path(checkpoint_dir)))
        except OSError:
            initial_exploration = False

    return render_template(
        'hypothesis_studio.html',
        nav_active='studio',
        page_class='v2-page-studio',
        project_slug=project_slug,
        round_workspace_url=_v2_round_workspace_nav_url(project, project_slug),
        # True only before the first exploratory round has ever started; the
        # frontend renders the "Start your first round" blocking overlay and
        # keeps the normal Studio surfaces inert/suppressed in this state.
        initial_exploration=initial_exploration,
        running_round_index=running_round_index,
        # The number of hypotheses is configurable in the composer (default 3,
        # allowed 1-10). These are passed as config rather than hardcoded into
        # the reusable interaction logic.
        default_num_hypotheses=3,
        min_num_hypotheses=1,
        max_num_hypotheses=10,
    )


@app.route('/projects/<project_slug>/audit')
def project_audit_agent(project_slug):
    """Project-scoped Audit Agent tab.

    This embeds the legacy audit chat experience into the V2 project shell
    without changing the whole session into legacy audit mode.
    """
    project, matched_by_id = _v2_resolve_project_by_slug_or_id(project_slug)
    if project is None:
        abort(404)
    if matched_by_id:
        return redirect(url_for('project_audit_agent', project_slug=project['url_slug']))
    project = _v2_activate_project(project)

    project_cfg = _get_project_config()
    checkpoint_dir = project_cfg.get('checkpoint_dir') or project.get('checkpoint_dir') or CLI_CHECKPOINT_DIR
    code_dir = project_cfg.get('code_dir') or project.get('code_dir') or CLI_CODE_DIR
    github_url = project.get('github_url') or project_cfg.get('github_url') or CLI_GITHUB_URL

    research_paper_pdf_url = None
    research_paper_pdf_available = False
    pdf_path = _resolve_research_paper_pdf_path(
        None,
        checkpoint_dir,
        _get_configured_research_paper_path(project_cfg),
    )
    if pdf_path:
        research_paper_pdf_available = True
        research_paper_pdf_url = url_for('research_paper_pdf')

    audit_guide_html = None
    audit_guide_path = Path(__file__).resolve().parent.parent / 'guides' / 'AUDIT_UI.md'
    if audit_guide_path.is_file():
        try:
            audit_guide_html = render_markdown_to_html(audit_guide_path.read_text(encoding='utf-8'))
        except Exception:
            audit_guide_html = None

    _write_interaction_log('audit_session_start', {
        'checkpoint_dir': checkpoint_dir,
        'code_dir': code_dir,
        'project_slug': project.get('url_slug'),
        'surface': 'v2_project_tab',
    })
    log_event('audit_session_start', {'checkpoint_dir': checkpoint_dir, 'surface': 'v2_project_tab'})

    return render_template(
        'project_audit_agent.html',
        nav_active='audit',
        page_class='v2-page-audit',
        project_slug=project['url_slug'],
        round_workspace_url=_v2_round_workspace_nav_url(project, project['url_slug']),
        checkpoint_dir=checkpoint_dir,
        code_dir=code_dir,
        github_url=github_url,
        research_paper_pdf_url=research_paper_pdf_url,
        research_paper_pdf_available=research_paper_pdf_available,
        audit_guide_html=Markup(audit_guide_html) if audit_guide_html else None,
        chat_storage_scope=f"{_audit_chat_storage_scope()}:v2:{project.get('url_slug')}",
    )


def _render_round_report_view(project: dict, round_dir: Path, round_number: int):
    """Render the report/review experience for ONE SPECIFIC round directory.

    Sourced ENTIRELY from round_dir's own round_report.md / round_report_
    summary.md / memory_report.md -- never from session['generator'] state,
    never from 'latest completed round' inference. This is what makes
    /legacy/projects/<slug>/rounds/<n> truthful for every completed round (the
    latest one AND any historical one), not just whichever round the
    session's generator happens to be pointed at. Reuses the existing
    report.html template and render_checkpoint_markdown_html (the same
    generator-independent markdown+asset renderer the checkpoint browser
    already uses for arbitrary in-tree files) rather than duplicating report
    rendering."""
    report_path = round_dir / 'round_report.md'
    if not report_path.exists():
        abort(404)
    try:
        raw = report_path.read_text(encoding='utf-8', errors='replace').strip()
    except OSError:
        abort(404)
    match = re.search(r'## Round Report\n\n(.*)', raw, re.DOTALL)
    full_text = match.group(1).strip() if match else raw

    summary_text = None
    summary_path = round_dir / 'round_report_summary.md'
    if summary_path.exists():
        try:
            summary_text = summary_path.read_text(encoding='utf-8', errors='replace').strip() or None
        except OSError:
            pass

    has_summary = bool(summary_text)
    has_full = bool(full_text)
    view = request.args.get('view', 'summary')
    if view == 'summary' and not has_summary:
        view = 'full'
    if view == 'full' and not has_full:
        view = 'summary'

    # Project-scoped (not generator-scoped) asset resolution, so images embedded
    # in THIS round's report resolve from THIS round's own directory.
    code_dir = Path(project['code_dir']).resolve() if project.get('code_dir') else None
    checkpoint_dir_for_assets = round_dir.parent.resolve()

    def _render(md: str | None) -> str | None:
        if not md:
            return None
        return render_checkpoint_markdown_html(
            md, checkpoint_dir=checkpoint_dir_for_assets, code_dir=code_dir, source_path=report_path,
        )

    summary_html = _render(summary_text) if view == 'summary' else None
    full_html = _render(full_text) if view == 'full' else None

    feedback_available = (round_dir / 'memory_report.md').exists()
    n_rounds = len(_v2_iter_round_dirs(round_dir.parent))

    return render_template(
        'report.html',
        summary_html=Markup(summary_html) if summary_html else None,
        full_html=Markup(full_html) if full_html else None,
        report_available=True,
        report_type='round',
        current_view=view,
        has_summary=has_summary,
        has_full=has_full,
        has_round_report=True,
        title=f"Round {round_number} Report",
        n_rounds=n_rounds,
        feedback_available=feedback_available,
        # Report chat/download/resync are session-generator-bound V1
        # features out of scope for this hardening pass (round CONTENT
        # correctness); disabling the chat entry point here avoids offering
        # an action that would silently operate on a different round.
        code_chat_available=False,
        audit_mode=_is_audit_mode(),
        audit_only=_is_audit_only_mode(),
        chat_storage_scope=_session_debug_id(),
    )


@app.route('/rounds/')
def rounds_workspace():
    """Session-scoped V2 Round Workspace URL.

    The top navigation uses this short URL and it always renders the new V2
    workspace shell for the active project. Numbered project round URLs remain
    available as compatibility/deep links for reports and exact historical
    round content.
    """
    project_slug = session.get('v2_active_project_slug')
    if not project_slug:
        return redirect(url_for('projects'))
    project, matched_by_id = _v2_resolve_project_by_slug_or_id(project_slug)
    if project is None:
        return redirect(url_for('projects'))
    project = _v2_activate_project(project)

    checkpoint_dir = project.get('checkpoint_dir')
    if not checkpoint_dir:
        return redirect(url_for('project_hypotheses', project_slug=project['url_slug']))

    round_dir = None
    pinned_round = None
    pre_round_overlay = False
    try:
        checkpoint_path = Path(checkpoint_dir)
        rounds_root = _v2_round_dirs_root(checkpoint_path)
        active_round_dir = _v2_active_round_dir_for(checkpoint_path)
        active_index = _v2_round_index(active_round_dir)
        if active_index is not None:
            round_dir = active_round_dir
            pinned_round = active_index
        else:
            all_rounds = _v2_iter_round_dirs(rounds_root)
            if all_rounds:
                pinned_round, round_dir = all_rounds[-1]
            else:
                round_dir = rounds_root / 'round_0'

        pre_round_overlay = _v2_no_round_has_started(rounds_root)
    except OSError:
        round_dir = None

    return render_template(
        'round_workspace.html',
        nav_active='workspace',
        page_class='v2-page-round-workspace',
        project_slug=project['url_slug'],
        round_workspace_url=url_for('rounds_workspace'),
        checkpoint_path=str(project.get('checkpoint_dir') or ''),
        round_path=str(round_dir or ''),
        pinned_round=pinned_round,
        workspace_session_scope=_session_debug_id(),
        pre_round_overlay=pre_round_overlay,
        overlay_body=(
            'DiscoveryAgent works best when it begins with an exploratory '
            'round to understand your data. Once the round is in progress, '
            'you can use Round Workspace to see what DiscoveryAgent is doing.'
        ),
        home_url=url_for('project_home_canonical', project_slug=project['url_slug']),
    )


@app.route('/projects/<project_slug>/rounds/<int:round_number>')
def project_round(project_slug, round_number):
    """Compatibility V2 round URL.

    The non-legacy project round path should never show the old report surface;
    it activates the requested project and sends the scientist to the new
    session-scoped Round Workspace screen at /rounds/. Exact historical reports
    live under /legacy/projects/<slug>/rounds/<n>.
    """
    project, matched_by_id = _v2_resolve_project_by_slug_or_id(project_slug)
    if project is None:
        abort(404)
    _v2_activate_project(project)
    return redirect(url_for('rounds_workspace'))


@app.route('/legacy/projects/<project_slug>/rounds/<int:round_number>')
def legacy_project_round(project_slug, round_number):
    """Legacy V2 exact-round report/monitor route, addressed by url_slug.

    This is the old numbered route behavior: one stable URL per round number
    for completed reports, pinned running monitors, and historical content.
    The user-facing Round Workspace tab uses /rounds/ instead.
    """
    project, matched_by_id = _v2_resolve_project_by_slug_or_id(project_slug)
    if project is None:
        abort(404)
    if matched_by_id:
        return redirect(url_for('legacy_project_round', project_slug=project['url_slug'], round_number=round_number))
    project = _v2_activate_project(project)

    checkpoint_dir = project.get('checkpoint_dir')
    round_dir = None
    if checkpoint_dir:
        rounds_root = _v2_round_dirs_root(Path(checkpoint_dir))
        candidate = rounds_root / f'round_{round_number}'
        if candidate.exists():
            round_dir = candidate
    if round_dir is None:
        abort(404)

    try:
        status = _v2_round_status(_inspect_latest_round(round_dir))
    except OSError:
        abort(404)

    if status == 'completed':
        # Same experience whether this is the latest completed round or an
        # older historical one -- content is always read from round_dir
        # itself, never from a 'latest' scan.
        return _render_round_report_view(project, round_dir, round_number)

    if status == 'running':
        # Pinned directly to round_dir: bypasses the sibling/active-round
        # scan entirely (see _get_latest_round_dir_for_session), so an old
        # directory that merely LOOKS running can never cause a different,
        # genuinely-active round to be shown here. The V2 shell receives the
        # same pinned round metadata for future workspace controllers.
        return render_template(
            'round_workspace.html',
            nav_active='workspace',
            page_class='v2-page-round-workspace',
            project_slug=project['url_slug'],
            round_workspace_url=url_for('rounds_workspace'),
            checkpoint_path=str(project.get('checkpoint_dir') or ''),
            round_path=str(round_dir),
            pinned_round=round_number,
            workspace_session_scope=_session_debug_id(),
        )

    # Before ANY autonomous round has ever started, Round 0 has no live content
    # yet, but the Round Workspace must still be reachable and show the shared
    # initial-exploration callout (same canonical predicate as Project Home and
    # the Studio). Render the V2 workspace shell with the blocking "Start your
    # first round" overlay rather than redirecting away.
    if round_number == 0 and _v2_no_round_has_started(rounds_root):
        return render_template(
            'round_workspace.html',
            nav_active='workspace',
            page_class='v2-page-round-workspace',
            project_slug=project['url_slug'],
            round_workspace_url=url_for('rounds_workspace'),
            checkpoint_path=str(project.get('checkpoint_dir') or ''),
            round_path=str(round_dir),
            pinned_round=None,
            workspace_session_scope=_session_debug_id(),
            pre_round_overlay=True,
            overlay_body=(
                'DiscoveryAgent works best when it begins with an exploratory '
                'round to understand your data. Once the round is in progress, '
                'you can use Round Workspace to see what DiscoveryAgent is doing.'
            ),
            # The overlay keeps the quiet "Back to project home" link while
            # the shared V2 nav remains available above the workspace.
            home_url=url_for('project_home_canonical', project_slug=project_slug),
        )

    # Not yet started (selected/exploring/pending/unknown): nothing to show at
    # this round's URL yet -- send the scientist to where the next action
    # actually lives instead of rendering an empty page.
    return redirect(url_for('project_hypotheses', project_slug=project_slug))


@app.route('/api/projects', methods=['GET'])
def api_projects_list():
    """List only V2 registry projects (v2_projects.json) — never demo fixtures
    and never the V1/legacy login project."""
    try:
        # Backfill any pre-slug legacy records once (no-op / no write when
        # every record already has a url_slug).
        _v2_ensure_url_slugs_backfilled()
        # Most recently viewed/opened first (last_opened_at is stamped at
        # creation and on every project-scoped visit); older registries
        # lacking the field fall back to created_at so they still sort
        # sensibly.
        raw_projects = sorted(
            _v2_load_projects(),
            key=lambda p: p.get('last_opened_at') or p.get('created_at') or '',
            reverse=True,
        )
        summaries: list[dict] = []
        for p in raw_projects:
            try:
                summaries.append(_v2_project_summary(
                    p.get('id'), p.get('display_name'), p.get('dataset_context'),
                    p.get('checkpoint_dir'), _v2_project_task_description(p), p.get('created_at'), p.get('updated_at'),
                    url_slug=p.get('url_slug'),
                ))
            except Exception:
                continue
        return jsonify({'success': True, 'projects': summaries})
    except Exception as exc:  # pragma: no cover - defensive guard
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/projects', methods=['POST'])
def api_projects_create():
    """Create a local V2 project: dirs + README.md + uploads + minimal config.json."""

    requested_project_name = (request.form.get('project_name') or '').strip()
    task_description = _normalize_task_description(request.form.get('task_description'))
    dataset_context = (request.form.get('dataset_context') or '').strip()

    # Project Name, Research Objective, and Project Context are required at
    # creation: every project must carry the information DiscoveryAgent needs to
    # generate hypotheses. This replaces the old "start a round / provide it
    # later" gate — a created project is always ready for the Hypothesis Studio.
    missing_fields = []
    if not requested_project_name:
        missing_fields.append('project name')
    if not task_description:
        missing_fields.append('research objective')
    if not dataset_context:
        missing_fields.append('project context')
    if missing_fields:
        return jsonify({
            'success': False,
            'error': 'Please complete the required fields: ' + ', '.join(missing_fields) + '.',
        }), 400

    files = request.files.getlist('files')

    record: dict | None = None
    with _V2_PROJECTS_LOCK:
        projects = _v2_load_projects()
        project_name = requested_project_name or _v2_unique_default_project_name(projects)
        project_id = _v2_new_project_id(project_name)
        root = _v2_data_root() / project_id
        if root.exists():
            return jsonify({'success': False, 'error': 'A project directory with this identifier already exists.'}), 409

        code_dir = root / 'code'
        data_dir = code_dir / 'data'
        round0 = root / 'checkpoint' / 'checkpoints' / 'round_0'
        created = False
        try:
            code_dir.mkdir(parents=True, exist_ok=False)
            data_dir.mkdir(parents=True, exist_ok=False)
            round0.mkdir(parents=True, exist_ok=False)
            created = True

            # code/README.md is the ONE canonical Project Context document --
            # not a "dataset README": it can describe multiple datasets, file
            # relationships, interventions, preprocessing, limitations, and
            # other project-wide context, so it belongs beside the project
            # workspace, not mixed into code/data/ with source materials.
            readme_path = code_dir / 'README.md' if dataset_context else None
            if readme_path:
                readme_path.write_text(f"# {project_name}\n\n{dataset_context}\n", encoding='utf-8')

            # Uploaded research files live under working_directory/data, which
            # is the DiscoveryAgent runner's canonical dataset location.
            for storage in files:
                if not storage or not storage.filename:
                    continue
                _v2_save_project_upload({'data_dir': str(data_dir), 'code_dir': str(code_dir)}, storage)

            # Minimum valid config the existing loader requires. No
            # readme_path key: select_hypothesis.py's load_checkpoint() only
            # ever consults the constructor's readme_path argument (passed at
            # runtime from project_cfg['readme_path'] -- see
            # _v2_activate_v2_project/get_or_restore_generator), never
            # config['readme_path'] from the file, so this key was dead.
            config = {
                'config': {
                    'working_directory': str(code_dir),
                    'task_description': task_description,
                    'hypothesis_model': DEFAULT_HYPOTHESIS_MODEL,
                    'dataset_name': project_name,
                }
            }
            (round0 / 'config.json').write_text(json.dumps(config, indent=2), encoding='utf-8')

            now = datetime.now(tz=timezone.utc).isoformat()
            # Generated once at creation; stable afterward (never regenerated
            # on rename -- see api_projects_edit).
            url_slug = _v2_unique_url_slug(project_name, projects)
            record = {
                'id': project_id,
                'url_slug': url_slug,
                'display_name': project_name,
                'task_description': task_description,
                'dataset_context': dataset_context,
                'project_root': str(root),
                'code_dir': str(code_dir),
                'data_dir': str(data_dir),
                'checkpoint_dir': str(round0),
                'readme_path': str(readme_path) if readme_path else None,
                'hypothesis_model': DEFAULT_HYPOTHESIS_MODEL,
                'created_at': now,
                'updated_at': now,
                'last_opened_at': now,
            }
            projects.append(record)
            _v2_save_projects(projects)
        except Exception as exc:  # cleanup so no half-created/registered project remains
            if created:
                shutil.rmtree(root, ignore_errors=True)
            import traceback
            traceback.print_exc()
            return jsonify({'success': False, 'error': f'Failed to create project: {exc}'}), 500

    # Activate outside the registry lock.
    _v2_activate_v2_project(record)
    return jsonify({
        'success': True,
        'project_id': record['id'],
        'destination': url_for('project_home_canonical', project_slug=record['url_slug']),
    })


@app.route('/api/projects/<project_id>/continue', methods=['POST'])
def api_projects_continue(project_id):
    """Activate the chosen V2 project and return the canonical Project Home
    destination. Internal API -- keeps using the immutable internal id (see
    _v2_resolve_project_by_slug_or_id / section 9), unlike the user-facing
    /projects/<slug> URLs. No longer state-aware: Project Home is the
    project's orientation layer, so Continue always lands there rather than
    bypassing it based on project state."""
    _v2_ensure_url_slugs_backfilled()
    project = _v2_get_project(project_id)
    if not project:
        return jsonify({'success': False, 'error': 'Project not found.'}), 404
    project = _v2_activate_project(project)
    return jsonify({
        'success': True,
        'destination': url_for('project_home_canonical', project_slug=project['url_slug']),
    })


def _v2_existing_files(project: dict) -> list[dict]:
    """List persisted source files by project-data relative path for edit prefill."""
    files_dir = _v2_project_files_dir(project)
    out: list[dict] = []
    if not files_dir:
        return out
    files_path = Path(files_dir)
    if not files_path.is_dir():
        return out
    try:
        entries = sorted((p for p in files_path.rglob('*') if p.is_file()),
                         key=lambda p: p.relative_to(files_path).as_posix().lower())
        for entry in entries:
            rel = entry.relative_to(files_path).as_posix()
            if rel in {'README.md', 'README.txt'}:
                continue
            # Modern projects have a dedicated data_dir, so everything below
            # it is user-supplied research input. Legacy projects fall back to
            # code_dir, where DiscoveryAgent also writes scripts, figures,
            # logs, and result artifacts. Never present those generated
            # workspaces as source files in the project editor.
            if not project.get('data_dir'):
                top_level = PurePosixPath(rel).parts[0].lower()
                if top_level in {
                    'analysis_output', 'output', 'outputs', 'results',
                    'figures', 'plots', 'logs', 'src', 'scripts',
                    'agentoutputs', 'checkpoints', '__pycache__',
                }:
                    continue
            try:
                out.append({'name': rel, 'size': entry.stat().st_size})
            except OSError:
                out.append({'name': rel, 'size': None})
    except OSError:
        return out
    return out


@app.route('/api/projects/<project_id>/files', methods=['POST', 'DELETE'])
def api_projects_upload_file(project_id):
    """Add or remove one project source file."""

    with _V2_PROJECTS_LOCK:
        projects = _v2_load_projects()
        record = next((p for p in projects if p.get('id') == project_id), None)
    if record is None:
        return jsonify({'success': False, 'error': 'Project not found.'}), 404

    if request.method == 'DELETE':
        relative_path = (request.args.get('path') or '').replace('\\', '/').strip().lstrip('/')
        visible_files = {item['name'] for item in _v2_existing_files(record)}
        if not relative_path or relative_path not in visible_files:
            return jsonify({'success': False, 'error': 'Source file not found.'}), 404
        files_dir = Path(_v2_project_files_dir(record))
        target = files_dir / PurePosixPath(relative_path)
        try:
            root_resolved = files_dir.resolve()
            target_resolved = target.resolve()
            if not target_resolved.is_relative_to(root_resolved) or target_resolved == root_resolved:
                raise ValueError('Source file path escapes the project data directory.')
            if not target_resolved.is_file():
                return jsonify({'success': False, 'error': 'Source file not found.'}), 404
            target_resolved.unlink()
            parent = target_resolved.parent
            while parent != root_resolved:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
            return jsonify({'success': True})
        except ValueError as exc:
            return jsonify({'success': False, 'error': str(exc)}), 400
        except OSError as exc:
            return jsonify({'success': False, 'error': f'Failed to remove source file: {exc}'}), 500

    storage = request.files.get('file')
    if not storage or not storage.filename:
        return jsonify({'success': False, 'error': 'No file provided.'}), 400

    try:
        saved = _v2_save_project_upload(record, storage, request.form.get('relative_path'))
        files_dir = Path(_v2_project_files_dir(record))
        rel = saved.relative_to(files_dir).as_posix()
        return jsonify({
            'success': True,
            'file': {'name': rel, 'size': saved.stat().st_size},
        })
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:  # pragma: no cover - defensive guard
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'Failed to upload file: {exc}'}), 500


@app.route('/api/projects/<project_id>', methods=['GET'])
def api_projects_detail(project_id):
    """Full detail for the edit modal (all V2 projects are editable)."""
    project = _v2_get_project(project_id)
    if not project:
        return jsonify({'success': False, 'error': 'Project not found.'}), 404
    return jsonify({
        'success': True,
        'project': {
            'id': project.get('id'),
            'display_name': project.get('display_name'),
            'task_description': _v2_project_task_description(project),
            'dataset_context': project.get('dataset_context'),
            'files': _v2_existing_files(project),
        },
    })


def _v2_project_delete_root(record: dict) -> Path:
    """Resolve and validate the on-disk project root before destructive delete."""
    root_raw = record.get('project_root') or str(_v2_data_root() / str(record.get('id') or ''))
    root = Path(root_raw)
    data_root = _v2_data_root().resolve()
    target = root.resolve()
    try:
        target.relative_to(data_root)
    except ValueError:
        raise ValueError('Project root is outside the V2 data root.')
    if target == data_root:
        raise ValueError('Refusing to delete the V2 data root.')
    return target


@app.route('/api/projects/<project_id>', methods=['DELETE'])
def api_projects_delete(project_id):
    """Delete a V2 project from the registry and remove its local project root."""

    was_active = session.get('v2_active_project_id') == project_id
    with _V2_PROJECTS_LOCK:
        projects = _v2_load_projects()
        index = next((i for i, p in enumerate(projects) if p.get('id') == project_id), None)
        if index is None:
            return jsonify({'success': False, 'error': 'Project not found.'}), 404

        record = projects[index]
        try:
            project_root = _v2_project_delete_root(record)
            if project_root.exists():
                if not project_root.is_dir():
                    raise ValueError('Project root is not a directory.')
                shutil.rmtree(project_root)
            del projects[index]
            _v2_save_projects(projects)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            return jsonify({'success': False, 'error': f'Failed to delete project: {exc}'}), 500

    if was_active:
        clear_session_state()
        for key in (
            'project_config', 'project_label', 'v2_active_project_id',
            'v2_active_project_slug', 'checkpoint_s3_prefix', 'cleanup_ready',
        ):
            session.pop(key, None)

    return jsonify({'success': True, 'destination': url_for('projects')})


@app.route('/api/projects/<project_id>/edit', methods=['POST'])
def api_projects_edit(project_id):
    """Update a V2 project's name/context, rewrite code/README.md, add new files."""

    requested_project_name = (request.form.get('project_name') or '').strip()
    task_description = _normalize_task_description(request.form.get('task_description'))
    dataset_context = (request.form.get('dataset_context') or '').strip()

    files = request.files.getlist('files')

    with _V2_PROJECTS_LOCK:
        projects = _v2_load_projects()
        record = next((p for p in projects if p.get('id') == project_id), None)
        if record is None:
            return jsonify({'success': False, 'error': 'Project not found.'}), 404

        project_name = requested_project_name or _v2_unique_default_project_name(projects, exclude_id=project_id)
        code_dir = Path(record.get('code_dir')) if record.get('code_dir') else None
        files_dir = _v2_project_files_dir(record)
        try:
            # code/README.md is the one canonical Project Context document
            # (see api_projects_create). Never touch code/data/ here --
            # uploaded research files live there and must never be deleted
            # as "legacy README" cleanup, even if a user's own file happens
            # to be named README.txt/README.md.
            readme_path = (code_dir / 'README.md') if code_dir else None
            if dataset_context:
                if not readme_path:
                    raise ValueError('Project code directory is not configured.')
                readme_path.write_text(f"# {project_name}\n\n{dataset_context}\n", encoding='utf-8')
                record['readme_path'] = str(readme_path)
            else:
                if readme_path and readme_path.exists():
                    readme_path.unlink()
                record['readme_path'] = None

            # Save only newly added files (reuse create-time upload safety;
            # existing files are never deleted here).
            for storage in files:
                if not storage or not storage.filename:
                    continue
                _v2_save_project_upload(record, storage)

            record['display_name'] = project_name
            record['task_description'] = task_description
            record['dataset_context'] = dataset_context
            record['updated_at'] = datetime.now(tz=timezone.utc).isoformat()
            _v2_sync_project_round_config(record)
            _v2_save_projects(projects)
        except Exception as exc:  # pragma: no cover - defensive guard
            import traceback
            traceback.print_exc()
            return jsonify({'success': False, 'error': f'Failed to save changes: {exc}'}), 500

    _v2_sync_active_project_session(record)
    generator = get_generator()
    if generator and session.get('v2_active_project_id') == project_id:
        generator.task_description = _v2_project_task_description(record)
        if record.get('readme_path'):
            generator.dataset_readme_path = Path(record['readme_path'])

    return jsonify({'success': True, 'project_id': project_id})


@app.route('/api/project_objective', methods=['POST'])
def api_project_objective():
    """Persist the active V2 project's canonical Research Objective."""

    data = request.json or {}
    task_description = _normalize_task_description(data.get('task_description') or data.get('research_objective'))
    if not task_description:
        return jsonify({
            'success': False,
            'code': 'missing_task_description',
            'error': 'missing_task_description',
            'message': 'Enter a research objective before continuing.',
        }), 400

    project_id = session.get('v2_active_project_id')
    if not project_id:
        return jsonify({'success': False, 'error': 'No active V2 project'}), 400

    with _V2_PROJECTS_LOCK:
        projects = _v2_load_projects()
        record = next((p for p in projects if p.get('id') == project_id), None)
        if record is None:
            return jsonify({'success': False, 'error': 'Project not found.'}), 404
        record['task_description'] = task_description
        record['updated_at'] = datetime.now(tz=timezone.utc).isoformat()
        _v2_sync_project_round_config(record)
        _v2_save_projects(projects)

    _v2_sync_active_project_session(record)
    generator = get_generator()
    if generator:
        generator.task_description = task_description

    return jsonify({
        'success': True,
        'task_description': task_description,
        'project_state': _v2_build_project_state(),
    })


def _load_readme_content(readme_path: str):
    """Best-effort loader for the dataset README content."""
    normalized_path = _clean_path_value(readme_path)
    if not normalized_path:
        return None, 'No README path configured'

    try:
        path = Path(normalized_path).expanduser()
        if path.is_dir():
            return None, f'Readme path {path} is a directory'
        content = path.read_text(encoding='utf-8', errors='ignore')
        return content, None
    except FileNotFoundError:
        return None, f'Readme not found at {Path(normalized_path).expanduser()}'
    except Exception as exc:  # pragma: no cover - defensive guard
        return None, f'Failed to read README: {exc}'


def render_markdown_to_html(md_text: str) -> str:
    """Render markdown to HTML with a safe fallback."""
    if not md_text:
        return ""

    try:
        import markdown

        return markdown.markdown(
            md_text,
            extensions=["extra", "sane_lists", "smarty", "tables", "toc"],
            output_format="html5",
        )
    except Exception as exc:
        # Fall back to a minimal renderer if the markdown package is missing
        print(f"⚠️  Falling back to minimal markdown rendering: {exc}")
        paragraphs = []
        for para in md_text.split("\n\n"):
            para = para.strip()
            if not para:
                continue
            paragraphs.append(f"<p>{para.replace(os.linesep, '<br>')}</p>")
        return "\n".join(paragraphs)


def _path_within_root(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _iter_allowed_readme_roots(project_cfg: dict) -> list[Path]:
    """Return the allowed filesystem roots for README reads."""
    roots: list[Path] = []
    code_root: Path | None = None
    code_dir_value = _clean_path_value(project_cfg.get('code_dir') or CLI_CODE_DIR)
    if code_dir_value:
        try:
            code_root = Path(code_dir_value).expanduser().resolve()
            roots.append(code_root)
            roots.append(code_root / 'data')
        except Exception:
            pass

    for configured in (project_cfg.get('readme_path'), CLI_README_PATH):
        configured_value = _clean_path_value(configured)
        if not configured_value:
            continue
        try:
            configured_path = Path(configured_value).expanduser()
            if not configured_path.is_absolute() and code_root is not None:
                configured_path = (code_root / configured_path).resolve()
            else:
                configured_path = configured_path.resolve()
            roots.append(configured_path if configured_path.is_dir() else configured_path.parent)
        except Exception:
            continue

    # De-duplicate while preserving order
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(root)
    return deduped


def _resolve_readme_path_secure(project_cfg: dict, requested_path: str | None = None) -> tuple[Path | None, str | None]:
    """
    Resolve a README path while enforcing an allowlist of project-local roots.

    Resolution order is:
    1) requested_path (if provided)
    2) project config readme_path
    3) CLI readme_path

    If an earlier candidate is stale/missing, later candidates are attempted.
    """
    raw_candidates = []
    for raw in (requested_path, project_cfg.get('readme_path'), CLI_README_PATH):
        cleaned = _clean_path_value(raw)
        if cleaned and cleaned not in raw_candidates:
            raw_candidates.append(cleaned)
    if not raw_candidates:
        return None, 'No README path configured'

    allowed_roots = _iter_allowed_readme_roots(project_cfg)
    if not allowed_roots:
        return None, 'README roots are not configured'

    first_allowed_missing: Path | None = None
    saw_outside_allowed = False

    for raw_path in raw_candidates:
        candidate = Path(raw_path).expanduser()
        candidate_paths: list[Path] = []
        if candidate.is_absolute():
            candidate_paths.append(candidate.resolve())
        else:
            for root in allowed_roots:
                try:
                    candidate_paths.append((root / candidate).resolve())
                except Exception:
                    continue
            if not candidate_paths:
                candidate_paths.append(candidate.resolve())

        for resolved_candidate in candidate_paths:
            if not any(_path_within_root(resolved_candidate, root) for root in allowed_roots):
                saw_outside_allowed = True
                continue
            if resolved_candidate.is_dir():
                continue
            if resolved_candidate.exists():
                return resolved_candidate, None
            if first_allowed_missing is None:
                first_allowed_missing = resolved_candidate

    if first_allowed_missing is not None:
        return None, f'Readme not found at {first_allowed_missing}'
    if saw_outside_allowed:
        return None, 'Requested README path is outside allowed project directories'
    return None, 'README unavailable'


def rewrite_image_sources(
    html_content: str,
    checkpoint_prefix: str | None,
    code_prefix: str | None,
    checkpoint_dir: Path | None = None,
    code_dir: Path | None = None,
    source_dir: Path | None = None,
) -> str:
    """
    Ensure image sources in rendered markdown point to served asset routes.

    Args:
        html_content: Rendered markdown HTML.
        checkpoint_prefix: URL prefix for checkpoint assets (ending with /).
        code_prefix: URL prefix for code assets (ending with /).
        checkpoint_dir: Local checkpoint directory root.
        code_dir: Local code directory root.
        source_dir: Directory containing the markdown source file.
    """
    if not html_content:
        return html_content

    def _normalize_prefix(prefix: str | None) -> str | None:
        if not prefix:
            return None
        return prefix if prefix.endswith('/') else f"{prefix}/"

    checkpoint_prefix = _normalize_prefix(checkpoint_prefix)
    code_prefix = _normalize_prefix(code_prefix)
    cwd = Path.cwd().resolve()

    def _rewrite_src(src: str) -> str:
        if not src:
            return src
        if src.startswith("/report_assets/") or src.startswith("/background_report/assets/"):
            return src
        if SCHEME_PATTERN.match(src) or src.startswith("data:") or src.startswith("#"):
            return src

        path = Path(src)
        if path.is_absolute():
            if code_dir and code_prefix and _path_within_root(path, code_dir):
                rel = path.resolve().relative_to(code_dir.resolve()).as_posix()
                return f"{code_prefix}{rel}"
            if checkpoint_dir and checkpoint_prefix and _path_within_root(path, checkpoint_dir):
                rel = path.resolve().relative_to(checkpoint_dir.resolve()).as_posix()
                return f"{checkpoint_prefix}{rel}"
            return src

        if source_dir:
            candidate = (source_dir / path).resolve()
            if code_dir and code_prefix and _path_within_root(candidate, code_dir) and candidate.exists():
                rel = candidate.relative_to(code_dir.resolve()).as_posix()
                return f"{code_prefix}{rel}"
            if checkpoint_dir and checkpoint_prefix and _path_within_root(candidate, checkpoint_dir) and candidate.exists():
                rel = candidate.relative_to(checkpoint_dir.resolve()).as_posix()
                return f"{checkpoint_prefix}{rel}"

        if code_dir and code_prefix:
            candidate = (cwd / path).resolve()
            if _path_within_root(candidate, code_dir):
                rel = candidate.relative_to(code_dir.resolve()).as_posix()
                return f"{code_prefix}{rel}"
            candidate = (code_dir / path).resolve()
            if _path_within_root(candidate, code_dir):
                rel = candidate.relative_to(code_dir.resolve()).as_posix()
                return f"{code_prefix}{rel}"
        if checkpoint_dir and checkpoint_prefix:
            candidate = (cwd / path).resolve()
            if _path_within_root(candidate, checkpoint_dir):
                rel = candidate.relative_to(checkpoint_dir.resolve()).as_posix()
                return f"{checkpoint_prefix}{rel}"
            candidate = (checkpoint_dir / path).resolve()
            if _path_within_root(candidate, checkpoint_dir) and candidate.exists():
                rel = candidate.relative_to(checkpoint_dir.resolve()).as_posix()
                return f"{checkpoint_prefix}{rel}"

        base_prefix = code_prefix or checkpoint_prefix
        if not base_prefix:
            return src
        cleaned = src.lstrip('./')
        return f"{base_prefix}{cleaned}"

    def _replace(match: re.Match) -> str:
        lead = match.group(1)
        quote = match.group(2)
        src = match.group(3)
        return f"{lead}{quote}{_rewrite_src(src)}{quote}"

    return IMG_SRC_PATTERN.sub(_replace, html_content)


def render_report_html(md_text: str, generator: InteractiveHypothesisGenerator | None) -> str:
    """Render markdown to HTML and rewrite image sources for report assets."""
    if not md_text:
        return ""

    html_content = render_markdown_to_html(md_text)
    if not generator:
        return html_content

    checkpoint_prefix = url_for('report_asset', root='checkpoint', filename='')
    code_prefix = url_for('report_asset', root='code', filename='')
    checkpoint_dir = Path(generator.checkpoint_dir).resolve() if generator.checkpoint_dir else None
    code_dir = Path(generator.code_dir).resolve() if generator.code_dir else None
    return rewrite_image_sources(
        html_content,
        checkpoint_prefix=checkpoint_prefix,
        code_prefix=code_prefix,
        checkpoint_dir=checkpoint_dir,
        code_dir=code_dir,
    )


def render_checkpoint_markdown_html(
    md_text: str,
    *,
    checkpoint_dir: Path | None,
    code_dir: Path | None,
    source_path: Path,
) -> str:
    """Render checkpoint markdown and rewrite image sources for the browser."""
    html_content = render_markdown_to_html(md_text)
    checkpoint_prefix = url_for('checkpoint_browser_asset', filename='')
    code_prefix = url_for('report_asset', root='code', filename='') if code_dir else None
    return rewrite_image_sources(
        html_content,
        checkpoint_prefix=checkpoint_prefix,
        code_prefix=code_prefix,
        checkpoint_dir=checkpoint_dir,
        code_dir=code_dir,
        source_dir=source_path.parent.resolve(),
    )


def _load_text_file(path: Path) -> str:
    """Best-effort UTF-8 file reader with logging."""
    try:
        return path.read_text(encoding='utf-8')
    except Exception as exc:
        print(f"⚠️  Failed to read file {path}: {exc}")
        return ""


def _load_research_paper_text(generator: InteractiveHypothesisGenerator | None) -> str | None:
    """Load the latest research paper text from the generator, if available."""
    if not generator:
        return None
    path = getattr(generator, "research_paper_path", None)
    if not path:
        return None
    if not isinstance(path, Path):
        path = Path(path)
    if not path.exists():
        return None
    text = _load_text_file(path)
    return text if text else None


def _get_audit_only_report_text(generator: InteractiveHypothesisGenerator | None) -> str:
    """Return the audit-only report text (memory report preferred)."""
    if not generator:
        return ""
    return (
        getattr(generator, "memory_report_summary", None)
        or getattr(generator, "memory_report", None)
        or ""
    )


def _candidate_research_paper_roots(checkpoint_dir: Path | None) -> list[Path]:
    """Return candidate roots that may contain research_paper_* directories."""
    if not checkpoint_dir:
        return []
    roots: list[Path] = [checkpoint_dir]
    try:
        if checkpoint_dir.name.startswith("round_"):
            roots.append(checkpoint_dir.parent)
            roots.append(checkpoint_dir.parent.parent)
        else:
            roots.append(checkpoint_dir.parent)
    except Exception:
        pass
    # Deduplicate while preserving order
    seen = set()
    unique_roots = []
    for root in roots:
        if not root:
            continue
        key = str(root.resolve()) if root.exists() else str(root)
        if key in seen:
            continue
        seen.add(key)
        unique_roots.append(root)
    return unique_roots


def _get_configured_research_paper_path(project_cfg: dict | None) -> str | None:
    """Return the project-configured research paper path, if one is set."""
    if not isinstance(project_cfg, dict):
        return None
    return _clean_path_value(project_cfg.get('research_paper_path'))


def _resolve_configured_research_paper_target(
    configured_path: str | None,
    checkpoint_dir: str | Path | None,
) -> Path | None:
    """Resolve an explicit paper file or directory against checkpoint/log roots."""
    normalized = _clean_path_value(configured_path)
    if not normalized:
        return None

    requested = Path(normalized).expanduser()
    candidates: list[Path] = []
    if requested.is_absolute():
        candidates.append(requested)
    elif checkpoint_dir:
        for root in _candidate_research_paper_roots(Path(checkpoint_dir)):
            candidates.append(root / requested)

    for candidate in candidates:
        try:
            candidate = candidate.resolve()
        except Exception:
            pass
        if candidate.exists():
            return candidate
        paper_md = candidate / "research_paper.md"
        if paper_md.exists():
            return candidate
        paper_pdf = candidate / "research_paper.pdf"
        if paper_pdf.exists():
            return candidate
    return None


def _find_latest_research_paper_dir(checkpoint_dir: Path | None) -> Path | None:
    """Find the latest research_paper_* directory beneath the checkpoint roots."""
    if not checkpoint_dir:
        return None
    pattern = re.compile(r"^research_paper_(\d{8})_(\d{6})$")
    latest_stamp = None
    latest_dir = None
    for root in _candidate_research_paper_roots(checkpoint_dir):
        if not root.exists() or not root.is_dir():
            continue
        try:
            for entry in root.iterdir():
                if not entry.is_dir():
                    continue
                match = pattern.match(entry.name)
                if not match:
                    continue
                stamp = f"{match.group(1)}_{match.group(2)}"
                if latest_stamp is None or stamp > latest_stamp:
                    latest_stamp = stamp
                    latest_dir = entry
        except Exception:
            continue
    return latest_dir


def _find_research_paper_pdf(research_paper_dir: Path | None) -> Path | None:
    """Find a PDF file inside the research_paper directory."""
    if not research_paper_dir or not research_paper_dir.exists():
        return None
    preferred = research_paper_dir / "research_paper.pdf"
    if preferred.exists():
        return preferred
    try:
        pdfs = [p for p in research_paper_dir.glob("*.pdf") if p.is_file()]
    except Exception:
        return None
    if not pdfs:
        return None
    pdfs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return pdfs[0]


def _resolve_research_paper_pdf_path(
    generator: InteractiveHypothesisGenerator | None,
    checkpoint_dir: str | Path | None,
    configured_research_paper_path: str | None = None,
) -> Path | None:
    """Resolve the best available research paper PDF path."""
    research_dir = None
    configured_target = _resolve_configured_research_paper_target(
        configured_research_paper_path,
        checkpoint_dir,
    )
    if configured_target:
        if configured_target.is_file() and configured_target.suffix.lower() == ".pdf":
            return configured_target
        if configured_target.is_file():
            return _find_research_paper_pdf(configured_target.parent)
        return _find_research_paper_pdf(configured_target)
    if generator and getattr(generator, "research_paper_path", None):
        path = generator.research_paper_path
        if not isinstance(path, Path):
            path = Path(path)
        if path.exists():
            research_dir = path.parent
    if research_dir is None and checkpoint_dir:
        research_dir = _find_latest_research_paper_dir(Path(checkpoint_dir))
    return _find_research_paper_pdf(research_dir)


def _resolve_report_asset_base_dir(root: str) -> Path | None:
    """Resolve the base directory for report assets without requiring a restored generator."""
    generator = get_generator()
    project_cfg = _get_project_config()

    raw_path = None
    if root == 'checkpoint':
        raw_path = (
            getattr(generator, 'checkpoint_dir', None)
            or project_cfg.get('checkpoint_dir')
            or CLI_CHECKPOINT_DIR
        )
    elif root == 'code':
        raw_path = (
            getattr(generator, 'code_dir', None)
            or project_cfg.get('code_dir')
            or CLI_CODE_DIR
        )
    else:
        return None

    if not raw_path:
        return None

    try:
        resolved = Path(raw_path).expanduser().resolve()
    except Exception:
        return None

    if root == 'checkpoint':
        resolved = _latest_round_checkpoint_dir(resolved)

    return resolved if resolved.exists() else None


def create_generator(
    checkpoint_dir: str,
    hypothesis_model: str = None,
    legacy_hyp_gen: bool = None,
    num_hypotheses: int = None,
    code_dir: str = None,
    include_background_literature: bool = None,
    generate_plan: bool = None,
    readme_path: str = None,
    enable_citations: bool = None,
    cache_documents: bool = None,
    research_paper_path: str = None,
) -> InteractiveHypothesisGenerator:
    """Create a new generator instance."""
    if 'session_id' not in session:
        session['session_id'] = os.urandom(16).hex()

    session_id = session['session_id']

    # Create generator using CLI class (disable signal handlers for web context)
    generator = InteractiveHypothesisGenerator(
        checkpoint_dir=checkpoint_dir,
        code_dir=code_dir if code_dir else CLI_CODE_DIR,
        output_file=None,
        hypothesis_model=hypothesis_model,
        register_signal_handlers=False,  # Signal handlers don't work in Flask threads
        use_legacy_hyp_gen=legacy_hyp_gen if legacy_hyp_gen is not None else CLI_LEGACY_HYP_GEN,
        num_hypotheses=num_hypotheses if num_hypotheses is not None else CLI_NUM_HYPOTHESES,
        include_background_literature=include_background_literature if include_background_literature is not None else CLI_INCLUDE_BACKGROUND_LITERATURE,
        generate_plan=generate_plan if generate_plan is not None else CLI_GENERATE_PLAN,
        readme_path=readme_path if readme_path is not None else CLI_README_PATH,
        enable_citations=enable_citations if enable_citations is not None else CLI_ENABLE_CITATIONS,
        cache_documents=cache_documents if cache_documents is not None else CLI_CACHE_DOCUMENTS,
        research_paper_path=research_paper_path,
    )
    # Enable prompt debug printing only for the web app context
    generator.debug_print_prompts = True
    generator.audit_only_mode = _is_audit_only_mode()

    GENERATORS[session_id] = generator
    with _GENERATOR_LAST_USED_LOCK:
        _GENERATOR_LAST_USED[session_id] = time.time()
    return generator


@app.after_request
def attach_operation_debug_headers(response):
    """Attach request-scoped debug headers for easier production triage."""
    debug_payload = getattr(g, 'operation_debug', None)
    if isinstance(debug_payload, dict):
        request_id = debug_payload.get('request_id')
        if request_id:
            response.headers['X-DA-Request-Id'] = request_id
        session_debug_id = debug_payload.get('session_debug_id')
        if session_debug_id:
            response.headers['X-DA-Session'] = session_debug_id
        lock_wait_ms = debug_payload.get('lock_wait_ms')
        if lock_wait_ms is not None:
            response.headers['X-DA-Lock-Wait-Ms'] = f"{float(lock_wait_ms):.1f}"
        held_ms = debug_payload.get('held_ms')
        if held_ms is not None:
            response.headers['X-DA-Op-Held-Ms'] = f"{float(held_ms):.1f}"
    return response


# ============================================================================
# ROUTES - Thin API wrappers around CLI class methods
# ============================================================================

@app.route('/')
def home():
    """Redirect the local application root to the canonical Projects URL."""
    return redirect(url_for('projects'))


@app.route('/app')
def app_interface():
    """Render the main workspace page."""
    if _is_audit_mode():
        return redirect(url_for('audit_home'))
    project_cfg = _get_project_config()
    audit_only = _is_audit_only_mode()
    auto_initialize = CLI_AUTO_INITIALIZE
    if audit_only or project_cfg.get('checkpoint_dir'):
        auto_initialize = True
    checkpoint_dir = project_cfg.get('checkpoint_dir') or CLI_CHECKPOINT_DIR
    if not audit_only and checkpoint_dir:
        try:
            latest_round_dir = _latest_round_checkpoint_dir(Path(checkpoint_dir))
            # Also scan sibling round_* dirs so that a newly started round_N+1 is
            # detected even when the session checkpoint still points at round_N.
            active_round_dir = _resolve_active_monitor_round(latest_round_dir)
            if active_round_dir.exists() and _classify_round_state(active_round_dir) == 'round_monitor':
                v2_round_url = _v2_active_round_workspace_url_for_session()
                return redirect(v2_round_url or url_for('round_monitor'))
        except OSError:
            pass
    code_dir = project_cfg.get('code_dir') or CLI_CODE_DIR
    readme_path = project_cfg.get('readme_path') or CLI_README_PATH
    github_url = project_cfg.get('github_url') or CLI_GITHUB_URL
    default_model = CLI_HYPOTHESIS_MODEL
    if not default_model and not CLI_DEV_MODE:
        default_model = DEFAULT_HYPOTHESIS_MODEL
    ui_include_background = True if not CLI_DEV_MODE else CLI_INCLUDE_BACKGROUND_LITERATURE
    ui_generate_plan = True
    ui_enable_citations = False
    research_paper_pdf_url = None
    research_paper_pdf_available = False
    if audit_only:
        pdf_path = _resolve_research_paper_pdf_path(
            None,
            checkpoint_dir,
            _get_configured_research_paper_path(project_cfg),
        )
        if pdf_path:
            research_paper_pdf_available = True
            research_paper_pdf_url = url_for('research_paper_pdf')
    return render_template(
        'index.html',
        checkpoint_dir=checkpoint_dir,
        hypothesis_model=default_model,
        code_dir=code_dir,
        readme_path=readme_path,
        github_url=github_url,
        task_description=project_cfg.get('task_description'),
        auto_initialize=auto_initialize,
        include_background_literature=ui_include_background,
        legacy_hyp_gen=CLI_LEGACY_HYP_GEN,
        num_hypotheses=CLI_NUM_HYPOTHESES,
        generate_plan=ui_generate_plan,
        enable_citations=ui_enable_citations,
        cache_documents=CLI_CACHE_DOCUMENTS,
        dev_mode=CLI_DEV_MODE,
        audit_only=audit_only,
        research_paper_pdf_url=research_paper_pdf_url,
        research_paper_pdf_available=research_paper_pdf_available,
        chat_storage_scope=_session_debug_id(),
    )


@app.route('/audit')
def audit_home():
    """Audit-only entrypoint (background report)."""
    if not _is_audit_mode():
        return redirect(url_for('app_interface'))
    project_cfg = _get_project_config()
    github_url = project_cfg.get('github_url') or CLI_GITHUB_URL
    checkpoint_dir = project_cfg.get('checkpoint_dir') or CLI_CHECKPOINT_DIR
    code_dir = project_cfg.get('code_dir') or CLI_CODE_DIR
    research_paper_pdf_url = None
    research_paper_pdf_available = False
    pdf_path = _resolve_research_paper_pdf_path(
        None,
        checkpoint_dir,
        _get_configured_research_paper_path(project_cfg),
    )
    if pdf_path:
        research_paper_pdf_available = True
        research_paper_pdf_url = url_for('research_paper_pdf')

    _write_interaction_log('audit_session_start', {
        'checkpoint_dir': checkpoint_dir,
        'code_dir': code_dir,
    })
    log_event('audit_session_start', {'checkpoint_dir': checkpoint_dir})

    audit_guide_html = None
    audit_guide_path = Path(__file__).resolve().parent.parent / 'guides' / 'AUDIT_UI.md'
    if audit_guide_path.is_file():
        try:
            audit_guide_html = render_markdown_to_html(audit_guide_path.read_text(encoding='utf-8'))
        except Exception:
            audit_guide_html = None

    return render_template(
        'audit_home.html',
        report_url=url_for('unified_report', type='background', view='summary'),
        github_url=github_url,
        checkpoint_dir=checkpoint_dir,
        code_dir=code_dir,
        research_paper_pdf_url=research_paper_pdf_url,
        research_paper_pdf_available=research_paper_pdf_available,
        audit_guide_html=Markup(audit_guide_html) if audit_guide_html else None,
        chat_storage_scope=_audit_chat_storage_scope(),
    )


@app.route('/finalize')
def finalize():
    """Render the finalize selection page."""
    if _is_audit_mode():
        return redirect(url_for('audit_home'))
    if _is_audit_only_mode():
        return redirect(url_for('app_interface'))
    auto_initialize = CLI_AUTO_INITIALIZE
    checkpoint_dir = _get_default_config_value('checkpoint_dir', CLI_CHECKPOINT_DIR)
    code_dir = _get_default_config_value('code_dir', CLI_CODE_DIR)
    readme_path = _get_default_config_value('readme_path', CLI_README_PATH)
    ui_include_background = True if not CLI_DEV_MODE else CLI_INCLUDE_BACKGROUND_LITERATURE
    ui_generate_plan = True
    return render_template(
        'finalize.html',
        checkpoint_dir=checkpoint_dir,
        hypothesis_model=CLI_HYPOTHESIS_MODEL,
        code_dir=code_dir,
        readme_path=readme_path,
        auto_initialize=auto_initialize,
        include_background_literature=ui_include_background,
        legacy_hyp_gen=CLI_LEGACY_HYP_GEN,
        num_hypotheses=CLI_NUM_HYPOTHESES,
        generate_plan=ui_generate_plan,
        enable_citations=False,
        cache_documents=CLI_CACHE_DOCUMENTS,
        dev_mode=CLI_DEV_MODE,
    )


@app.route('/activity_log')
def activity_log():
    """Collect post-selection activity log before logout."""
    if _is_audit_mode():
        return redirect(url_for('audit_home'))
    if _is_audit_only_mode():
        return redirect(url_for('app_interface'))
    return render_template(
        'activity_log.html',
        username=session.get('username'),
        project_label=session.get('project_label'),
        finalize_feedback=session.get('finalize_feedback', ''),
    )


@app.route('/thank_you')
def thank_you():
    """Confirmation page after activity log submission."""
    if _is_audit_mode():
        return redirect(url_for('audit_home'))
    if _is_audit_only_mode():
        return redirect(url_for('app_interface'))
    return render_template('thank_you.html')


def _get_latest_round_dir_for_session() -> Path | None:
    """Resolve the latest round directory for the current session's project, or None.

    If the request carries an explicit ?round=N, that round's own directory is
    used directly -- bypassing the sibling-scan entirely. This is how the
    legacy /legacy/projects/<slug>/rounds/<n> route pins Round Monitor's initial
    render AND its client-side AJAX polling (round_monitor.js forwards
    ?round=N on every fetch once pinned) to the EXACT requested round, so a
    numbered round route can never silently drift to a different "active"
    round. Without ?round=N, behavior is unchanged: if the session checkpoint
    dir is a round_N folder, siblings are scanned for any active
    round_monitor round (e.g. round_{N+1} started by the backend while the
    session still points at round_N) and returned instead.
    """
    project_cfg = _get_project_config()
    checkpoint_dir = project_cfg.get('checkpoint_dir') or CLI_CHECKPOINT_DIR
    if not checkpoint_dir:
        return None
    try:
        checkpoint_path = Path(checkpoint_dir)
        if not checkpoint_path.exists():
            return None
        pinned = request.args.get('round')
        if pinned is not None and pinned.isdigit():
            rounds_root = _v2_round_dirs_root(checkpoint_path)
            candidate = rounds_root / f'round_{pinned}'
            if candidate.exists():
                return candidate
        round_dir = _latest_round_checkpoint_dir(checkpoint_path)
        return _resolve_active_monitor_round(round_dir)
    except OSError:
        return None


@app.route('/round_monitor')
def round_monitor(pinned_round_dir: "Path | None" = None, pinned_round_number: "int | None" = None):
    """Render the Round Monitor screen for an in-progress/partial round.

    pinned_round_dir/pinned_round_number are set only when called directly
    (not via URL routing) from the legacy /legacy/projects/<slug>/rounds/<n>
    dispatcher, so the page renders the EXACT requested round rather than
    whatever _get_latest_round_dir_for_session's sibling-scan would otherwise
    pick. The bare /round_monitor route (no args) keeps its existing
    session-based behavior unchanged."""
    round_dir = pinned_round_dir or _get_latest_round_dir_for_session()
    if not round_dir or _classify_round_state(round_dir) != 'round_monitor':
        return redirect(url_for('app_interface'))
    project_cfg = _get_project_config()
    return render_template(
        'round_monitor.html',
        checkpoint_path=str(project_cfg.get('checkpoint_dir') or ''),
        round_path=str(round_dir),
        pinned_round=pinned_round_number,
    )


@app.route('/api/round_status', methods=['GET'])
def api_round_status():
    """Filesystem-evidence-only status of the latest round, no generator required."""
    round_dir = _get_latest_round_dir_for_session()
    if not round_dir:
        return jsonify({'success': False, 'error': 'No checkpoint directory configured'})

    project_cfg = _get_project_config()
    info = _inspect_latest_round(round_dir)
    state = _classify_round_state(round_dir)
    selected_context_path = _locate_selected_hypothesis_context(round_dir)
    # Whether ANY autonomous round has ever started for this project. When False,
    # the Round Workspace shows the initial-exploration "Start your first round"
    # callout instead of an empty workspace. Same canonical predicate as Project
    # Home and the Studio, computed over all sibling round_* dirs.
    try:
        has_ever_started = not _v2_no_round_has_started(
            _v2_round_dirs_root(Path(project_cfg.get('checkpoint_dir') or round_dir)))
    except OSError:
        has_ever_started = True
    return jsonify({
        'success': True,
        'state': state,
        'has_ever_started': has_ever_started,
        'checkpoint_path': str(project_cfg.get('checkpoint_dir') or ''),
        'latest_round_path': str(round_dir),
        'active_round_path': str(round_dir),
        'has_selected_hypothesis': info['has_selected_hypothesis'],
        'has_generated_hypotheses': info['has_generated_hypotheses'],
        'has_round_report': info['has_round_report'],
        'has_memory_report': info['has_memory_report'],
        'query_count': info['query_count'],
        'latest_query_name': info['latest_query_name'],
        'last_modified': info['last_modified'],
        'selected_hypothesis_path': str(selected_context_path) if selected_context_path else None,
        'has_selected_hypothesis_context': selected_context_path is not None,
    })


# --- Round Constellation: visual concept-map data for Round Directory -----
# Deterministic, filesystem/summary-based only -- never an LLM call. Reuses the
# per-query summary _get_query_summary already produces (see api_round_tree).
# Edges are a best-effort VISUAL reconstruction, not a claim of exact
# derivation: every edge is inferred=True with a `reason` recording the rule
# that produced it. See _build_round_constellation's docstring for the chain.

# Structured director-activity -> lane. Authoritative when present (see
# _classify_query_lane), which is how a node becomes Literature/Validation/etc
# from a real run event rather than from summary keywords.
_SUBAGENT_LABEL_TO_LANE: dict[str, str] = {
    'Execution Agent': 'analysis',
    'Deep Research Agent': 'literature',
    'Validation Agent': 'validation',
    'Synthesis/Report Writer': 'synthesis',
}

# Cue buckets, scored independently and resolved by a fixed priority (see
# _classify_lane_from_text) so a lane reflects the WORK a query did, not
# incidental summary words. Two deliberate splits:
#   * Literature holds only multi-word RETRIEVAL phrases -- a summary merely
#     mentioning "literature"/"citations" is not evidence a retrieval ran.
#   * Synthesis is split STRONG (real integration/finalization) vs WEAK (a bare
#     append/addendum, which only says where output went); weak never beats
#     Analysis, so an appended addendum that ran real analysis stays Analysis.
_LANE_CUE_BUCKETS: dict[str, tuple[str, ...]] = {
    # Retrieval-only -- multi-word phrases naming an actual review/search/
    # deep-research step. NO bare "literature"/"published"/"paper"/"citation".
    'literature': (
        'literature review', 'literature search', 'literature retrieval',
        'reviewed the literature', 'reviewed published literature', 'review of published',
        'review of the literature', 'searched the literature', 'searched published literature',
        'deep research', 'deep_research', 'deep-research', 'citation search', 'retrieved papers',
        'paper retrieval', 'grounded in published', 'published-literature review',
    ),
    # Verification/replication/robustness STEP. Interpretation words
    # ("support"/"confirm"/"consistency") are NOT here -- they're weak ANALYSIS
    # cues below. Null/specificity/control cues are multi-word so a passing
    # mention of "specificity"/"control" doesn't flip a query to validation.
    'validation': (
        'validate', 'validating', 'validation', 'verify', 'verification', 'verifying',
        'cross-check', 'cross check', 'crosscheck', 'replicate', 'replication', 'robustness',
        'benchmark', 'sanity check', 'independent check', 'independent replication',
        'null control', 'negative control', 'background control', 'specificity control',
        'null model', 'permutation test', 'specificity check', 'robustness check',
    ),
    # STRONG synthesis -- real integration/finalization. Bare boilerplate
    # ("summary"/"report"/...) is absent, and so is the bare noun "synthesis":
    # it shows up in artifact names a query merely appends to (e.g.
    # FINAL_HYPOTHESIS_SYNTHESIS.md), so only verb forms and qualified phrases
    # ("final synthesis", "synthesis of") count as a real synthesis action.
    'synthesis_strong': (
        'synthesize', 'synthesise', 'synthesizing', 'synthesising', 'final synthesis',
        'synthesis of', 'synthesis across', 'integrate', 'integrating', 'integrated',
        'integration', 'consolidate', 'consolidation', 'consolidated', 'combine prior',
        'combine the', 'combine multiple', 'combining prior', 'final evidence table',
        'final summary figure', 'final report', 'evidence table', 'summary figure',
        'round report', 'memory report',
    ),
    # WEAK synthesis -- describes only that output was appended/updated
    # somewhere, not that the work itself was integrative. Never beats
    # Analysis on its own.
    'synthesis_weak': (
        'addendum', 'append', 'appending', 'appended', 'update report', 'updated report',
        'updating report',
    ),
    'analysis': (
        'test', 'tested', 'testing', 'whether', 'compare', 'compared', 'comparing', 'contrast',
        'contrasting', 'characterize', 'characterization', 'characterizing', 'analyze', 'analysis',
        'analytical', 'quantify', 'quantified', 'quantitative', 'model', 'regression', 'score',
        'scoring', 'readiness score', 'metric', 'ranking', 'ranked', 'expression', 'enrichment',
        'correlation', 'correlate', 'differential', 'outlier', 'outliers', 'discriminating',
        'decompose', 'decomposition', 'pattern', 'cluster', 'subtype', 'dataset', 'cross-dataset',
        'disconfirm', 'disconfirms', 'disconfirming', 'support', 'contradict', 'contradicts',
        'contradicting', 'consistent', 'consistency', 'computed', 'calculate', 'estimate', 'rrs',
    ),
}

_LANE_FILENAME_CUES: list[tuple[str, tuple[str, ...]]] = [
    ('validation', ('validation', 'verify', 'crosscheck', 'cross_check')),
    ('literature', ('literature', 'research', 'citation', 'reference')),
    ('synthesis', ('synthesis', 'addendum', 'final_report', 'evidence_table')),
    ('analysis', ('analysis', 'result', 'model', 'stats', 'figure', 'plot', 'table')),
]

# Standalone heading lines that are boilerplate in almost every generated
# query markdown/report -- e.g. "## Summary", "# Results" -- and must never
# be treated as a query's actual TITLE (see _get_query_markdown_title).
_LANE_BOILERPLATE_HEADING_RE = re.compile(
    r'^#{1,6}\s*(summary|results|findings|conclusion|query report|report|markdown|evidence)\s*$',
    re.IGNORECASE,
)

_QUERY_TITLE_READ_CAP = 500  # chars -- only need the first heading line or two, never the report body

# The report BODY is never scanned for classification: ordinary prose uses
# words like "final"/"combine"/"evidence table" descriptively, and a long
# document accumulates enough incidental hits to drown out the short, deliberate
# summary signal. Only the markdown TITLE heading is used, alongside the summary.
def _get_query_markdown_title(qd: Path) -> str:
    md_path = qd / f"{qd.name}.md"
    if not md_path.exists():
        return ''
    try:
        with open(md_path, 'r', encoding='utf-8', errors='replace') as f:
            snippet = f.read(_QUERY_TITLE_READ_CAP)
    except OSError:
        return ''
    for line in snippet.splitlines():
        stripped = line.strip()
        if not stripped.startswith('#'):
            continue
        if _LANE_BOILERPLATE_HEADING_RE.match(stripped):
            continue  # a heading that's just "Summary"/"Report"/etc, not a real title -- keep looking
        return stripped.lstrip('#').strip()
    return ''


def _lane_cue_scores(text: str, weight: float) -> dict[str, float]:
    """Weighted count of distinct cues from each _LANE_CUE_BUCKETS list appearing in `text` (assumed already lowercased)."""
    scores = {bucket: 0.0 for bucket in _LANE_CUE_BUCKETS}
    if not text:
        return scores
    for bucket, cues in _LANE_CUE_BUCKETS.items():
        scores[bucket] = weight * sum(1 for cue in cues if cue in text)
    return scores


def _classify_lane_from_text(summary_text: str, title_text: str) -> str | None:
    """
    Resolve a visual lane from two SHORT text sources -- the query's cached
    summary (weight 2) and its markdown TITLE heading (weight 1), never the
    report body -- using a fixed PRIORITY so a lane reflects the query's
    actual activity type rather than incidental words:

      1. literature (retrieval phrase) -- an actual review/search/deep-research
         step. Bare "literature"/"published"/"citations" are NOT here, so a
         paragraph that merely mentions literature can't produce Literature.
      2. validation -- explicit verify/validation/cross-check/replicate/
         robustness/null/specificity/control cues. Wins over synthesis so a
         "literature validation" append lands on Validation, not Literature.
      3. synthesis_strong -- genuine integration/consolidation/finalization.
      4. analysis -- any analytical/testing/quantitative cue. Beats
         weak-synthesis, so an "appended addendum" that ran correlation/
         enrichment/scoring is Analysis, not Synthesis.
      5. synthesis_weak -- a bare append/addendum/update with no analytical
         content of its own.

    Returns the lane string, or None when nothing scored (caller falls back to
    filename cues / 'other'). Pure/deterministic and filesystem-free.
    """
    summary_scores = _lane_cue_scores((summary_text or '').strip().lower(), weight=2.0)
    title_scores = _lane_cue_scores((title_text or '').strip().lower(), weight=1.0)
    scores = {bucket: summary_scores[bucket] + title_scores[bucket] for bucket in summary_scores}

    if scores['literature']:
        return 'literature'
    # Validation outranks synthesis (a "literature validation" / null /
    # specificity check that also appends is Validation, not Synthesis), and
    # outranks analysis when it scored at least as strongly, so a clearly
    # framed cross-check/robustness step isn't swallowed by an incidental
    # analysis word it also contains.
    if scores['validation'] and scores['validation'] >= scores['synthesis_strong']:
        return 'validation'
    if scores['synthesis_strong']:
        return 'synthesis'
    if scores['analysis']:
        return 'analysis'
    if scores['validation']:
        return 'validation'
    if scores['synthesis_weak']:
        return 'synthesis'
    return None


def _classify_query_lane(jsonl_objs: list[dict], info: dict | None, summary_text: str, qd: Path) -> str:
    """
    Deterministic visual-lane classification (analysis/literature/
    validation/synthesis/other) for grouping/coloring one query's
    constellation node. Priority:
      1. Structured director-activity evidence (current_step / jsonl
         event_type / subagent field, via the same _resolve_subagent_label
         mapping director_activity uses) -- authoritative when present, so
         it decides outright.
      2. Otherwise, _classify_lane_from_text over the cached summary and the
         markdown TITLE heading only (never the report body -- see
         _get_query_markdown_title).
      3. Filename cues in its top-level folder (excluding boilerplate -- own
         report, checkpoint, query_status.json, the summary cache, logs).
      4. 'other' -- a true fallback, only when nothing above matched.
    """
    candidate_tokens: list[str] = []
    if info and isinstance(info.get('current_step'), str):
        candidate_tokens.append(info['current_step'])
    for obj in jsonl_objs:
        for field in ('event_type', 'subagent', 'agent'):
            val = obj.get(field)
            if isinstance(val, str):
                candidate_tokens.append(val)
    for token in candidate_tokens:
        resolved = _resolve_subagent_label(token)
        if resolved:
            lane = _SUBAGENT_LABEL_TO_LANE.get(resolved[0])
            if lane:
                return lane

    text_lane = _classify_lane_from_text(summary_text, _get_query_markdown_title(qd))
    if text_lane:
        return text_lane

    # Exclude the boilerplate every query writes (its own report, checkpoint,
    # query_status.json, the summary cache, logs) so a file that merely exists
    # can't drive classification.
    own_markdown = f"{qd.name}.md".lower()
    try:
        filenames = [
            p.name.lower() for p in qd.iterdir()
            if p.is_file()
            and p.name.lower() != own_markdown
            and p.name.lower() not in _STANDARD_QUERY_ARTIFACT_NAMES
            and Path(p.name.lower()).suffix not in _STANDARD_QUERY_ARTIFACT_SUFFIXES
        ]
    except OSError:
        filenames = []
    for name in filenames:
        for lane, cues in _LANE_FILENAME_CUES:
            if any(cue in name for cue in cues):
                return lane

    return 'other'


# Filenames/suffixes that are the boilerplate every query writes -- never
# counted as "richness" evidence since they carry no signal about how much
# this particular query actually produced.
_STANDARD_QUERY_ARTIFACT_NAMES = frozenset({'checkpoint.json', 'query_status.json', _QUERY_SUMMARY_CACHE_FILE})
_STANDARD_QUERY_ARTIFACT_SUFFIXES = frozenset({'.log'})
_ARTIFACT_RICHNESS_COUNT_CAP = 5       # notable files beyond which more files stop adding to the score
_ARTIFACT_RICHNESS_SIZE_CAP_BYTES = 200_000  # total non-standard-artifact bytes beyond which more size stops adding to the score
_ARTIFACT_RICHNESS_REPORT_BYTES_CAP = 6_000   # report length beyond which more text stops adding to the score


def _count_notable_artifact_names(qd: Path, names: list[str]) -> int:
    """How many of `names` (filenames, from either a directory listing or artifacts_written metadata) are non-standard for query dir `qd`."""
    own_markdown = f"{qd.name}.md".lower()
    count = 0
    for name in names:
        if not isinstance(name, str):
            continue
        lowered = name.strip().lower()
        if not lowered or lowered == own_markdown or lowered in _STANDARD_QUERY_ARTIFACT_NAMES:
            continue
        if Path(lowered).suffix in _STANDARD_QUERY_ARTIFACT_SUFFIXES:
            continue
        count += 1
    return count


def _compute_artifact_richness(qd: Path, info: dict | None = None) -> float:
    """
    Bounded 0.0-1.0 richness score from generic file-count/size evidence,
    visual-only (feeds the node-size formula in round_monitor.js), not a
    quality measure. Combines three capped signals:
      - report_bytes: size of this query's own report markdown -- its length
        is genuine evidence of how much the query produced.
      - notable_count: non-standard artifacts beyond the boilerplate every
        query writes -- the larger of a top-level directory listing and
        query_status.json's artifacts_written field.
      - nonstandard_bytes: total size of those non-standard files on disk.
    Each is capped so no single signal can dominate.
    """
    try:
        entries = list(qd.iterdir())
    except OSError:
        entries = []

    own_markdown = f"{qd.name}.md".lower()
    report_bytes = 0
    fs_notable_count = 0
    nonstandard_bytes = 0
    for p in entries:
        if not p.is_file():
            continue
        lowered = p.name.lower()
        if lowered == own_markdown:
            try:
                report_bytes = p.stat().st_size
            except OSError:
                pass
            continue
        if lowered in _STANDARD_QUERY_ARTIFACT_NAMES or Path(lowered).suffix in _STANDARD_QUERY_ARTIFACT_SUFFIXES:
            continue
        fs_notable_count += 1
        try:
            nonstandard_bytes += p.stat().st_size
        except OSError:
            pass

    meta_names = (info or {}).get('artifacts_written') or []
    meta_notable_count = _count_notable_artifact_names(qd, meta_names)

    notable_count = max(fs_notable_count, meta_notable_count)
    report_score = min(report_bytes, _ARTIFACT_RICHNESS_REPORT_BYTES_CAP) / _ARTIFACT_RICHNESS_REPORT_BYTES_CAP
    count_score = min(notable_count, _ARTIFACT_RICHNESS_COUNT_CAP) / _ARTIFACT_RICHNESS_COUNT_CAP
    size_score = min(nonstandard_bytes, _ARTIFACT_RICHNESS_SIZE_CAP_BYTES) / _ARTIFACT_RICHNESS_SIZE_CAP_BYTES
    richness = 0.5 * report_score + 0.3 * count_score + 0.2 * size_score
    return round(min(1.0, max(0.0, richness)), 3)


_QUERY_REFERENCE_READ_CAP = 6000  # chars -- generous enough to catch a reference mention anywhere reasonably early in a report


def _gather_query_reference_text(qd: Path) -> str:
    """
    Text to scan for explicit mentions of OTHER queries: this query's own
    top-level markdown report only (capped). Never reads subdirectories or
    other query folders.
    """
    md_path = qd / f"{qd.name}.md"
    if not md_path.exists():
        return ''
    try:
        with open(md_path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read(_QUERY_REFERENCE_READ_CAP)
    except OSError:
        return ''


def _find_referenced_query_ids(text: str, own_query_id: str) -> list[str]:
    """
    Generic scan for ANY mention of another query within a query's own text
    (bare or phrased) -- 'query_004', 'query 4', 'Query 4', 'from query 2',
    'building on query 3', 'compared to query_003', 'see query_003' all
    match, since only the 'query N' token itself is matched, never specific
    surrounding phrasing. Excludes a self-reference (e.g. this query's own
    "# Query 4 Details" title); order-preserving and de-duplicated. This is
    the superset used by both _find_strong_lineage_query_ids (a subset, for
    primary_flow edges) and weak/bare mentions (secondary_reference edges).
    """
    if not text:
        return []
    seen: list[str] = []
    for match in _QUERY_ID_NUM_RE.finditer(text):
        candidate = _canonical_query_id(match.group(0))
        if candidate and candidate != own_query_id and candidate not in seen:
            seen.append(candidate)
    return seen


# Phrases that indicate genuine, deliberate lineage ("this query builds on
# that one"), as opposed to an incidental/bare mention ("compared to
# query_003", "see query_003") that merely references another query without
# claiming to build on it. Only phrases in this list create a primary_flow
# edge; every other mention becomes a much fainter secondary_reference edge
# instead (see _build_round_constellation) -- this is what keeps an early
# query that happens to get name-dropped a lot from becoming a dense hub.
_STRONG_LINEAGE_RE = re.compile(
    r'\b(?:building\s+on|derived\s+from|following|based\s+on|extends?|from)\s+'
    r'q(?:uery)?[\s_-]?0*(\d+)\b',
    re.IGNORECASE,
)


def _find_strong_lineage_query_ids(text: str, own_query_id: str) -> list[str]:
    """
    Subset of _find_referenced_query_ids: only mentions immediately preceded
    by a genuine-lineage phrase (see _STRONG_LINEAGE_RE) -- 'building on
    query 3', 'derived from query 3', 'following query 3', 'based on query
    3', 'from query 3', 'extends query 3'. Never a bare 'query_003' mention
    on its own. Order-preserving and de-duplicated.
    """
    if not text:
        return []
    seen: list[str] = []
    for match in _STRONG_LINEAGE_RE.finditer(text):
        candidate = _canonical_query_id(f"query_{match.group(1)}")
        if candidate and candidate != own_query_id and candidate not in seen:
            seen.append(candidate)
    return seen


_CONSTELLATION_ORIGIN_ID = '__origin__'
_CONSTELLATION_SYNTHESIS_CONVERGENCE_CAP = 3


def _make_constellation_edge(source: str, target: str, reason: str, edge_type: str) -> dict:
    """Every constellation edge is inferred visual structure, not guaranteed scientific lineage -- always direction='downstream' (edges only ever point from an earlier-processed node to a later one, a DAG by construction)."""
    return {'source': source, 'target': target, 'inferred': True, 'reason': reason, 'edge_type': edge_type, 'direction': 'downstream'}

# The root query's first N children ("branch seeds") attach side-by-side under
# the root. Fixed at 2 regardless of round size, so query 4+ stays properly
# downstream instead of becoming another top-level branch.
_CONSTELLATION_BRANCH_SEED_WINDOW = 2

# Recency-weighted fallback-parent scoring for later queries (see
# _score_candidate): a late query prefers recent relevant context over an old
# branch seed unless that seed is still close and lane-compatible.
_CONSTELLATION_RECENCY_WEIGHT = 1.0        # base weight; recency = 1 / (1 + order distance)
_CONSTELLATION_BRANCH_CONTEXT_BONUS = 0.1  # small nudge toward root/branch-seed candidates -- never enough alone to beat a closer, lane-compatible node
_CONSTELLATION_ROOT_SEED_AGE_PENALTY_PER_STEP = 0.02  # grows with distance, specifically for root/branch-seed candidates, so they fade out for genuinely late queries even though _BRANCH_CONTEXT_BONUS nudges toward them
_CONSTELLATION_FANOUT_SCORE_PENALTY = 0.12  # per existing outgoing fallback edge already registered on a candidate -- discourages (not forbids, until the hard cap) reusing an already-busy parent
_CONSTELLATION_HARD_FANOUT_CAP = 3          # absolute outgoing-edge cap per node, regardless of score

# Lane-compatibility bonus per (this query's lane -> candidate's lane), over the
# same 5 generic lanes as _classify_query_lane (nothing dataset-specific).
_CONSTELLATION_LANE_COMPAT_BONUS: dict[str, dict[str, float]] = {
    'analysis': {'analysis': 0.35, 'validation': 0.15, 'synthesis': 0.15, 'literature': 0.1, 'other': 0.1},
    'validation': {'validation': 0.35, 'analysis': 0.3, 'synthesis': 0.25, 'literature': 0.15, 'other': 0.1},
    'literature': {'literature': 0.35, 'analysis': 0.3, 'synthesis': 0.25, 'validation': 0.2, 'other': 0.1},
    'synthesis': {'analysis': 0.3, 'validation': 0.3, 'literature': 0.25, 'other': 0.15, 'synthesis': 0.0},
    'other': {'other': 0.2, 'analysis': 0.15, 'validation': 0.15, 'synthesis': 0.1, 'literature': 0.1},
}


def _build_round_constellation(
    round_dir: Path,
    query_dirs: list[Path],
    infos_by_name: dict[str, dict | None],
    summaries_by_name: dict[str, str],
    statuses_by_name: dict[str, str],
) -> dict:
    """
    Build a deterministic, visualization-only directed research-flow map of
    how the round unfolded: an origin node (Selected Hypothesis, id
    '__origin__'), one node per numeric query_* dir, and edges connecting
    them. Non-numeric query_* dirs (no reliable ordering evidence) are
    omitted here -- the constellation is inherently order-dependent, unlike
    the plain Round Directory list.

    Every edge carries edge_type ('primary_flow' | 'convergence' |
    'secondary_reference') and direction ('downstream', since edges only
    ever point from an earlier-processed node to a later one -- this is a
    DAG by construction). Only primary_flow/convergence edges are meant to
    be visible by default; secondary_reference edges are deliberately much
    fainter/hover-only so a query that gets mentioned a lot in passing
    doesn't read as more connected than one with genuine lineage.

    This is a LAYERED graph, not a flat chain: the root sits under Selected
    Hypothesis, its branch seeds side-by-side beneath it, later queries
    downstream. The frontend derives each node's y from this structure plus
    chronological order (see computeConstellationRankY), so the parent choice
    here is what produces the layered look.

    Edge inference priority per query (first match wins for the PRIMARY parent):
      1. explicit_reference -- an earlier query named via a genuine-lineage
         phrase ("building on query 3", etc; see _find_strong_lineage_query_ids).
         A bare mention does NOT count here (see secondary_reference below).
      2. branch_seed -- one of the root's first _CONSTELLATION_BRANCH_SEED_WINDOW
         children: attach directly to the root.
      3. recent_relevant_context -- score every earlier, not-yet-capped query as
         a fallback parent (see _score_candidate) and pick the best; dominated
         by recency, with a lane-compatibility bonus and a penalty for reaching
         back to the root/a branch seed.
      4. synthesis_convergence -- for a 'synthesis' query, the same scorer picks
         the top _CONSTELLATION_SYNTHESIS_CONVERGENCE_CAP candidates (non-
         synthesis first) as convergence parents.
      5. order_fallback -- attach to the immediately preceding query when no
         scoring candidate exists.
      6. origin_fallback -- no earlier query exists (the root): attach to
         Selected Hypothesis.

    Every other (non-primary) mention becomes a fainter secondary_reference
    edge, so a query named in passing never reads as a hub.

    A final repair pass guarantees every node ends up with at least one
    parent (see the loop after the main one) -- root/floating queries are a
    visual-fallback concern, not a claim of true derivation.
    """
    jsonl_query_groups = _group_round_activity_jsonl_by_query(round_dir)

    numeric_dirs: list[tuple[int, Path]] = []
    for qd in query_dirs:
        suffix = qd.name.split("query_", 1)[-1]
        if suffix.isdigit():
            numeric_dirs.append((int(suffix), qd))
    numeric_dirs.sort(key=lambda item: item[0])

    nodes: list[dict] = []
    lanes_by_id: dict[str, str] = {}

    for order, (num, qd) in enumerate(numeric_dirs, start=1):
        query_id = qd.name
        info = infos_by_name.get(query_id)
        summary_text = summaries_by_name.get(query_id, '') or ''
        status = statuses_by_name.get(query_id, 'unknown')
        jsonl_objs = jsonl_query_groups.get(_canonical_query_id(query_id) or query_id, [])

        lane = _classify_query_lane(jsonl_objs, info, summary_text, qd)
        richness = _compute_artifact_richness(qd, info)
        lanes_by_id[query_id] = lane

        nodes.append({
            'query_id': query_id,
            'display_label': f"Query {num}",
            'status': status,
            'summary': summary_text,
            'lane': lane,
            'artifact_richness': richness,
            'parents': [],  # filled in below -- primary_flow/convergence sources only, not secondary_reference
            'order': order,
        })

    edges: list[dict] = []
    processed_ids: list[str] = []  # ids in order, as they're resolved -- lets "nearest prior" look backward safely
    known_ids = {n['query_id'] for n in nodes}
    order_by_id = {n['query_id']: n['order'] for n in nodes}
    fanout_counts: dict[str, int] = {}  # parent_id -> count of fallback-derived primary_flow/convergence edges out of it

    # Root = earliest numeric query_* dir (order-based, never hardcoded).
    root_id = nodes[0]['query_id'] if nodes else None
    branch_seed_ids = {n['query_id'] for n in nodes[1:1 + _CONSTELLATION_BRANCH_SEED_WINDOW]}

    def _at_fanout_cap(candidate_id: str) -> bool:
        return fanout_counts.get(candidate_id, 0) >= _CONSTELLATION_HARD_FANOUT_CAP

    def _register_fanout(candidate_id: str) -> None:
        fanout_counts[candidate_id] = fanout_counts.get(candidate_id, 0) + 1

    def _score_candidate(candidate_id: str, current_node: dict) -> float:
        """
        Recency-weighted fallback-parent score for `candidate_id` as a
        parent of `current_node`. Recency dominates (so "attach to whatever
        came right before" is the default outcome whenever nothing more
        relevant stands out); lane compatibility adds a modest bonus;
        candidates that already have several fallback children, or that are
        the root/a branch seed being reached for by a now-distant later
        query, are penalized so a late query doesn't default back to an
        early hub just because it was the first thing that ever matched.
        """
        distance = current_node['order'] - order_by_id[candidate_id]
        score = _CONSTELLATION_RECENCY_WEIGHT / (1.0 + distance)
        score += _CONSTELLATION_LANE_COMPAT_BONUS.get(current_node['lane'], {}).get(lanes_by_id[candidate_id], 0.0)
        if candidate_id == root_id or candidate_id in branch_seed_ids:
            score += _CONSTELLATION_BRANCH_CONTEXT_BONUS
            score -= _CONSTELLATION_ROOT_SEED_AGE_PENALTY_PER_STEP * distance
        score -= _CONSTELLATION_FANOUT_SCORE_PENALTY * fanout_counts.get(candidate_id, 0)
        return score

    def _ranked_candidates(current_node: dict, exclude_lane: str | None = None) -> list[str]:
        """processed_ids not yet at the hard fan-out cap, best score first."""
        pool = [
            pid for pid in processed_ids
            if not _at_fanout_cap(pid) and (exclude_lane is None or lanes_by_id[pid] != exclude_lane)
        ]
        pool.sort(key=lambda pid: _score_candidate(pid, current_node), reverse=True)
        return pool

    for node in nodes:
        query_id = node['query_id']
        qd = round_dir / query_id
        reference_text = _gather_query_reference_text(qd)
        all_refs = [
            ref for ref in _find_referenced_query_ids(reference_text, query_id)
            if ref in known_ids and ref in processed_ids
        ]
        strong_refs = [
            ref for ref in _find_strong_lineage_query_ids(reference_text, query_id)
            if ref in known_ids and ref in processed_ids
        ]

        parents: list[str] = []
        reason = 'origin_fallback'
        edge_type = 'primary_flow'

        if strong_refs:
            parents = strong_refs
            reason = 'explicit_reference'
            # explicit_reference edges are exempt from the fan-out cap (see
            # module docstring) -- deliberately not registered below.
        elif query_id in branch_seed_ids and root_id in processed_ids and not _at_fanout_cap(root_id):
            # Branch seeds attach directly under the root (rule 2).
            parents = [root_id]
            reason = 'branch_seed'
            _register_fanout(root_id)
        elif node['lane'] == 'synthesis' and processed_ids:
            ranked = _ranked_candidates(node, exclude_lane='synthesis') or _ranked_candidates(node)
            parents = ranked[:_CONSTELLATION_SYNTHESIS_CONVERGENCE_CAP]
            reason = 'synthesis_convergence'
            edge_type = 'convergence'
            for pid in parents:
                _register_fanout(pid)
        elif processed_ids:
            ranked = _ranked_candidates(node)
            if ranked:
                parents = [ranked[0]]
                reason = 'recent_relevant_context'
                _register_fanout(ranked[0])

        if not parents and processed_ids:
            parents = [processed_ids[-1]]
            reason = 'order_fallback'
            # Never subject to the fan-out cap -- see docstring.

        if parents:
            node['parents'] = parents
            for parent_id in parents:
                edges.append(_make_constellation_edge(parent_id, query_id, reason, edge_type))
        else:
            # Record the origin sentinel as this node's parent (not just as a
            # bare edge) so the frontend's upstream traversal, which reads
            # node.parents, sees Selected Hypothesis as an upstream neighbor.
            node['parents'] = [_CONSTELLATION_ORIGIN_ID]
            edges.append(_make_constellation_edge(_CONSTELLATION_ORIGIN_ID, query_id, 'origin_fallback', 'primary_flow'))

        # Every other bare mention becomes a fainter secondary_reference edge.
        for ref in all_refs:
            if ref in strong_refs:
                continue
            edges.append(_make_constellation_edge(ref, query_id, 'weak_mention', 'secondary_reference'))

        processed_ids.append(query_id)

    # Defensive net: the loop above always assigns a parent, but never let a
    # node render floating if a future rule change leaves one parentless.
    for node in nodes:
        if not node['parents']:
            node['parents'] = [_CONSTELLATION_ORIGIN_ID]
            edges.append(_make_constellation_edge(_CONSTELLATION_ORIGIN_ID, node['query_id'], 'origin_fallback', 'primary_flow'))

    return {
        'origin': {'label': 'Selected Hypothesis'},
        'nodes': nodes,
        'edges': edges,
    }


def _file_node(path: Path) -> dict:
    """Build a leaf-file tree node from stat metadata only (no content reads)."""
    try:
        exists = path.exists()
    except OSError:
        exists = False
    node = {'name': path.name, 'type': 'file', 'status': 'present' if exists else 'missing'}
    if exists:
        try:
            node['last_modified'] = path.stat().st_mtime
        except OSError:
            node['last_modified'] = None
    return node


ROUND_WORKSPACE_TREE_MAX_DEPTH = 10
ROUND_WORKSPACE_TREE_MAX_NODES = 5000
ROUND_WORKSPACE_TREE_IGNORED_NAMES = {
    '.DS_Store', '__pycache__', '.pytest_cache', '.git', '.ipynb_checkpoints',
    'checkpoint.json', 'messages.jsonl', 'tool_calls.jsonl',
    'round_activity.jsonl', 'query_status.json', 'live_round.log', 'round.log',
}
ROUND_WORKSPACE_TREE_IGNORED_SUFFIXES = {'.lock', '.tmp', '.temp', '.pyc', '.pyo', '.swp'}

ROUND_WORKSPACE_HIDDEN_EXACT = {
    '.flask_session', '.git', '.pytest_cache', '.ipynb_checkpoints', '__pycache__',
    'latest', 'checkpoint.json', 'messages.jsonl', 'tool_calls.jsonl',
    'round_activity.jsonl', 'query_status.json', 'config.json', '.env',
}
ROUND_WORKSPACE_HIDDEN_DIRS = {
    'cache', 'caches', 'checkpoints', 'interactive_generation_logs', 'interactive_hyp_gen',
}
ROUND_WORKSPACE_FIGURE_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.tif', '.tiff',
}
ROUND_WORKSPACE_SCIENTIFIC_OUTPUT_ROOTS = {
    'figures', 'figure', 'images', 'visualizations', 'visualization',
    'outputs', 'output', 'results', 'result',
}
ROUND_WORKSPACE_TECHNICAL_EXTENSIONS = {
    '.py', '.r', '.js', '.ts', '.sh', '.sql', '.yaml', '.yml', '.tex', '.log', '.jsonl',
}
ROUND_WORKSPACE_EXTERNAL_ARTIFACT_EXTENSIONS = (
    ROUND_WORKSPACE_FIGURE_EXTENSIONS | {'.pdf'}
)
ROUND_WORKSPACE_REFERENCE_MAX_BYTES = 10 * 1024 * 1024
ROUND_WORKSPACE_ARTIFACT_CACHE_TTL_SECS = float(
    os.environ.get('DISCOVERY_AGENT_ROUND_WORKSPACE_ARTIFACT_CACHE_TTL_SECS', '15')
)
ROUND_WORKSPACE_TREE_CACHE_TTL_SECS = float(
    os.environ.get('DISCOVERY_AGENT_ROUND_WORKSPACE_TREE_CACHE_TTL_SECS', '5')
)
_ROUND_WORKSPACE_ARTIFACT_CACHE: dict[tuple, tuple[float, dict]] = {}
_ROUND_WORKSPACE_TREE_CACHE: dict[tuple, tuple[float, dict]] = {}
_ROUND_WORKSPACE_ROUND_ENTRY_CACHE: dict[tuple, tuple[float, dict]] = {}
_ROUND_WORKSPACE_CACHE_LOCK = threading.RLock()


def _round_workspace_state_token(round_dir: Path) -> tuple:
    """Cheap invalidation token for files that change a round's visible state.

    The cache TTL remains the final guard for deeply nested output changes.
    Direct round/query directory metadata catches normal backend writes without
    repeating the full recursive tree walk merely to decide whether to reuse it.
    """
    token: list[tuple[str, int, int]] = []
    try:
        entries = list(round_dir.iterdir())
    except OSError:
        return (('missing', 0, 0),)
    for path in entries:
        try:
            stat = path.stat()
        except OSError:
            continue
        token.append((path.name, stat.st_mtime_ns, stat.st_size))
        if path.is_dir() and re.fullmatch(r'query_\d+', path.name):
            for name in ('query_status.json', 'checkpoint.json', f'{path.name}.md'):
                child = path / name
                try:
                    child_stat = child.stat()
                except OSError:
                    continue
                token.append((f'{path.name}/{name}', child_stat.st_mtime_ns, child_stat.st_size))
    return tuple(sorted(token))


def _round_workspace_request_registry_cache() -> dict:
    """One registry map per Flask request, avoiding recursive rebuilds per figure."""
    if not has_request_context():
        return {}
    cache = getattr(g, '_round_workspace_artifact_registries', None)
    if cache is None:
        cache = {}
        g._round_workspace_artifact_registries = cache
    return cache


def _round_workspace_source_id(project_id: str, round_index: int, source_kind: str) -> str:
    """Return a stable opaque browser identifier for one trusted round source."""
    value = f'{project_id}:{round_index}:{source_kind}'.encode('utf-8')
    return f'src_{hashlib.sha256(value).hexdigest()[:20]}'


def _round_workspace_safe_relative(raw: str) -> PurePosixPath | None:
    """Normalize a persisted POSIX path without ever accepting traversal."""
    if not isinstance(raw, str) or not raw.strip() or '\x00' in raw:
        return None
    path = PurePosixPath(raw.strip().replace('\\', '/'))
    if path.is_absolute() or any(part in {'', '.', '..'} for part in path.parts):
        return None
    return path


def _round_workspace_external_file_allowed(path: Path, root: Path, relative: PurePosixPath) -> bool:
    """Apply hidden/technical and containment rules to an exact external reference."""
    lowered = [part.lower() for part in relative.parts]
    name = relative.name.lower()
    if (
        not relative.parts
        or any(part.startswith('.') for part in relative.parts)
        or any(part in ROUND_WORKSPACE_HIDDEN_EXACT or part in ROUND_WORKSPACE_HIDDEN_DIRS for part in lowered)
        or any(part.startswith(('interactive_generation_', 'interactive_hyp_gen')) for part in lowered)
        or any(part.endswith('_cache') for part in lowered)
        or any(token in name for token in ('credential', 'secret', 'token', 'raw_messages', 'tool_calls'))
        or path.suffix.lower() not in ROUND_WORKSPACE_EXTERNAL_ARTIFACT_EXTENSIONS
    ):
        return False
    try:
        cursor = root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                return False
        resolved_root = root.resolve()
        resolved = path.resolve()
        return resolved.is_relative_to(resolved_root) and resolved.is_file()
    except OSError:
        return False


def _round_workspace_reference_strings(round_dir: Path) -> set[str]:
    """Collect exact artifact references persisted by this round.

    Reports and checkpoint workflow results are the round-scoped manifests
    available in existing DiscoveryAgent runs. Only image/PDF-looking strings
    are retained; this function does not enumerate any external directory.
    """
    references: set[str] = set()
    suffixes = '|'.join(re.escape(ext.lstrip('.')) for ext in sorted(
        ROUND_WORKSPACE_EXTERNAL_ARTIFACT_EXTENSIONS, key=len, reverse=True
    ))
    pattern = re.compile(
        rf'(?P<path>(?:/[^\s`"\'<>()\[\]]+|(?:outputs|images|logs)/[^\s`"\'<>()\[\]]+)\.(?:{suffixes}))',
        re.IGNORECASE,
    )

    candidates: list[Path] = []
    try:
        candidates.extend(path for path in round_dir.rglob('*.md') if path.is_file())
        candidates.extend(path for path in round_dir.glob('query_*/checkpoint.json') if path.is_file())
    except OSError:
        return references
    for candidate in candidates:
        try:
            if candidate.is_symlink() or candidate.stat().st_size > ROUND_WORKSPACE_REFERENCE_MAX_BYTES:
                continue
            text = candidate.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        for match in pattern.finditer(text):
            references.add(match.group('path').rstrip('.,;:'))
    return references


def _build_round_workspace_artifact_registry(round_index: int) -> dict[str, dict]:
    """Build the sole trusted registry used by both tree inventory and serving."""
    checkpoint_dir = _round_workspace_checkpoint_dir_for_session()
    record = _active_project_record()
    if checkpoint_dir is None:
        return {}
    rounds_root = _v2_round_dirs_root_from_any_checkpoint_path(checkpoint_dir).resolve()
    round_dir = rounds_root / f'round_{round_index}'
    project_id = str((record or {}).get('id') or session.get('v2_active_project_slug') or 'active')
    checkpoint_id = _round_workspace_source_id(project_id, round_index, 'checkpoint')
    registry = {
        checkpoint_id: {
            'kind': 'checkpoint', 'root': round_dir, 'files': None,
        },
    }

    config_path = round_dir / 'config.json'
    try:
        payload = json.loads(config_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        app.logger.debug('Round %s has no trusted external artifact mapping', round_index)
        return registry
    config = payload.get('config') if isinstance(payload, dict) else None
    persisted_round = payload.get('round_idx') if isinstance(payload, dict) else None
    # New V2 round_0 configs may omit round_idx, but historical runner configs
    # must match their directory explicitly.
    if not isinstance(config, dict) or (
        persisted_round is not None and persisted_round != round_index
    ):
        app.logger.warning('Rejected mismatched external artifact metadata for round %s', round_index)
        return registry

    remote_working_raw = config.get('working_directory')
    remote_log_raw = config.get('log_dir')
    remote_checkpoint_raw = config.get('checkpoint_base_dir')
    if not all(isinstance(value, str) and value.strip() for value in (
        remote_working_raw, remote_log_raw, remote_checkpoint_raw,
    )):
        app.logger.debug('Round %s has no trusted external artifact mapping', round_index)
        return registry
    remote_working = PurePosixPath(remote_working_raw)
    remote_log = PurePosixPath(remote_log_raw)
    remote_checkpoint = PurePosixPath(remote_checkpoint_raw)
    if (
        not remote_working.is_absolute()
        or not remote_log.is_absolute()
        or not remote_checkpoint.is_absolute()
        or any('..' in path.parts for path in (remote_working, remote_log, remote_checkpoint))
        or remote_checkpoint.parent != remote_log
        or remote_checkpoint.name != 'checkpoints'
    ):
        app.logger.warning('Rejected invalid external artifact roots for round %s', round_index)
        return registry

    code_raw = (record or {}).get('code_dir')
    checkpoint_raw = (record or {}).get('checkpoint_dir')
    if not code_raw or not checkpoint_raw:
        return registry
    code_root = Path(code_raw)
    project_checkpoint = Path(checkpoint_raw)
    try:
        # These roots must be the active project's registered siblings.
        project_root = project_checkpoint.resolve()
        if re.fullmatch(r'round_\d+', project_root.name):
            project_root = project_root.parent.parent
        elif project_root.name == 'checkpoints':
            project_root = project_root.parent
        if code_root.resolve().parent != project_root.parent:
            app.logger.warning('Rejected cross-project external artifact roots for round %s', round_index)
            return registry
        run_root = rounds_root.parent
    except OSError:
        return registry

    output_files: dict[str, dict] = {}
    run_files: dict[str, dict] = {}
    references = _round_workspace_reference_strings(round_dir)
    for reference in references:
        reference_path = PurePosixPath(reference)
        source_root: Path | None = None
        relative: PurePosixPath | None = None
        display: PurePosixPath | None = None
        target_files: dict[str, dict] | None = None
        if reference_path.is_absolute():
            try:
                relative = reference_path.relative_to(remote_working)
                source_root, display, target_files = code_root, relative, output_files
            except ValueError:
                try:
                    relative = reference_path.relative_to(remote_log)
                    source_root, display, target_files = run_root, PurePosixPath('logs') / relative, run_files
                except ValueError:
                    continue
        else:
            relative = _round_workspace_safe_relative(reference)
            if relative is None:
                continue
            if relative.parts[0].lower() == 'outputs':
                source_root, display, target_files = code_root, relative, output_files
            elif relative.parts[0].lower() == 'images':
                source_root, display, target_files = run_root, PurePosixPath('logs') / relative, run_files
            elif len(relative.parts) > 1 and tuple(part.lower() for part in relative.parts[:2]) == ('logs', 'images'):
                relative = PurePosixPath(*relative.parts[1:])
                source_root, display, target_files = run_root, PurePosixPath('logs') / relative, run_files
            else:
                continue
        target = source_root.joinpath(*relative.parts)
        if not _round_workspace_external_file_allowed(target, source_root, relative):
            continue
        artifact_path = relative.as_posix()
        target_files[artifact_path] = {
            'target': target.resolve(), 'display_path': display.as_posix(),
            'artifact_path': artifact_path, 'visibility': 'research',
        }

    for kind, root, files in (
        ('execution-output', code_root, output_files),
        ('run-artifacts', run_root, run_files),
    ):
        if files:
            registry[_round_workspace_source_id(project_id, round_index, kind)] = {
                'kind': kind, 'root': root, 'files': files,
            }
    if references and not output_files and not run_files:
        app.logger.debug(
            'Round %s has %s external artifact references but no referenced files are present locally',
            round_index, len(references),
        )
    return registry


def _round_workspace_artifact_registry(round_index: int) -> dict[str, dict]:
    """Return a trusted registry, reused within and briefly across requests."""
    checkpoint_dir = _round_workspace_checkpoint_dir_for_session()
    if checkpoint_dir is None:
        return {}
    rounds_root = _v2_round_dirs_root_from_any_checkpoint_path(checkpoint_dir).resolve()
    round_dir = rounds_root / f'round_{round_index}'
    record = _active_project_record() or {}
    cache_key = (
        str(record.get('id') or session.get('v2_active_project_slug') or 'active'),
        round_index,
        str(round_dir),
        str(record.get('code_dir') or ''),
        _round_workspace_state_token(round_dir),
    )
    request_cache = _round_workspace_request_registry_cache()
    if cache_key in request_cache:
        return request_cache[cache_key]
    now = time.monotonic()
    with _ROUND_WORKSPACE_CACHE_LOCK:
        cached = _ROUND_WORKSPACE_ARTIFACT_CACHE.get(cache_key)
        if cached and now - cached[0] <= ROUND_WORKSPACE_ARTIFACT_CACHE_TTL_SECS:
            request_cache[cache_key] = cached[1]
            return cached[1]
    registry = _build_round_workspace_artifact_registry(round_index)
    with _ROUND_WORKSPACE_CACHE_LOCK:
        _ROUND_WORKSPACE_ARTIFACT_CACHE[cache_key] = (now, registry)
        if len(_ROUND_WORKSPACE_ARTIFACT_CACHE) > 64:
            oldest = min(_ROUND_WORKSPACE_ARTIFACT_CACHE, key=lambda key: _ROUND_WORKSPACE_ARTIFACT_CACHE[key][0])
            _ROUND_WORKSPACE_ARTIFACT_CACHE.pop(oldest, None)
    request_cache[cache_key] = registry
    return registry


def _round_workspace_resolve_artifact(
    round_index: int,
    source_id: str,
    relative_path: str,
) -> Path | None:
    """Resolve one browser artifact identity through the trusted registry."""
    relative = _round_workspace_safe_relative(relative_path)
    if relative is None:
        return None
    registry = _round_workspace_artifact_registry(round_index)
    source = registry.get(source_id)
    if source is None:
        return None
    if source['kind'] == 'checkpoint':
        root = source['root']
        unresolved = root.joinpath(*relative.parts)
        try:
            status = _v2_round_status(_inspect_latest_round(root))
            if _round_workspace_visibility(unresolved, root, status) == 'hidden':
                return None
            resolved = unresolved.resolve()
            if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
                return None
            return resolved
        except (OSError, ValueError):
            return None
    descriptor = source['files'].get(relative.as_posix())
    if descriptor is None:
        return None
    target = descriptor['target']
    return target if _round_workspace_external_file_allowed(target, source['root'], relative) else None


def _round_workspace_visibility(path: Path, round_dir: Path, round_status: str) -> str:
    """Classify one real round entry as research, technical, or hidden.

    This is the shared authority for both the tree and artifact serving. It is
    intentionally path/context aware: extensions alone do not make JSON/text
    scientific, and backend-control names remain inaccessible at every depth.
    """
    try:
        relative = path.relative_to(round_dir)
    except ValueError:
        return 'hidden'
    parts = relative.parts
    lowered = [part.lower() for part in parts]
    name = path.name.lower()
    if not parts or any(part in {'.', '..'} or part.startswith('.') for part in parts):
        return 'hidden'
    try:
        cursor = round_dir
        for part in parts:
            cursor = cursor / part
            if cursor.is_symlink():
                return 'hidden'
    except OSError:
        return 'hidden'
    if any(part in ROUND_WORKSPACE_HIDDEN_EXACT or part in ROUND_WORKSPACE_HIDDEN_DIRS for part in lowered):
        return 'hidden'
    if any(part.startswith('_temporarily_hidden_') for part in lowered):
        return 'hidden'
    if any(part.startswith(('interactive_generation_', 'interactive_hyp_gen')) for part in lowered):
        return 'hidden'
    if name.startswith(('checkpoint', 'session_', 'auth_')) or name in {
        'session.json', 'authentication.json', 'status.json', 'activity.jsonl',
    }:
        return 'hidden'
    if any(token in name for token in ('raw_messages', 'model_messages', 'tool_calls')):
        return 'hidden'
    if any(part.endswith('_cache') for part in lowered):
        return 'hidden'
    if name.startswith(('config_', 'config-backend', 'config_backend_')) or name == 'config.json':
        return 'hidden'
    if name.endswith(('.bak', '.backup', '.lock', '.pid', '.tmp', '.temp', '.swp', '.pyc', '.pyo')):
        return 'hidden'
    if any(token in name for token in ('credential', 'secret', 'token')):
        return 'hidden'
    if name in {'round_report.md', 'memory_report.md'} and len(parts) == 1:
        try:
            eligible = (
                round_status == 'completed'
                and path.is_file()
                and not path.is_symlink()
                and path.stat().st_size > 0
                and path.resolve().is_relative_to(round_dir.resolve())
            )
            return 'research' if eligible else 'hidden'
        except OSError:
            return 'hidden'
    try:
        if path.is_dir():
            return 'technical'  # promoted to research when a research descendant survives
    except OSError:
        return 'hidden'

    if re.fullmatch(r'query_\d+', path.parent.name.lower()) and name == f'{path.parent.name.lower()}.md':
        return 'research'
    if name == 'selected_hypothesis.md' and len(parts) == 1:
        return 'research'
    if name.startswith('selected_hypothesis') and path.suffix.lower() == '.md':
        return 'technical'
    if name in {'generated_hypotheses.json', 'generated_hypotheses.md'}:
        return 'technical'
    if name == 'hypothesis_literature_context.md' or ('searches' in lowered and path.suffix.lower() == '.md'):
        return 'technical'
    if name.startswith('hypothesis_literature_context') and path.suffix.lower() == '.json':
        return 'technical'
    if name in {'round.log', 'live_round.log', 'ai_researcher.log', 'ai_researcher_actions.log'}:
        return 'technical'
    if name.startswith('backend_round') and path.suffix.lower() == '.log':
        return 'technical'
    if name == 'transcript.txt':
        return 'technical'

    ext = path.suffix.lower()
    if ext in ROUND_WORKSPACE_FIGURE_EXTENSIONS:
        first = lowered[0]
        in_query_hierarchy = bool(re.fullmatch(r'query_\d+', first))
        in_query_reports = first == 'da_query_reports'
        in_output_root = first in ROUND_WORKSPACE_SCIENTIFIC_OUTPUT_ROOTS
        in_tracked_images = len(lowered) >= 2 and lowered[:2] == ['logs', 'images']
        return (
            'research'
            if in_query_hierarchy or in_query_reports or in_output_root or in_tracked_images
            else 'hidden'
        )
    if ext in ROUND_WORKSPACE_TECHNICAL_EXTENSIONS:
        return 'technical'
    if ext in {'.md', '.markdown'}:
        return 'technical'
    if ext == '.json':
        return 'technical'
    if ext == '.txt':
        return 'technical'
    return 'technical'


def _curate_round_workspace_tree(node: dict, round_dir: Path, round_status: str) -> dict | None:
    """Recursively remove hidden/empty entries and annotate retained visibility."""
    target = round_dir / node.get('path', '')
    visibility = 'research' if not node.get('path') else _round_workspace_visibility(target, round_dir, round_status)
    if visibility == 'hidden':
        return None
    if node.get('type') == 'dir':
        children = []
        for child in node.get('children', []):
            curated = _curate_round_workspace_tree(child, round_dir, round_status)
            if curated is not None:
                children.append(curated)
        if node.get('path') and not children:
            return None
        node['children'] = children
        visibility = 'research' if any(child.get('visibility') == 'research' for child in children) else 'technical'
    node['visibility'] = visibility
    return node


def _round_workspace_sort_key(path: Path) -> tuple[int, list[object], str]:
    """Sort like an IDE explorer: directories first, then natural name order."""
    try:
        is_dir = path.is_dir() and not path.is_symlink()
    except OSError:
        is_dir = False
    parts: list[tuple[int, object]] = []
    for piece in re.split(r'(\d+)', path.name.lower()):
        parts.append((0, int(piece)) if piece.isdigit() else (1, piece))
    return (0 if is_dir else 1, parts, path.name.lower())


def _round_workspace_tree_node(
    path: Path,
    root: Path,
    depth: int = 0,
    budget: dict | None = None,
) -> dict:
    """Build a recursive filesystem tree for the Round Workspace explorer.

    Stat metadata only; file contents are never read. Symlink directories are
    displayed but not traversed, preventing cycles and out-of-root walks.
    """
    if budget is None:
        budget = {'remaining': ROUND_WORKSPACE_TREE_MAX_NODES, 'truncated': False}
    budget['remaining'] -= 1

    try:
        stat = path.stat()
        exists = True
    except OSError:
        stat = None
        exists = False

    try:
        rel_path = '' if path == root else str(path.relative_to(root))
    except ValueError:
        rel_path = path.name

    try:
        is_dir = path.is_dir() and not path.is_symlink()
    except OSError:
        is_dir = False

    node = {
        'name': path.name,
        'path': rel_path,
        'type': 'dir' if is_dir else 'file',
        'status': 'present' if exists else 'missing',
        'last_modified': stat.st_mtime if stat else None,
    }
    if stat and not is_dir:
        node['size'] = stat.st_size
    if path.is_symlink():
        node['symlink'] = True

    if is_dir:
        children = []
        if depth >= ROUND_WORKSPACE_TREE_MAX_DEPTH:
            node['truncated'] = True
            budget['truncated'] = True
        elif budget['remaining'] <= 0:
            node['truncated'] = True
            budget['truncated'] = True
        else:
            try:
                entries = [
                    entry for entry in path.iterdir()
                    if entry.name not in ROUND_WORKSPACE_TREE_IGNORED_NAMES
                    and not entry.name.startswith('.')
                    and entry.suffix.lower() not in ROUND_WORKSPACE_TREE_IGNORED_SUFFIXES
                ]
                entries.sort(key=_round_workspace_sort_key)
            except OSError:
                entries = []
                node['status'] = 'unreadable'
            for entry in entries:
                if budget['remaining'] <= 0:
                    node['truncated'] = True
                    budget['truncated'] = True
                    break
                children.append(_round_workspace_tree_node(entry, root, depth + 1, budget))
        node['children'] = children
    return node


def _round_workspace_annotate_source(node: dict, source_id: str) -> None:
    node['source'] = source_id
    node['source_kind'] = 'checkpoint'
    if node.get('type') == 'file':
        node['artifact_path'] = node.get('path', '')
    for child in node.get('children', []):
        _round_workspace_annotate_source(child, source_id)


def _round_workspace_tree_file_paths(node: dict, root: Path) -> list[Path]:
    """Return physical file paths already discovered by the raw tree walk."""
    paths: list[Path] = []
    if node.get('type') == 'file' and node.get('path'):
        paths.append(root / node['path'])
    for child in node.get('children', []):
        paths.extend(_round_workspace_tree_file_paths(child, root))
    return paths


def _round_workspace_insert_external_node(root: dict, descriptor: dict, source_id: str) -> None:
    display = PurePosixPath(descriptor['display_path'])
    children = root.setdefault('children', [])
    walked: list[str] = []
    for part in display.parts[:-1]:
        walked.append(part)
        child = next(
            (item for item in children if item.get('type') == 'dir' and item.get('name') == part),
            None,
        )
        if child is None:
            child = {
                'name': part, 'path': '/'.join(walked), 'type': 'dir',
                'status': 'present', 'visibility': 'research', 'children': [],
            }
            children.append(child)
        child['visibility'] = 'research'
        children = child['children']
    target = descriptor['target']
    try:
        stat = target.stat()
    except OSError:
        return
    children.append({
        'name': display.name,
        'path': display.as_posix(),
        'artifact_path': descriptor['artifact_path'],
        'source': source_id,
        'source_kind': 'external',
        'type': 'file',
        'status': 'present',
        'visibility': 'research',
        'size': stat.st_size,
        'last_modified': stat.st_mtime,
    })


def _round_workspace_sort_tree(node: dict) -> None:
    children = node.get('children', [])
    children.sort(key=lambda item: (
        0 if item.get('type') == 'dir' else 1,
        [
            (0, int(piece)) if piece.isdigit() else (1, piece)
            for piece in re.split(r'(\d+)', item.get('name', '').lower())
        ],
        item.get('name', '').lower(),
    ))
    for child in children:
        _round_workspace_sort_tree(child)


def _round_workspace_flatten_query_reports(tree: dict) -> None:
    """Promote query_N/query_N.md into the round root without changing its path."""
    promoted: list[dict] = []
    retained: list[dict] = []
    for child in tree.get('children', []):
        match = re.fullmatch(r'query_(\d+)', child.get('name', ''), re.IGNORECASE)
        if child.get('type') != 'dir' or match is None:
            retained.append(child)
            continue
        report_name = f"{child['name'].lower()}.md"
        report = next((
            item for item in child.get('children', [])
            if item.get('type') == 'file' and item.get('name', '').lower() == report_name
        ), None)
        remaining = [item for item in child.get('children', []) if item is not report]
        if report is not None:
            promoted.append(report)
        if remaining:
            child['children'] = remaining
            retained.append(child)
    tree['children'] = retained + promoted


def _round_workspace_add_associated_selected_hypothesis(tree: dict, round_dir: Path) -> None:
    """Expose the launcher hypothesis when legacy runs stored it in the prior round."""
    if any(
        child.get('type') == 'file' and child.get('name') == 'selected_hypothesis.md'
        for child in tree.get('children', [])
    ):
        return
    selected = _locate_selected_hypothesis_context(round_dir)
    if selected is None:
        return
    try:
        stat = selected.stat()
        storage_match = re.fullmatch(r'round_(\d+)', selected.parent.name)
        if not storage_match or not selected.is_file() or selected.is_symlink():
            return
    except OSError:
        return
    tree.setdefault('children', []).append({
        'name': 'selected_hypothesis.md',
        'path': 'selected_hypothesis.md',
        'artifact_path': selected.name,
        'storage_round_index': int(storage_match.group(1)),
        'source_kind': 'checkpoint',
        'type': 'file',
        'status': 'present',
        'visibility': 'research',
        'size': stat.st_size,
        'last_modified': stat.st_mtime,
    })


def _round_workspace_sort_round_root(tree: dict) -> None:
    """Order the curated round root by the scientific workflow."""
    def _key(item: dict) -> tuple:
        name = item.get('name', '').lower()
        path = item.get('path', '')
        query_match = re.fullmatch(r'query_0*(\d+)\.md', name)
        if name == 'selected_hypothesis.md':
            return (0, 0, name)
        if query_match and re.fullmatch(r'query_\d+/query_\d+\.md', path, re.IGNORECASE):
            return (1, int(query_match.group(1)), name)
        if name == 'memory_report.md':
            return (3, 0, name)
        if name == 'round_report.md':
            return (4, 0, name)
        return (2, 0 if item.get('type') == 'dir' else 1, name)

    tree['children'].sort(key=_key)


def _round_workspace_entry(
    index: int,
    round_dir: Path,
    active_index: int | None,
    registry: dict[str, dict] | None = None,
) -> dict:
    budget = {'remaining': ROUND_WORKSPACE_TREE_MAX_NODES, 'truncated': False}
    info = _inspect_latest_round(round_dir)
    status = _v2_round_status(info)
    raw_tree = _round_workspace_tree_node(round_dir, round_dir, budget=budget)
    tree = _curate_round_workspace_tree(raw_tree, round_dir, status) or {
        'name': round_dir.name, 'path': '', 'type': 'dir', 'children': [], 'visibility': 'research',
    }
    checkpoint_source = next((
        source_id for source_id, source in (registry or {}).items()
        if source.get('kind') == 'checkpoint'
    ), 'checkpoint')
    _round_workspace_annotate_source(tree, checkpoint_source)
    _round_workspace_add_associated_selected_hypothesis(tree, round_dir)
    _round_workspace_flatten_query_reports(tree)
    _round_workspace_sort_tree(tree)
    _round_workspace_sort_round_root(tree)
    # Reports stay ordinary alphabetically-sorted file nodes. Incomplete or
    # empty report artifacts are suppressed; a valid completed round report is
    # annotated for a decorative tree label only (its name/path stay intact).
    valid_round_report = status == 'completed' and any(
        child.get('name') == 'round_report.md'
        and child.get('type') == 'file'
        and not child.get('symlink')
        and (child.get('size') or 0) > 0
        for child in tree.get('children', [])
    )
    tree['children'] = [
        child for child in tree.get('children', [])
        if child.get('name') not in {'round_report.md', 'memory_report.md'}
        or (
            status == 'completed'
            and child.get('type') == 'file'
            and not child.get('symlink')
            and (child.get('size') or 0) > 0
        )
    ]
    if valid_round_report:
        next(
            child for child in tree['children']
            if child.get('name') == 'round_report.md'
        )['primary_output'] = True
    return {
        'round_index': index,
        'storage_round_index': index,
        # Round Workspace mirrors the actual filesystem folder numbers
        # (round_0 -> Round 0), unlike Hypothesis Studio's +1 cycle labels.
        'target_round_number': index,
        'label': f'Round {index}',
        'status': status,
        'is_current': index == active_index,
        'tree': tree,
        'truncated': budget['truncated'],
        'has_round_report': valid_round_report,
    }


def _round_workspace_artifact_path(round_index: int, relative_path: str) -> Path | None:
    """Resolve an artifact under one active-project round without allowing traversal."""
    checkpoint_dir = _round_workspace_checkpoint_dir_for_session()
    if checkpoint_dir is None or not relative_path or '\x00' in relative_path:
        return None
    rounds_root = _v2_round_dirs_root_from_any_checkpoint_path(checkpoint_dir).resolve()
    round_dir = (rounds_root / f'round_{round_index}').resolve()
    try:
        unresolved = round_dir / relative_path
        status = _v2_round_status(_inspect_latest_round(round_dir))
        if _round_workspace_visibility(unresolved, round_dir, status) == 'hidden':
            return None
        target = unresolved.resolve()
        if not target.is_relative_to(round_dir) or not target.is_file():
            return None
    except (OSError, ValueError):
        return None
    return target


def _round_workspace_checkpoint_dir_for_session() -> Path | None:
    """Resolve the active project's checkpoint root for the workspace API.

    Prefer the active V2 project registry record over session.project_config so
    the endpoint still works after legacy session sync paths that did not copy
    every registry field into the Flask session.
    """
    record = _active_project_record()
    if record and record.get('checkpoint_dir'):
        return Path(record['checkpoint_dir'])

    checkpoint_dir = _get_project_config().get('checkpoint_dir')
    if checkpoint_dir:
        return Path(checkpoint_dir)

    project_slug = session.get('v2_active_project_slug')
    if project_slug:
        project, _matched_by_id = _v2_resolve_project_by_slug_or_id(project_slug)
        if project and project.get('checkpoint_dir'):
            return Path(project['checkpoint_dir'])
    return None


def _round_workspace_code_root_for_session() -> Path | None:
    """Return the active project's registered code directory."""
    record = _active_project_record()
    raw = (record or {}).get('code_dir')
    if not raw:
        raw = _get_project_config().get('code_dir')
    if not raw:
        return None
    try:
        root = Path(raw).resolve()
        return root if root.is_dir() else None
    except OSError:
        return None


def _round_workspace_code_tree() -> dict:
    """Build the shared visual-output tree shown once beside the round roots."""
    root = _round_workspace_code_root_for_session()
    tree = {
        'name': 'Code', 'path': '', 'type': 'dir', 'children': [],
        'visibility': 'research', 'source_kind': 'project-code',
    }
    if root is None:
        return tree

    budget = {'remaining': ROUND_WORKSPACE_TREE_MAX_NODES, 'truncated': False}

    def _curate(node: dict) -> dict | None:
        relative = _round_workspace_safe_relative(node.get('path', ''))
        if relative is None:
            return None
        target = root.joinpath(*relative.parts)
        if node.get('type') == 'dir':
            children = [
                curated for child in node.get('children', [])
                if (curated := _curate(child)) is not None
            ]
            if not children:
                return None
            node['children'] = children
            node['visibility'] = 'research'
        else:
            if not _round_workspace_external_file_allowed(target, root, relative):
                return None
            node['visibility'] = 'research'
            node['artifact_path'] = relative.as_posix()
        node['source_kind'] = 'project-code'
        return node

    for name in ('analysis_output', 'outputs'):
        path = root / name
        try:
            if not path.is_dir() or path.is_symlink():
                continue
        except OSError:
            continue
        curated = _curate(_round_workspace_tree_node(path, root, budget=budget))
        if curated is not None:
            tree['children'].append(curated)
    _round_workspace_sort_tree(tree)
    tree['truncated'] = budget['truncated']
    return tree


def _round_workspace_code_artifact_path(relative_path: str) -> Path | None:
    """Resolve a visual shared-code artifact without allowing traversal."""
    root = _round_workspace_code_root_for_session()
    relative = _round_workspace_safe_relative(relative_path)
    if root is None or relative is None or relative.parts[0] not in {'analysis_output', 'outputs'}:
        return None
    target = root.joinpath(*relative.parts)
    return target.resolve() if _round_workspace_external_file_allowed(target, root, relative) else None


@app.route('/api/round_workspace_tree', methods=['GET'])
def api_round_workspace_tree():
    """Recursive IDE-style file explorer for every round_* folder in the active project."""
    try:
        checkpoint_dir = _round_workspace_checkpoint_dir_for_session()
        if checkpoint_dir is None:
            return jsonify({'success': False, 'error': 'No active project'}), 404
        rounds_root = _v2_round_dirs_root_from_any_checkpoint_path(checkpoint_dir)
        active_index = _v2_current_studio_round_index(rounds_root, checkpoint_dir)
        round_dirs = list(_v2_iter_round_dirs(rounds_root))
        code_root = _round_workspace_code_root_for_session()
        tree_cache_key = (
            str(session.get('v2_active_project_slug') or 'active'),
            str(rounds_root.resolve()),
            active_index,
            tuple((index, _round_workspace_state_token(round_dir)) for index, round_dir in round_dirs),
            _round_workspace_state_token(code_root) if code_root is not None else (),
        )
        now = time.monotonic()
        with _ROUND_WORKSPACE_CACHE_LOCK:
            cached = _ROUND_WORKSPACE_TREE_CACHE.get(tree_cache_key)
            if cached and now - cached[0] <= ROUND_WORKSPACE_TREE_CACHE_TTL_SECS:
                return jsonify(copy.deepcopy(cached[1]))
        round_entries = []
        for index, round_dir in round_dirs:
            state_token = _round_workspace_state_token(round_dir)
            entry_cache_key = (
                tree_cache_key[0], str(round_dir.resolve()), index, state_token,
            )
            with _ROUND_WORKSPACE_CACHE_LOCK:
                cached_entry = _ROUND_WORKSPACE_ROUND_ENTRY_CACHE.get(entry_cache_key)
                entry_ttl = (
                    300
                    if cached_entry and cached_entry[1].get('status') == 'completed'
                    else ROUND_WORKSPACE_TREE_CACHE_TTL_SECS
                )
                entry = (
                    copy.deepcopy(cached_entry[1])
                    if cached_entry and now - cached_entry[0] <= entry_ttl
                    else None
                )
            if entry is None:
                registry = _round_workspace_artifact_registry(index)
                entry = _round_workspace_entry(index, round_dir, active_index, registry)
                with _ROUND_WORKSPACE_CACHE_LOCK:
                    _ROUND_WORKSPACE_ROUND_ENTRY_CACHE[entry_cache_key] = (
                        now, copy.deepcopy(entry),
                    )
                    if len(_ROUND_WORKSPACE_ROUND_ENTRY_CACHE) > 64:
                        oldest = min(
                            _ROUND_WORKSPACE_ROUND_ENTRY_CACHE,
                            key=lambda key: _ROUND_WORKSPACE_ROUND_ENTRY_CACHE[key][0],
                        )
                        _ROUND_WORKSPACE_ROUND_ENTRY_CACHE.pop(oldest, None)
            entry['is_current'] = index == active_index
            round_entries.append(entry)
        payload = {
            'success': True,
            'project_slug': session.get('v2_active_project_slug'),
            'current_round_index': active_index,
            'rounds': round_entries,
            'code_tree': _round_workspace_code_tree(),
            'initial_exploration': _v2_no_round_has_started(rounds_root),
        }
        with _ROUND_WORKSPACE_CACHE_LOCK:
            _ROUND_WORKSPACE_TREE_CACHE[tree_cache_key] = (now, copy.deepcopy(payload))
            if len(_ROUND_WORKSPACE_TREE_CACHE) > 16:
                oldest = min(_ROUND_WORKSPACE_TREE_CACHE, key=lambda key: _ROUND_WORKSPACE_TREE_CACHE[key][0])
                _ROUND_WORKSPACE_TREE_CACHE.pop(oldest, None)
        return jsonify(payload)
    except Exception as exc:  # pragma: no cover - defensive guard
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/round_workspace_artifact', methods=['GET'])
def api_round_workspace_artifact():
    """Serve a selected round artifact inline or as a download."""
    round_raw = request.args.get('round', '')
    if not round_raw.isdigit():
        abort(400)
    source_id = request.args.get('source', '')
    round_index = int(round_raw)
    relative_path = request.args.get('path', '')
    target = (
        _round_workspace_resolve_artifact(round_index, source_id, relative_path)
        if source_id
        else _round_workspace_artifact_path(round_index, relative_path)
    )
    if target is None:
        abort(404)
    try:
        if target.stat().st_size > 50 * 1024 * 1024:
            abort(413)
    except OSError:
        abort(404)
    return send_from_directory(target.parent, target.name)


@app.route('/api/round_workspace_code_artifact', methods=['GET'])
def api_round_workspace_code_artifact():
    """Serve an approved visual artifact from the active project's shared code outputs."""
    target = _round_workspace_code_artifact_path(request.args.get('path', ''))
    if target is None:
        abort(404)
    try:
        if target.stat().st_size > 50 * 1024 * 1024:
            abort(413)
    except OSError:
        abort(404)
    return send_from_directory(target.parent, target.name)


@app.route('/api/round_tree', methods=['GET'])
def api_round_tree():
    """Shallow filesystem tree of the latest round directory only. Stat metadata only, no file reads."""
    round_dir = _get_latest_round_dir_for_session()
    if not round_dir or not round_dir.exists():
        return jsonify({'success': False, 'error': 'No checkpoint directory configured'})

    project_cfg = _get_project_config()
    query_dirs = _list_query_dirs(round_dir)
    active_query = query_dirs[-1].name if query_dirs else None

    selected_context_path = _locate_selected_hypothesis_context(round_dir)
    if selected_context_path:
        selected_node = _file_node(selected_context_path)
        selected_node['name'] = 'selected_hypothesis.md'
        selected_node['source'] = (
            'active_round' if selected_context_path.parent == round_dir else 'previous_round'
        )
    else:
        # Missing selected_hypothesis.md is expected/non-fatal context here --
        # query_* dirs alone are sufficient evidence the round started.
        selected_node = {'name': 'selected_hypothesis.md', 'type': 'file', 'status': 'missing'}
    children = [selected_node]
    for fname in ("generated_hypotheses.json", "generated_hypotheses.md"):
        children.append(_file_node(round_dir / fname))

    has_round_report = (round_dir / "round_report.md").exists()
    log_text, _ = _read_log_tail(round_dir, max_bytes=20_000)
    query_statuses = _infer_query_statuses(query_dirs, has_round_report, log_text)

    # Captured once per query dir here and reused for the Round Constellation
    # below, so that visualization never recomputes (or re-triggers an LLM
    # call for) a summary the tree above already has.
    infos_by_name: dict[str, dict | None] = {}
    summaries_by_name: dict[str, str] = {}
    statuses_by_name: dict[str, str] = {}

    for qd in query_dirs:
        try:
            artifact_names = sorted(p.name for p in qd.iterdir())
        except OSError:
            artifact_names = []
        qd_children = []
        for name in artifact_names:
            qd_children.append(_file_node(qd / name))
        try:
            qd_mtime = qd.stat().st_mtime
        except OSError:
            qd_mtime = None

        query_summary_text = _get_query_summary(qd)

        # query_status.json is the primary source of truth when present
        # (new 'running'/'failed' statuses it can report are surfaced
        # directly); legacy query_NNN.md/log/mtime inference remains the
        # fallback for rounds written before this instrumentation existed.
        status_data = _read_query_status(qd)
        if status_data is not None:
            info = _summarize_query_status(status_data)
            node = {
                'name': qd.name,
                'type': 'dir',
                'status': info['status'],
                'description': query_summary_text,
                'current_step': info['current_step'],
                'error': info['error'],
                'last_modified': qd_mtime,
                'children': qd_children,
            }
        else:
            info = None
            node = {
                'name': qd.name,
                'type': 'dir',
                'status': query_statuses.get(qd.name, 'unknown'),
                'description': query_summary_text,
                'last_modified': qd_mtime,
                'children': qd_children,
            }
        children.append(node)
        infos_by_name[qd.name] = info
        summaries_by_name[qd.name] = query_summary_text
        statuses_by_name[qd.name] = node['status']

    children.append(_file_node(round_dir / "round_report.md"))
    memory_report_path = round_dir / "memory_report.md"
    if memory_report_path.exists():
        children.append(_file_node(memory_report_path))
    for log_name in ("live_round.log", "round.log"):
        children.append(_file_node(round_dir / log_name))

    summary = {'query_count': len(query_dirs)}
    if selected_context_path:
        try:
            with open(selected_context_path, 'r', encoding='utf-8', errors='replace') as f:
                raw_selected_text = f.read(20_000)
            display_text = _extract_hypothesis_display_text(raw_selected_text)
            if display_text:
                summary['selected_hypothesis_text'] = display_text
        except OSError:
            pass
    else:
        summary['selected_hypothesis_message'] = (
            'Selected hypothesis source not found, but query artifacts indicate this round has started.'
        )

    constellation = _build_round_constellation(
        round_dir, query_dirs, infos_by_name, summaries_by_name, statuses_by_name,
    )

    return jsonify({
        'success': True,
        'round_path': str(round_dir),
        'checkpoint_path': str(project_cfg.get('checkpoint_dir') or ''),
        'active_query': active_query,
        'summary': summary,
        'constellation': constellation,
        'root': {
            'name': round_dir.name,
            'type': 'dir',
            'status': 'active',
            'children': children,
        },
    })


def _find_round_log_path(round_dir: Path) -> Path | None:
    """Return live_round.log (preferred) or round.log if either exists, else None."""
    for candidate_name in ("live_round.log", "round.log"):
        candidate = round_dir / candidate_name
        if candidate.exists():
            return candidate
    return None


def _read_log_tail(round_dir: Path, max_bytes: int = 20_000) -> tuple[str | None, str | None]:
    """Read the last max_bytes of the round log, if one exists. Returns (text, log_file_name)."""
    log_path = _find_round_log_path(round_dir)
    if log_path is None:
        return None, None
    try:
        size = log_path.stat().st_size
        tail_bytes = min(size, max_bytes)
        with open(log_path, 'rb') as f:
            f.seek(max(0, size - tail_bytes))
            text = f.read().decode('utf-8', errors='replace')
        return text, log_path.name
    except OSError:
        return None, None


ROUND_ACTIVITY_LOG_MAX_BYTES = 100_000


def _round_activity_planned_query_count(round_dir: Path) -> int | None:
    """Read the runner's explicit planned query count without inventing one."""
    try:
        payload = json.loads((round_dir / 'config.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    config = payload.get('config') if isinstance(payload, dict) else None
    value = config.get('num_queries') if isinstance(config, dict) else None
    return value if isinstance(value, int) and value > 0 else None


def _round_activity_report_available(round_dir: Path, filename: str, round_status: str) -> bool:
    """Match Round Workspace's completed, nonempty, regular-file eligibility."""
    if round_status != 'completed':
        return False
    path = round_dir / filename
    try:
        return path.is_file() and not path.is_symlink() and path.stat().st_size > 0
    except OSError:
        return False


def _round_activity_dir_for_request() -> tuple[int | None, Path | None, str | None]:
    """Resolve the requested workspace round, falling back for legacy callers."""
    requested = request.args.get('round')
    if requested is None:
        round_dir = _get_latest_round_dir_for_session()
        return _v2_round_index(round_dir), round_dir, None
    if not requested.isdigit():
        return None, None, 'Invalid round'
    round_index = int(requested)
    checkpoint_dir = _round_workspace_checkpoint_dir_for_session()
    if checkpoint_dir is None:
        return round_index, None, 'No checkpoint directory configured'
    rounds_root = _v2_round_dirs_root_from_any_checkpoint_path(checkpoint_dir).resolve()
    round_dir = (rounds_root / f'round_{round_index}').resolve()
    try:
        if not round_dir.is_relative_to(rounds_root) or not round_dir.is_dir():
            return round_index, None, 'Round not found'
    except OSError:
        return round_index, None, 'Round not found'
    return round_index, round_dir, None


@app.route('/api/round_activity', methods=['GET'])
def api_round_activity():
    """
    Deterministic, cheap (no LLM call) extraction of high-level round events
    from the log tail, for the Activity tab. The log is never required to exist.

    Returns both:
      - 'events': the legacy flat, chronological {timestamp, message, query}
        list (unchanged) -- kept for any existing consumer of this route.
      - 'discovery_activity': the normalized, collapsible-friendly timeline
        (see _build_discovery_activity_timeline) -- one grouped row per
        query plus simple round-level rows. This is the primary structured
        query timeline going forward; 'events' remains the old Round
        Activity feed and is not the focus of this shape.
    """
    round_index, round_dir, round_error = _round_activity_dir_for_request()
    if not round_dir:
        return jsonify({'success': False, 'error': round_error or 'No checkpoint directory configured'})

    log_text, log_name = _read_log_tail(round_dir, max_bytes=ROUND_ACTIVITY_LOG_MAX_BYTES)
    # Build events regardless of whether a log file exists -- the
    # query-centric builder can produce a useful feed from query
    # directory/file mtimes and descriptions alone (see
    # _build_query_activity_events), which matters for any round where live
    # log monitoring wasn't available/started for that run.
    events = _extract_round_activity_events(log_text, round_dir=round_dir)
    discovery_activity = _build_discovery_activity_timeline(round_dir, log_text, log_name, events)
    round_info = _inspect_latest_round(round_dir)
    round_status = _v2_round_status(round_info)
    return jsonify({
        'success': True,
        'round_index': round_index,
        'round_status': round_status,
        'planned_query_count': _round_activity_planned_query_count(round_dir),
        'created_query_count': round_info['query_count'],
        'has_round_report': _round_activity_report_available(
            round_dir, 'round_report.md', round_status,
        ),
        'has_memory_report': _round_activity_report_available(
            round_dir, 'memory_report.md', round_status,
        ),
        'exists': log_text is not None,
        'events': events,
        'discovery_activity': discovery_activity,
        'log_file': log_name,
        'message': None if log_text is not None else (
            'No live log found yet; progress below is inferred from checkpoint/query files only.'
        ),
    })


def _read_capped(path: Path, max_chars: int) -> str | None:
    """Read up to max_chars of a text file, marking truncation. None if missing/unreadable."""
    if not path.exists():
        return None
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read(max_chars + 1)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[truncated]..."
        return text
    except OSError:
        return None


def _query_status_summary_lines(round_dir: Path, query_dirs: list[Path]) -> list[str]:
    """
    Shared 'query_NNN (status): description' lines for the Audit Agent
    Chat's queries-mode context, reusing the same inference used by
    /api/round_tree. Prefers query_status.json when present, including its
    current_step/artifacts_written/error fields (summarized, never a raw
    JSON dump) so the chat can explain query state without inventing
    anything beyond what's actually recorded.
    """
    if not query_dirs:
        return []
    has_round_report = (round_dir / "round_report.md").exists()
    log_text, _ = _read_log_tail(round_dir, max_bytes=20_000)
    statuses = _infer_query_statuses(query_dirs, has_round_report, log_text)
    lines = []
    for qd in query_dirs:
        status_data = _read_query_status(qd)
        if status_data is not None:
            info = _summarize_query_status(status_data)
            detail_bits = [info['status']]
            if info['current_step']:
                detail_bits.append(f"step={info['current_step']}")
            artifact_note = _summarize_artifacts_written(info['artifacts_written'])
            if artifact_note:
                detail_bits.append(artifact_note.rstrip('.'))
            if info['error']:
                detail_bits.append(f"error={info['error']}")
            lines.append(f"- {qd.name} ({', '.join(detail_bits)}): {info['description']}")
        else:
            status = statuses.get(qd.name, 'unknown')
            description = _infer_query_description(qd, log_text)
            lines.append(f"- {qd.name} ({status}): {description}")
    return lines


def _build_audit_checkpoint_inventory(generator: InteractiveHypothesisGenerator | None) -> str | None:
    """Return an authoritative, compact inventory of checkpoint rounds on disk.

    The Audit Agent can read the full filesystem through the code-chat tools,
    but its first answer was anchoring on the background report. This manifest
    gives it the actual discovered round_N directories before it decides what
    to inspect.
    """
    project_cfg = _get_project_config()
    raw_checkpoint_dir = (
        getattr(generator, 'checkpoint_dir', None)
        or project_cfg.get('checkpoint_dir')
        or _get_default_config_value('checkpoint_dir', CLI_CHECKPOINT_DIR)
    )
    if not raw_checkpoint_dir:
        return None

    try:
        checkpoint_path = Path(raw_checkpoint_dir).expanduser().resolve()
    except (OSError, RuntimeError):
        checkpoint_path = Path(raw_checkpoint_dir).expanduser()

    try:
        rounds_root = _v2_round_dirs_root_from_any_checkpoint_path(checkpoint_path)
        rounds = _v2_iter_round_dirs(rounds_root)
    except OSError:
        rounds = []
        rounds_root = checkpoint_path

    if not rounds:
        return (
            "## Authoritative checkpoint filesystem inventory\n"
            f"- Configured checkpoint path: {checkpoint_path}\n"
            f"- Resolved rounds root: {rounds_root}\n"
            "- Discovered numeric round directories: none\n"
            "Treat this inventory as authoritative over older background-report prose."
        )

    lines = [
        "## Authoritative checkpoint filesystem inventory",
        f"- Configured checkpoint path: {checkpoint_path}",
        f"- Resolved rounds root: {rounds_root}",
        f"- Latest discovered numeric round: round_{rounds[-1][0]}",
        "",
        "Discovered numeric round directories:",
    ]
    for index, round_dir in rounds:
        query_dirs = _list_query_dirs(round_dir)
        info = _inspect_latest_round(round_dir)
        artifacts = []
        if info.get('has_round_report'):
            artifacts.append("round_report.md")
        if info.get('has_memory_report'):
            artifacts.append("memory_report.md")
        if info.get('has_generated_hypotheses'):
            artifacts.append("generated_hypotheses.json")
        if info.get('has_selected_hypothesis'):
            artifacts.append("selected_hypothesis.md")
        artifacts_text = ", ".join(artifacts) if artifacts else "no top-level report/hypothesis artifacts"
        latest_query = query_dirs[-1].name if query_dirs else "none"
        lines.append(
            f"- round_{index}: {round_dir} "
            f"({len(query_dirs)} query dirs, latest query: {latest_query}; {artifacts_text})"
        )

    lines.extend([
        "",
        "When the user asks about rounds, treat this inventory as the source of truth.",
        "Do not infer the number of rounds only from the background report.",
        "For round summaries, inspect the relevant round_report.md files on disk, including the latest discovered round.",
    ])
    return "\n".join(lines)


def _round_chat_file_nav_inventory(
    round_index: int,
    round_dir: Path,
    viewer_file: dict | None = None,
) -> str:
    """Describe the exact research-visible File Nav set without reading files."""
    registry = _round_workspace_artifact_registry(round_index)
    entry = _round_workspace_entry(round_index, round_dir, round_index, registry)
    viewer_source = None
    viewer_path = None
    viewer_kind = None
    if isinstance(viewer_file, dict):
        viewer_source = viewer_file.get('source')
        viewer_kind = viewer_file.get('sourceKind') or viewer_file.get('source_kind')
        viewer_path = (
            viewer_file.get('artifactPath')
            or viewer_file.get('artifact_path')
            or viewer_file.get('path')
        )
    lines: list[str] = []

    def _walk(children: list[dict], label: str) -> None:
        for item in children:
            if item.get('visibility') != 'research':
                continue
            if item.get('type') == 'dir':
                _walk(item.get('children') or [], label)
                continue
            artifact_path = item.get('artifact_path') or item.get('path') or ''
            source_kind = item.get('source_kind') or 'checkpoint'
            is_open = (
                artifact_path == viewer_path
                and (
                    (source_kind == 'project-code' and viewer_kind == 'project-code')
                    or item.get('source') == viewer_source
                )
            )
            size = item.get('size')
            size_note = f", {size} bytes" if isinstance(size, int) else ""
            open_note = ", currently open" if is_open else ""
            lines.append(f"- {label}/{item.get('path') or item.get('name')} ({source_kind}{size_note}{open_note})")

    _walk((entry.get('tree') or {}).get('children') or [], f"Round {round_index}")
    _walk(_round_workspace_code_tree().get('children') or [], "Code")
    return (
        "## Authoritative File Nav inventory\n"
        + ("\n".join(lines) if lines else "No research-visible files.")
    )


def _round_chat_context_bundle(round_dir: Path, mode: str = 'queries') -> str:
    """
    Build a bounded, read-only text bundle from current round files for the
    Audit Agent Chat. Never reads the live backend's full message/tool
    traces unbounded -- truncates aggressively to keep token usage sane.

    mode='queries': scientific grounding (selected hypothesis, prior-round
    memory_report.md, query write-ups, active-round round/memory reports,
    query status summary), explicitly labeled PRIOR vs CURRENT-ROUND.
    mode='code': execution grounding (extracted activity events, log tail,
    query artifact file names, error/exception mentions) -- no hypothesis/
    report content.
    """
    sections: list[str] = []
    query_dirs = _list_query_dirs(round_dir)

    if mode == 'code':
        log_text, log_name = _read_log_tail(round_dir, max_bytes=20_000)
        if log_text:
            # Pre-extracted, already-deterministic high-level events (same
            # extraction /api/round_activity uses) so the model has a
            # summarized starting point instead of needing to parse the raw
            # tail itself -- this is the evidence it should lead with.
            activity_events = _extract_round_activity_events(log_text, round_dir=round_dir, max_events=60)
            if activity_events:
                event_lines = [
                    f"- {e['timestamp'] or '??:??:??'}: {e['message']}" for e in activity_events
                ]
                sections.append(f"## Extracted activity events (from {log_name})\n" + "\n".join(event_lines))
            sections.append(
                f"## Raw tail of {log_name} (for reference only -- summarize this, do not paste it back verbatim "
                "unless explicitly asked for raw log lines)\n" + log_text
            )
            error_lines = [
                line for line in log_text.splitlines()
                if '[ERROR]' in line or 'Traceback' in line or 'Exception' in line
            ]
            if error_lines:
                sections.append("## Error/exception mentions in log tail\n" + "\n".join(error_lines[-20:]))
        else:
            sections.append("## Live log\nNo live_round.log or round.log found yet for this round.")

        if query_dirs:
            lines = []
            for qd in query_dirs:
                try:
                    artifact_names = sorted(p.name for p in qd.iterdir())
                except OSError:
                    artifact_names = []
                lines.append(f"- {qd.name}/: {', '.join(artifact_names) if artifact_names else '(empty)'}")
            sections.append("## Query artifact files discovered (names only)\n" + "\n".join(lines))

            # query_status.json explains execution STATE (running/completed/
            # failed, current_step, error) -- summarized, not a raw JSON
            # dump, and intentionally excludes objective/summary here since
            # those are scientific content for 'queries' mode, not execution.
            status_lines = []
            for qd in query_dirs:
                status_data = _read_query_status(qd)
                if status_data is None:
                    continue
                info = _summarize_query_status(status_data)
                bits = [info['status']]
                if info['current_step']:
                    bits.append(f"step={info['current_step']}")
                if info['error']:
                    bits.append(f"error={info['error']}")
                status_lines.append(f"- {qd.name}: {', '.join(bits)}")
            if status_lines:
                sections.append("## Query execution state (from query_status.json)\n" + "\n".join(status_lines))
        else:
            sections.append("## Query artifact files discovered\nNone yet.")

        return "\n\n".join(sections)

    # mode == 'queries' (default): scientific/interpretive grounding.
    # Sections are explicitly labeled PRIOR vs CURRENT-ROUND so the model can
    # keep "what was already known before this round" separate from "what
    # this round itself has produced" rather than blending the two.
    selected_context_path = _locate_selected_hypothesis_context(round_dir)
    if selected_context_path:
        selected = _read_capped(selected_context_path, 6000)
        if selected:
            sections.append(f"## Selected Hypothesis / Framing (from {selected_context_path})\n{selected}")
    else:
        sections.append(
            "## Selected Hypothesis / Framing\n"
            "Selected hypothesis source not found, but query artifacts indicate this round has started. "
            "Base answers about the hypothesis/motivation on query artifacts only, and say so explicitly."
        )

    # PRIOR-ROUND context: memory_report.md normally only exists in the
    # *previous* round's folder while the active round is still in progress
    # (the active round gets its own memory_report.md only once it finishes
    # accumulating, which is rare to see here). This is the single most
    # important piece of "what's already established" context, so it gets a
    # generous cap.
    previous_round_dir = _previous_round_dir(round_dir)
    input_memory_report_path = previous_round_dir / "memory_report.md" if previous_round_dir else None
    if input_memory_report_path and input_memory_report_path.exists():
        prior_memory = _read_capped(input_memory_report_path, 12000)
        if prior_memory:
            sections.append(f"## PRIOR CONTEXT: memory_report.md from previous round (from {input_memory_report_path})\n{prior_memory}")
    else:
        sections.append(
            "## PRIOR CONTEXT: memory_report.md from previous round\n"
            "Not found (either this is round 0, or no previous-round memory_report.md exists on disk)."
        )

    generated = _read_capped(round_dir / "generated_hypotheses.json", 4000)
    if generated:
        sections.append(f"## generated_hypotheses.json\n{generated}")

    if query_dirs:
        sections.append(
            "## CURRENT-ROUND Query Status Summary (name, status, short description)\n"
            + "\n".join(_query_status_summary_lines(round_dir, query_dirs))
        )

        # Include small .md write-ups for the most recent few query dirs only.
        for qd in query_dirs[-3:]:
            for md_path in sorted(qd.glob("*.md")):
                content = _read_capped(md_path, 3000)
                if content:
                    sections.append(f"## CURRENT-ROUND {qd.name}/{md_path.name}\n{content}")
    else:
        sections.append("## CURRENT-ROUND Query Status Summary\nNone yet.")

    # ACTIVE-ROUND round_report.md/memory_report.md: rare while still in
    # progress (Round Monitor only shows while round_report.md is absent),
    # but included defensively in case this is called right at the boundary.
    # The explicit present/NOT YET WRITTEN status line (rather than just
    # omitting a section silently) is what the "Not Yet Available" heading in
    # the progress-summary instruction is meant to read from directly.
    active_round_report_path = round_dir / "round_report.md"
    active_round_memory_report_path = round_dir / "memory_report.md"
    sections.append(
        "## CURRENT-ROUND Report Status\n"
        f"- round_report.md: {'present' if active_round_report_path.exists() else 'NOT YET WRITTEN'}\n"
        f"- memory_report.md: {'present' if active_round_memory_report_path.exists() else 'NOT YET WRITTEN'}"
    )

    active_round_report = _read_capped(active_round_report_path, 6000)
    if active_round_report:
        sections.append(f"## CURRENT-ROUND round_report.md\n{active_round_report}")

    active_memory_report = _read_capped(active_round_memory_report_path, 6000)
    if active_memory_report:
        sections.append(f"## CURRENT-ROUND memory_report.md (from {active_round_memory_report_path})\n{active_memory_report}")

    return "\n\n".join(sections)


ROUND_CHAT_SYSTEM_PROMPT_QUERIES = """You are a read-only observer assistant for a scientist monitoring an in-progress \
research round being run by DiscoveryAgent. You can only see filesystem evidence captured below -- you have no live \
connection to the running DiscoveryAgent process and CANNOT steer, interrupt, pause, or modify it in any way.

Your job is to SYNTHESIZE the scientific state of the round, not to copy/paste raw file contents back to the \
scientist. The evidence below is explicitly labeled PRIOR CONTEXT (what was already established in previous rounds, \
mainly via the previous round's memory_report.md) versus CURRENT-ROUND (what this round itself has produced: query \
write-ups/status, and this round's own round_report.md/memory_report.md if they already exist, which is rare while \
a round is still in progress). Always keep these two separate in your answer -- never present prior-round findings \
as if this round discovered them.

Summarize in your own words with short bullets; do not quote long passages verbatim, and never paste an entire file. \
Cite the source naturally in prose (e.g. "From memory_report.md..." or "From query_004/query_004.md...") rather than \
dumping it. If a query directory exists but its write-up only contains boilerplate with no stated objective or \
results yet, say plainly that the objective/results haven't been written yet for that query -- do not guess what \
they might be.

Never invent findings, numbers, gene/mechanism names, or conclusions that are not present in the evidence below. If \
the evidence does not contain the answer, say so explicitly (e.g. "I don't see evidence of that yet in the current \
round files") rather than guessing or inferring beyond what is shown."""

ROUND_CHAT_SYSTEM_PROMPT_CODE = """You are a read-only observer assistant for a scientist monitoring an in-progress \
research round being run by DiscoveryAgent. You can only see filesystem evidence captured below -- you have no live \
connection to the running DiscoveryAgent process and CANNOT steer, interrupt, pause, or modify it in any way.

Your job is to SUMMARIZE runtime/execution activity, not to paste raw log text back to the scientist. The evidence \
below may include pre-extracted activity events, a raw log tail (for your reference only), query artifact \
filenames, and error/exception mentions. Describe what happened in terms of events: queries started/completed, \
checkpoints saved, execution agent runs, deep research / literature checks, errors, and files created. If the log \
is noisy or mostly boilerplate/system-prompt output, say so plainly and summarize only the meaningful events you can \
identify -- do not reproduce large blocks of raw terminal output, README content, or directory dumps.

Only quote a raw log line or two if the scientist explicitly asks to see the raw log; otherwise always summarize. \
Never invent events, \
files, or outcomes that are not present in the evidence below. If the evidence does not contain the answer, say so \
explicitly rather than guessing."""

ROUND_CHAT_SYSTEM_PROMPT_ALL = """You are the unified read-only Audit Agent for a scientist monitoring a DiscoveryAgent \
research round. Use both kinds of filesystem evidence provided below: scientific query/report evidence and runtime/code \
execution evidence. Infer which evidence matters from the scientist's question; do not make them choose a mode. Connect \
execution events to scientific artifacts when the files support that connection, but keep prior-round context distinct \
from current-round findings. Summarize rather than dumping raw files or logs. You have no live connection to the process \
and CANNOT steer, interrupt, pause, or modify it. Never invent findings, events, files, or conclusions; if the evidence \
does not answer the question, say so explicitly."""

_PROGRESS_SUMMARY_INTENT_RE = re.compile(
    r'\b('
    r'what\s+has\s+(?:been\s+)?(?:completed|done|found)'
    r'|summar(?:y|ize|ise)\s+(?:the\s+)?progress'
    r'|progress\s+(?:so\s+far|update|summary)'
    r'|where\s+are\s+we'
    r'|what\s+(?:is|was)\s+query'
    r'|what\s+has\s+discoveryagent\s+(?:done|found)'
    r'|what\s+is\s+(?:the\s+agent|discoveryagent)\s+doing'
    r'|what.{0,15}happening\s+in\s+the\s+terminal'
    r')\b',
    re.IGNORECASE,
)


def _is_progress_summary_intent(message: str) -> bool:
    """Lightweight intent check: does this question want a progress/status summary rather than a narrow lookup?"""
    return bool(_PROGRESS_SUMMARY_INTENT_RE.search(message or ''))


# Conservative regex: only fires when a number is adjacent to "query" or "q",
# so bare numbers in ordinary sentences are never treated as query references.
_QUERY_REFERENCE_RE = re.compile(
    r'\bquery[_ ]?(\d{1,3})\b|\bq(\d{1,3})\b',
    re.IGNORECASE,
)


def _detect_query_reference(message: str) -> str | None:
    """Return canonical zero-padded query name (e.g. 'query_012') if the message targets a specific query; else None."""
    m = _QUERY_REFERENCE_RE.search(message or '')
    if not m:
        return None
    num_str = m.group(1) or m.group(2)
    return f"query_{int(num_str):03d}"


def _resolve_query_dir(round_dir: Path, query_name: str) -> Path | None:
    """Find the actual directory for a canonical query name, tolerating zero-padding variations."""
    direct = round_dir / query_name
    if direct.is_dir():
        return direct
    try:
        num = int(query_name.split('_')[1])
    except (IndexError, ValueError):
        return None
    try:
        for d in round_dir.iterdir():
            if d.is_dir() and d.name.startswith('query_'):
                try:
                    if int(d.name.split('_')[1]) == num:
                        return d
                except (IndexError, ValueError):
                    pass
    except OSError:
        pass
    return None


# Budget-aware constants for targeted single-query context loading.
#
# Files at or below the per-file thresholds are included in full.
# Files above are smart-truncated (head + tail = 2 × _QUERY_TRUNC_HALF chars).
# The total budget caps the combined character count of all loaded content.
_QUERY_MD_THRESHOLD   = 150_000  # chars — include full MD file if at or below this
_QUERY_JSON_THRESHOLD =  25_000  # chars — include full JSON file if at or below this
_QUERY_TRUNC_HALF     =  40_000  # chars per half for oversized files (80 K total)
_QUERY_TOTAL_BUDGET   = 220_000  # chars — total cap across all sections in one request

# Filenames/suffixes to always skip in query dirs: raw execution traces and logs.
_QUERY_SKIP_JSON  = frozenset({'checkpoint.json'})
_QUERY_SKIP_NAMES = frozenset({'round.log', 'live_round.log'})
_QUERY_SKIP_SUFFIXES = frozenset({'.log', '.jsonl'})
_QUERY_RESULT_KEYWORDS = frozenset({
    'synthesis', 'report', 'completion', 'result', 'finding', 'conclusion',
})


def _load_query_specific_context(round_dir: Path, query_dir: Path) -> str:
    """Budget-aware, deep context bundle for a single query directory.

    File-selection rules (applied in priority order until the total budget
    _QUERY_TOTAL_BUDGET is reached):

      1. Short selected-hypothesis snippet from the round dir (≤ 2 K chars).
      2. Query status summary derived from query_status.json + log inference.
      3. MD files from the query dir top level, in this order:
           {qname}.md, query.md, report.md, summary.md, final.md
           → other .md files whose stem contains a result keyword
           → remaining .md files
      4. JSON files from the query dir top level, in this order:
           query_status.json, {qname}.json, query.json, metadata.json, status.json
           → other .json files not in _QUERY_SKIP_JSON
      Skip: checkpoint.json, *.log, *.jsonl, and all subdirectories.

    Each file is included in full when size ≤ threshold; otherwise smart-
    truncated to head + tail so both the objective and conclusions are present.
    Files that would push the total over _QUERY_TOTAL_BUDGET are listed by name
    so the model knows they exist but are not loaded.
    """
    parts: list[str] = []
    budget = _QUERY_TOTAL_BUDGET
    qname = query_dir.name

    def _charge(block: str) -> None:
        parts.append(block)
        nonlocal budget
        budget -= len(block)

    # --- 1. Short round anchor: selected hypothesis snippet ---
    hyp_path = _locate_selected_hypothesis_context(round_dir)
    if hyp_path:
        hyp_text = _read_capped(hyp_path, 2000)
        if hyp_text and hyp_text.strip():
            _charge(f"=== Round context: {hyp_path.name} (snippet) ===\n{hyp_text}")

    # --- 2. Query status summary (always small) ---
    status_lines = _query_status_summary_lines(round_dir, [query_dir])
    if status_lines:
        _charge("=== Query Status ===\n" + "\n".join(status_lines))

    # --- 3 & 4. Build ordered candidate file list ---
    seen: set[Path] = set()
    candidates: list[Path] = []

    def _add_if_new(p: Path) -> None:
        if p.is_file() and p not in seen:
            candidates.append(p)
            seen.add(p)

    # Priority MD names
    for name in [f"{qname}.md", "query.md", "report.md", "summary.md", "final.md"]:
        _add_if_new(query_dir / name)

    # Keyword-matched MD files, then all remaining MD files
    try:
        md_all = sorted(p for p in query_dir.iterdir() if p.is_file() and p.suffix == '.md')
    except OSError:
        md_all = []
    for p in md_all:
        if p not in seen and any(k in p.stem.lower() for k in _QUERY_RESULT_KEYWORDS):
            _add_if_new(p)
    for p in md_all:
        _add_if_new(p)

    # Priority JSON names (query_status first so objective/status appears early)
    for name in ["query_status.json", f"{qname}.json", "query.json", "metadata.json", "status.json"]:
        p = query_dir / name
        if p.name not in _QUERY_SKIP_JSON:
            _add_if_new(p)

    # Remaining top-level JSON files
    try:
        for p in sorted(query_dir.iterdir()):
            if p.is_file() and p.suffix == '.json' and p.name not in _QUERY_SKIP_JSON:
                _add_if_new(p)
    except OSError:
        pass

    # --- Load candidates within budget ---
    omitted: list[str] = []
    for fpath in candidates:
        if fpath.name in _QUERY_SKIP_NAMES or fpath.suffix in _QUERY_SKIP_SUFFIXES:
            omitted.append(f"{fpath.name} (skipped: raw log/trace)")
            continue

        is_json = fpath.suffix == '.json'
        threshold = _QUERY_JSON_THRESHOLD if is_json else _QUERY_MD_THRESHOLD

        try:
            raw = fpath.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue

        if is_json:
            try:
                import json as _json
                raw = _json.dumps(_json.loads(raw), indent=2, ensure_ascii=False)
            except Exception:
                pass

        if len(raw) <= threshold:
            content = raw
            header = f"=== {fpath.name} ==="
        else:
            half = _QUERY_TRUNC_HALF
            skipped = len(raw) - half * 2
            content = (
                raw[:half]
                + f"\n\n...[{skipped:,} chars from the middle omitted"
                  f" — objectives above, findings/conclusions below]...\n\n"
                + raw[-half:]
            )
            header = (
                f"=== {fpath.name}"
                f" (file is {len(raw):,} chars; showing first+last {half:,} chars) ==="
            )

        block = f"{header}\n{content}"
        if len(block) > budget:
            omitted.append(f"{fpath.name} ({len(raw):,} chars — omitted: total budget reached)")
            continue
        _charge(block)

    if omitted:
        _charge(
            "=== Files not loaded (budget reached or skipped) ===\n"
            + "\n".join(f"- {name}" for name in omitted)
            + "\nDo not infer findings from these files; use only the evidence loaded above."
        )

    return "\n\n".join(parts) if parts else f"No files found yet in {qname}."


_PROGRESS_SUMMARY_INSTRUCTION_QUERIES = """

The scientist is asking for a round progress summary. Structure your answer as Markdown, using only the headings \
below that are actually relevant to their question (omit any that don't apply), in this order:

## Round Progress Summary
### Selected Hypothesis
### Prior Context
### Completed Current-Round Queries
### Current In-Progress Query
### Key Findings So Far
### Not Yet Available

Use short bullets under each heading. Summarize -- do not quote large passages verbatim. Do not invent findings not \
present in the evidence above; list anything unknown or not yet written under "Not Yet Available" rather than \
guessing."""

_PROGRESS_SUMMARY_INSTRUCTION_CODE = """

The scientist is asking for a runtime/execution progress summary. Respond with a concise, summarized account of \
activity (query starts/completions, checkpoints saved, execution agent runs, deep research/literature checks, \
errors, files created), grounded only in the evidence above. Do not paste raw log lines unless the scientist \
explicitly asked for raw log text; if they did not, summarize instead."""


@app.route('/api/round_feedback', methods=['GET'])
def api_round_feedback_get():
    """
    Return existing scientist feedback for the latest completed round.

    Response fields:
      feedback      – saved feedback text, or null
      saved_path    – absolute path of scientist_feedback.md, or null
      memory_injected – true when memory_report.md contains the feedback section
      round_dir     – name of the resolved round directory
    """

    generator = get_or_restore_generator()
    if not generator:
        return jsonify({'success': False, 'error': 'No active session'})

    round_dir = _resolve_latest_completed_round_dir(generator)
    if round_dir is None:
        return jsonify({'success': True, 'feedback': None, 'saved_path': None,
                        'memory_injected': False, 'round_dir': None,
                        'message': 'No completed round report found'})

    feedback_path = round_dir / _SCIENTIST_FEEDBACK_FILE
    feedback_text: str | None = None
    if feedback_path.exists():
        try:
            feedback_text = feedback_path.read_text(encoding='utf-8').strip() or None
        except OSError:
            pass

    memory_injected = False
    memory_path = round_dir / "memory_report.md"
    if memory_path.exists():
        try:
            start_marker, _ = _feedback_markers(round_dir.name)
            memory_injected = start_marker in memory_path.read_text(encoding='utf-8')
        except OSError:
            pass

    return jsonify({
        'success': True,
        'feedback': feedback_text,
        'saved_path': str(feedback_path) if feedback_text else None,
        'memory_injected': memory_injected,
        'round_dir': round_dir.name,
    })


@app.route('/api/round_feedback', methods=['POST'])
def api_round_feedback_post():
    """
    Save scientist feedback for the latest completed round.

    Writes/updates:
      round_N/scientist_feedback.md  – human-readable artifact
      round_N/memory_report.md       – idempotent marked section injected when present

    Request JSON:
      feedback  – feedback text (empty string clears)

    Response:
      saved_path        – path of scientist_feedback.md
      memory_injected   – whether memory_report.md was updated
      round_dir         – name of resolved round directory
    """

    generator = get_or_restore_generator()
    if not generator:
        return jsonify({'success': False, 'error': 'No active session'})

    round_dir = _resolve_latest_completed_round_dir(generator)
    if round_dir is None:
        return jsonify({
            'success': False,
            'error': 'No completed round found (round_report.md and memory_report.md are both required).',
        })

    data = request.json or {}
    feedback_text = (data.get('feedback') or '').strip()

    feedback_path = round_dir / _SCIENTIST_FEEDBACK_FILE
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if not feedback_text:
        # Empty save → remove the file and the injected section
        try:
            if feedback_path.exists():
                feedback_path.unlink()
        except OSError as exc:
            return jsonify({'success': False, 'error': f'Could not clear feedback file: {exc}'})
        with _MEMORY_REPORT_LOCK:
            _remove_feedback_from_memory_report(round_dir)
        return jsonify({
            'success': True,
            'saved_path': None,
            'memory_injected': False,
            'round_dir': round_dir.name,
            'message': 'Feedback cleared',
        })

    # Build the scientist_feedback.md artifact
    round_label = round_dir.name.replace('_', ' ').title()
    file_content = (
        f"# Scientist Feedback on {round_label}\n\n"
        f"**Saved:** {timestamp}\n"
        f"**Purpose:** Human critique to be appended to next-round memory/context.\n\n"
        f"{feedback_text}\n"
    )
    try:
        feedback_path.write_text(file_content, encoding='utf-8')
    except OSError as exc:
        return jsonify({'success': False, 'error': f'Could not write feedback file: {exc}'})

    # Build the section to inject into memory_report.md
    feedback_section = (
        f"## Scientist Feedback on Previous Round\n\n"
        f"*Saved by scientist at {timestamp}. "
        f"This critique should inform the next round's memory and context.*\n\n"
        f"{feedback_text}"
    )
    with _MEMORY_REPORT_LOCK:
        memory_injected = _inject_feedback_into_memory_report(round_dir, feedback_section)

    print(f"Scientist feedback saved: {feedback_path} (memory_injected={memory_injected})")
    return jsonify({
        'success': True,
        'saved_path': str(feedback_path),
        'memory_injected': memory_injected,
        'round_dir': round_dir.name,
        'timestamp': timestamp,
    })


@app.route('/api/round_feedback', methods=['DELETE'])
def api_round_feedback_delete():
    """Clear scientist feedback for the latest completed round."""

    generator = get_or_restore_generator()
    if not generator:
        return jsonify({'success': False, 'error': 'No active session'})

    round_dir = _resolve_latest_completed_round_dir(generator)
    if round_dir is None:
        return jsonify({'success': True, 'message': 'No completed round to clear'})

    feedback_path = round_dir / _SCIENTIST_FEEDBACK_FILE
    try:
        if feedback_path.exists():
            feedback_path.unlink()
    except OSError as exc:
        return jsonify({'success': False, 'error': f'Could not delete feedback file: {exc}'})

    with _MEMORY_REPORT_LOCK:
        _remove_feedback_from_memory_report(round_dir)
    return jsonify({'success': True, 'round_dir': round_dir.name})


@app.route('/api/round_chat', methods=['POST'])
def api_round_chat():
    """Read-only Audit Agent Chat: answers questions using only current round filesystem evidence."""

    data = request.json or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({'success': False, 'error': 'Message is required'})

    mode = (data.get('mode') or 'queries').strip().lower()
    if mode not in ('queries', 'code', 'all'):
        mode = 'all'

    round_index, round_dir, round_error = _round_activity_dir_for_request()
    if not round_dir or not round_dir.exists():
        return jsonify({'success': False, 'error': round_error or 'No checkpoint directory configured'})

    try:
        import anthropic

        file_nav_inventory = _round_chat_file_nav_inventory(
            round_index,
            round_dir,
            data.get('viewer_file'),
        )

        # In queries mode, check if the message targets a specific query.
        query_name = _detect_query_reference(message) if mode in ('queries', 'all') else None
        query_dir = _resolve_query_dir(round_dir, query_name) if query_name else None

        if query_name and not query_dir:
            # Named query doesn't exist yet — tell the user what's available.
            try:
                available = ', '.join(
                    sorted(d.name for d in round_dir.iterdir()
                           if d.is_dir() and d.name.startswith('query_'))
                ) or 'none yet'
            except OSError:
                available = 'unknown'
            return jsonify({
                'success': True,
                'answer': f"I don't see a directory for **{query_name}** in this round. "
                          f"Available queries: {available}.",
                'mode': mode,
            })

        if query_dir:
            # Targeted single-query answer — only load that query's files.
            context_bundle = _load_query_specific_context(round_dir, query_dir)
            system_prompt = ROUND_CHAT_SYSTEM_PROMPT_ALL if mode == 'all' else ROUND_CHAT_SYSTEM_PROMPT_QUERIES
            progress_intent = False
            user_content = (
                f"The user asked about {query_dir.name}. "
                f"You have been given the contents of {query_dir.name}'s folder, "
                f"prioritized by relevance. "
                f"Use the query-specific files as primary evidence. "
                f"Do not summarize the whole round unless the user asks for that. "
                f"Files are included in full when they fit within the budget; "
                f"any file that was truncated is explicitly labeled as such — "
                f"do not claim content is missing or truncated unless the label says so.\n\n"
                f"Query folder contents for {query_dir.name} (from {round_dir}):\n\n{context_bundle}\n\n"
                f"{file_nav_inventory}\n\n"
                "The File Nav inventory confirms which research artifacts exist and which one "
                "the scientist currently has open. It does not provide the pixels of image files; "
                "use captions and textual references in the supplied evidence when discussing them.\n\n"
                f"Scientist's question: {message}\n\n"
                f"Answer specifically about {query_dir.name}: its objective, approach, findings, "
                f"key results, limitations, and conclusions. "
                f"Do not summarize other queries unless directly asked."
            )
        else:
            # Broad question — use the full context bundle.
            if mode == 'all':
                context_bundle = (
                    "# Scientific queries and reports\n\n"
                    + _round_chat_context_bundle(round_dir, mode='queries')
                    + "\n\n# Runtime and code execution\n\n"
                    + _round_chat_context_bundle(round_dir, mode='code')
                )
                system_prompt = ROUND_CHAT_SYSTEM_PROMPT_ALL
            else:
                context_bundle = _round_chat_context_bundle(round_dir, mode=mode)
                system_prompt = ROUND_CHAT_SYSTEM_PROMPT_CODE if mode == 'code' else ROUND_CHAT_SYSTEM_PROMPT_QUERIES
            progress_intent = _is_progress_summary_intent(message)
            user_content = (
                f"Current round evidence (from {round_dir}):\n\n{context_bundle}\n\n"
                f"{file_nav_inventory}\n\n"
                "The File Nav inventory confirms which research artifacts exist and which one "
                "the scientist currently has open. It does not provide the pixels of image files; "
                "use captions and textual references in the supplied evidence when discussing them.\n\n"
                f"Scientist's question: {message}"
            )
            if progress_intent:
                user_content += (
                    _PROGRESS_SUMMARY_INSTRUCTION_CODE if mode == 'code' else _PROGRESS_SUMMARY_INSTRUCTION_QUERIES
                )

        client = anthropic.Anthropic()
        request_kwargs = dict(
            model=_resolve_code_chat_model(get_generator()),
            max_tokens=4096,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_content}],
        )
        try:
            response = client.messages.create(**request_kwargs)
        except anthropic.APIStatusError as exc:
            # Transient server-side failures (rate limit / overloaded) get one
            # retry after a short pause instead of surfacing as a chat error.
            if exc.status_code not in (429, 500, 503, 529):
                raise
            time.sleep(2)
            response = client.messages.create(**request_kwargs)
        answer = "".join(
            block.text for block in response.content if getattr(block, 'type', None) == 'text'
        )
        stop_reason = getattr(response, 'stop_reason', None)
        if stop_reason == 'max_tokens':
            answer += '\n\n*(Response hit the output limit. Ask me to continue for the rest.)*'
        log_event('round_chat', {
            'round_path': str(round_dir), 'mode': mode,
            'progress_intent': progress_intent, 'query_target': query_name,
            'stop_reason': stop_reason,
        })
        return jsonify({'success': True, 'answer': answer, 'mode': mode})
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(exc)})


@app.route('/background_report')
def background_report():
    """Render the research paper markdown (if available) on a dedicated page."""
    if _is_audit_only_mode():
        return redirect(url_for('app_interface'))
    generator = get_generator()
    if not generator:
        return redirect(url_for('app_interface'))

    report_html = None
    error_message = None
    research_path = getattr(generator, 'research_paper_path', None)
    if research_path and not isinstance(research_path, Path):
        research_path = Path(research_path)

    if research_path and research_path.exists():
        try:
            raw_markdown = research_path.read_text(encoding='utf-8')
            report_html = render_report_html(raw_markdown, generator)
        except Exception as exc:
            error_message = f"Failed to load research_paper.md: {exc}"
    else:
        error_message = "research_paper.md is not available for this session."

    return render_template(
        'background_report.html',
        report_html=Markup(report_html) if report_html else None,
        report_available=bool(report_html),
        error_message=error_message,
        report_path=str(research_path) if research_path else None,
        last_updated=datetime.fromtimestamp(research_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        if research_path and research_path.exists()
        else None,
        code_chat_available=_is_code_chat_available(_resolve_code_dir(generator)),
        chat_storage_scope=_session_debug_id(),
    )


@app.route('/background_report/assets/<path:filename>')
def background_report_asset(filename):
    """Serve assets (images) referenced by the research paper."""

    checkpoint_dir = _resolve_report_asset_base_dir('checkpoint')
    if not checkpoint_dir:
        abort(404)

    images_root = (checkpoint_dir / "images").resolve()
    if not images_root.exists():
        abort(404)

    target_path = (images_root / filename).resolve()
    if not _path_within_root(target_path, images_root) or not target_path.exists():
        abort(404)

    return send_from_directory(images_root, filename)


@app.route('/api/serve_asset')
def api_serve_asset():
    """Serve a figure/image from within the project working directory.

    Security model:
    - Resolves the requested path (following all symlinks) to its canonical
      absolute form, then verifies it is strictly contained within one of the
      allowed project roots using Path.relative_to() — immune to the
      startswith-prefix confusion attack (e.g. /proj vs /proj_evil).
    - Only the extensions in ALLOWED_FIGURE_EXTENSIONS are accepted (never
      serves .py, .json, .env, or any other non-image file).
    - Files larger than 20 MB are rejected.
    """

    raw_path = (request.args.get('path') or '').strip()
    if not raw_path or len(raw_path) > 4096:
        abort(400)

    # Build allowed roots from the active session / generator.
    # Uses Path.relative_to() so "/project" cannot be spoofed by "/project_evil".
    allowed_roots: list[Path] = []
    generator = get_generator()
    project_cfg = _get_project_config()

    code_dir = (
        getattr(generator, 'code_dir', None)
        or project_cfg.get('code_dir')
        or CLI_CODE_DIR
    )
    if code_dir:
        try:
            code_path = Path(code_dir).expanduser().resolve()
            # working_dir (parent of code/) covers both code/ and checkpoint/ siblings.
            allowed_roots.append(code_path.parent)
            allowed_roots.append(code_path)
        except Exception:
            pass

    checkpoint_dir = (
        getattr(generator, 'checkpoint_dir', None)
        or project_cfg.get('checkpoint_dir')
        or CLI_CHECKPOINT_DIR
    )
    if checkpoint_dir:
        try:
            cp_path = Path(checkpoint_dir).expanduser().resolve()
            allowed_roots.append(cp_path)
            # Include the parent too, since the agent sees the full checkpoints tree.
            allowed_roots.append(cp_path.parent)
        except Exception:
            pass

    if not allowed_roots:
        abort(403)

    def _is_inside(candidate: Path, root: Path) -> bool:
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            return False

    raw_candidate = Path(raw_path).expanduser()
    candidate_paths: list[Path] = []
    if raw_candidate.is_absolute():
        try:
            candidate_paths.append(raw_candidate.resolve())
        except Exception:
            abort(400)
    else:
        search_roots: list[Path] = []
        code_dir_value = (
            getattr(generator, 'code_dir', None)
            or project_cfg.get('code_dir')
            or CLI_CODE_DIR
        )
        checkpoint_dir_value = (
            getattr(generator, 'checkpoint_dir', None)
            or project_cfg.get('checkpoint_dir')
            or CLI_CHECKPOINT_DIR
        )
        for root_value in (code_dir_value, checkpoint_dir_value):
            if not root_value:
                continue
            try:
                search_roots.append(Path(root_value).expanduser().resolve())
            except Exception:
                continue
        for root in allowed_roots + search_roots:
            try:
                candidate_paths.append((root / raw_candidate).resolve())
            except Exception:
                continue
        try:
            candidate_paths.append(raw_candidate.resolve())
        except Exception:
            pass

    target = next(
        (
            candidate
            for candidate in candidate_paths
            if candidate.suffix.lower() in ALLOWED_FIGURE_EXTENSIONS
            and candidate.is_file()
            and any(_is_inside(candidate, root) for root in allowed_roots)
        ),
        None,
    )
    if target is None:
        abort(404)

    try:
        if target.stat().st_size > 20 * 1024 * 1024:
            abort(413)
    except Exception:
        abort(404)

    if not any(_is_inside(target, root) for root in allowed_roots):
        abort(403)

    return send_from_directory(target.parent, target.name)


@app.route('/research_paper/pdf')
def research_paper_pdf():
    """Serve the latest research paper PDF if available."""
    generator = get_or_restore_generator()
    project_cfg = _get_project_config()
    checkpoint_dir = project_cfg.get('checkpoint_dir') or _get_default_config_value('checkpoint_dir', CLI_CHECKPOINT_DIR)
    pdf_path = _resolve_research_paper_pdf_path(
        generator,
        checkpoint_dir,
        _get_configured_research_paper_path(project_cfg),
    )
    if not pdf_path or not pdf_path.exists():
        abort(404)
    return send_from_directory(pdf_path.parent, pdf_path.name)


@app.route('/report')
def unified_report():
    """
    Unified report viewer with summary/full toggle.

    Query params:
    - type: 'background' | 'round' (default: 'background')
    - view: 'summary' | 'full' (default: 'summary')
    """
    report_type = request.args.get('type', 'background')
    view = request.args.get('view', 'summary')
    return _render_unified_report(report_type, view)


def _render_unified_report(report_type: str, view: str):
    """Shared body of the unified report viewer, callable with explicit
    params (not just via /report's query string) so the canonical
    /projects/<id>/rounds/<n> route can render the latest completed round's
    report directly, without duplicating this rendering logic."""
    print(f"[/report] Request received. session_id={session.get('session_id')}")
    generator = get_or_restore_generator()
    resolved_code_dir = _resolve_code_dir(generator)
    print(f"[/report] code_dir resolved to: {resolved_code_dir} (exists={_is_code_chat_available(resolved_code_dir)})")
    if not generator:
        # Generator missing and couldn't be restored - redirect to workspace
        print("[/report] No generator available, redirecting to workspace")
        if _is_audit_mode():
            sync_payload = _compute_s3_sync_status()
            sync_state = sync_payload.get('state')
            sync_error = sync_payload.get('error')
            if sync_payload.get('required'):
                state = sync_payload.get('state')
                if state in ('running', 'pending'):
                    error_message = "Report data is still syncing from S3. Please wait a moment and try again."
                elif state == 'error':
                    error_message = f"Report data sync failed: {sync_payload.get('error') or 'Unknown error'}"
                else:
                    error_message = "Report data is not available for this session yet. Please try again shortly."
            else:
                error_message = "Report data is not available for this session yet. Please try again shortly."
            return render_template(
                'report.html',
                report_available=False,
                error_message=error_message,
                title="Background Report",
                report_type="background",
                current_view="summary",
                has_summary=False,
                has_full=False,
                n_rounds=0,
                code_chat_available=False,
                audit_mode=True,
                audit_only=_is_audit_only_mode(),
                sync_state=sync_state,
                sync_error=sync_error,
                chat_storage_scope=_session_debug_id(),
            )
        return redirect(url_for('app_interface'))

    if _is_audit_mode():
        report_type = 'background'
    if _is_audit_only_mode():
        report_type = 'background'

    # Calculate number of completed rounds from checkpoint path
    n_rounds = _get_completed_rounds_count(generator.checkpoint_dir)

    # Scientist feedback needs a round dir with both round_report.md and
    # memory_report.md (matching _resolve_latest_completed_round_dir).
    feedback_available = False

    # Determine which content to show based on type and view
    if report_type == 'background':
        summary_content = getattr(generator, 'memory_report_summary', None)
        full_content = getattr(generator, 'memory_report', None)
        title = "Background Report"
    else:  # round — always resolve from the latest completed round on disk
        _full, _summary, _report_dir = _load_latest_round_report_content(
            Path(generator.checkpoint_dir)
        )
        if _report_dir is not None:
            print(f"Round Report: using latest completed round report: {_report_dir / 'round_report.md'}")
            feedback_available = (_report_dir / "memory_report.md").exists()
        else:
            print("Round Report: no completed round_report.md found; falling back to session round")
        full_content = _full or getattr(generator, 'round_report', None)
        summary_content = _summary or getattr(generator, 'round_report_summary', None)
        title = "Round Report"

    has_summary = bool(summary_content)
    has_full = bool(full_content)

    # Fallback logic for initial view
    if view == 'summary' and not has_summary:
        view = 'full'
    if view == 'full' and not has_full:
        view = 'summary'

    # Check if any content is available
    if not summary_content and not full_content:
        return render_template(
            'report.html',
            report_available=False,
            error_message=f"No {report_type} report available.",
            title=title,
            report_type=report_type,
            current_view=view,
            has_summary=has_summary,
            has_full=has_full,
            n_rounds=n_rounds,
            code_chat_available=_is_code_chat_available(_resolve_code_dir(generator)),
            audit_mode=_is_audit_mode(),
            audit_only=_is_audit_only_mode(),
            chat_storage_scope=_session_debug_id(),
        )

    # Render only the requested view to avoid loading both large reports into memory
    if view == 'summary':
        summary_html = render_report_html(summary_content, generator) if summary_content else None
        full_html = None
    else:
        summary_html = None
        full_html = render_report_html(full_content, generator) if full_content else None

    return render_template(
        'report.html',
        summary_html=Markup(summary_html) if summary_html else None,
        full_html=Markup(full_html) if full_html else None,
        report_available=True,
        report_type=report_type,
        current_view=view,
        has_summary=has_summary,
        has_full=has_full,
        has_round_report=getattr(generator, 'round_report', None) is not None or getattr(generator, 'round_report_summary', None) is not None,
        title=title,
        n_rounds=n_rounds,
        feedback_available=feedback_available,
        code_chat_available=_is_code_chat_available(_resolve_code_dir(generator)),
        audit_mode=_is_audit_mode(),
        audit_only=_is_audit_only_mode(),
        chat_storage_scope=_session_debug_id(),
    )


@app.route('/report_assets/<root>/<path:filename>')
def report_asset(root, filename):
    """Serve report assets from the checkpoint or code directories."""

    base_dir = _resolve_report_asset_base_dir(root)
    if root not in ('checkpoint', 'code'):
        abort(404)

    if not base_dir or not base_dir.exists():
        abort(404)

    target_path = (base_dir / filename).resolve()
    if not _path_within_root(target_path, base_dir) or not target_path.exists():
        abort(404)

    return send_from_directory(base_dir, filename)


@app.route('/download_report', methods=['GET'])
def download_report():
    """Download a report view as markdown."""
    generator = get_or_restore_generator()
    if not generator:
        abort(404)

    report_type = request.args.get('type', 'background')
    if _is_audit_only_mode():
        report_type = 'background'
    elif report_type not in ('background', 'round'):
        report_type = 'background'
    view = request.args.get('view', 'full')
    report_text, _, resolved_view = _get_report_text_for_chat(generator, report_type, view)

    if not report_text and getattr(generator, 'checkpoint_dir', None):
        checkpoint_dir = Path(generator.checkpoint_dir)
        if report_type == 'round':
            # Use the latest round dir so downloads stay current when the
            # session checkpoint points to an older round.
            _latest_dir = _latest_round_checkpoint_dir(
                checkpoint_dir.parent.parent if checkpoint_dir.name.startswith("round_") else checkpoint_dir.parent
            )
            _round_dir = _latest_dir if (_latest_dir / "round_report.md").exists() else checkpoint_dir
            candidate_file = _round_dir / (
                'round_report_summary.md' if resolved_view == 'summary' else 'round_report.md'
            )
        else:
            candidate_file = checkpoint_dir / (
                'memory_report_summary.md' if resolved_view == 'summary' else 'memory_report.md'
            )
        if candidate_file.exists():
            report_text = _load_text_file(candidate_file)

    if not report_text:
        abort(404)

    base_name = "round_report" if report_type == "round" else "memory_report"
    filename = f"{base_name}_summary.md" if resolved_view == "summary" else f"{base_name}.md"

    response = make_response(str(report_text))
    response.headers['Content-Type'] = 'text/markdown; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename=\"{filename}\"'
    return response


def _build_report_context(generator: InteractiveHypothesisGenerator) -> dict:
    """Collect context strings for the report chat."""
    research_text = ""
    if getattr(generator, "research_paper_path", None):
        path = generator.research_paper_path
        if path and not isinstance(path, Path):
            path = Path(path)
        if path and path.exists():
            research_text = _load_text_file(path)

    background_text = generator._build_combined_background() if hasattr(generator, "_build_combined_background") else ""
    return {
        "research_paper": research_text,
        "background": background_text or "",
    }


def _get_report_text_for_chat(
    generator: InteractiveHypothesisGenerator,
    report_type: str,
    view: str,
) -> tuple[str | None, str, str]:
    """Resolve the report text + label for report chat context."""
    report_type = (report_type or "background").lower()
    view = (view or "summary").lower()

    if report_type == "round":
        # Always resolve from the latest completed round on disk so chat
        # context stays current when the session checkpoint lags behind.
        full_content = None
        summary_content = None
        checkpoint_dir = getattr(generator, 'checkpoint_dir', None)
        if checkpoint_dir:
            full_content, summary_content, _ = _load_latest_round_report_content(
                Path(checkpoint_dir)
            )
        # Fallback to generator attributes if disk lookup found nothing
        if not full_content:
            full_content = getattr(generator, 'round_report', None)
        if not summary_content:
            summary_content = getattr(generator, 'round_report_summary', None)
        title = "Round Report"
    else:
        summary_content = getattr(generator, "memory_report_summary", None)
        full_content = getattr(generator, "memory_report", None)
        title = "Background Report"

    resolved_view = view
    if view == "summary" and not summary_content:
        resolved_view = "full"
    if view == "full" and not full_content:
        resolved_view = "summary"

    content = summary_content if resolved_view == "summary" else full_content
    return content, title, resolved_view


def _normalize_report_type_for_chat(report_type: str | None) -> str:
    """Normalize report chat type to the supported set."""
    return "round" if (report_type or "").lower() == "round" else "background"


def _report_chat_context_key(report_type: str | None) -> str:
    """Build the stable context key used across report summary/full views."""
    normalized_type = _normalize_report_type_for_chat(report_type)
    return f"{normalized_type}:shared"


def _get_persistent_report_text_for_chat(
    generator: InteractiveHypothesisGenerator,
    report_type: str | None,
) -> tuple[str | None, str, str]:
    """
    Resolve report text for persistent report chat context.

    We always prefer the full report so summary/full page switches share one context.
    """
    normalized_type = _normalize_report_type_for_chat(report_type)
    return _get_report_text_for_chat(generator, normalized_type, "full")


def _sanitize_restored_chat_message(message: dict) -> dict | None:
    """Normalize one client-provided chat turn before restoring it."""
    if not isinstance(message, dict):
        return None

    role = sanitize_field(str(message.get('role') or ''), 16).lower()
    # Only compact conversation turns; skip system/status messages.
    if role not in {'user', 'assistant'}:
        return None

    content = sanitize_field(str(message.get('content') or ''), CHAT_RESTORE_MESSAGE_MAX_CHARS)
    if not content:
        return None

    return {'role': role, 'content': content}


def _sanitize_restored_chat_messages(messages) -> list[dict]:
    """Normalize a restored message list and enforce conservative size caps."""
    if not isinstance(messages, list):
        return []

    sanitized = []
    for raw in messages:
        msg = _sanitize_restored_chat_message(raw)
        if not msg:
            continue
        sanitized.append(msg)

    if len(sanitized) > CHAT_RESTORE_MAX_MESSAGES:
        sanitized = sanitized[-CHAT_RESTORE_MAX_MESSAGES:]

    compacted = []
    used_chars = 0
    for msg in reversed(sanitized):
        msg_chars = len(msg.get('content', ''))
        if used_chars + msg_chars > CHAT_RESTORE_MAX_CHARS:
            break
        compacted.append(msg)
        used_chars += msg_chars

    compacted.reverse()
    return compacted


def _stringify_chat_content(value) -> str:
    """Normalize chat content to a string without truncation for telemetry snapshots."""
    if isinstance(value, str):
        return value
    if value is None:
        return ''
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _full_chat_messages_for_logging(messages) -> list[dict]:
    """Build a full chat message list suitable for durable telemetry exports."""
    if not isinstance(messages, list):
        return []
    normalized = []
    for raw in messages:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get('role') or 'unknown').strip().lower() or 'unknown'
        normalized.append({
            'role': role,
            'content': _stringify_chat_content(raw.get('content')),
        })
    return normalized


def _get_report_conversation_snapshot(generator: InteractiveHypothesisGenerator | None, report_key: str) -> list[dict]:
    if not generator:
        return []
    conversations = getattr(generator, 'report_code_chat_conversations', None)
    if not isinstance(conversations, dict):
        return []
    conversation = conversations.get(report_key)
    if not conversation:
        return []
    messages = getattr(conversation, 'messages', None)
    return _full_chat_messages_for_logging(messages)


def _get_hypothesis_conversation_snapshot(generator: InteractiveHypothesisGenerator | None) -> list[dict]:
    if not generator:
        return []
    agent = getattr(generator, 'hypothesis_gen_agent', None)
    messages = getattr(agent, 'messages', None)
    return _full_chat_messages_for_logging(messages)


def _extract_relevant_snippets(question: str, texts: dict, max_snippets: int = 4) -> str:
    """
    Naive keyword-based retrieval over the report + background.

    This keeps us deterministic and offline while still surfacing helpful excerpts.
    """
    if not question:
        return "Ask a question about the report or background."

    import re

    def tokenize(text):
        return [t.lower() for t in re.findall(r"[a-zA-Z]{3,}", text)]

    question_terms = set(tokenize(question))
    if not question_terms:
        question_terms = set(tokenize(question.lower()))

    candidates = []
    for label, blob in texts.items():
        if not blob:
            continue
        paragraphs = [p.strip() for p in blob.split("\n\n") if p.strip()]
        for para in paragraphs:
            para_terms = set(tokenize(para))
            overlap = len(question_terms & para_terms)
            if overlap > 0:
                candidates.append((overlap, label, para))

    if not candidates:
        return "No specific matches found. Please try rephrasing or ask a more targeted question."

    candidates.sort(key=lambda x: x[0], reverse=True)
    snippets = candidates[:max_snippets]
    lines = []
    for _, label, para in snippets:
        prefix = "Report" if label == "research_paper" else "Background"
        lines.append(f"- {prefix}: {para}")

    return "Here are relevant excerpts I found:\n\n" + "\n\n".join(lines)


def _removed_api_login():
    """Authenticate a user via username + project key."""
    if not is_project_key_configured():
        return jsonify({'success': False, 'error': 'Server not configured for logins'}), 503

    data = request.get_json(silent=True) or {}
    username = sanitize_field(data.get('username'), 80)
    project_key = sanitize_field(data.get('project_key'), 256)
    requested_mode = APP_MODE_SELECTION

    if not username or not project_key:
        return jsonify({'success': False, 'error': 'Username and project key are required'}), 400

    digest = verify_project_key(project_key)
    if not digest:
        return jsonify({'success': False, 'error': 'Invalid project key'}), 401

    registered, registration_error = is_username_registered(username)
    if registration_error:
        return jsonify({'success': False, 'error': 'Registration system unavailable. Please try again later.'}), 503
    if not registered:
        return jsonify({
            'success': False,
            'error': 'Username not registered. Please submit the registration form before logging in.',
            'registration_required': True,
            'suggested_username': username,
        }), 403

    registry_entry = PROJECT_KEY_REGISTRY.get(digest)
    clear_session_state()
    session.clear()
    session['authenticated'] = True
    session['username'] = username
    session['project_config'] = registry_entry.get('config', {}) if registry_entry else {}
    session['project_label'] = registry_entry.get('label') if registry_entry else None
    session['checkpoint_s3_prefix'] = registry_entry.get('checkpoint_s3_prefix') if registry_entry else None
    session['project_key_digest'] = digest
    forced_mode = None
    if registry_entry and isinstance(registry_entry.get('config'), dict):
        forced_mode = registry_entry['config'].get('app_mode')
        if not forced_mode and registry_entry['config'].get('audit'):
            forced_mode = APP_MODE_AUDIT
    if forced_mode in (APP_MODE_SELECTION, APP_MODE_AUDIT, APP_MODE_AUDIT_ONLY):
        requested_mode = forced_mode
    session['app_mode'] = requested_mode
    # Force a fresh per-login session id to avoid reusing prior cookie-bound state.
    session['session_id'] = os.urandom(16).hex()
    session['generation_id'] = 0
    progress = get_project_progress(digest)
    session['project_submission_count'] = progress.get('submission_count', 0)
    session['project_round'] = _get_project_round_from_disk()
    session.permanent = True

    # Ensure disk paths for code/checkpoints are bound to the project prefix.
    checkpoint_s3_prefix = session.get('checkpoint_s3_prefix')
    if checkpoint_s3_prefix:
        existing_cfg = session.get('project_config', {})
        github_remote_path = existing_cfg.get('github_remote_path')
        readme_path = existing_cfg.get('readme_path')
        derived = _derive_project_config(
            checkpoint_s3_prefix,
            github_remote_path,
            readme_path=readme_path,
        )
        session['project_config'].update(derived)
        os.makedirs(Path(derived['checkpoint_dir']), exist_ok=True)
        os.makedirs(Path(derived['code_dir']), exist_ok=True)
        os.makedirs(_checkpoint_sync_root(checkpoint_s3_prefix), exist_ok=True)

    checkpoint_s3_prefix = session.get('project_config', {}).get('checkpoint_s3_prefix')
    if checkpoint_s3_prefix:
        session['checkpoint_sync_requested_at'] = _utcnow_iso()
        target_root = str(_checkpoint_sync_root(checkpoint_s3_prefix))
        start_sequential_checkpoint_sync(digest, checkpoint_s3_prefix, target_root=target_root)

    _write_interaction_log('login', {'username': username, 'app_mode': requested_mode})
    log_event('login', {'username': username, 'project_key_digest': digest})

    redirect_target = url_for('audit_home') if requested_mode == APP_MODE_AUDIT else url_for('app_interface')
    return jsonify({'success': True, 'redirect': redirect_target})


def _compute_s3_sync_status() -> dict:
    """Compatibility result for local projects; remote sync is unavailable."""
    return {'success': True, 'required': False, 'state': 'not_required'}
    """Compute S3 sync status for the current session."""
    project_cfg = _get_project_config()
    checkpoint_s3_prefix = (
        project_cfg.get('checkpoint_s3_prefix')
        or session.get('checkpoint_s3_prefix')
    )
    digest = session.get('project_key_digest')
    if not checkpoint_s3_prefix or not digest:
        return {'success': True, 'required': False, 'state': 'not_required'}
    if DISABLE_S3_SYNC:
        return {'success': True, 'required': False, 'state': 'not_required'}

    requested_at = session.get('checkpoint_sync_requested_at')
    assets_ready = _project_assets_ready(project_cfg)
    completion_seen = _sync_completion_seen_after(checkpoint_s3_prefix, requested_at)
    marker_payload = _read_sync_marker(checkpoint_s3_prefix)
    marker_updated = _parse_utc_iso(marker_payload.get('updated_at'))
    requested_dt = _parse_utc_iso(requested_at)
    marker_matches_request = bool(
        requested_dt
        and marker_updated
        and marker_updated >= requested_dt
    )
    sync_needs_wait = (bool(requested_at) and not (completion_seen and assets_ready)) or not assets_ready

    tokens = [digest, f"{digest}:pull_s3"]
    with S3_SYNC_LOCK:
        statuses = {token: S3_SYNC_STATUS.get(token) or {} for token in tokens}

    if all(not statuses[token] for token in tokens):
        if not sync_needs_wait:
            session.pop('checkpoint_sync_requested_at', None)
            return {'success': True, 'required': False, 'state': 'not_required'}
        if requested_at and marker_matches_request:
            marker_state = marker_payload.get('state')
            if marker_state == 'error':
                return {
                    'success': True,
                    'required': True,
                    'state': 'error',
                    'error': marker_payload.get('error') or 'Unknown error',
                    'statuses': statuses,
                }
            if marker_state == 'running':
                return {
                    'success': True,
                    'required': True,
                    'state': 'running',
                    'error': None,
                    'statuses': statuses,
                }
            return {
                'success': True,
                'required': True,
                'state': 'pending',
                'error': None,
                'statuses': statuses,
            }
        if boto3 is None:
            return {
                'success': True,
                'required': True,
                'state': 'error',
                'error': 'boto3 not installed',
                'statuses': statuses,
            }
        if checkpoint_s3_prefix:
            target_root = str(_checkpoint_sync_root(checkpoint_s3_prefix))
            start_sequential_checkpoint_sync(digest, checkpoint_s3_prefix, target_root=target_root)
        with S3_SYNC_LOCK:
            statuses = {token: S3_SYNC_STATUS.get(token) or {} for token in tokens}

    def _state(value: dict) -> str:
        return value.get('state') or 'pending'

    states = [_state(statuses[token]) for token in tokens]
    error_message = None
    for token in tokens:
        if statuses[token].get('state') == 'error':
            error_message = statuses[token].get('error') or 'Unknown error'
            break

    if error_message:
        overall_state = 'error'
    elif all(state == 'success' for state in states):
        overall_state = 'success'
    elif any(state == 'running' for state in states):
        overall_state = 'running'
    else:
        overall_state = 'pending'

    if not error_message and (
        (all(state == 'success' for state in states) and assets_ready)
        or (completion_seen and assets_ready)
    ):
        session.pop('checkpoint_sync_requested_at', None)
        return {
            'success': True,
            'required': False,
            'state': 'not_required',
            'error': None,
            'statuses': statuses,
        }

    return {
        'success': True,
        'required': True if sync_needs_wait or overall_state != 'success' else False,
        'state': overall_state,
        'error': error_message,
        'statuses': statuses,
    }


@app.route('/api/memory_status', methods=['GET'])
def api_memory_status():
    """
    Debug endpoint to check current server memory usage.

    Returns memory stats including RSS, VMS, and percentage of system memory.
    Useful for debugging OOM issues in production.
    """
    try:
        mem_info = get_memory_info()
        is_high, current_mem = check_memory_threshold(threshold_mb=2500.0)

        # Log that memory status was checked
        log_memory("api_memory_status: status check requested")

        return jsonify({
            'success': True,
            'memory': {
                'rss_mb': round(mem_info['rss_mb'], 2),
                'vms_mb': round(mem_info['vms_mb'], 2),
                'percent': round(mem_info['percent'], 2),
                'available_system_mb': round(mem_info['available_mb'], 2) if mem_info['available_mb'] > 0 else None,
                'timestamp': mem_info['timestamp'],
            },
            'warnings': {
                'high_memory': is_high,
                'threshold_mb': 400.0,
            },
            'limits': {
                'render_instance_mb': 512,  # Starter plan
                'warning_threshold_mb': 400,
                'danger_threshold_mb': 450,
            },
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'memory': {'rss_mb': get_memory_mb()},
        })


def _removed_api_logout():
    """Clear the current session and any attached generators."""
    log_event('logout')
    cleanup_prefix = session.get('checkpoint_s3_prefix')
    if cleanup_prefix and session.get('cleanup_ready'):
        try:
            cleaned, cleanup_error = _cleanup_disk_cache(cleanup_prefix)
            if cleaned:
                log_event('checkpoint_cache_cleared', {
                    'checkpoint_s3_prefix': cleanup_prefix,
                    'source': 'logout',
                })
            else:
                log_event('checkpoint_cache_clear_skipped', {
                    'checkpoint_s3_prefix': cleanup_prefix,
                    'reason': cleanup_error,
                    'source': 'logout',
                })
        except Exception as exc:
            log_event('checkpoint_cache_clear_failed', {
                'checkpoint_s3_prefix': cleanup_prefix,
                'error': str(exc),
                'source': 'logout',
            })
    clear_session_state()
    session.clear()
    response = jsonify({'success': True})
    return _clear_session_cookie(response)


@app.route('/api/cleanup', methods=['POST'])
def api_cleanup():
    """Best-effort cleanup for downloaded checkpoint/code data."""
    if not AUTO_CLEANUP_ENABLED:
        return jsonify({'success': False, 'error': 'Cleanup disabled'}), 403
    if not session.get('cleanup_ready'):
        return jsonify({'success': False, 'error': 'Cleanup not armed'}), 403
    cleanup_prefix = session.get('checkpoint_s3_prefix')
    try:
        cleaned, cleanup_error = _cleanup_disk_cache(cleanup_prefix)
        if cleaned:
            log_event('checkpoint_cache_cleared', {
                'checkpoint_s3_prefix': cleanup_prefix,
                'source': 'cleanup',
            })
        else:
            log_event('checkpoint_cache_clear_skipped', {
                'checkpoint_s3_prefix': cleanup_prefix,
                'reason': cleanup_error,
                'source': 'cleanup',
            })
        clear_session_state()
        session['cleanup_ready'] = False
        return jsonify({'success': True, 'cleaned': cleaned, 'reason': cleanup_error})
    except Exception as exc:
        log_event('checkpoint_cache_clear_failed', {
            'checkpoint_s3_prefix': cleanup_prefix,
            'error': str(exc),
            'source': 'cleanup',
        })
        return jsonify({'success': False, 'error': str(exc)}), 500


def _removed_api_register():
    """Collect metadata for provisioning a new username."""
    data = request.get_json(silent=True) or {}

    name = sanitize_field(data.get('name'), 120)
    email = sanitize_field(data.get('email'), 160)
    username = sanitize_field(data.get('username'), 80)
    confirm_username = sanitize_field(data.get('confirm_username'), 80)
    affiliation = sanitize_field(data.get('affiliation'), 160)

    errors = {}
    if not name:
        errors['name'] = 'Name is required'
    if not email:
        errors['email'] = 'Email is required'
    if not username:
        errors['username'] = 'Username is required'
    if not confirm_username:
        errors['confirm_username'] = 'Please confirm your username'
    elif username and username != confirm_username:
        errors['confirm_username'] = 'Usernames do not match'

    if errors:
        return jsonify({'success': False, 'errors': errors, 'error': 'Please correct the highlighted fields'}), 400

    submission = {
        'name': name,
        'affiliation': affiliation,
        'email': email,
        'username': username,
    }

    try:
        record_registration_submission(submission)
    except OSError as exc:
        return jsonify({'success': False, 'error': f'Failed to persist registration: {exc}'}), 500

    session['registration_name'] = name
    log_event('registration_submitted', {'username': username})
    return jsonify({
        'success': True,
        'message': 'Thanks! Your username is registered for project activity logging.',
        'name': name,
    })


@app.route('/api/log_event', methods=['POST'])
def api_log_event():
    """Accept client-side telemetry events."""
    content_length = request.content_length or 0
    if content_length > CLIENT_EVENT_MAX_BYTES:
        return jsonify({'success': False, 'error': 'Payload too large'}), 413

    data = request.get_json(silent=True) or {}
    event_type = sanitize_field(data.get('event_type'), 80)
    payload = data.get('payload') if isinstance(data.get('payload'), dict) else {}

    if not event_type:
        return jsonify({'success': False, 'error': 'event_type is required'}), 400
    if event_type not in ALLOWED_CLIENT_EVENT_TYPES:
        return jsonify({'success': False, 'error': 'Unsupported event_type'}), 400

    identity = _client_event_identity()
    if _client_event_rate_limited(identity):
        return jsonify({'success': False, 'error': 'Rate limit exceeded'}), 429

    safe_payload = _sanitize_event_payload(payload, max_depth=3, max_items=30, max_str=300)
    if len(json.dumps(safe_payload, ensure_ascii=True, default=str)) > CLIENT_EVENT_MAX_BYTES:
        return jsonify({'success': False, 'error': 'Payload too large'}), 413

    log_event(event_type, safe_payload)
    return jsonify({'success': True})


@app.route('/api/generation_status', methods=['GET'])
def api_generation_status():
    """Unlocked status check for an in-flight Hypothesis Studio generation.

    Returning to the Studio while /api/generate is still holding the per-session
    operation lock must not block on /api/initialize. This endpoint reads only
    the generation state guarded by GENERATION_LOCK, so the client can render
    the in-progress generation screen immediately and poll until initialization
    is safe again.
    """
    session_id = session.get('session_id')
    status = _generation_status_for_session(session_id)
    return jsonify({
        'success': True,
        **status,
        'regeneration': _hypothesis_regeneration_status_for_session(session_id),
        'generation_id': _get_generation_id(),
        'session_debug_id': _session_debug_id(),
    })


@app.route('/api/initialize', methods=['POST'])
@with_session_operation_lock('api_initialize')
def api_initialize():
    """Initialize a new session with a checkpoint directory."""
    data = request.json or {}
    project_cfg = _get_project_config()
    data_path = data.get('data_path') or project_cfg.get('data_path')
    checkpoint_dir = (
        data.get('checkpoint_dir')
        or project_cfg.get('checkpoint_dir')
        or data_path
        or CLI_CHECKPOINT_DIR
    )
    hypothesis_model = data.get('hypothesis_model')
    legacy_hyp_gen = data.get('legacy_hyp_gen')
    num_hypotheses = data.get('num_hypotheses')
    code_dir = data.get('code_dir') or project_cfg.get('code_dir') or CLI_CODE_DIR
    include_background_literature = data.get('include_background_literature')
    if include_background_literature is None:
        include_background_literature = CLI_INCLUDE_BACKGROUND_LITERATURE
    generate_plan = data.get('generate_plan')
    if generate_plan is None:
        generate_plan = True
    readme_path = data.get('readme_path') or project_cfg.get('readme_path') or CLI_README_PATH
    research_paper_path = data.get('research_paper_path') or _get_configured_research_paper_path(project_cfg)
    enable_citations = False
    cache_documents = data.get('cache_documents')
    if cache_documents is None:
        cache_documents = CLI_CACHE_DOCUMENTS

    # User-facing mode defaults
    if not CLI_DEV_MODE:
        include_background_literature = True
        if not hypothesis_model:
            hypothesis_model = DEFAULT_HYPOTHESIS_MODEL
    else:
        if not hypothesis_model:
            hypothesis_model = CLI_HYPOTHESIS_MODEL

    if not checkpoint_dir:
        return jsonify({'success': False, 'error': 'No checkpoint directory provided'})
    checkpoint_dir = str(_v2_resolve_current_studio_checkpoint_dir(Path(checkpoint_dir)))
    if project_cfg.get('checkpoint_s3_prefix') and not Path(checkpoint_dir).exists():
        return jsonify({'success': False, 'error': 'Checkpoint sync in progress. Please try again shortly.'})

    try:
        # Create generator instance
        generator = create_generator(
            checkpoint_dir,
            hypothesis_model,
            legacy_hyp_gen=legacy_hyp_gen,
            num_hypotheses=num_hypotheses,
            code_dir=code_dir,
            include_background_literature=include_background_literature,
            generate_plan=generate_plan,
            readme_path=readme_path,
            enable_citations=enable_citations,
            cache_documents=cache_documents,
            research_paper_path=research_paper_path,
        )

        # Validate checkpoint using CLI method
        if not generator.validate_checkpoint():
            return jsonify({'success': False, 'error': 'Invalid checkpoint directory'})

        # Load checkpoint using CLI method
        if not generator.load_checkpoint():
            return jsonify({'success': False, 'error': 'Failed to load checkpoint'})

        project_task = (project_cfg.get('task_description') or '').strip()
        if project_task:
            generator.task_description = project_task

        # Initialize agent using CLI method
        generator.initialize_agent()
        restored_hypotheses = _restore_generated_hypotheses_if_available(generator)
        # Match app_interface's routing: also scan sibling round_* dirs so a
        # round the backend already started (round_N+1 while the session still
        # points at round_N) triggers the Round Monitor redirect here too.
        active_round_dir = _resolve_active_monitor_round(Path(generator.checkpoint_dir))
        round_state = _classify_round_state(active_round_dir)

        progress_payload = _get_project_progress_payload()

        # Persist resolved paths so cross-worker restores use the same config.
        existing_cfg = session.get('project_config')
        runtime_cfg = existing_cfg if isinstance(existing_cfg, dict) else {}
        runtime_cfg = dict(runtime_cfg)
        runtime_cfg.update({
            'checkpoint_dir': checkpoint_dir,
            'code_dir': str(code_dir) if code_dir else None,
            'readme_path': str(readme_path) if readme_path else None,
            'task_description': generator.task_description,
            # Generation settings, so cross-worker/post-restart restores
            # recreate the generator the user actually configured.
            'hypothesis_model': hypothesis_model,
            'include_background_literature': bool(include_background_literature),
            'generate_plan': bool(generate_plan),
        })
        if data_path:
            runtime_cfg['data_path'] = str(data_path)
        if project_cfg.get('data_dir'):
            runtime_cfg['data_dir'] = project_cfg.get('data_dir')
        session['project_config'] = runtime_cfg

        log_event('initialize', {
            'checkpoint_dir': checkpoint_dir,
            'code_dir': code_dir,
            'hypothesis_model': hypothesis_model,
        })
        return jsonify({
            'success': True,
            'n_hypotheses': generator.hypothesis_config.n_candidate_hypotheses,
            'legacy_hyp_gen': generator.use_legacy_hyp_gen,
            'dataset_description': generator.dataset_description,
            'task_description': generator.task_description,
            'has_round_report': generator.round_report is not None,
            'has_memory_report': generator.memory_report is not None,
            'has_memory_report_summary': getattr(generator, 'memory_report_summary', None) is not None,
            'has_round_report_summary': getattr(generator, 'round_report_summary', None) is not None,
            'code_chat_available': generator.code_dir is not None,
            'include_background_literature': generator.include_background_literature,
            'has_background_literature': generator.deep_research_background is not None,
            'has_research_paper': bool(getattr(generator, 'research_paper_path', None)),
            'generate_plan': getattr(generator.hypothesis_config, 'generate_plan', False),
            'enable_citations': generator.enable_citations,
            'generation_id': _get_generation_id(),
            'session_debug_id': _session_debug_id(),
            'restored_hypotheses': restored_hypotheses,
            'redirect_to_round_monitor': round_state == 'round_monitor',
            # User-facing /rounds/ destination when a V2 project is active;
            # None for legacy/non-V2 sessions, which keep redirecting to the
            # generic /round_monitor route unchanged.
            'round_workspace_url': (
                url_for('rounds_workspace')
                if round_state == 'round_monitor' and session.get('v2_active_project_slug')
                and _v2_round_index(active_round_dir) is not None
                else None
            ),
            **progress_payload,
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/session_info', methods=['GET'])
def api_session_info():
    """Return session details for secondary pages."""
    generator = get_generator()
    if not generator:
        return jsonify({'success': False, 'error': 'No active session'})

    progress_payload = _get_project_progress_payload()

    code_dir = _resolve_code_dir(generator)
    return jsonify({
        'success': True,
        'n_hypotheses': generator.hypothesis_config.n_candidate_hypotheses,
        'dataset_description': generator.dataset_description,
        'task_description': generator.task_description,
        'has_round_report': generator.round_report is not None,
        'has_memory_report_summary': getattr(generator, 'memory_report_summary', None) is not None,
        'has_round_report_summary': getattr(generator, 'round_report_summary', None) is not None,
        'include_background_literature': generator.include_background_literature,
        'has_research_paper': bool(getattr(generator, 'research_paper_path', None)),
        'generate_plan': getattr(generator.hypothesis_config, 'generate_plan', False),
        'enable_citations': getattr(generator, 'enable_citations', False),
        'code_chat_available': _is_code_chat_available(code_dir),
        'code_dir': str(code_dir) if code_dir else None,
        **progress_payload
    })


@app.route('/api/readme', methods=['GET'])
def api_readme():
    """Return dataset README contents for the UI teaser."""
    project_cfg = _get_project_config()
    requested_path = request.args.get('path')
    readme_path, resolve_error = _resolve_readme_path_secure(project_cfg, requested_path)
    if not readme_path:
        return jsonify({'success': False, 'error': resolve_error or 'README unavailable'}), 404
    content, error = _load_readme_content(str(readme_path))
    if content is None:
        return jsonify({'success': False, 'error': error or 'README unavailable'}), 404

    return jsonify({
        'success': True,
        'content': content,
        'path': str(readme_path),
    })


@app.route('/readme_view')
def readme_view():
    """Render the dataset README as a standalone markdown page."""
    project_cfg = _get_project_config()
    requested_path = request.args.get('path')
    readme_path, resolve_error = _resolve_readme_path_secure(project_cfg, requested_path)
    content = None
    error = resolve_error
    if readme_path:
        content, error = _load_readme_content(str(readme_path))

    readme_html = None
    error_message = None
    if content is None:
        error_message = error or 'README unavailable.'
    else:
        readme_html = render_markdown_to_html(content)
        code_dir = project_cfg.get('code_dir') or CLI_CODE_DIR
        code_prefix = url_for('readme_asset', filename='')
        code_dir_path = Path(code_dir).resolve() if code_dir else None
        readme_html = rewrite_image_sources(
            readme_html,
            checkpoint_prefix=None,
            code_prefix=code_prefix,
            checkpoint_dir=None,
            code_dir=code_dir_path,
        )

    return render_template(
        'readme_view.html',
        readme_html=Markup(readme_html) if readme_html else None,
        error_message=error_message,
        readme_path=str(readme_path) if readme_path else None,
    )


@app.route('/readme_assets/<path:filename>')
def readme_asset(filename):
    """Serve README assets from the configured code directory."""
    project_cfg = _get_project_config()
    code_dir = project_cfg.get('code_dir') or CLI_CODE_DIR
    if not code_dir:
        abort(404)
    base_dir = Path(code_dir).resolve()
    if not base_dir.exists():
        abort(404)

    target_path = (base_dir / filename).resolve()
    if not _path_within_root(target_path, base_dir) or not target_path.exists():
        abort(404)

    return send_from_directory(base_dir, filename)


@app.route('/website_guide')
def website_guide():
    """Render the website guide as a standalone markdown page."""
    project_root = Path(__file__).parent.parent
    guide_path = None
    for candidate in (
        project_root / 'guides' / 'WEBSITE_GUIDE.md',
        project_root / 'WEBSITE_GUIDE.md',  # Backward-compatible legacy path
    ):
        if candidate.exists():
            guide_path = candidate
            break

    guide_html = None
    error_message = None

    if not guide_path:
        error_message = 'Website guide not available.'
    else:
        try:
            with open(guide_path, 'r', encoding='utf-8') as f:
                content = f.read()
            guide_html = render_markdown_to_html(content)
        except Exception as e:
            error_message = f'Error loading website guide: {str(e)}'

    return render_template(
        'website_guide.html',
        guide_html=Markup(guide_html) if guide_html else None,
        error_message=error_message,
    )


# =====================================================================
# Hypothesis Studio durable history — write-side instrumentation
# ---------------------------------------------------------------------
# The permanent product object is the round's chronological conversation.
# These helpers append snapshot events to <round dir>/hypothesis_studio_history.json
# ONLY after an operation has already succeeded. They never mutate hypothesis
# state, never call an LLM, and never raise into the instrumented route: history
# capture must not be able to fail a scientific operation.
# =====================================================================

def _studio_history_round_dir(generator) -> Path | None:
    """Round dir a live event belongs to: the generator's active checkpoint dir
    (the same directory generated_hypotheses.json / selected_hypothesis.md use)."""
    expected = _v2_resolve_current_studio_checkpoint_dir_for_project()
    if generator is not None and getattr(generator, 'checkpoint_dir', None):
        current = Path(generator.checkpoint_dir)
        if expected is not None:
            try:
                if current.resolve() != expected.resolve():
                    return expected
            except OSError:
                pass
        return current
    return expected


def _studio_hypothesis_snapshot(generator, index: int, full_text: str | None = None) -> dict:
    """Snapshot one hypothesis exactly as the scientist saw it (parsed sections
    plus full text), for durable before/after and set events."""
    text = full_text
    if text is None:
        hyps = getattr(generator, 'hypotheses', None) or []
        if isinstance(index, int) and 1 <= index <= len(hyps):
            text = hyps[index - 1]
    text = text or ''
    try:
        sections = generator.parse_hypothesis_sections(text)
    except Exception:
        sections = _parse_hypothesis_sections_standalone(text)
    snap = {
        'index': index,
        'title': sections.get('title'),
        'full_text': text,
        'hypothesis': sections.get('hypothesis'),
        'justification': sections.get('justification'),
        'interpretation': sections.get('interpretation'),
        'plan': sections.get('plan'),
    }
    state_ids = getattr(generator, 'hypothesis_state_ids', None)
    if isinstance(state_ids, list) and isinstance(index, int) and 1 <= index <= len(state_ids):
        snap['state_id'] = state_ids[index - 1]
    thread_ids = _ensure_hypothesis_thread_ids(generator)
    if isinstance(thread_ids, list) and isinstance(index, int) and 1 <= index <= len(thread_ids):
        thread_id = thread_ids[index - 1]
        if isinstance(thread_id, str) and thread_id.strip():
            snap['hypothesis_thread_id'] = thread_id.strip()[:80]
    return snap


def _studio_record_events(round_dir, events: list, *, strict: bool = False) -> dict | None:
    """Append events, optionally surfacing a persistence failure to the caller.

    Normal history instrumentation remains best-effort. Historical-result
    activation uses ``strict=True`` because its content and thread association
    must be committed together or rolled back together.
    """
    if not round_dir or not events:
        return None
    try:
        return hypothesis_history.append_events(round_dir, events)
    except Exception as exc:  # pragma: no cover - defensive; never fail the op
        if strict:
            raise
        print(f"⚠️ Warning: failed to record hypothesis studio history: {exc}")
        return None


def _persist_generated_hypotheses(generator, *, strict: bool = False) -> None:
    """Persist the current slot state, surfacing failures when supported."""
    try:
        generator.persist_generated_hypotheses(raise_on_error=strict)
    except TypeError:
        # Compatibility for focused test doubles and older local generators.
        generator.persist_generated_hypotheses()


def _studio_record_generation(generator, parsed_hypotheses: list, generation_id) -> str | None:
    """Append the generated hypothesis-set snapshot after successful generation."""
    round_dir = _studio_history_round_dir(generator)
    if round_dir is None:
        return None
    snapshots = []
    for position, parsed in enumerate(parsed_hypotheses or [], 1):
        idx = int(parsed.get('index') or position) if isinstance(parsed, dict) else position
        snapshot = dict(parsed) if isinstance(parsed, dict) else {}
        current = _studio_hypothesis_snapshot(generator, idx, full_text=snapshot.get('full_text'))
        snapshot.update({key: value for key, value in current.items() if value is not None})
        snapshots.append(snapshot)
    event = hypothesis_history.hypothesis_set_generated_event(
        snapshots, generation_id=generation_id,
    )
    event['id'] = uuid.uuid4().hex
    _studio_record_events(round_dir, [event])
    return event['id']


def _studio_record_chat(generator, message: str, reply: str, hypothesis_index) -> None:
    """After a successful post-generation hypothesis-mode chat exchange, append
    the scientist message and DiscoveryAgent response (never failed requests)."""
    round_dir = _studio_history_round_dir(generator)
    if round_dir is None or not reply:
        return
    user_ev = hypothesis_history.user_message_event(message, hypothesis_history.PHASE_HYPOTHESIS_DISCUSSION)
    assistant_ev = hypothesis_history.assistant_message_event(
        reply, hypothesis_history.PHASE_HYPOTHESIS_DISCUSSION)
    if hypothesis_index is not None:
        try:
            idx = int(hypothesis_index)
            # Attribute BOTH sides of the exchange by display index (presentation)
            # AND by stable thread id (durable per-hypothesis thread restoration).
            user_ev['hypothesis_index'] = idx
            assistant_ev['hypothesis_index'] = idx
            thread_id = _hypothesis_thread_key_for_index(generator, idx)
            if thread_id:
                user_ev['thread_id'] = thread_id
                assistant_ev['thread_id'] = thread_id
        except (TypeError, ValueError):
            pass
    _studio_record_events(round_dir, [user_ev, assistant_ev])


def _studio_record_mutation(generator, kind: str, index: int, before: dict, after: dict,
                            request_text: str | None = None, *, action: str | None = None,
                            regeneration_position: int | None = None,
                            source_result_event_id: str | None = None,
                            request_thread_id: str | None = None,
                            strict: bool = False) -> str | None:
    """After a successful agent-driven edit/regenerate, append the durable
    revision provenance.

    When the scientist supplied explicit free-text guidance for this revision,
    record the exact request as a `user_message` (phase hypothesis_discussion,
    with hypothesis_index) IMMEDIATELY ahead of the revision event, in a single
    atomic append so history can never contain a request without its result.

    request_text is None for literal manual edits and for button-only actions
    (a plain "Regenerate" carries only a system default, not a scientist
    message): in those cases only the revision event is recorded — no scientist
    message is fabricated.
    """
    round_dir = _studio_history_round_dir(generator)
    if round_dir is None:
        return None
    if kind == 'revised':
        mutation_ev = hypothesis_history.hypothesis_revised_event(index, before, after)
    else:
        mutation_ev = hypothesis_history.hypothesis_regenerated_event(index, before, after)
    if action:
        mutation_ev['action'] = action
    if regeneration_position is not None:
        mutation_ev['regeneration_position'] = regeneration_position
    if source_result_event_id:
        mutation_ev['source_result_event_id'] = source_result_event_id
    mutation_ev['id'] = uuid.uuid4().hex

    events = []
    request_text = (request_text or '').strip()
    if request_text:
        user_ev = hypothesis_history.user_message_event(
            request_text, hypothesis_history.PHASE_HYPOTHESIS_DISCUSSION)
        if index is not None:
            user_ev['hypothesis_index'] = index
            # Tag with the thread active DURING this operation. For a regenerate
            # the caller records this BEFORE rotating the thread id, so the
            # request stays with the discarded thread and the regenerated
            # hypothesis's fresh thread never re-collects it.
            thread_id = request_thread_id or _hypothesis_thread_key_for_index(generator, index)
            if thread_id:
                user_ev['thread_id'] = thread_id
        events.append(user_ev)
    events.append(mutation_ev)
    # One batched append: request + result land together or not at all.
    _studio_record_events(round_dir, events, strict=strict)
    return mutation_ev['id']


def _studio_record_selection(generator, selected: dict) -> None:
    """After /api/select persists selected_hypothesis.md, append the closing
    hypothesis_selected event (the round's final substantive event)."""
    round_dir = _studio_history_round_dir(generator)
    if round_dir is None:
        return
    _studio_record_events(round_dir, [hypothesis_history.hypothesis_selected_event(selected)])


def _studio_record_removal(generator, index: int, removed: dict, remaining: list) -> str | None:
    """After a successful hypothesis removal, append the durable removed event
    (with the re-indexed surviving set snapshot)."""
    round_dir = _studio_history_round_dir(generator)
    if round_dir is None:
        return None
    snapshots = []
    for position, parsed in enumerate(remaining or [], 1):
        idx = int(parsed.get('index') or position) if isinstance(parsed, dict) else position
        snapshot = dict(parsed) if isinstance(parsed, dict) else {}
        current = _studio_hypothesis_snapshot(generator, idx, full_text=snapshot.get('full_text'))
        snapshot.update({key: value for key, value in current.items() if value is not None})
        snapshots.append(snapshot)
    event = hypothesis_history.hypothesis_removed_event(index, removed, snapshots)
    event['id'] = uuid.uuid4().hex
    _studio_record_events(round_dir, [event])
    return event['id']


def _hypothesis_thread_ctx(generator, idx: int, restore: bool = False):
    """Context manager scoping one V1 ask/edit/regenerate call to the hypothesis
    at 1-based idx via its STABLE thread key. Degrades to a no-op for generators
    that predate per-hypothesis threads (e.g. test doubles), preserving the
    shared-conversation behavior for them.

    The swap itself is made atomic by the generator's own _thread_swap_lock
    (inside use_hypothesis_thread), so it cannot interleave even without an outer
    lock. The per-session operation lock (with_session_operation_lock) remains
    the outer serialization for the rest of each request.
    """
    if not hasattr(generator, 'use_hypothesis_thread'):
        return nullcontext()
    thread_key = _hypothesis_thread_key_for_index(generator, idx)
    if not thread_key:
        return nullcontext()
    restored = None
    if restore and thread_key not in getattr(generator, 'hypothesis_chat_threads', {}):
        restored = _restore_hypothesis_thread_messages(generator, thread_key)
    return generator.use_hypothesis_thread(thread_key, restored_messages=restored)


def _regenerate_reset_hypothesis_thread(generator, idx: int) -> str | None:
    """Give the hypothesis at 1-based idx a clean refinement thread after a
    single-hypothesis regeneration: drop the old in-memory thread and rotate its
    stable thread id so the new result starts clean. Pre-regeneration durable
    messages remain available if that historical result is activated later."""
    if not hasattr(generator, 'clear_hypothesis_thread'):
        # Test doubles and legacy generators still receive a durable mapping.
        return _rotate_hypothesis_thread_id(generator, idx)
    old_key = _hypothesis_thread_key_for_index(generator, idx)
    if old_key:
        generator.clear_hypothesis_thread(old_key)
    return _rotate_hypothesis_thread_id(generator, idx)


def _restore_hypothesis_thread_messages(generator, thread_key: str) -> list[dict]:
    """Rebuild one hypothesis's refinement-thread messages from durable history
    (used when the in-memory thread was lost to a worker restart/eviction).

    Attribution is by STABLE thread id: every discussion turn is tagged with the
    thread id of the hypothesis it concerned when recorded. Grouping by thread id
    is immune to index shifts (removal) and to a regenerated hypothesis reusing
    an index. A regenerate rotates the thread id, so each result restores only
    the messages associated with its own durable thread.
    """
    if not thread_key:
        return []
    try:
        round_dir = _studio_history_round_dir(generator)
        if round_dir is None:
            return []
        history = hypothesis_history.load_history(round_dir)
        messages: list[dict] = []
        for ev in history.get('events') or []:
            etype = ev.get('type')
            if etype not in (hypothesis_history.TYPE_USER_MESSAGE,
                             hypothesis_history.TYPE_ASSISTANT_MESSAGE):
                continue
            if ev.get('phase') != hypothesis_history.PHASE_HYPOTHESIS_DISCUSSION:
                continue
            if ev.get('thread_id') != thread_key:
                continue
            role = 'assistant' if etype == hypothesis_history.TYPE_ASSISTANT_MESSAGE else 'user'
            content = ev.get('content') or ''
            if content.strip():
                messages.append({'role': role, 'content': content})
        return messages
    except Exception:
        return []


@app.route('/api/generate', methods=['POST'])
@with_generation_operation_lock
def api_generate():
    """Generate candidate hypotheses using CLI method."""
    if _is_audit_only_mode():
        return _reject_audit_only('Hypothesis generation')

    # Lifecycle guard (defense-in-depth for the UI's pre-Round-0 lock): no
    # hypothesis generation before exploratory Round 0 has started. Checked first
    # and before building a generator, using the same canonical predicate as the
    # Studio page, so a direct API call in the pristine state is rejected too.
    if _active_v2_project_pre_round_0():
        return _initial_exploration_required_response()

    generator = get_or_restore_generator()
    if not generator:
        return jsonify({'success': False, 'error': 'No active session'})

    # Objective + Context are required at project creation, so a normal V2
    # project is already ready — there is no duplicate readiness gate here. This
    # only catches a legacy/incomplete project, with a friendly message. Ordered
    # AFTER the pre-Round-0 guard: a pristine project reports the lifecycle error,
    # not this setup error.
    if _check_project_readiness(generator)['missing']:
        return _project_setup_incomplete_response()

    session_id = session.get('session_id')
    token = getattr(g, 'generation_token', None)
    if not session_id or not token:
        return jsonify({'success': False, 'error': 'Session unavailable'})

    previous_state = {
        'hypotheses': list(generator.hypotheses) if getattr(generator, 'hypotheses', None) else [],
        'last_generation_citations': copy.deepcopy(getattr(generator, 'last_generation_citations', [])),
        'citation_documents': copy.deepcopy(getattr(generator, 'citation_documents', [])),
        'agent_messages': copy.deepcopy(
            getattr(getattr(generator, 'hypothesis_gen_agent', None), 'messages', [])
        ),
        'agent_last_response_citations': copy.deepcopy(
            getattr(getattr(generator, 'hypothesis_gen_agent', None), 'last_response_citations', [])
        ),
    }

    try:
        if _is_generation_cancelled(session_id, token):
            return jsonify({'success': False, 'error': 'Generation cancelled', 'cancelled': True})
        data = request.json or {}
        requested_n = data.get('num_hypotheses')
        guidance = data.get('guidance')
        if requested_n is not None:
            try:
                requested_n = int(requested_n)
                if requested_n < 1:
                    requested_n = 1
                if requested_n > 10:
                    requested_n = 10
                generator.hypothesis_config.n_candidate_hypotheses = requested_n
            except (TypeError, ValueError):
                requested_n = None

        # Log which model will be used for hypothesis generation (console visibility)
        model_name = None
        llm_cfg = getattr(getattr(generator, 'hypothesis_config', None), 'llm_config', None)
        if llm_cfg:
            model_name = getattr(llm_cfg, 'model_name', None)
        if not model_name:
            model_name = getattr(generator, 'hypothesis_model', None) or CLI_HYPOTHESIS_MODEL
        print(f"[api_generate] Using hypothesis model: {model_name or 'unknown'}")

        # On full regeneration, delete persisted files before generating so stale
        # hypotheses cannot be restored if this generation fails or is cancelled.
        is_regeneration = bool(getattr(generator, 'hypotheses', None))
        if is_regeneration:
            print("Clearing persisted generated hypotheses before regeneration")
            generator.clear_persisted_generated_hypotheses()

        # Use CLI method to generate hypotheses
        n = generator.hypothesis_config.n_candidate_hypotheses
        guidance_payload = guidance if isinstance(guidance, str) else None
        candidate_hypotheses = generator.generate_candidates(n, guidance=guidance_payload)

        if _is_generation_cancelled(session_id, token):
            generator.hypotheses = previous_state['hypotheses']
            generator.last_generation_citations = previous_state['last_generation_citations']
            generator.citation_documents = previous_state['citation_documents']
            if getattr(generator, 'hypothesis_gen_agent', None):
                generator.hypothesis_gen_agent.messages = previous_state['agent_messages']
                generator.hypothesis_gen_agent.last_response_citations = previous_state['agent_last_response_citations']
            return jsonify({'success': False, 'error': 'Generation cancelled', 'cancelled': True})

        # Full regeneration replaces all hypotheses, so rotate every state id and
        # assign fresh stable thread ids (generate_candidates already dropped the
        # old threads and captured the new generation-time baseline snapshot).
        state_ids = _reset_hypothesis_state_ids(generator)
        _reset_hypothesis_thread_ids(generator)
        generator.persist_generated_hypotheses()

        # Parse hypotheses for display
        parsed_hypotheses = []
        for i, hyp in enumerate(generator.hypotheses, 1):
            sections = generator.parse_hypothesis_sections(hyp)
            parsed_hypotheses.append({
                'index': i,
                'state_id': state_ids[i - 1] if i - 1 < len(state_ids) else None,
                'title': sections.get('title'),
                'full_text': hyp,
                'hypothesis': sections['hypothesis'],
                'justification': sections['justification'],
                'interpretation': sections['interpretation'],
                'plan': sections.get('plan'),
                'thread_recoverable': bool(
                    _hypothesis_thread_key_for_index(generator, i)
                ),
            })

        log_full_event_to_s3(
            'hypothesis_generate',
            {
                'model': model_name,
                'count': len(parsed_hypotheses),
                'hypotheses': parsed_hypotheses,
                'guidance': guidance_payload or None,
                'citations': getattr(generator, 'last_generation_citations', []),
                'documents': getattr(generator, 'citation_documents', []),
            },
        )

        generation_id = _increment_generation_id()
        # Append this generated set exactly as the scientist saw it.
        generation_result_event_id = _studio_record_generation(
            generator, parsed_hypotheses, generation_id
        )
        request_id = (getattr(g, 'operation_debug', {}) or {}).get('request_id')
        print(
            f"[api_generate] completed request={request_id} "
            f"session={_session_debug_id()} generation_id={generation_id} hypotheses={len(parsed_hypotheses)}"
        )
        return jsonify({
            'success': True,
            'hypotheses': parsed_hypotheses,
            'count': len(generator.hypotheses),
            'citations': getattr(generator, 'last_generation_citations', []),
            'documents': getattr(generator, 'citation_documents', []),
            'generation_id': generation_id,
            'result_event_id': generation_result_event_id,
            'session_debug_id': _session_debug_id(),
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        if _is_generation_cancelled(session_id, token):
            generator.hypotheses = previous_state['hypotheses']
            generator.last_generation_citations = previous_state['last_generation_citations']
            generator.citation_documents = previous_state['citation_documents']
            if getattr(generator, 'hypothesis_gen_agent', None):
                generator.hypothesis_gen_agent.messages = previous_state['agent_messages']
                generator.hypothesis_gen_agent.last_response_citations = previous_state['agent_last_response_citations']
            return jsonify({'success': False, 'error': 'Generation cancelled', 'cancelled': True})
        return jsonify({'success': False, 'error': str(e)})
@app.route('/api/cancel_generation', methods=['POST'])
def api_cancel_generation():
    """Request cancellation of an in-flight hypothesis generation."""
    if _is_audit_only_mode():
        return _reject_audit_only('Hypothesis generation')
    session_id = session.get('session_id')
    if not session_id:
        return jsonify({'success': False, 'error': 'Session unavailable'})

    _request_generation_cancel(session_id)
    return jsonify({'success': True})


@app.route('/api/cancel_regeneration', methods=['POST'])
def api_cancel_regeneration():
    """Request cancellation of an in-flight single-hypothesis regeneration."""
    if _is_audit_only_mode():
        return _reject_audit_only('Hypothesis regeneration')
    session_id = session.get('session_id')
    if not session_id:
        return jsonify({'success': False, 'error': 'Session unavailable'})

    data = request.json or {}
    raw_index = data.get('index')
    try:
        index = int(raw_index) if raw_index is not None else None
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Invalid hypothesis index'})
    cancelled = _request_hypothesis_regeneration_cancel(session_id, index=index)
    return jsonify({'success': True, 'cancelled': cancelled})


@app.route('/api/cancel_report_chat', methods=['POST'])
def api_cancel_report_chat():
    """Request cancellation of an in-flight report chat."""
    if _is_audit_only_mode():
        return _reject_audit_only('Report chat')

    session_id = session.get('session_id')
    if not session_id:
        return jsonify({'success': False, 'error': 'Session unavailable'})

    data = request.json or {}
    request_id = sanitize_field(data.get('request_id'), 120)
    cancelled = _request_report_chat_cancel(session_id, request_id=request_id or None)
    return jsonify({'success': True, 'cancelled': cancelled})


@app.route('/api/cancel_hypothesis_thread', methods=['POST'])
def api_cancel_hypothesis_thread():
    """Request cancellation of an in-flight Hypothesis Studio chat/revision."""
    if _is_audit_only_mode():
        return _reject_audit_only('Hypothesis Studio chat')

    session_id = session.get('session_id')
    if not session_id:
        return jsonify({'success': False, 'error': 'Session unavailable'})

    data = request.json or {}
    request_id = sanitize_field(data.get('request_id'), 120)
    index = data.get('index')
    cancelled = _request_hypothesis_thread_cancel(
        session_id,
        request_id=request_id or None,
        index=index,
    )
    return jsonify({'success': True, 'cancelled': cancelled})


@app.route('/api/ask', methods=['POST'])
@with_session_operation_lock('api_ask')
def api_ask():
    """Ask the agent a question using CLI method."""
    if _is_audit_only_mode():
        return _reject_audit_only('Hypothesis chat')
    generator = get_generator()
    if not generator:
        return jsonify({'success': False, 'error': 'No active session'})

    data = request.json
    question = data.get('question')
    hypothesis_index = data.get('hypothesis_index')

    if not question:
        return jsonify({'success': False, 'error': 'No question provided'})

    try:
        # Use CLI method to ask question
        response = generator.ask_agent_question(question, hypothesis_index=hypothesis_index)

        if response:
            conversation_snapshot = _get_hypothesis_conversation_snapshot(generator)
            _write_interaction_log('ask', {
                'question': question,
                'answer': response,
                'conversation_messages': conversation_snapshot,
            })
            log_full_event_to_s3(
                'ask',
                {
                    'question': question,
                    'answer': response,
                    'citations': getattr(generator, 'last_answer_citations', []),
                    'documents': getattr(generator, 'citation_documents', []),
                    'conversation_messages': conversation_snapshot,
                },
            )
            log_event('ask', {'question': question})
            return jsonify({
                'success': True,
                'answer': response,
                'citations': getattr(generator, 'last_answer_citations', []),
                'documents': getattr(generator, 'citation_documents', []),
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to get response'})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/chat_context', methods=['GET'])
def api_chat_context():
    """Expose the latest hypotheses and reports for the chat sidebar."""
    generator = get_generator()
    if not generator:
        return jsonify({'success': False, 'error': 'No active session'})

    try:
        state_ids = _ensure_hypothesis_state_ids(generator)
        hypotheses_payload = []
        for idx, hyp in enumerate(generator.hypotheses, 1):
            sections = generator.parse_hypothesis_sections(hyp)
            hypotheses_payload.append({
                'index': idx,
                'state_id': state_ids[idx - 1] if idx - 1 < len(state_ids) else None,
                'title': sections.get('title'),
                'hypothesis': sections.get('hypothesis'),
                'justification': sections.get('justification'),
                'interpretation': sections.get('interpretation'),
                'plan': sections.get('plan'),
                'full_text': hyp
            })

        combined_background = (
            _get_audit_only_report_text(generator)
            if _is_audit_only_mode()
            else generator._build_combined_background()
        )
        round_report = None if _is_audit_only_mode() else generator.round_report
        memory_report = generator.memory_report
        background_literature = generator.deep_research_background
        research_paper = _load_research_paper_text(generator) if _is_audit_only_mode() else None

        return jsonify({
            'success': True,
            'dataset_description': generator.dataset_description,
            'task_description': generator.task_description,
            'background_report': combined_background,
            'round_report': round_report,
            'memory_report': memory_report,
            'research_paper': research_paper,
            'background_literature': background_literature,
            'include_background_literature': generator.include_background_literature,
            'hypotheses': hypotheses_payload,
            'documents': getattr(generator, 'citation_documents', []),
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


def _run_code_chat_with_timeout(timeout_secs, **kwargs):
    """Run run_code_chat with a SIGALRM-based timeout when available.

    If the call exceeds *timeout_secs* a TimeoutError is raised, giving Flask
    a chance to return a clean error response instead of letting gunicorn kill
    the worker and serve a 500. SIGALRM can only be installed from Python's
    main thread, so threaded local Flask requests skip this wrapper and rely on
    the underlying SDK/request timeouts instead.
    """
    if threading.current_thread() is not threading.main_thread():
        log_memory(
            "run_code_chat: skipping SIGALRM timeout outside main thread",
            level="debug",
        )
        return run_code_chat(**kwargs)

    def _handler(signum, frame):
        raise TimeoutError(
            f"Query timed out after {timeout_secs}s. "
            "Try breaking the question into smaller steps."
        )

    old_handler = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(timeout_secs)
    try:
        return run_code_chat(**kwargs)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def _response_time_payload(started_at: float) -> dict:
    elapsed_ms = max(0, int(round((perf_counter() - started_at) * 1000)))
    if elapsed_ms >= 1000:
        response_time = f"{elapsed_ms / 1000:.2f}s"
    else:
        response_time = f"{elapsed_ms} ms"
    return {
        'response_time_ms': elapsed_ms,
        'response_time': response_time,
    }


def _prune_audit_chat_jobs(now: float | None = None) -> None:
    """Drop old completed audit-chat jobs from the local in-memory registry."""
    cutoff = (now or time.time()) - AUDIT_CHAT_JOB_TTL_SECS
    with AUDIT_CHAT_JOBS_LOCK:
        stale_ids = [
            job_id
            for job_id, job in AUDIT_CHAT_JOBS.items()
            if job.get('updated_at', job.get('created_at', 0)) < cutoff
        ]
        for job_id in stale_ids:
            AUDIT_CHAT_JOBS.pop(job_id, None)


def _flask_response_to_json_payload(result) -> tuple[dict, int]:
    """Normalize a Flask view return value into a JSON payload + status code."""
    status_code = 200
    response = result
    if isinstance(result, tuple):
        response = result[0]
        if len(result) > 1 and isinstance(result[1], int):
            status_code = result[1]
    if hasattr(response, 'status_code'):
        status_code = response.status_code
    payload = None
    if hasattr(response, 'get_json'):
        payload = response.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = {'success': False, 'error': 'Audit chat returned an unreadable response.'}
    return payload, status_code


def _is_audit_chat_job_cancel_requested(job_id: str | None) -> bool:
    if not job_id:
        return False
    with AUDIT_CHAT_JOBS_LOCK:
        job = AUDIT_CHAT_JOBS.get(job_id)
        return bool(job and job.get('cancel_requested'))


def _run_audit_chat_job(job_id: str) -> None:
    """Run an audit chat request in the background so browser reloads can reattach."""
    with AUDIT_CHAT_JOBS_LOCK:
        job = AUDIT_CHAT_JOBS.get(job_id)
        if not job:
            return
        if job.get('cancel_requested') or job.get('status') == 'cancelled':
            job['status'] = 'cancelled'
            job['result'] = {
                'success': False,
                'error': 'Request cancelled',
                'cancelled': True,
                **_response_time_payload(job.get('perf_started_at', perf_counter())),
            }
            job['updated_at'] = time.time()
            return
        job['status'] = 'running'
        job['updated_at'] = time.time()
        request_payload = copy.deepcopy(job.get('request') or {})
        session_snapshot = copy.deepcopy(job.get('session') or {})

    try:
        with app.test_request_context('/api/chat', method='POST', json=request_payload):
            for key, value in session_snapshot.items():
                session[key] = value
            g.operation_debug = {
                'request_id': uuid.uuid4().hex[:12],
                'operation': 'api_audit_chat_job',
                'session_debug_id': _session_debug_id(),
                'lock_wait_ms': 0.0,
            }
            g.audit_chat_job_id = job_id
            result = api_chat.__wrapped__()
            payload, status_code = _flask_response_to_json_payload(result)

        with AUDIT_CHAT_JOBS_LOCK:
            job = AUDIT_CHAT_JOBS.get(job_id)
            if job:
                if job.get('cancel_requested') or job.get('status') == 'cancelled' or payload.get('cancelled'):
                    job['status'] = 'cancelled'
                    job['result'] = {
                        'success': False,
                        'error': 'Request cancelled',
                        'cancelled': True,
                        **_response_time_payload(job.get('perf_started_at', perf_counter())),
                    }
                else:
                    job['status'] = 'succeeded' if status_code < 400 and payload.get('success') else 'failed'
                    job['result'] = payload
                job['status_code'] = status_code
                job['updated_at'] = time.time()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        with AUDIT_CHAT_JOBS_LOCK:
            job = AUDIT_CHAT_JOBS.get(job_id)
            if job:
                if job.get('cancel_requested') or job.get('status') == 'cancelled':
                    job['status'] = 'cancelled'
                    job['result'] = {
                        'success': False,
                        'error': 'Request cancelled',
                        'cancelled': True,
                        **_response_time_payload(job.get('perf_started_at', perf_counter())),
                    }
                else:
                    job['status'] = 'failed'
                    job['result'] = {
                        'success': False,
                        'error': f"Unexpected server error ({type(exc).__name__}): {str(exc) or type(exc).__name__}",
                        'error_type': 'server_exception',
                    }
                job['status_code'] = 500
                job['updated_at'] = time.time()


@app.route('/api/audit_chat_jobs', methods=['POST'])
def api_audit_chat_jobs():
    """Submit an audit chat request as a local background job."""
    if not (_is_audit_mode() or session.get('v2_active_project_slug')):
        return jsonify({'success': False, 'error': 'Audit chat jobs are only available in audit mode.'}), 403

    session_id = session.get('session_id')
    if not session_id:
        return jsonify({'success': False, 'error': 'No active session'}), 401

    data = request.json or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({'success': False, 'error': 'Message is required'}), 400

    _prune_audit_chat_jobs()
    requested_job_id = (data.get('job_id') or '').strip()
    job_id = requested_job_id if re.fullmatch(r'[a-fA-F0-9]{32}', requested_job_id) else uuid.uuid4().hex
    request_payload = {
        'message': message,
        'mode': 'audit',
    }
    if isinstance(data.get('messages'), list):
        request_payload['messages'] = data.get('messages')

    now = time.time()
    with AUDIT_CHAT_JOBS_LOCK:
        existing_job = AUDIT_CHAT_JOBS.get(job_id)
        if existing_job and existing_job.get('session_id') == session_id:
            return jsonify({'success': True, 'job_id': job_id, 'status': existing_job.get('status') or 'pending'})
        AUDIT_CHAT_JOBS[job_id] = {
            'id': job_id,
            'session_id': session_id,
            'status': 'pending',
            'request': request_payload,
            'session': dict(session),
            'created_at': now,
            'updated_at': now,
            'perf_started_at': perf_counter(),
            'result': None,
            'status_code': None,
            'cancel_requested': False,
        }

    thread = threading.Thread(target=_run_audit_chat_job, args=(job_id,), daemon=True)
    thread.start()
    return jsonify({'success': True, 'job_id': job_id, 'status': 'pending'})


@app.route('/api/audit_chat_jobs/<job_id>', methods=['GET'])
def api_audit_chat_job_status(job_id: str):
    """Return the current status/result for a local audit chat job."""
    session_id = session.get('session_id')
    if not session_id:
        return jsonify({'success': False, 'error': 'No active session'}), 401

    _prune_audit_chat_jobs()
    with AUDIT_CHAT_JOBS_LOCK:
        job = copy.deepcopy(AUDIT_CHAT_JOBS.get(job_id))

    if not job or job.get('session_id') != session_id:
        return jsonify({'success': False, 'error': 'Audit chat job not found'}), 404

    return jsonify({
        'success': True,
        'job_id': job_id,
        'status': job.get('status') or 'pending',
        'result': job.get('result'),
        'created_at': job.get('created_at'),
        'updated_at': job.get('updated_at'),
    })


@app.route('/api/audit_chat_jobs/<job_id>/cancel', methods=['POST'])
def api_audit_chat_job_cancel(job_id: str):
    """Request cancellation of a local audit chat job."""
    session_id = session.get('session_id')
    if not session_id:
        return jsonify({'success': False, 'error': 'No active session'}), 401

    with AUDIT_CHAT_JOBS_LOCK:
        job = AUDIT_CHAT_JOBS.get(job_id)
        if not job or job.get('session_id') != session_id:
            return jsonify({'success': False, 'error': 'Audit chat job not found'}), 404
        status = job.get('status') or 'pending'
        if status in {'succeeded', 'failed', 'cancelled'}:
            return jsonify({'success': True, 'cancelled': status == 'cancelled', 'status': status})
        job['cancel_requested'] = True
        job['status'] = 'cancelled'
        job['result'] = {
            'success': False,
            'error': 'Request cancelled',
            'cancelled': True,
            **_response_time_payload(job.get('perf_started_at', perf_counter())),
        }
        job['updated_at'] = time.time()

    return jsonify({'success': True, 'cancelled': True, 'status': 'cancelled'})


@app.route('/api/chat', methods=['POST'])
@with_session_operation_lock('api_chat')
def api_chat():
    """Unified chat endpoint that can talk to the hypothesis agent or the code sub-agent."""
    mem_before = log_memory("api_chat: request received")
    request_started_at = perf_counter()

    generator = get_or_restore_generator()
    if not generator:
        return jsonify({'success': False, 'error': 'No active session', **_response_time_payload(request_started_at)})

    data = request.json or {}
    message = (data.get('message') or '').strip()
    hypothesis_index = data.get('hypothesis_index')
    mode = (data.get('mode') or 'hypothesis').lower()
    client_request_id = sanitize_field(data.get('request_id'), 120)
    request_messages = _sanitize_restored_chat_messages(data.get('messages')) if isinstance(data.get('messages'), list) else None
    request_id = (getattr(g, 'operation_debug', {}) or {}).get('request_id')
    print(
        f"[api_chat] request={request_id} session={_session_debug_id()} "
        f"mode={mode} hypothesis_index={hypothesis_index}"
    )

    if not message:
        return jsonify({'success': False, 'error': 'Message is required', **_response_time_payload(request_started_at)})

    active_report_key = None
    full_conversation_messages = []
    hypothesis_thread_session_id = session.get('session_id')
    hypothesis_thread_request_id = None
    try:
        if _is_audit_only_mode() or _is_audit_mode() or mode == 'audit':
            # Resolve code directory
            code_dir = None
            if getattr(generator, 'code_dir', None):
                code_dir = generator.code_dir
            else:
                code_dir = _get_default_config_value('code_dir', CLI_CODE_DIR)

            if not code_dir:
                return jsonify({'success': False, 'error': 'Code chat unavailable (no code_dir configured)', **_response_time_payload(request_started_at)})

            code_dir_path = Path(code_dir).expanduser().resolve()
            working_dir = code_dir_path.parent
            model_name = _resolve_code_chat_model(generator)

            report_key = _report_chat_context_key('background')
            active_report_key = report_key
            # Get or create conversation history
            if not hasattr(generator, 'report_code_chat_conversations') or generator.report_code_chat_conversations is None:
                generator.report_code_chat_conversations = {}
            conversation = generator.report_code_chat_conversations.get(report_key)
            legacy_report_key = 'background:summary'
            if conversation is None and report_key != legacy_report_key:
                legacy_conversation = generator.report_code_chat_conversations.get(legacy_report_key)
                if isinstance(legacy_conversation, CodeChatConversation):
                    conversation = legacy_conversation
                    generator.report_code_chat_conversations[report_key] = legacy_conversation
                    generator.report_code_chat_conversations.pop(legacy_report_key, None)
            if conversation is None:
                conversation = CodeChatConversation()
                generator.report_code_chat_conversations[report_key] = conversation
            if request_messages is not None:
                conversation.messages = [{'role': msg['role'], 'content': msg['content']} for msg in request_messages]
                conversation.sdk_session_id = None
            report_prompt_sections = []
            checkpoint_inventory = _build_audit_checkpoint_inventory(generator)
            if checkpoint_inventory:
                report_prompt_sections.append(checkpoint_inventory)

            report_text = _get_audit_only_report_text(generator)
            if report_text:
                prefix = (
                    "\nBelow is a report of research that has been done so far by the agent. "
                    "Use it as useful prose context, but prefer the authoritative checkpoint "
                    "filesystem inventory above when deciding which rounds exist."
                )
                report_prompt_sections.append(f"{prefix}\n## Background Report\n{report_text}\n")

            research_paper_text = _load_research_paper_text(generator)
            if research_paper_text:
                report_prompt_sections.append(
                    "\nBelow is the latest research paper for this session. "
                    "Use it to answer questions, and say when the paper does not contain the answer.\n"
                    f"## Research Paper\n{research_paper_text}\n"
                )

            report_prompt = "\n".join(report_prompt_sections)

            # Memory check before expensive code chat operation
            is_high, current_mem = check_memory_threshold(threshold_mb=2500.0)
            if is_high:
                log_memory(f"api_chat: WARNING - memory at {current_mem:.1f}MB before audit code_chat", level="warn")

            log_memory("api_chat: starting run_code_chat for audit mode (Claude Agent SDK)")
            with MemoryTracker("api_chat:run_code_chat:audit", warn_threshold_mb=400.0) as tracker:
                reply = _run_code_chat_with_timeout(
                    CODE_CHAT_TIMEOUT_SECS,
                    question=message,
                    working_dir=working_dir,
                    model=model_name,
                    include_trace=True,
                    report_prompt=report_prompt,
                    multi_agent=True,
                    conversation=conversation,
                    cancel_requested=lambda: _is_audit_chat_job_cancel_requested(getattr(g, 'audit_chat_job_id', None)),
                )
            log_memory(f"api_chat: run_code_chat audit completed (delta={tracker.delta_mb:+.1f}MB)")

            source = 'code'
            if isinstance(reply, dict) and reply.get('cancelled'):
                return jsonify({
                    'success': False,
                    'error': 'Request cancelled',
                    'cancelled': True,
                    **_response_time_payload(request_started_at),
                })
            if isinstance(reply, dict) and reply.get('error'):
                reset_hint = reply.get('reset_hint')
                return jsonify({
                    'success': False,
                    'error': reply['error'],
                    'error_type': reply.get('error_type'),
                    'reset_hint': reset_hint,
                    **_response_time_payload(request_started_at),
                })
            thinking = reply.get('thinking') if isinstance(reply, dict) else None
            reply_text = reply.get('answer') if isinstance(reply, dict) else reply
            full_conversation_messages = _get_report_conversation_snapshot(generator, report_key)
        elif mode == 'code':
            # Memory check before expensive code chat operation
            is_high, current_mem = check_memory_threshold(threshold_mb=2500.0)
            if is_high:
                log_memory(f"api_chat: WARNING - memory at {current_mem:.1f}MB before code_chat", level="warn")

            # Resolve code directory for code chat
            code_dir = None
            if getattr(generator, 'code_dir', None):
                code_dir = generator.code_dir
            else:
                code_dir = _get_default_config_value('code_dir', CLI_CODE_DIR)

            if not code_dir:
                return jsonify({'success': False, 'error': 'Code chat unavailable (no code_dir configured)', **_response_time_payload(request_started_at)})

            code_dir_path = Path(code_dir).expanduser().resolve()
            working_dir = code_dir_path.parent

            log_memory("api_chat: starting run_code_chat (Claude Agent SDK)")
            with MemoryTracker("api_chat:run_code_chat", warn_threshold_mb=400.0) as tracker:
                reply = _run_code_chat_with_timeout(
                    CODE_CHAT_TIMEOUT_SECS,
                    question=message,
                    working_dir=working_dir,
                    model=_resolve_code_chat_model(generator),
                    include_trace=True,
                )
            log_memory(f"api_chat: run_code_chat completed (delta={tracker.delta_mb:+.1f}MB)")

            source = 'code'
            if isinstance(reply, dict) and reply.get('error'):
                reset_hint = reply.get('reset_hint')
                return jsonify({
                    'success': False,
                    'error': reply['error'],
                    'error_type': reply.get('error_type'),
                    'reset_hint': reset_hint,
                    **_response_time_payload(request_started_at),
                })
            thinking = reply.get('thinking') if isinstance(reply, dict) else None
            reply_text = reply.get('answer') if isinstance(reply, dict) else reply
        else:
            hypothesis_thread_request_id = client_request_id or request_id or os.urandom(8).hex()
            _begin_hypothesis_thread_request(
                hypothesis_thread_session_id,
                hypothesis_thread_request_id,
                index=hypothesis_index,
                kind='discuss',
            )
            log_memory("api_chat: calling ask_agent_question (hypothesis mode)")
            # V2: a focused-hypothesis question runs inside that hypothesis's own
            # refinement thread (V1 baseline seed + only this hypothesis's prior
            # exchanges). Unscoped questions keep the legacy shared conversation.
            thread_idx = None
            try:
                candidate_idx = int(hypothesis_index) if hypothesis_index is not None else None
            except (TypeError, ValueError):
                candidate_idx = None
            if candidate_idx and 1 <= candidate_idx <= len(getattr(generator, 'hypotheses', []) or []):
                thread_idx = candidate_idx
            if thread_idx is not None:
                version_reference_context = _hypothesis_version_reference_context(
                    generator, thread_idx, message,
                )
                with _hypothesis_thread_ctx(generator, thread_idx, restore=True):
                    reply = generator.ask_agent_question(
                        message,
                        hypothesis_index=thread_idx,
                        version_reference_context=version_reference_context,
                    )
            else:
                reply = generator.ask_agent_question(message, hypothesis_index=hypothesis_index)
            log_memory("api_chat: ask_agent_question completed")
            source = 'agent'
            thinking = None
            reply_text = reply
            full_conversation_messages = _get_hypothesis_conversation_snapshot(generator)

        if not reply_text:
            if source == 'agent':
                _end_hypothesis_thread_request(hypothesis_thread_session_id, hypothesis_thread_request_id)
            return jsonify({'success': False, 'error': 'Failed to get response', **_response_time_payload(request_started_at)})

        timing_payload = _response_time_payload(request_started_at)

        if source == 'agent' and _is_hypothesis_thread_cancelled(hypothesis_thread_session_id, hypothesis_thread_request_id):
            _end_hypothesis_thread_request(hypothesis_thread_session_id, hypothesis_thread_request_id)
            return jsonify({
                'success': False,
                'error': 'Request cancelled',
                'cancelled': True,
                **timing_payload,
            })

        # Write to local durable interaction log (always-on, no external deps).
        # This is the primary research data store – it captures every exchange.
        _write_interaction_log('chat', {
            'mode': mode,
            'source': source,
            'chat_scope': active_report_key or ('hypothesis' if source == 'agent' else mode),
            'message': message,
            'reply': reply_text,
            'agent_trace': thinking,  # full tool-call trace from run_code_chat
            'conversation_messages': full_conversation_messages,
            'response_time_ms': timing_payload['response_time_ms'],
        })

        log_full_event_to_s3(
            'chat',
            {
                'mode': mode,
                'source': source,
                'chat_scope': active_report_key or ('hypothesis' if source == 'agent' else mode),
                'message': message,
                'reply': reply_text,
                'thinking': thinking,
                'citations': getattr(generator.hypothesis_gen_agent, 'last_response_citations', []) if source == 'agent' else [],
                'documents': getattr(generator, 'citation_documents', []),
                'conversation_messages': full_conversation_messages,
                'response_time_ms': timing_payload['response_time_ms'],
            },
            source=source,
        )
        log_event('chat', {
            'mode': mode,
            'source': source,
            'message': message,
            'reply': (reply_text or '')[:2000],  # truncated for DB column safety
        })
        # Durable chronology: record post-generation hypothesis-mode Q&A only.
        # This is the non-mutating ask path (source == 'agent'); code/audit chat
        # is not part of the Hypothesis Studio conversation and is never recorded.
        if source == 'agent':
            _studio_record_chat(generator, message, reply_text, hypothesis_index)
            _end_hypothesis_thread_request(hypothesis_thread_session_id, hypothesis_thread_request_id)
        mem_after = log_memory("api_chat: response ready")
        return jsonify({
            'success': True,
            'reply': reply_text,
            'mode': mode,
            'source': source,
            'thinking': thinking,
            'citations': getattr(generator.hypothesis_gen_agent, 'last_response_citations', []) if source == 'agent' else [],
            'documents': getattr(generator, 'citation_documents', []),
            '_debug_memory': {'before_mb': round(mem_before, 1), 'after_mb': round(mem_after, 1)},
            **timing_payload,
        })

    except TimeoutError as e:
        _end_hypothesis_thread_request(hypothesis_thread_session_id, hypothesis_thread_request_id)
        log_memory("api_chat: TimeoutError - query exceeded time limit", level="error")
        return jsonify({
            'success': False,
            'error': str(e),
            'error_type': 'timeout',
            **_response_time_payload(request_started_at),
        })
    except MemoryError as e:
        _end_hypothesis_thread_request(hypothesis_thread_session_id, hypothesis_thread_request_id)
        log_memory("api_chat: MemoryError caught", level="error")
        return jsonify({'success': False, 'error': f'Memory limit exceeded: {str(e)}', **_response_time_payload(request_started_at)})
    except Exception as e:
        _end_hypothesis_thread_request(hypothesis_thread_session_id, hypothesis_thread_request_id)
        import traceback
        traceback.print_exc()
        log_memory(f"api_chat: exception - {type(e).__name__}", level="error")
        request_id = (getattr(g, 'operation_debug', {}) or {}).get('request_id')
        message = str(e).strip() or type(e).__name__
        return jsonify({
            'success': False,
            'error': f"Unexpected server error ({type(e).__name__}): {message}",
            'error_type': 'server_exception',
            'request_id': request_id,
            **_response_time_payload(request_started_at),
        })


@app.route('/api/audit_chat_restore', methods=['POST'])
@with_session_operation_lock('api_audit_chat_restore')
def api_audit_chat_restore():
    """Restore the audit chat conversation state from client-provided transcript."""
    if not (_is_audit_mode() or session.get('v2_active_project_slug')):
        return jsonify({'success': False, 'error': 'Audit chat restore is only available in audit mode.'}), 403

    generator = get_or_restore_generator()
    if not generator:
        return jsonify({'success': False, 'error': 'No active session'})

    data = request.json or {}
    force_restore = bool(data.get('force'))
    messages = _sanitize_restored_chat_messages(data.get('messages'))
    report_key = _report_chat_context_key('background')

    if not hasattr(generator, 'report_code_chat_conversations') or generator.report_code_chat_conversations is None:
        generator.report_code_chat_conversations = {}

    existing = generator.report_code_chat_conversations.get(report_key)
    existing_messages = getattr(existing, 'messages', None) if existing else None
    if isinstance(existing_messages, list) and existing_messages and not force_restore:
        return jsonify({
            'success': True,
            'restored': False,
            'reason': 'conversation_already_initialized',
            'message_count': len(existing_messages),
        })

    conversation = existing if isinstance(existing, CodeChatConversation) else CodeChatConversation()
    conversation.messages = [{'role': msg['role'], 'content': msg['content']} for msg in messages]
    generator.report_code_chat_conversations[report_key] = conversation
    conversation_snapshot = _full_chat_messages_for_logging(conversation.messages)

    _write_interaction_log('audit_chat_restore', {
        'report_key': report_key,
        'force': force_restore,
        'message_count': len(conversation_snapshot),
        'conversation_messages': conversation_snapshot,
    })
    log_full_event_to_s3(
        'audit_chat_restore',
        {
            'report_key': report_key,
            'force': force_restore,
            'message_count': len(conversation_snapshot),
            'conversation_messages': conversation_snapshot,
        },
        source='restore',
    )

    log_event('audit_chat_restored', {
        'message_count': len(conversation_snapshot),
        'force': force_restore,
    })

    return jsonify({
        'success': True,
        'restored': True,
        'message_count': len(conversation_snapshot),
    })


@app.route('/api/reset_chat_context', methods=['POST'])
@with_session_operation_lock('api_reset_chat_context')
def api_reset_chat_context():
    """Reset conversation context for chat agents."""
    data = request.json or {}
    scope = (data.get('scope') or 'hypothesis').lower()
    report_type = _normalize_report_type_for_chat(data.get('report_type'))
    report_key = _report_chat_context_key(report_type)

    if _is_audit_only_mode() and scope != 'report':
        return _reject_audit_only('Chat reset')

    generator = get_or_restore_generator()
    if not generator:
        return jsonify({'success': False, 'error': 'No active session'})

    if scope == 'hypothesis':
        conversation_snapshot = _get_hypothesis_conversation_snapshot(generator)
        _write_interaction_log('chat_reset', {
            'scope': 'hypothesis',
            'message_count': len(conversation_snapshot),
            'conversation_messages': conversation_snapshot,
        })
        log_full_event_to_s3(
            'chat_reset',
            {
                'scope': 'hypothesis',
                'message_count': len(conversation_snapshot),
                'conversation_messages': conversation_snapshot,
            },
            source='system',
        )
        if getattr(generator, 'hypothesis_gen_agent', None):
            try:
                generator.reset_hypothesis_chat_context(reason='api_reset_chat_context')
            except Exception as exc:
                print(f"[api_reset_chat_context] context reseed failed: {exc}; falling back to raw reset")
                generator.hypothesis_gen_agent.reset_conversation()
        generator.qa_history = []
        generator.last_answer_citations = []
        log_event('chat_reset', {'scope': 'hypothesis', 'message_count': len(conversation_snapshot)})
        return jsonify({'success': True})

    if scope == 'report':
        conversation_snapshot = _get_report_conversation_snapshot(generator, report_key)
        legacy_report_key = 'background:summary' if report_key == _report_chat_context_key('background') else None
        if legacy_report_key:
            legacy_snapshot = _get_report_conversation_snapshot(generator, legacy_report_key)
            if len(legacy_snapshot) > len(conversation_snapshot):
                conversation_snapshot = legacy_snapshot
        _write_interaction_log('chat_reset', {
            'scope': 'report',
            'report_type': report_type,
            'message_count': len(conversation_snapshot),
            'conversation_messages': conversation_snapshot,
        })
        log_full_event_to_s3(
            'chat_reset',
            {
                'scope': 'report',
                'report_type': report_type,
                'message_count': len(conversation_snapshot),
                'conversation_messages': conversation_snapshot,
            },
            source='system',
        )
        report_text, report_label, _ = _get_persistent_report_text_for_chat(generator, report_type)
        if _is_audit_only_mode():
            research_paper_text = _load_research_paper_text(generator)
            report_text = _get_audit_only_report_text(generator)
            if research_paper_text:
                report_text = f"{report_text}\n\n## Research Paper\n{research_paper_text}"
                report_label = "Background Report + Research Paper"
            else:
                report_label = "Background Report"

        generator.report_chat_agents.pop(report_key, None)
        generator.report_chat_context_hashes.pop(report_key, None)
        generator.report_qa_history.pop(report_key, None)
        if legacy_report_key:
            generator.report_chat_agents.pop(legacy_report_key, None)
            generator.report_chat_context_hashes.pop(legacy_report_key, None)
            generator.report_qa_history.pop(legacy_report_key, None)
        if hasattr(generator, 'report_code_chat_conversations') and generator.report_code_chat_conversations:
            generator.report_code_chat_conversations.pop(report_key, None)
            if legacy_report_key:
                generator.report_code_chat_conversations.pop(legacy_report_key, None)

        if report_text and report_label:
            generator._get_report_chat_agent(report_key, report_label, report_text)

        log_event('chat_reset', {'scope': 'report', 'report_type': report_type, 'message_count': len(conversation_snapshot)})
        return jsonify({'success': True})

    return jsonify({'success': False, 'error': 'Invalid scope'}), 400


@app.route('/api/report_chat', methods=['POST'])
@with_session_operation_lock('api_report_chat')
def api_report_chat():
    """Chat endpoint scoped to the research paper + background reports, with code mode available."""
    if _is_audit_only_mode():
        return _reject_audit_only('Report chat')
    # Memory monitoring: check before starting expensive operation
    mem_before = log_memory("report_chat: request received")
    request_started_at = perf_counter()
    is_high, current_mem = check_memory_threshold(threshold_mb=2500.0)
    if is_high:
        log_memory(f"report_chat: WARNING - memory already at {current_mem:.1f}MB before starting", level="warn")

    data = request.json or {}
    message = (data.get('message') or '').strip()
    mode = (data.get('mode') or 'code').lower()
    report_type = _normalize_report_type_for_chat(data.get('report_type'))
    report_view = (data.get('report_view') or 'summary').lower()
    report_key = _report_chat_context_key(report_type)
    client_request_id = sanitize_field(data.get('request_id'), 120)

    if not message:
        return jsonify({'success': False, 'error': 'Message is required', **_response_time_payload(request_started_at)})

    session_id = session.get('session_id')
    if not session_id:
        return jsonify({'success': False, 'error': 'Session unavailable', **_response_time_payload(request_started_at)})

    if not client_request_id:
        client_request_id = os.urandom(8).hex()
    _begin_report_chat_request(session_id, client_request_id)

    generator = get_or_restore_generator()

    full_conversation_messages = []
    try:
        report_text = None
        report_label = None
        if generator:
            report_text, report_label, _ = _get_persistent_report_text_for_chat(generator, report_type)

        if mode != 'code':
            return jsonify({'success': False, 'error': 'Only code chat is supported in report view', **_response_time_payload(request_started_at)})

        # Reuse the existing code chat helper
        code_dir = None
        if generator and getattr(generator, 'code_dir', None):
            code_dir = generator.code_dir
        else:
            code_dir = _get_default_config_value('code_dir', CLI_CODE_DIR)

        if not code_dir:
            return jsonify({'success': False, 'error': 'Code chat unavailable (no code_dir configured)', **_response_time_payload(request_started_at)})

        code_dir_path = Path(code_dir).expanduser().resolve()
        working_dir = code_dir_path.parent

        if not working_dir:
            return jsonify({'success': False, 'error': 'Code chat unavailable (no code_dir configured)', **_response_time_payload(request_started_at)})

        model_name = _resolve_code_chat_model(generator)

        report_prompt_sections = []
        if report_text and report_label:
            if report_type == 'background':
                prefix = "\nBelow is a report of all the research that has been done so far by the agent. Focus on answering questions about this report, and use the code and checkpoints to support your answers."
            elif report_type == 'round':
                prefix = "\nBelow is a report of the research that has been done in the previous round of research only. Focus on answering questions about this report, and use the code and checkpoints to support your answers."
            else:
                raise ValueError(f"Invalid report type: {report_type}")

            report_prompt_sections.append(f"{prefix}\n## {report_label}\n{report_text}\n")

        if _is_audit_only_mode():
            research_paper_text = _load_research_paper_text(generator)
            if research_paper_text:
                report_prompt_sections.append(
                    "\nBelow is the latest research paper for this session. "
                    "Use it to answer questions, and say when the paper does not contain the answer.\n"
                    f"## Research Paper\n{research_paper_text}\n"
                )

        report_prompt = "\n".join(report_prompt_sections)

        conversation = None
        if generator:
            if not hasattr(generator, 'report_code_chat_conversations') or generator.report_code_chat_conversations is None:
                generator.report_code_chat_conversations = {}
            conversation = generator.report_code_chat_conversations.get(report_key)
            if conversation is None:
                conversation = CodeChatConversation()
            generator.report_code_chat_conversations[report_key] = conversation

        # Memory monitoring: track the code_chat call which spawns Claude Agent SDK
        log_memory("report_chat: starting run_code_chat (Claude Agent SDK)")
        with MemoryTracker("report_chat:run_code_chat", warn_threshold_mb=400.0) as tracker:
            reply = _run_code_chat_with_timeout(
                CODE_CHAT_TIMEOUT_SECS,
                question=message,
                working_dir=working_dir,
                model=model_name,
                include_trace=True,
                report_prompt=report_prompt,
                conversation=conversation,
                cancel_requested=lambda: _is_report_chat_cancelled(session_id, client_request_id),
            )
        log_memory(f"report_chat: run_code_chat completed (delta={tracker.delta_mb:+.1f}MB)")

        if isinstance(reply, dict) and reply.get('cancelled'):
            return jsonify({
                'success': False,
                'error': 'Request cancelled',
                'cancelled': True,
                **_response_time_payload(request_started_at),
            })

        if isinstance(reply, dict) and reply.get('error'):
            reset_hint = reply.get('reset_hint')
            return jsonify({
                'success': False,
                'error': reply['error'],
                'reset_hint': reset_hint,
                **_response_time_payload(request_started_at),
            })

        thinking = reply.get('thinking') if isinstance(reply, dict) else None
        reply_text = reply.get('answer') if isinstance(reply, dict) else reply

        if not reply_text:
            return jsonify({'success': False, 'error': 'Failed to get response', **_response_time_payload(request_started_at)})

        timing_payload = _response_time_payload(request_started_at)

        full_conversation_messages = _get_report_conversation_snapshot(generator, report_key)
        _write_interaction_log('report_chat', {
            'mode': 'code',
            'report_type': report_type,
            'report_view': report_view,
            'message': message,
            'reply': reply_text,
            'thinking': thinking,
            'conversation_messages': full_conversation_messages,
            'response_time_ms': timing_payload['response_time_ms'],
        })

        log_full_event_to_s3(
            'report_chat',
            {
                'mode': 'code',
                'report_type': report_type,
                'report_view': report_view,
                'message': message,
                'reply': reply_text,
                'thinking': thinking,
                'citations': [] if not generator else getattr(generator, 'last_report_answer_citations', []),
                'conversation_messages': full_conversation_messages,
                'response_time_ms': timing_payload['response_time_ms'],
            },
            source='code',
        )
        log_event('report_chat', {'mode': 'code', 'report_type': report_type, 'report_view': report_view, 'message': message})
        mem_after = log_memory("report_chat: response ready")
        return jsonify({
            'success': True,
            'reply': reply_text,
            'mode': 'code',
            'thinking': thinking,
            'citations': [] if not generator else getattr(generator, 'last_report_answer_citations', []),
            'documents': [],
            '_debug_memory': {'before_mb': round(mem_before, 1), 'after_mb': round(mem_after, 1)},
            **timing_payload,
        })

    except MemoryError as e:
        log_memory("report_chat: MemoryError caught", level="error")
        return jsonify({'success': False, 'error': f'Memory limit exceeded: {str(e)}', **_response_time_payload(request_started_at)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        log_memory(f"report_chat: exception - {type(e).__name__}", level="error")
        return jsonify({'success': False, 'error': str(e), **_response_time_payload(request_started_at)})
    finally:
        _end_report_chat_request(session_id, client_request_id)


@app.route('/api/edit', methods=['POST'])
@with_session_operation_lock('api_edit')
def api_edit():
    """Edit a hypothesis with feedback using CLI method."""
    if _is_audit_only_mode():
        return _reject_audit_only('Hypothesis editing')

    data = request.json or {}
    raw_idx = data.get('index')
    state_id = data.get('state_id')
    feedback = (data.get('feedback') or '').strip()
    manual_hypothesis = (data.get('manual_hypothesis') or '').strip()
    request_hypothesis = (data.get('request_hypothesis') or '').strip()
    action = (data.get('action') or '').strip()
    edit_regeneration_version = action == 'edit_regeneration_version'
    regeneration_position = None
    regeneration_result_id = None
    if edit_regeneration_version:
        regeneration_result_id = _valid_history_result_id(data.get('regeneration_result_id'))
        try:
            regeneration_position = int(data.get('regeneration_position'))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Invalid regeneration version'})
        if regeneration_position < 0 or not regeneration_result_id:
            return jsonify({'success': False, 'error': 'Invalid regeneration version'})
    client_request_id = sanitize_field(data.get('request_id'), 120)

    generator = get_or_restore_generator()
    if not generator:
        return jsonify({'success': False, 'error': 'No active session'})

    seed_hypothesis = manual_hypothesis or request_hypothesis
    if seed_hypothesis:
        # A historical result must be resolved from durable server history;
        # never let browser text replace the current slot before validation.
        if not edit_regeneration_version:
            _seed_hypothesis_from_request_draft(generator, raw_idx, state_id, seed_hypothesis)

    request_id = (getattr(g, 'operation_debug', {}) or {}).get('request_id')
    idx, idx_error, used_stale_fallback = _resolve_hypothesis_index_for_edit(
        generator,
        raw_idx,
        state_id,
        # Normal manual editing keeps its existing interrupted-navigation
        # recovery. Editing a historical regeneration result must instead
        # validate the exact current active token and reject stale previews.
        allow_stale_fallback=bool(manual_hypothesis) and not edit_regeneration_version,
    )
    if idx_error == 'missing_index':
        return jsonify({'success': False, 'error': 'Missing index'})
    if idx_error == 'invalid_index':
        return jsonify({'success': False, 'error': 'Invalid hypothesis index'})
    if idx_error == 'stale_state':
        return jsonify({
            'success': False,
            'error': 'The hypothesis changed and your editor is out of date. Re-open it and try again.',
            'stale_selection': True,
            'reset_hint': 'Re-open the hypothesis from the list, then retry your edit.',
        })

    historical_result = None
    if edit_regeneration_version:
        historical_result = _studio_resolve_regeneration_result(
            generator, idx, regeneration_result_id
        )
        if historical_result is None:
            return jsonify({'success': False, 'error': 'That regeneration result is no longer available.'})
        # Position is display metadata only; derive it from the trusted durable
        # identity rather than accepting the browser's position as authority.
        regeneration_position = historical_result['regeneration_position']
        trusted_snapshot = historical_result['snapshot']
        trusted_base_text = (
            trusted_snapshot.get('full_text')
            or trusted_snapshot.get('formatted_output')
            or trusted_snapshot.get('hypothesis')
            or ''
        ).strip()
        if not trusted_base_text:
            return jsonify({'success': False, 'error': 'That regeneration result has no hypothesis content.'})

    print(
        f"[api_edit] request={request_id} session={_session_debug_id()} "
        f"raw_index={raw_idx} resolved_index={idx} used_stale_fallback={used_stale_fallback} "
        f"has_feedback={bool(feedback)} has_manual={bool(manual_hypothesis)}"
    )

    if not feedback and not manual_hypothesis:
        return jsonify({'success': False, 'error': 'Provide direct edits or agent feedback'})

    # A restored client draft is sufficient context for this mutation. Require
    # project setup only when the request has no self-contained hypothesis text.
    if feedback and not seed_hypothesis and not _resolve_canonical_task_description(generator):
        return _project_setup_incomplete_response()

    session_id = session.get('session_id')
    thread_request_id = client_request_id or request_id or os.urandom(8).hex()
    if feedback:
        _begin_hypothesis_thread_request(session_id, thread_request_id, index=idx, kind='revise')

    try:
        edited_hypothesis = None
        combined_feedback = None
        # Snapshot the pre-edit hypothesis for the durable before/after event.
        history_before = _studio_hypothesis_snapshot(generator, idx)
        prior_content = generator.hypotheses[idx - 1]
        prior_state_ids = list(getattr(generator, 'hypothesis_state_ids', []) or [])
        prior_thread_ids = list(getattr(generator, 'hypothesis_thread_ids', []) or [])
        prior_chat_threads = copy.deepcopy(
            getattr(generator, 'hypothesis_chat_threads', None)
        )
        active_result_thread_id = None
        historical_thread_recovered = None
        if historical_result is not None:
            generator.hypotheses[idx - 1] = trusted_base_text
            active_result_thread_id, historical_thread_recovered = _activate_hypothesis_thread_for_result(
                generator, idx, historical_result.get('hypothesis_thread_id')
            )
        # Exact scientist-authored revision request (agent-driven path only).
        # Manual direct editing carries no request message; keep it None so no
        # scientist message is fabricated for a literal manual edit.
        history_request_text = None

        # If the user only provided direct edits, save them verbatim
        if manual_hypothesis and not feedback:
            generator.hypotheses[idx - 1] = generator._sanitize_hypothesis_text(manual_hypothesis)
            if hasattr(generator, 'log_interaction'):
                generator.log_interaction('manual_edit', {
                    'hypothesis_index': idx,
                    'manual_text': manual_hypothesis[:500]
                })
            edited_hypothesis = generator.hypotheses[idx - 1]
        else:
            history_request_text = feedback
            # Combine user-edited draft (if any) with agent instructions
            combined_feedback = feedback
            if historical_result is not None:
                combined_feedback = (
                    "Use the historical hypothesis result below as the starting point. "
                    "Respect the Title/Hypothesis/Justification structure. "
                    "Apply the requested changes that follow.\n\n"
                    f"Historical hypothesis:\n{trusted_base_text}\n\n"
                    f"Requested changes:\n{feedback}"
                )
            elif manual_hypothesis:
                combined_feedback = (
                    "Use the user-edited draft below as the starting point. "
                    "Respect the Title/Hypothesis/Justification structure. "
                    "Apply the requested changes that follow.\n\n"
                    f"User-edited draft:\n{manual_hypothesis}\n\n"
                    f"Requested changes:\n{feedback}"
                )

            # Use CLI method to edit hypothesis with combined feedback, inside
            # this hypothesis's own refinement thread (V1 behavior, isolated).
            with _hypothesis_thread_ctx(generator, idx, restore=True):
                edited_hypothesis = generator.edit_hypothesis(idx, feedback=combined_feedback)

        if edited_hypothesis:
            if feedback and _is_hypothesis_thread_cancelled(session_id, thread_request_id):
                before_text = (history_before or {}).get('full_text')
                if before_text and 1 <= idx <= len(getattr(generator, 'hypotheses', []) or []):
                    generator.hypotheses[idx - 1] = prior_content
                generator.hypothesis_state_ids = prior_state_ids
                generator.hypothesis_thread_ids = prior_thread_ids
                if prior_chat_threads is not None:
                    generator.hypothesis_chat_threads = prior_chat_threads
                _end_hypothesis_thread_request(session_id, thread_request_id)
                return jsonify({'success': False, 'error': 'Request cancelled', 'cancelled': True})
            edited_hypothesis = generator._sanitize_hypothesis_text(edited_hypothesis)
            sections = generator.parse_hypothesis_sections(edited_hypothesis)
            state_id = _rotate_hypothesis_state_id(generator, idx)
            _persist_generated_hypotheses(generator, strict=bool(historical_result))
            # Durable chronology: record the exact scientist request (when the
            # revision was agent-driven) immediately ahead of the revision.
            _studio_record_mutation(
                generator, 'revised', idx, history_before,
                _studio_hypothesis_snapshot(generator, idx, full_text=edited_hypothesis),
                request_text=history_request_text,
                action='edit_regeneration_version' if edit_regeneration_version else None,
                regeneration_position=regeneration_position,
                source_result_event_id=(
                    historical_result['result_event_id'] if historical_result else None
                ),
                request_thread_id=active_result_thread_id,
                strict=bool(historical_result),
            )
            source = (
                'regeneration_version_edit'
                if edit_regeneration_version
                else ('manual' if manual_hypothesis and not feedback else 'llm_edit')
            )
            log_full_event_to_s3(
                'hypothesis_edit',
                {
                    'index': idx,
                    'feedback': feedback,
                    'manual_hypothesis': manual_hypothesis,
                    'combined_feedback': combined_feedback,
                    'result': {
                        'title': sections.get('title'),
                        'full_text': edited_hypothesis,
                        'hypothesis': sections['hypothesis'],
                        'justification': sections['justification'],
                        'interpretation': sections['interpretation'],
                        'plan': sections.get('plan'),
                    },
                    'citations': getattr(generator.hypothesis_gen_agent, 'last_response_citations', []),
                    'documents': getattr(generator, 'citation_documents', []),
                },
                hypothesis_index=idx,
                source=source,
            )

            log_event('edit_hypothesis', {'index': idx})
            _end_hypothesis_thread_request(session_id, thread_request_id)
            return jsonify({
                'success': True,
                'hypothesis': {
                    'index': idx,
                    'state_id': state_id,
                    'title': sections.get('title'),
                    'full_text': edited_hypothesis,
                    'hypothesis': sections['hypothesis'],
                    'justification': sections['justification'],
                    'interpretation': sections['interpretation'],
                    'plan': sections.get('plan'),
                    'thread_recoverable': bool(
                        _hypothesis_thread_key_for_index(generator, idx)
                    ),
                },
                'citations': getattr(generator.hypothesis_gen_agent, 'last_response_citations', []),
                'documents': getattr(generator, 'citation_documents', []),
                'historical_thread_recovered': historical_thread_recovered,
            })
        else:
            if historical_result is not None:
                generator.hypotheses[idx - 1] = prior_content
                generator.hypothesis_state_ids = prior_state_ids
                generator.hypothesis_thread_ids = prior_thread_ids
                if prior_chat_threads is not None:
                    generator.hypothesis_chat_threads = prior_chat_threads
            _end_hypothesis_thread_request(session_id, thread_request_id)
            return jsonify({'success': False, 'error': 'Failed to edit hypothesis'})

    except Exception as e:
        if 'prior_content' in locals() and historical_result is not None:
            generator.hypotheses[idx - 1] = prior_content
            generator.hypothesis_state_ids = prior_state_ids
            generator.hypothesis_thread_ids = prior_thread_ids
            if prior_chat_threads is not None:
                generator.hypothesis_chat_threads = prior_chat_threads
            try:
                _persist_generated_hypotheses(generator)
            except Exception:
                pass
        _end_hypothesis_thread_request(session_id, thread_request_id)
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/regenerate', methods=['POST'])
@with_session_operation_lock('api_regenerate')
def api_regenerate():
    """Regenerate a hypothesis with feedback using CLI method."""
    if _is_audit_only_mode():
        return _reject_audit_only('Hypothesis regeneration')

    data = request.json or {}
    raw_idx = data.get('index')
    state_id = data.get('state_id')
    raw_generation_id = data.get('generation_id')
    feedback = (data.get('feedback') or '').strip()
    manual_hypothesis = (data.get('manual_hypothesis') or '').strip()
    request_hypothesis = (data.get('request_hypothesis') or '').strip()
    request_id = (getattr(g, 'operation_debug', {}) or {}).get('request_id')

    generator = get_or_restore_generator()
    if not generator:
        return jsonify({'success': False, 'error': 'No active session'})

    seed_hypothesis = manual_hypothesis or request_hypothesis
    if seed_hypothesis:
        _seed_hypothesis_from_request_draft(generator, raw_idx, state_id, seed_hypothesis)

    request_generation_id, generation_id_error = _parse_optional_generation_id(raw_generation_id)
    if generation_id_error == 'invalid_generation_id':
        return jsonify({'success': False, 'error': 'Invalid generation id'})
    current_generation_id = _get_generation_id()
    if request_generation_id is not None and request_generation_id != current_generation_id:
        return jsonify({
            'success': False,
            'error': 'The hypothesis list changed and this regenerate request is out of date. Re-open it and try again.',
            'stale_selection': True,
            'reset_hint': 'Re-open the hypothesis from the current list, then retry.',
        })
    idx, idx_error, used_stale_fallback = _resolve_hypothesis_index_for_regenerate(generator, raw_idx, state_id)
    if idx_error == 'missing_index':
        return jsonify({'success': False, 'error': 'Missing index'})
    if idx_error == 'invalid_index':
        return jsonify({'success': False, 'error': 'Invalid hypothesis index'})
    if idx_error == 'stale_state':
        return jsonify({
            'success': False,
            'error': 'The hypothesis changed and your editor is out of date. Re-open it and try again.',
            'stale_selection': True,
            'reset_hint': 'Re-open the hypothesis from the list, then retry.',
        })

    print(
        f"[api_regenerate] request={request_id} session={_session_debug_id()} "
        f"request_generation_id={request_generation_id} current_generation_id={current_generation_id} "
        f"raw_index={raw_idx} resolved_index={idx} "
        f"used_stale_fallback={used_stale_fallback} "
        f"has_feedback={bool(feedback)} has_manual={bool(manual_hypothesis)}"
    )

    if not feedback and not manual_hypothesis:
        return jsonify({'success': False, 'error': 'Provide direct edits or agent feedback'})

    # A restored client draft is sufficient context for this mutation. Require
    # project setup only when the request has no self-contained hypothesis text.
    if feedback and not seed_hypothesis and not _resolve_canonical_task_description(generator):
        return _project_setup_incomplete_response()

    try:
        new_hypothesis = None
        combined_feedback = None
        # Snapshot the pre-regeneration hypothesis for the before/after event.
        history_before = _studio_hypothesis_snapshot(generator, idx)
        # Exact scientist-authored request (agent-driven path only); None for
        # literal manual regeneration so no scientist message is fabricated.
        history_request_text = None

        # If only direct edits are provided, treat them as the new hypothesis
        if manual_hypothesis and not feedback:
            generator.hypotheses[idx - 1] = generator._sanitize_hypothesis_text(manual_hypothesis)
            if hasattr(generator, 'log_interaction'):
                generator.log_interaction('manual_regenerate', {
                    'hypothesis_index': idx,
                    'manual_text': manual_hypothesis[:500]
                })
            new_hypothesis = generator.hypotheses[idx - 1]
        else:
            history_request_text = feedback
            combined_feedback = feedback
            if manual_hypothesis:
                combined_feedback = (
                    "Use the user-provided draft below as the starting point, "
                    "but regenerate a cleaner, stronger version. "
                    "Keep the Title/Hypothesis/Justification structure. "
                    "Requested improvements follow.\n\n"
                    f"User draft:\n{manual_hypothesis}\n\n"
                    f"Requested changes:\n{feedback}"
                )

            # Use CLI method to regenerate hypothesis (now accepts feedback parameter!)
            # Runs in this hypothesis's own thread so the exchange cannot leak
            # into other hypotheses' refinement contexts.
            with _hypothesis_thread_ctx(generator, idx):
                new_hypothesis = generator.regenerate_hypothesis(idx, feedback=combined_feedback)

        if new_hypothesis:
            new_hypothesis = generator._sanitize_hypothesis_text(new_hypothesis)
            sections = generator.parse_hypothesis_sections(new_hypothesis)
            state_id = _rotate_hypothesis_state_id(generator, idx)
            prior_thread_id = _hypothesis_thread_key_for_index(generator, idx)
            # The regenerated result owns a fresh post-regeneration thread. It
            # must be known before the durable `after` snapshot is created.
            _regenerate_reset_hypothesis_thread(generator, idx)
            generator.persist_generated_hypotheses()
            # Durable chronology: record the exact scientist request (when
            # agent-driven) on the prior thread while the resulting snapshot
            # retains the fresh post-regeneration thread.
            regeneration_result_event_id = _studio_record_mutation(
                generator, 'regenerated', idx, history_before,
                _studio_hypothesis_snapshot(generator, idx, full_text=new_hypothesis),
                request_text=history_request_text,
                request_thread_id=prior_thread_id,
            )
            source = 'manual_regenerate' if manual_hypothesis and not feedback else 'llm_regenerate'
            log_full_event_to_s3(
                'hypothesis_regenerate',
                {
                    'index': idx,
                    'feedback': feedback,
                    'manual_hypothesis': manual_hypothesis,
                    'combined_feedback': combined_feedback,
                    'result': {
                        'title': sections.get('title'),
                        'full_text': new_hypothesis,
                        'hypothesis': sections['hypothesis'],
                        'justification': sections['justification'],
                        'interpretation': sections['interpretation'],
                        'plan': sections.get('plan'),
                    },
                    'citations': getattr(generator.hypothesis_gen_agent, 'last_response_citations', []),
                    'documents': getattr(generator, 'citation_documents', []),
                },
                hypothesis_index=idx,
                source=source,
            )

            log_event('regenerate_hypothesis', {'index': idx})
            return jsonify({
                'success': True,
                'hypothesis': {
                    'index': idx,
                    'state_id': state_id,
                    'title': sections.get('title'),
                    'full_text': new_hypothesis,
                    'hypothesis': sections['hypothesis'],
                    'justification': sections['justification'],
                    'interpretation': sections['interpretation'],
                    'plan': sections.get('plan'),
                    'thread_recoverable': bool(
                        _hypothesis_thread_key_for_index(generator, idx)
                    ),
                },
                'result_event_id': regeneration_result_event_id,
                'citations': getattr(generator.hypothesis_gen_agent, 'last_response_citations', []),
                'documents': getattr(generator, 'citation_documents', []),
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to regenerate hypothesis'})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/regenerate_guidance', methods=['POST'])
@with_session_operation_lock('api_regenerate_guidance')
def api_regenerate_guidance():
    """Regenerate a hypothesis with additional guidance for the next run."""
    if _is_audit_only_mode():
        return _reject_audit_only('Hypothesis regeneration')

    data = request.json or {}
    raw_idx = data.get('index')
    state_id = data.get('state_id')
    raw_generation_id = data.get('generation_id')
    feedback = (data.get('feedback') or '').strip()
    manual_hypothesis = (data.get('manual_hypothesis') or '').strip()
    request_hypothesis = (data.get('request_hypothesis') or '').strip()
    guidance = (data.get('guidance') or '').strip()
    request_id = (getattr(g, 'operation_debug', {}) or {}).get('request_id')

    generator = get_or_restore_generator()
    if not generator:
        return jsonify({'success': False, 'error': 'No active session'})

    seed_hypothesis = manual_hypothesis or request_hypothesis
    if seed_hypothesis:
        _seed_hypothesis_from_request_draft(generator, raw_idx, state_id, seed_hypothesis)

    request_generation_id, generation_id_error = _parse_optional_generation_id(raw_generation_id)
    if generation_id_error == 'invalid_generation_id':
        return jsonify({'success': False, 'error': 'Invalid generation id'})
    current_generation_id = _get_generation_id()
    if request_generation_id is not None and request_generation_id != current_generation_id:
        return jsonify({
            'success': False,
            'error': 'The hypothesis list changed and this regenerate request is out of date. Re-open it and try again.',
            'stale_selection': True,
            'reset_hint': 'Re-open the hypothesis from the current list, then retry.',
        })
    idx, idx_error, used_stale_fallback = _resolve_hypothesis_index_for_regenerate(generator, raw_idx, state_id)
    if idx_error == 'missing_index':
        return jsonify({'success': False, 'error': 'Missing index'})
    if idx_error == 'invalid_index':
        return jsonify({'success': False, 'error': 'Invalid hypothesis index'})
    if idx_error == 'stale_state':
        return jsonify({
            'success': False,
            'error': 'The hypothesis changed and your editor is out of date. Re-open it and try again.',
            'stale_selection': True,
            'reset_hint': 'Re-open the hypothesis from the list, then retry.',
        })

    print(
        f"[api_regenerate_guidance] request={request_id} session={_session_debug_id()} "
        f"request_generation_id={request_generation_id} current_generation_id={current_generation_id} "
        f"raw_index={raw_idx} resolved_index={idx} used_stale_fallback={used_stale_fallback} "
        f"has_feedback={bool(feedback)} has_manual={bool(manual_hypothesis)} has_guidance={bool(guidance)}"
    )

    guidance_payload = guidance if isinstance(guidance, str) else ""
    if guidance_payload and not guidance_payload.strip():
        guidance_payload = ""
    if (
        (feedback or guidance_payload or not manual_hypothesis)
        and not seed_hypothesis
        and not _resolve_canonical_task_description(generator)
    ):
        return _project_setup_incomplete_response()

    session_id = session.get('session_id')
    regeneration_token = _begin_hypothesis_regeneration(session_id, idx)
    previous_state = {
        'hypothesis': generator.hypotheses[idx - 1] if 0 <= idx - 1 < len(getattr(generator, 'hypotheses', []) or []) else None,
        'agent_messages': copy.deepcopy(getattr(getattr(generator, 'hypothesis_gen_agent', None), 'messages', [])),
        'agent_last_response_citations': copy.deepcopy(getattr(getattr(generator, 'hypothesis_gen_agent', None), 'last_response_citations', [])),
    }
    try:
        new_hypothesis = None
        combined_feedback = None
        base_feedback = feedback or DEFAULT_REGENERATE_FEEDBACK
        # Snapshot the pre-regeneration hypothesis for the before/after event.
        history_before = _studio_hypothesis_snapshot(generator, idx)
        # Exact scientist-authored request: the free-text feedback and/or the
        # "guidance for next run", each verbatim. A plain regenerate (only the
        # DEFAULT_REGENERATE_FEEDBACK system default, no scientist text) yields
        # None so no scientist message is fabricated for a button-only action.
        history_request_text = "\n\n".join(t for t in (feedback, guidance_payload) if t) or None

        if manual_hypothesis and not feedback and not guidance_payload:
            generator.hypotheses[idx - 1] = generator._sanitize_hypothesis_text(manual_hypothesis)
            if hasattr(generator, 'log_interaction'):
                generator.log_interaction('manual_regenerate', {
                    'hypothesis_index': idx,
                    'manual_text': manual_hypothesis[:500]
                })
            new_hypothesis = generator.hypotheses[idx - 1]
        else:
            combined_feedback = base_feedback
            if manual_hypothesis:
                combined_feedback = (
                    "Use the user-provided draft below as the starting point, "
                    "but regenerate a cleaner, stronger version. "
                    "Keep the Title/Hypothesis/Justification structure. "
                    "Requested improvements follow.\n\n"
                    f"User draft:\n{manual_hypothesis}\n\n"
                    f"Requested changes:\n{base_feedback}"
                )

            if guidance_payload:
                combined_feedback = (
                    f"{combined_feedback}\n\n"
                    "Additional guidance from the user (include verbatim in context for the next generation):\n"
                    f"{guidance_payload}"
                )

            # Runs in this hypothesis's own thread so the exchange cannot leak
            # into other hypotheses' refinement contexts.
            with _hypothesis_thread_ctx(generator, idx):
                new_hypothesis = generator.regenerate_hypothesis(idx, feedback=combined_feedback)

        if _is_hypothesis_regeneration_cancelled(session_id, regeneration_token):
            if previous_state['hypothesis'] is not None:
                generator.hypotheses[idx - 1] = previous_state['hypothesis']
            if getattr(generator, 'hypothesis_gen_agent', None):
                generator.hypothesis_gen_agent.messages = previous_state['agent_messages']
                generator.hypothesis_gen_agent.last_response_citations = previous_state['agent_last_response_citations']
            return jsonify({'success': False, 'error': 'Regeneration cancelled', 'cancelled': True})

        if new_hypothesis:
            new_hypothesis = generator._sanitize_hypothesis_text(new_hypothesis)
            sections = generator.parse_hypothesis_sections(new_hypothesis)
            state_id = _rotate_hypothesis_state_id(generator, idx)
            prior_thread_id = _hypothesis_thread_key_for_index(generator, idx)
            _regenerate_reset_hypothesis_thread(generator, idx)
            generator.persist_generated_hypotheses()
            # Durable chronology: record the exact scientist request (feedback
            # and/or guidance, when present) immediately ahead of the
            # regeneration; None for a button-only default regenerate. The
            # request stays on the prior thread, while `after` owns the fresh one.
            regeneration_result_event_id = _studio_record_mutation(
                generator, 'regenerated', idx, history_before,
                _studio_hypothesis_snapshot(generator, idx, full_text=new_hypothesis),
                request_text=history_request_text,
                request_thread_id=prior_thread_id,
            )
            source = (
                'manual_regenerate'
                if manual_hypothesis and not feedback and not guidance_payload
                else 'llm_regenerate'
            )
            log_full_event_to_s3(
                'hypothesis_regenerate',
                {
                    'index': idx,
                    'feedback': base_feedback,
                    'manual_hypothesis': manual_hypothesis,
                    'combined_feedback': combined_feedback,
                    'guidance': guidance_payload or None,
                    'result': {
                        'title': sections.get('title'),
                        'full_text': new_hypothesis,
                        'hypothesis': sections['hypothesis'],
                        'justification': sections['justification'],
                        'interpretation': sections['interpretation'],
                        'plan': sections.get('plan'),
                    },
                    'citations': getattr(generator.hypothesis_gen_agent, 'last_response_citations', []),
                    'documents': getattr(generator, 'citation_documents', []),
                },
                hypothesis_index=idx,
                source=source,
            )

            log_event('regenerate_hypothesis', {'index': idx})
            return jsonify({
                'success': True,
                'hypothesis': {
                    'index': idx,
                    'state_id': state_id,
                    'title': sections.get('title'),
                    'full_text': new_hypothesis,
                    'hypothesis': sections['hypothesis'],
                    'justification': sections['justification'],
                    'interpretation': sections['interpretation'],
                    'plan': sections.get('plan'),
                    'thread_recoverable': bool(
                        _hypothesis_thread_key_for_index(generator, idx)
                    ),
                },
                'result_event_id': regeneration_result_event_id,
                'citations': getattr(generator.hypothesis_gen_agent, 'last_response_citations', []),
                'documents': getattr(generator, 'citation_documents', []),
            })
        return jsonify({'success': False, 'error': 'Failed to regenerate hypothesis'})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})
    finally:
        _end_hypothesis_regeneration(session_id, regeneration_token)


@app.route('/api/remove_hypothesis', methods=['POST'])
@with_session_operation_lock('api_remove_hypothesis')
def api_remove_hypothesis():
    """Remove one candidate hypothesis from the active workspace.

    Durable, non-LLM state operation: deletes the hypothesis (and its refinement
    thread) from the in-memory generator, re-persists generated_hypotheses.json
    with the surviving set, rotates the generation id (so stale per-card requests
    fail safely), and records a hypothesis_removed event carrying the re-indexed
    surviving snapshot in the round's durable history.
    """
    if _is_audit_only_mode():
        return _reject_audit_only('Hypothesis removal')

    data = request.json or {}
    raw_idx = data.get('index')
    state_id = data.get('state_id')

    generator = get_or_restore_generator()
    if not generator:
        return jsonify({'success': False, 'error': 'No active session'})

    idx, idx_error = _resolve_hypothesis_index(generator, raw_idx, state_id)
    if idx_error == 'stale_state':
        # Remove is a card-position operation. If the card's version id is stale
        # but the displayed index still exists, remove that current slot instead
        # of forcing the user to reopen the menu after a revision/regeneration.
        idx, idx_error = _resolve_hypothesis_index(generator, raw_idx, None)
    if idx_error == 'missing_index':
        return jsonify({'success': False, 'error': 'Missing index'})
    if idx_error == 'invalid_index':
        return jsonify({'success': False, 'error': 'Invalid hypothesis index'})
    if idx_error == 'stale_state':
        return jsonify({
            'success': False,
            'error': 'The hypothesis changed and this remove request is out of date. Re-open it and try again.',
            'stale_selection': True,
            'reset_hint': 'Re-check the hypothesis in the current list, then retry.',
        })

    try:
        removed_snapshot = _studio_hypothesis_snapshot(generator, idx)

        # Drop the removed slot's state id FIRST so the surviving hypotheses
        # keep their original (stable) state ids after the index shift.
        prior_ids = list(getattr(generator, 'hypothesis_state_ids', []) or [])
        if idx - 1 < len(prior_ids):
            del prior_ids[idx - 1]
        generator.hypothesis_state_ids = prior_ids

        # Drop the removed hypothesis's refinement thread by its STABLE key, then
        # delete its slot from the parallel thread-id list. Survivors keep their
        # own thread ids (and thus their threads) — no index-based shifting can
        # mis-attach a thread to a different hypothesis.
        removed_thread_key = _hypothesis_thread_key_for_index(generator, idx)
        if removed_thread_key and hasattr(generator, 'clear_hypothesis_thread'):
            generator.clear_hypothesis_thread(removed_thread_key)
        prior_thread_ids = list(getattr(generator, 'hypothesis_thread_ids', []) or [])
        if idx - 1 < len(prior_thread_ids):
            del prior_thread_ids[idx - 1]
        generator.hypothesis_thread_ids = prior_thread_ids

        del generator.hypotheses[idx - 1]
        state_ids = _ensure_hypothesis_state_ids(generator)
        _ensure_hypothesis_thread_ids(generator)

        if generator.hypotheses:
            generator.persist_generated_hypotheses()
        else:
            generator.clear_persisted_generated_hypotheses()

        if hasattr(generator, 'log_interaction'):
            generator.log_interaction('remove_hypothesis', {
                'hypothesis_index': idx,
                'remaining': len(generator.hypotheses),
            })

        parsed_hypotheses = []
        for i, hyp in enumerate(generator.hypotheses, 1):
            sections = generator.parse_hypothesis_sections(hyp)
            parsed_hypotheses.append({
                'index': i,
                'state_id': state_ids[i - 1] if i - 1 < len(state_ids) else None,
                'title': sections.get('title'),
                'full_text': hyp,
                'hypothesis': sections['hypothesis'],
                'justification': sections['justification'],
                'interpretation': sections['interpretation'],
                'plan': sections.get('plan'),
                'thread_recoverable': bool(
                    _hypothesis_thread_key_for_index(generator, i)
                ),
            })

        removal_result_event_id = _studio_record_removal(
            generator, idx, removed_snapshot, parsed_hypotheses
        )
        generation_id = _increment_generation_id()

        log_full_event_to_s3(
            'hypothesis_remove',
            {
                'index': idx,
                'removed': removed_snapshot,
                'remaining': parsed_hypotheses,
            },
            hypothesis_index=idx,
        )
        log_event('remove_hypothesis', {'index': idx})

        return jsonify({
            'success': True,
            'removed_index': idx,
            'hypotheses': parsed_hypotheses,
            'count': len(parsed_hypotheses),
            'generation_id': generation_id,
            'result_event_id': removal_result_event_id,
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/get_report', methods=['POST'])
def api_get_report():
    """Get background report or round report using CLI method."""
    if _is_audit_only_mode():
        return _reject_audit_only('Reports')
    generator = get_generator()
    if not generator:
        return jsonify({'success': False, 'error': 'No active session'})

    data = request.json
    report_type = data.get('type', 'background')

    try:
        if report_type == 'background':
            # Use CLI method to build combined background
            combined = generator._build_combined_background()
            log_event('get_report', {'type': report_type})
            return jsonify({
                'success': True,
                'report': combined,
                'report_html': render_report_html(combined, generator),
            })
        elif report_type == 'background_literature':
            if not generator.include_background_literature:
                return jsonify({
                    'success': False,
                    'error': 'Background literature not enabled for this session'
                })
            if generator.deep_research_background:
                log_event('get_report', {'type': report_type})
                return jsonify({
                    'success': True,
                    'report': generator.deep_research_background,
                    'report_html': render_report_html(generator.deep_research_background, generator),
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'No background literature available'
                })
        elif report_type == 'round':
            # Always resolve from the latest completed round on disk.
            _full, _, _report_dir = _load_latest_round_report_content(
                Path(generator.checkpoint_dir)
            ) if getattr(generator, 'checkpoint_dir', None) else (None, None, None)
            if _report_dir is not None:
                print(f"Round Report: using latest completed round report: {_report_dir / 'round_report.md'}")
            round_content = _full or getattr(generator, 'round_report', None)
            if round_content:
                log_event('get_report', {'type': report_type})
                return jsonify({
                    'success': True,
                    'report': round_content,
                    'report_html': render_report_html(round_content, generator),
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'No round report available'
                })
        else:
            return jsonify({
                'success': False,
                'error': 'Invalid report type'
            })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/get_citation_document', methods=['POST'])
def api_get_citation_document():
    """Get a specific document from citation_documents by index or title."""
    if _is_audit_only_mode():
        return _reject_audit_only('Citation lookup')
    generator = get_generator()
    if not generator:
        return jsonify({'success': False, 'error': 'No active session'})

    data = request.json
    doc_index = data.get('document_index')
    doc_title = data.get('title')

    try:
        documents = getattr(generator, 'citation_documents', [])
        if not documents:
            return jsonify({'success': False, 'error': 'No citation documents available'})

        target_doc = None

        # Find document by index
        if doc_index is not None and 0 <= doc_index < len(documents):
            target_doc = documents[doc_index]
        # Or find by title
        elif doc_title:
            for doc in documents:
                if doc.get('title') == doc_title:
                    target_doc = doc
                    break

        if not target_doc:
            return jsonify({'success': False, 'error': 'Document not found'})

        # Extract content from the document
        source = target_doc.get('source', {})
        content = ''
        if source.get('type') == 'text':
            content = source.get('data', '')
        elif source.get('type') == 'base64':
            # For PDFs, we can't easily display, return a note
            content = '[PDF document - content cannot be displayed as text]'

        return jsonify({
            'success': True,
            'title': target_doc.get('title', 'Document'),
            'content': content,
            'document_index': doc_index
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/get_qa_history', methods=['GET'])
def api_get_qa_history():
    """Get Q&A history from CLI generator."""
    if _is_audit_only_mode():
        return _reject_audit_only('Q&A history')
    generator = get_generator()
    if not generator:
        return jsonify({'success': False, 'error': 'No active session'})

    return jsonify({
        'success': True,
        'qa_history': generator.qa_history
    })


@app.route('/api/preview_feedback', methods=['POST'])
def api_preview_feedback():
    """Apply feedback to a hypothesis draft without saving."""
    if _is_audit_only_mode():
        return _reject_audit_only('Hypothesis preview')
    generator = get_generator()
    if not generator:
        return jsonify({'success': False, 'error': 'No active session'})

    data = request.json or {}
    hypothesis_text = (data.get('hypothesis_text') or '').strip()
    feedback = (data.get('feedback') or '').strip()

    if not hypothesis_text:
        return jsonify({'success': False, 'error': 'Missing hypothesis text'})
    if not feedback:
        return jsonify({'success': False, 'error': 'Missing feedback'})

    try:
        revised = generator.apply_scientist_feedback(hypothesis_text, feedback)
        revised = generator._sanitize_hypothesis_text(revised)
        sections = generator.parse_hypothesis_sections(revised)
        return jsonify({
            'success': True,
            'hypothesis': {
                'full_text': revised,
                'title': sections.get('title'),
                'hypothesis': sections['hypothesis'],
                'justification': sections['justification'],
                'interpretation': sections['interpretation'],
                'plan': sections.get('plan'),
            },
            'citations': getattr(generator.hypothesis_gen_agent, 'last_response_citations', []),
            'documents': getattr(generator, 'citation_documents', []),
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/select', methods=['POST'])
@with_session_operation_lock('api_select')
def api_select():
    """Save the selected hypothesis using CLI method."""
    if _is_audit_only_mode():
        return _reject_audit_only('Hypothesis selection')

    data = request.json or {}
    selection = data.get('selection')  # int or 'custom'
    custom_hypothesis = data.get('custom_hypothesis')
    custom_source = sanitize_field(data.get('custom_source'), 32)
    feedback = data.get('feedback')
    final_text = (data.get('final_text') or '').strip()
    agent_guidance = data.get('agent_guidance')
    selection_state_id = sanitize_field(data.get('selection_state_id'), 128)
    regeneration_result_id = _valid_history_result_id(data.get('regeneration_result_id'))
    base_hypothesis_state_id = sanitize_field(data.get('base_hypothesis_state_id'), 128)
    base_hypothesis_index_raw = data.get('base_hypothesis_index')
    request_id = (getattr(g, 'operation_debug', {}) or {}).get('request_id')
    session['finalize_feedback'] = feedback or ''

    generator = get_or_restore_generator()
    if not generator:
        return jsonify({'success': False, 'error': 'No active session'})

    print(
        f"[api_select] request={request_id} session={_session_debug_id()} "
        f"selection={selection} has_custom={bool(custom_hypothesis)} "
        f"has_feedback={bool(feedback)} has_final_text={bool(final_text)} has_guidance={bool(agent_guidance)} "
        f"custom_source={custom_source or 'none'} "
        f"has_selection_state_id={bool(selection_state_id)} has_base_index={base_hypothesis_index_raw is not None} "
        f"has_base_state_id={bool(base_hypothesis_state_id)}"
    )

    try:
        trusted_selection_result = None
        if regeneration_result_id:
            if selection == 'custom':
                return jsonify({'success': False, 'error': 'Invalid regeneration result selection'})
            try:
                trusted_selection_index = int(selection)
            except (TypeError, ValueError):
                return jsonify({'success': False, 'error': 'Invalid hypothesis selection'})
            trusted_selection_result = _studio_resolve_regeneration_result(
                generator, trusted_selection_index, regeneration_result_id
            )
            if trusted_selection_result is None:
                return jsonify({'success': False, 'error': 'That regeneration result is no longer available.'})
            trusted_snapshot = trusted_selection_result['snapshot']
            final_text = (
                trusted_snapshot.get('full_text')
                or trusted_snapshot.get('formatted_output')
                or trusted_snapshot.get('hypothesis')
                or ''
            ).strip()
            if not final_text:
                return jsonify({'success': False, 'error': 'That regeneration result has no hypothesis content.'})

        has_final_text = bool(final_text)
        if not has_final_text and isinstance(custom_hypothesis, str) and custom_hypothesis.strip():
            final_text = custom_hypothesis.strip()
            has_final_text = True

        if has_final_text:
            if selection != 'custom':
                if trusted_selection_result is None:
                    _seed_hypothesis_from_request_draft(generator, selection, selection_state_id, final_text)
            elif base_hypothesis_index_raw is not None:
                _seed_hypothesis_from_request_draft(
                    generator,
                    base_hypothesis_index_raw,
                    base_hypothesis_state_id,
                    final_text,
                )

        state_ids = _ensure_hypothesis_state_ids(generator)
        bypassed_stale_guard = False

        selection_index = None
        if selection != 'custom':
            try:
                selection_index = int(selection)
            except (TypeError, ValueError):
                return jsonify({'success': False, 'error': 'Invalid hypothesis selection'})
            if selection_index < 1 or selection_index > len(generator.hypotheses):
                return jsonify({'success': False, 'error': 'Invalid hypothesis selection'})
            current_state_id = state_ids[selection_index - 1] if selection_index - 1 < len(state_ids) else None
            if trusted_selection_result is not None and (
                    not current_state_id or selection_state_id != current_state_id):
                return jsonify({
                    'success': False,
                    'error': 'The hypothesis changed and this regeneration result is out of date. Re-open it and try again.',
                    'stale_selection': True,
                })
            if not selection_state_id and not has_final_text:
                fallback_text = generator.hypotheses[selection_index - 1]
                if isinstance(fallback_text, str) and fallback_text.strip():
                    final_text = fallback_text.strip()
                    has_final_text = True
                    bypassed_stale_guard = True
                else:
                    return jsonify({
                        'success': False,
                        'error': 'Selection is missing a state id. Please re-open the hypothesis and confirm again.',
                        'stale_selection': True,
                    })
            if (not current_state_id or selection_state_id != current_state_id) and not has_final_text:
                fallback_text = generator.hypotheses[selection_index - 1]
                if isinstance(fallback_text, str) and fallback_text.strip():
                    final_text = fallback_text.strip()
                    has_final_text = True
                    bypassed_stale_guard = True
                else:
                    return jsonify({
                        'success': False,
                        'error': 'Your selected hypothesis is out of date. Please re-open and confirm again.',
                        'stale_selection': True,
                    })
            if (not current_state_id or selection_state_id != current_state_id) and has_final_text:
                bypassed_stale_guard = True

        base_hypothesis_index = None
        if base_hypothesis_index_raw is not None and str(base_hypothesis_index_raw).strip() != '':
            try:
                base_hypothesis_index = int(base_hypothesis_index_raw)
            except (TypeError, ValueError):
                return jsonify({'success': False, 'error': 'Invalid base hypothesis index'})
            if base_hypothesis_index < 1 or base_hypothesis_index > len(generator.hypotheses):
                return jsonify({'success': False, 'error': 'Invalid base hypothesis index'})
            current_base_state_id = (
                state_ids[base_hypothesis_index - 1]
                if base_hypothesis_index - 1 < len(state_ids)
                else None
            )
            if not base_hypothesis_state_id and not has_final_text:
                return jsonify({
                    'success': False,
                    'error': 'Edited selection is missing a base state id. Please re-open and confirm again.',
                    'stale_selection': True,
                })
            if (not current_base_state_id or base_hypothesis_state_id != current_base_state_id) and not has_final_text:
                return jsonify({
                    'success': False,
                    'error': 'The hypothesis changed after you started editing. Please review the latest version and confirm again.',
                    'stale_selection': True,
                })
            if (not current_base_state_id or base_hypothesis_state_id != current_base_state_id) and has_final_text:
                bypassed_stale_guard = True

        if selection == 'custom':
            if custom_source not in {'direct_custom', 'base_edit'} and not has_final_text:
                return jsonify({
                    'success': False,
                    'error': 'Custom selection is missing source metadata. Please re-open and confirm again.',
                    'stale_selection': True,
                })
            if custom_source not in {'direct_custom', 'base_edit'} and has_final_text:
                custom_source = 'direct_custom'
            if custom_source == 'base_edit':
                if (base_hypothesis_index is None or not base_hypothesis_state_id) and not has_final_text:
                    return jsonify({
                        'success': False,
                        'error': 'Edited custom selection is missing base hypothesis state. Please re-open and confirm again.',
                        'stale_selection': True,
                    })
                if (base_hypothesis_index is None or not base_hypothesis_state_id) and has_final_text:
                    custom_source = 'direct_custom'
                    base_hypothesis_index = None
                    base_hypothesis_state_id = ''
            else:
                # Direct custom text should not carry base-hypothesis guard fields.
                if (base_hypothesis_index is not None or base_hypothesis_state_id) and not has_final_text:
                    return jsonify({
                        'success': False,
                        'error': 'Unexpected base hypothesis metadata for direct custom selection.',
                        'stale_selection': True,
                    })
                if (base_hypothesis_index is not None or base_hypothesis_state_id) and has_final_text:
                    base_hypothesis_index = None
                    base_hypothesis_state_id = ''

        # Prepare selection for CLI method
        if final_text:
            selection_value = final_text
            feedback_for_save = None
        elif selection == 'custom':
            if not custom_hypothesis:
                return jsonify({'success': False, 'error': 'No custom hypothesis provided'})
            selection_value = custom_hypothesis
            feedback_for_save = feedback
        else:
            selection_value = selection_index
            feedback_for_save = feedback

        # Use CLI method to save selection (skip if dry-run mode)
        if CLI_DRY_RUN:
            output_path = "[DRY RUN - Not Saved]"
            log_path = "[DRY RUN - Not Saved]"
            guidance_path = "[DRY RUN - Not Saved]" if agent_guidance else None
            selected_hypothesis_s3_uri = None
        else:
            if final_text:
                output_path = generator.save_selection_raw(
                    final_text,
                    agent_guidance,
                    username=session.get('username'),
                )
            else:
                output_path = generator.save_selection(
                    selection_value,
                    feedback_for_save,
                    agent_guidance,
                    username=session.get('username'),
                )
            guidance_path = None
            timestamped_output_path = None
            if generator.final_selection:
                guidance_path = generator.final_selection.get('agent_guidance_path')
                timestamped_output_path = generator.final_selection.get('timestamped_output_path')
            log_path = generator.save_session_log()
            selected_hypothesis_s3_uri = None
            checkpoint_s3_prefix = (
                session.get('checkpoint_s3_prefix')
                or session.get('project_label')
                or session.get('project_key_digest')
                or ''
            )
            try:
                selected_hypothesis_s3_uri = upload_selected_hypothesis_to_s3(
                    output_path, checkpoint_s3_prefix
                )
                if selected_hypothesis_s3_uri:
                    log_event('selected_hypothesis_uploaded', {
                        's3_uri': selected_hypothesis_s3_uri,
                        'output_path': output_path,
                    })
                else:
                    log_event('selected_hypothesis_upload_skipped', {
                        'output_path': output_path,
                        'checkpoint_s3_prefix': checkpoint_s3_prefix,
                    })
            except Exception as exc:
                log_event('selected_hypothesis_upload_failed', {
                    'output_path': output_path,
                    'checkpoint_s3_prefix': checkpoint_s3_prefix,
                    'error': str(exc),
                })
            if timestamped_output_path:
                try:
                    timestamped_s3_uri = upload_selected_hypothesis_to_s3(
                        timestamped_output_path, checkpoint_s3_prefix
                    )
                    if timestamped_s3_uri:
                        log_event('selected_hypothesis_timestamped_uploaded', {
                            's3_uri': timestamped_s3_uri,
                            'output_path': timestamped_output_path,
                        })
                    else:
                        log_event('selected_hypothesis_timestamped_upload_skipped', {
                            'output_path': timestamped_output_path,
                            'checkpoint_s3_prefix': checkpoint_s3_prefix,
                        })
                except Exception as exc:
                    log_event('selected_hypothesis_timestamped_upload_failed', {
                        'output_path': timestamped_output_path,
                        'checkpoint_s3_prefix': checkpoint_s3_prefix,
                        'error': str(exc),
                    })

        # Get final hypothesis content
        if final_text:
            hypothesis_text = final_text
        else:
            if isinstance(selection_value, int):
                hypothesis_text = generator.hypotheses[selection_value - 1]
            else:
                hypothesis_text = selection_value

        # Apply feedback if provided
        if feedback and not final_text:
            hypothesis_text = generator.apply_scientist_feedback(hypothesis_text, feedback)

        # Sanitize to drop any trailing extraneous content (skip when saving raw final text)
        if not final_text:
            hypothesis_text = generator._sanitize_hypothesis_text(hypothesis_text)

        progress = increment_project_submission(
            session.get('project_key_digest'),
            username=session.get('username'),
            label=session.get('project_label'),
        )
        session['project_submission_count'] = progress.get('submission_count', session.get('project_submission_count', 0))
        session['project_round'] = _get_project_round_from_disk()

        # Parse to get formatted output
        sections = generator.parse_hypothesis_sections(hypothesis_text)
        if final_text:
            output_content = hypothesis_text
        else:
            output_content = ""
            if sections['hypothesis']:
                output_content += f"**Hypothesis**: {sections['hypothesis']}\n\n"
            if sections['justification']:
                output_content += f"**Justification**: {sections['justification']}\n"
            if sections.get('plan'):
                output_content += f"\n**Plan**: {sections['plan']}\n"
            if feedback and not final_text:
                output_content += f"\n**Feedback from expert scientist**: {feedback.strip()}\n"
            if not output_content.strip():
                output_content = hypothesis_text
        if agent_guidance:
            output_content = (
                output_content.rstrip()
                + "\n\n**Overall Research Feedback**: "
                + agent_guidance.strip()
                + "\n"
            )

        log_full_event_to_s3(
            'hypothesis_finalized',
            {
                'selection': selection,
                'selection_value': selection_value,
                'selection_state_id': selection_state_id or None,
                'custom_source': custom_source or None,
                'base_hypothesis_index': base_hypothesis_index,
                'base_hypothesis_state_id': base_hypothesis_state_id or None,
                'bypassed_stale_guard': bypassed_stale_guard,
                'regeneration_result_id': regeneration_result_id,
                'feedback': feedback,
                'agent_guidance': agent_guidance,
                'custom_hypothesis': selection_value if isinstance(selection_value, str) else None,
                'full_text': hypothesis_text,
                'formatted_output': output_content,
                'citations': getattr(generator.hypothesis_gen_agent, 'last_response_citations', []),
                'documents': getattr(generator, 'citation_documents', []),
            },
            hypothesis_index=selection_value if isinstance(selection_value, int) else None,
            source='finalize',
        )

        log_event('select_hypothesis', {
            'selection': selection,
            'hypothesis_index': selection_value if isinstance(selection_value, int) else None,
            'title': sections.get('title'),
            'has_feedback': bool(feedback),
            'has_agent_guidance': bool(agent_guidance),
            'has_selection_state_id': bool(selection_state_id),
            'custom_source': custom_source or None,
            'base_hypothesis_index': base_hypothesis_index,
            'has_base_hypothesis_state_id': bool(base_hypothesis_state_id),
        })
        admin_notification_status = None
        if not CLI_DRY_RUN:
            admin_notification_status = send_admin_selection_notification(
                username=session.get('username'),
                project_prefix=checkpoint_s3_prefix or 'unknown',
                hypothesis_text=output_content,
                s3_uri=selected_hypothesis_s3_uri,
                round_num=progress.get('submission_count'),
            )
        s3_uri = None
        if not CLI_DRY_RUN and log_path:
            checkpoint_s3_prefix = (
                session.get('checkpoint_s3_prefix')
                or session.get('project_label')
                or session.get('project_key_digest')
                or 'unknown'
            )
            round_num = progress.get('submission_count', 0)
            try:
                s3_uri = upload_session_log_to_s3(log_path, checkpoint_s3_prefix, round_num)
                if s3_uri:
                    log_event('session_log_uploaded', {'s3_uri': s3_uri, 'log_path': log_path})
                else:
                    log_event('session_log_upload_failed', {'log_path': log_path})
            except Exception as exc:
                log_event('session_log_upload_failed', {'log_path': log_path, 'error': str(exc)})

        # Durable chronology: the selection is the round's closing substantive
        # event. Snapshot the exact final scientific artifact the scientist
        # selected (hypothesis/justification/optional plan). Only reached after
        # selected_hypothesis.md was persisted above; best-effort, non-fatal.
        if not CLI_DRY_RUN:
            _studio_record_selection(generator, {
                'index': selection_value if isinstance(selection_value, int) else None,
                'title': sections.get('title'),
                'full_text': hypothesis_text,
                'hypothesis': sections.get('hypothesis') or _extract_hypothesis_display_text(hypothesis_text),
                'justification': sections.get('justification'),
                'plan': sections.get('plan'),
                'formatted_output': output_content,
                'source_result_event_id': regeneration_result_id,
            })

        # Arm cleanup for when the user closes the site after final selection.
        session['cleanup_ready'] = not CLI_DRY_RUN
        log_event('cleanup_armed', {'enabled': bool(session.get('cleanup_ready'))})
        return jsonify({
            'success': True,
            'output_path': output_path,
            'timestamped_output_path': (
                timestamped_output_path
                if not CLI_DRY_RUN and 'timestamped_output_path' in locals()
                else "[DRY RUN - Not Saved]"
            ),
            'log_path': log_path,
            'session_log_s3_uri': s3_uri,
            'selected_hypothesis_s3_uri': selected_hypothesis_s3_uri,
            'hypothesis': output_content,
            'plan': sections.get('plan'),
            'full_text': hypothesis_text,
            'citations': getattr(generator.hypothesis_gen_agent, 'last_response_citations', []),
            'documents': getattr(generator, 'citation_documents', []),
            'agent_guidance': agent_guidance,
            'guidance_path': guidance_path,
            'admin_notification': admin_notification_status,
            'project_submission_count': session.get('project_submission_count', progress.get('submission_count', 0)),
            'project_round': _get_project_round_from_disk(),
            'cleanup_ready': bool(session.get('cleanup_ready')),
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/post_selection_actions', methods=['POST'])
def api_post_selection_actions():
    """Store post-selection activity log entries."""
    if _is_audit_only_mode():
        return _reject_audit_only('Post-selection logging')
    generator = get_or_restore_generator()

    data = request.json or {}
    raw_actions = data.get('post_selection_actions') or ''
    post_selection_actions = sanitize_field(raw_actions, 2000)

    try:
        if generator:
            if post_selection_actions and hasattr(generator, 'log_interaction'):
                generator.log_interaction('post_selection_actions', {
                    'details': post_selection_actions
                })
            if generator.final_selection is not None:
                generator.final_selection['post_selection_actions'] = post_selection_actions or None

        log_path = "[DRY RUN - Not Saved]"
        s3_uri = None
        if not CLI_DRY_RUN and generator:
            log_path = generator.save_session_log()
            if log_path:
                checkpoint_s3_prefix = (
                    session.get('checkpoint_s3_prefix')
                    or session.get('project_label')
                    or session.get('project_key_digest')
                    or 'unknown'
                )
                round_num = session.get('project_submission_count', 0)
                try:
                    s3_uri = upload_session_log_to_s3(log_path, checkpoint_s3_prefix, round_num)
                    if s3_uri:
                        log_event('session_log_uploaded', {'s3_uri': s3_uri, 'log_path': log_path})
                    else:
                        log_event('session_log_upload_failed', {'log_path': log_path})
                except Exception as exc:
                    log_event('session_log_upload_failed', {'log_path': log_path, 'error': str(exc)})

        log_event('post_selection_actions', {
            'has_post_selection_actions': bool(post_selection_actions),
        })

        # Best-effort cleanup after activity log submission (user is done).
        cleanup_prefix = session.get('checkpoint_s3_prefix')
        cleanup_result = None
        cleanup_reason = None
        if AUTO_CLEANUP_ENABLED and cleanup_prefix:
            try:
                cleaned, cleanup_error = _cleanup_disk_cache(cleanup_prefix)
                cleanup_result = cleaned
                cleanup_reason = cleanup_error
                if cleaned:
                    log_event('checkpoint_cache_cleared', {
                        'checkpoint_s3_prefix': cleanup_prefix,
                        'source': 'post_selection_actions',
                    })
                else:
                    log_event('checkpoint_cache_clear_skipped', {
                        'checkpoint_s3_prefix': cleanup_prefix,
                        'reason': cleanup_error,
                        'source': 'post_selection_actions',
                    })
            except Exception as exc:
                cleanup_reason = str(exc)
                log_event('checkpoint_cache_clear_failed', {
                    'checkpoint_s3_prefix': cleanup_prefix,
                    'error': str(exc),
                    'source': 'post_selection_actions',
                })
            session['cleanup_ready'] = False

        return jsonify({
            'success': True,
            'log_path': log_path,
            'session_log_s3_uri': s3_uri,
            'cleanup': {
                'attempted': bool(cleanup_prefix and AUTO_CLEANUP_ENABLED),
                'cleaned': cleanup_result,
                'reason': cleanup_reason,
            },
        })
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)})


def _render_error_page(status_code, heading, message, *, show_retry=False):
    """Render a restrained V2 error page without exposing exception details."""
    return render_template(
        f'{status_code}.html' if status_code in {404, 500} else 'error.html',
        error_code=status_code,
        error_heading=heading,
        error_message=message,
        show_retry=show_retry,
        bare_shell=True,
        hide_appbar_actions=True,
        page_class='v2-page-error',
    ), status_code


# Error handlers
@app.errorhandler(400)
def bad_request_error(error):
    """Handle malformed full-page requests."""
    return _render_error_page(
        400,
        'That request could not be understood.',
        'The link or request was incomplete, so DiscoveryAgent could not open it.',
    )


@app.errorhandler(403)
def forbidden_error(error):
    """Handle requests for unavailable local resources."""
    return _render_error_page(
        403,
        'That resource is not available.',
        'DiscoveryAgent could not open the requested local resource. Return to Projects to continue.',
    )


@app.errorhandler(404)
def not_found_error(error):
    """Handle 404 errors."""
    return _render_error_page(
        404,
        'We couldn\'t find that page',
        'The page may have moved, or the address may be incorrect.',
    )


@app.errorhandler(413)
def request_too_large_error(error):
    """Handle local resources that exceed an existing size limit."""
    return _render_error_page(
        413,
        'That item is too large to open here.',
        'Return to Projects and choose a smaller local file or artifact.',
    )


@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors."""
    return _render_error_page(
        500,
        'DiscoveryAgent hit an unexpected problem.',
        'Try loading the page again. If the problem continues, return to Projects and resume from there.',
        show_retry=True,
    )


# ============================================================================
# Admin: interaction log download
# ============================================================================

@app.route('/api/admin/interaction_logs', methods=['GET'])
def api_admin_interaction_logs():
    """Download the full interaction log as a JSONL file.

    Protected by DISCOVERY_AGENT_ADMIN_TOKEN env var.  Pass the token in the
    request with the header:  X-Admin-Token: <token>
    Or as a query param:      ?token=<token>

    Returns the raw JSONL text so it can be piped into any JSON processor.
    """
    if not ADMIN_TOKEN:
        return jsonify({'success': False, 'error': 'Admin endpoint is disabled'}), 503
    provided = request.headers.get('X-Admin-Token', '')
    if not hmac.compare_digest(provided.strip(), ADMIN_TOKEN.strip()):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    if not INTERACTION_LOG_PATH.exists():
        return jsonify({'success': True, 'records': [], 'count': 0})

    fmt = request.args.get('format', 'jsonl').lower()

    try:
        with _interaction_log_lock:
            raw = INTERACTION_LOG_PATH.read_text(encoding='utf-8')
    except Exception as exc:
        return jsonify({'success': False, 'error': f'Could not read log: {exc}'}), 500

    if fmt == 'json':
        records = []
        for line in raw.splitlines():
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
        return jsonify({'success': True, 'records': records, 'count': len(records)})

    # Default: return raw JSONL as a downloadable file
    response = make_response(raw)
    response.headers['Content-Type'] = 'application/x-ndjson'
    response.headers['Content-Disposition'] = 'attachment; filename="audit_interactions.jsonl"'
    return response


# ---------------------------------------------------------------------------
# Checkpoint file browser
# ---------------------------------------------------------------------------

_ROUND_WORKSPACE_QUERY_REPORT_PATH_RE = re.compile(
    r'^(?:query_(?P<direct>\d+)/query_(?P=direct)\.md'
    r'|da_query_reports/query_(?P<flat>\d+)\.md'
    r'|da_query_reports/query_(?P<nested>\d+)/query_(?P=nested)\.md)$'
)
_ROUND_WORKSPACE_QUERY_RESULT_WRAPPERS = frozenset({
    'Agent Report',
    'Deep Research Report',
    'Execution Agent Report',
    'Web Search Report',
})


def _round_workspace_markdown_headings(markdown: str) -> list[dict]:
    """Tokenize ATX headings outside fenced code, retaining source offsets."""
    headings: list[dict] = []
    offset = 0
    fence: tuple[str, int] | None = None
    for line in markdown.splitlines(keepends=True):
        stripped = line.lstrip()
        fence_match = re.match(r'(`{3,}|~{3,})', stripped)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = (marker[0], len(marker))
            elif marker[0] == fence[0] and len(marker) >= fence[1]:
                fence = None
            offset += len(line)
            continue
        if fence is None:
            match = re.match(r'^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*(?:\r?\n)?$', line)
            if match:
                headings.append({
                    'level': len(match.group(1)),
                    'text': match.group(2).strip(),
                    'start': offset,
                    'end': offset + len(line),
                })
        offset += len(line)
    return headings


def _round_workspace_query_number(relative_path: str) -> int | None:
    match = _ROUND_WORKSPACE_QUERY_REPORT_PATH_RE.fullmatch(relative_path)
    if not match:
        return None
    value = next((part for part in match.groups() if part is not None), None)
    return int(value) if value is not None else None


def _trim_duplicate_generated_title(markdown: str, title: str) -> str:
    """Remove only an immediately repeated canonical title after a wrapper."""
    leading = len(markdown) - len(markdown.lstrip())
    candidate = markdown[leading:]
    headings = _round_workspace_markdown_headings(candidate)
    if headings and headings[0]['start'] == 0 and headings[0]['level'] == 1 and headings[0]['text'] == title:
        return candidate[headings[0]['end']:].lstrip()
    return markdown.lstrip()


def _extract_generated_round_report_body(markdown: str) -> str | None:
    """Return the body after the verified legacy ``## Round Report`` wrapper."""
    match = re.search(r'(?m)^## Round Report[ \t]*\r?\n(?:[ \t]*\r?\n)?', markdown)
    if not match:
        return None
    body = markdown[match.end():].strip()
    return body or None


def prepare_generated_report_for_display(
    report_type: str,
    round_number: int,
    relative_path: str,
    markdown_text: str,
) -> str:
    """Canonical generated-report display preparation shared by legacy and V2."""
    normalized_path = str(relative_path or '').replace('\\', '/').lstrip('/')
    if report_type == 'round' and normalized_path == 'round_report.md':
        title = f'Round {round_number} Report'
        body = _extract_generated_round_report_body(markdown_text)
        if body is None:
            return markdown_text
        body = _trim_duplicate_generated_title(body, title)
        return f'# {title}\n\n{body}' if body else markdown_text
    if report_type == 'query':
        return _prepare_round_workspace_query_markdown_for_display(
            round_number, normalized_path, markdown_text,
        )
    return markdown_text


def _prepare_round_workspace_query_markdown_for_display(
    round_index: int,
    normalized_path: str,
    markdown: str,
) -> str:
    """Normalize only verified generated query wrappers."""
    headings = _round_workspace_markdown_headings(markdown)
    if not headings:
        return markdown
    first_nonblank = len(markdown) - len(markdown.lstrip())
    title_heading = headings[0]
    if title_heading['start'] != first_nonblank or title_heading['level'] != 1:
        return markdown
    query_number = _round_workspace_query_number(normalized_path)
    if query_number is None:
        return markdown
    title = f'Query {query_number} Details'
    if title_heading['text'] != title:
        return markdown
    query_heading = next(
        (heading for heading in headings[1:]
         if heading['level'] == 1 and heading['text'] == 'Query'),
        None,
    )
    if query_heading is None:
        return markdown
    result_wrapper = next(
        (heading for heading in headings
         if heading['start'] > query_heading['start']
         and heading['level'] == 1
         and heading['text'] in _ROUND_WORKSPACE_QUERY_RESULT_WRAPPERS),
        None,
    )
    if result_wrapper is None:
        return markdown
    body = _trim_duplicate_generated_title(markdown[result_wrapper['end']:], title)
    return f'# {title}\n\n{body}' if body else markdown


def _prepare_round_workspace_markdown_for_display(
    round_index: int,
    relative_path: str,
    markdown: str,
) -> str:
    """Remove generated report preambles for the V2 viewer only.

    Classification is deliberately path-based and the transformation is
    conditional on the exact generator wrapper structure. Any ambiguous or
    malformed document is returned byte-for-byte unchanged.
    """
    normalized_path = str(relative_path or '').replace('\\', '/').lstrip('/')
    if normalized_path == 'round_report.md':
        return prepare_generated_report_for_display(
            'round', round_index, normalized_path, markdown,
        )
    if _round_workspace_query_number(normalized_path) is not None:
        return prepare_generated_report_for_display(
            'query', round_index, normalized_path, markdown,
        )
    return markdown


def _get_checkpoint_base() -> Path | None:
    """Return the checkpoints/ directory for the current session."""
    project_cfg = _get_project_config()
    checkpoint_s3_prefix = session.get('checkpoint_s3_prefix') or project_cfg.get('checkpoint_s3_prefix')
    if checkpoint_s3_prefix:
        return _disk_root_for_prefix(checkpoint_s3_prefix) / "checkpoint" / "checkpoints"
    checkpoint_dir = project_cfg.get('checkpoint_dir') or CLI_CHECKPOINT_DIR
    if checkpoint_dir:
        root = _resolve_checkpoint_root(Path(checkpoint_dir))
        return root / "checkpoints"
    return None


def _build_tree(base: Path, rel: str = "", depth: int = 0, max_depth: int = 5) -> list[dict]:
    """Recursively build a directory tree (relative paths, max depth)."""
    if depth > max_depth:
        return []
    current = base / rel if rel else base
    if not current.is_dir():
        return []
    entries = []
    try:
        children = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return []
    for child in children:
        child_rel = f"{rel}/{child.name}" if rel else child.name
        if child.is_dir():
            entries.append({
                'name': child.name,
                'type': 'dir',
                'path': child_rel,
                'children': _build_tree(base, child_rel, depth + 1, max_depth),
            })
        else:
            entries.append({
                'name': child.name,
                'type': 'file',
                'path': child_rel,
            })
    return entries


@app.route('/checkpoint_browser')
def checkpoint_browser():
    """Render the checkpoint file browser."""
    guide_html = ''
    guide_path = Path(__file__).resolve().parent.parent / 'guides' / 'CHECKPOINT_STRUCTURE.md'
    if guide_path.is_file():
        try:
            guide_html = render_markdown_to_html(guide_path.read_text(encoding='utf-8'))
        except Exception:
            pass
    return render_template('checkpoint_browser.html', guide_html=Markup(guide_html))


@app.route('/checkpoint_assets/<path:filename>')
def checkpoint_browser_asset(filename):
    """Serve files from the checkpoint browser tree."""

    base = _get_checkpoint_base()
    if not base or not base.is_dir():
        abort(404)

    unresolved_target = base / filename
    try:
        relative = unresolved_target.relative_to(base)
    except ValueError:
        abort(403)
    if not relative.parts or not re.fullmatch(r'round_\d+', relative.parts[0]):
        abort(404)
    round_dir = base / relative.parts[0]
    try:
        status = _v2_round_status(_inspect_latest_round(round_dir))
    except OSError:
        abort(404)
    if _round_workspace_visibility(unresolved_target, round_dir, status) == 'hidden':
        abort(404)
    target_path = unresolved_target.resolve()
    if not _path_within_root(target_path, base.resolve()) or not target_path.is_file():
        abort(404)
    # Nested symlinks are hidden before resolution; repeat round containment on
    # the canonical target to prevent a link into another round/project.
    if not _path_within_root(target_path, round_dir.resolve()):
        abort(404)
    return send_from_directory(target_path.parent, target_path.name)


_ROUND_WORKSPACE_MARKDOWN_IMAGE_RE = re.compile(
    r'!\[(?P<alt>[^\]]*)\]\(\s*(?P<dest><[^>]+>|[^\s)]+)'
    r'(?P<title>\s+(?:"[^"]*"|\'[^\']*\'))?\s*\)'
)


def _prepare_round_workspace_report_figures(
    round_index: int,
    source_id: str,
    relative_path: str,
    markdown: str,
    artifact_role: str,
) -> tuple[str, list[dict]]:
    """Replace recognized Markdown image destinations with opaque display tokens.

    The artifact role still controls document normalization elsewhere, but
    embedded figures use the same trusted resolution path in every Markdown
    file displayed by Round Workspace.
    """
    registry = _round_workspace_artifact_registry(round_index)
    checkpoint_source = next((
        key for key, value in registry.items() if value.get('kind') == 'checkpoint'
    ), '')
    report_parent = PurePosixPath(relative_path).parent
    figures: list[dict] = []

    def replace(match: re.Match) -> str:
        alt = match.group('alt').strip()
        raw_dest = match.group('dest')
        dest = raw_dest[1:-1] if raw_dest.startswith('<') and raw_dest.endswith('>') else raw_dest
        suffix = PurePosixPath(dest).suffix.lower()
        if suffix == '.pdf' or suffix not in ROUND_WORKSPACE_FIGURE_EXTENSIONS:
            return match.group(0)

        resolved_url: str | None = None
        if not PurePosixPath(dest).is_absolute():
            candidate = _round_workspace_safe_relative(
                (report_parent / PurePosixPath(dest)).as_posix()
            )
            if candidate is not None and checkpoint_source:
                target = _round_workspace_resolve_artifact(
                    round_index, checkpoint_source, candidate.as_posix(),
                )
                if target is not None:
                    resolved_url = url_for(
                        'checkpoint_browser_asset',
                        filename=f'round_{round_index}/{candidate.as_posix()}',
                    )
        if resolved_url is None:
            comparable = dest.lstrip('./')
            for candidate_source, source in registry.items():
                if source.get('kind') == 'checkpoint':
                    continue
                for artifact_path, descriptor in source.get('files', {}).items():
                    display_path = descriptor.get('display_path', artifact_path)
                    if (
                        comparable in {artifact_path, display_path}
                        or dest.endswith(f'/{artifact_path}')
                        or dest.endswith(f'/{display_path}')
                    ):
                        resolved_url = url_for(
                            'api_round_workspace_artifact',
                            round=round_index,
                            source=candidate_source,
                            path=artifact_path,
                        )
                        break
                if resolved_url:
                    break

        lowered = dest.lower().replace('\\', '/')
        trusted_missing = (
            resolved_url is None
            and '..' not in PurePosixPath(lowered).parts
            and (
                lowered.startswith(('figures/', 'outputs/', 'images/', 'logs/images/'))
                or any(marker in lowered for marker in (
                    '/outputs/', '/analysis_output/', '/images/',
                ))
            )
        )
        if resolved_url is None and not trusted_missing:
            # Unauthorized references remain inert text, never a browser image
            # request and never an exposed absolute filesystem path.
            return f'**Figure unavailable:** {alt or "Referenced figure"}'

        token = f'rw_fig_{len(figures)}'
        figures.append({
            'token': token,
            'alt': alt,
            'available': resolved_url is not None,
            'url': resolved_url,
        })
        title = match.group('title') or ''
        return f'![{alt}](rw-report-figure:{token}{title})'

    return _ROUND_WORKSPACE_MARKDOWN_IMAGE_RE.sub(replace, markdown), figures


@app.route('/api/round_workspace_markdown')
def api_round_workspace_markdown():
    """Return display-normalized Markdown while leaving raw artifacts intact."""

    round_value = request.args.get('round', '')
    relative_path = request.args.get('path', '')
    if not round_value.isdigit() or not relative_path:
        return jsonify({'success': False, 'error': 'Missing or invalid file parameters'}), 400
    round_index = int(round_value)
    source_id = request.args.get('source', '')
    target = (
        _round_workspace_resolve_artifact(round_index, source_id, relative_path)
        if source_id
        else _round_workspace_artifact_path(round_index, relative_path)
    )
    if target is None:
        return jsonify({'success': False, 'error': 'File not found'}), 404
    if target.suffix.lower() not in {'.md', '.markdown'}:
        return jsonify({'success': False, 'error': 'File is not Markdown'}), 400
    try:
        raw = target.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return jsonify({'success': False, 'error': 'Unable to load file'}), 404
    artifact_role = (
        'round_report' if relative_path == 'round_report.md'
        else ('query_report' if _round_workspace_query_number(relative_path) is not None else 'markdown')
    )
    display = _prepare_round_workspace_markdown_for_display(
        round_index,
        relative_path,
        raw,
    )
    display, figures = _prepare_round_workspace_report_figures(
        round_index, source_id, relative_path, display, artifact_role,
    )
    return jsonify({
        'success': True,
        'display_text': display,
        'display_normalized': display != raw,
        'artifact_role': artifact_role,
        'figures': figures,
        # Compatibility for older cached clients. This is display content, not
        # raw disk content; raw bytes remain available only from artifact routes.
        'markdown': display,
    })


@app.route('/api/checkpoint_tree')
def api_checkpoint_tree():
    """Return the checkpoint directory tree as JSON."""
    base = _get_checkpoint_base()
    if not base or not base.is_dir():
        return jsonify({'success': False, 'error': 'Checkpoint directory not found'}), 404
    tree = _build_tree(base)
    return jsonify({'success': True, 'tree': tree})


_CHECKPOINT_TEXT_EXTENSIONS = frozenset({'.md', '.json', '.yaml', '.yml', '.txt', '.log', '.jsonl', '.csv', '.py', '.cfg'})
_CHECKPOINT_TRUNCATE_EXTENSIONS = frozenset({'.jsonl', '.log', '.csv'})
_CHECKPOINT_TRUNCATE_LINES = 200


@app.route('/api/checkpoint_file')
def api_checkpoint_file():
    """Return the contents of a checkpoint file."""
    base = _get_checkpoint_base()
    if not base or not base.is_dir():
        return jsonify({'success': False, 'error': 'Checkpoint directory not found'}), 404

    rel_path = request.args.get('path', '')
    if not rel_path:
        return jsonify({'success': False, 'error': 'Missing path parameter'}), 400

    unresolved_target = base / rel_path
    try:
        relative = unresolved_target.relative_to(base)
    except ValueError:
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    if not relative.parts or not re.fullmatch(r'round_\d+', relative.parts[0]):
        return jsonify({'success': False, 'error': 'File not found'}), 404
    round_dir = base / relative.parts[0]
    try:
        status = _v2_round_status(_inspect_latest_round(round_dir))
    except OSError:
        return jsonify({'success': False, 'error': 'File not found'}), 404
    if _round_workspace_visibility(unresolved_target, round_dir, status) == 'hidden':
        return jsonify({'success': False, 'error': 'File not found'}), 404

    # Canonical containment protects against traversal and links into another
    # round or project. The visibility classifier rejects links themselves.
    target = unresolved_target.resolve()
    if not _path_within_root(target, base.resolve()) or not _path_within_root(target, round_dir.resolve()):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    if not target.is_file():
        return jsonify({'success': False, 'error': 'File not found'}), 404

    ext = target.suffix.lower()
    if ext not in _CHECKPOINT_TEXT_EXTENSIONS:
        return jsonify({'success': False, 'error': f'Unsupported file type: {ext}'}), 400

    try:
        raw = target.read_text(encoding='utf-8', errors='replace')
    except Exception as exc:
        return jsonify({'success': False, 'error': f'Could not read file: {exc}'}), 500

    truncated = False
    if ext in _CHECKPOINT_TRUNCATE_EXTENSIONS:
        lines = raw.splitlines(True)
        if len(lines) > _CHECKPOINT_TRUNCATE_LINES:
            raw = ''.join(lines[:_CHECKPOINT_TRUNCATE_LINES])
            truncated = True

    html = None
    if ext == '.md':
        generator = get_generator()
        checkpoint_dir = base.resolve()
        code_dir = _resolve_code_dir(generator)
        if code_dir is not None:
            try:
                code_dir = code_dir.resolve()
            except Exception:
                code_dir = None
        html = render_checkpoint_markdown_html(
            raw,
            checkpoint_dir=checkpoint_dir,
            code_dir=code_dir,
            source_path=target,
        )

    # Determine render mode for the frontend
    if ext == '.md':
        render = 'markdown'
    elif ext in ('.json', '.yaml', '.yml', '.py', '.cfg'):
        render = 'code'
    else:
        render = 'plain'

    return jsonify({
        'success': True,
        'content': raw,
        'html': html,
        'render': render,
        'truncated': truncated,
        'truncated_at': _CHECKPOINT_TRUNCATE_LINES if truncated else None,
    })


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Interactive Hypothesis Generation Web UI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start with manual initialization
  python app.py --port 8000

  # Auto-initialize with checkpoint directory
  python app.py --checkpoint-dir /path/to/checkpoint/round_0

  # Auto-initialize with custom model
  python app.py --checkpoint-dir /path/to/checkpoint/round_0 --model claude-opus-4-6
        """
    )
    parser.add_argument('--port', type=int, default=8000, help='Port to run the server on')
    parser.add_argument('--checkpoint-dir', '--checkpoint_dir', type=str, default=None,
                        help='Checkpoint directory path (auto-initializes if provided)')
    parser.add_argument('--model', type=str, default=None,
                        choices=['claude-sonnet-4-5-20250929', DEFAULT_HYPOTHESIS_MODEL, 'claude-haiku-4-5-20251001'],
                        help='Hypothesis generation model')
    parser.add_argument('--code-dir', '--code_dir', type=str, default=None,
                        help='Path to code directory for Claude Code (enables code chat)')
    parser.add_argument('--legacy-hyp-gen', action='store_true',
                        help='Use legacy hypothesis generation (one model call per hypothesis)')
    parser.add_argument('--num-hypotheses', type=int, default=None,
                        help='Override number of hypotheses to generate')
    parser.add_argument('--include-background-literature', action='store_true',
                        help='Include deep_research_background.md when building hypothesis prompts')
    parser.add_argument('--generate-plan', action='store_true',
                        help='Generate an execution plan for each hypothesis')
    parser.add_argument('--enable-citations', dest='enable_citations', action='store_true',
                        help='Enable Claude citations by sending background documents as citable sources (Anthropic only)')
    parser.add_argument('--cache-documents', dest='cache_documents', action='store_true',
                        help='Enable prompt caching for document blocks when citations are enabled')
    parser.add_argument('--auto-initialize', dest='auto_initialize', action='store_true',
                        help='Automatically initialize the session on page load using CLI options')
    parser.add_argument('--readme-path', dest='readme_path', type=str, default=None,
                        help='Explicit path to dataset README (overrides auto-detected data/README.txt)')
    parser.add_argument('--github-url', dest='github_url', type=str, default=None,
                        help='GitHub repository URL for the project (shown on main page)')
    parser.add_argument('--dry-run', dest='dry_run', action='store_true',
                        help='Dry run mode - prevents saving hypothesis selections (useful for testing)')
    parser.add_argument('--dev', dest='dev_mode', action='store_true',
                        help='Enable developer UI (advanced controls). Default is user-facing simplified UI.')
    args = parser.parse_args()

    # Set global config from CLI arguments
    CLI_CHECKPOINT_DIR = args.checkpoint_dir
    CLI_HYPOTHESIS_MODEL = args.model
    CLI_LEGACY_HYP_GEN = args.legacy_hyp_gen
    CLI_NUM_HYPOTHESES = args.num_hypotheses
    CLI_CODE_DIR = args.code_dir
    CLI_INCLUDE_BACKGROUND_LITERATURE = args.include_background_literature
    CLI_GENERATE_PLAN = args.generate_plan
    CLI_AUTO_INITIALIZE = args.auto_initialize
    CLI_README_PATH = args.readme_path
    CLI_GITHUB_URL = args.github_url
    CLI_DRY_RUN = args.dry_run
    CLI_ENABLE_CITATIONS = args.enable_citations
    CLI_CACHE_DOCUMENTS = args.cache_documents
    CLI_DEV_MODE = args.dev_mode
    print(f"Starting server on port {args.port}")
    if CLI_CHECKPOINT_DIR:
        print(f"Auto-initializing with checkpoint: {CLI_CHECKPOINT_DIR}")
        if CLI_HYPOTHESIS_MODEL:
            print(f"Using model: {CLI_HYPOTHESIS_MODEL}")
        if CLI_LEGACY_HYP_GEN:
            print("Using legacy hypothesis generation mode.")
        if CLI_NUM_HYPOTHESES:
            print(f"Generating {CLI_NUM_HYPOTHESES} hypotheses.")
        if CLI_INCLUDE_BACKGROUND_LITERATURE:
            print("Including background literature (deep_research_background.md) in prompts.")
        if CLI_GENERATE_PLAN:
            print("Including plan generation for each hypothesis.")
    if CLI_DRY_RUN:
        print("⚠️  DRY RUN MODE ENABLED - Hypothesis selections will NOT be saved to disk")

    use_reloader = os.environ.get('DISCOVERY_AGENT_USE_RELOADER', '0') == '1'
    host = os.environ.get('DISCOVERY_AGENT_HOST', '127.0.0.1')
    browser_host = '127.0.0.1' if host in {'0.0.0.0', '::'} else host
    threading.Timer(0.8, webbrowser.open, args=(f'http://{browser_host}:{args.port}/',)).start()
    app.run(debug=True, port=args.port, host=host, use_reloader=use_reloader)
