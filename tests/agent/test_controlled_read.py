"""Single-step, trajectory and final-facts tests for the controlled read runtime."""

from uuid import uuid4

from property_agent.agent.adapters.api.presentation import turn_data
from property_agent.agent.application.conversation_service import ConversationSnapshot
from property_agent.agent.application.runner import AgentTurn
from property_agent.agent.controlled_read import ReadPlanGuard, build_controlled_read_node
from property_agent.agent.nodes.explain_result import explain_result_node
from property_agent.agent.read_contracts import PlannerAction, PlannerDecision
from property_agent.agent.read_planner import GatewayReadPlanner
from property_agent.agent.read_tools import read_tool_specs
from property_agent.agent.state import GraphState
from property_agent.agent.tools.base import ok


def state(intent="ANNOUNCEMENT", **slots):
    value = GraphState(
        conversation_id="react-test",
        actor_id=uuid4(),
        community_id=uuid4(),
        current_house_id=uuid4(),
        intent=intent,
        slots={"user_text": "今天会停水吗", **slots},
    )
    value.trusted_context = {
        "business_date": "2026-08-12",
        "community_name": "幸福小区",
        "building": "1",
    }
    return value


def test_single_step_guard_rejects_write_and_identity_arguments():
    guard = ReadPlanGuard(read_tool_specs(), max_steps=5)
    for decision, code in (
        (
            PlannerDecision(PlannerAction.CALL_TOOL, "repair_create", {}),
            "UNKNOWN_READ_TOOL",
        ),
        (
            PlannerDecision(
                PlannerAction.CALL_TOOL,
                "list_bills",
                {"community_id": "attacker"},
            ),
            "TRUSTED_ARGUMENT_OVERRIDE",
        ),
    ):
        try:
            guard.validate(decision, step=0, fingerprints=set())
        except ValueError as exc:
            assert str(exc) == code
        else:  # pragma: no cover
            raise AssertionError("guard must reject unsafe plan")


def test_trajectory_queries_context_date_and_announcement_then_finishes():
    planner = GatewayReadPlanner(object())
    calls = []

    def tool(name, **data):
        def invoke(_state, _arguments):
            calls.append(name)
            return ok(name, **data)

        return invoke

    node = build_controlled_read_node(
        planner=planner,
        specs=read_tool_specs(),
        tools={
            "get_current_context": tool("get_current_context", building="1"),
            "get_business_date": tool("get_business_date", business_date="2026-08-12"),
            "search_announcements": tool(
                "search_announcements",
                count=0,
                items=[],
                target_date="2026-08-12",
                topic="WATER_OUTAGE",
            ),
        },
    )

    result = node(state(topic="WATER_OUTAGE", target_date="2026-08-12"))

    assert calls == ["get_current_context", "get_business_date", "search_announcements"]
    assert result.read_trace["finish_reason"] == "ANSWER_READY"
    assert result.read_trace["step_count"] == 3
    assert result.tool_result["data"]["count"] == 0
    assert len(result.read_facts["observations"]) == 3


def test_inspection_completion_query_returns_exact_summary_facts():
    planner = GatewayReadPlanner(object())
    calls = []

    def invoke(_state, arguments):
        calls.append(arguments)
        return ok(
            "list_inspection_tasks",
            target="task",
            count=2,
            total=7,
            completed=5,
            incomplete=2,
            status_counts={"COMPLETED": 5, "IN_PROGRESS": 2},
            items=[
                {"entity_type": "INSPECTION_TASK", "title": "消防巡检", "status": "IN_PROGRESS"},
                {"entity_type": "INSPECTION_TASK", "title": "车库巡检", "status": "IN_PROGRESS"},
            ],
        )

    node = build_controlled_read_node(
        planner=planner,
        specs=read_tool_specs(),
        tools={
            "get_current_context": lambda _state, _arguments: ok("get_current_context"),
            "list_inspection_tasks": invoke,
        },
    )
    value = state(intent="INSPECTION", user_text="巡检任务都完成了吗", action="query")
    result = node(value)
    explain_result_node()(result)

    assert calls == [{}]
    assert result.tool_result["data"]["total"] == 7
    assert "还有 2 项未完成" in result.messages[-1]["content"]


