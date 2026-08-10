from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime

try:  # Optional for environments that rely on .env locally
    from dotenv import load_dotenv  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    load_dotenv = None

from sqlalchemy import Column, DateTime, Integer, JSON, String, Text, create_engine, func
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

if load_dotenv:
    load_dotenv()


def _normalize_database_url(raw: str | None) -> str | None:
    """
    Normalize DATABASE_URL for SQLAlchemy/psycopg2.

    Render/Postgres often supplies URLs beginning with postgres://; SQLAlchemy
    prefers postgresql+psycopg2://. We rewrite only when needed to avoid driver
    errors while keeping the original value otherwise.
    """
    if not raw:
        return None
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+psycopg2://", 1)
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+psycopg2://", 1)
    return raw


DATABASE_URL = _normalize_database_url(
    os.environ.get("DISCOVERY_AGENT_DATABASE_URL") or os.environ.get("DATABASE_URL")
)

ENGINE = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
SessionLocal = (
    scoped_session(sessionmaker(bind=ENGINE, expire_on_commit=False)) if ENGINE else None
)

Base = declarative_base()


def db_enabled() -> bool:
    return ENGINE is not None


def init_db() -> None:
    if ENGINE is None:
        return
    Base.metadata.create_all(ENGINE)


@contextmanager
def db_session_scope():
    if SessionLocal is None:
        raise RuntimeError("Database is not configured")
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class ProjectKey(Base):
    __tablename__ = "project_keys"

    hash = Column(String, primary_key=True)
    plaintext_key = Column(String, nullable=True)
    label = Column(String, nullable=True)
    project_prefix = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    length_bytes = Column(Integer, nullable=True)
    usernames = Column(JSON, nullable=False, default=list)
    config = Column(JSON, nullable=False, default=dict)
    revoked_at = Column(DateTime, nullable=True)


class ProjectProgress(Base):
    __tablename__ = "project_progress"

    project_key_hash = Column(String, primary_key=True)
    submission_count = Column(Integer, nullable=False, default=0)
    last_submitted_at = Column(DateTime, nullable=True)
    last_submitted_by = Column(String, nullable=True)
    label = Column(String, nullable=True)


class RegistrationSubmission(Base):
    __tablename__ = "registration_submissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    affiliation = Column(String, nullable=False)
    email = Column(String, nullable=False)
    username = Column(String, nullable=False)
    submitted_at = Column(DateTime, nullable=False, default=func.now())


class EventLog(Base):
    __tablename__ = "event_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_key_hash = Column(String, nullable=True)
    username = Column(String, nullable=True)
    session_id = Column(String, nullable=True)
    event_type = Column(String, nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=func.now())

