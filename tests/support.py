from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from property_agent.repair.application.commands import TimelineEntry, WorkOrderSearch
from property_agent.repair.application.ports import (
    IdempotencyRecord,
    RequestContext,
)
from property_agent.repair.domain.entities import WorkOrder
from property_agent.repair.domain.enums import ActionCode, ProcessRecordType, Role
from property_agent.repair.domain.errors import BusinessError, forbidden


@dataclass
class FakeState:
    orders: dict[UUID, WorkOrder] = field(default_factory=dict)
    status_logs: list[TimelineEntry] = field(default_factory=list)
    process_records: list[TimelineEntry] = field(default_factory=list)
    reviews: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    idempotency: dict[tuple[UUID, str, str], IdempotencyRecord] = field(
        default_factory=dict
    )
    audits: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    handovers: list[dict[str, Any]] = field(default_factory=list)


class FakeWorkOrderRepository:
    def __init__(self, state: FakeState) -> None:
        self.state = state

    def add(self, work_order: WorkOrder) -> None:
        self.state.orders[work_order.id] = work_order

    def save(self, work_order: WorkOrder) -> None:
        self.state.orders[work_order.id] = work_order

    def get(self, work_order_id: UUID, community_id: UUID) -> WorkOrder | None:
        order = self.state.orders.get(work_order_id)
        return order if order and order.community_id == community_id else None

    def list(
        self, community_id: UUID, search: WorkOrderSearch, context: RequestContext
    ) -> list[WorkOrder]:
        results = [
            item
            for item in self.state.orders.values()
            if item.community_id == community_id
        ]
        if context.has_any_role(Role.RESIDENT) and not context.has_any_role(
            Role.CUSTOMER_SERVICE, Role.MANAGER
        ):
            results = [item for item in results if item.house_id in context.house_ids]
        if context.has_any_role(Role.REPAIR_WORKER) and not context.has_any_role(
            Role.CUSTOMER_SERVICE, Role.MANAGER
        ):
            results = [item for item in results if item.assignee_id == context.actor_id]
        if search.house_id:
            results = [item for item in results if item.house_id == search.house_id]
        if search.statuses:
            results = [item for item in results if item.status.value in search.statuses]
        if search.assigned_to_me:
            results = [item for item in results if item.assignee_id == context.actor_id]
        return results[search.offset : search.offset + search.limit]

    def add_status_log(
        self,
        *,
        work_order_id: UUID,
        community_id: UUID,
        from_status: str | None,
        action: ActionCode,
        to_status: str,
        operator_id: UUID,
        operator_role: Role,
        reason: str | None,
        request_id: str,
        created_at: datetime,
    ) -> None:
        self.state.status_logs.append(
            TimelineEntry(
                entry_type="STATUS",
                action=action.value,
                operator_id=operator_id,
                created_at=created_at,
                from_status=from_status,
                to_status=to_status,
                reason=reason,
            )
        )

    def add_process_record(
        self,
        *,
        work_order_id: UUID,
        community_id: UUID,
        record_type: ProcessRecordType,
        note: str,
        operator_id: UUID,
        appointment_at: datetime | None,
        attachment_ids: tuple[UUID, ...],
        created_at: datetime,
    ) -> None:
        self.state.process_records.append(
            TimelineEntry(
                entry_type="PROCESS",
                action=record_type.value,
                operator_id=operator_id,
                created_at=created_at,
                note=note,
                attachment_ids=attachment_ids,
            )
        )

    def add_review(
        self,
        *,
        work_order_id: UUID,
        community_id: UUID,
        reviewer_id: UUID,
        rating: int,
        comment: str | None,
        created_at: datetime,
    ) -> None:
        self.state.reviews[work_order_id] = {
            "reviewer_id": reviewer_id,
            "rating": rating,
            "comment": comment,
            "created_at": created_at,
        }

    def timeline(self, work_order_id: UUID, community_id: UUID) -> list[TimelineEntry]:
        return sorted(
            [*self.state.status_logs, *self.state.process_records],
            key=lambda item: item.created_at,
        )


