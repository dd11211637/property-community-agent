"""Resolve trusted work-order identifiers into user-facing display labels."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from property_agent.platform.infrastructure.orm_models import HouseModel, UserModel
from property_agent.repair.adapters.presentation import work_order_data
from property_agent.repair.application.ports import RequestContext
from property_agent.repair.application.service import WorkOrderService
from property_agent.repair.domain.entities import WorkOrder
from property_agent.repair.infrastructure.models import WorkOrderProcessRecordModel


@dataclass(frozen=True, slots=True)
class ServiceFacts:
    phase: str
    appointment: dict | None
    completion_attachment_ids: tuple[str, ...]


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
        service_facts = self._service_facts(work_orders)
        return [
            {
                **work_order_data(item, service, context),
                "reporter_name": users.get(item.reporter_id),
                "assignee_name": users.get(item.assignee_id) if item.assignee_id else None,
                "house_display": houses.get(item.house_id),
                "contact_name": item.contact_name,
                "contact_phone": item.contact_phone,
                "access_instructions": item.access_instructions,
                "preferred_time_windows": list(item.preferred_time_windows),
                "request_attachment_ids": [str(value) for value in item.request_attachment_ids],
                "completion_attachment_ids": list(service_facts[item.id].completion_attachment_ids),
                "current_appointment": service_facts[item.id].appointment,
                "service_phase": service_facts[item.id].phase,
            }
            for item in work_orders
        ]

    def _service_facts(self, work_orders: Sequence[WorkOrder]) -> dict[UUID, ServiceFacts]:
        identifiers = {item.id for item in work_orders}
        rows = self._session.scalars(
            select(WorkOrderProcessRecordModel)
            .where(WorkOrderProcessRecordModel.work_order_id.in_(identifiers))
            .order_by(WorkOrderProcessRecordModel.created_at)
        ).all()
        by_order: dict[UUID, list[WorkOrderProcessRecordModel]] = {
            identifier: [] for identifier in identifiers
        }
        for row in rows:
            by_order[row.work_order_id].append(row)
        return {item.id: self._facts_for(item, by_order[item.id]) for item in work_orders}

    @staticmethod
    def _facts_for(
        work_order: WorkOrder, records: Sequence[WorkOrderProcessRecordModel]
    ) -> ServiceFacts:
        appointments = [record for record in records if record.record_type == "APPOINTMENT"]
        latest_appointment = appointments[-1] if appointments else None
        completions = [record for record in records if record.record_type == "COMPLETION"]
        attachments = tuple(
            value for record in completions for value in (record.attachment_ids or ())
        )
        appointment = (
            {
                "appointment_at": latest_appointment.appointment_at.isoformat(),
                "note": latest_appointment.note,
                "recorded_at": latest_appointment.created_at.isoformat(),
            }
            if latest_appointment and latest_appointment.appointment_at
            else None
        )
        return ServiceFacts(
            phase=WorkOrderPresenter._service_phase(work_order, records),
            appointment=appointment,
            completion_attachment_ids=attachments,
        )

    @staticmethod
    def _service_phase(
        work_order: WorkOrder, records: Sequence[WorkOrderProcessRecordModel]
    ) -> str:
        status_phases = {
            "PENDING_ASSIGNMENT": "REQUESTED",
            "PENDING_ACCEPTANCE": "ASSIGNED",
            "PENDING_VERIFICATION": "AWAITING_ACCEPTANCE",
            "REWORKING": "REWORK",
            "CLOSED": "CLOSED",
        }
        if work_order.status.value in status_phases:
            return status_phases[work_order.status.value]
        record_phases = {
            "APPOINTMENT": "APPOINTMENT_SCHEDULED",
            "ARRIVAL": "ARRIVED",
            "PROGRESS": "IN_SERVICE",
            "BLOCKED": "BLOCKED",
            "COMPLETION": "AWAITING_ACCEPTANCE",
        }
        return record_phases.get(records[-1].record_type, "IN_SERVICE") if records else "IN_SERVICE"

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
