from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from property_agent.repair.adapters.presentation import work_order_data
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
    ProcessRecordType,
    RepairCategory,
    Urgency,
)


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchWorkOrdersInput(ToolInput):
    house_id: UUID | None = None
    statuses: list[str] = Field(default_factory=list)
    assigned_to_me: bool = False
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class CreateWorkOrderInput(ToolInput):
    house_id: UUID
    category: RepairCategory
    location: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1)
    urgency: Urgency
    confirmation_token: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=128)
    attachment_ids: list[UUID] = Field(default_factory=list)


class StateTransitionActionInput(ToolInput):
    work_order_id: UUID
    action: Literal[
        "ASSIGN",
        "ACCEPT",
        "REJECT",
        "RECORD_PROGRESS",
        "SUBMIT_COMPLETION",
        "SUBMIT_REWORK_COMPLETION",
        "VERIFY_PASS",
        "REQUEST_REWORK",
    ]
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)
    assignee_id: UUID | None = None
    reason: str | None = None
    note: str | None = None
    record_type: ProcessRecordType | None = None
    appointment_at: datetime | None = None
    attachment_ids: list[UUID] = Field(default_factory=list)


class CreateReviewActionInput(ToolInput):
    work_order_id: UUID
    action: Literal["CREATE_REVIEW"]
    idempotency_key: str = Field(min_length=1, max_length=128)
    rating: int = Field(ge=1, le=5)
    comment: str | None = None


ExecuteWorkOrderActionInput = Annotated[
    StateTransitionActionInput | CreateReviewActionInput,
    Field(discriminator="action"),
]
EXECUTE_ACTION_INPUT_ADAPTER = TypeAdapter(ExecuteWorkOrderActionInput)


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_work_orders": {
        "name": "search_work_orders",
        "description": "Search authorized repair work orders using structured filters.",
        "parameters": SearchWorkOrdersInput.model_json_schema(),
    },
    "create_work_order": {
        "name": "create_work_order",
        "description": (
            "Create a repair work order after all fields and the user's confirmation "
            "have already been collected."
        ),
        "parameters": CreateWorkOrderInput.model_json_schema(),
    },
    "execute_work_order_action": {
        "name": "execute_work_order_action",
        "description": "Execute an authorized action against the current work-order state.",
        "parameters": EXECUTE_ACTION_INPUT_ADAPTER.json_schema(),
    },
}


class RepairToolAdapter:
    """Framework-neutral tools; this class performs no intent detection or routing."""

    def __init__(self, service: WorkOrderService) -> None:
        self._service = service

    def search_work_orders(
        self, arguments: dict[str, Any], context: RequestContext
    ) -> dict[str, Any]:
        payload = SearchWorkOrdersInput.model_validate(arguments)
        results = self._service.search(
            WorkOrderSearch(
                house_id=payload.house_id,
                statuses=tuple(payload.statuses),
                assigned_to_me=payload.assigned_to_me,
                limit=payload.limit,
                offset=payload.offset,
            ),
            context,
        )
        return {
            "items": [work_order_data(item, self._service, context) for item in results],
            "limit": payload.limit,
            "offset": payload.offset,
        }

    def create_work_order(
        self, arguments: dict[str, Any], context: RequestContext
    ) -> dict[str, Any]:
        payload = CreateWorkOrderInput.model_validate(arguments)
        result = self._service.create(
            CreateWorkOrderCommand(
                house_id=payload.house_id,
                category=payload.category,
                location=payload.location,
                description=payload.description,
                urgency=payload.urgency,
                confirmation_token=payload.confirmation_token,
                attachment_ids=tuple(payload.attachment_ids),
            ),
            context,
            idempotency_key=payload.idempotency_key,
        )
        return work_order_data(result, self._service, context)

    def execute_work_order_action(
        self, arguments: dict[str, Any], context: RequestContext
    ) -> dict[str, Any]:
        payload = EXECUTE_ACTION_INPUT_ADAPTER.validate_python(arguments)
        if isinstance(payload, CreateReviewActionInput):
            result = self._service.create_review(
                payload.work_order_id,
                CreateReviewCommand(rating=payload.rating, comment=payload.comment),
                context,
                idempotency_key=payload.idempotency_key,
            )
            return work_order_data(result, self._service, context)
        result = self._service.execute_action(
            payload.work_order_id,
            ExecuteActionCommand(
                action=ActionCode(payload.action),
                expected_version=payload.expected_version,
                assignee_id=payload.assignee_id,
                reason=payload.reason,
                note=payload.note,
                record_type=payload.record_type,
                appointment_at=payload.appointment_at,
                attachment_ids=tuple(payload.attachment_ids),
            ),
            context,
            idempotency_key=payload.idempotency_key,
        )
        return work_order_data(result, self._service, context)
