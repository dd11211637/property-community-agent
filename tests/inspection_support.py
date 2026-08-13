from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from property_agent.inspection.application.commands import TimelineEntry
from property_agent.inspection.application.ports import (
    AttachmentPort,
    AuditPort,
    ConfirmationPort,
    EscalationPort,
    IdempotencyPort,
    IdempotencyRecord,
    MessagePort,
    RequestContext,
    SharedPorts,
    StaffDirectoryPort,
)
from property_agent.inspection.application.service import (
    EVENT_HANDLER_ROLES,
    EVENT_REVIEW_ROLES,
    TASK_ASSIGN_ROLES,
    TASK_ASSIGNEE_ROLES,
)
from property_agent.inspection.domain.entities import InspectionTask, SecurityEvent
from property_agent.inspection.domain.enums import TaskStatus
from property_agent.inspection.domain.errors import forbidden


@dataclass
class FakeState:
    tasks: dict[UUID, InspectionTask] = field(default_factory=dict)
    events: dict[UUID, SecurityEvent] = field(default_factory=dict)
    task_records: list[dict[str, Any]] = field(default_factory=list)
    event_disposals: list[dict[str, Any]] = field(default_factory=list)
    task_status_logs: list[dict[str, Any]] = field(default_factory=list)
    event_status_logs: list[dict[str, Any]] = field(default_factory=list)
    idempotency: dict[tuple[UUID, str, str], IdempotencyRecord] = field(default_factory=dict)
    audits: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    confirmed: list[tuple[str, str, str]] = field(default_factory=list)  # (token, action, hash)
    escalations: list[dict[str, Any]] = field(default_factory=list)  # 高风险升级兜底记录


