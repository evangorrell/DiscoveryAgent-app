#!/usr/bin/env python3
"""Fetch per-session telemetry events from S3."""
import argparse
import json
import re
from pathlib import Path
import boto3

DEFAULT_BUCKET = "agentic-science"
DEFAULT_PREFIX = "telemetry/session_logs"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Pull telemetry events for a session from S3."
    )
    parser.add_argument("--project", help="Project key hash used in S3 path")
    parser.add_argument("--session", help="Session ID used in S3 path")
    parser.add_argument(
        "--s3-uri",
        help="Any telemetry S3 URI; project/session are derived from it",
    )
    parser.add_argument(
        "--bucket",
        default=DEFAULT_BUCKET,
        help=f"S3 bucket name (default: {DEFAULT_BUCKET})",
    )
    parser.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help=f"Telemetry prefix (default: {DEFAULT_PREFIX})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Number of sample events to print (default: 5)",
    )
    parser.add_argument(
        "--output",
        help="Write full event list to this JSON file",
    )
    return parser.parse_args()


def parse_project_session_from_uri(s3_uri: str):
    # Example: s3://bucket/telemetry/session_logs/<project>/session_<session_id>/...
    match = re.search(r"/session_logs/([^/]+)/session_([^/]+)/", s3_uri)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def list_event_keys(s3, bucket, prefix):
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".json"):
                yield key


def load_events(s3, bucket, prefix):
    events = []
    for key in list_event_keys(s3, bucket, prefix):
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        events.append(json.loads(body))
    events.sort(key=lambda e: e.get("timestamp", ""))
    return events


def write_output(path_str, events):
    if not path_str:
        return
    path = Path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(events, handle, indent=2, ensure_ascii=True)
    print(f"Wrote {len(events)} events to {path}")


def main():
    args = parse_args()

    project = args.project
    session = args.session

    if args.s3_uri:
        uri_project, uri_session = parse_project_session_from_uri(args.s3_uri)
        if uri_project:
            project = uri_project
        if uri_session:
            session = uri_session

    if not project or not session:
        raise SystemExit(
            "Provide --project and --session, or --s3-uri that includes them."
        )

    s3 = boto3.client("s3")
    s3_prefix = f"{args.prefix}/{project}/session_{session}/"

    events = load_events(s3, args.bucket, s3_prefix)
    print(f"Loaded {len(events)} events from s3://{args.bucket}/{s3_prefix}")

    write_output(args.output, events)

    for event in events[: args.limit]:
        print(event.get("timestamp"), event.get("event_type"), event.get("hypothesis_index"))


if __name__ == "__main__":
    main()
