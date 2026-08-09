"""
Platform infrastructure database — shared engine and session factory.

Provides a single database connection pool used by all domain modules.
"""

from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from property_agent.platform.infrastructure.orm_models import Base

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost/property_agent",
)

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Return the singleton SQLAlchemy Engine, creating it if needed."""
    global _engine
    if _engine is None:
        connect_args: dict = {}
        if "sqlite" in DATABASE_URL:
            connect_args["check_same_thread"] = False
        _engine = create_engine(
            DATABASE_URL,
            echo=False,
            connect_args=connect_args,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the singleton Session factory."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(),
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yield a database session with automatic cleanup."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    """Create all tables from the shared Base metadata."""
    Base.metadata.create_all(bind=get_engine())


def dispose_engine() -> None:
    """Dispose the engine (for testing / shutdown)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
        _engine = None
    _SessionLocal = None
