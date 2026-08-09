"""
Platform API routes — auth and shared platform endpoints.

PRD 5.2 (PF-01, PF-02).
Health check routes moved to health_routes.py (PRD 5.4).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from property_agent.platform.adapters.api.dependencies import (
    RequestContext,
    get_current_user,
)
from property_agent.platform.adapters.api.schemas import (
    ConfirmationGenerateRequest,
    ConfirmationGenerateResponse,
    HouseSelectionRequest,
    HouseSelectionResponse,
    LoginRequest,
    LoginResponse,
)
from property_agent.platform.infrastructure.database import get_db
from property_agent.platform.infrastructure.orm_models import (
    CommunityModel,
    HouseModel,
    UserHouseBindingModel,
    UserModel,
    UserRoleModel,
)
from property_agent.platform.services.auth import create_jwt_token, verify_password
from property_agent.platform.services.shared import (
    AuditService,
    ConfirmationService,
)

router = APIRouter(tags=["platform"])

# ═══════════════════════════════════════════════════════════════
# Auth routes (PF-01, PF-02)
# ═══════════════════════════════════════════════════════════════

@router.post("/api/auth/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> LoginResponse:
    """JWT login endpoint (PF-01).

    Validates username/password against the users table using bcrypt,
    returns a signed JWT access token. The token payload is generated
    server-side and includes actor_id, community_id, roles, and
    bound_house_ids. The frontend must not submit or override these claims.
    """
    user = (
        db.query(UserModel)
        .filter_by(username=body.username, status="ACTIVE")
        .first()
    )

    if user is None:
        AuditService(db).log(
            actor_id=UUID("00000000-0000-0000-0000-000000000000"),
            community_id=UUID("00000000-0000-0000-0000-000000000000"),
            action="LOGIN_FAILED",
            resource_type="USER",
            parameter_summary={"username": body.username},
            result="FAILURE",
            request_id=getattr(request.state, "request_id", ""),
        )
        raise HTTPException(401, detail="Invalid username or password")

    if not verify_password(body.password, user.password_hash):
        AuditService(db).log(
            actor_id=user.id,
            community_id=user.community_id,
            action="LOGIN_FAILED",
            resource_type="USER",
            resource_id=str(user.id),
            parameter_summary={"username": body.username},
            result="FAILURE",
            request_id=getattr(request.state, "request_id", ""),
        )
        raise HTTPException(401, detail="Invalid username or password")

    # Get roles (all role assignments)
    roles = db.query(UserRoleModel).filter_by(user_id=user.id).all()
    role_names = [r.role for r in roles] if roles else ["RESIDENT"]

    # Get active house bindings
    bindings = (
        db.query(UserHouseBindingModel)
        .filter_by(user_id=user.id, status="ACTIVE")
        .all()
    )
    house_ids = [b.house_id for b in bindings]

    # Auto-select for single-house users
    current_house_id = house_ids[0] if len(house_ids) == 1 else None

    # Get community name
    community = db.query(CommunityModel).filter_by(id=user.community_id).first()
    community_name = community.name if community else ""

    # Create signed JWT — payload includes actor_id, community_id, roles, bound_house_ids
    token = create_jwt_token(
        actor_id=user.id,
        community_id=user.community_id,
        roles=role_names,
        bound_house_ids=house_ids,
    )

    # Audit successful login
    AuditService(db).log(
        actor_id=user.id,
        community_id=user.community_id,
        action="LOGIN_SUCCESS",
        resource_type="USER",
        resource_id=str(user.id),
        result="SUCCESS",
        request_id=getattr(request.state, "request_id", ""),
    )

    return LoginResponse(
        access_token=token,
        actor_id=user.id,
        display_name=user.display_name,
        community_id=user.community_id,
        community_name=community_name,
        roles=role_names,
        house_ids=house_ids,
        current_house_id=current_house_id,
    )


@router.post("/api/auth/house", response_model=HouseSelectionResponse)
def select_house(
    body: HouseSelectionRequest,
    request: Request,
    context: RequestContext = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> HouseSelectionResponse:
    """Select or switch current house (PF-02)."""
    if body.house_id not in context.bound_house_ids:
        AuditService(db).log(
            actor_id=context.actor_id,
            community_id=context.community_id,
            action="ACCESS_DENIED",
            resource_type="HOUSE",
            resource_id=str(body.house_id),
            result="DENIED",
            request_id=context.request_id,
        )
        raise HTTPException(403, detail="Not bound to this house")

    house = db.query(HouseModel).filter_by(id=body.house_id).first()
    if not house:
        raise HTTPException(404, detail="House not found")

    return HouseSelectionResponse(
        house_id=house.id,
        building=house.building,
        unit=house.unit,
        room_no=house.room_no,
    )


# ═══════════════════════════════════════════════════════════════
# Confirmation & Idempotency helpers (PF-04)
# ═══════════════════════════════════════════════════════════════

@router.post("/api/confirmations", response_model=ConfirmationGenerateResponse)
def generate_confirmation(
    body: ConfirmationGenerateRequest,
    context: RequestContext = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ConfirmationGenerateResponse:
    """Generate a confirmation token for a pending write operation."""
    svc = ConfirmationService(db)
    token = svc.generate(
        actor_id=context.actor_id,
        action=body.action,
        parameters=body.parameters,
    )
    return ConfirmationGenerateResponse(token=token)