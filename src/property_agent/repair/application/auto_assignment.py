"""Deterministic Agent-side repair assignment using trusted staff and workload data."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from property_agent.platform.context import RequestContext as PlatformRequestContext
from property_agent.platform.infrastructure.orm_models import HouseModel, UserModel, UserRoleModel
from property_agent.repair.application.commands import ExecuteActionCommand
from property_agent.repair.application.ports import RequestContext
from property_agent.repair.application.service import WorkOrderService
from property_agent.repair.domain.entities import WorkOrder
from property_agent.repair.domain.enums import ActionCode, Role, WorkOrderStatus
from property_agent.repair.domain.errors import BusinessError
from property_agent.repair.infrastructure.models import WorkOrderModel

SYSTEM_DISPATCHER_ID = UUID(int=0)
OPEN_ASSIGNED_STATUSES = (
    WorkOrderStatus.PENDING_ACCEPTANCE.value,
    WorkOrderStatus.PROCESSING.value,
    WorkOrderStatus.REWORKING.value,
)


class AutoAssignmentService:
    """Select the least-loaded eligible worker and reuse the existing assignment state machine."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        work_orders: WorkOrderService,
    ) -> None:
        self._session_factory = session_factory
        self._work_orders = work_orders

    def assign(self, work_order: WorkOrder, *, request_id: str) -> WorkOrder:
        if work_order.status != WorkOrderStatus.PENDING_ASSIGNMENT:
            return work_order
        assignee_id = self._select_candidate(work_order)
        if assignee_id is None:
            return work_order
        context = RequestContext(
            actor_id=SYSTEM_DISPATCHER_ID,
            community_id=work_order.community_id,
            roles=frozenset({Role.MANAGER}),
            request_id=f"{request_id}-auto-assign"[:64],
        )
        try:
            return self._work_orders.execute_action(
                work_order.id,
                ExecuteActionCommand(
                    action=ActionCode.ASSIGN,
                    expected_version=work_order.version,
                    assignee_id=assignee_id,
                ),
                context,
                idempotency_key=f"auto-assign:{work_order.id}",
            )
        except BusinessError as exc:
            if exc.code != "VERSION_CONFLICT":
                raise
            return self._work_orders.get(work_order.id, context)

    def _select_candidate(self, work_order: WorkOrder) -> UUID | None:
        now = datetime.now(UTC)
        with self._session_factory() as session:
            house = session.get(HouseModel, work_order.house_id)
            scopes = (
                ("*",) if house is None else ("*", house.building, f"BUILDING:{house.building}")
            )
            workload = func.count(func.distinct(WorkOrderModel.id)).label("workload")
            statement = (
                select(UserModel.id, workload)
                .join(UserRoleModel, UserRoleModel.user_id == UserModel.id)
                .outerjoin(
                    WorkOrderModel,
                    and_(
                        WorkOrderModel.assignee_id == UserModel.id,
                        WorkOrderModel.status.in_(OPEN_ASSIGNED_STATUSES),
                    ),
                )
                .where(
                    UserModel.community_id == work_order.community_id,
                    UserModel.status == "ACTIVE",
                    UserRoleModel.role == "REPAIR_WORKER",
                    UserRoleModel.scope.in_(scopes),
                    UserRoleModel.valid_from <= now,
                    or_(UserRoleModel.valid_until.is_(None), UserRoleModel.valid_until > now),
                )
                .group_by(UserModel.id)
                .order_by(workload.asc(), UserModel.id.asc())
                .limit(1)
            )
            row = session.execute(statement).first()
            return row[0] if row else None


class AutoAssigningWorkOrderService:
    """Agent-facing facade: create first, then deterministically dispatch without model input."""

    def __init__(self, service: WorkOrderService, assignments: AutoAssignmentService) -> None:
        self._service = service
        self._assignments = assignments

    def create(self, command: Any, context: PlatformRequestContext, *, idempotency_key: str):
        work_order = self._service.create(command, context, idempotency_key=idempotency_key)
        return self._assignments.assign(work_order, request_id=context.request_id)

    def search(self, search: Any, context: PlatformRequestContext):
        return self._service.search(search, context)

    def get(self, work_order_id: UUID, context: PlatformRequestContext):
        return self._service.get(work_order_id, context)

    def timeline(self, work_order_id: UUID, context: PlatformRequestContext):
        return self._service.timeline(work_order_id, context)


def build_agent_work_order_service(
    session_factory: sessionmaker[Session], service: WorkOrderService
) -> AutoAssigningWorkOrderService:
    return AutoAssigningWorkOrderService(
        service,
        AutoAssignmentService(session_factory, service),
    )
