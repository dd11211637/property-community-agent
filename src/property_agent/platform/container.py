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
from typing import Any

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from property_agent.agent.application.composition import bind_runtime, close_runtime_resources
from property_agent.agent.application.confirmation_provider import prepare_confirmation
from property_agent.agent.application.conversation_service import ConversationService
from property_agent.agent.application.memory_runtime import (
    _display_part as _display_part,
)
from property_agent.agent.application.memory_runtime import (
    build_agent_context_loader,
    build_turn_recorder,
)
from property_agent.agent.application.recovery import AgentRecoveryService
from property_agent.agent.application.runner import AgentSessionRunner
from property_agent.agent.capabilities.bootstrap import build_capability_executor
from property_agent.agent.graph import build_agent_graph
from property_agent.agent.infrastructure.checkpointer import SqlAlchemyCheckpointer
from property_agent.agent.infrastructure.run_lease import RunLeaseService
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
from property_agent.announcement.application.scheduler import AnnouncementScheduler
from property_agent.announcement.application.service import AnnouncementService
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
from property_agent.platform.application.approval_service import ApprovalService
from property_agent.platform.context import RequestContext
from property_agent.platform.infrastructure.database import (
    dispose_engine,
    get_session_factory,
)
from property_agent.platform.infrastructure.orm_models import MessageRecordModel
from property_agent.platform.infrastructure.outbox_dispatcher import OutboxDispatcher
from property_agent.repair.application.auto_assignment import build_agent_work_order_service
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
        close_runtime_resources(app)

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


def build_work_order_service(approval_service: ApprovalService) -> WorkOrderService:
    """Assemble the production repair service.

    Wires the sync SQLAlchemy session factory into the repair Unit of Work and
    the eight production shared ports (idempotency, confirmation, house access,
    staff directory, attachment, audit, message outbox, handover). Each request
    gets a fresh session; the repository and every port share it, so a single
    ``commit()`` persists the work order, timeline, audit trail and outbox
    message atomically. ``approval_service`` 注入端口用于 P0 审批原子消费。
    """
    session_factory = get_session_factory()

    def unit_of_work_factory() -> SqlAlchemyRepairUnitOfWork:
        # P0-1: 显式闭包绑定 approval_service，避免运行时
        # ``build_shared_ports(session)`` 缺参 TypeError（审查报告 composition-root
        # 错误）。测试 fixture 已手工绑定，生产路径必须同样绑定。
        return SqlAlchemyRepairUnitOfWork(
            session_factory,
            lambda session: build_shared_ports(session, approval_service),
        )

    return WorkOrderService(unit_of_work_factory)


def build_announcement_service(approval_service: ApprovalService) -> AnnouncementService:
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
        return SqlAlchemyAnnouncementUnitOfWork(
            session_factory,
            lambda session: build_announcement_ports(
                session, approval_service, enforce_fence=settings.agent_concurrency_guard
            ),
        )

    return AnnouncementService(unit_of_work_factory)


def build_billing_service(
    approval_service: ApprovalService, *, enforce_fence: bool = False
) -> BillingService:
    """Assemble the production billing service (PRD 6.3).

    The billing read path is isolated behind ``BillingSourcePort`` (the local
    SQLAlchemy source is the default adapter). Bill Queries are scoped by
    community + current house and audited via the platform ``AuditService``.
    Billing tables, audit and idempotency rows share the request transaction on
    the unified application database.
    """

    def uow_factory(transaction: Any) -> SqlAlchemyBillingUnitOfWork:
        return SqlAlchemyBillingUnitOfWork(
            transaction, approval_service, enforce_fence=enforce_fence
        )

    return BillingService(uow_factory)


def build_consultation_service(
    approval_service: ApprovalService, *, enforce_fence: bool = False
) -> ConsultationService:
    """Assemble the production financial-consultation service (PRD 6.3).

    Persists the consultation lifecycle, idempotency record and audit event in
    the same request transaction. The service is stateless and stores no session.
    """

    def uow_factory(transaction: Any) -> SqlAlchemyBillingUnitOfWork:
        return SqlAlchemyBillingUnitOfWork(
            transaction, approval_service, enforce_fence=enforce_fence
        )

    return ConsultationService(uow_factory)


def build_inspection_services(
    approval_service: ApprovalService,
) -> tuple[InspectionTaskService, SecurityEventService]:
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
        return SqlAlchemyInspectionUnitOfWork(
            session_factory,
            lambda session: build_inspection_ports(
                session, approval_service, enforce_fence=settings.agent_concurrency_guard
            ),
        )

    return InspectionTaskService(unit_of_work_factory), SecurityEventService(unit_of_work_factory)


