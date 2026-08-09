from uuid import uuid4

import pytest

from property_agent.repair.application.commands import (
    CreateReviewCommand,
    CreateWorkOrderCommand,
    ExecuteActionCommand,
    WorkOrderSearch,
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
from tests.conftest import Ids


def create_command(ids: Ids, **overrides) -> CreateWorkOrderCommand:
    values = {
        "house_id": ids.house,
        "category": RepairCategory.WATER_PLUMBING,
        "location": "Kitchen",
        "description": "Pipe is leaking",
        "urgency": Urgency.NORMAL,
        "confirmation_token": "confirmed",
    }
    values.update(overrides)
    return CreateWorkOrderCommand(**values)


def create_order(
    service: WorkOrderService,
    ids: Ids,
    resident_context: RequestContext,
    *,
    key: str = "create-1",
):
    return service.create(
        create_command(ids), resident_context, idempotency_key=key
    )


def test_create_is_confirmed_and_idempotent(
    service, harness, ids, resident_context
) -> None:
    first = create_order(service, ids, resident_context)
    second = create_order(service, ids, resident_context)

    assert first.id == second.id
    assert first.status == WorkOrderStatus.PENDING_ASSIGNMENT
    assert len(harness.state.orders) == 1
    assert len(harness.confirmations.consumed) == 1
    assert len(harness.state.status_logs) == 1


def test_same_idempotency_key_with_other_parameters_conflicts(
    service, ids, resident_context
) -> None:
    create_order(service, ids, resident_context)

    with pytest.raises(BusinessError) as error:
        service.create(
            create_command(ids, location="Bathroom"),
            resident_context,
            idempotency_key="create-1",
        )

    assert error.value.code == "IDEMPOTENCY_CONFLICT"


def test_high_risk_does_not_create_order(service, harness, ids, resident_context) -> None:
    with pytest.raises(BusinessError) as error:
        service.create(
            create_command(ids, urgency=Urgency.HIGH_RISK),
            resident_context,
            idempotency_key="high-risk",
        )

    assert error.value.code == "HANDOVER_REQUIRED"
    assert harness.state.orders == {}


def test_invalid_confirmation_does_not_create_order(
    service, harness, ids, resident_context
) -> None:
    with pytest.raises(BusinessError) as error:
        service.create(
            create_command(ids, confirmation_token="bad-token"),
            resident_context,
            idempotency_key="invalid-confirmation",
        )

    assert error.value.code == "CONFIRMATION_INVALID"
    assert harness.state.orders == {}


def test_complete_rework_and_review_flow(
    service,
    harness,
    ids,
    resident_context,
    customer_service_context,
    repair_context,
) -> None:
    order = create_order(service, ids, resident_context)
    order = service.execute_action(
        order.id,
        ExecuteActionCommand(
            action=ActionCode.ASSIGN,
            expected_version=order.version,
            assignee_id=ids.repair_worker,
        ),
        customer_service_context,
        idempotency_key="assign-1",
    )
    order = service.execute_action(
        order.id,
        ExecuteActionCommand(
            action=ActionCode.ACCEPT, expected_version=order.version
        ),
        repair_context,
        idempotency_key="accept-1",
    )
    order = service.execute_action(
        order.id,
        ExecuteActionCommand(
            action=ActionCode.SUBMIT_COMPLETION,
            expected_version=order.version,
            note="Replaced connector and tested.",
        ),
        repair_context,
        idempotency_key="complete-1",
    )
    order = service.execute_action(
        order.id,
        ExecuteActionCommand(
            action=ActionCode.REQUEST_REWORK,
            expected_version=order.version,
            reason="Leak remains.",
        ),
        resident_context,
        idempotency_key="rework-1",
    )
    order = service.execute_action(
        order.id,
        ExecuteActionCommand(
            action=ActionCode.SUBMIT_REWORK_COMPLETION,
            expected_version=order.version,
            note="Replaced the full valve and retested.",
        ),
        repair_context,
        idempotency_key="complete-2",
    )
    order = service.execute_action(
        order.id,
        ExecuteActionCommand(
            action=ActionCode.VERIFY_PASS, expected_version=order.version
        ),
        resident_context,
        idempotency_key="verify-1",
    )
    order = service.create_review(
        order.id,
        CreateReviewCommand(rating=5, comment="Resolved"),
        resident_context,
        idempotency_key="review-1",
    )

    assert order.status == WorkOrderStatus.CLOSED
    assert order.has_review is True
    assert len(harness.state.process_records) == 2
    assert [item.note for item in harness.state.process_records] == [
        "Replaced connector and tested.",
        "Replaced the full valve and retested.",
    ]
    assert harness.state.reviews[order.id]["rating"] == 5


def test_wrong_worker_cannot_accept(
    service,
    ids,
    resident_context,
    customer_service_context,
) -> None:
    order = create_order(service, ids, resident_context)
    order = service.execute_action(
        order.id,
        ExecuteActionCommand(
            action=ActionCode.ASSIGN,
            expected_version=order.version,
            assignee_id=ids.repair_worker,
        ),
        customer_service_context,
        idempotency_key="assign-1",
    )
    other_worker = RequestContext(
        actor_id=ids.other_worker,
        community_id=ids.community,
        roles=frozenset({Role.REPAIR_WORKER}),
        request_id="req_other_worker",
    )

    with pytest.raises(BusinessError) as error:
        service.execute_action(
            order.id,
            ExecuteActionCommand(
                action=ActionCode.ACCEPT, expected_version=order.version
            ),
            other_worker,
            idempotency_key="accept-other",
        )

    assert error.value.code == "RESOURCE_NOT_FOUND"


def test_stale_version_is_rejected(
    service,
    ids,
    resident_context,
    customer_service_context,
) -> None:
    order = create_order(service, ids, resident_context)

    with pytest.raises(BusinessError) as error:
        service.execute_action(
            order.id,
            ExecuteActionCommand(
                action=ActionCode.ASSIGN,
                expected_version=99,
                assignee_id=ids.repair_worker,
            ),
            customer_service_context,
            idempotency_key="assign-stale",
        )

    assert error.value.code == "VERSION_CONFLICT"


def test_action_idempotency_returns_original_response_snapshot(
    service,
    ids,
    resident_context,
    customer_service_context,
    repair_context,
) -> None:
    order = create_order(service, ids, resident_context)
    assignment = ExecuteActionCommand(
        action=ActionCode.ASSIGN,
        expected_version=order.version,
        assignee_id=ids.repair_worker,
    )
    assigned = service.execute_action(
        order.id,
        assignment,
        customer_service_context,
        idempotency_key="assign-snapshot",
    )
    assigned_version = assigned.version
    service.execute_action(
        order.id,
        ExecuteActionCommand(
            action=ActionCode.ACCEPT,
            expected_version=assigned_version,
        ),
        repair_context,
        idempotency_key="accept-after-assignment",
    )

    replay = service.execute_action(
        order.id,
        assignment,
        customer_service_context,
        idempotency_key="assign-snapshot",
    )

    assert replay.status == WorkOrderStatus.PENDING_ACCEPTANCE
    assert replay.version == assigned_version


def test_resident_cannot_access_other_house(service, harness, ids) -> None:
    other_resident = RequestContext(
        actor_id=uuid4(),
        community_id=ids.community,
        roles=frozenset({Role.RESIDENT}),
        house_ids=frozenset({ids.other_house}),
        request_id="req_other_resident",
    )
    harness.house_access.allowed_houses.add(ids.other_house)
    order = service.create(
        create_command(ids, house_id=ids.other_house),
        other_resident,
        idempotency_key="other-create",
    )
    original_resident = RequestContext(
        actor_id=ids.resident,
        community_id=ids.community,
        roles=frozenset({Role.RESIDENT}),
        house_ids=frozenset({ids.house}),
        request_id="req_original",
    )

    with pytest.raises(BusinessError) as error:
        service.get(order.id, original_resident)

    assert error.value.code == "RESOURCE_NOT_FOUND"


def test_read_operations_require_an_explicit_role(
    service, ids, resident_context
) -> None:
    order = service.create(
        create_command(ids),
        resident_context,
        idempotency_key="role-required-create",
    )
    roleless_context = RequestContext(
        actor_id=uuid4(),
        community_id=ids.community,
        roles=frozenset(),
        request_id="req_roleless",
    )

    with pytest.raises(BusinessError) as get_error:
        service.get(order.id, roleless_context)
    with pytest.raises(BusinessError) as search_error:
        service.search(WorkOrderSearch(), roleless_context)

    assert get_error.value.code == "FORBIDDEN"
    assert search_error.value.code == "FORBIDDEN"


def test_whitespace_idempotency_key_is_rejected(
    service, ids, resident_context
) -> None:
    with pytest.raises(BusinessError) as error:
        service.create(
            create_command(ids),
            resident_context,
            idempotency_key="   ",
        )

    assert error.value.code == "VALIDATION_ERROR"


def test_request_context_rejects_invalid_request_id(ids) -> None:
    with pytest.raises(ValueError, match="request_id"):
        RequestContext(
            actor_id=ids.resident,
            community_id=ids.community,
            roles=frozenset({Role.RESIDENT}),
            request_id="x" * 65,
        )
