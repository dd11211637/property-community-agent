import pytest

from property_agent.inspection.application.commands import (
    CreateInspectionTaskCommand,
    CreateSecurityEventCommand,
    ExecuteEventActionCommand,
    ExecuteTaskActionCommand,
    InspectionTaskSearch,
    SecurityEventSearch,
)
from property_agent.inspection.domain.enums import (
    EventAction,
    EventRiskLevel,
    EventStatus,
    EventType,
    TaskAction,
    TaskRecordType,
    TaskStatus,
)
from property_agent.inspection.domain.errors import (
    BusinessError,
)


# ============================== 巡检任务 ==============================
def _create_task(task_service, context, idem="k-create", **kw):
    return task_service.create_task(
        CreateInspectionTaskCommand(
            title=kw.get("title", "夜间巡检"),
            description=kw.get("description", "B1-B3 安全巡检"),
            route_points=kw.get("route_points", ("B1", "B2", "B3")),
        ),
        context,
        idempotency_key=idem,
    )


def test_create_task_requires_manager_or_security(task_service, resident_context):
    with pytest.raises(BusinessError) as exc:
        _create_task(task_service, resident_context)
    assert exc.value.code == "FORBIDDEN"


def test_create_task_missing_route_point(task_service, manager_context):
    with pytest.raises(BusinessError) as exc:
        _create_task(task_service, manager_context, route_points=())
    assert exc.value.code == "VALIDATION_ERROR"


def test_create_task_idempotency_replay(task_service, manager_context):
    t1 = _create_task(task_service, manager_context, idem="replay-1")
    t2 = _create_task(task_service, manager_context, idem="replay-1")
    assert t1.id == t2.id


def test_create_task_idempotency_conflict(task_service, manager_context):
    _create_task(task_service, manager_context, idem="conflict-1", title="A")
    with pytest.raises(BusinessError) as exc:
        _create_task(task_service, manager_context, idem="conflict-1", title="B")
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"


def test_task_full_lifecycle(task_service, harness, ids, manager_context, security_context):
    task = _create_task(task_service, manager_context, idem="life-1")
    assert task.status == TaskStatus.PLANNED
    # 分派给安保
    task = task_service.execute_task_action(
        task.id,
        ExecuteTaskActionCommand(
            action=TaskAction.ASSIGN, expected_version=task.version, assignee_id=ids.security_worker
        ),
        manager_context,
        idempotency_key="life-assign",
    )
    assert task.status == TaskStatus.ASSIGNED
    assert task.assignee_id == ids.security_worker
    # 安保开始
    task = task_service.execute_task_action(
        task.id,
        ExecuteTaskActionCommand(action=TaskAction.START, expected_version=task.version),
        security_context,
        idempotency_key="life-start",
    )
    assert task.status == TaskStatus.IN_PROGRESS
    # 安保提交记录（需确认令牌）
    task = task_service.execute_task_action(
        task.id,
        ExecuteTaskActionCommand(
            action=TaskAction.SUBMIT_RECORDS,
            expected_version=task.version,
            record_type=TaskRecordType.COMPLETION,
            note="已巡检完毕，无异常",
            confirmation_token="ct-1",
        ),
        security_context,
        idempotency_key="life-submit",
    )
    assert task.status == TaskStatus.SUBMITTED
    # 管理者完成
    task = task_service.execute_task_action(
        task.id,
        ExecuteTaskActionCommand(action=TaskAction.COMPLETE, expected_version=task.version),
        manager_context,
        idempotency_key="life-complete",
    )
    assert task.status == TaskStatus.COMPLETED
    assert task.closed_at is not None


def test_submit_records_requires_confirmation(task_service, ids, manager_context, security_context):
    task = _create_task(task_service, manager_context, idem="conf-1")
    task = task_service.execute_task_action(
        task.id,
        ExecuteTaskActionCommand(
            action=TaskAction.ASSIGN, expected_version=task.version, assignee_id=ids.security_worker
        ),
        manager_context,
        idempotency_key="conf-assign",
    )
    task = task_service.execute_task_action(
        task.id,
        ExecuteTaskActionCommand(action=TaskAction.START, expected_version=task.version),
        security_context,
        idempotency_key="conf-start",
    )
    with pytest.raises(BusinessError) as exc:
        task_service.execute_task_action(
            task.id,
            ExecuteTaskActionCommand(
                action=TaskAction.SUBMIT_RECORDS,
                expected_version=task.version,
                record_type=TaskRecordType.COMPLETION,
                note="无异常",
            ),
            security_context,
            idempotency_key="conf-submit",
        )
    assert exc.value.code == "CONFIRMATION_REQUIRED"


