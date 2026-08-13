from collections.abc import Sequence
from uuid import UUID, uuid4

from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session, selectinload

from property_agent.inspection.application.commands import TimelineEntry
from property_agent.inspection.application.ports import RequestContext
from property_agent.inspection.application.service import (
    EVENT_HANDLER_ROLES,
    EVENT_REVIEW_ROLES,
    TASK_ASSIGN_ROLES,
    TASK_ASSIGNEE_ROLES,
)
from property_agent.inspection.domain.entities import (
    AiSuggestion,
    InspectionTask,
    SecurityEvent,
)
from property_agent.inspection.domain.enums import (
    EventRiskLevel,
    EventStatus,
    EventType,
    TaskStatus,
)
from property_agent.inspection.domain.errors import version_conflict
from property_agent.inspection.infrastructure.models import (
    InspectionTaskModel,
    InspectionTaskRecordModel,
    InspectionTaskStatusLogModel,
    SecurityEventDisposalModel,
    SecurityEventModel,
    SecurityEventStatusLogModel,
)


class SqlAlchemyInspectionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # ----------------------------- 巡检任务 -----------------------------
    def add_task(self, task: InspectionTask) -> None:
        self._session.add(self._task_to_model(task))

    def save_task(self, task: InspectionTask) -> None:
        stmt = (
            update(InspectionTaskModel)
            .where(
                InspectionTaskModel.id == task.id,
                InspectionTaskModel.community_id == task.community_id,
                InspectionTaskModel.version == task.version - 1,
            )
            .values(
                status=task.status.value,
                assignee_id=task.assignee_id,
                version=task.version,
                updated_at=task.updated_at,
                closed_at=task.closed_at,
                ai_suggestions=[s.to_dict() for s in task.ai_suggestions],
                ai_pending_confirm=task.ai_pending_confirm,
            )
            .execution_options(synchronize_session=False)
        )
        result = self._session.execute(stmt)
        if result.rowcount != 1:
            current = self._session.scalar(
                select(InspectionTaskModel.version).where(InspectionTaskModel.id == task.id)
            )
            raise version_conflict(current or task.version)

    def get_task(self, task_id: UUID, community_id: UUID) -> InspectionTask | None:
        model = self._session.scalar(
            select(InspectionTaskModel)
            .options(selectinload(InspectionTaskModel.records))
            .where(
                InspectionTaskModel.id == task_id, InspectionTaskModel.community_id == community_id
            )
        )
        return self._task_to_domain(model) if model else None

    def list_tasks(
        self, community_id: UUID, search, context: RequestContext
    ) -> Sequence[InspectionTask]:
        stmt: Select[tuple[InspectionTaskModel]] = (
            select(InspectionTaskModel)
            .options(selectinload(InspectionTaskModel.records))
            .where(InspectionTaskModel.community_id == community_id)
            .order_by(InspectionTaskModel.created_at.desc())
        )
        if search.statuses:
            stmt = stmt.where(InspectionTaskModel.status.in_(search.statuses))
        if search.assigned_to_me:
            stmt = stmt.where(InspectionTaskModel.assignee_id == context.actor_id)
        if context.has_any_role(*TASK_ASSIGNEE_ROLES) and not context.has_any_role(
            *TASK_ASSIGN_ROLES
        ):
            stmt = stmt.where(InspectionTaskModel.assignee_id == context.actor_id)
        stmt = stmt.offset(search.offset).limit(search.limit)
        return [self._task_to_domain(m) for m in self._session.scalars(stmt).all()]

    def aggregate_task_statuses(self, community_id, search, context) -> dict[str, int]:
        """Return an unpaginated status summary in the same authorized scope as list_tasks."""
        stmt = select(InspectionTaskModel.status, func.count(InspectionTaskModel.id)).where(
            InspectionTaskModel.community_id == community_id
        )
        if search.statuses:
            stmt = stmt.where(InspectionTaskModel.status.in_(search.statuses))
        if search.assigned_to_me:
            stmt = stmt.where(InspectionTaskModel.assignee_id == context.actor_id)
        if context.has_any_role(*TASK_ASSIGNEE_ROLES) and not context.has_any_role(
            *TASK_ASSIGN_ROLES
        ):
            stmt = stmt.where(InspectionTaskModel.assignee_id == context.actor_id)
        stmt = stmt.group_by(InspectionTaskModel.status)
        return {str(status): int(count) for status, count in self._session.execute(stmt)}

    def find_active_tasks(self, community_id: UUID) -> Sequence[InspectionTask]:
        """返回社区内仍在进行的巡检任务（计划/已分派/进行中），用于冲突校验。

        PRD 6.4：计划时间与路线冲突校验。仅在内存中做时间窗与路线点的交集判断，
        避免在 JSON 列上做跨数据库（SQLite/PostgreSQL）不兼容的查询。
        """
        stmt = (
            select(InspectionTaskModel)
            .options(selectinload(InspectionTaskModel.records))
            .where(
                InspectionTaskModel.community_id == community_id,
                InspectionTaskModel.status.in_(
                    [
                        TaskStatus.PLANNED.value,
                        TaskStatus.ASSIGNED.value,
                        TaskStatus.IN_PROGRESS.value,
                    ]
                ),
            )
        )
        return [self._task_to_domain(m) for m in self._session.scalars(stmt).all()]

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
        self._session.add(
            InspectionTaskStatusLogModel(
                id=uuid4(),
                community_id=community_id,
                task_id=task_id,
                from_status=from_status,
                action_code=action.value if hasattr(action, "value") else action,
                to_status=to_status,
                operator_id=operator_id,
                operator_role=operator_role,
                reason=reason,
                request_id=request_id,
                created_at=created_at,
            )
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
        self._session.add(
            InspectionTaskRecordModel(
                id=uuid4(),
                community_id=community_id,
                task_id=task_id,
                record_type=record_type.value if hasattr(record_type, "value") else record_type,
                point=point,
                note=note,
                operator_id=operator_id,
                attachment_ids=[str(v) for v in attachment_ids],
                is_supplement=is_supplement,
                actual_time=actual_time,
                supplement_reason=supplement_reason,
                created_at=created_at,
            )
        )

    def task_timeline(self, task_id: UUID, community_id: UUID) -> Sequence[TimelineEntry]:
        status_stmt = select(InspectionTaskStatusLogModel).where(
            InspectionTaskStatusLogModel.task_id == task_id,
            InspectionTaskStatusLogModel.community_id == community_id,
        )
        record_stmt = select(InspectionTaskRecordModel).where(
            InspectionTaskRecordModel.task_id == task_id,
            InspectionTaskRecordModel.community_id == community_id,
        )
        entries = [
            TimelineEntry(
                entry_type="STATUS",
                action=item.action_code,
                operator_id=item.operator_id,
                created_at=item.created_at,
                from_status=item.from_status,
                to_status=item.to_status,
                reason=item.reason,
            )
            for item in self._session.scalars(status_stmt)
        ]
        entries.extend(
            TimelineEntry(
                entry_type="RECORD",
                action=item.record_type,
                operator_id=item.operator_id,
                created_at=item.created_at,
                note=item.note,
                attachment_ids=tuple(UUID(v) for v in item.attachment_ids),
            )
            for item in self._session.scalars(record_stmt)
        )
        return sorted(entries, key=lambda e: e.created_at)

    # ----------------------------- 安防事件 -----------------------------
    def add_event(self, event: SecurityEvent) -> None:
        self._session.add(self._event_to_model(event))

    def save_event(self, event: SecurityEvent) -> None:
        stmt = (
            update(SecurityEventModel)
            .where(
                SecurityEventModel.id == event.id,
                SecurityEventModel.community_id == event.community_id,
                SecurityEventModel.version == event.version - 1,
            )
            .values(
                status=event.status.value,
                assignee_id=event.assignee_id,
                grade_confirmed_by=event.grade_confirmed_by,
                report_source=event.report_source,
                version=event.version,
                updated_at=event.updated_at,
                closed_at=event.closed_at,
            )
            .execution_options(synchronize_session=False)
        )
        result = self._session.execute(stmt)
        if result.rowcount != 1:
            current = self._session.scalar(
                select(SecurityEventModel.version).where(SecurityEventModel.id == event.id)
            )
            raise version_conflict(current or event.version)

    def get_event(self, event_id: UUID, community_id: UUID) -> SecurityEvent | None:
        model = self._session.scalar(
            select(SecurityEventModel)
            .options(selectinload(SecurityEventModel.disposals))
            .where(
                SecurityEventModel.id == event_id, SecurityEventModel.community_id == community_id
            )
        )
        return self._event_to_domain(model) if model else None

    def list_events(
        self, community_id: UUID, search, context: RequestContext
    ) -> Sequence[SecurityEvent]:
        stmt: Select[tuple[SecurityEventModel]] = (
            select(SecurityEventModel)
            .options(selectinload(SecurityEventModel.disposals))
            .where(SecurityEventModel.community_id == community_id)
            .order_by(SecurityEventModel.created_at.desc())
        )
        if search.statuses:
            stmt = stmt.where(SecurityEventModel.status.in_(search.statuses))
        if search.risk_levels:
            stmt = stmt.where(SecurityEventModel.risk_level.in_(search.risk_levels))
        if search.assigned_to_me:
            stmt = stmt.where(SecurityEventModel.assignee_id == context.actor_id)
        is_staff = context.has_any_role(*EVENT_HANDLER_ROLES)
        is_manager = context.has_any_role(*EVENT_REVIEW_ROLES)
        if is_staff and not is_manager:
            stmt = stmt.where(
                (SecurityEventModel.assignee_id == context.actor_id)
                | (SecurityEventModel.reporter_id == context.actor_id)
            )
        elif not is_manager and not is_staff:
            stmt = stmt.where(SecurityEventModel.reporter_id == context.actor_id)
        stmt = stmt.offset(search.offset).limit(search.limit)
        return [self._event_to_domain(m) for m in self._session.scalars(stmt).all()]

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
        self._session.add(
            SecurityEventStatusLogModel(
                id=uuid4(),
                community_id=community_id,
                event_id=event_id,
                from_status=from_status,
                action_code=action.value if hasattr(action, "value") else action,
                to_status=to_status,
                operator_id=operator_id,
                operator_role=operator_role,
                reason=reason,
                request_id=request_id,
                created_at=created_at,
            )
        )

    def add_event_disposal(
        self, *, event_id, community_id, handler_id, note, attachment_ids, created_at
    ) -> None:
        self._session.add(
            SecurityEventDisposalModel(
                id=uuid4(),
                community_id=community_id,
                event_id=event_id,
                handler_id=handler_id,
                note=note,
                attachment_ids=[str(v) for v in attachment_ids],
                created_at=created_at,
            )
        )

    def event_timeline(self, event_id: UUID, community_id: UUID) -> Sequence[TimelineEntry]:
        status_stmt = select(SecurityEventStatusLogModel).where(
            SecurityEventStatusLogModel.event_id == event_id,
            SecurityEventStatusLogModel.community_id == community_id,
        )
        disposal_stmt = select(SecurityEventDisposalModel).where(
            SecurityEventDisposalModel.event_id == event_id,
            SecurityEventDisposalModel.community_id == community_id,
        )
        entries = [
            TimelineEntry(
                entry_type="STATUS",
                action=item.action_code,
                operator_id=item.operator_id,
                created_at=item.created_at,
                from_status=item.from_status,
                to_status=item.to_status,
                reason=item.reason,
            )
            for item in self._session.scalars(status_stmt)
        ]
        entries.extend(
            TimelineEntry(
                entry_type="DISPOSAL",
                action="SUBMIT_DISPOSAL",
                operator_id=item.handler_id,
                created_at=item.created_at,
                note=item.note,
                attachment_ids=tuple(UUID(v) for v in item.attachment_ids),
            )
            for item in self._session.scalars(disposal_stmt)
        )
        return sorted(entries, key=lambda e: e.created_at)

    # ----------------------------- 转换 -----------------------------
    @staticmethod
    def _task_to_model(task: InspectionTask) -> InspectionTaskModel:
        return InspectionTaskModel(
            id=task.id,
            community_id=task.community_id,
            business_no=task.business_no,
            title=task.title,
            description=task.description,
            route_points=list(task.route_points),
            created_by=task.created_by,
            create_idempotency_key=task.create_idempotency_key,
            status=task.status.value,
            assignee_id=task.assignee_id,
            planned_at=task.planned_at,
            due_at=task.due_at,
            version=task.version,
            created_at=task.created_at,
            updated_at=task.updated_at,
            closed_at=task.closed_at,
            ai_suggestions=[s.to_dict() for s in task.ai_suggestions],
            ai_pending_confirm=task.ai_pending_confirm,
        )

    @staticmethod
    def _task_to_domain(model: InspectionTaskModel) -> InspectionTask:
        return InspectionTask(
            id=model.id,
            community_id=model.community_id,
            business_no=model.business_no,
            title=model.title,
            description=model.description,
            route_points=tuple(model.route_points or ()),
            created_by=model.created_by,
            create_idempotency_key=model.create_idempotency_key,
            status=TaskStatus(model.status),
            assignee_id=model.assignee_id,
            planned_at=model.planned_at,
            due_at=model.due_at,
            version=model.version,
            created_at=model.created_at,
            updated_at=model.updated_at,
            closed_at=model.closed_at,
            ai_suggestions=tuple(AiSuggestion.from_dict(s) for s in (model.ai_suggestions or [])),
            ai_pending_confirm=model.ai_pending_confirm,
        )

    @staticmethod
    def _event_to_model(event: SecurityEvent) -> SecurityEventModel:
        return SecurityEventModel(
            id=event.id,
            community_id=event.community_id,
            business_no=event.business_no,
            source_task_id=event.source_task_id,
            reporter_id=event.reporter_id,
            event_type=event.event_type.value,
            risk_level=event.risk_level.value,
            location=event.location,
            description=event.description,
            create_idempotency_key=event.create_idempotency_key,
            status=event.status.value,
            assignee_id=event.assignee_id,
            grade_confirmed_by=event.grade_confirmed_by,
            report_source=event.report_source,
            version=event.version,
            created_at=event.created_at,
            updated_at=event.updated_at,
            closed_at=event.closed_at,
        )

    @staticmethod
    def _event_to_domain(model: SecurityEventModel) -> SecurityEvent:
        return SecurityEvent(
            id=model.id,
            community_id=model.community_id,
            business_no=model.business_no,
            reporter_id=model.reporter_id,
            event_type=EventType(model.event_type),
            risk_level=EventRiskLevel(model.risk_level),
            location=model.location,
            description=model.description,
            source_task_id=model.source_task_id,
            create_idempotency_key=model.create_idempotency_key,
            status=EventStatus(model.status),
            assignee_id=model.assignee_id,
            grade_confirmed_by=model.grade_confirmed_by,
            report_source=model.report_source,
            version=model.version,
            created_at=model.created_at,
            updated_at=model.updated_at,
            closed_at=model.closed_at,
        )
