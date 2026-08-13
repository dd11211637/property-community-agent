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

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from property_agent.agent.application.conversation_service import ConversationService
from property_agent.agent.application.recovery import AgentRecoveryService
from property_agent.agent.application.runner import AgentSessionRunner
from property_agent.agent.graph import build_agent_graph
from property_agent.agent.infrastructure.checkpointer import SqlAlchemyCheckpointer
from property_agent.agent.model_gateway import (
    DeepSeekModelGateway,
    DeterministicModelGateway,
    FallbackModelGateway,
    ModelGateway,
)
from property_agent.agent.read_planner import GatewayReadPlanner
from property_agent.agent.read_tools import build_read_tools, read_tool_specs
from property_agent.agent.state import GraphState
from property_agent.agent.tools import (
    build_announcement_tools,
    build_billing_tools,
    build_inspection_tools,
    build_repair_tools,
)
from property_agent.agent.tools.repair import normalize_repair_category
from property_agent.announcement.application.scheduler import AnnouncementScheduler
from property_agent.announcement.application.service import AnnouncementService
from property_agent.announcement.domain.enums import AnnouncementAction
from property_agent.announcement.infrastructure.shared_ports import build_announcement_ports
from property_agent.announcement.infrastructure.uow import SqlAlchemyAnnouncementUnitOfWork
from property_agent.billing.application.service import BillingService, ConsultationService
from property_agent.billing.infrastructure.unit_of_work import SqlAlchemyBillingUnitOfWork
from property_agent.config import settings
from property_agent.inspection.adapters.api.dependencies import to_inspection_context
from property_agent.inspection.application.service import (
    InspectionTaskService,
    SecurityEventService,
)
from property_agent.inspection.infrastructure.shared_ports import build_inspection_ports
from property_agent.inspection.infrastructure.uow import SqlAlchemyInspectionUnitOfWork
from property_agent.platform.application.confirmation_service import ConfirmationService
from property_agent.platform.context import RequestContext
from property_agent.platform.infrastructure.database import (
    dispose_engine,
    get_session_factory,
)
from property_agent.platform.infrastructure.orm_models import (
    CommunityModel,
    HouseModel,
    MessageRecordModel,
)
from property_agent.platform.infrastructure.outbox_dispatcher import OutboxDispatcher
from property_agent.repair.application.service import WorkOrderService
from property_agent.repair.domain.enums import Urgency
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
    settings.validate_runtime_security()
    logger.info("Container starting (env=%s)...", settings.env)

    # Initialize database connection pool (side-effect: creates engine + session factory)
    get_async_engine()
    get_async_session_factory()
    logger.info("Async database engine created")

    # Initialize application services and bind them to app.state
    build_production_container(app)
    dispatcher: OutboxDispatcher = app.state.outbox_dispatcher
    dispatcher_task = asyncio.create_task(dispatcher.run(), name="message-outbox-dispatcher")
    announcement_scheduler = AnnouncementScheduler(app.state.announcement_service)
    announcement_scheduler_task = asyncio.create_task(
        announcement_scheduler.run(), name="announcement-scheduler"
    )

    try:
        yield  # ── Application runs here ──
    finally:
        await announcement_scheduler.stop()
        await announcement_scheduler_task
        await dispatcher.stop()
        await dispatcher_task

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
        app.state.agent_runner         → production agent session runner (PRD 6.5)
    """
    global _services_configured

    container = ContainerState()
    container.services = _build_services(app)
    dispatcher = build_outbox_dispatcher()
    app.state.outbox_dispatcher = dispatcher
    container.services["outbox_dispatcher"] = dispatcher
    app.state.container = container
    _services_configured = True

    logger.info("build_production_container: services=%s", list(container.services.keys()))


async def _deliver_in_app_message(message: MessageRecordModel) -> bool:
    """Production in-app transport.

    Enqueueing already persists the complete inbox item atomically with its business event.
    This transport acknowledges that durable record as delivered; external SMS/voice is
    deliberately outside the product scope.
    """
    return bool(message.receiver_id and message.id)


def build_outbox_dispatcher() -> OutboxDispatcher:
    return OutboxDispatcher(
        session_factory=get_session_factory(),
        send_message=_deliver_in_app_message,
    )


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

    The billing read path is isolated behind ``BillingSourcePort`` (the local
    SQLAlchemy source is the default adapter). Bill
    Queries are scoped by community + current house and audited via the
    platform ``AuditService``. Billing tables, audit and idempotency rows share
    the request transaction on the unified application database.
    """
    return BillingService(SqlAlchemyBillingUnitOfWork)


