"""PRD §6.4 巡检与安防增强 — 验收测试。

覆盖 7 项增强：
  1. 计划时间与路线冲突校验
  2. 附件上传状态、补交原因和实际时间
  3. AI 异常建议的数据结构与"待人工确认"标识
  4. 模型失败时允许人工直接上报（report_source）
  5. 高风险事件通知值班人员
  6. 通知失败状态、备用联系人与升级（无值班人员 -> handover_ticket）
  7. 高风险事件只能由授权管理者复核关闭（GRADE_CONFIRM 前置 + HANDOVER_REQUIRED 守卫）

其中附件上传状态校验走真实 SQLAlchemy 端口（逻辑只在生产端口里）；其余经
``Harness`` 的 fake 端口覆盖服务层分支。
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from property_agent.inspection.application.commands import (
    AddAiSuggestionCommand,
    CreateInspectionTaskCommand,
    CreateSecurityEventCommand,
    ExecuteEventActionCommand,
    ExecuteTaskActionCommand,
)
from property_agent.inspection.application.service import (
    InspectionTaskService,
    SecurityEventService,
)
from property_agent.inspection.domain.entities import InspectionTask
from property_agent.inspection.domain.enums import (
    EventAction,
    EventRiskLevel,
    EventStatus,
    EventType,
    TaskAction,
    TaskStatus,
)
from property_agent.inspection.domain.enums import (
    TaskRecordType as _TR,
)
from property_agent.inspection.domain.errors import BusinessError
from property_agent.inspection.infrastructure.shared_ports import SqlAlchemyAttachmentPort
from property_agent.platform.infrastructure.orm_models import AttachmentModel, Base
from tests.inspection_support import Harness


# ============================== 1. 计划时间与路线冲突校验 ==============================
def test_plan_route_conflict_detected(ids, manager_context):
    h = Harness(security_workers={ids.security_worker}, duty_users=[ids.duty_user])
    now = datetime.now(UTC)
    seed = InspectionTask(
        id=uuid4(),
        community_id=ids.community,
        business_no="XJ-SEED-1",
        title="既有计划",
        description="B1-B2 巡检",
        route_points=("B1", "B2"),
        created_by=ids.manager,
        create_idempotency_key="seed-k",
        status=TaskStatus.PLANNED,
        planned_at=now,
        due_at=now + timedelta(hours=1),
        created_at=now,
        updated_at=now,
    )
    h.state.tasks[seed.id] = seed
    svc = InspectionTaskService(h.uow)

    # 时间窗重叠 + 共享路线点 B1 -> 冲突
    with pytest.raises(BusinessError) as exc:
        svc.create_task(
            CreateInspectionTaskCommand(
                title="新计划-冲突",
                description="d",
                route_points=("B1", "B3"),
                planned_at=now + timedelta(minutes=30),
                due_at=now + timedelta(minutes=90),
            ),
            manager_context,
            idempotency_key="conflict-k",
        )
    assert exc.value.code == "PLAN_CONFLICT"

    # 时间窗不重叠（错峰）-> 允许
    ok = svc.create_task(
        CreateInspectionTaskCommand(
            title="新计划-错峰",
            description="d",
            route_points=("B1", "B3"),
            planned_at=now + timedelta(hours=2),
            due_at=now + timedelta(hours=3),
        ),
        manager_context,
        idempotency_key="ok-k",
    )
    assert ok.status == TaskStatus.PLANNED

    # 路线点无交集 -> 允许
    ok2 = svc.create_task(
        CreateInspectionTaskCommand(
            title="新计划-异线",
            description="d",
            route_points=("B9", "B10"),
            planned_at=now + timedelta(minutes=30),
            due_at=now + timedelta(minutes=90),
        ),
        manager_context,
        idempotency_key="ok2-k",
    )
    assert ok2.status == TaskStatus.PLANNED


# ============================== 2. 附件上传状态 / 补交原因 ==============================
def test_attachment_uploading_status_rejected(ids):
    """附件仍处于 UPLOADING 状态时，生产端口拒绝挂载（PRD 6.4：附件上传状态）。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        att = AttachmentModel(
            id=uuid4(),
            community_id=ids.community,
            uploader_id=ids.resident,
            file_name="door.jpg",
            content_type="image/jpeg",
            size_bytes=10,
            status="UPLOADING",
            storage_key="k1",
        )
        session.add(att)
        session.commit()

        port = SqlAlchemyAttachmentPort(session)
        with pytest.raises(BusinessError) as exc:
            port.ensure_usable(
                attachment_ids=(att.id,),
                actor_id=ids.resident,
                community_id=ids.community,
                request_id="req-att",
            )
        assert exc.value.code == "VALIDATION_ERROR"
        assert exc.value.details.get("status") == "UPLOADING"


