from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from property_agent.platform.errors import BusinessError

MAX_REQUEST_ID_LENGTH = 64


def request_id(header_value: str | None) -> str:
    if header_value is not None:
        candidate = header_value.strip()
        if 1 <= len(candidate) <= MAX_REQUEST_ID_LENGTH:
            return candidate
    return f"req_{uuid4().hex}"


def install_http_foundation(app: FastAPI) -> None:
    """Install shared request tracing and error-envelope behavior once per app."""

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = request_id(request.headers.get("X-Request-ID"))
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(BusinessError)
    async def business_error_handler(request: Request, exc: BusinessError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "data": None,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
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
