"""
Shared HTTP foundation: request tracing plus the unified error envelope.

PRD 5.1 / 12.1: every application surface — the unified app and each standalone
module app — must emit the same envelope and the same ``X-Request-ID`` header.
:func:`install_http_foundation` is the one-call installer that guarantees this.
"""
from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from property_agent.platform.adapters.api.envelope import (
    error_envelope,
    register_common_error_handlers,
)
from property_agent.platform.errors import BusinessError

MAX_REQUEST_ID_LENGTH = 64

__all__ = [
    "MAX_REQUEST_ID_LENGTH",
    "install_business_error_handler",
    "install_http_foundation",
    "install_request_id_middleware",
    "request_id",
]


def request_id(header_value: str | None) -> str:
    """Normalise an inbound ``X-Request-ID`` or mint a new trace id."""
    if header_value is not None:
        candidate = header_value.strip()
        if 1 <= len(candidate) <= MAX_REQUEST_ID_LENGTH:
            return candidate
    return f"req_{uuid4().hex}"


def install_request_id_middleware(app: FastAPI) -> None:
    """Attach a trace id to ``request.state`` and echo it back on the response."""

    @app.middleware("http")
    async def _request_id_middleware(request: Request, call_next):
        request.state.request_id = request_id(request.headers.get("X-Request-ID"))
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response


def install_business_error_handler(app: FastAPI) -> None:
    """Render :class:`BusinessError` through the shared error envelope."""

    @app.exception_handler(BusinessError)
    async def _business_error_handler(request: Request, exc: BusinessError) -> JSONResponse:
        return error_envelope(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )


def install_http_foundation(app: FastAPI) -> None:
    """Install shared request tracing and error-envelope behaviour once per app."""
    install_request_id_middleware(app)
    install_business_error_handler(app)
    register_common_error_handlers(app)
