"""Seed deterministic cross-module data for the Docker demo environment.

The schema must already be at Alembic head. This module contains data only and
is intentionally outside ``src/`` so production startup never imports it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import sessionmaker

from property_agent.announcement.infrastructure.models import AnnouncementModel
from property_agent.billing.infrastructure.orm_models import (
    BillingRuleModel,
    BillingUserModel,
    BillModel,
    BuildingModel,
    ConsultationModel,
    RoomModel,
)
from property_agent.inspection.infrastructure.models import (
    InspectionTaskModel,
    SecurityEventModel,
)
from property_agent.platform.infrastructure.database import get_engine
from property_agent.platform.infrastructure.orm_models import (
    AuditLogModel,
    HandoverTicketModel,
    MessageRecordModel,
)
from property_agent.repair.infrastructure.models import WorkOrderModel
from testing.seeds.seed_platform import (
    COMMUNITY_A,
    CS_A,
    HOUSE_A_101,
    HOUSE_A_102,
    HOUSE_A_201,
    MANAGER_A,
    REPAIR_A,
    RESIDENT_A_MULTI,
    RESIDENT_A_SINGLE,
    SECURITY_A,
)
from testing.seeds.seed_platform import (
    seed as seed_platform,
)

WORK_ORDER_ID = UUID("c1000000-0000-0000-0000-000000000001")
ANNOUNCEMENT_ID = UUID("c2000000-0000-0000-0000-000000000001")
INSPECTION_TASK_ID = UUID("c3000000-0000-0000-0000-000000000001")
SECURITY_EVENT_ID = UUID("c4000000-0000-0000-0000-000000000001")
FAILED_MESSAGE_ID = UUID("c5000000-0000-0000-0000-000000000002")
DEMO_TIMEZONE = ZoneInfo("Asia/Shanghai")


def seed(engine=None) -> bool:
    """Seed platform and representative records; safe to run repeatedly."""
    engine = engine or get_engine()
    seed_platform(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        if session.get(WorkOrderModel, WORK_ORDER_ID) is not None:
            changed = _normalize_existing_demo_rows(session)
            if changed:
                session.commit()
                print("Existing business demo data normalized successfully.")
            else:
                print("Business demo data already present; skipping.")
            return False
        now = datetime.now(timezone.utc)
        _seed_business_flows(session, now)
        _seed_billing(session, now.replace(tzinfo=None))
        _seed_operations(session, now)
        session.commit()
    print("Cross-module demo data seeded successfully.")
    return True


def _normalize_existing_demo_rows(session) -> bool:
    """Keep deterministic demo rows compatible with current domain enums."""
    changed = False
    work_order = session.get(WorkOrderModel, WORK_ORDER_ID)
    if work_order is not None:
        expected = {"category": "WATER_PLUMBING", "status": "PENDING_ACCEPTANCE"}
        for field, value in expected.items():
            if getattr(work_order, field) != value:
                setattr(work_order, field, value)
                changed = True
    security_event = session.get(SecurityEventModel, SECURITY_EVENT_ID)
    if security_event is not None:
        expected = {
            "event_type": "PERSONAL_SAFETY",
            "risk_level": "HIGH_RISK",
            "status": "ASSIGNED",
        }
        for field, value in expected.items():
            if getattr(security_event, field) != value:
                setattr(security_event, field, value)
                changed = True
    announcement = session.get(AnnouncementModel, ANNOUNCEMENT_ID)
    expected_audience = {"building_ids": ["1栋"]}
    if announcement is not None and announcement.audience_condition != expected_audience:
        announcement.audience_condition = expected_audience
        changed = True
    current_period, previous_period = _current_and_previous_periods(
        datetime.now(DEMO_TIMEZONE).replace(tzinfo=None)
    )
    expected_periods = {
        "demo-bill-01": current_period,
        "demo-bill-02": previous_period,
        "demo-bill-03": current_period,
        "demo-bill-04": previous_period,
    }
    for bill_id, period in expected_periods.items():
        bill = session.get(BillModel, bill_id)
        if bill is not None:
            expected_due_date = _due_date_for_period(period)
            if bill.bill_period != period:
                bill.bill_period = period
                changed = True
            if bill.due_date != expected_due_date:
                bill.due_date = expected_due_date
                changed = True
    return changed


def _current_and_previous_periods(now: datetime) -> tuple[str, str]:
    current = f"{now.year:04d}-{now.month:02d}"
    previous_month = 12 if now.month == 1 else now.month - 1
    previous_year = now.year - 1 if now.month == 1 else now.year
    return current, f"{previous_year:04d}-{previous_month:02d}"


def _due_date_for_period(period: str) -> date:
    year, month = (int(part) for part in period.split("-"))
    if month == 12:
        return date(year + 1, 1, 10)
    return date(year, month + 1, 10)


def _seed_business_flows(session, now: datetime) -> None:
    session.add_all(
        [
            WorkOrderModel(
                id=WORK_ORDER_ID,
                community_id=COMMUNITY_A,
                business_no="WX-20260813-DEMO001",
                house_id=HOUSE_A_101,
                reporter_id=RESIDENT_A_SINGLE,
                category="WATER_PLUMBING",
                location="1栋1单元101卫生间",
                description="演示数据：水管漏水，待维修工接单。",
                urgency="NORMAL",
                status="PENDING_ACCEPTANCE",
                assignee_id=REPAIR_A,
                version=2,
                create_idempotency_key="demo-repair-001",
                created_at=now - timedelta(hours=4),
                updated_at=now - timedelta(hours=2),
            ),
            AnnouncementModel(
                id=ANNOUNCEMENT_ID,
                community_id=COMMUNITY_A,
                business_no="AN-DEMO-001",
                title="停水维护演示公告",
                body="1栋将于明日9:00-11:00停水维护。",
                category="MAINTENANCE",
                audience_condition={"building_ids": ["1栋"]},
                created_by=CS_A,
                create_idempotency_key="demo-announcement-001",
                status="PENDING_REVIEW",
                version=1,
                manager_recheck_required=False,
                created_at=now - timedelta(hours=3),
                updated_at=now - timedelta(hours=3),
            ),
            InspectionTaskModel(
                id=INSPECTION_TASK_ID,
                community_id=COMMUNITY_A,
                business_no="IT-DEMO-001",
                title="夜间消防通道巡检",
                description="检查消防通道和应急照明。",
                route_points=["1栋大厅", "地下车库", "消防通道"],
                created_by=MANAGER_A,
                create_idempotency_key="demo-inspection-001",
                status="ASSIGNED",
                assignee_id=SECURITY_A,
                planned_at=now + timedelta(hours=1),
                due_at=now + timedelta(hours=4),
                version=2,
                created_at=now - timedelta(hours=1),
                updated_at=now,
                ai_suggestions=[],
                ai_pending_confirm=False,
            ),
            SecurityEventModel(
                id=SECURITY_EVENT_ID,
                community_id=COMMUNITY_A,
                business_no="SE-DEMO-001",
                reporter_id=RESIDENT_A_SINGLE,
                event_type="PERSONAL_SAFETY",
                risk_level="HIGH_RISK",
                location="1栋南门",
                description="演示数据：发现可疑人员，需人工处置。",
                create_idempotency_key="demo-security-001",
                status="ASSIGNED",
                assignee_id=SECURITY_A,
                report_source="MANUAL",
                version=2,
                created_at=now - timedelta(minutes=50),
                updated_at=now - timedelta(minutes=40),
            ),
        ]
    )


def _seed_billing(session, now: datetime) -> None:
    building_id = "demo-building-a1"
    rooms = {
        HOUSE_A_101: "demo-room-a101",
        HOUSE_A_102: "demo-room-a102",
        HOUSE_A_201: "demo-room-a201",
    }
    session.add(
        BuildingModel(
            building_id=building_id,
            building_name="幸福小区演示楼栋",
            total_floors=18,
            total_units=36,
            address="幸福小区1栋",
        )
    )
    for house_id, room_id in rooms.items():
        session.add(
            RoomModel(
                room_id=room_id,
                building_id=building_id,
                room_number=str(house_id)[-3:],
                room_area=Decimal("100.00"),
                property_fee_rate=Decimal("2.5000"),
                parking_spots=1,
                parking_fee_rate=Decimal("150.00"),
            )
        )
    session.add_all(
        [
            BillingUserModel(
                user_id=str(RESIDENT_A_SINGLE),
                user_name="张三",
                role="owner",
                building_id=building_id,
                room_id=rooms[HOUSE_A_101],
                status="ACTIVE",
            ),
            BillingUserModel(
                user_id=str(RESIDENT_A_MULTI),
                user_name="李四",
                role="owner",
                building_id=building_id,
                room_id=rooms[HOUSE_A_102],
                status="ACTIVE",
            ),
        ]
    )
    statuses = ["UNPAID", "OVERDUE", "PAID", "CANCELLED"]
    current_period, previous_period = _current_and_previous_periods(now)
    periods = [current_period, previous_period, current_period, previous_period]
    for index, status in enumerate(statuses, start=1):
        house_id = HOUSE_A_101 if index < 3 else HOUSE_A_201
        user_id = RESIDENT_A_SINGLE if index < 3 else RESIDENT_A_MULTI
        session.add(
            BillModel(
                bill_id=f"demo-bill-{index:02d}",
                user_id=str(user_id),
                room_id=rooms[house_id],
                bill_period=periods[index - 1],
                property_fee=Decimal("250.00"),
                utility_fee=Decimal("30.00"),
                parking_fee=Decimal("150.00"),
                late_fee=Decimal("0.00"),
                total_amount=Decimal("430.00"),
                due_date=_due_date_for_period(periods[index - 1]),
                status=status,
                community_id="幸福小区",
                house_id=str(house_id),
                version=1,
                fee_type="PROPERTY" if index != 4 else "UTILITY",
                source_time=now,
                rule_version="2026.1" if index != 4 else None,
                rule_name="住宅物业费规则" if index != 4 else None,
            )
        )
    session.add_all(
        [
            BillingRuleModel(
                id="demo-rule-property",
                community_id="幸福小区",
                fee_type="PROPERTY",
                version="2026.1",
                name="住宅物业费规则",
                parameters={"unit": "元/平方米/月", "rate": "2.50"},
                valid_from=datetime(2026, 1, 1),
            ),
            ConsultationModel(
                id="demo-consultation-01",
                community_id="幸福小区",
                house_id=str(HOUSE_A_101),
                actor_id=str(RESIDENT_A_SINGLE),
                bill_id="demo-bill-01",
                subject="物业费计算方式",
                description="请说明本月物业费的计算明细。",
                status="SUBMITTED",
                version=1,
            ),
        ]
    )


def _seed_operations(session, now: datetime) -> None:
    session.add_all(
        [
            MessageRecordModel(
                id=UUID("c5000000-0000-0000-0000-000000000001"),
                receiver_id=RESIDENT_A_SINGLE,
                business_type="REPAIR",
                resource_id=str(WORK_ORDER_ID),
                title="报修已派单",
                body="维修工已收到您的报修。",
                status="SENT",
                retry_count=0,
                idempotency_key="demo-message-sent-001",
                created_at=now,
                updated_at=now,
            ),
            MessageRecordModel(
                id=FAILED_MESSAGE_ID,
                receiver_id=MANAGER_A,
                business_type="INSPECTION",
                resource_id=str(SECURITY_EVENT_ID),
                title="高风险事件消息失败",
                body="请在管理工作台人工接管。",
                status="FAILED",
                retry_count=5,
                last_error="demo transport unavailable",
                idempotency_key="demo-message-failed-001",
                created_at=now,
                updated_at=now,
            ),
            AuditLogModel(
                id=UUID("c6000000-0000-0000-0000-000000000001"),
                actor_id=CS_A,
                community_id=COMMUNITY_A,
                action="ANNOUNCEMENT_SUBMIT_REVIEW",
                resource_type="ANNOUNCEMENT",
                resource_id=str(ANNOUNCEMENT_ID),
                parameter_summary={"demo": True},
                result="SUCCESS",
                request_id="demo-seed-request",
                created_at=now,
            ),
            HandoverTicketModel(
                id=UUID("c7000000-0000-0000-0000-000000000001"),
                community_id=COMMUNITY_A,
                requester_id=RESIDENT_A_SINGLE,
                resource_type="EVENT",
                resource_id=str(SECURITY_EVENT_ID),
                request_id="demo-handover-request",
                payload={"risk_level": "HIGH_RISK"},
                source="INSPECTION",
                queue="SECURITY",
                summary="高风险安防事件需人工处置",
                reason="HIGH_RISK",
                status="PENDING",
                created_at=now,
                updated_at=now,
            ),
            HandoverTicketModel(
                id=UUID("c7000000-0000-0000-0000-000000000002"),
                community_id=COMMUNITY_A,
                requester_id=MANAGER_A,
                resource_type="MESSAGE",
                resource_id=str(FAILED_MESSAGE_ID),
                request_id="demo-message-handover",
                payload={"retry_count": 5, "last_error": "demo transport unavailable"},
                source="INSPECTION",
                queue="SECURITY",
                summary="消息投递达到重试上限，需人工通知负责人",
                reason="MESSAGE_DELIVERY_FAILED",
                status="PENDING",
                created_at=now,
                updated_at=now,
            ),
        ]
    )


if __name__ == "__main__":
    seed()
