"""Failure-injection ASGI entry point for demonstrations only.

Run this entry point through ``testing/compose.demo.yaml``. Production imports
never reference this module or its switches.
"""

from __future__ import annotations

import os

from fastapi import Request
from fastapi.responses import JSONResponse

from property_agent.main import create_app

app = create_app()


def _enabled(name: str) -> bool:
    return os.getenv(name, "false").lower() in {"1", "true", "yes", "on"}


@app.middleware("http")
async def inject_demo_failures(request: Request, call_next):
    path = request.url.path
    failure = None
    if _enabled("DEMO_FAIL_BILLING_SOURCE") and path.startswith("/api/billing"):
        failure = "BILLING_SOURCE_UNAVAILABLE"
    elif _enabled("DEMO_FAIL_MODEL") and path.startswith("/api/agent/conversations"):
        failure = "MODEL_UNAVAILABLE"
    elif _enabled("DEMO_FAIL_MESSAGE_TRANSPORT") and path.startswith("/api/messages"):
        failure = "MESSAGE_TRANSPORT_UNAVAILABLE"
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
