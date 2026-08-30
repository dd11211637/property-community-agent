"""Compatibility checks for direct auth and typed business responses."""

from property_agent.main import create_app


def _response_schema(
    spec: dict[str, object], path: str, method: str, status: str
) -> dict[str, object]:
    paths = spec["paths"]
    operation = paths[path][method]
    return operation["responses"][status]["content"]["application/json"]["schema"]


def test_authentication_responses_remain_direct_models() -> None:
    spec = create_app().openapi()

    assert _response_schema(spec, "/api/auth/login", "post", "200") == {
        "$ref": "#/components/schemas/LoginResponse"
    }
    assert _response_schema(spec, "/api/auth/house", "post", "200") == {
        "$ref": "#/components/schemas/HouseSelectionResponse"
    }


def test_stable_business_responses_use_typed_envelopes() -> None:
    spec = create_app().openapi()
    expected_refs = {
        ("/api/work-orders", "get", "200"): "Envelope_WorkOrderListResponse_",
        ("/api/announcements", "get", "200"): "Envelope_AnnouncementListResponse_",
        ("/api/inspection-tasks", "get", "200"): "Envelope_InspectionTaskListResponse_",
        ("/api/messages", "get", "200"): "Envelope_MessageListResponse_",
        ("/api/admin/dashboard", "get", "200"): "Envelope_AdminDashboardResponse_",
        (
            "/api/agent/conversations/{conversation_id}/messages",
            "post",
            "200",
        ): "Envelope_AgentTurnResponse_",
        ("/api/agent/memories", "get", "200"): "Envelope_list_MemoryResponse__",
    }

    for (path, method, status), schema_name in expected_refs.items():
        assert _response_schema(spec, path, method, status) == {
            "$ref": f"#/components/schemas/{schema_name}"
        }


def test_agent_stream_remains_event_stream_not_envelope_json() -> None:
    spec = create_app().openapi()
    response = spec["paths"]["/api/agent/conversations/{conversation_id}/messages/stream"]["post"][
        "responses"
    ]["200"]

    assert "text/event-stream" in response["content"]
    assert "application/json" not in response["content"]
