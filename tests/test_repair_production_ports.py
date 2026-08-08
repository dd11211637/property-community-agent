"""
Production shared-port integration tests — PRD 6.1 报修生产化接入.

Unlike ``tests/test_service.py`` (in-memory fakes) these tests wire the *real*
adapters from ``repair.infrastructure.shared_ports`` against a live SQLite
database, exercising the exact code path the assembled FastAPI application
uses:

    WorkOrderService
      → SqlAlchemyRepairUnitOfWork
        → build_shared_ports(session)
          → idempotency_records / confirmation_tokens / houses /
            user_house_bindings / user_roles / attachments /
            audit_logs / message_records / handover_tickets

Coverage:
  * idempotent create (replay + conflict)
  * confirmation token consume / reuse / parameter tampering
  * house access (binding, community isolation, community-wide staff roles)
  * attachment validation (existence, owner, scope, status, type, size)
  * high-risk handover ticket + duty-staff notification (no work order)
  * status change → timeline + outbox message + audit written in one commit
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from property_agent.platform.application.confirmation_service import ConfirmationService
from property_agent.platform.application.hashing import canonical_hash
from property_agent.platform.infrastructure.orm_models import (
    ATTACHMENT_MAX_SIZE_BYTES,
    AttachmentModel,
    AuditLogModel,
    Base,
    CommunityModel,
    HandoverTicketModel,
    HouseModel,
    IdempotencyRecordModel,
    MessageRecordModel,
    UserHouseBindingModel,
    UserModel,
    UserRoleModel,
)
from property_agent.repair.application.commands import (
    CreateWorkOrderCommand,
    ExecuteActionCommand,
)
from property_agent.repair.application.ports import RequestContext
from property_agent.repair.application.service import WorkOrderService
from property_agent.repair.domain.enums import (
    ActionCode,
    RepairCategory,
    Role,
    Urgency,
    WorkOrderStatus,
)
from property_agent.repair.domain.errors import BusinessError
from property_agent.repair.infrastructure.models import WorkOrderModel
from property_agent.repair.infrastructure.shared_ports import build_shared_ports
from property_agent.repair.infrastructure.uow import SqlAlchemyRepairUnitOfWork

# ═══════════════════════════════════════════════════════════════
# Fixtures — real SQLite database seeded with platform master data
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Seed:
    community: UUID
    other_community: UUID
    house: UUID
    unbound_house: UUID
    foreign_house: UUID
    resident: UUID
    other_resident: UUID
    customer_service: UUID
    manager: UUID
    repair_worker: UUID


@pytest.fixture
def sessions() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(
        bind=engine, autocommit=False, autoflush=False, expire_on_commit=False
    )


@pytest.fixture
def seed(sessions: sessionmaker[Session]) -> Seed:
    data = Seed(
        community=uuid4(),
        other_community=uuid4(),
        house=uuid4(),
        unbound_house=uuid4(),
        foreign_house=uuid4(),
        resident=uuid4(),
        other_resident=uuid4(),
        customer_service=uuid4(),
        manager=uuid4(),
        repair_worker=uuid4(),
    )
    with sessions() as session:
        session.add_all(
            [
                CommunityModel(id=data.community, name="阳光花园"),
                CommunityModel(id=data.other_community, name="翠竹苑"),
                HouseModel(
                    id=data.house, community_id=data.community,
                    building="1", unit="2", room_no="301",
                ),
                HouseModel(
                    id=data.unbound_house, community_id=data.community,
                    building="1", unit="2", room_no="302",
                ),
                HouseModel(
                    id=data.foreign_house, community_id=data.other_community,
                    building="9", unit="1", room_no="101",
                ),
            ]
        )
        for user_id, username, role in (
            (data.resident, "resident", "RESIDENT"),
            (data.other_resident, "resident2", "RESIDENT"),
            (data.customer_service, "cs", "CUSTOMER_SERVICE"),
            (data.manager, "manager", "MANAGER"),
            (data.repair_worker, "worker", "REPAIR_WORKER"),
        ):
            session.add(
                UserModel(
                    id=user_id,
                    community_id=data.community,
                    username=username,
                    display_name=username,
                    password_hash="x",
                )
            )
            session.add(UserRoleModel(user_id=user_id, role=role))
        session.add(
            UserHouseBindingModel(
                user_id=data.resident, house_id=data.house, status="ACTIVE"
            )
        )
        session.commit()
    return data


@pytest.fixture
def service(sessions: sessionmaker[Session]) -> WorkOrderService:
    return WorkOrderService(
        lambda: SqlAlchemyRepairUnitOfWork(sessions, build_shared_ports)
    )


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════


def resident_ctx(seed: Seed, request_id: str = "req_prod") -> RequestContext:
    return RequestContext(
        actor_id=seed.resident,
        community_id=seed.community,
        roles=frozenset({Role.RESIDENT}),
        request_id=request_id,
        house_ids=frozenset({seed.house}),
    )


def staff_ctx(seed: Seed, request_id: str = "req_staff") -> RequestContext:
    return RequestContext(
        actor_id=seed.customer_service,
        community_id=seed.community,
        roles=frozenset({Role.CUSTOMER_SERVICE}),
        request_id=request_id,
    )


def make_command(
    seed: Seed,
    *,
    token: str = "",
    urgency: Urgency = Urgency.NORMAL,
    house_id: UUID | None = None,
    location: str = "厨房水槽下方",
    attachment_ids: tuple[UUID, ...] = (),
) -> CreateWorkOrderCommand:
    return CreateWorkOrderCommand(
        house_id=house_id or seed.house,
        category=RepairCategory.WATER_PLUMBING,
        location=location,
        description="下水管接口渗水，地面已积水。",
        urgency=urgency,
        confirmation_token=token,
        attachment_ids=attachment_ids,
    )


def command_hash(command: CreateWorkOrderCommand) -> str:
    payload = asdict(command)
    payload.pop("confirmation_token")
    return canonical_hash(payload)


def mint_token(
    sessions: sessionmaker[Session],
    *,
    actor_id: UUID,
    action: str,
    parameter_hash: str,
) -> str:
    """Insert a confirmation token bound to a pre-computed parameter hash."""
    from datetime import datetime, timedelta, timezone

    from property_agent.platform.infrastructure.orm_models import ConfirmationTokenModel

    token = f"tok_{uuid4().hex}"
    with sessions() as session:
        session.add(
            ConfirmationTokenModel(
                token=token,
                actor_id=actor_id,
                action=action,
                parameter_hash=parameter_hash,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
        )
        session.commit()
    return token


def confirmed_command(
    sessions: sessionmaker[Session],
    seed: Seed,
    *,
    actor_id: UUID | None = None,
    action: str = "CREATE_WORK_ORDER",
    **kwargs,
) -> CreateWorkOrderCommand:
    """Build a create command carrying a valid confirmation token."""
    draft = make_command(seed, **kwargs)
    token = mint_token(
        sessions,
        actor_id=actor_id or seed.resident,
        action=action,
        parameter_hash=command_hash(draft),
    )
    return make_command(seed, token=token, **kwargs)


def add_attachment(
    sessions: sessionmaker[Session],
    *,
    community_id: UUID,
    uploader_id: UUID,
    status: str = "UPLOADED",
    content_type: str = "image/jpeg",
    size_bytes: int = 2048,
) -> UUID:
    attachment_id = uuid4()
    with sessions() as session:
        session.add(
            AttachmentModel(
                id=attachment_id,
                community_id=community_id,
                uploader_id=uploader_id,
                file_name="leak.jpg",
                content_type=content_type,
                size_bytes=size_bytes,
                status=status,
                storage_key=f"repair/{attachment_id}",
                business_type="REPAIR",
            )
        )
        session.commit()
    return attachment_id


def count(sessions: sessionmaker[Session], model) -> int:
    with sessions() as session:
        return session.execute(select(func.count()).select_from(model)).scalar_one()


# ═══════════════════════════════════════════════════════════════
# Happy path — one commit writes order + timeline + audit + idempotency
# ═══════════════════════════════════════════════════════════════


def test_create_persists_order_timeline_audit_and_idempotency(
    sessions, seed, service
) -> None:
    command = confirmed_command(sessions, seed)

    work_order = service.create(command, resident_ctx(seed), idempotency_key="k-create")

    assert work_order.status == WorkOrderStatus.PENDING_ASSIGNMENT
    assert work_order.business_no.startswith("WX-")

    with sessions() as session:
        stored = session.get(WorkOrderModel, work_order.id)
        assert stored is not None
        assert stored.community_id == seed.community
        assert stored.reporter_id == seed.resident

        audit = session.execute(select(AuditLogModel)).scalars().all()
        assert [row.action for row in audit] == ["REPAIR_CREATE"]
        assert audit[0].request_id == "req_prod"
        assert audit[0].result == "SUCCESS"

        record = session.execute(select(IdempotencyRecordModel)).scalar_one()
        assert record.operation == "CREATE_WORK_ORDER"
        assert record.resource_id == str(work_order.id)
        assert record.response_snapshot["status"] == WorkOrderStatus.PENDING_ASSIGNMENT

    timeline = service.timeline(work_order.id, resident_ctx(seed))
    assert [entry.action for entry in timeline] == [ActionCode.CREATE]


def test_replay_returns_first_result_without_second_row(sessions, seed, service) -> None:
    command = confirmed_command(sessions, seed)
    first = service.create(command, resident_ctx(seed), idempotency_key="k-replay")

    # The token is single-use; a genuine retry replays before touching it.
    second = service.create(command, resident_ctx(seed), idempotency_key="k-replay")

    assert second.id == first.id
    assert second.business_no == first.business_no
    assert count(sessions, WorkOrderModel) == 1
    assert count(sessions, IdempotencyRecordModel) == 1


def test_same_key_different_payload_is_a_conflict(sessions, seed, service) -> None:
    service.create(
        confirmed_command(sessions, seed), resident_ctx(seed), idempotency_key="k-conflict"
    )

    changed = confirmed_command(sessions, seed, location="卫生间")
    with pytest.raises(BusinessError) as excinfo:
        service.create(changed, resident_ctx(seed), idempotency_key="k-conflict")

    assert excinfo.value.code == "IDEMPOTENCY_CONFLICT"
    assert count(sessions, WorkOrderModel) == 1


# ═══════════════════════════════════════════════════════════════
# ConfirmationPort
# ═══════════════════════════════════════════════════════════════


def test_unknown_token_is_rejected_and_writes_nothing(sessions, seed, service) -> None:
    command = make_command(seed, token="not-a-real-token")

    with pytest.raises(BusinessError) as excinfo:
        service.create(command, resident_ctx(seed), idempotency_key="k-bad-token")

    assert excinfo.value.code == "CONFIRMATION_INVALID"
    assert count(sessions, WorkOrderModel) == 0
    assert count(sessions, IdempotencyRecordModel) == 0


def test_token_cannot_be_reused_for_a_second_order(sessions, seed, service) -> None:
    command = confirmed_command(sessions, seed)
    service.create(command, resident_ctx(seed), idempotency_key="k-first")

    with pytest.raises(BusinessError) as excinfo:
        service.create(command, resident_ctx(seed), idempotency_key="k-second")

    assert excinfo.value.code == "CONFIRMATION_INVALID"
    assert count(sessions, WorkOrderModel) == 1


def test_token_stops_matching_when_a_parameter_changes(sessions, seed, service) -> None:
    """PRD 12.2: 参数变化后旧确认令牌不可使用."""
    original = confirmed_command(sessions, seed, location="厨房")
    tampered = make_command(seed, token=original.confirmation_token, location="主卧")

    with pytest.raises(BusinessError) as excinfo:
        service.create(tampered, resident_ctx(seed), idempotency_key="k-tamper")

    assert excinfo.value.code == "CONFIRMATION_INVALID"
    assert count(sessions, WorkOrderModel) == 0


def test_token_minted_by_confirmation_service_is_accepted(sessions, seed, service) -> None:
    """PRD 12.2: one canonical hash across platform and business modules.

    The token is generated the way ``POST /api/confirmations`` does — from a
    raw parameter dict — and must be accepted by the repair service, which
    derives its hash from a parsed dataclass command.
    """
    draft = make_command(seed)
    params = asdict(draft)
    params.pop("confirmation_token")

    with sessions() as session:
        token = ConfirmationService(session).generate_token(
            actor_id=seed.resident, action="CREATE_WORK_ORDER", params=params
        )
        session.commit()

    command = make_command(seed, token=token)
    work_order = service.create(
        command, resident_ctx(seed), idempotency_key="k-platform-token"
    )

    assert work_order.status == WorkOrderStatus.PENDING_ASSIGNMENT
    assert canonical_hash(params) == command_hash(command)


# ═══════════════════════════════════════════════════════════════
# HouseAccessPort
# ═══════════════════════════════════════════════════════════════


def test_resident_without_binding_is_forbidden(sessions, seed, service) -> None:
    command = confirmed_command(sessions, seed, house_id=seed.unbound_house)

    with pytest.raises(BusinessError) as excinfo:
        service.create(command, resident_ctx(seed), idempotency_key="k-unbound")

    assert excinfo.value.code == "FORBIDDEN"
    assert count(sessions, WorkOrderModel) == 0


def test_house_from_another_community_is_forbidden(sessions, seed, service) -> None:
    command = confirmed_command(sessions, seed, house_id=seed.foreign_house)

    with pytest.raises(BusinessError) as excinfo:
        service.create(command, resident_ctx(seed), idempotency_key="k-foreign")

    assert excinfo.value.code == "FORBIDDEN"


def test_customer_service_may_report_for_any_house_in_the_community(
    sessions, seed, service
) -> None:
    command = confirmed_command(
        sessions,
        seed,
        actor_id=seed.customer_service,
        house_id=seed.unbound_house,
    )

    work_order = service.create(
        command, staff_ctx(seed), idempotency_key="k-staff-create"
    )

    assert work_order.house_id == seed.unbound_house
    assert work_order.reporter_id == seed.customer_service


def test_revoked_role_loses_community_wide_access(sessions, seed, service) -> None:
    """Roles are re-read from the database, not trusted from the token."""
    with sessions() as session:
        session.execute(
            UserRoleModel.__table__.delete().where(
                UserRoleModel.user_id == seed.customer_service
            )
        )
        session.commit()

    command = confirmed_command(
        sessions, seed, actor_id=seed.customer_service, house_id=seed.unbound_house
    )
    with pytest.raises(BusinessError) as excinfo:
        service.create(command, staff_ctx(seed), idempotency_key="k-revoked")

    assert excinfo.value.code == "FORBIDDEN"


# ═══════════════════════════════════════════════════════════════
# AttachmentPort
# ═══════════════════════════════════════════════════════════════


def test_valid_attachment_is_accepted(sessions, seed, service) -> None:
    attachment = add_attachment(
        sessions, community_id=seed.community, uploader_id=seed.resident
    )
    command = confirmed_command(sessions, seed, attachment_ids=(attachment,))

    work_order = service.create(
        command, resident_ctx(seed), idempotency_key="k-attach-ok"
    )

    assert work_order.id is not None


@pytest.mark.parametrize(
    ("kwargs", "expected_code"),
    [
        ({"status": "UPLOADING"}, "VALIDATION_ERROR"),
        ({"content_type": "application/x-msdownload"}, "VALIDATION_ERROR"),
        ({"size_bytes": ATTACHMENT_MAX_SIZE_BYTES + 1}, "VALIDATION_ERROR"),
    ],
)
def test_attachment_metadata_is_validated(
    sessions, seed, service, kwargs, expected_code
) -> None:
    attachment = add_attachment(
        sessions, community_id=seed.community, uploader_id=seed.resident, **kwargs
    )
    command = confirmed_command(sessions, seed, attachment_ids=(attachment,))

    with pytest.raises(BusinessError) as excinfo:
        service.create(command, resident_ctx(seed), idempotency_key="k-attach-bad")

    assert excinfo.value.code == expected_code
    assert count(sessions, WorkOrderModel) == 0


def test_attachment_owned_by_someone_else_is_forbidden(sessions, seed, service) -> None:
    attachment = add_attachment(
        sessions, community_id=seed.community, uploader_id=seed.other_resident
    )
    command = confirmed_command(sessions, seed, attachment_ids=(attachment,))

    with pytest.raises(BusinessError) as excinfo:
        service.create(command, resident_ctx(seed), idempotency_key="k-attach-owner")

    assert excinfo.value.code == "FORBIDDEN"


def test_attachment_from_another_community_is_forbidden(sessions, seed, service) -> None:
    attachment = add_attachment(
        sessions, community_id=seed.other_community, uploader_id=seed.resident
    )
    command = confirmed_command(sessions, seed, attachment_ids=(attachment,))

    with pytest.raises(BusinessError) as excinfo:
        service.create(command, resident_ctx(seed), idempotency_key="k-attach-scope")

    assert excinfo.value.code == "FORBIDDEN"


def test_unknown_attachment_id_is_rejected(sessions, seed, service) -> None:
    command = confirmed_command(sessions, seed, attachment_ids=(uuid4(),))

    with pytest.raises(BusinessError) as excinfo:
        service.create(command, resident_ctx(seed), idempotency_key="k-attach-missing")

    assert excinfo.value.code == "VALIDATION_ERROR"


# ═══════════════════════════════════════════════════════════════
# High-risk manual handover (PRD 6.1)
# ═══════════════════════════════════════════════════════════════


def test_high_risk_creates_handover_ticket_and_notifies_duty_staff(
    sessions, seed, service
) -> None:
    command = confirmed_command(
        sessions,
        seed,
        action="CREATE_WORK_ORDER_HANDOVER",
        urgency=Urgency.HIGH_RISK,
        location="地下车库配电房",
    )

    with pytest.raises(BusinessError) as excinfo:
        service.create(command, resident_ctx(seed), idempotency_key="k-high-risk")

    error = excinfo.value
    assert error.code == "HANDOVER_REQUIRED"
    assert error.status_code == 422
    ticket_id = UUID(str(error.details["handover_ticket_id"]))
    assert error.details["notified_staff"] == 2  # customer service + manager

    # No ordinary work order is created for a high-risk report.
    assert count(sessions, WorkOrderModel) == 0

    with sessions() as session:
        ticket = session.get(HandoverTicketModel, ticket_id)
        assert ticket is not None
        assert ticket.source == "REPAIR"
        assert ticket.queue == "CUSTOMER_SERVICE"
        assert ticket.reason == "HIGH_RISK"
        assert ticket.status == "PENDING"
        assert ticket.community_id == seed.community
        assert ticket.requester_id == seed.resident
        assert ticket.request_id == "req_prod"
        assert ticket.payload["location"] == "地下车库配电房"

        messages = session.execute(select(MessageRecordModel)).scalars().all()
        assert {msg.receiver_id for msg in messages} == {
            seed.customer_service,
            seed.manager,
        }
        assert all(msg.business_type == "REPAIR" for msg in messages)
        assert all(msg.resource_id == str(ticket_id) for msg in messages)
        assert all(msg.status == "PENDING" for msg in messages)

        audit = session.execute(select(AuditLogModel)).scalar_one()
        assert audit.action == "REPAIR_HIGH_RISK_HANDOVER"
        assert audit.resource_type == "HANDOVER_TICKET"


def test_high_risk_retry_replays_the_same_ticket(sessions, seed, service) -> None:
    command = confirmed_command(
        sessions, seed, action="CREATE_WORK_ORDER_HANDOVER", urgency=Urgency.HIGH_RISK
    )

    with pytest.raises(BusinessError) as first:
        service.create(command, resident_ctx(seed), idempotency_key="k-hr-retry")
    with pytest.raises(BusinessError) as second:
        service.create(command, resident_ctx(seed), idempotency_key="k-hr-retry")

    assert (
        first.value.details["handover_ticket_id"]
        == second.value.details["handover_ticket_id"]
    )
    assert count(sessions, HandoverTicketModel) == 1
    assert count(sessions, MessageRecordModel) == 2


def test_high_risk_failure_creates_no_ticket(sessions, seed, service) -> None:
    """PRD 6.1: 接口失败不生成虚假单号 — a rejected request leaves no trace."""
    command = make_command(
        seed, token="invalid", urgency=Urgency.HIGH_RISK, house_id=seed.unbound_house
    )

    with pytest.raises(BusinessError) as excinfo:
        service.create(command, resident_ctx(seed), idempotency_key="k-hr-fail")

    assert excinfo.value.code == "FORBIDDEN"
    assert count(sessions, HandoverTicketModel) == 0
    assert count(sessions, MessageRecordModel) == 0
    assert count(sessions, IdempotencyRecordModel) == 0


# ═══════════════════════════════════════════════════════════════
# StaffDirectoryPort + MessagePort on state change
# ═══════════════════════════════════════════════════════════════


def _create_order(sessions, seed, service, key: str = "k-flow"):
    return service.create(
        confirmed_command(sessions, seed), resident_ctx(seed), idempotency_key=key
    )


def test_assign_notifies_the_worker_and_records_the_transition(
    sessions, seed, service
) -> None:
    work_order = _create_order(sessions, seed, service)

    assigned = service.execute_action(
        work_order.id,
        ExecuteActionCommand(
            action=ActionCode.ASSIGN,
            expected_version=work_order.version,
            assignee_id=seed.repair_worker,
        ),
        staff_ctx(seed),
        idempotency_key="k-assign",
    )

    assert assigned.status == WorkOrderStatus.PENDING_ACCEPTANCE
    assert assigned.assignee_id == seed.repair_worker

    with sessions() as session:
        message = session.execute(select(MessageRecordModel)).scalar_one()
        assert message.receiver_id == seed.repair_worker
        assert message.business_type == "REPAIR"
        assert message.resource_id == str(work_order.id)

        actions = session.execute(select(AuditLogModel.action)).scalars().all()
        assert set(actions) == {"REPAIR_CREATE", "REPAIR_ASSIGN"}

    timeline = service.timeline(work_order.id, staff_ctx(seed))
    assert [entry.action for entry in timeline] == [ActionCode.CREATE, ActionCode.ASSIGN]


def test_assigning_a_non_worker_is_rejected(sessions, seed, service) -> None:
    work_order = _create_order(sessions, seed, service, key="k-flow-2")

    with pytest.raises(BusinessError) as excinfo:
        service.execute_action(
            work_order.id,
            ExecuteActionCommand(
                action=ActionCode.ASSIGN,
                expected_version=work_order.version,
                assignee_id=seed.other_resident,
            ),
            staff_ctx(seed),
            idempotency_key="k-assign-bad",
        )

    assert excinfo.value.code == "VALIDATION_ERROR"
    assert count(sessions, MessageRecordModel) == 0
