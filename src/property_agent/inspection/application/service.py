from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from property_agent.inspection.application.commands import (
    CreateInspectionTaskCommand,
    CreateSecurityEventCommand,
    ExecuteEventActionCommand,
    ExecuteTaskActionCommand,
    InspectionTaskSearch,
    SecurityEventSearch,
    TimelineEntry,
)
from property_agent.inspection.application.ports import (
    IdempotencyRecord,
    RequestContext,
)
from property_agent.inspection.domain.entities import InspectionTask, SecurityEvent
from property_agent.inspection.domain.enums import (
    EventAction,
    EventRiskLevel,
    EventStatus,
    EventType,
    Role,
    TaskAction,
    TaskRecordType,
    TaskStatus,
)
from property_agent.inspection.domain.errors import (
    confirmation_required,
    forbidden,
    idempotency_conflict,
    invalid_transition,
    not_found,
    validation_error,
    version_conflict,
)
from property_agent.platform.application.hashing import canonical_hash

# 角色分组
TASK_READ_ROLES = (Role.MANAGER, Role.SECURITY_STAFF)
TASK_CREATE_ROLES = (Role.MANAGER, Role.SECURITY_STAFF)
TASK_ASSIGN_ROLES = (Role.MANAGER,)
TASK_COMPLETE_ROLES = (Role.MANAGER,)
TASK_ASSIGNEE_ROLES = (Role.SECURITY_STAFF,)

EVENT_READ_ROLES = (Role.MANAGER, Role.SECURITY_STAFF, Role.CUSTOMER_SERVICE, Role.RESIDENT)
EVENT_CREATE_ROLES = (Role.RESIDENT, Role.CUSTOMER_SERVICE, Role.SECURITY_STAFF, Role.MANAGER)
EVENT_ASSIGN_ROLES = (Role.MANAGER,)
EVENT_HANDLER_ROLES = (Role.SECURITY_STAFF,)
EVENT_REVIEW_ROLES = (Role.MANAGER,)


