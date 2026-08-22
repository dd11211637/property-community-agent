"""
End-to-end test of the assembled production application — PRD 6.1 / 5.4.

Before this block the unified app answered ``503 ADAPTER_NOT_CONFIGURED`` on
every repair endpoint because ``container._build_services()`` only returned
string placeholders. These tests drive the *real* stack:

    main.create_app()
      → build_production_container()   (sync session factory + shared ports)
      → JWT auth dependency            (platform.get_current_user)
      → repair router                  (unified envelope)
      → SQLite database

so a regression that unwires the container fails here immediately.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from property_agent.main import create_app
from property_agent.platform.application.hashing import canonical_hash
from property_agent.platform.container import build_production_container
from property_agent.platform.infrastructure import database as platform_db
from property_agent.platform.infrastructure.orm_models import (
    Base,
    CommunityModel,
    ConfirmationTokenModel,
    HandoverTicketModel,
    HouseModel,
    MessageRecordModel,
    UserHouseBindingModel,
    UserModel,
    UserRoleModel,
)
from property_agent.platform.services.auth import create_jwt_token, hash_password
from property_agent.repair.infrastructure.models import WorkOrderModel

COMMUNITY = UUID("c0000000-0000-0000-0000-000000000001")
HOUSE = UUID("c1000000-0000-0000-0000-000000000101")
RESIDENT = UUID("c2000000-0000-0000-0000-000000000001")
CUSTOMER_SERVICE = UUID("c2000000-0000-0000-0000-000000000002")


@pytest.fixture
def sessions(monkeypatch) -> sessionmaker:
    """Point the platform session factory at an in-memory SQLite database."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(platform_db, "_engine", engine)
    monkeypatch.setattr(platform_db, "_SessionLocal", factory)
    yield factory
    engine.dispose()


@pytest.fixture
def seeded(sessions) -> sessionmaker:
    with sessions() as session:
        session.add_all(
            [
                CommunityModel(id=COMMUNITY, name="生产验收社区"),
                HouseModel(
                    id=HOUSE,
                    community_id=COMMUNITY,
                    building="3",
                    unit="1",
                    room_no="702",
                ),
                UserModel(
                    id=RESIDENT,
                    community_id=COMMUNITY,
                    username="resident",
                    display_name="住户",
                    password_hash=hash_password("123456"),
                ),
                UserModel(
                    id=CUSTOMER_SERVICE,
                    community_id=COMMUNITY,
                    username="cs",
                    display_name="客服",
                    password_hash=hash_password("123456"),
                ),
                UserRoleModel(user_id=RESIDENT, role="RESIDENT"),
                UserRoleModel(user_id=CUSTOMER_SERVICE, role="CUSTOMER_SERVICE"),
                UserHouseBindingModel(user_id=RESIDENT, house_id=HOUSE, status="ACTIVE"),
            ]
        )
        session.commit()
    return sessions


@pytest.fixture
def client(seeded) -> TestClient:
    """The unified app with the production container assembled.

    ``build_production_container`` is invoked directly instead of through the
    lifespan so the test does not need an async engine.
    """
    app = create_app()
    build_production_container(app)
    return TestClient(app)


def resident_token() -> str:
    return create_jwt_token(
        actor_id=RESIDENT,
        community_id=COMMUNITY,
        roles=["RESIDENT"],
        bound_house_ids=[HOUSE],
    )


def auth_headers(token: str, *, idempotency_key: str, request_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": idempotency_key,
        "X-Request-ID": request_id,
    }


def create_body(*, token: str = "", urgency: str = "NORMAL") -> dict:
    return {
        "house_id": str(HOUSE),
        "category": "WATER_PLUMBING",
        "location": "厨房",
        "description": "水管漏水，需要尽快处理。",
        "urgency": urgency,
        "confirmation_token": token,
        "attachment_ids": [],
    }


