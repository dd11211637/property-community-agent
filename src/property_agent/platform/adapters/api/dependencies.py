"""
Platform API dependencies — auth, house selection, and RBAC guards.

PRD 5.2: PF-01 (JWT Login), PF-02 (Current House Selection), PF-03 (RBAC & Data Isolation).

The RequestContext uses Python contextvars for coroutine safety. Any code in the
call stack can access the current request context via RequestContext.current().

Provides:
  - get_current_user: validate JWT and inject RequestContext
  - get_current_house_context: resolve current house, auto-select for single-house users
  - require_role: role-based access control guard
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request
from jose import JWTError
from sqlalchemy.orm import Session

from property_agent.platform.infrastructure.database import get_db
from property_agent.platform.infrastructure.orm_models import (
    HouseModel,
    UserModel,
)
from property_agent.platform.services.auth import decode_jwt_token
from property_agent.platform.services.shared import AuditService

# ---------------------------------------------------------------------------
# contextvars — coroutine-safe RequestContext
# ---------------------------------------------------------------------------

_request_context_var: contextvars.ContextVar[RequestContext | None] = contextvars.ContextVar(
    "request_context", default=None
)


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Coroutine-safe request context (PRD 5.2).

    Fields:
        actor_id: authenticated user ID
        community_id: current community (from JWT, not frontend)
        roles: frozen set of role strings
        request_id: trace identifier
        current_house_id: resolved current house (None until house selection)
        bound_house_ids: all active house bindings
    """

    actor_id: UUID
    community_id: UUID
    roles: frozenset[str]
    request_id: str
    current_house_id: UUID | None = None
    bound_house_ids: frozenset[UUID] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.request_id.strip() or len(self.request_id) > 64:
            raise ValueError("request_id must contain 1 to 64 non-whitespace characters.")

    def has_any_role(self, *roles: str) -> bool:
        return bool(self.roles.intersection(roles))

    # -- contextvars helpers --

    @classmethod
    def current(cls) -> RequestContext | None:
        """Return the current coroutine's RequestContext, or None."""
        return _request_context_var.get()

    def activate(self) -> None:
        """Set this context as the current coroutine's context."""
        _request_context_var.set(self)


# ═══════════════════════════════════════════════════════════════
# get_current_user — FastAPI dependency (JWT-based, PF-01)
# ═══════════════════════════════════════════════════════════════


async def get_current_user(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    db: Session = Depends(get_db),  # noqa: B008
) -> RequestContext:
    """Validate the JWT Authorization header and return a RequestContext.

    Extracts actor_id, community_id, roles, and bound_house_ids from the JWT.
    Verifies the user exists and is ACTIVE. All claims are server-generated;
    the frontend cannot submit or override roles, community_id, or house bindings.
    """
    if not authorization:
        raise HTTPException(401, detail="Authorization header required")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, detail="Invalid Authorization header format")

    # Decode and validate JWT
    try:
        payload = decode_jwt_token(token)
    except JWTError as exc:
        raise HTTPException(401, detail=f"Invalid or expired token: {exc}") from exc

    # Extract claims (all server-generated — trust the JWT payload)
    actor_id_str: str | None = payload.get("actor_id") or payload.get("sub")
    community_id_str: str | None = payload.get("community_id")
    roles_list: list[str] = payload.get("roles", [])
    bound_house_ids_raw: list[str] = payload.get("bound_house_ids", [])

    if not actor_id_str or not community_id_str:
        raise HTTPException(401, detail="Token missing required claims")

    try:
        actor_id = UUID(actor_id_str)
        community_id = UUID(community_id_str)
    except ValueError:
        raise HTTPException(401, detail="Invalid ID format in token") from None

    # Verify user still exists and is active (defence in depth)
    user = db.query(UserModel).filter_by(id=actor_id, status="ACTIVE").first()
    if user is None:
        raise HTTPException(401, detail="User not found or inactive")

    # Build bound_house_ids from JWT (avoid DB round-trip for house bindings)
    bound_house_ids: frozenset[UUID] = frozenset()
    for raw in bound_house_ids_raw:
        try:
            bound_house_ids = bound_house_ids | {UUID(raw)}
        except ValueError:
            pass  # skip malformed IDs

    roles = frozenset(roles_list) if roles_list else frozenset({"RESIDENT"})

    request_id = getattr(request.state, "request_id", "")

    ctx = RequestContext(
        actor_id=actor_id,
        community_id=community_id,
        roles=roles,
        request_id=request_id,
        bound_house_ids=bound_house_ids,
        current_house_id=None,  # resolved by get_current_house_context
    )
    ctx.activate()
    return ctx


