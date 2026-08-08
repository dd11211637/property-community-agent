"""
Production Composition Root — PRD 5.4.

Manages the full lifecycle of:
- Async SQLAlchemy engine and session factory
- Application services (Auth, Audit, Idempotency, Confirmation, Outbox)
- FastAPI dependency overrides for production service injection

The assembly pipeline is:
  Configuration → Database Engine / SessionFactory
    → Application Services → FastAPI dependency_overrides
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from property_agent.announcement.application.service import AnnouncementService
from property_agent.announcement.infrastructure.shared_ports import build_announcement_ports
from property_agent.announcement.infrastructure.uow import SqlAlchemyAnnouncementUnitOfWork
from property_agent.billing.application.service import BillingService, ConsultationService
from property_agent.config import settings
from property_agent.inspection.application.service import (
    InspectionTaskService,
    SecurityEventService,
)
from property_agent.inspection.infrastructure.shared_ports import build_inspection_ports
from property_agent.inspection.infrastructure.uow import SqlAlchemyInspectionUnitOfWork
from property_agent.platform.infrastructure.database import (
    dispose_engine,
    get_session_factory,
)
from property_agent.repair.application.service import WorkOrderService
from property_agent.repair.infrastructure.shared_ports import build_shared_ports
from property_agent.repair.infrastructure.uow import SqlAlchemyRepairUnitOfWork

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global state — managed by the container lifecycle
# ---------------------------------------------------------------------------

_async_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None
_services_configured: bool = False


class ContainerState:
    """Holds references to all initialized application services.

    Set on app.state.container after build_production_container() succeeds.
    """
    __slots__ = ("services",)

    def __init__(self) -> None:
        self.services: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Async database helpers
# ---------------------------------------------------------------------------

def _build_async_database_url(sync_url: str) -> str:
    """Convert a sync database URL to its async counterpart.

    postgresql+psycopg://... → postgresql+psycopg_async://...
    sqlite:///...            → sqlite+aiosqlite:///...
    """
    if sync_url.startswith("postgresql+psycopg://"):
        return sync_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    if sync_url.startswith("sqlite://"):
        return sync_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return sync_url


def get_async_engine() -> AsyncEngine:
    """Return the singleton async SQLAlchemy Engine."""
    global _async_engine
    if _async_engine is None:
        async_url = _build_async_database_url(settings.database_url)
        connect_args: dict = {}
        if "sqlite" in async_url:
            connect_args["check_same_thread"] = False
        _async_engine = create_async_engine(
            async_url,
            echo=settings.debug,
            connect_args=connect_args,
            pool_pre_ping=True,
        )
    return _async_engine


def get_async_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the singleton async session factory."""
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(
            bind=get_async_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _async_session_factory


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield an async database session."""
    factory = get_async_session_factory()
    async with factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Async database health check
# ---------------------------------------------------------------------------

async def check_database_health() -> bool:
    """Run SELECT 1 against the async engine to verify connectivity."""
    try:
        engine = get_async_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("Database health check failed")
        return False


# ---------------------------------------------------------------------------
# Service container check
# ---------------------------------------------------------------------------

def are_services_configured() -> bool:
    """Return True if the production service container has been assembled."""
    return _services_configured


# ---------------------------------------------------------------------------
# Lifespan — FastAPI async context manager
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: initialize and tear down production resources.

    Startup:
      1. Create async SQLAlchemy engine and session factory.
      2. Initialize application services (Auth, Audit, Idempotency, etc.).
      3. Bind services to app.state and dependency_overrides.

    Shutdown:
      1. Dispose the async engine.
      2. Reset global state.
    """
    global _services_configured, _async_engine, _async_session_factory

    # ── Startup ──────────────────────────────────────────────────
    logger.info("Container starting (env=%s)...", settings.env)

    # Initialize database connection pool (side-effect: creates engine + session factory)
    get_async_engine()
    get_async_session_factory()
    logger.info("Async database engine created")

    # Initialize application services and bind them to app.state
    build_production_container(app)

    yield  # ── Application runs here ──

    # ── Shutdown ─────────────────────────────────────────────────
    logger.info("Container shutting down...")
    _services_configured = False

    if _async_engine is not None:
        await _async_engine.dispose()
    _async_engine = None
    _async_session_factory = None
    dispose_engine()

    logger.info("Container shutdown complete")


# ---------------------------------------------------------------------------
# build_production_container — explicit assembly for testing
# ---------------------------------------------------------------------------

def build_production_container(app: FastAPI) -> None:
    """Explicitly build and inject the production service container.

    Call this during testing or when you need to assemble services
    outside of the lifespan context manager.

    Sets:
        app.state.container            → ContainerState with initialized services
        app.state.work_order_service   → production repair service (PRD 6.1)
        app.state.announcement_service → production announcement service (PRD 6.2)
        app.state.billing_service      → production billing service (PRD 6.3)
        app.state.consultation_service → production financial consultation service (PRD 6.3)
        app.state.task_service         → production inspection task service (PRD 6.4)
        app.state.event_service        → production security event service (PRD 6.4)
    """
    global _services_configured

    container = ContainerState()
    container.services = _build_services(app)
    app.state.container = container
    _services_configured = True

    logger.info("build_production_container: services=%s", list(container.services.keys()))


# ---------------------------------------------------------------------------
# Internal: service factory
# ---------------------------------------------------------------------------

def build_work_order_service() -> WorkOrderService:
    """Assemble the production repair service.

    Wires the sync SQLAlchemy session factory into the repair Unit of Work and
    the eight production shared ports (idempotency, confirmation, house access,
    staff directory, attachment, audit, message outbox, handover). Each request
    gets a fresh session; the repository and every port share it, so a single
    ``commit()`` persists the work order, timeline, audit trail and outbox
    message atomically.
    """
    session_factory = get_session_factory()

    def unit_of_work_factory() -> SqlAlchemyRepairUnitOfWork:
        return SqlAlchemyRepairUnitOfWork(session_factory, build_shared_ports)

    return WorkOrderService(unit_of_work_factory)


def build_announcement_service() -> AnnouncementService:
    """Assemble the production announcement service (PRD 6.2).

    Wires the sync SQLAlchemy session factory into the announcement Unit of
    Work and the five production shared ports (idempotency, confirmation,
    audience resolution, audit, message outbox). The repository and every port
    share one session per request, so publishing an announcement writes the
    state transition, the frozen audience snapshot, the audit row and all
    station messages in a single transaction.
    """
    session_factory = get_session_factory()

    def unit_of_work_factory() -> SqlAlchemyAnnouncementUnitOfWork:
        return SqlAlchemyAnnouncementUnitOfWork(session_factory, build_announcement_ports)

    return AnnouncementService(unit_of_work_factory)


def build_billing_service() -> BillingService:
    """Assemble the production billing service (PRD 6.3).

    The billing read path is isolated behind ``BillingSourcePort`` (local demo
    source by default; a remote/unavailable variant exists for R-02). Bill
    queries are scoped by community + current house and audited via the
    platform ``AuditService``. The billing DB keeps its own engine (轻量接入);
    the platform DB carries audit / idempotency rows.
    """
    return BillingService()


def build_consultation_service() -> ConsultationService:
    """Assemble the production financial-consultation service (PRD 6.3).

    Persists the consultation ticket lifecycle in the billing DB and every
    transition/audit row in the platform DB via the shared ports. Stateless —
    holds no session; sessions are opened per call.
    """
    return ConsultationService()


def build_inspection_services() -> tuple[InspectionTaskService, SecurityEventService]:
    """Assemble the production inspection task + security event services (PRD 6.4).

    Both share one Unit-of-Work factory backed by the platform SQLAlchemy
    session factory and the seven production shared ports (idempotency,
    confirmation, staff directory, attachment, audit, message outbox,
    escalation). Each request gets a fresh session; the repository and every
    port share it, so a single ``commit()`` persists the task / event, its
    timeline, the audit trail, the outbox message and any escalation ticket
    atomically.
    """
    session_factory = get_session_factory()

    def unit_of_work_factory() -> SqlAlchemyInspectionUnitOfWork:
        return SqlAlchemyInspectionUnitOfWork(session_factory, build_inspection_ports)

    return InspectionTaskService(unit_of_work_factory), SecurityEventService(
        unit_of_work_factory
    )


def _build_services(app: FastAPI) -> dict[str, Any]:
    """Create and return all application service instances.

    Services are created with their production dependencies (real database
    sessions, real JWT secret, etc.). No fake/mock backends are used.
    Fakes live in ``tests/``; local demo adapters live in ``testing/``.
    """
    services: dict[str, Any] = {}

    # Session-scoped platform services are constructed per request from the
    # `get_db` dependency, so here we only record that they are available.
    services["auth_service"] = "configured"
    services["audit_service"] = "configured"
    services["idempotency_service"] = "configured"
    services["confirmation_service"] = "configured"
    services["message_outbox_service"] = "configured"

    # Business services are long-lived: they hold a Unit-of-Work factory
    # rather than a session, so a single instance is safe to share.
    work_order_service = build_work_order_service()
    app.state.work_order_service = work_order_service
    services["work_order_service"] = work_order_service

    announcement_service = build_announcement_service()
    app.state.announcement_service = announcement_service
    services["announcement_service"] = announcement_service

    billing_service = build_billing_service()
    app.state.billing_service = billing_service
    services["billing_service"] = billing_service

    consultation_service = build_consultation_service()
    app.state.consultation_service = consultation_service
    services["consultation_service"] = consultation_service

    task_service, event_service = build_inspection_services()
    app.state.task_service = task_service
    app.state.event_service = event_service
    services["task_service"] = task_service
    services["event_service"] = event_service

    return services