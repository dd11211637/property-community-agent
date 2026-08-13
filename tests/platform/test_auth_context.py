from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from property_agent.platform.adapters.api.dependencies import (
    RequestContext,
    get_current_house_context,
    get_current_user,
)
from property_agent.platform.infrastructure.orm_models import (
    AuditLogModel,
    UserHouseBindingModel,
    UserRoleModel,
)
from property_agent.platform.services.auth import create_jwt_token


def _request() -> Request:
    request = Request({"type": "http", "headers": []})
    request.state.request_id = "req-live-auth"
    return request


@pytest.mark.asyncio
async def test_request_context_reloads_roles_and_house_bindings_from_database(session, seed_data):
    actor_id = seed_data["user_a"]
    token = create_jwt_token(
        actor_id=actor_id,
        community_id=seed_data["community_a"],
        roles=["SYSTEM_ADMIN"],
        bound_house_ids=[uuid4()],
    )

    context = await get_current_user(_request(), f"Bearer {token}", session)

    assert context.roles == frozenset({"RESIDENT"})
    assert context.bound_house_ids == frozenset({seed_data["house_a1"]})


@pytest.mark.asyncio
async def test_revoked_authorization_disappears_without_token_expiry(session, seed_data):
    actor_id = seed_data["user_a"]
    token = create_jwt_token(
        actor_id=actor_id,
        community_id=seed_data["community_a"],
        roles=["RESIDENT"],
        bound_house_ids=[seed_data["house_a1"]],
    )
    session.query(UserRoleModel).filter_by(user_id=actor_id).delete()
    session.query(UserHouseBindingModel).filter_by(user_id=actor_id).update({"status": "INACTIVE"})
    session.commit()

    context = await get_current_user(_request(), f"Bearer {token}", session)

    assert context.roles == frozenset()
    assert context.bound_house_ids == frozenset()


@pytest.mark.asyncio
async def test_cross_house_denial_audit_is_committed(session, seed_data):
    context = RequestContext(
        actor_id=seed_data["user_a"],
        community_id=seed_data["community_a"],
        roles=frozenset({"RESIDENT"}),
        request_id="req-cross-house",
        bound_house_ids=frozenset({seed_data["house_a1"]}),
    )

    with pytest.raises(HTTPException) as denied:
        await get_current_house_context(
            _request(),
            context,
            session,
            str(seed_data["house_a2"]),
        )

    assert denied.value.status_code == 403
    session.expire_all()
    audit = session.query(AuditLogModel).filter_by(request_id="req-cross-house").one()
    assert audit.action == "ACCESS_DENIED"
    assert audit.result == "DENIED"