class FakeInspectionRepository:
    def __init__(self, state: FakeState) -> None:
        self.state = state

    # ---- tasks ----
    def add_task(self, task: InspectionTask) -> None:
        self.state.tasks[task.id] = task

    def save_task(self, task: InspectionTask) -> None:
        self.state.tasks[task.id] = task

    def get_task(self, task_id: UUID, community_id: UUID) -> InspectionTask | None:
        task = self.state.tasks.get(task_id)
        return task if task and task.community_id == community_id else None

    def list_tasks(
        self, community_id: UUID, search, context: RequestContext
    ) -> list[InspectionTask]:
        results = [t for t in self.state.tasks.values() if t.community_id == community_id]
        if search.statuses:
            results = [t for t in results if t.status.value in search.statuses]
        if search.assigned_to_me:
            results = [t for t in results if t.assignee_id == context.actor_id]
        if context.has_any_role(*TASK_ASSIGNEE_ROLES) and not context.has_any_role(
            *TASK_ASSIGN_ROLES
        ):
            results = [t for t in results if t.assignee_id == context.actor_id]
        return results[search.offset : search.offset + search.limit]

    def aggregate_task_statuses(self, community_id, search, context) -> dict[str, int]:
        unpaged = type(search)(
            statuses=search.statuses,
            assigned_to_me=search.assigned_to_me,
            limit=100,
            offset=0,
        )
        counts: dict[str, int] = {}
        for task in self.list_tasks(community_id, unpaged, context):
            counts[task.status.value] = counts.get(task.status.value, 0) + 1
        return counts

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
    ) -> None:
        self.state.task_status_logs.append(
            {
                "task_id": task_id,
                "community_id": community_id,
                "from_status": from_status,
                "action_code": action.value if hasattr(action, "value") else action,
                "to_status": to_status,
                "operator_id": operator_id,
                "operator_role": operator_role,
                "reason": reason,
                "request_id": request_id,
                "created_at": created_at,
            }
        )

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
        supplement_reason,
        created_at,
    ) -> None:
        self.state.task_records.append(
            {
                "task_id": task_id,
                "community_id": community_id,
                "record_type": record_type.value if hasattr(record_type, "value") else record_type,
                "point": point,
                "note": note,
                "operator_id": operator_id,
                "attachment_ids": list(attachment_ids),
                "is_supplement": is_supplement,
                "actual_time": actual_time,
                "supplement_reason": supplement_reason,
                "created_at": created_at,
            }
        )

    def find_active_tasks(self, community_id: UUID) -> list[InspectionTask]:
        return [
            t
            for t in self.state.tasks.values()
            if t.community_id == community_id
            and t.status in (TaskStatus.PLANNED, TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS)
        ]

    def task_timeline(self, task_id: UUID, community_id: UUID) -> list[TimelineEntry]:
        entries: list[TimelineEntry] = []
        for log in self.state.task_status_logs:
            if log["task_id"] == task_id and log["community_id"] == community_id:
                entries.append(
                    TimelineEntry(
                        entry_type="STATUS",
                        action=log["action_code"],
                        operator_id=log["operator_id"],
                        created_at=log["created_at"],
                        from_status=log["from_status"],
                        to_status=log["to_status"],
                        reason=log["reason"],
                    )
                )
        for rec in self.state.task_records:
            if rec["task_id"] == task_id and rec["community_id"] == community_id:
                entries.append(
                    TimelineEntry(
                        entry_type="RECORD",
                        action=rec["record_type"],
                        operator_id=rec["operator_id"],
                        created_at=rec["created_at"],
                        note=rec["note"],
                        attachment_ids=tuple(UUID(v) for v in rec["attachment_ids"]),
                    )
                )
        return sorted(entries, key=lambda e: e.created_at)

    # ---- events ----
    def add_event(self, event: SecurityEvent) -> None:
        self.state.events[event.id] = event

    def save_event(self, event: SecurityEvent) -> None:
        self.state.events[event.id] = event

    def get_event(self, event_id: UUID, community_id: UUID) -> SecurityEvent | None:
        event = self.state.events.get(event_id)
        return event if event and event.community_id == community_id else None

    def list_events(
        self, community_id: UUID, search, context: RequestContext
    ) -> list[SecurityEvent]:
        results = [e for e in self.state.events.values() if e.community_id == community_id]
        if search.statuses:
            results = [e for e in results if e.status.value in search.statuses]
        if search.risk_levels:
            results = [e for e in results if e.risk_level.value in search.risk_levels]
        if search.assigned_to_me:
            results = [e for e in results if e.assignee_id == context.actor_id]
        is_staff = context.has_any_role(*EVENT_HANDLER_ROLES)
        is_manager = context.has_any_role(*EVENT_REVIEW_ROLES)
        if is_staff and not is_manager:
            results = [
                e
                for e in results
                if e.assignee_id == context.actor_id or e.reporter_id == context.actor_id
            ]
        elif not is_manager and not is_staff:
            results = [e for e in results if e.reporter_id == context.actor_id]
        return results[search.offset : search.offset + search.limit]

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
    ) -> None:
        self.state.event_status_logs.append(
            {
                "event_id": event_id,
                "community_id": community_id,
                "from_status": from_status,
                "action_code": action.value if hasattr(action, "value") else action,
                "to_status": to_status,
                "operator_id": operator_id,
                "operator_role": operator_role,
                "reason": reason,
                "request_id": request_id,
                "created_at": created_at,
            }
        )

    def add_event_disposal(
        self, *, event_id, community_id, handler_id, note, attachment_ids, created_at
    ) -> None:
        self.state.event_disposals.append(
            {
                "event_id": event_id,
                "community_id": community_id,
                "handler_id": handler_id,
                "note": note,
                "attachment_ids": list(attachment_ids),
                "created_at": created_at,
            }
        )

    def event_timeline(self, event_id: UUID, community_id: UUID) -> list[TimelineEntry]:
        entries: list[TimelineEntry] = []
        for log in self.state.event_status_logs:
            if log["event_id"] == event_id and log["community_id"] == community_id:
                entries.append(
                    TimelineEntry(
                        entry_type="STATUS",
                        action=log["action_code"],
                        operator_id=log["operator_id"],
                        created_at=log["created_at"],
                        from_status=log["from_status"],
                        to_status=log["to_status"],
                        reason=log["reason"],
                    )
                )
        for disp in self.state.event_disposals:
            if disp["event_id"] == event_id and disp["community_id"] == community_id:
                entries.append(
                    TimelineEntry(
                        entry_type="DISPOSAL",
                        action="SUBMIT_DISPOSAL",
                        operator_id=disp["handler_id"],
                        created_at=disp["created_at"],
                        note=disp["note"],
                        attachment_ids=tuple(UUID(v) for v in disp["attachment_ids"]),
                    )
                )
        return sorted(entries, key=lambda e: e.created_at)


