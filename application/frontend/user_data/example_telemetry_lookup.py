#!/usr/bin/env python3
"""Lookup recent telemetry S3 URIs for a given session_id."""
import os
from pathlib import Path

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


def main():
    if load_dotenv:
        dotenv_path = Path(__file__).resolve().parents[1] / ".env"
        load_dotenv(dotenv_path=dotenv_path, override=False)

    session_id = os.environ.get("TARGET_SESSION_ID")
    if not session_id:
        raise SystemExit("Set TARGET_SESSION_ID in env (e.g., export TARGET_SESSION_ID=...)")

    db_url = normalize_database_url(
        os.environ.get("DISCOVERY_AGENT_DATABASE_URL") or os.environ.get("DATABASE_URL")
    )
    if not db_url:
        raise SystemExit("Set DISCOVERY_AGENT_DATABASE_URL or DATABASE_URL")

    engine = create_engine(db_url, pool_pre_ping=True)

    query = text(
        """
        SELECT
            created_at,
            event_type,
            payload->>'s3_uri' AS s3_uri,
            payload->>'event_type' AS telemetry_event_type,
            payload->>'hypothesis_index' AS hypothesis_index
        FROM event_logs
        WHERE session_id = :session_id
          AND event_type = 'telemetry_event'
        ORDER BY created_at DESC
        LIMIT 20;
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(query, {"session_id": session_id}).fetchall()

    if not rows:
        print(f"No telemetry_event rows found for session_id={session_id}")
        return

    for row in rows:
        print(
            f"{row.created_at} | {row.telemetry_event_type} | "
            f"hypothesis_index={row.hypothesis_index} | {row.s3_uri}"
        )


if __name__ == "__main__":
    main()
