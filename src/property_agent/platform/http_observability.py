"""Request correlation, W3C trace extraction, and bounded HTTP telemetry."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from time import perf_counter
from uuid import uuid4

from fastapi import Request

from property_agent.config import settings

MAX_REQUEST_ID_LENGTH = 64
access_logger = logging.getLogger("property_agent.access")


def request_id(header_value: str | None) -> str:
    if header_value is not None:
        candidate = header_value.strip()
        if 1 <= len(candidate) <= MAX_REQUEST_ID_LENGTH:
            return candidate
    return f"req_{uuid4().hex}"


async def observe_http_request(request: Request, call_next):
    request.state.request_id = request_id(request.headers.get("X-Request-ID"))
    started = perf_counter()
    status_code = 500
    observability = getattr(request.app.state, "agent_observability", None)
    context = (
        observability.request_span(
            request.headers, request_id=request.state.request_id, method=request.method
        )
        if observability is not None
        else nullcontext()
    )
    with context as request_span:
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request.state.request_id
            return response
        finally:
            _finish_request(request, observability, request_span, started, status_code)


def _finish_request(request, observability, request_span, started, status_code) -> None:
    duration_seconds = perf_counter() - started
    route = request.scope.get("route")
    route_template = getattr(route, "path", "unmatched")
    if request_span is not None:
        request_span.set_attribute("http.route", route_template)
        request_span.set_attribute("http.response.status_code", status_code)
    trace_id, span_id = observability.correlation() if observability is not None else (None, None)
    if observability is not None:
        attributes = {"operation": request.method, "outcome": f"{status_code // 100}xx"}
        observability.count("agent_http_request_total", attributes=attributes)
        observability.duration(
            "agent_http_request_duration_seconds", duration_seconds, attributes=attributes
        )
    duration_ms = round(duration_seconds * 1000, 2)
    level = (
        logging.WARNING
        if duration_ms >= settings.slow_request_threshold_ms or status_code >= 500
        else logging.INFO
    )
    access_logger.log(
        level,
        "http_request",
        extra={
            "request_id": request.state.request_id,
            "trace_id": trace_id,
            "span_id": span_id,
            "method": request.method,
            "route": route_template,
            "status_code": status_code,
            "duration_ms": duration_ms,
        },
    )


__all__ = ["observe_http_request", "request_id"]
