from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from starlette.responses import Response

from property_agent.agent.observability import AgentObservability
from property_agent.platform.http_observability import observe_http_request, request_id


@pytest.mark.asyncio
async def test_http_log_correlation_excludes_headers_and_body_markers(caplog) -> None:
    markers = (
        "PRIVATE_USER_MESSAGE",
        "PRIVATE_MEMORY",
        "PRIVATE_SYSTEM_PROMPT",
        "PRIVATE_APPROVAL_TOKEN",
        "PRIVATE_CONFIRMATION_TOKEN",
        "PRIVATE_IDEMPOTENCY_KEY",
        "PRIVATE_ADDRESS",
        "13800000000",
    )
    marker_blob = "|".join(markers).encode()
    app = SimpleNamespace(state=SimpleNamespace(agent_observability=AgentObservability.in_memory()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/safe",
        "headers": [(b"x-private-marker", marker_blob)],
        "app": app,
        "route": SimpleNamespace(path="/safe"),
    }
    request = Request(scope)

    async def call_next(_request):
        return Response(status_code=200)

    with caplog.at_level(logging.INFO, logger="property_agent.access"):
        response = await observe_http_request(request, call_next)

    assert response.status_code == 200
    records = [record for record in caplog.records if record.name == "property_agent.access"]
    assert len(records) == 1
    rendered = repr(records[0].__dict__)
    assert records[0].request_id.startswith("req_")
    assert all(marker not in rendered for marker in markers)


@pytest.mark.asyncio
async def test_untrusted_request_id_cannot_enter_spans_metrics_or_logs(caplog) -> None:
    markers = (
        "13800000000",
        "Shanghai-Road-88",
        "private@example.com",
        "control-marker\r\nunsafe",
        "L" * 200,
    )
    observability = AgentObservability.in_memory()
    app = SimpleNamespace(state=SimpleNamespace(agent_observability=observability))

    async def call_next(_request):
        return Response(status_code=200)

    with caplog.at_level(logging.INFO, logger="property_agent.access"):
        for marker in markers:
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/safe",
                "headers": [(b"x-request-id", marker.encode("latin-1"))],
                "app": app,
                "route": SimpleNamespace(path="/safe"),
            }
            response = await observe_http_request(Request(scope), call_next)
            safe_id = response.headers["X-Request-ID"]
            assert safe_id.startswith("req_") and len(safe_id) == 36

    rendered = repr(observability.spans) + repr(observability.points)
    rendered += repr([record.__dict__ for record in caplog.records])
    assert all(marker not in rendered for marker in markers)


def test_only_server_opaque_request_id_format_is_preserved() -> None:
    opaque = "req_0123456789abcdef0123456789abcdef"
    assert request_id(opaque) == opaque
    assert request_id("req_api") != "req_api"