def test_task_optimistic_lock(task_service, ids, manager_context, security_context):
    task = _create_task(task_service, manager_context, idem="opt-1")
    task = task_service.execute_task_action(
        task.id,
        ExecuteTaskActionCommand(
            action=TaskAction.ASSIGN, expected_version=task.version, assignee_id=ids.security_worker
        ),
        manager_context,
        idempotency_key="opt-assign",
    )
    with pytest.raises(BusinessError) as exc:
        task_service.execute_task_action(
            task.id,
            ExecuteTaskActionCommand(action=TaskAction.START, expected_version=task.version - 1),
            security_context,
            idempotency_key="opt-start",
        )
    assert exc.value.code == "VERSION_CONFLICT"


def test_task_list_scoping(task_service, harness, ids, manager_context, security_context):
    t = _create_task(task_service, manager_context, idem="scope-1")
    task_service.execute_task_action(
        t.id,
        ExecuteTaskActionCommand(
            action=TaskAction.ASSIGN, expected_version=t.version, assignee_id=ids.security_worker
        ),
        manager_context,
        idempotency_key="scope-assign",
    )
    # 安保只能看到分配给自己的任务
    sec_results = task_service.search_tasks(InspectionTaskSearch(), security_context)
    assert len(sec_results) == 1 and sec_results[0].id == t.id
    # 管理者看到全部
    mgr_results = task_service.search_tasks(InspectionTaskSearch(), manager_context)
    assert len(mgr_results) == 1


# ============================== 安防事件 ==============================
def _create_event(event_service, context, risk=EventRiskLevel.MEDIUM, idem="ev-create", **kw):
    return event_service.create_event(
        CreateSecurityEventCommand(
            source_task_id=None,
            event_type=kw.get("event_type", EventType.EQUIPMENT_FAULT),
            risk_level=risk,
            location=kw.get("location", "B2 车库"),
            description=kw.get("description", "消防栓漏水"),
            confirmation_token="ct-ev",
            attachment_ids=(),
        ),
        context,
        idempotency_key=idem,
    )


def test_create_event_allows_resident_as_clue(event_service, resident_context):
    event = _create_event(event_service, resident_context, risk=EventRiskLevel.LOW, idem="ev-res")
    assert event.status == EventStatus.REPORTED
    assert event.reporter_id is not None


def test_create_event_requires_confirmation(event_service, manager_context):
    with pytest.raises(BusinessError) as exc:
        event_service.create_event(
            CreateSecurityEventCommand(
                source_task_id=None,
                event_type=EventType.FIRE,
                risk_level=EventRiskLevel.HIGH_RISK,
                location="3 栋",
                description="火情",
                confirmation_token="",
                attachment_ids=(),
            ),
            manager_context,
            idempotency_key="ev-noconf",
        )
    assert exc.value.code == "CONFIRMATION_REQUIRED"


def test_high_risk_event_notifies_duty(event_service, harness, resident_context):
    event = _create_event(
        event_service, resident_context, risk=EventRiskLevel.HIGH_RISK, idem="ev-hr"
    )
    assert event.risk_level == EventRiskLevel.HIGH_RISK
    high_risk_msgs = [m for m in harness.state.messages if m["event_type"] == "HIGH_RISK_EVENT"]
    assert len(high_risk_msgs) == 1
    assert high_risk_msgs[0]["receiver_id"] == harness.duty_users[0]


def test_event_assign_requires_manager(event_service, ids, security_context):
    event = _create_event(event_service, security_context, risk=EventRiskLevel.MEDIUM, idem="ev-a1")
    with pytest.raises(BusinessError) as exc:
        event_service.execute_event_action(
            event.id,
            ExecuteEventActionCommand(
                action=EventAction.ASSIGN,
                expected_version=event.version,
                assignee_id=ids.security_worker,
            ),
            security_context,
            idempotency_key="ev-a2",
        )
    assert exc.value.code == "FORBIDDEN"


