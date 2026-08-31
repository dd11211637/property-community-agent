import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from property_agent.repair.adapters.api.app import create_app
from property_agent.repair.adapters.api.dependencies import get_request_context
from property_agent.repair.adapters.tool_adapter import (
    EXECUTE_ACTION_INPUT_ADAPTER,
    TOOL_SCHEMAS,
    CreateReviewActionInput,
    RepairToolAdapter,
)
from property_agent.repair.domain.enums import WorkOrderStatus
from tests.conftest import Ids


def create_payload(ids: Ids) -> dict:
    return {
        "house_id": str(ids.house),
        "category": "WATER_PLUMBING",
        "location": "Kitchen",
        "description": "Pipe leak",
        "urgency": "NORMAL",
        "confirmation_token": "confirmed",
        "attachment_ids": [],
    }


def test_create_api_uses_unified_envelope(service, ids, resident_context) -> None:
    app = create_app(service)
    app.dependency_overrides[get_request_context] = lambda: resident_context
    client = TestClient(app)

    response = client.post(
        "/api/work-orders",
        json={**create_payload(ids), "appointment_at": "2026-09-01T15:00:00+08:00"},
        headers={"Idempotency-Key": "api-create", "X-Request-ID": "req_api"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == WorkOrderStatus.PENDING_ASSIGNMENT
    assert body["data"]["appointment_at"] == "2026-09-01T15:00:00+08:00"
    assert body["request_id"] == resident_context.request_id


def test_api_requires_trusted_context(service, ids) -> None:
    client = TestClient(create_app(service))

    response = client.post(
        "/api/work-orders",
        json=create_payload(ids),
        headers={"Idempotency-Key": "api-create"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_tool_adapter_exposes_framework_neutral_schemas(service, ids, resident_context) -> None:
    tools = RepairToolAdapter(service)

    result = tools.create_work_order(
        {
            **create_payload(ids),
            "idempotency_key": "tool-create",
        },
        resident_context,
    )

    assert result["status"] == "PENDING_ASSIGNMENT"
    assert set(TOOL_SCHEMAS) == {
        "search_work_orders",
        "create_work_order",
        "execute_work_order_action",
    }
    assert "parameters" in TOOL_SCHEMAS["create_work_order"]


def test_request_id_header_is_limited_to_database_capacity(service, ids, resident_context) -> None:
    app = create_app(service)
    app.dependency_overrides[get_request_context] = lambda: resident_context
    client = TestClient(app)

    response = client.post(
        "/api/work-orders",
        json=create_payload(ids),
        headers={"Idempotency-Key": "request-id-create", "X-Request-ID": "x" * 65},
    )

    assert response.status_code == 201
    assert response.headers["X-Request-ID"].startswith("req_")
    assert len(response.headers["X-Request-ID"]) <= 64


def test_review_tool_contract_does_not_require_expected_version(ids) -> None:
    payload = EXECUTE_ACTION_INPUT_ADAPTER.validate_python(
        {
            "work_order_id": str(ids.house),
            "action": "CREATE_REVIEW",
            "idempotency_key": "review-key",
            "rating": 5,
        }
    )

    assert isinstance(payload, CreateReviewActionInput)
    with pytest.raises(ValidationError):
        EXECUTE_ACTION_INPUT_ADAPTER.validate_python(
            {
                "work_order_id": str(ids.house),
                "action": "ACCEPT",
                "idempotency_key": "accept-key",
            }
        )
