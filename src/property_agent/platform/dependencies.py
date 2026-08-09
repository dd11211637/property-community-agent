"""
Authentication seam for business-module routers.

Business routers depend on :func:`get_request_context` instead of importing the
JWT dependency directly. This keeps the module decoupled from *how* identity is
established:

* **Standalone module app / tests** — a trusted middleware (or a
  ``dependency_overrides`` entry) puts a :class:`RequestContext` on
  ``request.state.request_context``; without it the request is rejected with
  ``AUTH_REQUIRED``.
* **Unified application** (``property_agent.main``) — the seam is bound to the
  platform JWT dependency ``get_current_user`` via
  :func:`bind_request_context_to_jwt`, so production traffic is authenticated
  with real tokens.
"""

from __future__ import annotations

from dataclasses import replace

from fastapi import FastAPI, Request

from property_agent.platform.adapters.api.dependencies import get_current_user
from property_agent.platform.context import RequestContext
from property_agent.platform.errors import BusinessError

__all__ = ["bind_request_context_to_jwt", "get_request_context"]


def get_request_context(request: Request) -> RequestContext:
    """Return identity data injected by trusted authentication middleware."""
    context = getattr(request.state, "request_context", None)
    if not isinstance(context, RequestContext):
        raise BusinessError("AUTH_REQUIRED", "Authentication is required.", 401)
    request_id = getattr(request.state, "request_id", "")
    if request_id and context.request_id != request_id:
        return replace(context, request_id=request_id)
    return context


def bind_request_context_to_jwt(app: FastAPI) -> None:
    """Wire the auth seam to the platform JWT dependency for production apps."""
    app.dependency_overrides[get_request_context] = get_current_user
