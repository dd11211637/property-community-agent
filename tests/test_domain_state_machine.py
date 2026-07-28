from datetime import UTC, datetime
from uuid import uuid4

import pytest

from property_agent.repair.domain.entities import WorkOrder
from property_agent.repair.domain.enums import (
    ActionCode,
    RepairCategory,
    Urgency,
    WorkOrderStatus,
)
from property_agent.repair.domain.errors import BusinessError


def make_order() -> WorkOrder:
    now = datetime.now(UTC)
    return WorkOrder(
        id=uuid4(),
        community_id=uuid4(),
        business_no="WX-TEST-001",
        house_id=uuid4(),
        reporter_id=uuid4(),
        category=RepairCategory.WATER_PLUMBING,
        location="Kitchen",
        description="Water leak",
        urgency=Urgency.NORMAL,
        create_idempotency_key="create-1",
        created_at=now,
        updated_at=now,
    )


def test_happy_path_state_machine() -> None:
    order = make_order()
    order.assignee_id = uuid4()

    order.transition(ActionCode.ASSIGN)
    order.transition(ActionCode.ACCEPT)
    order.transition(ActionCode.SUBMIT_COMPLETION)
    order.transition(ActionCode.REQUEST_REWORK)
    order.transition(ActionCode.SUBMIT_REWORK_COMPLETION)
    order.transition(ActionCode.VERIFY_PASS)

    assert order.status == WorkOrderStatus.CLOSED
    assert order.version == 7
    assert order.closed_at is not None


def test_illegal_transition_reports_current_state() -> None:
    order = make_order()

    with pytest.raises(BusinessError) as error:
        order.transition(ActionCode.ACCEPT)

    assert error.value.code == "INVALID_TRANSITION"
    assert error.value.details == {
        "current_status": "PENDING_ASSIGNMENT",
        "available_actions": ["ASSIGN"],
    }


def test_reject_clears_assignee() -> None:
    order = make_order()
    order.assignee_id = uuid4()
    order.transition(ActionCode.ASSIGN)

    order.transition(ActionCode.REJECT)

    assert order.status == WorkOrderStatus.PENDING_ASSIGNMENT
    assert order.assignee_id is None
