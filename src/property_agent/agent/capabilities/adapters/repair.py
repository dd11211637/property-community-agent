"""Typed repair capability adapters to the existing WorkOrderService."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from uuid import UUID

from pydantic import Field

from property_agent.agent.capabilities.contracts import (
    CapabilityDomainError,
    CapabilityInput,
    CapabilityOutput,
    CapabilityRuntimeContext,
)
from property_agent.repair.application.commands import CreateWorkOrderCommand, WorkOrderSearch
from property_agent.repair.domain.classification import classify_repair_category
from property_agent.repair.domain.enums import Urgency
from property_agent.repair.domain.errors import BusinessError as RepairBusinessError


@contextmanager
def _translate_public_repair_errors():
    try:
        yield
    except RepairBusinessError as exc:
        raise CapabilityDomainError(exc.code, exc.message, details=dict(exc.details or {})) from exc


class WorkOrderBrief(CapabilityOutput):
    entity_type: str = "WORK_ORDER"
    id: str
    business_no: str | None = None
    status: str
    category: str
    location: str | None = None
    urgency: str
    assignee_id: str | None = None
    version: int | None = None


class WorkOrderTimelineItem(CapabilityOutput):
    action: str
    from_status: str | None = None
    to_status: str | None = None
    note: str | None = None
    reason: str | None = None
    created_at: str


class RepairListInput(CapabilityInput):
    statuses: tuple[str, ...] = ()
    limit: int = Field(default=20, ge=1, le=100)


class RepairListOutput(CapabilityOutput):
    count: int = Field(ge=0)
    items: tuple[WorkOrderBrief, ...]


class RepairGetInput(CapabilityInput):
    work_order_id: str = Field(min_length=1, max_length=64)


class RepairGetOutput(CapabilityOutput):
    work_order: WorkOrderBrief
    timeline: tuple[WorkOrderTimelineItem, ...]


class RepairCreateInput(CapabilityInput):
    description: str = Field(min_length=1, max_length=2000)
    location: str = Field(min_length=1, max_length=255)
    urgency: str = "NORMAL"


class RepairCreateOutput(CapabilityOutput):
    work_order: WorkOrderBrief
    idempotency_key: str


def _brief(work_order: Any) -> WorkOrderBrief:
    return WorkOrderBrief(
        id=str(work_order.id),
        business_no=getattr(work_order, "business_no", None),
        status=str(getattr(work_order, "status", "")),
        category=str(getattr(work_order, "category", "")),
        location=getattr(work_order, "location", None),
        urgency=str(getattr(work_order, "urgency", "")),
        assignee_id=str(work_order.assignee_id) if work_order.assignee_id else None,
        version=getattr(work_order, "version", None),
    )


def normalize_repair_urgency(value: Any) -> Urgency:
    if isinstance(value, Urgency):
        return value
    try:
        return Urgency(str(value).upper())
    except ValueError:
        return Urgency.NORMAL


class RepairListAdapter:
    def __init__(self, service: Any) -> None:
        self._service = service

    def __call__(
        self, request: RepairListInput, runtime: CapabilityRuntimeContext
    ) -> RepairListOutput:
        search = WorkOrderSearch(
            house_id=runtime.current_house_id,
            statuses=request.statuses,
            limit=request.limit,
        )
        with _translate_public_repair_errors():
            items = self._service.search(search, runtime.request_context)
        return RepairListOutput(count=len(items), items=tuple(_brief(item) for item in items))


class RepairGetAdapter:
    def __init__(self, service: Any) -> None:
        self._service = service

    def __call__(
        self, request: RepairGetInput, runtime: CapabilityRuntimeContext
    ) -> RepairGetOutput:
        value = request.work_order_id.strip()
        with _translate_public_repair_errors():
            work_order = self._resolve(value, runtime)
            timeline = self._service.timeline(work_order.id, runtime.request_context)
        return RepairGetOutput(
            work_order=_brief(work_order),
            timeline=tuple(
                WorkOrderTimelineItem(
                    action=item.action,
                    from_status=item.from_status,
                    to_status=item.to_status,
                    note=item.note,
                    reason=item.reason,
                    created_at=item.created_at.isoformat(),
                )
                for item in timeline
            ),
        )

    def _resolve(self, value: str, runtime: CapabilityRuntimeContext) -> Any:
        if value.upper().startswith("WX-"):
            work_order = next(
                (
                    item
                    for item in self._service.search(
                        WorkOrderSearch(limit=100), runtime.request_context
                    )
                    if str(getattr(item, "business_no", "")).upper() == value.upper()
                ),
                None,
            )
            if work_order is None:
                raise CapabilityDomainError(
                    "WORK_ORDER_NOT_FOUND", "没有找到该工单，请核对工单号。"
                )
            return work_order
        try:
            return self._service.get(UUID(value), runtime.request_context)
        except ValueError as exc:
            raise CapabilityDomainError(
                "INVALID_WORK_ORDER_NUMBER",
                "工单号格式不正确，请输入 WX- 开头的完整工单号。",
            ) from exc


class RepairCreateAdapter:
    def __init__(self, service: Any) -> None:
        self._service = service

    def __call__(
        self, request: RepairCreateInput, runtime: CapabilityRuntimeContext
    ) -> RepairCreateOutput:
        if runtime.current_house_id is None:
            raise CapabilityDomainError(
                "CURRENT_HOUSE_REQUIRED", "repair_create 需要先选择当前房屋"
            )
        if runtime.write is None:
            raise RuntimeError("repair_create requires server write context")
        category = classify_repair_category(request.description)
        command = CreateWorkOrderCommand(
            house_id=runtime.current_house_id,
            category=category,
            location=request.location,
            description=request.description,
            urgency=normalize_repair_urgency(request.urgency),
            confirmation_token=runtime.write.confirmation_token,
            approval_ref=runtime.write.approval_ref,
        )
        with _translate_public_repair_errors():
            work_order = self._service.create(
                command,
                runtime.request_context,
                idempotency_key=runtime.write.idempotency_key,
            )
        return RepairCreateOutput(
            work_order=_brief(work_order), idempotency_key=runtime.write.idempotency_key
        )
