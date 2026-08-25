from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from starlette.responses import Response

from property_agent.agent.observability import AgentObservability
from property_agent.platform.http_observability import observe_http_request


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
