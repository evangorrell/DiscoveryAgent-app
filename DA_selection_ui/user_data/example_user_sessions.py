#!/usr/bin/env python3
"""List sessions for a given username from the event_logs table."""
import argparse
import os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

from sqlalchemy import create_engine, text


def normalize_database_url(raw: str | None) -> str | None:
    if not raw:
        return None
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+psycopg2://", 1)
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+psycopg2://", 1)
    return raw


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List sessions for a given username from the event_logs table."
    )
    parser.add_argument("username", help="Target username to query")
    return parser.parse_args()


PT_ZONE = ZoneInfo("America/Los_Angeles")
UTC_ZONE = ZoneInfo("UTC")


def to_pt_string(value: datetime | None) -> str:
    if not isinstance(value, datetime):
        return "n/a"
    dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC_ZONE)
    return dt.astimezone(PT_ZONE).isoformat()


def main():
    if load_dotenv:
        dotenv_path = Path(__file__).resolve().parents[1] / ".env"
        load_dotenv(dotenv_path=dotenv_path, override=False)

    args = parse_args()
    username = args.username

    db_url = normalize_database_url(
        os.environ.get("DISCOVERY_AGENT_DATABASE_URL") or os.environ.get("DATABASE_URL")
    )
    if not db_url:
        raise SystemExit("Set DISCOVERY_AGENT_DATABASE_URL or DATABASE_URL")

    engine = create_engine(db_url, pool_pre_ping=True)

    query = text(
        """
        SELECT
            session_id,
            project_key_hash,
            MIN(created_at) AS first_seen,
            MAX(created_at) AS last_seen,
            COUNT(*) AS event_count,
            COALESCE(
                MAX(
                    CASE
                        WHEN event_type = 'checkpoint_s3_prefix'
                        THEN NULLIF(payload->>'checkpoint_s3_prefix', '')
                        ELSE NULL
                    END
                ),
                MAX(NULLIF(payload->>'checkpoint_s3_prefix', '')),
                MAX(NULLIF(payload->>'project_prefix', ''))
            ) AS checkpoint_s3_prefix
        FROM event_logs
        WHERE username = :username
          AND session_id IS NOT NULL
        GROUP BY session_id, project_key_hash
        ORDER BY last_seen DESC;
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(query, {"username": username}).fetchall()

    print("Converting time zone to PT")

    if not rows:
        print(f"No sessions found for {username}")
        return

    for row in rows:
        checkpoint_s3_prefix = row.checkpoint_s3_prefix or "n/a"
        print(
            f"session_id={row.session_id} | "
            f"first_seen={to_pt_string(row.first_seen)} | last_seen={to_pt_string(row.last_seen)} | "
            f"events={row.event_count} | "
            f"checkpoint_s3_prefix={checkpoint_s3_prefix}"
        )


if __name__ == "__main__":
    main()
