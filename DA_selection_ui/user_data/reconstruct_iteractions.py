#!/usr/bin/env python3
"""Reconstruct LLM-ready chat transcripts from telemetry events stored in S3.

This script pulls telemetry JSON events from the same S3 layout used by the app:

    s3://<bucket>/<telemetry_prefix>/session_logs/<project_key_hash>/session_<session_id>/<YYYYMMDD>/event_*.json

and converts them into a conversation structure that can be replayed into an LLM API.

Session identifier to provide:
- In S3, a session is uniquely identified by the pair:
  1) `project_key_hash` (the `<project_key_hash>` folder)
  2) `session_id` (the `session_<session_id>` folder)
- You can provide these directly with `--project` + `--session`, or pass any telemetry
  event URI with `--s3-uri` and the script will parse both identifiers.

How to get the identifiers:
- By username from DB event logs:
  `python user_data/example_user_sessions.py <username>`
- From an existing telemetry URI:
  `TARGET_SESSION_ID=<session_id> python user_data/example_telemetry_lookup.py`
  then copy one `s3://...` URI and pass it to `--s3-uri`.

Output format:
- `conversations[*].latest_llm_messages` and each segment's `llm_messages` are directly
  shaped as message lists for LLM APIs:
  `{"role": "user"|"assistant", "content": "..."}`
- `raw_messages` preserves the original role labels seen in telemetry.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3

DEFAULT_BUCKET = "agentic-science"
DEFAULT_PREFIX = "telemetry/session_logs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconstruct LLM-ready chat transcripts from telemetry events in S3."
    )
    parser.add_argument("--project", help="Project key hash in S3 path (<project_key_hash>).")
    parser.add_argument("--session", help="Session id in S3 path (session_<session_id>).")
    parser.add_argument(
        "--s3-uri",
        help="Any telemetry event S3 URI; project/session will be auto-parsed.",
    )
    parser.add_argument(
        "--bucket",
        default=DEFAULT_BUCKET,
        help=f"S3 bucket name (default: {DEFAULT_BUCKET}). Ignored if --s3-uri includes a bucket.",
    )
    parser.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help=f"Telemetry root prefix (default: {DEFAULT_PREFIX}).",
    )
    parser.add_argument(
        "--scope",
        action="append",
        help=(
            "Optional scope filter; can be repeated. "
            "Examples: hypothesis, background:summary, report:background, report:round."
        ),
    )
    parser.add_argument(
        "--latest-only",
        action="store_true",
        help="Emit only one latest conversation per scope (instead of all reset-delimited segments).",
    )
    parser.add_argument(
        "--output",
        help="Write reconstructed JSON output to this file (default: print to stdout).",
    )
    return parser.parse_args()


def parse_session_from_s3_uri(s3_uri: str) -> tuple[str | None, str | None, str | None]:
    """Return (bucket, project_key_hash, session_id) from a telemetry S3 URI."""
    match = re.match(r"^s3://([^/]+)/(.+)$", s3_uri.strip())
    if not match:
        return None, None, None
    bucket = match.group(1)
    key = match.group(2)
    path_match = re.search(r"/session_logs/([^/]+)/session_([^/]+)/", "/" + key)
    if not path_match:
        return bucket, None, None
    return bucket, path_match.group(1), path_match.group(2)


def parse_iso_timestamp(raw: Any) -> datetime:
    if not isinstance(raw, str) or not raw:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def list_event_keys(s3_client, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj.get("Key")
            if isinstance(key, str) and key.endswith(".json"):
                keys.append(key)
    keys.sort()
    return keys


def load_events(s3_client, bucket: str, prefix: str) -> list[dict]:
    events: list[dict] = []
    for key in list_event_keys(s3_client, bucket, prefix):
        body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        event = json.loads(body)
        if isinstance(event, dict):
            event["_s3_key"] = key
            events.append(event)
    events.sort(
        key=lambda e: (
            parse_iso_timestamp(e.get("timestamp")),
            str(e.get("event_id") or ""),
            str(e.get("_s3_key") or ""),
        )
    )
    return events


def stringify_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def normalize_raw_messages(messages: Any) -> list[dict[str, str]]:
    if not isinstance(messages, list):
        return []
    normalized: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        content = stringify_content(message.get("content"))
        if not role or not content:
            continue
        normalized.append({"role": role, "content": content})
    return normalized


def to_llm_messages(raw_messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Map raw messages into a conservative Claude-compatible role/content shape."""
    llm_messages: list[dict[str, str]] = []
    for message in raw_messages:
        role = message.get("role", "")
        content = message.get("content", "")
        if role == "user":
            llm_messages.append({"role": "user", "content": content})
        elif role == "assistant":
            llm_messages.append({"role": "assistant", "content": content})
        elif role == "system":
            llm_messages.append({"role": "assistant", "content": f"[system]\\n{content}"})
        else:
            llm_messages.append({"role": "assistant", "content": f"[{role}]\\n{content}"})
    return llm_messages