def test_supplement_requires_reason(ids, security_context, manager_context):
    h = Harness(security_workers={ids.security_worker}, duty_users=[ids.duty_user])
    svc = InspectionTaskService(h.uow)
    task = svc.create_task(
        CreateInspectionTaskCommand(title="t", description="d", route_points=("B1",)),
        security_context,
        idempotency_key="sup-1",
    )
    task = svc.execute_task_action(
        task.id,
        ExecuteTaskActionCommand(
            action=TaskAction.ASSIGN, expected_version=task.version, assignee_id=ids.security_worker
        ),
        manager_context,
        idempotency_key="sup-assign",
    )
    task = svc.execute_task_action(
        task.id,
        ExecuteTaskActionCommand(action=TaskAction.START, expected_version=task.version),
        security_context,
        idempotency_key="sup-start",
    )

    # 补交记录但不填原因 -> 拒绝
    with pytest.raises(BusinessError) as exc:
        svc.execute_task_action(
            task.id,
            ExecuteTaskActionCommand(
                action=TaskAction.ADD_RECORD,
                expected_version=task.version,
                record_type=_TR.SUPPLEMENT,
                note="现场补交",
                is_supplement=True,
                actual_time=datetime.now(UTC),
            ),
            security_context,
            idempotency_key="sup-rec",
        )
    assert exc.value.code == "SUPPLEMENT_REASON_REQUIRED"

    # 填入原因 -> 成功，并记录补交原因与实际时间
    svc.execute_task_action(
        task.id,
        ExecuteTaskActionCommand(
            action=TaskAction.ADD_RECORD,
            expected_version=task.version,
            record_type=_TR.SUPPLEMENT,
            note="现场补交",
            is_supplement=True,
            actual_time=datetime.now(UTC),
            supplement_reason="首轮巡检遗漏该点位",
        ),
        security_context,
        idempotency_key="sup-rec2",
    )
    recs = [r for r in h.state.task_records if r["is_supplement"]]
    assert recs, "补交记录应已写入"
    assert recs[0]["supplement_reason"] == "首轮巡检遗漏该点位"
    assert recs[0]["actual_time"] is not None


# ============================== 3. AI 异常建议 + 待人工确认 ==============================
def test_ai_suggestion_pending_then_confirm(ids, manager_context):
    h = Harness(security_workers={ids.security_worker}, duty_users=[ids.duty_user])
    svc = InspectionTaskService(h.uow)
    task = svc.create_task(
        CreateInspectionTaskCommand(title="t", description="d", route_points=("B1",)),
        manager_context,
        idempotency_key="ai-1",
    )

    # AI 异常建议：追加后处于"待人工确认"
    task = svc.add_ai_suggestion(
        task.id,
        AddAiSuggestionCommand(point="B1", finding="门禁读卡异常", severity="HIGH"),
        manager_context,
        idempotency_key="ai-add",
    )
    assert task.ai_pending_confirm is True
    assert len(task.ai_suggestions) == 1
    assert task.ai_suggestions[0].pending_confirm is True

    # 只有授权管理者可确认
    task = svc.confirm_ai_suggestions(task.id, manager_context, idempotency_key="ai-conf")
    assert task.ai_pending_confirm is False
    assert task.ai_suggestions[0].confirmed_by == ids.manager
    assert task.ai_suggestions[0].pending_confirm is False

    # 无可确认项 -> 拒绝
    with pytest.raises(BusinessError) as exc:
        svc.confirm_ai_suggestions(task.id, manager_context, idempotency_key="ai-conf2")
    assert exc.value.code == "VALIDATION_ERROR"


# ============================== 4. 上报来源 MANUAL / AI ==============================
def test_report_source_manual_and_ai(ids, resident_context):
    h = Harness(security_workers={ids.security_worker}, duty_users=[ids.duty_user])
    svc = SecurityEventService(h.uow)

    ev_manual = svc.create_event(
        CreateSecurityEventCommand(
            source_task_id=None,
            event_type=EventType.FIRE,
            risk_level=EventRiskLevel.MEDIUM,
            location="B1",
            description="火情",
            confirmation_token="ct-manual",
            report_source="MANUAL",
        ),
        resident_context,
        idempotency_key="rs-manual",
    )
    assert ev_manual.report_source == "MANUAL"

    ev_ai = svc.create_event(
        CreateSecurityEventCommand(
            source_task_id=None,
            event_type=EventType.EQUIPMENT_FAULT,
            risk_level=EventRiskLevel.LOW,
            location="B2",
            description="模型识别到的隐患",
            confirmation_token="ct-ai",
            report_source="AI",
        ),
        resident_context,
        idempotency_key="rs-ai",
    )
    assert ev_ai.report_source == "AI"


