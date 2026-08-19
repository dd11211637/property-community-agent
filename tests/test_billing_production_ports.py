"""
Production shared-port integration tests — PRD 6.3 账单与财务咨询.

Mirrors ``tests/test_announcement_production_ports.py``: drive the real
``BillingService`` / ``ConsultationService`` against a live SQLite database.

Billing and platform tables share one application database. Tests use one in-memory
StaticPool engine so a single transaction covers business data, audit and idempotency.

Coverage:
  * 当前房屋必选（HOUSE_SELECTION_REQUIRED）
  * 账单按 社区 + 当前房屋 范围过滤（社区隔离 + 房屋隔离）
  * 规则查询：有效规则返回，无规则声明 unknown
  * 所有账单查询落审计（BILLING_BILL_QUERY）
  * 财务咨询单幂等创建
  * 咨询单状态机 DRAFT→SUBMITTED→PROCESSING→ANSWERED→RESOLVED
  * 申诉 APPEALED→PROCESSING
  * owner_only / staff_only 越权拦截
  * R-02：账单源不可用时仍保存咨询草稿
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from property_agent.billing.application.service import (
    BillingService,
    ConsultationService,
)
from property_agent.billing.domain.entities import ConsultationStatus
from property_agent.billing.errors import (
    BillingError,
    BillingSourceUnavailable,
    ConsultationError,
)
from property_agent.billing.infrastructure.orm_models import (
    BillingRuleModel,
    BillModel,
    ConsultationModel,
)
from property_agent.billing.infrastructure.unit_of_work import SqlAlchemyBillingUnitOfWork
from property_agent.platform.application.approval_service import ApprovalService
from property_agent.platform.context import RequestContext
from property_agent.platform.infrastructure.orm_models import (
    AuditLogModel,
    Base,
    CommunityModel,
)

COMMUNITY_CODE = "阳光花园"


class UnavailableBillingSourcePort:
    """Test fake for the R-02 source-outage path."""

    def list_bills(self, **_: object):
        raise BillingSourceUnavailable("账单数据源暂时不可用")

    def get_bill(self, *, bill_id: str):
        raise BillingSourceUnavailable("账单数据源暂时不可用")


class UnavailableBillingUnitOfWork(SqlAlchemyBillingUnitOfWork):
    """Test-only UoW variant that injects an unavailable external source."""

    def __init__(self, session: Session, approval_service: ApprovalService) -> None:
        super().__init__(session, approval_service)
        self.source = UnavailableBillingSourcePort()


class FailingAuditPort:
    def add(self, **_: object) -> None:
        raise RuntimeError("audit storage unavailable")


class FailingAuditUnitOfWork(SqlAlchemyBillingUnitOfWork):
    def __init__(self, session: Session, approval_service: ApprovalService) -> None:
        super().__init__(session, approval_service)
        self.audit = FailingAuditPort()


def _uow_factory(sessions: sessionmaker[Session]):
    """构造 (transaction) -> SqlAlchemyBillingUnitOfWork 工厂。

    ``SqlAlchemyBillingUnitOfWork`` 引入 P0 审批原子化后必须显式注入
    ``ApprovalService``；本测试里把 ApprovalService 绑到与业务同一引擎上即可。
    """

    def factory(transaction):
        return SqlAlchemyBillingUnitOfWork(transaction, ApprovalService(sessions))

    return factory


@pytest.fixture
def sessions():
    # 单一 in-memory 引擎，platform 与 billing 共用（同 Base.metadata）。
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)

    return factory


@pytest.fixture
def seed(sessions: sessionmaker[Session]):
    community_id = uuid4()
    other_community_id = uuid4()
    current_house = uuid4()
    another_house = uuid4()  # 同社区、不同房屋
    other_house = uuid4()  # 另一社区
    resident = uuid4()
    staff = uuid4()
    other_resident = uuid4()

    with sessions() as db:
        db.add_all(
            [
                CommunityModel(id=community_id, name=COMMUNITY_CODE),
                CommunityModel(id=other_community_id, name="其他社区"),
            ]
        )
        db.commit()

    # 账单库（与 platform 同一引擎）
    with sessions() as bdb:
        bdb.add_all(
            [
                BillModel(
                    bill_id="B001",
                    user_id="u1",
                    room_id="r1",
                    bill_period="2026-08",
                    property_fee=1200.0,
                    utility_fee=0,
                    parking_fee=0,
                    late_fee=0,
                    total_amount=1200.0,
                    due_date=date(2026, 8, 31),
                    status="UNPAID",
                    community_id=COMMUNITY_CODE,
                    house_id=str(current_house),
                    version=1,
                    fee_type="PROPERTY",
                ),
                BillModel(
                    bill_id="B002",
                    user_id="u2",
                    room_id="r2",
                    bill_period="2026-08",
                    property_fee=0,
                    utility_fee=300.0,
                    parking_fee=0,
                    late_fee=0,
                    total_amount=300.0,
                    due_date=date(2026, 8, 31),
                    status="UNPAID",
                    community_id=COMMUNITY_CODE,
                    house_id=str(another_house),
                    version=1,
                    fee_type="UTILITY",
                ),
                BillModel(
                    bill_id="B003",
                    user_id="u3",
                    room_id="r3",
                    bill_period="2026-08",
                    property_fee=900.0,
                    utility_fee=0,
                    parking_fee=0,
                    late_fee=0,
                    total_amount=900.0,
                    due_date=date(2026, 8, 31),
                    status="UNPAID",
                    community_id="其他社区",
                    house_id=str(other_house),
                    version=1,
                    fee_type="PROPERTY",
                ),
                BillModel(
                    bill_id="B004",
                    user_id="u1",
                    room_id="r1",
                    bill_period="2026-07",
                    property_fee=0,
                    utility_fee=88.0,
                    parking_fee=0,
                    late_fee=0,
                    total_amount=88.0,
                    due_date=date(2026, 7, 31),
                    status="UNPAID",
                    community_id=COMMUNITY_CODE,
                    house_id=str(current_house),
                    version=1,
                    fee_type="UTILITY",
                ),
                BillingRuleModel(
                    id="R001",
                    community_id=COMMUNITY_CODE,
                    fee_type="PROPERTY",
                    version="2026Q3",
                    name="住宅物业费口径",
                    parameters={"unit_price": 2.5},
                    valid_from=datetime(2026, 1, 1),
                    valid_until=None,
                ),
            ]
        )
        bdb.commit()

    return {
        "community_id": community_id,
        "other_community_id": other_community_id,
        "current_house": current_house,
        "another_house": another_house,
        "other_house_id": other_house,
        "resident": resident,
        "staff": staff,
        "other_resident": other_resident,
    }


def _ctx(seed, actor_field, *, with_house=True, roles=("RESIDENT",)):
    return RequestContext(
        actor_id=seed[actor_field],
        community_id=seed["community_id"],
        roles=frozenset(roles),
        request_id="req-test",
        current_house_id=seed["current_house"] if with_house else None,
        bound_house_ids=frozenset({seed["current_house"]}),
    )


def _make_consultation_approval(sessions, ctx, *, subject, description, bill_id=None):
    """为 consultation 测试生成 confirmation_token + approval_ref。

    与生产路径一致：先签发确认令牌（confirmations_records 表），再创建
    PENDING 审批（agent_action_approvals 表）。``params`` 必须与
    ``create_draft`` 实际传入的业务字段一致，否则审批消费时会判为
    ``APPROVAL_PARAMS_CHANGED``。
    """
    from property_agent.platform.application.confirmation_service import (
        ConfirmationService,
    )

    action = "CREATE_CONSULTATION"
    params = {"subject": subject, "description": description, "bill_id": bill_id}
    with sessions() as db:
        token = ConfirmationService(db).generate_token(
            actor_id=ctx.actor_id,
            action=action,
            params=params,
        )
        db.commit()
    approval = ApprovalService(sessions).create_pending(
        conversation_id="test-conv",
        actor_id=ctx.actor_id,
        action=action,
        params=params,
    )
    return token, str(approval.id)


# ── 账单查询 ─────────────────────────────────────────────


def test_list_bills_requires_current_house(sessions, seed):
    ctx = _ctx(seed, "resident", with_house=False)
    service = BillingService(_uow_factory(sessions))
    with sessions() as db:
        with pytest.raises(BillingError) as exc:
            service.list_bills(ctx, db)
    assert exc.value.code == "HOUSE_SELECTION_REQUIRED"


def test_list_bills_filters_by_house_and_community(sessions, seed):
    ctx = _ctx(seed, "resident")
    service = BillingService(_uow_factory(sessions))
    with sessions() as db:
        bills = service.list_bills(ctx, db)
    ids = {b.bill_id for b in bills}
    assert ids == {"B001", "B004"}, f"仅应返回当前房屋账单，实际: {ids}"
    # 跨社区 B003 与跨房屋 B002 均被排除


def test_get_bill_returns_rule_when_effective(sessions, seed):
    ctx = _ctx(seed, "resident")
    service = BillingService(_uow_factory(sessions))
    with sessions() as db:
        bill, rule = service.get_bill(ctx, db, "B001")
    assert bill.community_id == COMMUNITY_CODE
    assert rule is not None and rule.fee_type == "PROPERTY"
    assert rule.version == "2026Q3"


def test_get_bill_declares_unknown_when_no_rule(sessions, seed):
    ctx = _ctx(seed, "resident")
    service = BillingService(_uow_factory(sessions))
    with sessions() as db:
        bill, rule = service.get_bill(ctx, db, "B004")
    assert rule is None  # UTILITY 无规则 → 声明未知


def test_get_bill_hides_same_community_other_house(sessions, seed):
    ctx = _ctx(seed, "resident")
    with sessions() as db, pytest.raises(BillingError) as exc:
        BillingService(_uow_factory(sessions)).get_bill(ctx, db, "B002")
    assert exc.value.code == "BILL_NOT_FOUND"


def test_query_is_audited(sessions, seed):
    ctx = _ctx(seed, "resident")
    service = BillingService(_uow_factory(sessions))
    with sessions() as db:
        service.list_bills(ctx, db)
        audit = (
            db.query(AuditLogModel)
            .filter_by(action="BILLING_BILL_QUERY", actor_id=ctx.actor_id)
            .first()
        )
        assert audit is not None
        assert audit.resource_id == str(ctx.current_house_id)


# ── 财务咨询单 ───────────────────────────────────────────


def test_create_consultation_idempotent(sessions, seed):
    resident_ctx = _ctx(seed, "resident")
    service = ConsultationService(_uow_factory(sessions))
    token1, ref1 = _make_consultation_approval(
        sessions, resident_ctx, subject="物业费怎么算", description="详询"
    )
    with sessions() as db:
        t1 = service.create_draft(
            resident_ctx,
            db,
            subject="物业费怎么算",
            description="详询",
            idempotency_key="idem-1",
            confirmation_token=token1,
            approval_ref=ref1,
        )
        # 幂等回放也必须能消费同一审批：相同 (action, params_hash) 命中 open 审批。
        token2, ref2 = _make_consultation_approval(
            sessions, resident_ctx, subject="物业费怎么算", description="详询"
        )
        t2 = service.create_draft(
            resident_ctx,
            db,
            subject="物业费怎么算",
            description="详询",
            idempotency_key="idem-1",
            confirmation_token=token2,
            approval_ref=ref2,
        )
    assert t1.id == t2.id
    assert t1.status == ConsultationStatus.DRAFT


def test_create_consultation_rejects_idempotency_key_with_different_payload(sessions, seed):
    ctx = _ctx(seed, "resident")
    service = ConsultationService(_uow_factory(sessions))
    token1, ref1 = _make_consultation_approval(
        sessions, ctx, subject="原问题", description="原描述"
    )
    token2, ref2 = _make_consultation_approval(
        sessions, ctx, subject="新问题", description="新描述"
    )
    with sessions() as db:
        service.create_draft(
            ctx,
            db,
            subject="原问题",
            description="原描述",
            idempotency_key="idem-conflict",
            confirmation_token=token1,
            approval_ref=ref1,
        )
        with pytest.raises(ConsultationError) as exc:
            service.create_draft(
                ctx,
                db,
                subject="新问题",
                description="新描述",
                idempotency_key="idem-conflict",
                confirmation_token=token2,
                approval_ref=ref2,
            )
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"


def test_consultation_detail_is_private_between_residents(sessions, seed):
    owner_ctx = _ctx(seed, "resident")
    other_ctx = _ctx(seed, "other_resident")
    service = ConsultationService(_uow_factory(sessions))
    token, ref = _make_consultation_approval(
        sessions, owner_ctx, subject="仅本人可见", description="隐私内容"
    )
    with sessions() as db:
        ticket = service.create_draft(
            owner_ctx,
            db,
            subject="仅本人可见",
            description="隐私内容",
            idempotency_key="idem-private",
            confirmation_token=token,
            approval_ref=ref,
        )
        with pytest.raises(ConsultationError) as exc:
            service.get(other_ctx, db, ticket.id)
    assert exc.value.code == "CONSULTATION_FORBIDDEN"


def test_consultation_can_only_link_current_house_bill(sessions, seed):
    ctx = _ctx(seed, "resident")
    token, ref = _make_consultation_approval(
        sessions,
        ctx,
        subject="咨询别户账单",
        description="不应允许",
        bill_id="B002",
    )
    with sessions() as db, pytest.raises(BillingError) as exc:
        ConsultationService(_uow_factory(sessions)).create_draft(
            ctx,
            db,
            subject="咨询别户账单",
            description="不应允许",
            bill_id="B002",
            idempotency_key="idem-cross-house",
            confirmation_token=token,
            approval_ref=ref,
        )
    assert exc.value.code == "BILL_NOT_FOUND"


def test_consultation_lifecycle(sessions, seed):
    resident_ctx = _ctx(seed, "resident")
    staff_ctx = _ctx(seed, "staff", roles=("FINANCE",))
    service = ConsultationService(_uow_factory(sessions))
    token, ref = _make_consultation_approval(sessions, resident_ctx, subject="s", description="d")
    with sessions() as db:
        ticket = service.create_draft(
            resident_ctx,
            db,
            subject="s",
            description="d",
            idempotency_key="idem-2",
            confirmation_token=token,
            approval_ref=ref,
        )
        ticket = service.submit(resident_ctx, db, ticket.id, expected_version=ticket.version)
        assert ticket.status == ConsultationStatus.SUBMITTED
        ticket = service.start_processing(staff_ctx, db, ticket.id, expected_version=ticket.version)
        assert ticket.status == ConsultationStatus.PROCESSING
        ticket = service.answer(
            staff_ctx,
            db,
            ticket.id,
            "按 2.5 元/平米计征",
            expected_version=ticket.version,
        )
        assert ticket.status == ConsultationStatus.ANSWERED
        assert ticket.answer == "按 2.5 元/平米计征"
        ticket = service.resolve(staff_ctx, db, ticket.id, expected_version=ticket.version)
        assert ticket.status == ConsultationStatus.RESOLVED


def test_consultation_appeal(sessions, seed):
    resident_ctx = _ctx(seed, "resident")
    staff_ctx = _ctx(seed, "staff", roles=("FINANCE",))
    service = ConsultationService(_uow_factory(sessions))
    token, ref = _make_consultation_approval(sessions, resident_ctx, subject="s", description="d")
    with sessions() as db:
        ticket = service.create_draft(
            resident_ctx,
            db,
            subject="s",
            description="d",
            idempotency_key="idem-3",
            confirmation_token=token,
            approval_ref=ref,
        )
        ticket = service.submit(resident_ctx, db, ticket.id, expected_version=ticket.version)
        ticket = service.start_processing(staff_ctx, db, ticket.id, expected_version=ticket.version)
        ticket = service.answer(staff_ctx, db, ticket.id, "a", expected_version=ticket.version)
        ticket = service.appeal(resident_ctx, db, ticket.id, expected_version=ticket.version)
        assert ticket.status == ConsultationStatus.APPEALED
        ticket = service.start_processing(staff_ctx, db, ticket.id, expected_version=ticket.version)
        assert ticket.status == ConsultationStatus.PROCESSING


def test_consultation_owner_only(sessions, seed):
    resident_ctx = _ctx(seed, "resident")
    other_ctx = _ctx(seed, "other_resident")
    service = ConsultationService(_uow_factory(sessions))
    token, ref = _make_consultation_approval(sessions, resident_ctx, subject="s", description="d")
    with sessions() as db:
        ticket = service.create_draft(
            resident_ctx,
            db,
            subject="s",
            description="d",
            idempotency_key="idem-4",
            confirmation_token=token,
            approval_ref=ref,
        )
        with pytest.raises(ConsultationError) as exc:
            service.submit(other_ctx, db, ticket.id, expected_version=ticket.version)
    assert exc.value.code == "CONSULTATION_FORBIDDEN"


def test_consultation_staff_only(sessions, seed):
    resident_ctx = _ctx(seed, "resident")
    service = ConsultationService(_uow_factory(sessions))
    token, ref = _make_consultation_approval(sessions, resident_ctx, subject="s", description="d")
    with sessions() as db:
        ticket = service.create_draft(
            resident_ctx,
            db,
            subject="s",
            description="d",
            idempotency_key="idem-5",
            confirmation_token=token,
            approval_ref=ref,
        )
        with pytest.raises(ConsultationError) as exc:
            service.start_processing(resident_ctx, db, ticket.id, expected_version=ticket.version)
    assert exc.value.code == "CONSULTATION_FORBIDDEN"


def test_consultation_rejects_stale_version(sessions, seed):
    resident_ctx = _ctx(seed, "resident")
    service = ConsultationService(_uow_factory(sessions))
    token, ref = _make_consultation_approval(
        sessions, resident_ctx, subject="并发更新", description="验证版本冲突"
    )
    with sessions() as db:
        ticket = service.create_draft(
            resident_ctx,
            db,
            subject="并发更新",
            description="验证版本冲突",
            idempotency_key="idem-version",
            confirmation_token=token,
            approval_ref=ref,
        )
        service.submit(resident_ctx, db, ticket.id, expected_version=ticket.version)
        with pytest.raises(ConsultationError) as exc:
            service.submit(resident_ctx, db, ticket.id, expected_version=1)
    assert exc.value.code == "VERSION_CONFLICT"
    assert exc.value.details == {"current_version": 2}


def test_source_unavailable_keeps_draft_r02(sessions, seed):
    resident_ctx = _ctx(seed, "resident")
    # 账单源不可用时，list_bills 应失败（503），但咨询草稿仍可保存（R-02）。
    approval_service = ApprovalService(sessions)
    billing = BillingService(
        lambda tx: UnavailableBillingUnitOfWork(tx, approval_service)
    )
    consultation = ConsultationService(_uow_factory(sessions))
    token, ref = _make_consultation_approval(
        sessions, resident_ctx, subject="源挂了还能问吗", description="d"
    )
    with sessions() as db:
        with pytest.raises(BillingError) as exc:
            billing.list_bills(resident_ctx, db)
        assert exc.value.code == "BILLING_SOURCE_UNAVAILABLE"
        ticket = consultation.create_draft(
            resident_ctx,
            db,
            subject="源挂了还能问吗",
            description="d",
            idempotency_key="idem-6",
            confirmation_token=token,
            approval_ref=ref,
        )
    assert ticket.id
    assert len(ticket.id) == 32
    assert ticket.status == ConsultationStatus.DRAFT


def test_consultation_and_audit_share_one_transaction(sessions, seed):
    ctx = _ctx(seed, "resident")
    token, ref = _make_consultation_approval(
        sessions, ctx, subject="事务原子性", description="审计失败时咨询也不能落库"
    )
    approval_service = ApprovalService(sessions)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        with sessions() as db:
            ConsultationService(
                lambda tx: FailingAuditUnitOfWork(tx, approval_service)
            ).create_draft(
                ctx,
                db,
                subject="事务原子性",
                description="审计失败时咨询也不能落库",
                idempotency_key="idem-atomic",
                confirmation_token=token,
                approval_ref=ref,
            )

    with sessions() as db:
        assert db.query(ConsultationModel).filter_by(subject="事务原子性").count() == 0
