"""Compatibility serialization for idempotent repair responses."""

from datetime import datetime
from typing import Any
from uuid import UUID

from property_agent.repair.domain.entities import WorkOrder
from property_agent.repair.domain.enums import RepairCategory, Urgency, WorkOrderStatus


def work_order_snapshot(work_order: WorkOrder) -> dict[str, Any]:
    return {
        "id": str(work_order.id),
        "community_id": str(work_order.community_id),
        "business_no": work_order.business_no,
        "house_id": str(work_order.house_id),
        "reporter_id": str(work_order.reporter_id),
        "category": work_order.category.value,
        "location": work_order.location,
        "description": work_order.description,
        "urgency": work_order.urgency.value,
        "create_idempotency_key": work_order.create_idempotency_key,
        "contact_name": work_order.contact_name,
        "contact_phone": work_order.contact_phone,
        "access_instructions": work_order.access_instructions,
        "preferred_time_windows": list(work_order.preferred_time_windows),
        "request_attachment_ids": [str(value) for value in work_order.request_attachment_ids],
        "status": work_order.status.value,
        "assignee_id": str(work_order.assignee_id) if work_order.assignee_id else None,
        "version": work_order.version,
        "created_at": work_order.created_at.isoformat(),
        "updated_at": work_order.updated_at.isoformat(),
        "closed_at": work_order.closed_at.isoformat() if work_order.closed_at else None,
        "has_review": work_order.has_review,
    }


def work_order_from_snapshot(snapshot: dict[str, Any]) -> WorkOrder:
    return WorkOrder(
        id=UUID(snapshot["id"]),
        community_id=UUID(snapshot["community_id"]),
        business_no=snapshot["business_no"],
        house_id=UUID(snapshot["house_id"]),
        reporter_id=UUID(snapshot["reporter_id"]),
        category=RepairCategory(snapshot["category"]),
        location=snapshot["location"],
        description=snapshot["description"],
        urgency=Urgency(snapshot["urgency"]),
        create_idempotency_key=snapshot["create_idempotency_key"],
        contact_name=snapshot.get("contact_name"),
        contact_phone=snapshot.get("contact_phone"),
        access_instructions=snapshot.get("access_instructions"),
        preferred_time_windows=tuple(snapshot.get("preferred_time_windows") or ()),
        request_attachment_ids=tuple(
            UUID(value) for value in snapshot.get("request_attachment_ids") or ()
        ),
        status=WorkOrderStatus(snapshot["status"]),
        assignee_id=UUID(snapshot["assignee_id"]) if snapshot["assignee_id"] else None,
        version=snapshot["version"],
        created_at=datetime.fromisoformat(snapshot["created_at"]),
        updated_at=datetime.fromisoformat(snapshot["updated_at"]),
        closed_at=(
            datetime.fromisoformat(snapshot["closed_at"]) if snapshot["closed_at"] else None
        ),
        has_review=snapshot["has_review"],
    )
