"""
Shared persistence entry point — single ``Base`` and session factory helpers.

PRD 12.3: the modular monolith keeps **one** SQLAlchemy declarative registry so
that Alembic autogenerate, cross-module foreign keys and a single connection
pool all behave. ``Base`` is re-exported from
``platform.infrastructure.orm_models``; importing a different ``DeclarativeBase``
in a business module is a fatal "split Base" bug.

This module also offers the URL-based factories used by standalone module apps
and integration tests, on top of the process-wide singletons defined in
``platform.infrastructure.database``.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from property_agent.platform.infrastructure.database import (
    dispose_engine,
    get_db,
    get_engine,
    get_session_factory,
    init_db,
)
from property_agent.platform.infrastructure.orm_models import Base

__all__ = [
    "Base",
    "create_session_factory",
    "dispose_engine",
    "get_db",
    "get_engine",
    "get_session_factory",
    "init_db",
    "session_factory_from_engine",
]


def create_session_factory(database_url: str, *, echo: bool = False) -> sessionmaker[Session]:
    """Build a session factory bound to a dedicated engine for ``database_url``.

    Used by standalone module applications and integration tests that must not
    share the process-wide singleton engine.
    """
    connect_args: dict = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    return session_factory_from_engine(engine)


def session_factory_from_engine(engine: Engine) -> sessionmaker[Session]:
    """Build a session factory bound to an existing engine."""
    return sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
