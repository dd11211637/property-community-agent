"""M2 message center and management dashboard API integration tests."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from property_agent.inspection.infrastructure.models import SecurityEventModel
from property_agent.platform.adapters.api.dependencies import RequestContext, get_current_user
from property_agent.platform.adapters.api.envelope import register_common_error_handlers
from property_agent.platform.adapters.api.operations_routes import router
from property_agent.platform.infrastructure.database import get_db
from property_agent.platform.infrastructure.orm_models import (
    AuditLogModel,
    Base,
    CommunityModel,
    HandoverTicketModel,
    MessageRecordModel,
    UserModel,
    UserRoleModel,
)
from property_agent.platform.infrastructure.outbox_dispatcher import OutboxDispatcher

COMMUNITY_A = UUID("10000000-0000-0000-0000-000000000001")
COMMUNITY_B = UUID("20000000-0000-0000-0000-000000000001")
RESIDENT_A = UUID("10000000-0000-0000-0000-000000000011")
MANAGER_A = UUID("10000000-0000-0000-0000-000000000012")
MANAGER_B = UUID("20000000-0000-0000-0000-000000000012")


@pytest.fixture
def api_env():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add_all(
            [
                CommunityModel(id=COMMUNITY_A, name="社区A"),
                CommunityModel(id=COMMUNITY_B, name="社区B"),
                UserModel(
                    id=RESIDENT_A,
                    community_id=COMMUNITY_A,
                    username="resident-a",
                    display_name="住户A",
                    password_hash="unused",
                    phone="138****0001",
                ),
                UserModel(
                    id=MANAGER_A,
                    community_id=COMMUNITY_A,
                    username="manager-a",
                    display_name="经理A",
                    password_hash="unused",
                    phone="138****0002",
                ),
                UserModel(
                    id=UUID("10000000-0000-0000-0000-000000000013"),
                    community_id=COMMUNITY_A,
                    username="repair-a",
                    display_name="维修员A",
                    password_hash="unused",
                ),
                UserModel(
                    id=MANAGER_B,
                    community_id=COMMUNITY_B,
                    username="manager-b",
                    display_name="经理B",
                    password_hash="unused",
                    phone="138****0003",
                ),
            ]
        )
        session.flush()
        session.add(
            UserRoleModel(
                user_id=UUID("10000000-0000-0000-0000-000000000013"),
                role="REPAIR_WORKER",
                scope="*",
            )
        )
        own_message = MessageRecordModel(
            id=uuid4(),
            receiver_id=RESIDENT_A,
            business_type="REPAIR",
            resource_id="repair-a",
            title="报修进展",
            body="已派单",
            status="SENT",
            idempotency_key="own-message",
        )
        manager_failed = MessageRecordModel(
            id=uuid4(),
            receiver_id=MANAGER_A,
            business_type="INSPECTION",
            resource_id="event-a",
            title="投递失败A",
            body="请人工接管",
            status="FAILED",
            retry_count=5,
            idempotency_key="failed-a",
        )
        other_failed = MessageRecordModel(
            id=uuid4(),
            receiver_id=MANAGER_B,
            business_type="BILLING",
            resource_id="bill-b",
            title="投递失败B",
            body="其他社区",
            status="FAILED",
            retry_count=5,
            idempotency_key="failed-b",
        )
        session.add_all([own_message, manager_failed, other_failed])
        session.add_all(
            [
                HandoverTicketModel(
                    id=uuid4(),
                    community_id=COMMUNITY_A,
                    requester_id=MANAGER_A,
                    resource_type="MESSAGE",
                    resource_id=str(manager_failed.id),
                    source="INSPECTION",
                    queue="SECURITY",
                    summary="社区A待办",
                    reason="MESSAGE_DELIVERY_FAILED",
                    status="PENDING",
                ),
                HandoverTicketModel(
                    id=uuid4(),
                    community_id=COMMUNITY_B,
                    requester_id=MANAGER_B,
                    source="BILLING",
                    queue="CUSTOMER_SERVICE",
                    summary="社区B待办",
                    reason="MANUAL_REQUEST",
                    status="PENDING",
                ),
                SecurityEventModel(
                    id=uuid4(),
                    community_id=COMMUNITY_A,
                    business_no="SE-A-001",
                    reporter_id=RESIDENT_A,
                    event_type="FIRE",
                    risk_level="HIGH_RISK",
                    location="A栋",
                    description="社区A高风险",
                    create_idempotency_key="security-a",
                    status="ASSIGNED",
                    version=1,
                    created_at=now,
                    updated_at=now,
                ),
                SecurityEventModel(
                    id=uuid4(),
                    community_id=COMMUNITY_B,
                    business_no="SE-B-001",
                    reporter_id=MANAGER_B,
                    event_type="FIRE",
                    risk_level="HIGH_RISK",
                    location="B栋",
                    description="社区B高风险",
                    create_idempotency_key="security-b",
                    status="ASSIGNED",
                    version=1,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        session.commit()
        own_message_id = own_message.id
        manager_failed_id = manager_failed.id

    app = FastAPI()
    register_common_error_handlers(app)
    app.include_router(router)

    def override_db():
        with session_factory() as session:
            yield session

    async def override_user(request: Request):
        actor_id = UUID(request.headers.get("X-Test-Actor", str(RESIDENT_A)))
        user_community = COMMUNITY_B if actor_id == MANAGER_B else COMMUNITY_A
        role = request.headers.get("X-Test-Role", "RESIDENT")
        return RequestContext(
            actor_id=actor_id,
            community_id=user_community,
            roles=frozenset({role}),
            request_id="test-request",
        )

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    with TestClient(app) as client:
        yield client, session_factory, own_message_id, manager_failed_id
    engine.dispose()


def test_message_list_is_user_scoped_and_filterable(api_env):
    client, _, own_message_id, _ = api_env
    response = client.get("/api/messages?status=UNREAD&business_type=REPAIR")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["id"] == str(own_message_id)


def test_mark_read_is_scoped_idempotent_and_audited(api_env):
    client, session_factory, own_message_id, manager_failed_id = api_env
    headers = {"Idempotency-Key": "read-once"}
    first = client.post(f"/api/messages/{own_message_id}/read", headers=headers)
    replay = client.post(f"/api/messages/{own_message_id}/read", headers=headers)
    forbidden = client.post(
        f"/api/messages/{manager_failed_id}/read",
        headers={"Idempotency-Key": "read-foreign"},
    )
    assert first.status_code == replay.status_code == 200
    assert first.json()["data"] == replay.json()["data"]
    assert first.json()["data"]["status"] == "SENT"
    assert first.json()["data"]["is_read"] is True
    assert forbidden.status_code == 404
    with session_factory() as session:
        assert session.get(MessageRecordModel, own_message_id).read_at is not None
        assert session.query(AuditLogModel).filter_by(action="MESSAGE_READ").count() == 1


def test_mark_all_read_does_not_touch_other_users(api_env):
    client, session_factory, _, manager_failed_id = api_env
    response = client.post("/api/messages/read-all", headers={"Idempotency-Key": "read-all-once"})
    assert response.status_code == 200
    assert response.json()["data"]["updated_count"] == 1
    with session_factory() as session:
        assert session.get(MessageRecordModel, manager_failed_id).read_at is None


def test_admin_dashboard_uses_real_community_scoped_aggregates(api_env):
    client, _, _, manager_failed_id = api_env
    resident = client.get("/api/admin/dashboard")
    assert resident.status_code == 403

    manager = client.get(
        "/api/admin/dashboard",
        headers={"X-Test-Actor": str(MANAGER_A), "X-Test-Role": "MANAGER"},
    )
    assert manager.status_code == 200
    data = manager.json()["data"]
    assert data["pending_count"] == 1
    assert data["failed_message_count"] == 1
    assert data["high_risk_event_count"] == 1
    assert data["failed_messages"][0]["id"] == str(manager_failed_id)
    assert data["failed_messages"][0]["fallback_contact"] == "138****0002"
    assert data["integration_health"]["message_delivery"] == "DEGRADED"
    assert data["integration_health"]["database"] == "UP"
    assert data["integration_health"]["model_gateway"] in {
        "CONFIGURED_NOT_PROBED",
        "DETERMINISTIC_FALLBACK",
    }


def test_staff_directory_is_role_and_community_scoped(api_env):
    client, _, _, _ = api_env
    resident = client.get("/api/staff?role=REPAIR_WORKER")
    assert resident.status_code == 403

    manager = client.get(
        "/api/staff?role=REPAIR_WORKER",
        headers={"X-Test-Actor": str(MANAGER_A), "X-Test-Role": "MANAGER"},
    )
    assert manager.status_code == 200
    assert manager.json()["data"] == [
        {
            "id": "10000000-0000-0000-0000-000000000013",
            "display_name": "维修员A",
            "role": "REPAIR_WORKER",
        }
    ]


@pytest.mark.asyncio
async def test_retry_exhaustion_creates_one_manual_handover(api_env):
    _, session_factory, _, _ = api_env
    message_id = uuid4()
    with session_factory() as session:
        session.add(
            MessageRecordModel(
                id=message_id,
                receiver_id=MANAGER_A,
                business_type="BILLING",
                resource_id="consultation-a",
                title="财务咨询通知",
                body="投递失败",
                status="PENDING",
                idempotency_key="retry-handover",
            )
        )
        session.commit()

    dispatcher = OutboxDispatcher(
        session_factory=session_factory,
        send_message=AsyncMock(return_value=False),
        max_retry=1,
    )
    assert await dispatcher.run_once() == 1
    with session_factory() as session:
        message = session.get(MessageRecordModel, message_id)
        handovers = (
            session.query(HandoverTicketModel)
            .filter_by(resource_type="MESSAGE", resource_id=str(message_id))
            .all()
        )
        assert message.status == "FAILED"
        assert len(handovers) == 1
        assert handovers[0].community_id == COMMUNITY_A
        assert handovers[0].queue == "CUSTOMER_SERVICE"