class InspectionTaskService:
    def __init__(self, unit_of_work_factory: Any) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    # ----------------------------- 创建计划 -----------------------------
    def create_task(
        self,
        command: CreateInspectionTaskCommand,
        context: RequestContext,
        *,
        idempotency_key: str,
    ) -> InspectionTask:
        self._require_idempotency_key(idempotency_key)
        self._require_role(context, *TASK_CREATE_ROLES)
        operation = "INSPECTION_TASK_CREATE"
        request_hash = canonical_hash(asdict(command))
        with self._unit_of_work_factory() as uow:
            replay = self._idempotent_replay(uow, context, operation, idempotency_key, request_hash)
            if replay is not None:
                return replay
            self._validate_create(command)
            now = datetime.now(UTC)
            task = InspectionTask(
                id=uuid4(),
                community_id=context.community_id,
                business_no=self._new_business_no(now, "XJ"),
                title=command.title.strip(),
                description=command.description.strip(),
                route_points=tuple(p.strip() for p in command.route_points if p.strip()),
                created_by=context.actor_id,
                create_idempotency_key=idempotency_key,
                planned_at=command.planned_at,
                due_at=command.due_at,
                created_at=now,
                updated_at=now,
            )
            uow.repository.add_task(task)
            self._add_task_status_log(
                uow, task, context, action=TaskAction.CREATE, from_status=None, reason=None, now=now
            )
            uow.idempotency.add(
                IdempotencyRecord(
                    actor_id=context.actor_id,
                    operation=operation,
                    key=idempotency_key,
                    request_hash=request_hash,
                    resource_id=task.id,
                    response_snapshot=self._task_snapshot(task),
                )
            )
            self._audit(
                uow,
                task,
                context,
                action=TaskAction.CREATE,
                parameters={"title": task.title, "points": len(task.route_points)},
                now=now,
            )
            uow.commit()
            return task

    # ----------------------------- 查询 -----------------------------
    def search_tasks(
        self, search: InspectionTaskSearch, context: RequestContext
    ) -> list[InspectionTask]:
        self._require_role(context, *TASK_READ_ROLES)
        self._validate_pagination(search.limit, search.offset)
        with self._unit_of_work_factory() as uow:
            return list(uow.repository.list_tasks(context.community_id, search, context))

    def get_task(self, task_id: UUID, context: RequestContext) -> InspectionTask:
        with self._unit_of_work_factory() as uow:
            return self._get_authorized_task(uow, task_id, context)

    def task_timeline(self, task_id: UUID, context: RequestContext) -> list[TimelineEntry]:
        with self._unit_of_work_factory() as uow:
            self._get_authorized_task(uow, task_id, context)
            return list(uow.repository.task_timeline(task_id, context.community_id))

    # ----------------------------- 执行动作 -----------------------------
    def execute_task_action(
        self,
        task_id: UUID,
        command: ExecuteTaskActionCommand,
        context: RequestContext,
        *,
        idempotency_key: str,
    ) -> InspectionTask:
        self._require_idempotency_key(idempotency_key)
        operation = f"INSPECTION_TASK_{command.action.value}"
        request_hash = canonical_hash({"task_id": task_id, **asdict(command)})
        with self._unit_of_work_factory() as uow:
            replay = self._idempotent_replay(uow, context, operation, idempotency_key, request_hash)
            if replay is not None:
                return replay
            task = self._get_authorized_task(uow, task_id, context)
            if task.version != command.expected_version:
                raise version_conflict(task.version)
            now = datetime.now(UTC)
            from_status = task.status
            reason = self._apply_task_action(uow, task, command, context, now)
            uow.idempotency.add(
                IdempotencyRecord(
                    actor_id=context.actor_id,
                    operation=operation,
                    key=idempotency_key,
                    request_hash=request_hash,
                    resource_id=task.id,
                    response_snapshot=self._task_snapshot(task),
                )
            )
            if command.action != TaskAction.ADD_RECORD:
                self._add_task_status_log(
                    uow,
                    task,
                    context,
                    action=command.action,
                    from_status=from_status.value,
                    reason=reason,
                    now=now,
                )
            uow.repository.save_task(task)
            self._audit(
                uow,
                task,
                context,
                action=command.action,
                parameters={"reason": reason, "record_type": command.record_type},
                now=now,
            )
            uow.commit()
            return task

    # ----------------------------- 可用动作 -----------------------------
    def available_task_actions(
        self, task: InspectionTask, context: RequestContext
    ) -> list[TaskAction]:
        status = task.status
        if status == TaskStatus.PLANNED and context.has_any_role(*TASK_ASSIGN_ROLES):
            return [TaskAction.ASSIGN]
        if (
            status == TaskStatus.ASSIGNED
            and context.has_any_role(*TASK_ASSIGNEE_ROLES)
            and task.assignee_id == context.actor_id
        ):
            return [TaskAction.START]
        if (
            status == TaskStatus.IN_PROGRESS
            and context.has_any_role(*TASK_ASSIGNEE_ROLES)
            and task.assignee_id == context.actor_id
        ):
            return [TaskAction.SUBMIT_RECORDS, TaskAction.ADD_RECORD]
        if (
            status == TaskStatus.SUBMITTED
            and context.has_any_role(*TASK_ASSIGNEE_ROLES)
            and task.assignee_id == context.actor_id
        ):
            return [TaskAction.ADD_RECORD]
        if status == TaskStatus.SUBMITTED and context.has_any_role(*TASK_COMPLETE_ROLES):
            return [TaskAction.COMPLETE]
        return []

    # ----------------------------- 内部：动作处理 -----------------------------
    def _apply_task_action(self, uow, task, command, context, now) -> str | None:
        action = command.action
        if action == TaskAction.ASSIGN:
            return self._assign_task(uow, task, command, context, now)
        if action == TaskAction.START:
            self._require_task_assignee(task, context)
            task.transition(action, now=now)
            return None
        if action == TaskAction.SUBMIT_RECORDS:
            return self._submit_records(uow, task, command, context, now)
        if action == TaskAction.COMPLETE:
            self._require_role(context, *TASK_COMPLETE_ROLES)
            task.transition(action, now=now)
            return None
        if action == TaskAction.ADD_RECORD:
            return self._add_record(uow, task, command, context, now)
        raise validation_error(f"Unsupported action: {action.value}.")

    def _assign_task(self, uow, task, command, context, now) -> None:
        self._require_role(context, *TASK_ASSIGN_ROLES)
        if command.assignee_id is None:
            raise validation_error("assignee_id is required.")
        uow.staff_directory.ensure_security_staff(
            user_id=command.assignee_id,
            community_id=context.community_id,
            request_id=context.request_id,
        )
        task.assignee_id = command.assignee_id
        task.transition(command.action, now=now)

    def _submit_records(self, uow, task, command, context, now) -> str:
        self._require_task_assignee(task, context)
        if not command.confirmation_token or not command.confirmation_token.strip():
            raise confirmation_required()
        request_hash = canonical_hash(
            {"note": command.note, "record_type": command.record_type, "point": command.point}
        )
        uow.confirmations.consume(
            token=command.confirmation_token,
            actor_id=context.actor_id,
            action="INSPECTION_TASK_SUBMIT_RECORDS",
            parameter_hash=request_hash,
            request_id=context.request_id,
        )
        if command.record_type not in {
            TaskRecordType.CHECKIN,
            TaskRecordType.POINT_RECORD,
            TaskRecordType.PROGRESS,
            TaskRecordType.COMPLETION,
        }:
            raise validation_error("record_type is invalid for a submission.")
        note = self._required_text(command.note, "A submission note is required.")
        uow.attachments.ensure_usable(
            attachment_ids=command.attachment_ids,
            actor_id=context.actor_id,
            community_id=context.community_id,
            request_id=context.request_id,
        )
        uow.repository.add_task_record(
            task_id=task.id,
            community_id=context.community_id,
            record_type=command.record_type,
            point=command.point,
            note=note,
            operator_id=context.actor_id,
            attachment_ids=command.attachment_ids,
            is_supplement=False,
            actual_time=None,
            created_at=now,
        )
        task.transition(command.action, now=now)
        return note

    def _add_record(self, uow, task, command, context, now) -> str:
        self._require_task_assignee(task, context)
        if command.record_type not in {
            TaskRecordType.CHECKIN,
            TaskRecordType.POINT_RECORD,
            TaskRecordType.PROGRESS,
            TaskRecordType.COMPLETION,
            TaskRecordType.SUPPLEMENT,
        }:
            raise validation_error("record_type is invalid for a record.")
        note = self._required_text(command.note, "A record note is required.")
        uow.attachments.ensure_usable(
            attachment_ids=command.attachment_ids,
            actor_id=context.actor_id,
            community_id=context.community_id,
            request_id=context.request_id,
        )
        uow.repository.add_task_record(
            task_id=task.id,
            community_id=context.community_id,
            record_type=command.record_type,
            point=command.point,
            note=note,
            operator_id=context.actor_id,
            attachment_ids=command.attachment_ids,
            is_supplement=command.is_supplement,
            actual_time=command.actual_time,
            created_at=now,
        )
        task.touch(now=now)
        return note

    # ----------------------------- 内部：鉴权 -----------------------------
    def _get_authorized_task(self, uow, task_id, context) -> InspectionTask:
        self._require_role(context, *TASK_READ_ROLES)
        task = uow.repository.get_task(task_id, context.community_id)
        if task is None:
            raise not_found()
        if (
            context.has_any_role(*TASK_ASSIGNEE_ROLES)
            and not context.has_any_role(*TASK_ASSIGN_ROLES)
            and task.assignee_id != context.actor_id
        ):
            raise not_found()
        return task

    # ----------------------------- 内部：通用 -----------------------------
    def _validate_create(self, command: CreateInspectionTaskCommand) -> None:
        if not command.title.strip():
            raise validation_error("title is required.")
        if len(command.title.strip()) > 128:
            raise validation_error("title must not exceed 128 characters.")
        if not command.description.strip():
            raise validation_error("description is required.")
        if not command.route_points or not any(p.strip() for p in command.route_points):
            raise validation_error("At least one route point is required.")
        if (
            command.due_at is not None
            and command.planned_at is not None
            and command.due_at < command.planned_at
        ):
            raise validation_error("due_at must not be earlier than planned_at.")

    def _require_role(self, context, *roles) -> None:
        if not context.has_any_role(*roles):
            raise forbidden()

    def _require_task_assignee(self, task, context) -> None:
        if not context.has_any_role(*TASK_ASSIGNEE_ROLES) or task.assignee_id != context.actor_id:
            raise forbidden()

    def _require_idempotency_key(self, key: str) -> None:
        if not key or not key.strip() or len(key) > 128:
            raise validation_error(
                "Idempotency-Key is required and must not exceed 128 characters."
            )

    def _validate_pagination(self, limit: int, offset: int) -> None:
        if limit < 1 or limit > 100 or offset < 0:
            raise validation_error("Pagination must use offset >= 0 and limit between 1 and 100.")

    @staticmethod
    def _required_text(value, message) -> str:
        if value is None or not value.strip():
            raise validation_error(message)
        return value.strip()

    @staticmethod
    def _new_business_no(now: datetime, prefix: str) -> str:
        return f"{prefix}-{now:%Y%m%d}-{uuid4().hex[:8].upper()}"

    @staticmethod
    def _task_snapshot(task: InspectionTask) -> dict[str, Any]:
        return {
            "id": str(task.id),
            "community_id": str(task.community_id),
            "business_no": task.business_no,
            "title": task.title,
            "description": task.description,
            "route_points": list(task.route_points),
            "created_by": str(task.created_by),
            "create_idempotency_key": task.create_idempotency_key,
            "status": task.status.value,
            "assignee_id": str(task.assignee_id) if task.assignee_id else None,
            "planned_at": task.planned_at.isoformat() if task.planned_at else None,
            "due_at": task.due_at.isoformat() if task.due_at else None,
            "version": task.version,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
            "closed_at": task.closed_at.isoformat() if task.closed_at else None,
        }

    @staticmethod
    def _task_from_snapshot(snapshot) -> InspectionTask:
        return InspectionTask(
            id=UUID(snapshot["id"]),
            community_id=UUID(snapshot["community_id"]),
            business_no=snapshot["business_no"],
            title=snapshot["title"],
            description=snapshot["description"],
            route_points=tuple(snapshot.get("route_points", ())),
            created_by=UUID(snapshot["created_by"]) if snapshot.get("created_by") else uuid4(),
            create_idempotency_key=snapshot["create_idempotency_key"],
            status=TaskStatus(snapshot["status"]),
            assignee_id=UUID(snapshot["assignee_id"]) if snapshot.get("assignee_id") else None,
            planned_at=datetime.fromisoformat(snapshot["planned_at"])
            if snapshot.get("planned_at")
            else None,
            due_at=datetime.fromisoformat(snapshot["due_at"]) if snapshot.get("due_at") else None,
            version=snapshot["version"],
            created_at=datetime.fromisoformat(snapshot["created_at"]),
            updated_at=datetime.fromisoformat(snapshot["updated_at"]),
            closed_at=datetime.fromisoformat(snapshot["closed_at"])
            if snapshot.get("closed_at")
            else None,
        )

    def _add_task_status_log(self, uow, task, context, *, action, from_status, reason, now) -> None:
        uow.repository.add_task_status_log(
            task_id=task.id,
            community_id=context.community_id,
            from_status=from_status,
            action=action,
            to_status=task.status.value,
            operator_id=context.actor_id,
            operator_role=self._operator_role(context).value,
            reason=reason,
            request_id=context.request_id,
            created_at=now,
        )

    def _audit(self, uow, task, context, *, action, parameters, now) -> None:
        uow.audit.add(
            community_id=context.community_id,
            actor_id=context.actor_id,
            action=action.value,
            resource_type="INSPECTION_TASK",
            resource_id=task.id,
            parameter_summary=parameters,
            request_id=context.request_id,
            created_at=now,
        )

    @staticmethod
    def _operator_role(context) -> Role:
        for role in (Role.MANAGER, Role.SECURITY_STAFF, Role.CUSTOMER_SERVICE, Role.RESIDENT):
            if role in context.roles:
                return role
        raise forbidden()

    def _idempotent_replay(
        self, uow, context, operation, key, request_hash
    ) -> InspectionTask | None:
        existing = uow.idempotency.get(context.actor_id, operation, key)
        if existing is None:
            return None
        if existing.request_hash != request_hash:
            raise idempotency_conflict()
        return self._task_from_snapshot(existing.response_snapshot)