def infer_scope(event: dict) -> str:
    event_type = str(event.get("event_type") or "").strip()
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}

    chat_scope = payload.get("chat_scope")
    if isinstance(chat_scope, str) and chat_scope.strip():
        return chat_scope.strip()

    report_key = payload.get("report_key")
    if isinstance(report_key, str) and report_key.strip():
        return report_key.strip()

    if event_type == "report_chat":
        report_type = str(payload.get("report_type") or "background").strip().lower()
        return f"report:{report_type}"

    if event_type == "chat_reset":
        scope = str(payload.get("scope") or "").strip().lower()
        if scope == "report":
            report_type = str(payload.get("report_type") or "background").strip().lower()
            return f"report:{report_type}"
        if scope:
            return scope

    if event_type == "ask":
        return "hypothesis"

    if event_type == "chat":
        source = str(payload.get("source") or "").strip().lower()
        if source == "agent":
            return "hypothesis"
        mode = str(payload.get("mode") or "").strip().lower()
        if mode:
            return mode

    return "unknown"


def event_ref(event: dict) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return {
        "timestamp": event.get("timestamp"),
        "event_type": event.get("event_type"),
        "event_id": event.get("event_id"),
        "s3_key": event.get("_s3_key"),
        "source": payload.get("source"),
        "mode": payload.get("mode"),
    }


def new_segment(started_at: str | None, started_by: str | None) -> dict[str, Any]:
    return {
        "segment_index": 0,
        "started_at": started_at,
        "started_by": started_by,
        "ended_at": None,
        "ended_by": None,
        "events": [],
        "raw_messages": [],
        "llm_messages": [],
    }


def reconstruct_conversations(events: list[dict], scope_filters: set[str] | None = None) -> list[dict]:
    by_scope: dict[str, dict[str, Any]] = {}

    for event in events:
        scope = infer_scope(event)
        if scope_filters and scope not in scope_filters:
            continue

        event_type = str(event.get("event_type") or "").strip()
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        timestamp = event.get("timestamp")

        thread = by_scope.get(scope)
        if thread is None:
            thread = {"scope": scope, "segments": [new_segment(timestamp, "first_event")]}
            by_scope[scope] = thread
        segment = thread["segments"][-1]
        segment["events"].append(event_ref(event))

        snapshot = normalize_raw_messages(payload.get("conversation_messages"))
        if snapshot:
            segment["raw_messages"] = snapshot
            segment["llm_messages"] = to_llm_messages(snapshot)

        if event_type == "chat_reset":
            segment["ended_at"] = timestamp
            segment["ended_by"] = "chat_reset"
            thread["segments"].append(new_segment(timestamp, "post_reset"))
            continue

        if event_type == "audit_chat_restore":
            if segment.get("started_by") in (None, "first_event", "post_reset"):
                segment["started_by"] = "audit_chat_restore"

        # Fallback for old events that may not have snapshots yet.
        if not snapshot:
            if event_type in {"chat", "report_chat", "ask"}:
                user_text = stringify_content(payload.get("message") or payload.get("question"))
                assistant_text = stringify_content(payload.get("reply") or payload.get("answer"))
                fallback_raw: list[dict[str, str]] = []
                if user_text:
                    fallback_raw.append({"role": "user", "content": user_text})
                if assistant_text:
                    fallback_raw.append({"role": "assistant", "content": assistant_text})
                if fallback_raw:
                    combined = segment["raw_messages"] + fallback_raw
                    segment["raw_messages"] = combined
                    segment["llm_messages"] = to_llm_messages(combined)

    conversations: list[dict] = []
    for scope, thread in by_scope.items():
        segments = thread["segments"]
        filtered_segments = [
            seg
            for seg in segments
            if seg.get("raw_messages") or seg.get("events")
        ]
        for idx, seg in enumerate(filtered_segments):
            seg["segment_index"] = idx

        latest_messages: list[dict[str, str]] = []
        if filtered_segments:
            latest_messages = filtered_segments[-1].get("llm_messages", [])

        conversations.append(
            {
                "scope": scope,
                "segments": filtered_segments,
                "latest_llm_messages": latest_messages,
            }
        )

    conversations.sort(key=lambda c: c.get("scope", ""))
    return conversations


def main() -> None:
    args = parse_args()

    bucket = args.bucket
    project = args.project
    session_id = args.session

    if args.s3_uri:
        uri_bucket, uri_project, uri_session = parse_session_from_s3_uri(args.s3_uri)
        if uri_bucket:
            bucket = uri_bucket
        if uri_project:
            project = uri_project
        if uri_session:
            session_id = uri_session

    if not project or not session_id:
        raise SystemExit(
            "Provide --project and --session, or pass --s3-uri containing /session_logs/<project>/session_<session>/."
        )

    s3_prefix = f"{args.prefix.rstrip('/')}/{project}/session_{session_id}/"
    s3_client = boto3.client("s3")
    events = load_events(s3_client, bucket, s3_prefix)

    if not events:
        raise SystemExit(f"No telemetry events found at s3://{bucket}/{s3_prefix}")

    scope_filters = set(args.scope or [])
    conversations = reconstruct_conversations(events, scope_filters=scope_filters or None)

    output = {
        "identifier": {
            "project_key_hash": project,
            "session_id": session_id,
        },
        "source": {
            "bucket": bucket,
            "prefix": s3_prefix,
            "events_loaded": len(events),
        },
        "conversations": conversations,
    }

    if args.latest_only:
        output["conversations"] = [
            {
                "scope": convo["scope"],
                "latest_llm_messages": convo.get("latest_llm_messages", []),
            }
            for convo in conversations
        ]

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(output, handle, indent=2, ensure_ascii=False)
        print(f"Wrote reconstructed conversations to {output_path}")
        return

    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
