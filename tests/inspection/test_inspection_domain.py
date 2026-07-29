import uuid

import pytest

from property_agent.inspection.domain.entities import InspectionTask, SecurityEvent
from property_agent.inspection.domain.enums import (
    EventAction,
    EventRiskLevel,
    EventStatus,
    EventType,
    TaskAction,
    TaskStatus,
)
from property_agent.inspection.domain.errors import BusinessError


@pytest.fixture
def task() -> InspectionTask:
    return InspectionTask(
        id=uuid.uuid4(),
        community_id=uuid.uuid4(),
        business_no="XJ-20260728-AA11BB22",
        title="夜间巡检",
        description="B1-B3 车库夜间安全巡检",
        route_points=("B1", "B2", "B3"),
        created_by=uuid.uuid4(),
        create_idempotency_key="idem-1",
    )


@pytest.fixture
def event() -> SecurityEvent:
    return SecurityEvent(
        id=uuid.uuid4(),
        community_id=uuid.uuid4(),
        business_no="AQ-20260728-AA11BB22",
        reporter_id=uuid.uuid4(),
        event_type=EventType.GAS_LEAK,
        risk_level=EventRiskLevel.HIGH_RISK,
        location="B2 车库",
        description="明显燃气气味",
        create_idempotency_key="idem-1",
    )


def test_task_state_machine_happy_path(task):
    assert task.status == TaskStatus.PLANNED
    assert TaskAction.ASSIGN in task.state_actions()
    task.assignee_id = uuid.uuid4()
    task.transition(TaskAction.ASSIGN)
    assert task.status == TaskStatus.ASSIGNED
    assert TaskAction.START in task.state_actions()
    task.transition(TaskAction.START)
    assert task.status == TaskStatus.IN_PROGRESS
    assert TaskAction.ADD_RECORD in task.state_actions()
    task.transition(TaskAction.SUBMIT_RECORDS)
    assert task.status == TaskStatus.SUBMITTED
    assert TaskAction.COMPLETE in task.state_actions()
    task.transition(TaskAction.COMPLETE)
    assert task.status == TaskStatus.COMPLETED
    assert task.closed_at is not None


def test_task_assign_requires_assignee(task):
    with pytest.raises(BusinessError) as exc:
        task.transition(TaskAction.ASSIGN)
    assert exc.value.code == "VALIDATION_ERROR"


def test_task_add_record_does_not_change_status(task):
    task.assignee_id = uuid.uuid4()
    task.transition(TaskAction.ASSIGN)
    task.transition(TaskAction.START)
    v0 = task.version
    task.touch()
    assert task.status == TaskStatus.IN_PROGRESS
    assert task.version == v0 + 1


def test_task_invalid_transition_raises(task):
    with pytest.raises(BusinessError) as exc:
        task.transition(TaskAction.COMPLETE)
    assert exc.value.code == "INVALID_TRANSITION"
    assert exc.value.status_code == 409


def test_event_state_machine_happy_path(event):
    assert event.status == EventStatus.REPORTED
    event.assignee_id = uuid.uuid4()
    event.transition(EventAction.ASSIGN)
    assert event.status == EventStatus.ASSIGNED
    event.transition(EventAction.SUBMIT_DISPOSAL)
    assert event.status == EventStatus.PENDING_REVIEW
    assert {EventAction.REVIEW_PASS, EventAction.RETURN} <= set(event.state_actions())
    event.grade_confirmed_by = uuid.uuid4()
    event.transition(EventAction.REVIEW_PASS)
    assert event.status == EventStatus.CLOSED
    assert event.closed_at is not None


def test_event_return_loops_back(event):
    event.assignee_id = uuid.uuid4()
    event.transition(EventAction.ASSIGN)
    event.transition(EventAction.SUBMIT_DISPOSAL)
    event.transition(EventAction.RETURN)
    assert event.status == EventStatus.ASSIGNED


def test_event_high_risk_requires_grade_confirmation_on_close(event):
    event.assignee_id = uuid.uuid4()
    event.transition(EventAction.ASSIGN)
    event.transition(EventAction.SUBMIT_DISPOSAL)
    # 高风险事件未经人工确认不允许关闭（PRD：高风险不可由 AI 自动闭环）
    with pytest.raises(BusinessError) as exc:
        event.transition(EventAction.REVIEW_PASS)
    assert exc.value.code == "HANDOVER_REQUIRED"
    # 人工确认后方可关闭
    event.grade_confirmed_by = uuid.uuid4()
    event.transition(EventAction.REVIEW_PASS)
    assert event.status == EventStatus.CLOSED
    assert event.grade_confirmed_by is not None