# ═══════════════════════════════════════════════════════════════
# get_current_house_context — FastAPI dependency (PF-02)
# ═══════════════════════════════════════════════════════════════


async def get_current_house_context(
    request: Request,
    context: RequestContext = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    x_current_house_id: Annotated[str | None, Header(alias="X-Current-House-Id")] = None,
) -> RequestContext:
    """Resolve the current house ID and return an updated RequestContext.

    - Single-house users: auto-select the only valid house.
    - Multi-house users: must specify via X-Current-House-Id header.
    - No valid binding: 403.
    - Cross-house access: 403 with audit.

    The returned context has current_house_id set and is activated
    on the contextvars stack for downstream access.
    """
    if not context.bound_house_ids:
        raise HTTPException(403, detail="No valid house binding found")

    if x_current_house_id:
        try:
            house_id = UUID(x_current_house_id)
        except ValueError:
            raise HTTPException(400, detail="Invalid X-Current-House-Id format") from None

        if house_id not in context.bound_house_ids:
            # Cross-house access attempt — audit and reject
            audit = AuditService(db)
            audit.log(
                actor_id=context.actor_id,
                community_id=context.community_id,
                action="ACCESS_DENIED",
                resource_type="HOUSE",
                resource_id=str(house_id),
                parameter_summary={
                    "reason": "cross_house_access",
                    "user_house_ids": [str(h) for h in context.bound_house_ids],
                },
                result="DENIED",
                request_id=context.request_id,
            )
            raise HTTPException(403, detail="Access denied: not bound to this house")

        # Valid: create updated context with current_house_id set
        updated = RequestContext(
            actor_id=context.actor_id,
            community_id=context.community_id,
            roles=context.roles,
            request_id=context.request_id,
            current_house_id=house_id,
            bound_house_ids=context.bound_house_ids,
        )
        updated.activate()
        return updated

    # Single house — auto-select
    if len(context.bound_house_ids) == 1:
        house_id = next(iter(context.bound_house_ids))
        updated = RequestContext(
            actor_id=context.actor_id,
            community_id=context.community_id,
            roles=context.roles,
            request_id=context.request_id,
            current_house_id=house_id,
            bound_house_ids=context.bound_house_ids,
        )
        updated.activate()
        return updated

    # Multi-house, none specified — return HOUSE_SELECTION_REQUIRED
    houses = db.query(HouseModel).filter(HouseModel.id.in_(context.bound_house_ids)).all()
    house_options = [
        {"id": str(h.id), "building": h.building, "unit": h.unit, "room_no": h.room_no}
        for h in houses
    ]
    raise HTTPException(
        status_code=400,
        detail={
            "code": "HOUSE_SELECTION_REQUIRED",
            "message": "Multiple houses available. "
            "Please select one via X-Current-House-Id header.",
            "options": house_options,
        },
    )


# ═══════════════════════════════════════════════════════════════
# require_role — RBAC guard (PF-03)
# ═══════════════════════════════════════════════════════════════


def require_role(*allowed_roles: str):
    """FastAPI dependency factory: require at least one of the given roles."""

    async def _guard(context: RequestContext = Depends(get_current_user)) -> RequestContext:  # noqa: B008
        if not context.has_any_role(*allowed_roles):
            raise HTTPException(
                403,
                detail={
                    "code": "ROLE_REQUIRED",
                    "message": f"Requires one of: {', '.join(allowed_roles)}",
                    "current_roles": list(context.roles),
                },
            )
        return context

    return _guard


# ═══════════════════════════════════════════════════════════════
# require_idempotency_key — PF-04 idempotency interceptor
# ═══════════════════════════════════════════════════════════════


async def require_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    """FastAPI dependency: enforce Idempotency-Key header on write endpoints.

    Raises HTTP 400 with code ``IDEMPOTENCY_KEY_REQUIRED`` if the header is
    missing or empty.

    Usage::

        @router.post("/bills")
        async def create_bill(
            body: CreateBillRequest,
            idempotency_key: str = Depends(require_idempotency_key),
            db: Session = Depends(get_db),
            ctx: RequestContext = Depends(get_current_user),
        ):
            ...
    """
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(
            status_code=400,
            detail={
                "code": "IDEMPOTENCY_KEY_REQUIRED",
                "message": "Idempotency-Key header is required for write operations.",
            },
        )
    return idempotency_key.strip()


# ═══════════════════════════════════════════════════════════════
# get_current_house_id — simple dependency for house_id
# ═══════════════════════════════════════════════════════════════


async def get_current_house_id(
    context: RequestContext = Depends(get_current_house_context),  # noqa: B008
) -> UUID:
    """Return the resolved current house ID."""
    if context.current_house_id is None:
        raise HTTPException(400, detail="No house selected")
    return context.current_house_id