class FakeIdempotency:
    def __init__(self, state: FakeState) -> None:
        self.state = state

    def get(self, actor_id: UUID, operation: str, key: str) -> IdempotencyRecord | None:
        return self.state.idempotency.get((actor_id, operation, key))

    def add(self, record: IdempotencyRecord) -> None:
        self.state.idempotency[(record.actor_id, record.operation, record.key)] = record


class FakeConfirmation:
    def __init__(self) -> None:
        self.consumed: list[str] = []

    def consume(
        self,
        *,
        token: str,
        actor_id: UUID,
        action: str,
        parameter_hash: str,
        request_id: str,
    ) -> None:
        if token != "confirmed":
            raise BusinessError("CONFIRMATION_INVALID", "Confirmation is invalid.", 422)
        if token in self.consumed:
            raise BusinessError("CONFIRMATION_INVALID", "Confirmation was consumed.", 422)
        self.consumed.append(token)


class FakeHouseAccess:
    def __init__(self, allowed_houses: set[UUID]) -> None:
        self.allowed_houses = allowed_houses

    def ensure_access(
        self, *, actor_id: UUID, community_id: UUID, house_id: UUID, request_id: str
    ) -> None:
        if house_id not in self.allowed_houses:
            raise forbidden()


class FakeStaffDirectory:
    def __init__(self, repair_workers: set[UUID], duty_staff: set[UUID] | None = None) -> None:
        self.repair_workers = repair_workers
        self.duty_staff = duty_staff if duty_staff is not None else set()

    def ensure_repair_worker(
        self, *, user_id: UUID, community_id: UUID, request_id: str
    ) -> None:
        if user_id not in self.repair_workers:
            raise forbidden()

    def list_duty_staff(self, *, community_id: UUID, request_id: str) -> tuple[UUID, ...]:
        return tuple(sorted(self.duty_staff, key=str))


class FakeAttachments:
    def ensure_usable(
        self,
        *,
        attachment_ids: tuple[UUID, ...],
        actor_id: UUID,
        community_id: UUID,
        request_id: str,
    ) -> None:
        return None


class FakeAudit:
    def __init__(self, state: FakeState, *, fail: bool = False) -> None:
        self.state = state
        self.fail = fail

    def add(self, **event: Any) -> None:
        if self.fail:
            raise RuntimeError("simulated audit failure")
        self.state.audits.append(event)


class FakeMessages:
    def __init__(self, state: FakeState) -> None:
        self.state = state

    def enqueue(self, **event: Any) -> None:
        self.state.messages.append(event)


class FakeHandover:
    def __init__(self, state: FakeState) -> None:
        self.state = state

    def create(self, **ticket: Any) -> UUID:
        ticket_id = uuid4()
        self.state.handovers.append({"id": ticket_id, **ticket})
        return ticket_id


class FakeUnitOfWork:
    def __init__(self, harness: Harness) -> None:
        self.work_orders = FakeWorkOrderRepository(harness.state)
        self.idempotency = FakeIdempotency(harness.state)
        self.confirmations = harness.confirmations
        self.house_access = harness.house_access
        self.staff_directory = harness.staff_directory
        self.attachments = harness.attachments
        self.audit = harness.audit
        self.messages = harness.messages
        self.handover = harness.handover
        self.committed = False

    def __enter__(self) -> FakeUnitOfWork:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type:
            self.rollback()

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.committed = False


class Harness:
    def __init__(
        self,
        *,
        houses: set[UUID],
        repair_workers: set[UUID],
        duty_staff: set[UUID] | None = None,
    ) -> None:
        self.state = FakeState()
        self.confirmations = FakeConfirmation()
        self.house_access = FakeHouseAccess(houses)
        self.staff_directory = FakeStaffDirectory(repair_workers, duty_staff)
        self.attachments = FakeAttachments()
        self.audit = FakeAudit(self.state)
        self.messages = FakeMessages(self.state)
        self.handover = FakeHandover(self.state)

    def uow(self) -> FakeUnitOfWork:
        return FakeUnitOfWork(self)
