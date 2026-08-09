"""
Unified response envelope and shared FastAPI error handlers.

PRD 5.1 / 12.1: every API response — success or failure — uses the same
envelope so that the frontend and the agent tool layer can parse results
without per-module special cases::

    {"success": false, "data": null,
     "error": {"code": "...", "message": "...", "details": {...}},
     "request_id": "req_..."}

The handlers registered by :func:`register_common_error_handlers` are shared
by the unified application (``property_agent.main``) and the standalone
repair application, so both surfaces behave identically.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from property_agent.platform.domain.exceptions import PlatformError

# HTTP status → stable business error code. Platform dependencies raise plain
# ``HTTPException`` for auth/RBAC failures; mapping keeps the envelope codes
# stable instead of leaking ``HTTP_401``-style placeholders to clients.
STATUS_CODE_NAMES: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "AUTH_REQUIRED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


def error_envelope(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: object | None = None,
) -> JSONResponse:
    """Render the unified error envelope shared by every module."""
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "data": None,
            "error": {
                "code": code,
                "message": message,
                "details": jsonable_encoder(details) if details is not None else None,
            },
            "request_id": getattr(request.state, "request_id", ""),
        },
    )


def status_code_name(status_code: int) -> str:
    """Return the stable business code for a bare HTTP status."""
    return STATUS_CODE_NAMES.get(status_code, f"HTTP_{status_code}")


def register_common_error_handlers(app: FastAPI) -> None:
    """Register PlatformError / HTTPException / validation handlers on ``app``."""

    @app.exception_handler(PlatformError)
    async def platform_error_handler(request: Request, exc: PlatformError) -> JSONResponse:
        # Covers IDEMPOTENCY_KEY_REQUIRED / IDEMPOTENCY_CONFLICT /
        # INVALID_CONFIRMATION_TOKEN / auth failures raised by shared services.
        return error_envelope(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        # Platform dependencies raise HTTPException with either a plain string
        # or a structured {"code", "message", ...} detail; normalise both.
        detail = exc.detail
        if isinstance(detail, dict):
            code = str(detail.get("code", status_code_name(exc.status_code)))
            message = str(detail.get("message", ""))
            extra = {k: v for k, v in detail.items() if k not in {"code", "message"}}
            return error_envelope(
                request,
                status_code=exc.status_code,
                code=code,
                message=message,
                details=extra or None,
            )
        return error_envelope(
            request,
            status_code=exc.status_code,
            code=status_code_name(exc.status_code),
            message=str(detail),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return error_envelope(
            request,
            status_code=422,
            code="VALIDATION_ERROR",
            message="The request payload is invalid.",
            details={"errors": exc.errors()},
        )