def mint_token(sessions, *, action: str, body: dict) -> str:
    """Mint a confirmation token matching the hash the service will compute."""
    from property_agent.repair.application.commands import CreateWorkOrderCommand
    from property_agent.repair.domain.enums import RepairCategory, Urgency

    command = CreateWorkOrderCommand(
        house_id=UUID(body["house_id"]),
        category=RepairCategory(body["category"]),
        location=body["location"],
        description=body["description"],
        urgency=Urgency(body["urgency"]),
        confirmation_token="",
        attachment_ids=(),
    )
    params = asdict(command)
    params.pop("confirmation_token")
    # P0: approval_ref 是服务端签发的审批锁指针，不参与业务参数指纹
    # （与 repair service.create 的 hash 计算保持一致）。
    params.pop("approval_ref", None)

    token = f"tok_{uuid4().hex}"
    with sessions() as session:
        session.add(
            ConfirmationTokenModel(
                token=token,
                actor_id=RESIDENT,
                action=action,
                parameter_hash=canonical_hash(params),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
        )
        session.commit()
    return token


# ═══════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════


def test_missing_token_returns_unified_auth_envelope(client) -> None:
    response = client.post(
        "/api/work-orders",
        json=create_body(),
        headers={"Idempotency-Key": "e2e-noauth"},
    )

    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "AUTH_REQUIRED"
    assert body["data"] is None


def test_create_work_order_through_the_assembled_app(seeded, client) -> None:
    token = mint_token(seeded, action="CREATE_WORK_ORDER", body=create_body())

    response = client.post(
        "/api/work-orders",
        json=create_body(token=token),
        headers=auth_headers(resident_token(), idempotency_key="e2e-create", request_id="req_e2e"),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["success"] is True
    assert body["request_id"] == "req_e2e"
    data = body["data"]
    assert data["status"] == "PENDING_ASSIGNMENT"
    assert data["house_id"] == str(HOUSE)

    with seeded() as session:
        stored = session.get(WorkOrderModel, UUID(data["id"]))
        assert stored is not None
        assert stored.community_id == COMMUNITY
        assert stored.reporter_id == RESIDENT


def test_retry_with_the_same_key_replays_the_first_response(seeded, client) -> None:
    token = mint_token(seeded, action="CREATE_WORK_ORDER", body=create_body())
    headers = auth_headers(resident_token(), idempotency_key="e2e-retry", request_id="req_retry")

    first = client.post("/api/work-orders", json=create_body(token=token), headers=headers)
    second = client.post("/api/work-orders", json=create_body(token=token), headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["data"]["id"] == second.json()["data"]["id"]

    with seeded() as session:
        assert len(session.execute(select(WorkOrderModel)).scalars().all()) == 1


def test_high_risk_returns_handover_ticket_instead_of_a_work_order(seeded, client) -> None:
    body = create_body(urgency="HIGH_RISK")
    token = mint_token(seeded, action="CREATE_WORK_ORDER_HANDOVER", body=body)

    response = client.post(
        "/api/work-orders",
        json=create_body(token=token, urgency="HIGH_RISK"),
        headers=auth_headers(
            resident_token(), idempotency_key="e2e-high-risk", request_id="req_hr"
        ),
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "HANDOVER_REQUIRED"
    ticket_id = UUID(payload["error"]["details"]["handover_ticket_id"])
    assert payload["error"]["details"]["notified_staff"] == 1

    with seeded() as session:
        ticket = session.get(HandoverTicketModel, ticket_id)
        assert ticket is not None
        assert ticket.reason == "HIGH_RISK"
        assert ticket.request_id == "req_hr"
        assert session.execute(select(WorkOrderModel)).scalars().all() == []

        message = session.execute(select(MessageRecordModel)).scalar_one()
        assert message.receiver_id == CUSTOMER_SERVICE
        assert message.resource_id == str(ticket_id)


def test_missing_idempotency_key_is_rejected_before_any_write(seeded, client) -> None:
    token = mint_token(seeded, action="CREATE_WORK_ORDER", body=create_body())

    response = client.post(
        "/api/work-orders",
        json=create_body(token=token),
        headers={"Authorization": f"Bearer {resident_token()}"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    with seeded() as session:
        assert session.execute(select(WorkOrderModel)).scalars().all() == []


def test_health_endpoints_stay_available(client) -> None:
    assert client.get("/health").status_code == 200
