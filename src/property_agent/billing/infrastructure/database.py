"""Billing database sessions backed by the unified application database."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session, sessionmaker

from property_agent.platform.infrastructure.database import get_engine

engine = get_engine()

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    bind=engine,
)


def get_db() -> Generator[Session, None, None]:
    """Yield a billing session from the unified database pool."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_session() -> Session:
    """Return an unmanaged billing session for non-HTTP application code."""
    return SessionLocal()
