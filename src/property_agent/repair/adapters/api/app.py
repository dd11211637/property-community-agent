"""
Standalone repair FastAPI application.

Used by tests and local single-module runs. The unified production entry point
is ``property_agent.main``; both share the same error envelope and handlers so
responses are byte-compatible.
"""

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from property_agent.platform.adapters.api.envelope import (
    error_envelope,
    register_common_error_handlers,
)
from property_agent.repair.adapters.api.router import router
from property_agent.repair.application.service import WorkOrderService
from property_agent.repair.domain.errors import BusinessError

MAX_REQUEST_ID_LENGTH = 64


def _request_id(header_value: str | None) -> str:
    if header_value is not None:
        candidate = header_value.strip()
        if 1 <= len(candidate) <= MAX_REQUEST_ID_LENGTH:
            return candidate
    return f"req_{uuid4().hex}"


def create_app(service: WorkOrderService | None = None) -> FastAPI:
    app = FastAPI(title="Property Community Repair API", version="0.1.0")
    if service is not None:
        app.state.work_order_service = service

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = _request_id(request.headers.get("X-Request-ID"))
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(BusinessError)
    async def business_error_handler(request: Request, exc: BusinessError) -> JSONResponse:
        return error_envelope(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    register_common_error_handlers(app)

    app.include_router(router)
    return app