class SecurityEventService:
    def __init__(self, unit_of_work_factory: Any) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    # ----------------------------- 创建事件 -----------------------------
    def create_event(
        self,
        command: CreateSecurityEventCommand,
        context: RequestContext,
        *,
        idempotency_key: str,
    ) -> SecurityEvent:
        self._require_idempotency_key(idempotency_key)
        self._require_role(context, *EVENT_CREATE_ROLES)
        if not command.confirmation_token or not command.confirmation_token.strip():
            raise confirmation_required()
        operation = "SECURITY_EVENT_CREATE"
        request_hash = canonical_hash(asdict(command))
        with self._unit_of_work_factory() as uow:
            replay = self._idempotent_replay(uow, context, operation, idempotency_key, request_hash)
            if replay is not None:
                return replay
            self._validate_create(command)
            param_hash = canonical_hash(
                {
                    "event_type": command.event_type.value,
                    "risk_level": command.risk_level.value,
                    "location": command.location,
                }
            )
            uow.confirmations.consume(
                token=command.confirmation_token,
                actor_id=context.actor_id,
                action="SECURITY_EVENT_CREATE",
                parameter_hash=param_hash,
                request_id=context.request_id,
            )
            now = datetime.now(UTC)
            event = SecurityEvent(
                id=uuid4(),
                community_id=context.community_id,
                business_no=self._new_business_no(now, "AQ"),
                reporter_id=context.actor_id,
                event_type=command.event_type,
                risk_level=command.risk_level,
                location=command.location.strip(),
                description=command.description.strip(),
                source_task_id=command.source_task_id,
                create_idempotency_key=idempotency_key,
                created_at=now,
                updated_at=now,
            )
            uow.repository.add_event(event)
            self._add_event_status_log(
                uow,
                event,
                context,
                action=EventAction.CREATE,
                from_status=None,
                reason=None,
                now=now,
            )
            uow.idempotency.add(
                IdempotencyRecord(
                    actor_id=context.actor_id,
                    operation=operation,
                    key=idempotency_key,
                    request_hash=request_hash,
                    resource_id=event.id,
                    response_snapshot=self._event_snapshot(event),
                )
            )
            self._audit(
                uow,
                event,
                context,
                action=EventAction.CREATE,
                parameters={
                    "event_type": event.event_type.value,
                    "risk_level": event.risk_level.value,
                },
                now=now,
            )
            # 高风险：通知值班人员并锁定待人工确认（事件不自动关闭）
            if event.risk_level == EventRiskLevel.HIGH_RISK:
                for duty in uow.staff_directory.list_duty_users(context.community_id):
                    uow.messages.enqueue(
                        community_id=context.community_id,
                        receiver_id=duty,
                        event_type="HIGH_RISK_EVENT",
                        resource_id=event.id,
                        request_id=context.request_id,
                        created_at=now,
                    )
            uow.commit()
            return event

    # ----------------------------- 查询 -----------------------------
    def search_events(
        self, search: SecurityEventSearch, context: RequestContext
    ) -> list[SecurityEvent]:
        self._require_role(context, *EVENT_READ_ROLES)
        self._validate_pagination(search.limit, search.offset)
        with self._unit_of_work_factory() as uow:
            return list(uow.repository.list_events(context.community_id, search, context))

    def get_event(self, event_id: UUID, context: RequestContext) -> SecurityEvent:
        with self._unit_of_work_factory() as uow:
            return self._get_authorized_event(uow, event_id, context)

    def event_timeline(self, event_id: UUID, context: RequestContext) -> list[TimelineEntry]:
        with self._unit_of_work_factory() as uow:
            self._get_authorized_event(uow, event_id, context)
            return list(uow.repository.event_timeline(event_id, context.community_id))

    # ----------------------------- 执行动作 -----------------------------
    def execute_event_action(
        self,
        event_id: UUID,
        command: ExecuteEventActionCommand,
        context: RequestContext,
        *,
        idempotency_key: str,
    ) -> SecurityEvent:
        self._require_idempotency_key(idempotency_key)
        operation = f"SECURITY_EVENT_{command.action.value}"
        request_hash = canonical_hash({"event_id": event_id, **asdict(command)})
        with self._unit_of_work_factory() as uow:
            replay = self._idempotent_replay(uow, context, operation, idempotency_key, request_hash)
            if replay is not None:
                return replay
            event = self._get_authorized_event(uow, event_id, context)
            if event.version != command.expected_version:
                raise version_conflict(event.version)
            now = datetime.now(UTC)
            from_status = event.status
            reason = self._apply_event_action(uow, event, command, context, now)
            uow.idempotency.add(
                IdempotencyRecord(
                    actor_id=context.actor_id,
                    operation=operation,
                    key=idempotency_key,
                    request_hash=request_hash,
                    resource_id=event.id,
                    response_snapshot=self._event_snapshot(event),
                )
            )
            self._add_event_status_log(
                uow,
                event,
                context,
                action=command.action,
                from_status=from_status.value,
                reason=reason,
                now=now,
            )
            uow.repository.save_event(event)
            self._audit(
                uow,
                event,
                context,
                action=command.action,
                parameters={"reason": reason},
                now=now,
            )
            uow.commit()
            return event

    # ----------------------------- 可用动作 -----------------------------
    def available_event_actions(
        self, event: SecurityEvent, context: RequestContext
    ) -> list[EventAction]:
        status = event.status
        if status == EventStatus.REPORTED and context.has_any_role(*EVENT_ASSIGN_ROLES):
            return [EventAction.ASSIGN]
        if (
            status == EventStatus.ASSIGNED
            and context.has_any_role(*EVENT_HANDLER_ROLES)
            and event.assignee_id == context.actor_id
        ):
            return [EventAction.SUBMIT_DISPOSAL]
        if (
            status == EventStatus.PENDING_REVIEW
            and context.has_any_role(*EVENT_HANDLER_ROLES)
            and event.assignee_id == context.actor_id
        ):
            return [EventAction.SUBMIT_DISPOSAL]
        if status == EventStatus.PENDING_REVIEW and context.has_any_role(*EVENT_REVIEW_ROLES):
            return [EventAction.REVIEW_PASS, EventAction.RETURN]
        return []

    # ----------------------------- 内部：动作处理 -----------------------------
    def _apply_event_action(self, uow, event, command, context, now) -> str | None:
        action = command.action
        if action == EventAction.ASSIGN:
            return self._assign_event(uow, event, command, context, now)
        if action == EventAction.SUBMIT_DISPOSAL:
            return self._submit_disposal(uow, event, command, context, now)
        if action == EventAction.REVIEW_PASS:
            return self._review_pass(event, command, context, now)
        if action == EventAction.RETURN:
            self._require_role(context, *EVENT_REVIEW_ROLES)
            reason = self._required_text(command.note, "A return reason is required.")
            event.transition(action, now=now)
            return reason
        raise validation_error(f"Unsupported action: {action.value}.")

    def _assign_event(self, uow, event, command, context, now) -> None:
        self._require_role(context, *EVENT_ASSIGN_ROLES)
        if command.assignee_id is None:
            raise validation_error("assignee_id is required.")
        uow.staff_directory.ensure_security_staff(
            user_id=command.assignee_id,
            community_id=context.community_id,
            request_id=context.request_id,
        )
        event.assignee_id = command.assignee_id
        event.transition(command.action, now=now)

    def _submit_disposal(self, uow, event, command, context, now) -> str:
        self._require_event_handler(event, context)
        if event.status == EventStatus.PENDING_REVIEW:
            raise invalid_transition(
                event.status.value,
                command.action.value,
                [a.value for a in event.state_actions()],
            )
        note = self._required_text(command.note, "A disposal note is required.")
        uow.attachments.ensure_usable(
            attachment_ids=command.attachment_ids,
            actor_id=context.actor_id,
            community_id=context.community_id,
            request_id=context.request_id,
        )
        uow.repository.add_event_disposal(
            event_id=event.id,
            community_id=context.community_id,
            handler_id=context.actor_id,
            note=note,
            attachment_ids=command.attachment_ids,
            created_at=now,
        )
        event.transition(command.action, now=now)
        return note

    def _review_pass(self, event, command, context, now) -> None:
        self._require_role(context, *EVENT_REVIEW_ROLES)
        # 高风险事件：复核通过即代表等级与处置方案经人工确认
        if event.risk_level == EventRiskLevel.HIGH_RISK and event.grade_confirmed_by is None:
            event.grade_confirmed_by = context.actor_id
        event.transition(EventAction.REVIEW_PASS, now=now)
        return None

    # ----------------------------- 内部：鉴权 -----------------------------
    def _get_authorized_event(self, uow, event_id, context) -> SecurityEvent:
        self._require_role(context, *EVENT_READ_ROLES)
        event = uow.repository.get_event(event_id, context.community_id)
        if event is None:
            raise not_found()
        is_staff = context.has_any_role(*EVENT_HANDLER_ROLES)
        is_manager = context.has_any_role(*EVENT_REVIEW_ROLES)
        if is_staff and not is_manager:
            if event.assignee_id != context.actor_id and event.reporter_id != context.actor_id:
                raise not_found()
        elif (
            context.has_any_role(Role.CUSTOMER_SERVICE, Role.RESIDENT)
            and not is_manager
            and not is_staff
        ):
            if event.reporter_id != context.actor_id:
                raise not_found()
        return event

    # ----------------------------- 内部：通用 -----------------------------
    def _validate_create(self, command: CreateSecurityEventCommand) -> None:
        if not command.location.strip():
            raise validation_error("location is required.")
        if len(command.location.strip()) > 128:
            raise validation_error("location must not exceed 128 characters.")
        if not command.description.strip():
            raise validation_error("description is required.")

    def _require_role(self, context, *roles) -> None:
        if not context.has_any_role(*roles):
            raise forbidden()

    def _require_event_handler(self, event, context) -> None:
        if not context.has_any_role(*EVENT_HANDLER_ROLES) or event.assignee_id != context.actor_id:
            raise forbidden()

    def _require_idempotency_key(self, key: str) -> None:
        if not key or not key.strip() or len(key) > 128:
            raise validation_error(
                "Idempotency-Key is required and must not exceed 128 characters."
            )

    def _validate_pagination(self, limit: int, offset: int) -> None:
        if limit < 1 or limit > 100 or offset < 0:
            raise validation_error("Pagination must use offset >= 0 and limit between 1 and 100.")

    @staticmethod
    def _required_text(value, message) -> str:
        if value is None or not value.strip():
            raise validation_error(message)
        return value.strip()

    @staticmethod
    def _new_business_no(now: datetime, prefix: str) -> str:
        return f"{prefix}-{now:%Y%m%d}-{uuid4().hex[:8].upper()}"

    @staticmethod
    def _event_snapshot(event: SecurityEvent) -> dict[str, Any]:
        return {
            "id": str(event.id),
            "community_id": str(event.community_id),
            "business_no": event.business_no,
            "reporter_id": str(event.reporter_id),
            "event_type": event.event_type.value,
            "risk_level": event.risk_level.value,
            "location": event.location,
            "description": event.description,
            "status": event.status.value,
            "source_task_id": str(event.source_task_id) if event.source_task_id else None,
            "create_idempotency_key": event.create_idempotency_key,
            "assignee_id": str(event.assignee_id) if event.assignee_id else None,
            "grade_confirmed_by": str(event.grade_confirmed_by)
            if event.grade_confirmed_by
            else None,
            "version": event.version,
            "created_at": event.created_at.isoformat(),
            "updated_at": event.updated_at.isoformat(),
            "closed_at": event.closed_at.isoformat() if event.closed_at else None,
        }

    @staticmethod
    def _event_from_snapshot(snapshot) -> SecurityEvent:
        return SecurityEvent(
            id=UUID(snapshot["id"]),
            community_id=UUID(snapshot["community_id"]),
            business_no=snapshot["business_no"],
            reporter_id=UUID(snapshot["reporter_id"]),
            event_type=EventType(snapshot["event_type"]),
            risk_level=EventRiskLevel(snapshot["risk_level"]),
            location=snapshot["location"],
            description=snapshot["description"],
            source_task_id=UUID(snapshot["source_task_id"])
            if snapshot.get("source_task_id")
            else None,
            create_idempotency_key=snapshot["create_idempotency_key"],
            status=EventStatus(snapshot["status"]),
            assignee_id=UUID(snapshot["assignee_id"]) if snapshot.get("assignee_id") else None,
            grade_confirmed_by=UUID(snapshot["grade_confirmed_by"])
            if snapshot.get("grade_confirmed_by")
            else None,
            version=snapshot["version"],
            created_at=datetime.fromisoformat(snapshot["created_at"]),
            updated_at=datetime.fromisoformat(snapshot["updated_at"]),
            closed_at=datetime.fromisoformat(snapshot["closed_at"])
            if snapshot.get("closed_at")
            else None,
        )

    def _add_event_status_log(
        self, uow, event, context, *, action, from_status, reason, now
    ) -> None:
        uow.repository.add_event_status_log(
            event_id=event.id,
            community_id=context.community_id,
            from_status=from_status,
            action=action,
            to_status=event.status.value,
            operator_id=context.actor_id,
            operator_role=self._operator_role(context).value,
            reason=reason,
            request_id=context.request_id,
            created_at=now,
        )

    def _audit(self, uow, event, context, *, action, parameters, now) -> None:
        uow.audit.add(
            community_id=context.community_id,
            actor_id=context.actor_id,
            action=action.value,
            resource_type="SECURITY_EVENT",
            resource_id=event.id,
            parameter_summary=parameters,
            request_id=context.request_id,
            created_at=now,
        )

    @staticmethod
    def _operator_role(context) -> Role:
        for role in (Role.MANAGER, Role.SECURITY_STAFF, Role.CUSTOMER_SERVICE, Role.RESIDENT):
            if role in context.roles:
                return role
        raise forbidden()

    def _idempotent_replay(
        self, uow, context, operation, key, request_hash
    ) -> SecurityEvent | None:
        existing = uow.idempotency.get(context.actor_id, operation, key)
        if existing is None:
            return None
        if existing.request_hash != request_hash:
            raise idempotency_conflict()
        return self._event_from_snapshot(existing.response_snapshot)
