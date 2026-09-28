"""Local scientific audit records; terminal verbosity never controls persistence.

Mirrored in the frontend/backend AIR packages because each is installed separately.
No provider objects are serialized implicitly and no global/cwd fallback is used.
"""
from __future__ import annotations

import datetime
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import stat
import sys
import uuid

REDACTED = "[REDACTED]"
_SECRET_NAME = re.compile(
    r"(?i)^(?:.*[_-])?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|auth[_-]?token|token|secret|client[_-]?secret|password|passwd|"
    r"credential[s]?|authorization|cookies?|set[_-]?cookie|session[_-]?(?:id|key|secret)|"
    r"private[_-]?key|authentication|auth(?:[_-](?:blob|data))?)$"
)
_SECRET_TEXT_KEY = (
    r"(?:[\w-]*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"auth[_-]?token|client[_-]?secret|password|passwd|credentials?|session[_-]?(?:id|key|secret)|"
    r"private[_-]?key)|authorization|authentication|cookie|set-cookie|token|secret)"
)
_ASSIGNMENT = re.compile(
    r"(?i)([\"']?\b" + _SECRET_TEXT_KEY + r"[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}&]+)"
)
_AUTH_HEADER = re.compile(r"(?im)(^(?:authorization|cookie|set-cookie)\s*:\s*)[^\r\n]+")
_BEARER = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+")
_URL_AUTH = re.compile(r"(https?://)[^\s/@]+(?::[^\s/@]*)?@")
_KEY = re.compile(r"\b(?:sk-(?:ant-)?[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{12,})\b")
_PEM = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S)
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")


def secret_name(name):
    # Token counts/budgets are scientific metadata, not authentication.
    normalized = re.sub(r"([a-z])([A-Z])", r"\1_\2", str(name))
    return bool(_SECRET_NAME.fullmatch(normalized))


def known_secret_values(environment=None):
    source = os.environ if environment is None else environment
    return tuple(sorted({str(v) for k, v in source.items()
                         if secret_name(k) and v and len(str(v)) >= 4},
                        key=len, reverse=True))


def sanitize_text(value, known_secrets=()):
    text = _ANSI.sub("", str(value)).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(c for c in text if c in "\n\t" or ord(c) >= 32)
    for secret in sorted(set(known_secrets), key=len, reverse=True):
        if secret:
            text = text.replace(secret, REDACTED)
    text = _PEM.sub(REDACTED, text)
    text = _URL_AUTH.sub(r"\1[REDACTED]@", text)
    text = re.sub(r"(?i)(\bauthorization\s*[:=]\s*)(?:basic|bearer)\s+[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(\b(?:cookie|set-cookie)\s*[:=]\s*)[^\r\n]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)([?&](?:key|api_key|access_token|refresh_token|token|password|x-amz-signature|x-goog-signature|sig)=)[^&#\s]+", r"\1[REDACTED]", text)
    text = _BEARER.sub(r"\1[REDACTED]", text)
    text = re.sub(r"(?i)((?:--|/)(?:api[_-]?key|token|secret|password|credential)(?:=|\s+))[^\s,;]+", r"\1[REDACTED]", text)
    text = _KEY.sub(REDACTED, text)
    text = _AUTH_HEADER.sub(r"\1[REDACTED]", text)
    return _ASSIGNMENT.sub(r"\1[REDACTED]", text)


