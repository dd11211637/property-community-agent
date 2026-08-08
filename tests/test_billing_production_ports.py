"""
Production shared-port integration tests — PRD 6.3 账单与财务咨询.

Mirrors ``tests/test_announcement_production_ports.py``: drive the real
``BillingService`` / ``ConsultationService`` against a live SQLite database.

The billing DB keeps its own engine (轻量接入). In tests we redirect that engine to
the SAME in-memory StaticPool engine used for the platform master data so a single
session sees both ``fee_bills``/``billing_rules``/``billing_consultations`` and the
platform ``communities``/``audit_logs``/``idempotency_records`` rows.

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

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from property_agent.billing.application.service import (
    BillingService,
    ConsultationService,
)
from property_agent.billing.domain.entities import ConsultationStatus
from property_agent.billing.errors import BillingError, ConsultationError
from property_agent.billing.infrastructure.orm_models import (
    BillingRuleModel,
    BillModel,
)
from property_agent.billing.infrastructure.source_port import UnavailableBillingSourcePort
from property_agent.platform.context import RequestContext
from property_agent.platform.infrastructure.orm_models import (
    AuditLogModel,
    Base,
    CommunityModel,
)

COMMUNITY_CODE = "阳光花园"


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

    # 让 billing 模块使用同一个引擎（覆盖模块级默认引擎）。
    import property_agent.billing.application.service as bservice
    import property_agent.billing.infrastructure.database as bd

    bd.engine = engine
    bd.SessionLocal = factory
    bservice.SessionLocal = factory
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
                    bill_id="B001", user_id="u1", room_id="r1", bill_period="2026-08",
                    property_fee=1200.0, utility_fee=0, parking_fee=0, late_fee=0,
                    total_amount=1200.0, due_date=date(2026, 8, 31),
                    status="UNPAID", community_id=COMMUNITY_CODE,
                    house_id=str(current_house), version=1, fee_type="PROPERTY",
                ),
                BillModel(
                    bill_id="B002", user_id="u2", room_id="r2", bill_period="2026-08",
                    property_fee=0, utility_fee=300.0, parking_fee=0, late_fee=0,
                    total_amount=300.0, due_date=date(2026, 8, 31),
                    status="UNPAID", community_id=COMMUNITY_CODE,
                    house_id=str(another_house), version=1, fee_type="UTILITY",
                ),
                BillModel(
                    bill_id="B003", user_id="u3", room_id="r3", bill_period="2026-08",
                    property_fee=900.0, utility_fee=0, parking_fee=0, late_fee=0,
                    total_amount=900.0, due_date=date(2026, 8, 31),
                    status="UNPAID", community_id="其他社区",
                    house_id=str(other_house), version=1, fee_type="PROPERTY",
                ),
                BillingRuleModel(
                    id="R001", community_id=COMMUNITY_CODE, fee_type="PROPERTY",
                    version="2026Q3", name="住宅物业费口径",
                    parameters={"unit_price": 2.5},
                    valid_from=datetime(2026, 1, 1), valid_until=None,
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


# ── 账单查询 ─────────────────────────────────────────────

def test_list_bills_requires_current_house(sessions, seed):
    ctx = _ctx(seed, "resident", with_house=False)
    service = BillingService()
    with sessions() as db:
        with pytest.raises(BillingError) as exc:
            service.list_bills(ctx, db)
    assert exc.value.code == "HOUSE_SELECTION_REQUIRED"


def test_list_bills_filters_by_house_and_community(sessions, seed):
    ctx = _ctx(seed, "resident")
    service = BillingService()
    with sessions() as db:
        bills = service.list_bills(ctx, db)
    ids = {b.bill_id for b in bills}
    assert ids == {"B001"}, f"仅应返回当前房屋账单，实际: {ids}"
    # 跨社区 B003 与跨房屋 B002 均被排除


def test_get_bill_returns_rule_when_effective(sessions, seed):
    ctx = _ctx(seed, "resident")
    service = BillingService()
    with sessions() as db:
        bill, rule = service.get_bill(ctx, db, "B001")
    assert bill.community_id == COMMUNITY_CODE
    assert rule is not None and rule.fee_type == "PROPERTY"
    assert rule.version == "2026Q3"


def test_get_bill_declares_unknown_when_no_rule(sessions, seed):
    ctx = _ctx(seed, "resident")
    service = BillingService()
    with sessions() as db:
        bill, rule = service.get_bill(ctx, db, "B002")
    assert rule is None  # UTILITY 无规则 → 声明未知


def test_query_is_audited(sessions, seed):
    ctx = _ctx(seed, "resident")
    service = BillingService()
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
    service = ConsultationService()
    with sessions() as db:
        t1 = service.create_draft(
            resident_ctx, db, subject="物业费怎么算", description="详询",
            idempotency_key="idem-1",
        )
        t2 = service.create_draft(
            resident_ctx, db, subject="物业费怎么算", description="详询",
            idempotency_key="idem-1",
        )
    assert t1.id == t2.id
    assert t1.status == ConsultationStatus.DRAFT


def test_consultation_lifecycle(sessions, seed):
    resident_ctx = _ctx(seed, "resident")
    staff_ctx = _ctx(seed, "staff", roles=("FINANCE",))
    service = ConsultationService()
    with sessions() as db:
        ticket = service.create_draft(
            resident_ctx, db, subject="s", description="d", idempotency_key="idem-2",
        )
        ticket = service.submit(resident_ctx, db, ticket.id)
        assert ticket.status == ConsultationStatus.SUBMITTED
        ticket = service.start_processing(staff_ctx, db, ticket.id)
        assert ticket.status == ConsultationStatus.PROCESSING
        ticket = service.answer(staff_ctx, db, ticket.id, "按 2.5 元/平米计征")
        assert ticket.status == ConsultationStatus.ANSWERED
        assert ticket.answer == "按 2.5 元/平米计征"
        ticket = service.resolve(staff_ctx, db, ticket.id)
        assert ticket.status == ConsultationStatus.RESOLVED


def test_consultation_appeal(sessions, seed):
    resident_ctx = _ctx(seed, "resident")
    staff_ctx = _ctx(seed, "staff", roles=("FINANCE",))
    service = ConsultationService()
    with sessions() as db:
        ticket = service.create_draft(resident_ctx, db, subject="s", description="d", idempotency_key="idem-3")
        ticket = service.submit(resident_ctx, db, ticket.id)
        ticket = service.start_processing(staff_ctx, db, ticket.id)
        ticket = service.answer(staff_ctx, db, ticket.id, "a")
        ticket = service.appeal(resident_ctx, db, ticket.id)
        assert ticket.status == ConsultationStatus.APPEALED
        ticket = service.start_processing(staff_ctx, db, ticket.id)
        assert ticket.status == ConsultationStatus.PROCESSING


def test_consultation_owner_only(sessions, seed):
    resident_ctx = _ctx(seed, "resident")
    other_ctx = _ctx(seed, "other_resident")
    service = ConsultationService()
    with sessions() as db:
        ticket = service.create_draft(resident_ctx, db, subject="s", description="d", idempotency_key="idem-4")
        with pytest.raises(ConsultationError) as exc:
            service.submit(other_ctx, db, ticket.id)
    assert exc.value.code == "CONSULTATION_FORBIDDEN"


def test_consultation_staff_only(sessions, seed):
    resident_ctx = _ctx(seed, "resident")
    service = ConsultationService()
    with sessions() as db:
        ticket = service.create_draft(resident_ctx, db, subject="s", description="d", idempotency_key="idem-5")
        with pytest.raises(ConsultationError) as exc:
            service.start_processing(resident_ctx, db, ticket.id)
    assert exc.value.code == "CONSULTATION_FORBIDDEN"


def test_source_unavailable_keeps_draft_r02(sessions, seed):
    resident_ctx = _ctx(seed, "resident")
    # 账单源不可用时，list_bills 应失败（503），但咨询草稿仍可保存（R-02）。
    billing = BillingService(source_port_factory=lambda bdb: UnavailableBillingSourcePort())
    consultation = ConsultationService()
    with sessions() as db:
        with pytest.raises(BillingError) as exc:
            billing.list_bills(resident_ctx, db)
        assert exc.value.code == "BILLING_SOURCE_UNAVAILABLE"
        ticket = consultation.create_draft(
            resident_ctx, db, subject="源挂了还能问吗", description="d", idempotency_key="idem-6"
        )
    assert ticket.id
    assert ticket.status == ConsultationStatus.DRAFT