def test_event_full_lifecycle_and_high_risk_close(
    event_service, harness, ids, manager_context, security_context
):
    event = _create_event(
        event_service, security_context, risk=EventRiskLevel.HIGH_RISK, idem="ev-life"
    )
    # 管理者分派
    event = event_service.execute_event_action(
        event.id,
        ExecuteEventActionCommand(
            action=EventAction.ASSIGN,
            expected_version=event.version,
            assignee_id=ids.security_worker,
        ),
        manager_context,
        idempotency_key="ev-life-assign",
    )
    assert event.status == EventStatus.ASSIGNED
    # 处置人提交处置
    event = event_service.execute_event_action(
        event.id,
        ExecuteEventActionCommand(
            action=EventAction.SUBMIT_DISPOSAL,
            expected_version=event.version,
            note="已通风并上报燃气公司，现场已封锁",
        ),
        security_context,
        idempotency_key="ev-life-disp",
    )
    assert event.status == EventStatus.PENDING_REVIEW
    # 高风险事件须先由授权管理者完成等级/处置方案人工确认（GRADE_CONFIRM）
    event = event_service.execute_event_action(
        event.id,
        ExecuteEventActionCommand(action=EventAction.GRADE_CONFIRM, expected_version=event.version),
        manager_context,
        idempotency_key="ev-life-grade",
    )
    assert event.grade_confirmed_by == ids.manager
    # 管理者复核通过
    event = event_service.execute_event_action(
        event.id,
        ExecuteEventActionCommand(action=EventAction.REVIEW_PASS, expected_version=event.version),
        manager_context,
        idempotency_key="ev-life-pass",
    )
    assert event.status == EventStatus.CLOSED
    assert event.grade_confirmed_by == ids.manager


def test_event_submit_disposal_requires_handler(
    event_service, ids, manager_context, security_context
):
    event = _create_event(event_service, security_context, risk=EventRiskLevel.MEDIUM, idem="ev-h1")
    event = event_service.execute_event_action(
        event.id,
        ExecuteEventActionCommand(
            action=EventAction.ASSIGN,
            expected_version=event.version,
            assignee_id=ids.security_worker,
        ),
        manager_context,
        idempotency_key="ev-h2",
    )
    with pytest.raises(BusinessError) as exc:
        event_service.execute_event_action(
            event.id,
            ExecuteEventActionCommand(
                action=EventAction.SUBMIT_DISPOSAL, expected_version=event.version, note="处置"
            ),
            manager_context,
            idempotency_key="ev-h3",
        )
    assert exc.value.code in ("FORBIDDEN", "RESOURCE_NOT_FOUND")


def test_event_return_requires_reason(event_service, ids, manager_context, security_context):
    event = _create_event(event_service, security_context, risk=EventRiskLevel.MEDIUM, idem="ev-r1")
    event = event_service.execute_event_action(
        event.id,
        ExecuteEventActionCommand(
            action=EventAction.ASSIGN,
            expected_version=event.version,
            assignee_id=ids.security_worker,
        ),
        manager_context,
        idempotency_key="ev-r2",
    )
    event = event_service.execute_event_action(
        event.id,
        ExecuteEventActionCommand(
            action=EventAction.SUBMIT_DISPOSAL, expected_version=event.version, note="初步处置"
        ),
        security_context,
        idempotency_key="ev-r3",
    )
    with pytest.raises(BusinessError) as exc:
        event_service.execute_event_action(
            event.id,
            ExecuteEventActionCommand(
                action=EventAction.RETURN, expected_version=event.version, note=""
            ),
            manager_context,
            idempotency_key="ev-r4",
        )
    assert exc.value.code == "VALIDATION_ERROR"


def test_event_optimistic_lock(event_service, ids, manager_context, security_context):
    event = _create_event(event_service, security_context, risk=EventRiskLevel.MEDIUM, idem="ev-o1")
    event = event_service.execute_event_action(
        event.id,
        ExecuteEventActionCommand(
            action=EventAction.ASSIGN,
            expected_version=event.version,
            assignee_id=ids.security_worker,
        ),
        manager_context,
        idempotency_key="ev-o2",
    )
    with pytest.raises(BusinessError) as exc:
        event_service.execute_event_action(
            event.id,
            ExecuteEventActionCommand(
                action=EventAction.SUBMIT_DISPOSAL, expected_version=event.version - 1, note="处置"
            ),
            security_context,
            idempotency_key="ev-o3",
        )
    assert exc.value.code == "VERSION_CONFLICT"


def test_event_scoping_for_security(event_service, ids, manager_context, security_context):
    ev = _create_event(event_service, security_context, risk=EventRiskLevel.MEDIUM, idem="ev-s1")
    ev = event_service.execute_event_action(
        ev.id,
        ExecuteEventActionCommand(
            action=EventAction.ASSIGN, expected_version=ev.version, assignee_id=ids.security_worker
        ),
        manager_context,
        idempotency_key="ev-s2",
    )
    # 分配给自己的事件，安保可见
    results = event_service.search_events(SecurityEventSearch(), security_context)
    assert len(results) == 1 and results[0].id == ev.id


def test_event_idempotency(event_service, security_context):
    e1 = _create_event(event_service, security_context, risk=EventRiskLevel.LOW, idem="ev-idem")
    e2 = _create_event(event_service, security_context, risk=EventRiskLevel.LOW, idem="ev-idem")
    assert e1.id == e2.id
