"""Failure-injection ASGI entry point for demonstrations only.

Run this entry point through ``testing/compose.demo.yaml``. Production imports
never reference this module or its switches.
"""

from __future__ import annotations

import os

from fastapi import Request
from fastapi.responses import JSONResponse

from property_agent.agent.model_gateway import DeterministicModelGateway
from property_agent.main import create_app
from property_agent.platform import container
from property_agent.platform.infrastructure.outbox_dispatcher import OutboxDispatcher


async def _failed_message_transport(message) -> bool:
    return False


def _configure_demo_adapters() -> None:
    """Replace adapters only for this explicit demo entry point."""
    if _enabled("DEMO_FAIL_MODEL"):
        container.build_model_gateway = lambda _observability=None: DeterministicModelGateway()
    if _enabled("DEMO_FAIL_MESSAGE_TRANSPORT"):
        container.build_outbox_dispatcher = lambda: OutboxDispatcher(
            session_factory=container.get_session_factory(),
            send_message=_failed_message_transport,
        )


def _enabled(name: str) -> bool:
    return os.getenv(name, "false").lower() in {"1", "true", "yes", "on"}


_configure_demo_adapters()
app = create_app()


@app.middleware("http")
async def inject_demo_failures(request: Request, call_next):
    path = request.url.path
    failure = None
    if _enabled("DEMO_FAIL_BILLING_SOURCE") and (
        path.startswith("/api/billing/bills") or path.startswith("/api/billing/rules")
    ):
        failure = "BILLING_SOURCE_UNAVAILABLE"
    if failure:
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "data": None,
                "error": {"code": failure, "message": "Demo failure injection is enabled."},
                "request_id": getattr(request.state, "request_id", None),
            },
        )
    return await call_next(request)