def test_repeated_tool_call_stops_without_second_execution():
    class RepeatingPlanner:
        def plan_read(self, **_context):
            return PlannerDecision(
                PlannerAction.CALL_TOOL, "get_current_context", reason_code="LOOP"
            )

        deterministic_plan_read = plan_read

    calls = []
    node = build_controlled_read_node(
        planner=RepeatingPlanner(),
        specs=read_tool_specs(),
        tools={
            "get_current_context": lambda _state, _arguments: (
                calls.append(1) or ok("get_current_context")
            )
        },
    )

    result = node(state())

    assert calls == [1]
    assert result.read_trace["status"] == "REJECTED"
    assert result.read_trace["finish_reason"] == "REPEATED_TOOL_CALL"


def test_repeated_model_call_uses_deterministic_finish_after_verified_observation():
    class RepeatingModelPlanner:
        def plan_read(self, **_context):
            return PlannerDecision(
                PlannerAction.CALL_TOOL,
                "search_community_knowledge",
                {"query": "物业电话"},
            )

        def deterministic_plan_read(self, *, observations, **_context):
            if observations:
                return PlannerDecision(PlannerAction.FINAL, reason_code="ANSWER_READY")
            return self.plan_read()

    calls = []
    node = build_controlled_read_node(
        planner=RepeatingModelPlanner(),
        specs=read_tool_specs(),
        tools={
            "search_community_knowledge": lambda _state, _arguments: (
                calls.append(1) or ok("search_community_knowledge", count=0, items=[])
            )
        },
    )

    result = node(state(intent="GENERAL_HELP", user_text="物业电话是多少"))

    assert calls == [1]
    assert result.read_trace["status"] == "DEGRADED"
    assert result.read_trace["finish_reason"] == "ANSWER_READY"
    assert any(
        event.get("reason") == "REPEATED_TOOL_CALL_BLOCKED" for event in result.read_trace["events"]
    )


def test_output_scope_mismatch_is_not_exposed_as_success():
    class OncePlanner:
        def plan_read(self, *, observations, **_context):
            if observations:
                return PlannerDecision(PlannerAction.FINAL, reason_code="DONE")
            return PlannerDecision(PlannerAction.CALL_TOOL, "list_bills")

        deterministic_plan_read = plan_read

    node = build_controlled_read_node(
        planner=OncePlanner(),
        specs=read_tool_specs(),
        tools={
            "list_bills": lambda _state, _arguments: ok(
                "list_bills", items=[{"community_id": str(uuid4()), "amount": "999"}]
            )
        },
    )

    result = node(state(intent="BILLING"))

    observation = result.read_facts["observations"][0]
    assert observation["ok"] is False
    assert observation["error_code"] == "TOOL_EXECUTION_FAILED"
    assert result.read_facts["records"] == []


def test_step_limit_stops_before_next_tool_execution():
    class UniquePlanner:
        def plan_read(self, *, observations, **_context):
            tool = "get_current_context" if not observations else "get_business_date"
            return PlannerDecision(PlannerAction.CALL_TOOL, tool)

        deterministic_plan_read = plan_read

    calls = []
    node = build_controlled_read_node(
        planner=UniquePlanner(),
        specs=read_tool_specs(),
        tools={
            "get_current_context": lambda _state, _arguments: (
                calls.append("get_current_context") or ok("get_current_context")
            ),
            "get_business_date": lambda _state, _arguments: (
                calls.append("get_business_date") or ok("get_business_date")
            ),
        },
        max_steps=1,
    )

    result = node(state())

    assert calls == ["get_current_context"]
    assert result.read_trace["status"] == "REJECTED"
    assert result.read_trace["finish_reason"] == "MAX_STEPS_EXCEEDED"


def test_failed_read_trace_is_exposed_without_failed_business_facts():
    class FailingPlanner:
        def plan_read(self, *, observations, **_context):
            if observations:
                return PlannerDecision(PlannerAction.FINAL, reason_code="SOURCE_FAILED")
            return PlannerDecision(PlannerAction.CALL_TOOL, "list_bills")

        deterministic_plan_read = plan_read

    node = build_controlled_read_node(
        planner=FailingPlanner(),
        specs=read_tool_specs(),
        tools={
            "list_bills": lambda _state, _arguments: {
                "ok": False,
                "tool": "list_bills",
                "error_code": "BILLING_SOURCE_UNAVAILABLE",
                "reason": "账单源暂时不可用",
            }
        },
    )
    result = node(state(intent="BILLING"))
    turn = AgentTurn(
        state=result,
        conversation=ConversationSnapshot(
            conversation_id=result.conversation_id,
            actor_id=result.actor_id,
            community_id=result.community_id,
            current_house_id=result.current_house_id,
            status="ACTIVE",
            last_intent=result.intent,
            handover_required=False,
            handover_ticket_id=None,
        ),
        interrupt=None,
        done=True,
    )

    response = turn_data(turn)

    assert response["facts"] is None
    assert response["agent_trace"]["finish_reason"] == "SOURCE_FAILED"
    events = response["agent_trace"]["events"]
    assert events[1]["tool"] == "list_bills"
    assert "arguments" not in events[1]


