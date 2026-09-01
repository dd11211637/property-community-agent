from uuid import uuid4

from langgraph.graph.state import CompiledStateGraph

from property_agent.agent.application.langgraph_runtime import (
    LangGraphEngine,
    build_saver_resource,
)
from property_agent.agent.capabilities.catalog import default_capability_registry
from property_agent.agent.capabilities.executor import CapabilityExecutor
from property_agent.agent.capabilities.policy import default_capability_policy
from property_agent.agent.orchestration import PlanStatus, SpecialistName
from property_agent.agent.planning import SupervisorPlanner
from property_agent.agent.planning_contracts import RelevanceDecision, RelevanceJudgment
from property_agent.agent.react_contracts import GoalStatus, ReactDecision, ReactDecisionType
from property_agent.agent.runtime import ExecutionPolicy, PreparedWrite, RuntimeContext
from property_agent.agent.specialists import (
    AnnouncementSpecialist,
    BillingSpecialist,
    InspectionSpecialist,
    RepairSpecialist,
)
from property_agent.agent.specialists.supervisor import Supervisor
from property_agent.agent.state import AgentState
from tests.agent.pr5_semantic_fakes import StaticPlanningGateway, proposal, step
from tests.agent.test_pr5_planning_and_specialists import _runtime


def _engine(adapters, semantic, *, relevance=None, react_gateway=None):
    executor = CapabilityExecutor(
        default_capability_registry(), default_capability_policy(), adapters
    )
    specialists = (
        RepairSpecialist(executor),
        BillingSpecialist(executor),
        AnnouncementSpecialist(executor),
        InspectionSpecialist(executor),
    )
    supervisor = Supervisor(
        SupervisorPlanner(
            StaticPlanningGateway(
                semantic,
                relevance or RelevanceJudgment(RelevanceDecision.NO_MATCH),
            )
        ),
        {specialist.name: specialist for specialist in specialists},
        react_gateway=react_gateway,
    )
    return LangGraphEngine(build_saver_resource(in_memory=True).saver, supervisor)


class _ReactPlanningGateway(StaticPlanningGateway):
    def __init__(self, semantic, decisions):
        super().__init__(semantic)
        self.decisions = list(decisions)

    def react_decide(self, context):
        assert context["domain"] == "repair"
        return self.decisions.pop(0)


def test_official_graph_runs_react_reason_action_reason_loop():
    calls = []
    semantic = proposal(step("repair-goal", "repair", "repair_list", "查询报修"))
    gateway = _ReactPlanningGateway(
        semantic,
        [
            ReactDecision(
                ReactDecisionType.ACT,
                GoalStatus.IN_PROGRESS,
                capability="repair_list",
                arguments={},
                reason_code="LOOKUP",
            ),
            ReactDecision(
                ReactDecisionType.FINISH,
                GoalStatus.COMPLETED,
                reason_code="OBSERVED",
            ),
        ],
    )
    engine = _engine(
        {
            "repair_list": lambda _request, _runtime: (
                calls.append("repair_list")
                or {"count": 0, "items": (), "query_location": None, "query_category": None}
            )
        },
        semantic,
        react_gateway=gateway,
    )
    runtime = _runtime()
    runtime = RuntimeContext.from_request_context(
        runtime.request_context,
        conversation_id=runtime.conversation_id,
        execution_policy=ExecutionPolicy(react_domains=frozenset({"repair"})),
    )
    result = engine.invoke(_state("查询报修"), thread_id="react-conversation", runtime=runtime)
    assert result.done
    assert result.state.plan.status is PlanStatus.COMPLETED
    assert calls == ["repair_list"]


def test_react_hitl_resume_executes_bound_write_exactly_once():
    calls = []
    create_arguments = {
        "description": "厨房漏水",
        "location": "厨房",
        "urgency": "NORMAL",
        "appointment_at": None,
    }
    semantic = proposal(step("repair-goal", "repair", "repair_create", "先查询再报修"))
    gateway = _ReactPlanningGateway(
        semantic,
        [
            ReactDecision(
                ReactDecisionType.ACT,
                GoalStatus.IN_PROGRESS,
                capability="repair_list",
                arguments={"location": "厨房"},
            ),
            ReactDecision(
                ReactDecisionType.ACT,
                GoalStatus.IN_PROGRESS,
                capability="repair_create",
                arguments=create_arguments,
            ),
            ReactDecision(ReactDecisionType.FINISH, GoalStatus.COMPLETED),
        ],
    )
    engine = _engine(
        {
            "repair_list": lambda _request, _runtime: {
                "count": 0,
                "items": (),
                "query_location": "厨房",
                "query_category": None,
            },
            "repair_create": lambda _request, _runtime: (
                calls.append("repair_create")
                or {
                    "work_order": {
                        "id": str(uuid4()),
                        "status": "PENDING",
                        "category": "WATER_PLUMBING",
                        "urgency": "NORMAL",
                    },
                    "idempotency_key": "server-key",
                }
            ),
        },
        semantic,
        react_gateway=gateway,
    )
    runtime = _runtime()
    runtime = RuntimeContext.from_request_context(
        runtime.request_context,
        conversation_id=runtime.conversation_id,
        execution_policy=ExecutionPolicy(react_domains=frozenset({"repair"})),
    )
    first = engine.invoke(_state("先查询再报修"), thread_id="react-hitl", runtime=runtime)
    assert not first.done
    assert first.state.pending_action["tool"] == "repair_create"
    assert calls == []
    second = engine.resume(
        "react-hitl",
        {"confirmed": True},
        state=first.state,
        runtime=_bound_runtime(runtime, first.state.pending_action),
        runtime_cursor=first.runtime_cursor,
    )
    assert second.done
    assert calls == ["repair_create"]
    assert second.state.plan.status is PlanStatus.COMPLETED


