"""DeepSeek gateway contract, retry, fallback and trusted-slot boundary tests."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from property_agent.agent.model_gateway import (
    DeepSeekModelGateway,
    DeterministicModelGateway,
    FallbackModelGateway,
    ModelAnalysis,
    ModelGatewayError,
)
from property_agent.agent.nodes import classify_intent_node
from property_agent.agent.read_contracts import PlannerAction
from property_agent.agent.state import GraphState
from property_agent.inspection.domain.enums import Role
from property_agent.platform import container
from property_agent.platform.context import RequestContext


def _response(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _gateway(handler) -> DeepSeekModelGateway:
    return DeepSeekModelGateway(
        api_key="secret-test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        connect_timeout_seconds=1,
        read_timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )


def test_deepseek_uses_chat_completions_bearer_and_json_output():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return _response(
            json.dumps(
                {
                    "intent": "REPAIR",
                    "confidence": 0.91,
                    "slots": {"location": "厨房", "description": "水管漏水"},
                }
            )
        )

    result = _gateway(handler).analyze("厨房水管漏水")

    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["authorization"] == "Bearer secret-test-key"
    assert captured["body"]["model"] == "deepseek-v4-flash"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["thinking"] == {"type": "disabled"}
    assert "JSON" in captured["body"]["messages"][0]["content"]
    assert result.intent == "REPAIR"
    assert result.slots["location"] == "厨房"
    assert result.provider == "deepseek"
    assert result.degraded is False


def test_deepseek_revises_announcement_from_original_draft_and_instruction():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured["system"] = payload["messages"][0]["content"]
        captured["input"] = json.loads(payload["messages"][1]["content"])
        return _response(
            json.dumps(
                {
                    "title": "停水通知",
                    "body": "明天上午9点至下午4点暂停供水。",
                    "category": "MAINTENANCE",
                },
                ensure_ascii=False,
            )
        )

    result = _gateway(handler).revise_announcement(
        draft={
            "title": "停水通知",
            "body": "明天暂停供水。",
            "category": "MAINTENANCE",
        },
        audience={},
        instruction="改成明天上午9点至下午4点停水",
    )

    assert captured["input"]["draft"]["body"] == "明天暂停供水。"
    assert captured["input"]["instruction"] == "改成明天上午9点至下午4点停水"
    assert "保留未要求修改的事实和受众" in captured["system"]
    assert result["body"] == "明天上午9点至下午4点暂停供水。"


def test_deterministic_announcement_time_uses_trusted_server_date():
    result = DeterministicModelGateway().analyze_with_context(
        "帮我写公告，明天停水，今晚8点发布",
        history=[],
        trusted_context={"business_date": "2026-08-13"},
    )

    assert result.intent == "ANNOUNCEMENT"
    assert result.slots["target_date"] == "2026-08-14"
    assert result.slots["scheduled_at"] == "2026-08-13T20:00:00+08:00"


def test_deterministic_announcement_guard_does_not_invent_list_action():
    result = DeterministicModelGateway().analyze("修改原因，原因是供水设施损坏")
    assert result.intent == "ANNOUNCEMENT"
    assert "action" not in result.slots


def test_deepseek_retries_once_for_429_then_succeeds():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        return _response('{"intent":"BILLING","confidence":0.8,"slots":{}}')

    assert _gateway(handler).analyze("查账单").intent == "BILLING"
    assert calls == 2


def test_deepseek_retries_once_for_invalid_output_then_fails():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response("not-json")

    with pytest.raises(ModelGatewayError, match="after one retry"):
        _gateway(handler).analyze("查账单")
    assert calls == 2


def test_deepseek_does_not_retry_non_retryable_4xx():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401)

    with pytest.raises(ModelGatewayError, match="HTTP status 401"):
        _gateway(handler).analyze("查账单")
    assert calls == 1


def _read_plan_context():
    return {
        "question": "查询本月账单",
        "intent": "BILLING",
        "slots": {"period": "2026-08"},
        "trusted_context": {"business_date": "2026-08-12"},
        "observations": [],
        "tools": [{"name": "list_bills", "allowed_arguments": ["period"]}],
    }


def test_deepseek_read_planner_retries_once_for_429_then_succeeds():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429)
        return _response(
            '{"action":"CALL_TOOL","tool":"list_bills",'
            '"arguments":{"period":"2026-08"},"reason_code":"NEED_FACTS"}'
        )

    decision = _gateway(handler).plan_read(**_read_plan_context())

    assert calls == 2
    assert decision.action == PlannerAction.CALL_TOOL
    assert decision.tool == "list_bills"


def test_deepseek_read_planner_retries_invalid_json_then_fails():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response("not-json")

    with pytest.raises(ModelGatewayError, match="read planning failed after one retry"):
        _gateway(handler).plan_read(**_read_plan_context())
    assert calls == 2


def test_deepseek_read_planner_does_not_retry_non_retryable_4xx():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401)

    with pytest.raises(ModelGatewayError, match="read-planner HTTP status 401"):
        _gateway(handler).plan_read(**_read_plan_context())
    assert calls == 1


def test_fallback_marks_result_degraded_after_provider_failure():
    primary = _gateway(lambda request: httpx.Response(503))
    gateway = FallbackModelGateway(primary, DeterministicModelGateway())

    result = gateway.analyze("我家水管漏水要报修")

    assert result.intent == "REPAIR"
    assert result.provider == "keyword"
    assert result.degraded is True


def test_keyword_gateway_routes_community_knowledge_question_to_general_help():
    result = DeterministicModelGateway().analyze("物业电话是多少")

    assert result.intent == "GENERAL_HELP"
    assert result.confidence >= 0.5


@pytest.mark.parametrize(
    ("text", "period"),
    [
        ("查询这个月的账单", "2026-08"),
        ("查询上个月的账单", "2026-07"),
        ("查询上上个月的账单", "2026-06"),
        ("查询 2025 年 12 月账单", "2025-12"),
    ],
)
def test_keyword_gateway_resolves_billing_period_from_trusted_clock(text, period):
    gateway = DeterministicModelGateway(today_provider=lambda: date(2026, 8, 12))

    result = gateway.analyze(text)

    assert result.intent == "BILLING"
    assert result.slots == {"query_type": "list", "period": period}


def test_billing_relative_period_guard_overrides_model_date():
    class StalePrimary:
        def ready(self):
            return True

        def analyze(self, text):
            from property_agent.agent.model_gateway import ModelAnalysis

            return ModelAnalysis(
                intent="BILLING",
                confidence=0.95,
                slots={"query_type": "list", "period": "2025-01"},
                provider="deepseek",
            )

    fallback = DeterministicModelGateway(today_provider=lambda: date(2026, 8, 12))
    result = FallbackModelGateway(StalePrimary(), fallback).analyze("查询这个月的账单")

    assert result.slots["period"] == "2026-08"


def test_keyword_guard_corrects_valid_but_wrong_primary_intent_and_keeps_slots():
    class WrongPrimary:
        def ready(self):
            return True

        def analyze(self, text):
            from property_agent.agent.model_gateway import ModelAnalysis

            return ModelAnalysis(
                intent="UNCERTAIN",
                confidence=0.4,
                slots={"query_type": "账单列表"},
                provider="deepseek",
            )

    result = FallbackModelGateway(WrongPrimary(), DeterministicModelGateway()).analyze(
        "查一下我的账单"
    )

    assert result.intent == "BILLING"
    assert result.slots == {"query_type": "账单列表"}
    assert result.provider == "deepseek+keyword_guard"
    assert result.degraded is True


def test_keyword_guard_forces_explicit_repair_create_and_rejects_fake_work_order_id():
    class WrongRepairAction:
        def ready(self):
            return True

        def analyze(self, text):
            from property_agent.agent.model_gateway import ModelAnalysis

            return ModelAnalysis(
                intent="REPAIR",
                confidence=0.93,
                slots={
                    "action": "query",
                    "work_order_id": "E2E取消报修-123",
                    "category": "电气及照明",
                    "location": "客厅",
                    "description": "电灯损坏",
                },
                provider="deepseek",
            )

    result = FallbackModelGateway(WrongRepairAction(), DeterministicModelGateway()).analyze(
        "我要报修，客厅电灯损坏，E2E取消报修-123"
    )

    assert result.intent == "REPAIR"
    assert result.slots["action"] == "create"
    assert "work_order_id" not in result.slots


def test_keyword_guard_keeps_valid_work_order_number_for_progress_query():
    class QueryAnalysis:
        def ready(self):
            return True

        def analyze(self, text):
            from property_agent.agent.model_gateway import ModelAnalysis

            return ModelAnalysis(
                intent="REPAIR",
                confidence=0.91,
                slots={"action": "create", "work_order_id": "WX-20260812-ABC123"},
                provider="deepseek",
            )

    result = FallbackModelGateway(QueryAnalysis(), DeterministicModelGateway()).analyze(
        "查询工单进度 WX-20260812-ABC123"
    )

    assert result.slots == {"action": "query", "work_order_id": "WX-20260812-ABC123"}


def test_keyword_guard_fills_explicit_repair_slots_when_model_omits_them():
    class SparseAnalysis:
        def ready(self):
            return True

        def analyze(self, text):
            from property_agent.agent.model_gateway import ModelAnalysis

            return ModelAnalysis(
                intent="REPAIR",
                confidence=0.9,
                slots={"action": "create"},
                provider="deepseek",
            )

    result = FallbackModelGateway(SparseAnalysis(), DeterministicModelGateway()).analyze(
        "客厅电灯坏了，需要报修"
    )

    assert result.slots["category"] == "ELECTRICAL"
    assert result.slots["location"] == "客厅"
    assert result.slots["description"] == "客厅电灯坏了，需要报修"


def test_keyword_guard_fills_explicit_inspection_create_slots_when_model_omits_them():
    class SparseInspectionAnalysis:
        def ready(self):
            return True

        def analyze(self, text):
            return ModelAnalysis(
                intent="INSPECTION",
                confidence=0.95,
                slots={"action": "query_tasks"},
                provider="deepseek",
            )

    result = FallbackModelGateway(SparseInspectionAnalysis(), DeterministicModelGateway()).analyze(
        "我要对1栋1单元所有消防设施进行巡检"
    )

    assert result.slots["action"] == "create"
    assert result.slots["title"] == "消防设施巡检"
    assert result.slots["description"] == "对1栋1单元所有消防设施进行巡检"
    assert result.slots["point"] == "1栋1单元"


def test_model_slots_cannot_override_trusted_or_existing_values():
    class SuggestedGateway:
        def ready(self):
            return True

        def analyze(self, text):
            from property_agent.agent.model_gateway import ModelAnalysis

            return ModelAnalysis(
                intent="REPAIR",
                confidence=0.9,
                slots={
                    "actor_id": "attacker",
                    "community_id": "other",
                    "house_id": "other-house",
                    "expected_version": 99,
                    "tool": "announce_publish",
                    "location": "模型位置",
                    "description": "水管漏水",
                },
            )

    state = GraphState(
        conversation_id="trusted-boundary",
        actor_id="real-user",
        community_id="real-community",
        current_house_id="real-house",
        slots={"user_text": "漏水", "location": "用户位置"},
    )
    classify_intent_node(SuggestedGateway())(state)

    assert state.actor_id == "real-user"
    assert state.community_id == "real-community"
    assert state.current_house_id == "real-house"
    assert state.slots["location"] == "用户位置"
    assert state.slots["description"] == "水管漏水"
    assert "actor_id" not in state.slots
    assert "house_id" not in state.slots
    assert "expected_version" not in state.slots
    assert "tool" not in state.slots


def test_container_selects_keyword_without_key_and_fallback_with_key(monkeypatch):
    monkeypatch.setattr(container.settings, "deepseek_api_key", "")
    assert isinstance(container.build_model_gateway(), DeterministicModelGateway)

    monkeypatch.setattr(container.settings, "deepseek_api_key", "configured")
    assert isinstance(container.build_model_gateway(), FallbackModelGateway)


def test_agent_context_binds_only_an_authenticated_house(monkeypatch):
    actor_id, community_id, house_id, other_house = (uuid4() for _ in range(4))
    trusted = RequestContext(
        actor_id=actor_id,
        community_id=community_id,
        roles=frozenset({"RESIDENT"}),
        request_id="req-house",
        current_house_id=None,
        bound_house_ids=frozenset({house_id}),
    )
    monkeypatch.setattr(RequestContext, "current", classmethod(lambda cls: trusted))

    state = GraphState(
        conversation_id="house-context",
        actor_id=actor_id,
        community_id=community_id,
        current_house_id=house_id,
    )
    resolved = container.resolve_agent_request_context(state)
    assert resolved.actor_id == actor_id
    assert resolved.current_house_id == house_id
    assert resolved.house_ids == frozenset({house_id})

    state.current_house_id = other_house
    with pytest.raises(ValueError, match="not bound"):
        container.resolve_agent_request_context(state)


def test_agent_inspection_context_maps_platform_security_role(monkeypatch):
    actor_id, community_id = uuid4(), uuid4()
    trusted = RequestContext(
        actor_id=actor_id,
        community_id=community_id,
        roles=frozenset({"SECURITY_GUARD"}),
        request_id="req-inspection-role",
    )
    monkeypatch.setattr(RequestContext, "current", classmethod(lambda cls: trusted))

    state = GraphState(
        conversation_id="inspection-role",
        actor_id=actor_id,
        community_id=community_id,
    )
    platform_context = container.resolve_agent_request_context(state)
    inspection_context = container.to_inspection_context(
        platform_context, platform_context.request_id
    )

    assert inspection_context.roles == frozenset({Role.SECURITY_STAFF})


def test_four_domain_expression_dataset_routes_deterministically():
    dataset = json.loads(
        (Path(__file__).parent / "data" / "agent_eval_cases.json").read_text(encoding="utf-8")
    )
    gateway = DeterministicModelGateway()
    domain_cases = dataset["domain_utterances"]

    assert set(domain_cases) == {"REPAIR", "ANNOUNCEMENT", "BILLING", "INSPECTION"}
    assert all(len(cases) == 10 for cases in domain_cases.values())
    for expected, cases in domain_cases.items():
        for text in cases:
            assert gateway.analyze(text).intent == expected, text


def test_keyword_gateway_resolves_contextual_previous_month():
    gateway = DeterministicModelGateway(today_provider=lambda: date(2026, 8, 12))

    result = gateway.analyze_with_context(
        "那上个月呢",
        history=[
            {"role": "user", "content": "查询本月账单"},
            {"role": "assistant", "content": "我查到 2026-08 账单共 430 元。"},
        ],
        trusted_context={"business_date": "2026-08-12"},
    )

    assert result.intent == "BILLING"
    assert result.slots == {"period": "2026-07", "query_type": "list"}


def test_keyword_gateway_extracts_trusted_announcement_date_and_topic():
    gateway = DeterministicModelGateway(today_provider=lambda: date(2026, 8, 12))

    result = gateway.analyze("今天会停水吗")

    assert result.intent == "ANNOUNCEMENT"
    assert result.slots["topic"] == "WATER_OUTAGE"
    assert result.slots["target_date"] == "2026-08-12"


def test_fallback_keeps_explicit_keyword_confidence_when_model_is_tentative():
    class TentativeAnnouncementGateway:
        def ready(self):
            return True

        def analyze(self, text):
            return ModelAnalysis("ANNOUNCEMENT", 0.2, {})

        def analyze_with_context(self, text, *, history, trusted_context):
            return self.analyze(text)

    gateway = FallbackModelGateway(
        TentativeAnnouncementGateway(),
        DeterministicModelGateway(today_provider=lambda: date(2026, 8, 12)),
    )

    result = gateway.analyze("今天会停水吗")

    assert result.intent == "ANNOUNCEMENT"
    assert result.confidence >= 0.5
    assert result.slots["topic"] == "WATER_OUTAGE"


def test_edge_dataset_declares_required_safety_scenarios():
    dataset = json.loads(
        (Path(__file__).parent / "data" / "agent_eval_cases.json").read_text(encoding="utf-8")
    )
    assert set(dataset["edge_cases"]) == {
        "ambiguous",
        "high_risk",
        "unauthorized",
        "model_failure",
        "tool_failure",
        "missing_fields",
        "conflicting_information",
    }
    assert all(len(cases) == 5 for cases in dataset["edge_cases"].values())