def test_public_tool_error_message_is_preserved_for_user_reply():
    class MissingOrderPlanner:
        def plan_read(self, *, observations, **_context):
            if observations:
                return PlannerDecision(PlannerAction.FINAL, reason_code="NOT_FOUND")
            return PlannerDecision(
                PlannerAction.CALL_TOOL,
                "get_work_order",
                {"work_order_id": "WX-20260812-MISSING"},
            )

        deterministic_plan_read = plan_read

    result = build_controlled_read_node(
        planner=MissingOrderPlanner(),
        specs=read_tool_specs(),
        tools={
            "get_work_order": lambda _state, _arguments: {
                "ok": False,
                "tool": "get_work_order",
                "error_code": "WORK_ORDER_NOT_FOUND",
                "reason": "没有找到该工单，请核对工单号。",
            }
        },
    )(state(intent="REPAIR", action="query"))
    result = explain_result_node()(result)

    assert result.messages[-1]["content"] == "没有找到该工单，请核对工单号。"
    assert result.read_facts["observations"][0]["error_message"] == (
        "没有找到该工单，请核对工单号。"
    )


def test_general_help_knowledge_query_uses_only_read_tool():
    calls = []

    def search(_state, arguments):
        calls.append(("search_community_knowledge", arguments))
        return ok(
            "search_community_knowledge",
            count=1,
            items=[{"title": "物业联系方式", "source_name": "物业联系方式"}],
        )

    result = build_controlled_read_node(
        planner=GatewayReadPlanner(object()),
        specs=read_tool_specs(),
        tools={
            "get_current_context": lambda _state, _arguments: ok("get_current_context"),
            "search_community_knowledge": search,
        },
    )(state(intent="GENERAL_HELP", user_text="物业电话是多少"))
    result = explain_result_node()(result)

    assert [name for name, _arguments in calls] == ["search_community_knowledge"]
    assert calls[0][1]["query"] == "物业电话是多少"
    assert "物业联系方式" in result.messages[-1]["content"]


def test_observation_payload_is_bounded_before_next_planner_step():
    class InspectingPlanner:
        seen = None

        def plan_read(self, *, observations, **_context):
            if observations:
                self.seen = observations
                return PlannerDecision(PlannerAction.FINAL, reason_code="DONE")
            return PlannerDecision(PlannerAction.CALL_TOOL, "search_announcements")

        deterministic_plan_read = plan_read

    planner = InspectingPlanner()
    node = build_controlled_read_node(
        planner=planner,
        specs=read_tool_specs(),
        tools={
            "search_announcements": lambda _state, _arguments: ok(
                "search_announcements",
                items=[{"body": "长" * 5000}],
                count=1,
            )
        },
    )

    result = node(state())

    assert len(planner.seen[0]["data"]["items"][0]["body"]) == 2000
    assert len(result.tool_result["data"]["items"][0]["body"]) == 2000


def test_run_duration_guard_stops_before_planner_or_tool_execution():
    class MustNotRunPlanner:
        def plan_read(self, **_context):  # pragma: no cover - must not execute
            raise AssertionError("planner must not run after deadline")

        deterministic_plan_read = plan_read

    result = build_controlled_read_node(
        planner=MustNotRunPlanner(),
        specs=read_tool_specs(),
        tools={},
        max_duration_seconds=0,
    )(state())

    assert result.read_trace["status"] == "REJECTED"
    assert result.read_trace["finish_reason"] == "RUN_TIMEOUT"
    assert result.read_trace["step_count"] == 0


def test_primary_and_fallback_planner_failure_is_returned_as_safe_trace():
    class BrokenPlanner:
        def plan_read(self, **_context):
            raise RuntimeError("provider secret detail")

        def deterministic_plan_read(self, **_context):
            raise RuntimeError("fallback internal detail")

    result = build_controlled_read_node(
        planner=BrokenPlanner(),
        specs=read_tool_specs(),
        tools={},
    )(state())

    assert result.read_trace["status"] == "REJECTED"
    assert result.read_trace["finish_reason"] == "PLANNER_UNAVAILABLE"
    assert "secret" not in str(result.read_trace)
    assert result.tool_result["ok"] is False