def _state(text):
    return AgentState(
        conversation_id="conversation-1",
        slots={"user_text": text},
        messages=[{"role": "user", "content": text}],
    )


def test_official_supervisor_graph_executes_minimal_multi_domain_plan():
    calls = []
    engine = _engine(
        {
            "repair_list": lambda _request, _runtime: (
                calls.append("repair_list") or {"count": 0, "items": ()}
            ),
            "billing_query": lambda _request, _runtime: (
                calls.append("billing_query") or {"query_type": "list", "count": 0, "items": ()}
            ),
        },
        proposal(
            step("repair-read", "repair", "repair_list", "查询报修进度"),
            step("billing-read", "billing", "billing_query", "查询物业费"),
        ),
    )

    result = engine.invoke(
        _state("查一下我的报修进度，顺便看看物业费。"),
        thread_id="conversation-1",
        runtime=_runtime(),
    )

    assert isinstance(engine._graph, CompiledStateGraph)
    assert result.done is True
    assert result.state.plan.status is PlanStatus.COMPLETED
    assert calls == ["repair_list", "billing_query"]
    assert [item.specialist for item in result.state.specialist_results] == [
        SpecialistName.REPAIR,
        SpecialistName.BILLING,
    ]
    assert result.runtime_cursor["checkpoint_id"]


def test_live_inspection_result_controls_announcement_execution():
    calls = []
    engine = _engine(
        {
            "inspection_list": lambda _request, _runtime: (
                calls.append("inspection_list") or {"data": {"items": []}}
            ),
            "announcement_draft": lambda _request, _runtime: (
                calls.append("announcement_draft") or {"data": {"title": "test", "body": "test"}}
            ),
        },
        _inspection_announcement_proposal(),
    )

    result = engine.invoke(
        _state("看看电梯故障有没有巡检发现，如果真的有问题，准备一份业主公告。"),
        thread_id="conversation-1",
        runtime=_runtime(),
    )

    assert result.done is True
    assert calls == ["inspection_list"]


def test_two_writes_receive_two_exact_interrupts_and_a_cannot_authorize_b():
    calls = []
    engine = _engine(
        {
            "repair_create": lambda _request, _runtime: (
                calls.append("repair_create")
                or {
                    "work_order": {
                        "id": str(uuid4()),
                        "status": "PENDING",
                        "category": "WATER_PLUMBING",
                        "urgency": "NORMAL",
                    },
                    "idempotency_key": "repair-key",
                }
            ),
            "billing_consult": lambda request, _runtime: (
                calls.append("billing_consult")
                or {
                    "consultation": {
                        "id": str(uuid4()),
                        "subject": request.subject,
                        "status": "PENDING",
                        "bill_id": request.bill_id,
                    },
                    "idempotency_key": "billing-key",
                }
            ),
        },
        proposal(
            step(
                "repair-create",
                "repair",
                "repair_create",
                "提交厨房漏水报修",
                parameters={
                    "description": "厨房漏水",
                    "location": "厨房",
                    "urgency": "NORMAL",
                    "appointment_at": None,
                },
            ),
            step(
                "billing-consult",
                "billing",
                "billing_consult",
                "提交账单咨询",
                parameters={"subject": "账单咨询", "description": "咨询账单"},
            ),
        ),
    )
    runtime = _runtime()
    first = engine.invoke(
        _state("帮我提交厨房漏水报修，并提交账单咨询。"),
        thread_id="conversation-1",
        runtime=runtime,
    )
    pending_a = first.state.pending_action
    assert first.done is False
    assert pending_a["tool"] == "repair_create"

    runtime_a = _bound_runtime(runtime, pending_a)
    second = engine.resume(
        "conversation-1",
        {"confirmed": True},
        state=first.state,
        runtime=runtime_a,
        runtime_cursor=first.runtime_cursor,
    )
    pending_b = second.state.pending_action

    assert calls == ["repair_create"]
    assert second.done is False
    assert pending_b["tool"] == "billing_consult"
    assert pending_b["params_hash"] != pending_a["params_hash"]
    assert pending_b["plan_step_id"] != pending_a["plan_step_id"]

    third = engine.resume(
        "conversation-1",
        {"confirmed": True},
        state=second.state,
        runtime=_bound_runtime(runtime, pending_b),
        runtime_cursor=second.runtime_cursor,
    )
    assert third.done is True
    assert calls == ["repair_create", "billing_consult"]


def _bound_runtime(runtime: RuntimeContext, pending):
    return RuntimeContext.from_request_context(
        runtime.request_context,
        conversation_id=runtime.conversation_id,
        current_house_id=runtime.current_house_id,
        prepared_write=PreparedWrite(
            "server-token",
            "server-key",
            "server-approval",
            capability=pending["tool"],
            params_hash=pending["params_hash"],
            plan_id=pending["plan_id"],
            plan_step_id=pending["plan_step_id"],
        ),
    )


def _inspection_announcement_proposal():
    return proposal(
        step("inspection-read", "inspection", "inspection_list", "核验电梯巡检发现"),
        step(
            "announcement-draft",
            "announcement",
            "announcement_draft",
            "相关问题成立时准备公告",
            parameters={"topic": "电梯检修提示", "audience": {}, "requirements": "提示安全"},
            dependencies=("inspection-read",),
            condition={
                "kind": "relevant-inspection-issue",
                "semantic_goal": "巡检结果确认存在相关电梯问题",
            },
        ),
    )
