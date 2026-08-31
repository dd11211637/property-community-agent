from typing import Any

from property_agent.repair.application.commands import TimelineEntry
from property_agent.repair.application.ports import RequestContext
from property_agent.repair.application.service import WorkOrderService
from property_agent.repair.domain.entities import WorkOrder


def work_order_data(
    work_order: WorkOrder, service: WorkOrderService, context: RequestContext
) -> dict[str, Any]:
    return {
        "id": str(work_order.id),
        "business_no": work_order.business_no,
        "community_id": str(work_order.community_id),
        "house_id": str(work_order.house_id),
        "reporter_id": str(work_order.reporter_id),
        "category": work_order.category.value,
        "location": work_order.location,
        "description": work_order.description,
        "urgency": work_order.urgency.value,
        "status": work_order.status.value,
        "assignee_id": str(work_order.assignee_id) if work_order.assignee_id else None,
        "version": work_order.version,
        "available_actions": [
            action.value for action in service.available_actions(work_order, context)
        ],
        "has_review": work_order.has_review,
        "created_at": work_order.created_at.isoformat(),
        "updated_at": work_order.updated_at.isoformat(),
        "closed_at": work_order.closed_at.isoformat() if work_order.closed_at else None,
        "appointment_at": (
            work_order.appointment_at.isoformat() if work_order.appointment_at else None
        ),
    }


def timeline_entry_data(entry: TimelineEntry) -> dict[str, Any]:
    return {
        "entry_type": entry.entry_type,
        "action": entry.action,
        "operator_id": str(entry.operator_id),
        "created_at": entry.created_at.isoformat(),
        "from_status": entry.from_status,
        "to_status": entry.to_status,
        "reason": entry.reason,
        "note": entry.note,
        "appointment_at": entry.appointment_at.isoformat() if entry.appointment_at else None,
        "attachment_ids": [str(value) for value in entry.attachment_ids],
    }
