from fastapi.testclient import TestClient

from property_agent.inspection.adapters.api.app import create_app
from property_agent.inspection.adapters.api.dependencies import (
    get_event_service,
    get_request_context,
    get_task_service,
)
from property_agent.inspection.adapters.tool_adapter import InspectionToolAdapter


def _client(task_service, event_service, context):
    app = create_app(task_service, event_service)
    app.dependency_overrides[get_task_service] = lambda: task_service
    app.dependency_overrides[get_event_service] = lambda: event_service
    app.dependency_overrides[get_request_context] = lambda: context
    return TestClient(app)


def test_api_create_task_via_manager(client_maker, task_service, manager_context):
    client = client_maker(task_service, None, manager_context)
    resp = client.post(
        "/api/inspection-tasks",
        json={"title": "夜间巡检", "description": "B1-B3", "route_points": ["B1", "B2"]},
        headers={"Idempotency-Key": "api-k1"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["status"] == "PLANNED"
    assert "ASSIGN" in body["data"]["available_actions"]


def test_api_create_task_missing_idempotency(client_maker, task_service, manager_context):
    client = client_maker(task_service, None, manager_context)
    resp = client.post(
        "/api/inspection-tasks",
        json={"title": "夜间巡检", "description": "B1-B3", "route_points": ["B1", "B2"]},
    )
    assert resp.status_code == 422


def test_api_create_task_forbidden_for_resident(client_maker, task_service, resident_context):
    client = client_maker(task_service, None, resident_context)
    resp = client.post(
        "/api/inspection-tasks",
        json={"title": "夜间巡检", "description": "B1-B3", "route_points": ["B1", "B2"]},
        headers={"Idempotency-Key": "api-k2"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_api_submit_records_requires_confirmation(
    client_maker, task_service, ids, manager_context, security_context
):
    client = client_maker(task_service, None, manager_context)
    created = client.post(
        "/api/inspection-tasks",
        json={"title": "夜间巡检", "description": "B1-B3", "route_points": ["B1", "B2"]},
        headers={"Idempotency-Key": "api-life-1"},
    ).json()["data"]
    tid = created["id"]
    v1 = created["version"]
    client.post(
        f"/api/inspection-tasks/{tid}/actions/assign",
        json={"expected_version": v1, "assignee_id": str(ids.security_worker)},
        headers={"Idempotency-Key": "api-life-assign"},
    )
    # 切换到安保身份
    client2 = client_maker(task_service, None, security_context)
    started = client2.post(
        f"/api/inspection-tasks/{tid}/actions/start",
        json={"expected_version": v1 + 1},
        headers={"Idempotency-Key": "api-life-start"},
    ).json()["data"]
    resp = client2.post(
        f"/api/inspection-tasks/{tid}/actions/submit-records",
        json={
            "expected_version": started["version"],
            "record_type": "COMPLETION",
            "note": "无异常",
        },
        headers={"Idempotency-Key": "api-life-submit"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "CONFIRMATION_REQUIRED"


def test_api_create_high_risk_event(client_maker, event_service, harness, resident_context):
    client = client_maker(None, event_service, resident_context)
    resp = client.post(
        "/api/security-events",
        json={
            "event_type": "GAS_LEAK",
            "risk_level": "HIGH_RISK",
            "location": "B2 车库",
            "description": "明显燃气气味",
            "confirmation_token": "ct-1",
        },
        headers={"Idempotency-Key": "api-ev-1"},
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["risk_level"] == "HIGH_RISK"
    assert any(m["event_type"] == "HIGH_RISK_EVENT" for m in harness.state.messages)


def test_api_event_timeline(client_maker, event_service, ids, manager_context, security_context):
    client = client_maker(None, event_service, security_context)
    created = client.post(
        "/api/security-events",
        json={
            "event_type": "EQUIPMENT_FAULT",
            "risk_level": "MEDIUM",
            "location": "B2",
            "description": "消防栓漏水",
            "confirmation_token": "ct-1",
        },
        headers={"Idempotency-Key": "api-tl-1"},
    ).json()["data"]
    eid = created["id"]
    client2 = client_maker(None, event_service, manager_context)
    client2.post(
        f"/api/security-events/{eid}/actions/assign",
        json={"expected_version": created["version"], "assignee_id": str(ids.security_worker)},
        headers={"Idempotency-Key": "api-tl-assign"},
    )
    resp = client2.get(f"/api/security-events/{eid}/timeline")
    assert resp.status_code == 200
    assert isinstance(resp.json()["data"], list)
    assert len(resp.json()["data"]) >= 1


# ----------------------- Agent 工具 -----------------------
def test_tool_adapter_task_lifecycle(
    task_service, event_service, ids, manager_context, security_context
):
    adapter = InspectionToolAdapter(task_service, event_service)
    created = adapter.create_inspection_task(
        {
            "title": "夜间巡检",
            "description": "B1-B3",
            "route_points": ["B1", "B2"],
            "idempotency_key": "tool-1",
        },
        manager_context,
    )
    assert created["status"] == "PLANNED"
    tid = created["id"]
    v = created["version"]
    assigned = adapter.execute_inspection_task_action(
        {
            "task_id": tid,
            "action": "ASSIGN",
            "expected_version": v,
            "assignee_id": str(ids.security_worker),
            "idempotency_key": "tool-assign",
        },
        manager_context,
    )
    assert assigned["status"] == "ASSIGNED"
    started = adapter.execute_inspection_task_action(
        {
            "task_id": tid,
            "action": "START",
            "expected_version": assigned["version"],
            "idempotency_key": "tool-start",
        },
        security_context,
    )
    submitted = adapter.execute_inspection_task_action(
        {
            "task_id": tid,
            "action": "SUBMIT_RECORDS",
            "expected_version": started["version"],
            "record_type": "COMPLETION",
            "note": "无异常",
            "confirmation_token": "ct-1",
            "idempotency_key": "tool-submit",
        },
        security_context,
    )
    assert submitted["status"] == "SUBMITTED"
    searched = adapter.search_inspection_tasks({"assigned_to_me": True}, security_context)
    assert searched["items"][0]["id"] == tid


def test_tool_adapter_security_event_high_risk(
    task_service, event_service, harness, resident_context
):
    adapter = InspectionToolAdapter(task_service, event_service)
    created = adapter.create_security_event(
        {
            "event_type": "GAS_LEAK",
            "risk_level": "HIGH_RISK",
            "location": "B2 车库",
            "description": "明显燃气气味",
            "confirmation_token": "ct-1",
            "idempotency_key": "tool-ev-1",
        },
        resident_context,
    )
    assert created["risk_level"] == "HIGH_RISK"
    assert any(m["event_type"] == "HIGH_RISK_EVENT" for m in harness.state.messages)
    searched = adapter.search_security_events({"risk_levels": ["HIGH_RISK"]}, resident_context)
    assert len(searched["items"]) == 1
