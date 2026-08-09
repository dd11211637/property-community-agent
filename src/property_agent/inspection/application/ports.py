from collections.abc import Callable, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Protocol, Self
from uuid import UUID

from property_agent.inspection.application.commands import (
    InspectionTaskSearch,
    SecurityEventSearch,
    TimelineEntry,
)
from property_agent.inspection.domain.entities import InspectionTask, SecurityEvent
from property_agent.inspection.domain.enums import Role


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


class InspectionRepository(Protocol):
    # ---- InspectionTask ----
    def add_task(self, task: InspectionTask) -> None: ...

    def save_task(self, task: InspectionTask) -> None: ...

    def get_task(self, task_id: UUID, community_id: UUID) -> InspectionTask | None: ...

    def list_tasks(
        self, community_id: UUID, search: InspectionTaskSearch, context: RequestContext
    ) -> Sequence[InspectionTask]: ...

    def add_task_status_log(
        self,
        *,
        task_id,
        community_id,
        from_status,
        action,
        to_status,
        operator_id,
        operator_role,
        reason,
        request_id,
        created_at,
    ) -> None: ...

    def add_task_record(
        self,
        *,
        task_id,
        community_id,
        record_type,
        point,
        note,
        operator_id,
        attachment_ids,
        is_supplement,
        actual_time,
        created_at,
    ) -> None: ...

    def task_timeline(self, task_id: UUID, community_id: UUID) -> Sequence[TimelineEntry]: ...

    # ---- SecurityEvent ----
    def add_event(self, event: SecurityEvent) -> None: ...

    def save_event(self, event: SecurityEvent) -> None: ...

    def get_event(self, event_id: UUID, community_id: UUID) -> SecurityEvent | None: ...

    def list_events(
        self, community_id: UUID, search: SecurityEventSearch, context: RequestContext
    ) -> Sequence[SecurityEvent]: ...

    def add_event_status_log(
        self,
        *,
        event_id,
        community_id,
        from_status,
        action,
        to_status,
        operator_id,
        operator_role,
        reason,
        request_id,
        created_at,
    ) -> None: ...

    def add_event_disposal(
        self,
        *,
        event_id,
        community_id,
        handler_id,
        note,
        attachment_ids,
        created_at,
    ) -> None: ...

    def event_timeline(self, event_id: UUID, community_id: UUID) -> Sequence[TimelineEntry]: ...


class IdempotencyPort(Protocol):
    def get(self, actor_id: UUID, operation: str, key: str) -> IdempotencyRecord | None: ...

    def add(self, record: IdempotencyRecord) -> None: ...


class ConfirmationPort(Protocol):
    def consume(
        self, *, token: str, actor_id: UUID, action: str, parameter_hash: str, request_id: str
    ) -> None: ...


class StaffDirectoryPort(Protocol):
    def ensure_security_staff(
        self, *, user_id: UUID, community_id: UUID, request_id: str
    ) -> None: ...

    def list_duty_users(self, community_id: UUID) -> Sequence[UUID]: ...


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
        community_id,
        actor_id,
        action,
        resource_type,
        resource_id,
        parameter_summary,
        request_id,
        created_at,
    ) -> None: ...


class MessagePort(Protocol):
    def enqueue(
        self, *, community_id, receiver_id, event_type, resource_id, request_id, created_at
    ) -> None: ...


class EscalationPort(Protocol):
    """高风险通知失败/无可用值班人员时的升级与备用联系人（PRD 6.4）。"""

    def escalate_high_risk(
        self, *, community_id, event_id, event_business_no, reason, summary, request_id, created_at
    ) -> UUID: ...


@dataclass(frozen=True, slots=True)
class SharedPorts:
    idempotency: IdempotencyPort
    confirmations: ConfirmationPort
    staff_directory: StaffDirectoryPort
    attachments: AttachmentPort
    audit: AuditPort
    messages: MessagePort
    escalation: EscalationPort


SharedPortFactory = Callable[..., SharedPorts]


class InspectionUnitOfWork(Protocol):
    repository: InspectionRepository
    idempotency: IdempotencyPort
    confirmations: ConfirmationPort
    staff_directory: StaffDirectoryPort
    attachments: AttachmentPort
    audit: AuditPort
    messages: MessagePort
    escalation: EscalationPort

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
