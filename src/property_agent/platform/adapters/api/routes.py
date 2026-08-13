"""
Platform API routes — auth and shared platform endpoints.

PRD 5.2 (PF-01, PF-02).
Health check routes moved to health_routes.py (PRD 5.4).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from ipaddress import ip_address, ip_network
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from property_agent.config import settings
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
from property_agent.platform.application.login_guard import LoginGuard, LoginLockedError
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

_UNKNOWN_ACTOR_ID = UUID("00000000-0000-0000-0000-000000000000")
_INVALID_LOGIN_MESSAGE = "Invalid username or password"
_LOCKED_LOGIN_MESSAGE = "Too many login attempts. Please try again later."
# Always perform bcrypt work for unknown/ambiguous users so response timing does
# not provide a useful username-enumeration signal.
_DUMMY_PASSWORD_HASH = "$2b$12$zocBBfY1IK6gOBT.6KwDpeR12qE3m6hTzxypdzz8V9pAULGco8PWC"


def _source_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    try:
        peer_address = ip_address(peer)
        trusted = any(
            peer_address in ip_network(item.strip(), strict=False)
            for item in settings.trusted_proxy_cidrs.split(",")
            if item.strip()
        )
    except ValueError:
        trusted = False

    if trusted:
        forwarded = request.headers.get("X-Real-IP", "").strip()
        try:
            return str(ip_address(forwarded)) if forwarded else peer
        except ValueError:
            return peer
    return peer


def _login_guard(db: Session) -> LoginGuard:
    return LoginGuard(
        db,
        failure_limit=settings.login_failure_limit,
        window=timedelta(minutes=settings.login_failure_window_minutes),
        lock_duration=timedelta(minutes=settings.login_lock_minutes),
    )


def _audit_login(
    db: Session,
    request: Request,
    *,
    username: str,
    source_ip: str,
    user: UserModel | None,
    action: str,
    result: str,
) -> None:
    AuditService(db).log(
        actor_id=user.id if user else _UNKNOWN_ACTOR_ID,
        community_id=user.community_id if user else _UNKNOWN_ACTOR_ID,
        action=action,
        resource_type="USER",
        resource_id=str(user.id) if user else None,
        parameter_summary={"username": username, "source_ip": source_ip},
        result=result,
        request_id=getattr(request.state, "request_id", ""),
    )


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
    source_ip = _source_ip(request)
    guard = _login_guard(db)
    try:
        guard.ensure_allowed(body.username, source_ip)
    except LoginLockedError:
        _audit_login(
            db,
            request,
            username=body.username,
            source_ip=source_ip,
            user=None,
            action="LOGIN_BLOCKED",
            result="DENIED",
        )
        db.commit()
        raise HTTPException(429, detail=_LOCKED_LOGIN_MESSAGE) from None

    normalized_username = guard.normalize_username(body.username)
    users = (
        db.query(UserModel)
        .filter(func.lower(UserModel.username) == normalized_username, UserModel.status == "ACTIVE")
        .limit(2)
        .all()
    )
    # The schema historically scopes username uniqueness to a community while
    # the login contract has no community field. Ambiguous names must fail
    # closed instead of authenticating an arbitrary community account.
    user = users[0] if len(users) == 1 else None
    password_valid = verify_password(
        body.password,
        user.password_hash if user is not None else _DUMMY_PASSWORD_HASH,
    )
    if user is None or not password_valid:
        locked = guard.record_failure(body.username, source_ip)
        _audit_login(
            db,
            request,
            username=body.username,
            source_ip=source_ip,
            user=user,
            action="LOGIN_FAILED",
            result="FAILURE",
        )
        db.commit()
        if locked:
            raise HTTPException(429, detail=_LOCKED_LOGIN_MESSAGE)
        raise HTTPException(401, detail=_INVALID_LOGIN_MESSAGE)

    now = datetime.now(timezone.utc)
    roles = (
        db.query(UserRoleModel)
        .filter(
            UserRoleModel.user_id == user.id,
            UserRoleModel.valid_from <= now,
            (UserRoleModel.valid_until.is_(None) | (UserRoleModel.valid_until > now)),
        )
        .all()
    )
    role_names = [r.role for r in roles] if roles else ["RESIDENT"]

    # Get active house bindings
    bindings = (
        db.query(UserHouseBindingModel)
        .join(HouseModel, HouseModel.id == UserHouseBindingModel.house_id)
        .filter(
            UserHouseBindingModel.user_id == user.id,
            UserHouseBindingModel.status == "ACTIVE",
            UserHouseBindingModel.valid_from <= now,
            (
                UserHouseBindingModel.valid_until.is_(None)
                | (UserHouseBindingModel.valid_until > now)
            ),
            HouseModel.community_id == user.community_id,
            HouseModel.status == "ACTIVE",
        )
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

    guard.record_success(body.username, source_ip)
    _audit_login(
        db,
        request,
        username=body.username,
        source_ip=source_ip,
        user=user,
        action="LOGIN_SUCCESS",
        result="SUCCESS",
    )
    db.commit()

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
        db.commit()
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
    db.commit()
    return ConfirmationGenerateResponse(token=token)
