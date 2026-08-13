from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from property_agent.announcement.adapters.api.app import create_app
from property_agent.announcement.adapters.api.dependencies import (
    get_announcement_service,
    get_request_context,
)
from property_agent.announcement.application.service import AnnouncementService
from property_agent.platform.context import RequestContext
from property_agent.platform.roles import Role
from tests.announcement.support import Harness


def _client(service, context):
    app = create_app(service)
    app.dependency_overrides[get_announcement_service] = lambda: service
    app.dependency_overrides[get_request_context] = lambda: context
    return TestClient(app)


def test_api_uses_envelope_headers_and_expected_version() -> None:
    harness = Harness(audience_members=(uuid4(),))
    service = AnnouncementService(harness.uow)
    context = RequestContext(uuid4(), uuid4(), frozenset({Role.CUSTOMER_SERVICE}), "api")
    client = _client(service, context)
    response = client.post(
        "/api/announcements",
        json={
            "title": "检修",
            "body": "明日",
            "category": "GENERAL",
            "audience_condition": {"building_ids": ["B1"]},
        },
        headers={"Idempotency-Key": "create"},
    )
    assert response.status_code == 201
    assert response.json()["success"] is True
    item = response.json()["data"]
    edited = client.patch(
        f"/api/announcements/{item['id']}",
        json={
            "title": "检修更新",
            "body": "明日",
            "category": "GENERAL",
            "audience_condition": {"building_ids": ["B1"]},
            "expected_version": item["version"],
        },
        headers={"Idempotency-Key": "edit"},
    )
    assert edited.status_code == 200
    stale = client.post(
        f"/api/announcements/{item['id']}/submit-review",
        json={"expected_version": item["version"]},
        headers={"Idempotency-Key": "submit"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"


def test_api_requires_service_and_authorization() -> None:
    app = create_app()
    client = TestClient(app)
    assert client.get("/api/announcements").status_code == 503
    service = AnnouncementService(Harness().uow)
    resident = RequestContext(uuid4(), uuid4(), frozenset({Role.RESIDENT}), "resident")
    response = _client(service, resident).post(
        "/api/announcements",
        json={"title": "检修", "body": "明日", "category": "GENERAL", "audience_condition": {}},
        headers={"Idempotency-Key": "forbidden"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_manager_can_schedule_an_approved_announcement() -> None:
    community = uuid4()
    harness = Harness(audience_members=(uuid4(),))
    service = AnnouncementService(harness.uow)
    customer = RequestContext(uuid4(), community, frozenset({Role.CUSTOMER_SERVICE}), "customer")
    manager = RequestContext(uuid4(), community, frozenset({Role.MANAGER}), "manager")
    customer_client = _client(service, customer)
    manager_client = _client(service, manager)
    created = customer_client.post(
        "/api/announcements",
        json={
            "title": "消防检查",
            "body": "请相关住户配合检查。",
            "category": "SAFETY",
            "audience_condition": {"building_ids": ["B1"]},
        },
        headers={"Idempotency-Key": "schedule-create"},
    ).json()["data"]
    submitted = customer_client.post(
        f"/api/announcements/{created['id']}/submit-review",
        json={"expected_version": created["version"]},
        headers={"Idempotency-Key": "schedule-submit"},
    ).json()["data"]
    approved = manager_client.post(
        f"/api/announcements/{created['id']}/actions/approve",
        json={"expected_version": submitted["version"]},
        headers={"Idempotency-Key": "schedule-approve"},
    ).json()["data"]
    scheduled_at = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()

    response = manager_client.post(
        f"/api/announcements/{created['id']}/actions/schedule",
        json={
            "expected_version": approved["version"],
            "scheduled_at": scheduled_at,
            "confirmation_token": "confirmed",
        },
        headers={"Idempotency-Key": "schedule-publish"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "APPROVED"
    assert response.json()["data"]["scheduled_at"] == scheduled_at
    assert "SCHEDULE" in response.json()["data"]["available_actions"]