def sanitize_record(value, known_secrets=()):
    """Redact nested fields and copies of their values, retaining scientific text."""
    secrets = set(known_secrets) | set(known_secret_values())

    def collect(item, sensitive=False):
        if isinstance(item, dict):
            for key, child in item.items():
                collect(child, sensitive or secret_name(key))
        elif isinstance(item, (list, tuple)):
            for child in item:
                collect(child, sensitive)
        elif sensitive and isinstance(item, str) and item:
            secrets.add(item)
    collect(value)

    def clean(item):
        if isinstance(item, dict):
            binary = item.get("type") == "base64" or str(item.get("url", "")).startswith("data:")
            return {sanitize_text(k, secrets): (
                REDACTED if secret_name(k) else
                {"omitted": "binary", "sha256": hashlib.sha256(str(v).encode()).hexdigest()}
                if (binary and k in {"data", "url"}) else clean(v))
                for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [clean(v) for v in item]
        if isinstance(item, bytes):
            return {"omitted": "binary", "bytes": len(item),
                    "sha256": hashlib.sha256(item).hexdigest()}
        if isinstance(item, str):
            if item.startswith("data:") and ";base64," in item:
                return {"omitted": "binary", "sha256": hashlib.sha256(item.encode()).hexdigest()}
            return sanitize_text(item, secrets)
        if item is None or isinstance(item, (bool, int, float)):
            return item
        if isinstance(item, Path):
            return sanitize_text(str(item), secrets)
        # Never repr()/dump arbitrary SDK clients, requests or authentication objects.
        return {"omitted_object_type": type(item).__name__}
    return clean(value)


def provider_exchange_details(source, api_params, response=None, *, text=None, error=None,
                              provider=None, attempt=None, retryable=None):
    """Allowlisted metadata, not a dump of SDK/auth objects or binary responses."""
    params = api_params if isinstance(api_params, dict) else {}
    def fields(obj, names):
        return {name: obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
                for name in names}
    request = {k: params[k] for k in (
        "model", "max_tokens", "temperature", "top_p", "thinking", "output_config",
        "betas", "context_management", "tools", "mcp_servers", "top_k", "stop_sequences",
    ) if k in params}
    request["message_count"] = len(params.get("messages") or [])
    metadata = fields(response, ("id", "model", "stop_reason", "_request_id", "stop_details"))
    metadata["usage"] = fields(getattr(response, "usage", None), (
        "input_tokens", "output_tokens", "cache_read_input_tokens",
        "cache_creation_input_tokens", "prompt_tokens", "completion_tokens", "total_tokens"))
    if not isinstance(
            metadata.get("stop_details"), (dict, str, type(None))):
        metadata["stop_details"] = fields(metadata["stop_details"], ("category",))
    choices = getattr(response, "choices", None) or []
    metadata["finish_reasons"] = [getattr(choice, "finish_reason", None) for choice in choices]
    if provider is None and error is not None:
        module = type(error).__module__.split(".")[0]
        provider = module if module in {"openai", "anthropic", "claude_agent_sdk"} else None
    result = {"source": source, "provider": provider, "operation": "model_exchange",
              "status": "error" if error else "refused" if metadata["stop_reason"] == "refusal"
              else "success" if text else "empty_response",
              "request": request, "response": metadata, "returned_text_chars": len(text or ""),
              "attempt": attempt, "retryable": retryable}
    if error is not None:
        result["error"] = {"type": type(error).__name__, "message": str(error),
                           **fields(error, ("status_code", "request_id"))}
    return result


class RedactingFilter(logging.Filter):
    def filter(self, record):
        record.msg = sanitize_text(record.getMessage(), known_secret_values())
        record.args = ()
        if record.exc_info:
            record.exc_text = sanitize_text(
                logging.Formatter().formatException(record.exc_info), known_secret_values())
            record.exc_info = None
        return True


class AuditTrail:
    """Append/fsync under a process lock; never rotate away scientific evidence."""

    def __init__(self, directory, agent):
        directory = Path(directory).expanduser()
        if not directory.is_absolute():
            raise ValueError("Scientific audit storage requires an explicit absolute project data directory")
        resolved = directory.resolve()
        package = Path(__file__).resolve().parents[1]
        source_roots = [package.parent]
        source_roots.extend(p for p in package.parents if (p / ".git").exists())
        if any(resolved == root or root in resolved.parents for root in source_roots):
            raise ValueError("Scientific audit storage must be outside the application source checkout/package")
        self.directory = resolved
        self.agent = agent
        self.run_id = uuid.uuid4().hex
        self.context = {}
        self.path = self.directory / "scientific_audit.jsonl"

    def append(self, event, details):
        self.directory.mkdir(parents=True, exist_ok=True)
        record = sanitize_record({
            "schema_version": 1, "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "event_id": uuid.uuid4().hex, "run_id": self.run_id,
            "scope": str(self.directory), "agent": self.agent,
            "context": self.context, "event": event, "details": details,
        })
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "ab", buffering=0) as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise OSError("Audit target must be a regular file")
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                # Byte order survives restarts and concurrent writers; no mutable counter.
                record["offset"] = os.lseek(stream.fileno(), 0, os.SEEK_END)
                payload = (json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n").encode()
                view = memoryview(payload)
                while view:
                    written = stream.write(view)
                    if not written:
                        raise OSError("Incomplete scientific audit write")
                    view = view[written:]
                os.fsync(stream.fileno())
        except (OSError, ValueError):
            print("ERROR: Scientific audit persistence failed; this operation is not fully audited.",
                  file=sys.__stderr__)
            raise
        return record["event_id"]


class AuditLoggerMixin:
    """AIR logging interface with durable detail and separately gated display."""

    def log_audit(self, event, details):
        trail = getattr(self, "audit_trail", None)
        if trail is not None:
            return trail.append(event, details)
        # Never silently reduce a missing sink to terminal-only logging.
        raise RuntimeError("Scientific audit sink was not initialized")

    def _display(self, summary, details=None, error=False):
        message = sanitize_text(summary, known_secret_values())
        (self.logger.error if error else self.logger.info)(message)
        if os.environ.get("DISCOVERYAGENT_DEBUG_PROMPTS") == "1" and details is not None:
            # Verbose display must not leak into a process-global file handler.
            print(json.dumps(sanitize_record(details), ensure_ascii=False),
                  file=getattr(self, "terminal_stream", sys.__stdout__))

    def log_prompt(self, role, prompt_text, prompt_name=""):
        self.log_audit("prompt", {"role": role, "name": prompt_name, "content": prompt_text})
        self._display(f"[PROMPT] {prompt_name} ({role})", prompt_text)

    def log_response(self, response_text, source="model"):
        self.log_audit("response", {"source": source, "content": response_text})
        self._display(f"[RESPONSE] {source}", response_text)

    def log_code(self, code, iteration=None, analysis=None):
        details = {"code": code, "iteration": iteration, "analysis": analysis}
        self.log_audit("code", details)
        self._display("[CODE] recorded", details)

    def log_error(self, error_msg, code=None):
        self.log_audit("error", {"status": "error", "message": error_msg, "code": code})
        # Arbitrary provider exception text may contain research payloads.
        self._display("[ERROR] Operation failed; details saved in the local audit trail.",
                      {"message": error_msg, "code": code}, error=True)

    def log_action(self, action, details=None):
        self.log_audit(action, details)
        if action in {"round_started", "round_resumed", "query_start"}:
            self.audit_trail.context[action] = sanitize_record(details)
        summary = f"[ACTION] {action} - {self.analysis_name}"
        status = details.get("status") if isinstance(details, dict) else None
        if isinstance(status, str) and status in {
                "started", "success", "error", "refused", "empty_response", "fallback"}:
            summary += f" status={details['status']}"
        self._display(summary, details)


class ProjectAuditLogger(AuditLoggerMixin):
    """File-only Studio adapter: no process-wide handlers shared across projects."""

    def __init__(self, directory, agent="interactive_hyp_gen"):
        self.analysis_name = agent
        self.audit_trail = AuditTrail(directory, agent)
        self.logger = logging.Logger(f"scientific-audit.{self.audit_trail.run_id}")
        self.logger.propagate = False
        self.logger.addHandler(logging.NullHandler())
