"""Resolve trusted work-order identifiers into user-facing display labels."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from property_agent.platform.infrastructure.orm_models import HouseModel, UserModel
from property_agent.repair.adapters.presentation import work_order_data
from property_agent.repair.application.ports import RequestContext
from property_agent.repair.application.service import WorkOrderService
from property_agent.repair.domain.entities import WorkOrder


class WorkOrderPresenter:
    """Build work-order DTOs without replacing authoritative identifiers."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def present(
        self,
        work_order: WorkOrder,
        service: WorkOrderService,
        context: RequestContext,
    ) -> dict:
        return self.present_many([work_order], service, context)[0]

    def present_many(
        self,
        work_orders: Sequence[WorkOrder],
        service: WorkOrderService,
        context: RequestContext,
    ) -> list[dict]:
        users = self._user_names(work_orders)
        houses = self._house_labels(work_orders)
        return [
            {
                **work_order_data(item, service, context),
                "reporter_name": users.get(item.reporter_id),
                "assignee_name": users.get(item.assignee_id) if item.assignee_id else None,
                "house_display": houses.get(item.house_id),
            }
            for item in work_orders
        ]

    def _user_names(self, work_orders: Sequence[WorkOrder]) -> dict[UUID, str]:
        identifiers = {
            identifier
            for item in work_orders
            for identifier in (item.reporter_id, item.assignee_id)
            if identifier is not None
        }
        if not identifiers:
            return {}
        rows = self._session.execute(
            select(UserModel.id, UserModel.display_name).where(UserModel.id.in_(identifiers))
        )
        return {user_id: display_name for user_id, display_name in rows}

    def _house_labels(self, work_orders: Sequence[WorkOrder]) -> dict[UUID, str]:
        identifiers = {item.house_id for item in work_orders}
        if not identifiers:
            return {}
        rows = self._session.execute(select(HouseModel).where(HouseModel.id.in_(identifiers)))
        return {row.id: self._house_label(row) for row in rows.scalars()}

    @staticmethod
    def _house_label(house: HouseModel) -> str:
        building = house.building if house.building.endswith("栋") else f"{house.building}栋"
        unit = house.unit if house.unit.endswith("单元") else f"{house.unit}单元"
        room = house.room_no if house.room_no.endswith("室") else f"{house.room_no}室"
        return f"{building} {unit} {room}"