def build_consultation_service() -> ConsultationService:
    """Assemble the production financial-consultation service (PRD 6.3).

    Persists the consultation lifecycle, idempotency record and audit event in
    the same request transaction. The service is stateless and stores no session.
    """
    return ConsultationService(SqlAlchemyBillingUnitOfWork)


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

    return InspectionTaskService(unit_of_work_factory), SecurityEventService(unit_of_work_factory)


def build_agent_runner(app: FastAPI) -> AgentSessionRunner:
    """Assemble the production agent session runner (PRD §6.5).

    Wires the compiled agent graph with the real business services already on
    ``app.state`` (repair / announcement / billing / inspection), a persistent
    ``SqlAlchemyCheckpointer`` so pending-confirmation flows survive restarts,
    and a DeepSeek gateway when configured. Provider failures retry once and then
    degrade to deterministic keyword routing; without an API key the fallback is
    wired directly, so structured interfaces remain available (PRD R-02).

    Identity in tools comes from the trusted ``RequestContext`` activated by the
    platform auth layer (``RequestContext.current()``); a GraphState fallback is
    used only outside a request scope (background scans / tests).
    """
    session_factory = get_session_factory()
    checkpointer = SqlAlchemyCheckpointer(session_factory)
    gateway = build_model_gateway()

    def context_provider(state: GraphState) -> RequestContext:
        return resolve_agent_request_context(state)

    def inspection_context_provider(state: GraphState):
        context = resolve_agent_request_context(state)
        return to_inspection_context(context, context.request_id)

    def session_provider(state: GraphState) -> Any:
        return session_factory()

    def context_loader(state: GraphState) -> GraphState:
        """Load display-only, server-trusted context for dialogue understanding."""
        trusted: dict[str, Any] = {
            "business_date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        }
        with session_factory() as session:
            community = session.get(CommunityModel, state.community_id)
            if community is not None:
                trusted["community_name"] = community.name
            if state.current_house_id is not None:
                house = session.get(HouseModel, state.current_house_id)
                if house is not None and house.community_id == state.community_id:
                    building = _display_part(house.building, "栋")
                    unit = _display_part(house.unit, "单元")
                    room = _display_part(house.room_no, "室")
                    trusted.update(
                        {
                            "building": house.building,
                            "unit": house.unit,
                            "room_no": house.room_no,
                            "house_display": f"{building} {unit} {room}",
                        }
                    )
        state.trusted_context = trusted
        return state

    repair_tools = build_repair_tools(app.state.work_order_service, context_provider)
    announcement_tools = build_announcement_tools(
        app.state.announcement_service, context_provider, gateway
    )
    billing_tools = build_billing_tools(
        app.state.billing_service,
        app.state.consultation_service,
        context_provider,
        session_provider,
    )
    inspection_tools = build_inspection_tools(
        app.state.task_service, app.state.event_service, inspection_context_provider
    )
    controlled_read_tools = build_read_tools(
        announcement_tools=announcement_tools,
        billing_tools=billing_tools,
        repair_tools=repair_tools,
        inspection_tools=inspection_tools,
    )

    graph = build_agent_graph(
        gateway=gateway,
        repair_tools=repair_tools,
        announcement_tools=announcement_tools,
        billing_tools=billing_tools,
        inspection_tools=inspection_tools,
        checkpointer=checkpointer,
        context_loader=context_loader,
        read_planner=GatewayReadPlanner(gateway),
        read_tool_specs=read_tool_specs(),
        read_tools=controlled_read_tools,
    )
    conversations = ConversationService(session_factory)
    recovery = AgentRecoveryService(conversations=conversations, checkpointer=checkpointer)

    def confirmation_token_provider(state: GraphState) -> str:
        pending = state.pending_action or {}
        tool = str(pending.get("tool") or "")
        if tool in {"announce_publish", "announcement_schedule_publish"}:
            context = resolve_agent_request_context(state)
            announcement = app.state.announcement_service.get(
                UUID(str(state.slots["announcement_id"])), context
            )
            reviewed_version = int(state.slots["expected_version"])
            if reviewed_version != announcement.version:
                raise RuntimeError("公告内容已发生变化，请重新查看后再确认发布。")
            if tool == "announce_publish":
                parameters = {
                    "announcement_id": announcement.id,
                    "expected_version": announcement.version,
                    "action": AnnouncementAction.PUBLISH,
                }
                action = "ANNOUNCEMENT_PUBLISH"
            else:
                scheduled_at = datetime.fromisoformat(str(state.slots["scheduled_at"]))
                parameters = {
                    "announcement_id": announcement.id,
                    "expected_version": announcement.version,
                    "scheduled_at": scheduled_at,
                }
                action = "ANNOUNCEMENT_SCHEDULE"
        elif tool == "inspection_submit_records":
            parameters = {
                "note": str(state.slots.get("note") or ""),
                "record_type": str(state.slots.get("record_type") or "COMPLETION"),
                "point": str(state.slots.get("point") or ""),
            }
            action = "INSPECTION_TASK_SUBMIT_RECORDS"
        elif tool == "security_event_create":
            parameters = {
                "event_type": str(state.slots.get("event_type") or "OTHER"),
                "risk_level": str(state.slots.get("risk_level") or "MEDIUM"),
                "location": str(state.slots.get("location") or ""),
            }
            action = "SECURITY_EVENT_CREATE"
        elif tool != "repair_create":
            # 其余受控写工具也必须拿到服务端签发、绑定当前待确认参数的令牌；
            # 禁止再用可伪造的固定字符串充当确认凭据。
            parameters = dict(pending.get("params") or {})
            action = f"AGENT_{tool.upper()}"
        else:
            urgency_value = str(state.slots.get("urgency") or "NORMAL").upper()
            category = normalize_repair_category(state.slots.get("category"))
            try:
                urgency = Urgency(urgency_value)
            except ValueError:
                urgency = Urgency.NORMAL
            parameters = {
                "house_id": state.current_house_id,
                "category": category,
                "location": str(state.slots.get("location") or ""),
                "description": str(state.slots.get("description") or ""),
                "urgency": urgency,
                "attachment_ids": (),
            }
            action = "CREATE_WORK_ORDER"
        with session_factory() as session:
            confirmation_service = ConfirmationService(session)
            token = confirmation_service.generate_token(
                actor_id=state.actor_id,
                action=action,
                params=parameters,
            )
            if action.startswith("AGENT_"):
                confirmation_service.validate_and_consume_token(
                    token=token,
                    actor_id=state.actor_id,
                    action=action,
                    params=parameters,
                )
            session.commit()
            return token

    return AgentSessionRunner(
        graph=graph,
        conversations=conversations,
        recovery=recovery,
        confirmation_token_provider=confirmation_token_provider,
    )


def _display_part(value: Any, suffix: str) -> str:
    text = str(value or "").strip()
    return text if text.endswith(suffix) else f"{text}{suffix}"


def build_model_gateway() -> ModelGateway:
    """Build the production model adapter without exposing the API key downstream."""
    fallback = DeterministicModelGateway()
    if not settings.deepseek_api_key.strip():
        return fallback
    primary = DeepSeekModelGateway(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        connect_timeout_seconds=settings.deepseek_connect_timeout_seconds,
        read_timeout_seconds=settings.deepseek_read_timeout_seconds,
        total_timeout_seconds=settings.deepseek_total_timeout_seconds,
    )
    return FallbackModelGateway(primary, fallback)


def resolve_agent_request_context(state: GraphState) -> RequestContext:
    """Bind the API-validated house to trusted identity for downstream tools.

    JWT claims intentionally carry the set of bound houses but not a mutable current
    house. The agent route validates ``state.current_house_id`` against that set before
    graph execution; this composition-root guard repeats the membership check before
    handing context to any application service.
    """
    current = RequestContext.current()
    house = state.current_house_id
    if current is not None:
        if house is not None and house not in current.bound_house_ids:
            raise ValueError("Agent current house is not bound to the authenticated user")
        if house is not None and current.current_house_id != house:
            return replace(current, current_house_id=house)
        return current
    return RequestContext(
        actor_id=state.actor_id,
        community_id=state.community_id,
        roles=frozenset({"RESIDENT"}),
        request_id=f"agent-{state.conversation_id}"[:64],
        current_house_id=house,
        bound_house_ids=frozenset({house}) if house else frozenset(),
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

    # 统一智能体运行时（PRD §6.5）：依赖上面全部业务 service，装配后对话接口
    # 不再返回 503；模型用确定性关键词路由，无 LLM Key 也可跑通（R-02）。
    agent_runner = build_agent_runner(app)
    app.state.agent_runner = agent_runner
    services["agent_runner"] = agent_runner

    return services