def build_agent_runner(
    app: FastAPI,
    *,
    approval_service: ApprovalService,
    run_lease_service: RunLeaseService,
) -> AgentSessionRunner:
    """Assemble the production agent session runner (PRD §6.5).

    注入 ``AgentObservability``；SDK 缺失时降级且不影响正确性底座。

    Wires the compiled agent graph with the real business services already on
    ``app.state`` (repair / announcement / billing / inspection), a persistent
    ``SqlAlchemyCheckpointer`` so pending-confirmation flows survive restarts,
    and a DeepSeek gateway when configured. Provider failures retry once and then
    degrade to deterministic keyword routing; without an API key the fallback is
    wired directly, so structured interfaces remain available (PRD R-02).

    Identity in tools comes from the trusted ``RequestContext`` activated by the
    platform auth layer (``RequestContext.current()``); a GraphState fallback is
    used only outside a request scope (background scans / tests).

    P0 正确性底座：
      * ``confirmation_token_provider`` 创建 PENDING 审批并把 ``approval_ref``
        写回 state（不再"签发即消费"），业务 UoW 在同一事务内消费审批 + 令牌。
      * Runner 在 turn 开始读取 checkpoint 版本作为 CAS 期望值，并在长 turn
        期间持有 run lease 防止同会话并发 lost-update。
    """
    from property_agent.agent.observability import AgentObservability

    observability = AgentObservability.build(settings)

    session_factory = get_session_factory()
    checkpointer = SqlAlchemyCheckpointer(session_factory)
    gateway = build_model_gateway()
    app.state.agent_model_gateway = gateway

    context_loader = build_agent_context_loader(session_factory)
    graph, *_ = _build_agent_tooling(
        app=app,
        session_factory=session_factory,
        gateway=gateway,
        context_loader=context_loader,
        checkpointer=None,
    )
    conversations = ConversationService(session_factory)
    recovery = AgentRecoveryService(conversations=conversations, checkpointer=checkpointer)

    def confirmation_token_provider(state: GraphState) -> str:
        """见 ``_make_confirmation_token_provider``；P0 正确性底座。"""
        return prepare_confirmation(
            state,
            session_factory=session_factory,
            approval_service=approval_service,
            announcement_service=app.state.announcement_service,
        )

    return AgentSessionRunner(
        graph=graph,
        conversations=conversations,
        recovery=recovery,
        confirmation_token_provider=confirmation_token_provider,
        turn_recorder=build_turn_recorder(session_factory),
        checkpointer=checkpointer,
        run_lease=run_lease_service,
        approval_service=approval_service,
        enforce_concurrency=settings.agent_concurrency_guard,
        observability=observability,
    )


def _build_agent_tooling(
    *,
    app: FastAPI,
    session_factory: Any,
    gateway: ModelGateway,
    context_loader: Any,
    checkpointer: SqlAlchemyCheckpointer | None,
) -> tuple:
    """拼装 agent graph 与四个业务工具集，返回 ``(graph, repair, ... )``。"""

    def context_provider(state: GraphState) -> RequestContext:
        return resolve_agent_request_context(state)

    def session_provider(state: GraphState) -> Any:
        return session_factory()

    agent_work_orders = build_agent_work_order_service(
        session_factory, app.state.work_order_service
    )
    capability_executor = build_capability_executor(
        work_order_service=agent_work_orders,
        billing_service=app.state.billing_service,
        consultation_service=app.state.consultation_service,
        billing_session_provider=lambda runtime: session_provider(runtime.legacy_state),
        announcement_service=app.state.announcement_service,
        announcement_model_gateway=gateway,
        inspection_task_service=app.state.task_service,
        inspection_event_service=app.state.event_service,
    )
    app.state.agent_capability_executor = capability_executor
    repair_tools = build_repair_tools(agent_work_orders, context_provider, capability_executor)
    announcement_tools = build_announcement_tools(
        app.state.announcement_service, context_provider, gateway, capability_executor
    )
    billing_tools = build_billing_tools(
        app.state.billing_service,
        app.state.consultation_service,
        context_provider,
        session_provider,
        capability_executor,
    )
    inspection_tools = build_inspection_tools(
        app.state.task_service,
        app.state.event_service,
        context_provider,
        capability_executor,
        inspection_context_projector=lambda context: to_inspection_context(
            context, context.request_id
        ),
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
    return (
        graph,
        repair_tools,
        announcement_tools,
        billing_tools,
        inspection_tools,
        controlled_read_tools,
    )  # noqa: E501


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
    if current is None:
        raise ValueError("Trusted platform request context is required for Agent execution")
    if house is not None and house not in current.bound_house_ids:
        raise ValueError("Agent current house is not bound to the authenticated user")
    if house is not None and current.current_house_id != house:
        return replace(current, current_house_id=house)
    return current


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

    # P0 正确性底座：审批服务（同一会话的 PENDING/APPROVED/CONSUMED 生命周期）
    # 与运行 lease（fencing）由容器一次性装配，业务 Service 通过端口复用。
    session_factory = get_session_factory()
    approval_service = ApprovalService(
        session_factory,
        ttl_minutes=settings.agent_approval_ttl_minutes,
    )
    services["approval_service"] = approval_service
    run_lease_service = RunLeaseService(
        session_factory,
        lease_seconds=settings.agent_run_lease_seconds,
    )
    services["run_lease_service"] = run_lease_service

    # Business services are long-lived: they hold a Unit-of-Work factory
    # rather than a session, so a single instance is safe to share.
    work_order_service = build_work_order_service(approval_service)
    app.state.work_order_service = work_order_service
    services["work_order_service"] = work_order_service

    announcement_service = build_announcement_service(approval_service)
    app.state.announcement_service = announcement_service
    services["announcement_service"] = announcement_service

    billing_service = build_billing_service(
        approval_service, enforce_fence=settings.agent_concurrency_guard
    )
    app.state.billing_service = billing_service
    services["billing_service"] = billing_service

    consultation_service = build_consultation_service(
        approval_service, enforce_fence=settings.agent_concurrency_guard
    )
    app.state.consultation_service = consultation_service
    services["consultation_service"] = consultation_service

    task_service, event_service = build_inspection_services(approval_service)
    app.state.task_service = task_service
    app.state.event_service = event_service
    services["task_service"] = task_service
    services["event_service"] = event_service

    # 统一智能体运行时（PRD §6.5）：依赖上面全部业务 service，装配后对话接口
    # 不再返回 503；模型用确定性关键词路由，无 LLM Key 也可跑通（R-02）。
    agent_runner = build_agent_runner(
        app,
        approval_service=approval_service,
        run_lease_service=run_lease_service,
    )
    bind_runtime(app, agent_runner, ConversationService(session_factory), services)

    return services
