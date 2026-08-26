"""
物业社区管理智能体 — 统一生产入口 (FastAPI)

板块一：公共基础与生产运行 (PRD 5.4)
────────────────────────────────────────────────────────
装配链路:
  Configuration → Database Engine / SessionFactory
    → Application Services → FastAPI dependency_overrides

挂载模块:
  - platform  (auth, health, shared services)
  - repair    (报修)
  - announcement (公告)
  - inspection (巡检与安防)
  - billing   (账单查询、规则解释与财务咨询)
  - agent     (统一智能体会话)
────────────────────────────────────────────────────────
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from property_agent.agent.adapters.api.certification_router import (
    router as agent_certification_router,
)
from property_agent.agent.adapters.api.memory_router import router as agent_memory_router
from property_agent.agent.adapters.api.router import router as agent_router
from property_agent.agent.application.errors import AgentSessionError
from property_agent.announcement.adapters.api.router import router as announcement_router
from property_agent.billing.adapters.api.router import router as billing_router
from property_agent.config import settings
from property_agent.inspection.adapters.api.router import (
    event_router as inspection_event_router,
)
from property_agent.inspection.adapters.api.router import (
    task_router as inspection_task_router,
)
from property_agent.inspection.domain.errors import BusinessError as InspectionBusinessError
from property_agent.platform.adapters.api.envelope import (
    error_envelope,
    register_common_error_handlers,
)
from property_agent.platform.adapters.api.health_routes import router as health_router
from property_agent.platform.adapters.api.operations_routes import router as operations_router
from property_agent.platform.adapters.api.routes import router as platform_router
from property_agent.platform.container import lifespan
from property_agent.platform.dependencies import bind_request_context_to_jwt
from property_agent.platform.errors import BusinessError as PlatformBusinessError
from property_agent.platform.http_observability import observe_http_request
from property_agent.platform.observability import configure_logging
from property_agent.repair.adapters.api.router import router as repair_router
from property_agent.repair.domain.errors import BusinessError as RepairBusinessError


def create_app() -> FastAPI:
    """Create the unified FastAPI application with all modules assembled.

    Uses the lifespan context manager (PRD 5.4) to manage:
    - Async SQLAlchemy engine and session pool
    - Application service container assembly
    - Graceful shutdown of database connections

    Production services are configured via app.state.container and
    dependency_overrides. Until real services are injected, repair/
    inspection endpoints return 503 ADAPTER_NOT_CONFIGURED.
    """
    configure_logging(settings.log_level)
    app = FastAPI(
        title="Property Community Management Agent",
        version="0.1.0",
        description="物业社区管理智能体 — 统一后端服务",
        lifespan=lifespan,
    )

    # ── Middleware ──────────────────────────────────────────────
    app.middleware("http")(observe_http_request)

    # ── Error Handlers ─────────────────────────────────────────
    @app.exception_handler(RepairBusinessError)
    async def repair_error_handler(request: Request, exc: RepairBusinessError) -> JSONResponse:
        return error_envelope(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(InspectionBusinessError)
    async def inspection_error_handler(
        request: Request, exc: InspectionBusinessError
    ) -> JSONResponse:
        return error_envelope(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(PlatformBusinessError)
    async def business_error_handler(request: Request, exc: PlatformBusinessError) -> JSONResponse:
        # Raised by the announcement module and the shared validation helpers.
        return error_envelope(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(AgentSessionError)
    async def agent_session_error_handler(request: Request, exc: AgentSessionError) -> JSONResponse:
        # 会话归属 / 生命周期 / 恢复守卫失败（PRD §6.5.8）
        return error_envelope(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
        )

    # PlatformError / HTTPException / RequestValidationError
    register_common_error_handlers(app)

    # ── Mount Routers ──────────────────────────────────────────
    # Health probes (PRD 5.4) — always mounted first
    app.include_router(health_router)

    # Platform (auth, house selection, confirmations) — always mounted
    app.include_router(platform_router)
    app.include_router(operations_router)

    # Business modules — mounted unconditionally; endpoints return 503
    # if services are not yet injected (per PRD: "未装配返回 503 ADAPTER_NOT_CONFIGURED")
    app.include_router(repair_router)
    app.include_router(announcement_router)
    app.include_router(inspection_task_router)
    app.include_router(inspection_event_router)
    app.include_router(billing_router)

    # 统一智能体不可用不影响上面的结构化业务接口（PRD §6.5.11）。
    app.include_router(agent_memory_router)
    app.include_router(agent_router)
    if settings.certification_write_enabled and settings.deployment_environment in {
        "isolated-test",
        "preproduction",
    }:
        app.include_router(agent_certification_router)

    # The announcement router depends on the auth *seam*
    # (``platform.dependencies.get_request_context``) so it can also run as a
    # standalone app. In the unified application the seam is bound to the real
    # JWT dependency — no endpoint is reachable without a valid token.
    bind_request_context_to_jwt(app)

    return app


# ── Module-level app instance for uvicorn ──────────────────────
app = create_app()