# ============================== 5/6. 高风险通知值班 / 无值班升级 ==============================
def test_high_risk_notifies_duty_users(ids, resident_context):
    h = Harness(security_workers={ids.security_worker}, duty_users=[ids.duty_user])
    svc = SecurityEventService(h.uow)
    ev = svc.create_event(
        CreateSecurityEventCommand(
            source_task_id=None,
            event_type=EventType.PERSONAL_SAFETY,
            risk_level=EventRiskLevel.HIGH_RISK,
            location="B2",
            description="入侵告警",
            confirmation_token="ct-hr",
        ),
        resident_context,
        idempotency_key="hr-1",
    )
    assert ev.risk_level == EventRiskLevel.HIGH_RISK
    duty_msgs = [m for m in h.state.messages if m["event_type"] == "HIGH_RISK_EVENT"]
    assert [m["receiver_id"] for m in duty_msgs] == [ids.duty_user]
    assert h.state.escalations == []  # 有值班人员，不升级


def test_high_risk_no_duty_escalates(ids, resident_context):
    h = Harness(security_workers={ids.security_worker}, duty_users=[])
    svc = SecurityEventService(h.uow)
    svc.create_event(
        CreateSecurityEventCommand(
            source_task_id=None,
            event_type=EventType.PERSONAL_SAFETY,
            risk_level=EventRiskLevel.HIGH_RISK,
            location="B2",
            description="入侵告警",
            confirmation_token="ct-hr2",
        ),
        resident_context,
        idempotency_key="hr-2",
    )
    assert h.state.escalations, "无值班人员时应升级到备用联系人"
    assert h.state.escalations[0]["reason"] == "NO_DUTY_STAFF"
    assert h.state.messages == []  # 无人可通知，不产生站内信


# ============================== 7. 高风险只能由授权管理者复核关闭 ==============================
def test_high_risk_requires_grade_confirm_before_close(
    ids, resident_context, manager_context, security_context
):
    h = Harness(security_workers={ids.security_worker}, duty_users=[ids.duty_user])
    svc = SecurityEventService(h.uow)
    ev = svc.create_event(
        CreateSecurityEventCommand(
            source_task_id=None,
            event_type=EventType.PERSONAL_SAFETY,
            risk_level=EventRiskLevel.HIGH_RISK,
            location="B2",
            description="入侵告警",
            confirmation_token="ct-gc",
        ),
        resident_context,
        idempotency_key="gc-1",
    )
    ev = svc.execute_event_action(
        ev.id,
        ExecuteEventActionCommand(
            action=EventAction.ASSIGN, expected_version=ev.version, assignee_id=ids.security_worker
        ),
        manager_context,
        idempotency_key="gc-assign",
    )
    ev = svc.execute_event_action(
        ev.id,
        ExecuteEventActionCommand(
            action=EventAction.SUBMIT_DISPOSAL, expected_version=ev.version, note="已到场处置"
        ),
        security_context,
        idempotency_key="gc-disposal",
    )
    assert ev.status == EventStatus.PENDING_REVIEW
    assert svc.available_event_actions(ev, security_context) == []

    # 高风险未确认等级时，管理者可用动作仅为 GRADE_CONFIRM
    actions = svc.available_event_actions(ev, manager_context)
    assert actions == [EventAction.GRADE_CONFIRM]

    # 直接复核通过 -> 实体守卫拒绝
    with pytest.raises(BusinessError) as exc:
        svc.execute_event_action(
            ev.id,
            ExecuteEventActionCommand(action=EventAction.REVIEW_PASS, expected_version=ev.version),
            manager_context,
            idempotency_key="gc-pass",
        )
    assert exc.value.code == "HANDOVER_REQUIRED"

    # 授权管理者完成等级确认
    ev = svc.execute_event_action(
        ev.id,
        ExecuteEventActionCommand(action=EventAction.GRADE_CONFIRM, expected_version=ev.version),
        manager_context,
        idempotency_key="gc-grade",
    )
    assert ev.grade_confirmed_by == ids.manager

    # 确认后复核通过 -> 关闭
    ev = svc.execute_event_action(
        ev.id,
        ExecuteEventActionCommand(action=EventAction.REVIEW_PASS, expected_version=ev.version),
        manager_context,
        idempotency_key="gc-pass2",
    )
    assert ev.status == EventStatus.CLOSED
