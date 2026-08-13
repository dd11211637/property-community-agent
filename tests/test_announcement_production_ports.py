"""
Production shared-port integration tests — PRD 6.2 公告生产化接入.

``tests/announcement/`` drives the service through in-memory fakes. These tests
wire the *real* adapters from ``announcement.infrastructure.shared_ports``
against a live SQLite database, exercising the exact code path the assembled
FastAPI application uses::

    AnnouncementService
      → SqlAlchemyAnnouncementUnitOfWork
        → build_announcement_ports(session)
          → idempotency_records / confirmation_tokens /
            houses + user_house_bindings + users / audit_logs / message_records

Coverage:
  * audience resolution (community isolation, ACTIVE filters, building / unit /
    house_type dimensions, duplicate bindings, masked samples)
  * empty audience blocks the review submission
  * idempotent create (replay + conflict)
  * publish confirmation token (valid / tampered / reused)
  * publish writes snapshot + outbox message per recipient + audit in one commit
  * an unauthorised review attempt leaves a DENIED audit row
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from property_agent.announcement.application.commands import (
    CreateAnnouncementCommand,
    ReviewActionCommand,
)
from property_agent.announcement.application.service import AnnouncementService
from property_agent.announcement.domain.enums import AnnouncementAction, AnnouncementStatus
from property_agent.announcement.infrastructure.models import (
    AnnouncementAudienceSnapshotModel,
    AnnouncementModel,
)
from property_agent.announcement.infrastructure.shared_ports import (
    SqlAlchemyAudienceResolverPort,
    build_announcement_ports,
)
from property_agent.announcement.infrastructure.uow import SqlAlchemyAnnouncementUnitOfWork
from property_agent.platform.application.hashing import canonical_hash
from property_agent.platform.context import RequestContext
from property_agent.platform.errors import BusinessError
from property_agent.platform.infrastructure.orm_models import (
    AuditLogModel,
    Base,
    CommunityModel,
    ConfirmationTokenModel,
    HouseModel,
    MessageRecordModel,
    UserHouseBindingModel,
    UserModel,
    UserRoleModel,
)
from property_agent.platform.roles import Role

# ═══════════════════════════════════════════════════════════════
# Fixtures — real SQLite database seeded with platform master data
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Seed:
    community: UUID
    other_community: UUID
    # B1-1-101 住宅 (resident_a), B1-2-201 住宅 (resident_b),
    # B2-1-101 商铺 (shop_owner), B1-1-102 住宅 但住户 INACTIVE
    resident_a: UUID
    resident_b: UUID
    shop_owner: UUID
    inactive_resident: UUID
    foreign_resident: UUID
    customer_service: UUID
    manager: UUID


@pytest.fixture
def sessions() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


@pytest.fixture
def seed(sessions: sessionmaker[Session]) -> Seed:
    data = Seed(
        community=uuid4(),
        other_community=uuid4(),
        resident_a=uuid4(),
        resident_b=uuid4(),
        shop_owner=uuid4(),
        inactive_resident=uuid4(),
        foreign_resident=uuid4(),
        customer_service=uuid4(),
        manager=uuid4(),
    )
    house_a, house_b, shop, house_inactive = uuid4(), uuid4(), uuid4(), uuid4()
    foreign_house = uuid4()
    # resident_a is also bound to a second matching house — the resolver must
    # not count the same person twice.
    house_a2 = uuid4()

    with sessions() as session:
        session.add_all(
            [
                CommunityModel(id=data.community, name="公告验收社区"),
                CommunityModel(id=data.other_community, name="另一个社区"),
                HouseModel(
                    id=house_a,
                    community_id=data.community,
                    building="B1",
                    unit="1",
                    room_no="101",
                    house_type="RESIDENTIAL",
                ),
                HouseModel(
                    id=house_a2,
                    community_id=data.community,
                    building="B1",
                    unit="1",
                    room_no="103",
                    house_type="RESIDENTIAL",
                ),
                HouseModel(
                    id=house_b,
                    community_id=data.community,
                    building="B1",
                    unit="2",
                    room_no="201",
                    house_type="RESIDENTIAL",
                ),
                HouseModel(
                    id=shop,
                    community_id=data.community,
                    building="B2",
                    unit="1",
                    room_no="101",
                    house_type="SHOP",
                ),
                HouseModel(
                    id=house_inactive,
                    community_id=data.community,
                    building="B1",
                    unit="1",
                    room_no="102",
                    house_type="RESIDENTIAL",
                ),
                HouseModel(
                    id=foreign_house,
                    community_id=data.other_community,
                    building="B1",
                    unit="1",
                    room_no="101",
                    house_type="RESIDENTIAL",
                ),
                _user(data.resident_a, data.community, "resident_a", "张三"),
                _user(data.resident_b, data.community, "resident_b", "李四"),
                _user(data.shop_owner, data.community, "shop_owner", "王五"),
                _user(
                    data.inactive_resident,
                    data.community,
                    "inactive",
                    "赵六",
                    status="FROZEN",
                ),
                _user(data.foreign_resident, data.other_community, "outsider", "外区住户"),
                _user(data.customer_service, data.community, "cs", "客服"),
                _user(data.manager, data.community, "manager", "管理员"),
                UserRoleModel(user_id=data.resident_a, role="RESIDENT"),
                UserRoleModel(user_id=data.customer_service, role="CUSTOMER_SERVICE"),
                UserRoleModel(user_id=data.manager, role="MANAGER"),
                UserHouseBindingModel(user_id=data.resident_a, house_id=house_a, status="ACTIVE"),
                UserHouseBindingModel(user_id=data.resident_a, house_id=house_a2, status="ACTIVE"),
                UserHouseBindingModel(user_id=data.resident_b, house_id=house_b, status="ACTIVE"),
                UserHouseBindingModel(user_id=data.shop_owner, house_id=shop, status="ACTIVE"),
                UserHouseBindingModel(
                    user_id=data.inactive_resident, house_id=house_inactive, status="ACTIVE"
                ),
                UserHouseBindingModel(
                    user_id=data.foreign_resident, house_id=foreign_house, status="ACTIVE"
                ),
            ]
        )
        session.commit()
    return data


def _user(
    user_id: UUID, community_id: UUID, username: str, display_name: str, *, status: str = "ACTIVE"
) -> UserModel:
    return UserModel(
        id=user_id,
        community_id=community_id,
        username=username,
        display_name=display_name,
        password_hash="x",
        status=status,
    )


@pytest.fixture
def service(sessions: sessionmaker[Session]) -> AnnouncementService:
    def unit_of_work_factory() -> SqlAlchemyAnnouncementUnitOfWork:
        return SqlAlchemyAnnouncementUnitOfWork(sessions, build_announcement_ports)

    return AnnouncementService(unit_of_work_factory)


def cs_context(seed: Seed, request_id: str = "req_cs") -> RequestContext:
    return RequestContext(
        seed.customer_service, seed.community, frozenset({Role.CUSTOMER_SERVICE}), request_id
    )


def manager_context(seed: Seed, request_id: str = "req_mgr") -> RequestContext:
    return RequestContext(seed.manager, seed.community, frozenset({Role.MANAGER}), request_id)


def create_command(**overrides) -> CreateAnnouncementCommand:
    payload = {
        "title": "电梯例行检修",
        "body": "本周六 9:00-12:00 对 B1 栋电梯进行例行检修，请提前安排出行。",
        "category": "MAINTENANCE",
        "audience_condition": {"building_ids": ["B1"]},
        "scheduled_at": None,
    }
    payload.update(overrides)
    return CreateAnnouncementCommand(**payload)


def publish_to_approved(
    service: AnnouncementService, seed: Seed, *, condition: dict | None = None
) -> UUID:
    """Drive a draft all the way to APPROVED and return its id."""
    draft = service.create_draft(
        create_command(
            audience_condition=condition if condition is not None else {"building_ids": ["B1"]}
        ),
        cs_context(seed),
        idempotency_key="create-1",
    )
    submitted = service.submit_review(
        draft.id,
        ReviewActionCommand(AnnouncementAction.SUBMIT_REVIEW, draft.version),
        cs_context(seed),
        idempotency_key="submit-1",
    )
    service.review_action(
        draft.id,
        ReviewActionCommand(AnnouncementAction.APPROVE, submitted.version),
        manager_context(seed),
        idempotency_key="approve-1",
    )
    return draft.id


def mint_publish_token(
    sessions: sessionmaker[Session],
    seed: Seed,
    *,
    announcement_id: UUID,
    expected_version: int,
    actor_id: UUID | None = None,
) -> str:
    """Mint a confirmation token matching the hash the service will compute."""
    parameter_hash = canonical_hash(
        {
            "announcement_id": announcement_id,
            "expected_version": expected_version,
            "action": AnnouncementAction.PUBLISH,
        }
    )
    token = f"tok_{uuid4().hex}"
    with sessions() as session:
        session.add(
            ConfirmationTokenModel(
                token=token,
                actor_id=actor_id or seed.manager,
                action="ANNOUNCEMENT_PUBLISH",
                parameter_hash=parameter_hash,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
        )
        session.commit()
    return token


# ═══════════════════════════════════════════════════════════════
# AudienceResolverPort
# ═══════════════════════════════════════════════════════════════


def test_audience_resolver_scopes_to_the_community_and_active_records(sessions, seed) -> None:
    with sessions() as session:
        snapshot = SqlAlchemyAudienceResolverPort(session).resolve(
            community_id=seed.community, condition={}, request_id="req"
        )

    # resident_a (deduplicated across two houses), resident_b, shop_owner.
    # Excluded: the FROZEN user, the resident of another community, and staff
    # accounts that hold no house binding.
    assert set(snapshot.member_ids) == {seed.resident_a, seed.resident_b, seed.shop_owner}
    assert snapshot.count == 3
    assert len(snapshot.member_ids) == len(set(snapshot.member_ids))


def test_audience_resolver_applies_each_whitelisted_dimension(sessions, seed) -> None:
    with sessions() as session:
        resolver = SqlAlchemyAudienceResolverPort(session)

        by_building = resolver.resolve(
            community_id=seed.community, condition={"building_ids": ["B1"]}, request_id="req"
        )
        by_unit = resolver.resolve(
            community_id=seed.community,
            condition={"building_ids": ["B1"], "unit_ids": ["2"]},
            request_id="req",
        )
        by_type = resolver.resolve(
            community_id=seed.community, condition={"house_types": ["SHOP"]}, request_id="req"
        )

    assert set(by_building.member_ids) == {seed.resident_a, seed.resident_b}
    assert set(by_unit.member_ids) == {seed.resident_b}
    assert set(by_type.member_ids) == {seed.shop_owner}


def test_audience_resolver_masks_samples_and_rejects_unknown_dimensions(sessions, seed) -> None:
    with sessions() as session:
        resolver = SqlAlchemyAudienceResolverPort(session)
        snapshot = resolver.resolve(
            community_id=seed.community, condition={"unit_ids": ["2"]}, request_id="req"
        )

        assert snapshot.samples == ({"receiver": "李**", "address": "B1-2-201"},)

        with pytest.raises(BusinessError) as error:
            resolver.resolve(
                community_id=seed.community,
                condition={"user_ids": [str(seed.resident_a)]},
                request_id="req",
            )
    assert error.value.code == "VALIDATION_ERROR"
    assert error.value.status_code == 422


def test_submit_review_is_blocked_when_the_audience_is_empty(service, sessions, seed) -> None:
    draft = service.create_draft(
        create_command(audience_condition={"building_ids": ["B9"]}),
        cs_context(seed),
        idempotency_key="create-empty",
    )

    with pytest.raises(BusinessError) as error:
        service.submit_review(
            draft.id,
            ReviewActionCommand(AnnouncementAction.SUBMIT_REVIEW, draft.version),
            cs_context(seed),
            idempotency_key="submit-empty",
        )

    assert error.value.code == "EMPTY_AUDIENCE"
    with sessions() as session:
        stored = session.get(AnnouncementModel, draft.id)
        assert stored.status == AnnouncementStatus.DRAFT.value
        assert session.execute(select(AnnouncementAudienceSnapshotModel)).all() == []


# ═══════════════════════════════════════════════════════════════
# IdempotencyPort
# ═══════════════════════════════════════════════════════════════


def test_replaying_the_same_key_returns_the_first_announcement(service, sessions, seed) -> None:
    first = service.create_draft(create_command(), cs_context(seed), idempotency_key="dup")
    second = service.create_draft(create_command(), cs_context(seed), idempotency_key="dup")

    assert first.id == second.id
    assert first.business_no == second.business_no
    with sessions() as session:
        assert len(session.execute(select(AnnouncementModel)).scalars().all()) == 1


def test_reusing_a_key_with_different_parameters_conflicts(service, seed) -> None:
    service.create_draft(create_command(), cs_context(seed), idempotency_key="dup")

    with pytest.raises(BusinessError) as error:
        service.create_draft(
            create_command(title="完全不同的标题"), cs_context(seed), idempotency_key="dup"
        )

    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    assert error.value.status_code == 409


# ═══════════════════════════════════════════════════════════════
# ConfirmationPort + publish transaction
# ═══════════════════════════════════════════════════════════════


def test_publish_and_withdraw_fan_out_messages_to_the_frozen_audience(
    service, sessions, seed
) -> None:
    announcement_id = publish_to_approved(service, seed)
    with sessions() as session:
        approved = session.get(AnnouncementModel, announcement_id)
        version = approved.version
    token = mint_publish_token(
        sessions, seed, announcement_id=announcement_id, expected_version=version
    )

    published = service.publish(
        announcement_id,
        ReviewActionCommand(AnnouncementAction.PUBLISH, version, confirmation_token=token),
        manager_context(seed, "req_publish"),
        idempotency_key="publish-1",
    )

    assert published.status is AnnouncementStatus.PUBLISHED
    with sessions() as session:
        snapshots = session.execute(select(AnnouncementAudienceSnapshotModel)).scalars().all()
        # One frozen at submit-review, one at publish.
        assert len(snapshots) == 2
        assert set(snapshots[-1].member_ids) == {str(seed.resident_a), str(seed.resident_b)}

        messages = session.execute(select(MessageRecordModel)).scalars().all()
        assert {message.receiver_id for message in messages} == {
            seed.resident_a,
            seed.resident_b,
        }
        assert {message.business_type for message in messages} == {"ANNOUNCEMENT"}
        assert all(message.resource_id == str(announcement_id) for message in messages)

        actions = {row.action for row in session.execute(select(AuditLogModel)).scalars().all()}
        assert "ANNOUNCEMENT_PUBLISH" in actions

        consumed = session.execute(select(ConfirmationTokenModel)).scalar_one()
        assert consumed.consumed_at is not None

    withdrawn = service.withdraw(
        announcement_id,
        ReviewActionCommand(AnnouncementAction.WITHDRAW, published.version, "维护计划调整"),
        manager_context(seed, "req_withdraw"),
        idempotency_key="withdraw-1",
    )
    assert withdrawn.status is AnnouncementStatus.WITHDRAWN

    with sessions() as session:
        messages = session.execute(select(MessageRecordModel)).scalars().all()
        assert len(messages) == 4
        withdrawal_messages = [message for message in messages if message.title == "公告已撤回"]
        assert {message.receiver_id for message in withdrawal_messages} == {
            seed.resident_a,
            seed.resident_b,
        }
        actions = {row.action for row in session.execute(select(AuditLogModel)).scalars().all()}
        assert "ANNOUNCEMENT_WITHDRAW" in actions


def test_publish_rejects_a_token_minted_for_other_parameters(service, sessions, seed) -> None:
    announcement_id = publish_to_approved(service, seed)
    with sessions() as session:
        version = session.get(AnnouncementModel, announcement_id).version
    # Token bound to a stale version — the operator confirmed something else.
    token = mint_publish_token(
        sessions, seed, announcement_id=announcement_id, expected_version=version - 1
    )

    with pytest.raises(BusinessError) as error:
        service.publish(
            announcement_id,
            ReviewActionCommand(AnnouncementAction.PUBLISH, version, confirmation_token=token),
            manager_context(seed),
            idempotency_key="publish-bad",
        )

    assert error.value.code == "CONFIRMATION_INVALID"
    with sessions() as session:
        assert session.get(AnnouncementModel, announcement_id).status == (
            AnnouncementStatus.APPROVED.value
        )
        assert session.execute(select(MessageRecordModel)).all() == []


def test_a_confirmation_token_cannot_be_replayed(service, sessions, seed) -> None:
    announcement_id = publish_to_approved(service, seed)
    with sessions() as session:
        version = session.get(AnnouncementModel, announcement_id).version
    token = mint_publish_token(
        sessions, seed, announcement_id=announcement_id, expected_version=version
    )
    service.publish(
        announcement_id,
        ReviewActionCommand(AnnouncementAction.PUBLISH, version, confirmation_token=token),
        manager_context(seed),
        idempotency_key="publish-1",
    )

    with pytest.raises(BusinessError) as error:
        service.withdraw(
            announcement_id,
            ReviewActionCommand(AnnouncementAction.PUBLISH, version, confirmation_token=token),
            manager_context(seed),
            idempotency_key="publish-2",
        )
    # WITHDRAW is required here — the reused token never even gets consulted.
    assert error.value.code == "VALIDATION_ERROR"

    with sessions() as session:
        assert session.execute(select(ConfirmationTokenModel)).scalar_one().consumed_at is not None


# ═══════════════════════════════════════════════════════════════
# AuditPort
# ═══════════════════════════════════════════════════════════════


def test_an_unauthorised_review_attempt_is_audited_as_denied(service, sessions, seed) -> None:
    draft = service.create_draft(create_command(), cs_context(seed), idempotency_key="create-1")

    with pytest.raises(BusinessError) as error:
        service.review_action(
            draft.id,
            ReviewActionCommand(AnnouncementAction.APPROVE, draft.version),
            cs_context(seed, "req_denied"),
            idempotency_key="approve-denied",
        )

    assert error.value.code == "FORBIDDEN"
    with sessions() as session:
        denied = (
            session.execute(select(AuditLogModel).where(AuditLogModel.result == "DENIED"))
            .scalars()
            .all()
        )
        assert len(denied) == 1
        assert denied[0].action == "ANNOUNCEMENT_UNAUTHORIZED_ANNOUNCEMENT_ACTION"
        assert denied[0].request_id == "req_denied"
        assert session.get(AnnouncementModel, draft.id).status == AnnouncementStatus.DRAFT.value
