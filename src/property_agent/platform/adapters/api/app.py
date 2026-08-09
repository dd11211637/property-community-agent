"""
Platform API app factory — creates the FastAPI application with platform routes.

Follows the same pattern as repair/adapters/api/app.py and
inspection/adapters/api/app.py.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from property_agent.platform.adapters.api.routes import router
from property_agent.platform.services.errors import PlatformError

MAX_REQUEST_ID_LENGTH = 64


def _request_id(header_value: str | None) -> str:
    if header_value is not None:
        candidate = header_value.strip()
        if 1 <= len(candidate) <= MAX_REQUEST_ID_LENGTH:
            return candidate
    return f"req_{uuid4().hex}"


def create_app() -> FastAPI:
    """Create a FastAPI app with platform routes, middleware, and error handlers."""

    app = FastAPI(title="Property Community Platform API", version="0.1.0")

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = _request_id(request.headers.get("X-Request-ID"))
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(PlatformError)
    async def platform_error_handler(request: Request, exc: PlatformError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "data": None,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": None,
                },
                "request_id": getattr(request.state, "request_id", ""),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "data": None,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "The request payload is invalid.",
                    "details": {"errors": jsonable_encoder(exc.errors())},
                },
                "request_id": getattr(request.state, "request_id", ""),
            },
        )

    app.include_router(router)
    return app
