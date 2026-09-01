from collections.abc import Sequence
from uuid import UUID, uuid4

from sqlalchemy import Select, select, update
from sqlalchemy.orm import Session, selectinload

from property_agent.repair.application.commands import TimelineEntry, WorkOrderSearch
from property_agent.repair.application.ports import RequestContext
from property_agent.repair.domain.entities import WorkOrder
from property_agent.repair.domain.enums import (
    ActionCode,
    ProcessRecordType,
    RepairCategory,
    Role,
    Urgency,
    WorkOrderStatus,
)
from property_agent.repair.domain.errors import version_conflict
from property_agent.repair.infrastructure.models import (
    WorkOrderModel,
    WorkOrderProcessRecordModel,
    WorkOrderReviewModel,
    WorkOrderStatusLogModel,
)


class SqlAlchemyWorkOrderRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, work_order: WorkOrder) -> None:
        self._session.add(self._to_model(work_order))

    def save(self, work_order: WorkOrder) -> None:
        statement = (
            update(WorkOrderModel)
            .where(
                WorkOrderModel.id == work_order.id,
                WorkOrderModel.community_id == work_order.community_id,
                WorkOrderModel.version == work_order.version - 1,
            )
            .values(
                status=work_order.status.value,
                assignee_id=work_order.assignee_id,
                version=work_order.version,
                updated_at=work_order.updated_at,
                closed_at=work_order.closed_at,
                appointment_at=work_order.appointment_at,
            )
            .execution_options(synchronize_session=False)
        )
        result = self._session.execute(statement)
        if result.rowcount != 1:
            current_version = self._session.scalar(
                select(WorkOrderModel.version).where(WorkOrderModel.id == work_order.id)
            )
            raise version_conflict(current_version or work_order.version)

    def get(self, work_order_id: UUID, community_id: UUID) -> WorkOrder | None:
        statement = (
            select(WorkOrderModel)
            .options(selectinload(WorkOrderModel.review))
            .where(
                WorkOrderModel.id == work_order_id,
                WorkOrderModel.community_id == community_id,
            )
        )
        model = self._session.scalar(statement)
        return self._to_domain(model) if model else None

    def list(
        self, community_id: UUID, search: WorkOrderSearch, context: RequestContext
    ) -> Sequence[WorkOrder]:
        statement: Select[tuple[WorkOrderModel]] = (
            select(WorkOrderModel)
            .options(selectinload(WorkOrderModel.review))
            .where(WorkOrderModel.community_id == community_id)
            .order_by(WorkOrderModel.created_at.desc())
        )
        if search.house_id is not None:
            statement = statement.where(WorkOrderModel.house_id == search.house_id)
        if search.statuses:
            statement = statement.where(WorkOrderModel.status.in_(search.statuses))
        if search.assigned_to_me:
            statement = statement.where(WorkOrderModel.assignee_id == context.actor_id)
        if search.location:
            statement = statement.where(WorkOrderModel.location == search.location)
        if search.category:
            statement = statement.where(WorkOrderModel.category == search.category)
        if context.has_any_role(Role.RESIDENT) and not context.has_any_role(
            Role.CUSTOMER_SERVICE, Role.MANAGER
        ):
            statement = statement.where(WorkOrderModel.house_id.in_(context.house_ids))
        if context.has_any_role(Role.REPAIR_WORKER) and not context.has_any_role(
            Role.CUSTOMER_SERVICE, Role.MANAGER
        ):
            statement = statement.where(WorkOrderModel.assignee_id == context.actor_id)
        statement = statement.offset(search.offset).limit(search.limit)
        return [self._to_domain(model) for model in self._session.scalars(statement).all()]

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
        created_at,
    ) -> None:
        self._session.add(
            WorkOrderStatusLogModel(
                id=uuid4(),
                community_id=community_id,
                work_order_id=work_order_id,
                from_status=from_status,
                action_code=action.value,
                to_status=to_status,
                operator_id=operator_id,
                operator_role=operator_role.value,
                reason=reason,
                request_id=request_id,
                created_at=created_at,
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
        appointment_at,
        attachment_ids: tuple[UUID, ...],
        created_at,
    ) -> None:
        self._session.add(
            WorkOrderProcessRecordModel(
                id=uuid4(),
                community_id=community_id,
                work_order_id=work_order_id,
                record_type=record_type.value,
                note=note,
                operator_id=operator_id,
                appointment_at=appointment_at,
                attachment_ids=[str(value) for value in attachment_ids],
                created_at=created_at,
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
        created_at,
    ) -> None:
        self._session.add(
            WorkOrderReviewModel(
                id=uuid4(),
                community_id=community_id,
                work_order_id=work_order_id,
                reviewer_id=reviewer_id,
                rating=rating,
                comment=comment,
                created_at=created_at,
            )
        )

    def timeline(self, work_order_id: UUID, community_id: UUID) -> Sequence[TimelineEntry]:
        status_statement = select(WorkOrderStatusLogModel).where(
            WorkOrderStatusLogModel.work_order_id == work_order_id,
            WorkOrderStatusLogModel.community_id == community_id,
        )
        process_statement = select(WorkOrderProcessRecordModel).where(
            WorkOrderProcessRecordModel.work_order_id == work_order_id,
            WorkOrderProcessRecordModel.community_id == community_id,
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
            for item in self._session.scalars(status_statement)
        ]
        entries.extend(
            TimelineEntry(
                entry_type="PROCESS",
                action=item.record_type,
                operator_id=item.operator_id,
                created_at=item.created_at,
                note=item.note,
                appointment_at=item.appointment_at,
                attachment_ids=tuple(UUID(value) for value in item.attachment_ids),
            )
            for item in self._session.scalars(process_statement)
        )
        return sorted(entries, key=lambda item: item.created_at)

    @staticmethod
    def _to_model(work_order: WorkOrder) -> WorkOrderModel:
        return WorkOrderModel(
            id=work_order.id,
            community_id=work_order.community_id,
            business_no=work_order.business_no,
            house_id=work_order.house_id,
            reporter_id=work_order.reporter_id,
            category=work_order.category.value,
            location=work_order.location,
            description=work_order.description,
            urgency=work_order.urgency.value,
            status=work_order.status.value,
            assignee_id=work_order.assignee_id,
            version=work_order.version,
            create_idempotency_key=work_order.create_idempotency_key,
            created_at=work_order.created_at,
            updated_at=work_order.updated_at,
            closed_at=work_order.closed_at,
            appointment_at=work_order.appointment_at,
        )

    @staticmethod
    def _to_domain(model: WorkOrderModel) -> WorkOrder:
        return WorkOrder(
            id=model.id,
            community_id=model.community_id,
            business_no=model.business_no,
            house_id=model.house_id,
            reporter_id=model.reporter_id,
            category=RepairCategory(model.category),
            location=model.location,
            description=model.description,
            urgency=Urgency(model.urgency),
            create_idempotency_key=model.create_idempotency_key,
            status=WorkOrderStatus(model.status),
            assignee_id=model.assignee_id,
            version=model.version,
            created_at=model.created_at,
            updated_at=model.updated_at,
            closed_at=model.closed_at,
            appointment_at=model.appointment_at,
            has_review=model.review is not None,
        )
