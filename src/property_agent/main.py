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
  - inspection (巡检与安防)
  - billing   (费用查询与智能缴费)
────────────────────────────────────────────────────────
"""
from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from property_agent.billing.adapters.api.routes import router as billing_router
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
from property_agent.platform.adapters.api.routes import router as platform_router
from property_agent.platform.container import lifespan
from property_agent.repair.adapters.api.router import router as repair_router
from property_agent.repair.domain.errors import BusinessError as RepairBusinessError

MAX_REQUEST_ID_LENGTH = 64


def _request_id(header_value: str | None) -> str:
    if header_value is not None:
        candidate = header_value.strip()
        if 1 <= len(candidate) <= MAX_REQUEST_ID_LENGTH:
            return candidate
    return f"req_{uuid4().hex}"


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
    app = FastAPI(
        title="Property Community Management Agent",
        version="0.1.0",
        description="物业社区管理智能体 — 统一后端服务",
        lifespan=lifespan,
    )

    # ── Middleware ──────────────────────────────────────────────
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = _request_id(request.headers.get("X-Request-ID"))
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

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

    # PlatformError / HTTPException / RequestValidationError
    register_common_error_handlers(app)

    # ── Mount Routers ──────────────────────────────────────────
    # Health probes (PRD 5.4) — always mounted first
    app.include_router(health_router)

    # Platform (auth, house selection, confirmations) — always mounted
    app.include_router(platform_router)

    # Business modules — mounted unconditionally; endpoints return 503
    # if services are not yet injected (per PRD: "未装配返回 503 ADAPTER_NOT_CONFIGURED")
    app.include_router(repair_router)
    app.include_router(inspection_task_router)
    app.include_router(inspection_event_router)
    app.include_router(billing_router)

    return app


# ── Module-level app instance for uvicorn ──────────────────────
app = create_app()