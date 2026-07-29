from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Any, Protocol, Self
from uuid import UUID

from property_agent.repair.application.commands import TimelineEntry, WorkOrderSearch
from property_agent.repair.domain.entities import WorkOrder
from property_agent.repair.domain.enums import ActionCode, ProcessRecordType, Role


@dataclass(frozen=True, slots=True)
class RequestContext:
    actor_id: UUID
    community_id: UUID
    roles: frozenset[Role]
    request_id: str
    house_ids: frozenset[UUID] = frozenset()

    def __post_init__(self) -> None:
        if not self.request_id.strip() or len(self.request_id) > 64:
            raise ValueError("request_id must contain 1 to 64 non-whitespace characters.")

    def has_any_role(self, *roles: Role) -> bool:
        return bool(self.roles.intersection(roles))


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    actor_id: UUID
    operation: str
    key: str
    request_hash: str
    resource_id: UUID
    response_snapshot: dict[str, Any]


class WorkOrderRepository(Protocol):
    def add(self, work_order: WorkOrder) -> None: ...

    def save(self, work_order: WorkOrder) -> None: ...

    def get(self, work_order_id: UUID, community_id: UUID) -> WorkOrder | None: ...

    def list(
        self, community_id: UUID, search: WorkOrderSearch, context: RequestContext
    ) -> Sequence[WorkOrder]: ...

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
    ) -> None: ...

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
    ) -> None: ...

    def add_review(
        self,
        *,
        work_order_id: UUID,
        community_id: UUID,
        reviewer_id: UUID,
        rating: int,
        comment: str | None,
        created_at: datetime,
    ) -> None: ...

    def timeline(self, work_order_id: UUID, community_id: UUID) -> Sequence[TimelineEntry]: ...


class IdempotencyPort(Protocol):
    def get(self, actor_id: UUID, operation: str, key: str) -> IdempotencyRecord | None: ...

    def add(self, record: IdempotencyRecord) -> None: ...


class ConfirmationPort(Protocol):
    def consume(
        self,
        *,
        token: str,
        actor_id: UUID,
        action: str,
        parameter_hash: str,
        request_id: str,
    ) -> None: ...


class HouseAccessPort(Protocol):
    def ensure_access(
        self, *, actor_id: UUID, community_id: UUID, house_id: UUID, request_id: str
    ) -> None: ...


class StaffDirectoryPort(Protocol):
    def ensure_repair_worker(
        self, *, user_id: UUID, community_id: UUID, request_id: str
    ) -> None: ...


class AttachmentPort(Protocol):
    def ensure_usable(
        self,
        *,
        attachment_ids: tuple[UUID, ...],
        actor_id: UUID,
        community_id: UUID,
        request_id: str,
    ) -> None: ...


class AuditPort(Protocol):
    def add(
        self,
        *,
        community_id: UUID,
        actor_id: UUID,
        action: str,
        resource_type: str,
        resource_id: UUID,
        parameter_summary: dict[str, Any],
        request_id: str,
        created_at: datetime,
    ) -> None: ...


class MessagePort(Protocol):
    def enqueue(
        self,
        *,
        community_id: UUID,
        receiver_id: UUID,
        event_type: str,
        resource_id: UUID,
        request_id: str,
        created_at: datetime,
    ) -> None: ...


class RepairUnitOfWork(Protocol):
    work_orders: WorkOrderRepository
    idempotency: IdempotencyPort
    confirmations: ConfirmationPort
    house_access: HouseAccessPort
    staff_directory: StaffDirectoryPort
    attachments: AttachmentPort
    audit: AuditPort
    messages: MessagePort

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


UnitOfWorkFactory = Callable[[], RepairUnitOfWork]
