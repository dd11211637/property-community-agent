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
