from fastapi.testclient import TestClient

from property_agent.announcement.application.service import AnnouncementService
from property_agent.inspection.adapters.api.dependencies import (
    get_event_service,
    get_task_service,
)
from property_agent.inspection.application.service import (
    InspectionTaskService,
    SecurityEventService,
)
from property_agent.main import create_app
from property_agent.platform.context import RequestContext
from property_agent.platform.dependencies import get_request_context
from property_agent.platform.roles import Role
from tests.announcement.support import Harness as AnnouncementHarness
from tests.conftest import Ids as RepairIds
from tests.inspection.support import Harness as InspectionHarness


def test_project_app_registers_repair_and_inspection_routes() -> None:
    app = create_app()

    paths = app.openapi()["paths"]

    assert "/api/work-orders" in paths
    assert "/api/inspection-tasks" in paths
    assert "/api/security-events" in paths
    assert "/api/bills" in paths
    assert "/api/announcements" in paths
    assert "/health" in paths


def test_project_app_runs_both_module_entry_points(
    service,
    ids: RepairIds,
    resident_context,
) -> None:
    inspection_harness = InspectionHarness(security_workers=set(), duty_users=[])
    task_service = InspectionTaskService(inspection_harness.uow)
    event_service = SecurityEventService(inspection_harness.uow)
    announcement_service = AnnouncementService(AnnouncementHarness().uow)
    integrated_context = RequestContext(
        actor_id=resident_context.actor_id,
        community_id=resident_context.community_id,
        roles=frozenset({Role.RESIDENT, Role.MANAGER}),
        house_ids=resident_context.house_ids,
        request_id="req_main",
    )
    app = create_app(
        repair_service=service,
        inspection_task_service=task_service,
        security_event_service=event_service,
        announcement_service=announcement_service,
    )
    app.dependency_overrides[get_request_context] = lambda: integrated_context
    app.dependency_overrides[get_task_service] = lambda: task_service
    app.dependency_overrides[get_event_service] = lambda: event_service
    client = TestClient(app)

    repair_response = client.post(
        "/api/work-orders",
        json={
            "house_id": str(ids.house),
            "category": "WATER_PLUMBING",
            "location": "Kitchen",
            "description": "Pipe leak",
            "urgency": "NORMAL",
            "confirmation_token": "confirmed",
            "attachment_ids": [],
        },
        headers={"Idempotency-Key": "main-repair-create"},
    )
    inspection_response = client.post(
        "/api/inspection-tasks",
        json={
            "title": "Night inspection",
            "description": "Building route",
            "route_points": ["B1", "B2"],
        },
        headers={"Idempotency-Key": "main-inspection-create"},
    )

    assert repair_response.status_code == 201
    assert inspection_response.status_code == 201
    assert repair_response.json()["data"]["status"] == "PENDING_ASSIGNMENT"
    assert inspection_response.json()["data"]["status"] == "PLANNED"
    assert client.get("/health").json() == {"status": "ok"}