class FakeIdempotencyPort(IdempotencyPort):
    def __init__(self, state: FakeState) -> None:
        self.state = state

    def get(self, actor_id, operation, key):
        return self.state.idempotency.get((actor_id, operation, key))

    def add(self, record: IdempotencyRecord) -> None:
        self.state.idempotency[(record.actor_id, record.operation, record.key)] = record


class FakeConfirmationPort(ConfirmationPort):
    def __init__(self, state: FakeState) -> None:
        self.state = state

    def consume(self, *, token, actor_id, action, parameter_hash, request_id) -> None:
        self.state.confirmed.append((token, action, parameter_hash))


class FakeStaffDirectoryPort(StaffDirectoryPort):
    def __init__(
        self, state: FakeState, security_workers: set[UUID], duty_users: list[UUID]
    ) -> None:
        self.state = state
        self.security_workers = security_workers
        self.duty_users = duty_users

    def ensure_security_staff(self, *, user_id, community_id, request_id) -> None:
        if user_id not in self.security_workers:
            raise forbidden("The user is not a security staff member of this community.")

    def list_duty_users(self, community_id) -> list[UUID]:
        return list(self.duty_users)


class FakeAttachmentPort(AttachmentPort):
    def ensure_usable(self, *, attachment_ids, actor_id, community_id, request_id) -> None:
        return None


class FakeAuditPort(AuditPort):
    def __init__(self, state: FakeState) -> None:
        self.state = state

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
    ) -> None:
        self.state.audits.append(
            {
                "community_id": community_id,
                "actor_id": actor_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "parameter_summary": parameter_summary,
                "request_id": request_id,
                "created_at": created_at,
            }
        )


class FakeMessagePort(MessagePort):
    def __init__(self, state: FakeState) -> None:
        self.state = state

    def enqueue(
        self, *, community_id, receiver_id, event_type, resource_id, request_id, created_at
    ) -> None:
        self.state.messages.append(
            {
                "community_id": community_id,
                "receiver_id": receiver_id,
                "event_type": event_type,
                "resource_id": resource_id,
                "request_id": request_id,
                "created_at": created_at,
            }
        )


class FakeEscalationPort(EscalationPort):
    def __init__(self, state: FakeState) -> None:
        self.state = state

    def escalate_high_risk(
        self, *, community_id, event_id, event_business_no, reason, summary, request_id, created_at
    ) -> UUID:
        ticket_id = uuid4()
        self.state.escalations.append(
            {
                "ticket_id": ticket_id,
                "community_id": community_id,
                "event_id": event_id,
                "event_business_no": event_business_no,
                "reason": reason,
                "summary": summary,
                "request_id": request_id,
                "created_at": created_at,
            }
        )
        return ticket_id


class FakeUnitOfWork:
    def __init__(self, state: FakeState, ports: SharedPorts) -> None:
        self.state = state
        self._ports = ports
        self.repository = FakeInspectionRepository(state)
        self.idempotency = ports.idempotency
        self.confirmations = ports.confirmations
        self.staff_directory = ports.staff_directory
        self.attachments = ports.attachments
        self.audit = ports.audit
        self.messages = ports.messages
        self.escalation = ports.escalation

    def __enter__(self) -> FakeUnitOfWork:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None


@dataclass
class Harness:
    state: FakeState = field(default_factory=FakeState)
    security_workers: set[UUID] = field(default_factory=set)
    duty_users: list[UUID] = field(default_factory=list)

    def ports(self) -> SharedPorts:
        return SharedPorts(
            idempotency=FakeIdempotencyPort(self.state),
            confirmations=FakeConfirmationPort(self.state),
            staff_directory=FakeStaffDirectoryPort(
                self.state, self.security_workers, self.duty_users
            ),
            attachments=FakeAttachmentPort(),
            audit=FakeAuditPort(self.state),
            messages=FakeMessagePort(self.state),
            escalation=FakeEscalationPort(self.state),
        )

    def uow(self):
        return FakeUnitOfWork(self.state, self.ports())
