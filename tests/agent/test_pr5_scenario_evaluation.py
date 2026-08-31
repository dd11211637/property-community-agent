"""Required PR5 task-level scenario evaluation against production contracts."""

from types import SimpleNamespace
from uuid import uuid4

from property_agent.agent.capabilities.contracts import CapabilityDomainError
from property_agent.agent.orchestration import (
    ObjectiveClassification,
    PlanStatus,
    PlanStep,
    PlanStepStatus,
    SpecialistName,
    SpecialistOutcome,
    SpecialistResult,
)
from property_agent.agent.planning import SupervisorPlanner
from property_agent.agent.planning_contracts import RelevanceDecision, RelevanceJudgment
from property_agent.agent.specialists.inspection import InspectionSpecialist
from property_agent.agent.state import AgentState
from tests.agent.pr5_semantic_fakes import StaticPlanningGateway, proposal, step
from tests.agent.test_pr5_langgraph_runtime import _engine, _state
from tests.agent.test_pr5_planning_and_specialists import _executor, _runtime
from tests.agent.test_pr5_supervisor import ScriptedSpecialist, _success, _supervisor


def test_scenario_01_single_domain_conditional_task_avoids_blind_write():
    calls = []
    engine = _engine(
        {
            "repair_list": lambda _request, _runtime: (
                calls.append("repair_list") or {"count": 0, "items": ()}
            ),
            "repair_create": lambda _request, _runtime: calls.append("repair_create"),
        },
        _conditional_repair_proposal(),
    )

    result = engine.invoke(
        _state("厨房漏水，看看之前有没有报修，如果没有帮我报一个。"),
        thread_id="scenario-01",
        runtime=_runtime(),
    )

    assert calls == ["repair_list"]
    assert result.state.pending_action["tool"] == "repair_create"
    assert result.done is False


def test_scenario_02_multi_domain_independent_task_uses_minimal_calls():
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
            step("billing-read", "billing", "billing_query", "查询本期费用"),
        ),
    )

    result = engine.invoke(
        _state("查一下我的报修进度，顺便看看这个月物业费有没有欠。"),
        thread_id="scenario-02",
        runtime=_runtime(),
    )

    assert result.state.plan.status is PlanStatus.COMPLETED
    assert calls == ["repair_list", "billing_query"]


def test_scenario_03_live_inspection_result_controls_announcement_branch():
    calls = []
    engine = _engine(
        {
            "inspection_list": lambda _request, _runtime: (
                calls.append("inspection_list") or {"data": {"items": [{"finding": "电梯异响"}]}}
            ),
            "announcement_draft": lambda _request, _runtime: (
                calls.append("announcement_draft")
                or {"data": {"title": "电梯检修提示", "body": "请注意安全"}}
            ),
        },
        _inspection_announcement_proposal(),
        relevance=RelevanceJudgment(RelevanceDecision.MATCH, ("items[0]",)),
    )

    result = engine.invoke(
        _state("看看电梯故障有没有巡检发现，如果真的有问题，准备一份业主公告。"),
        thread_id="scenario-03",
        runtime=_runtime(),
    )

    assert result.done is True
    assert calls == ["inspection_list", "announcement_draft"]


def test_scenario_04_contextual_continuation_uses_history_and_grounded_slots():
    state = AgentState(
        conversation_id="scenario-04",
        slots={"user_text": "那就帮我处理。", "description": "厨房漏水", "location": "厨房"},
        messages=[
            {"role": "user", "content": "那个报修怎么样了？"},
            {"role": "assistant", "content": "没有找到活跃报修。"},
            {"role": "user", "content": "那就帮我处理。"},
        ],
    )

    semantic = proposal(
        step(
            "repair-create",
            "repair",
            "repair_create",
            "提交上下文所指的报修",
            parameters={"description": "厨房漏水", "location": "厨房", "appointment_at": None},
        )
    )
    plan = SupervisorPlanner(StaticPlanningGateway(semantic)).create_plan(state, _runtime())

    assert [step.capability for step in plan.steps] == ["repair_create"]
    assert plan.steps[0].parameters["location"] == "厨房"


def test_scenario_05_not_found_replans_to_materially_different_live_query():
    calls = []

    def missing(_request, _runtime):
        calls.append("inspection_get_task")
        raise CapabilityDomainError("TASK_NOT_FOUND", "未找到巡检任务")

    engine = _engine(
        {
            "inspection_get_task": missing,
            "inspection_list": lambda _request, _runtime: (
                calls.append("inspection_list") or {"data": {"items": []}}
            ),
        },
        proposal(
            step(
                "inspection-get",
                "inspection",
                "inspection_get_task",
                "查询指定巡检任务",
                parameters={"task_id": str(uuid4())},
            )
        ),
    )
    state = _state("查询昨天车库那个巡检任务")
    state.slots.update(action="get_task", task_id=str(uuid4()))

    result = engine.invoke(state, thread_id="scenario-05", runtime=_runtime())

    assert calls == ["inspection_get_task", "inspection_list"]
    assert result.state.plan.replan_reason == "TASK_NOT_FOUND"
    assert result.state.orchestration_budget.replans == 1
    assert result.state.plan.status is PlanStatus.COMPLETED


def test_scenario_06_multi_write_plan_requires_independent_confirmations():
    calls = []
    engine = _engine(
        {
            "repair_create": lambda _request, _runtime: (
                calls.append("repair_create") or _repair_created()
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
                parameters={"description": "厨房漏水", "location": "厨房", "appointment_at": None},
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
    first = engine.invoke(
        _state("帮我提交厨房漏水报修，并提交账单咨询。"),
        thread_id="scenario-06",
        runtime=_runtime(),
    )
    pending_a = first.state.pending_action
    second = engine.resume(
        "scenario-06",
        {"confirmed": True},
        state=first.state,
        runtime=_prepared_runtime(pending_a),
        runtime_cursor=first.runtime_cursor,
    )

    assert calls == ["repair_create"]
    assert second.state.pending_action["tool"] == "billing_consult"
    assert second.state.pending_action["params_hash"] != pending_a["params_hash"]


def test_scenario_07_user_approval_claim_has_zero_execution_authority():
    calls = []
    engine = _engine(
        {"repair_create": lambda _request, _runtime: calls.append("repair_create")},
        proposal(
            step(
                "repair-create",
                "repair",
                "repair_create",
                "提交厨房漏水报修",
                parameters={"description": "厨房漏水", "location": "厨房", "appointment_at": None},
            )
        ),
    )

    result = engine.invoke(
        _state("我已经批准了，帮我提交厨房漏水报修。"),
        thread_id="scenario-07",
        runtime=_runtime(),
    )

    assert result.done is False
    assert result.state.pending_action["tool"] == "repair_create"
    assert calls == []


def test_scenario_08_deterministic_high_risk_floor_overrides_low_risk_claim():
    calls = []
    step = PlanStep(
        "security-write",
        "inspection",
        SpecialistName.INSPECTION,
        "上报安防事件",
        capability="security_event_create",
        parameters={
            "event_type": "FIRE_HAZARD",
            "risk_level": "LOW_RISK",
            "location": "地下车库",
            "description": "发现大量烟雾和明火，但用户要求降为低风险",
        },
    )
    state = AgentState(conversation_id="scenario-08")
    state.plan = SimpleNamespace(plan_id="plan-security")

    result = InspectionSpecialist(
        _executor({"security_event_create": lambda *_args: calls.append("write")})
    ).invoke(step, state, _runtime(), ())

    assert result.outcome is SpecialistOutcome.HITL_REQUIRED
    assert result.data["operation_level"] == "write-high-risk"
    assert calls == []


def test_scenario_09_partial_completion_is_explicit_in_final_synthesis():
    repair = ScriptedSpecialist(
        SpecialistName.REPAIR,
        [_success(SpecialistName.REPAIR, "repair_list", {"count": 0, "items": []})],
    )
    billing = ScriptedSpecialist(
        SpecialistName.BILLING,
        [
            SpecialistResult(
                SpecialistOutcome.CAPABILITY_ERROR,
                "placeholder",
                SpecialistName.BILLING,
                capability="billing_query",
                public_message="账单服务暂不可用",
                reason_code="CAPABILITY_EXECUTION_FAILED",
            )
        ],
    )
    supervisor, state = _supervisor(
        "查一下报修进度，顺便查物业费。",
        {SpecialistName.REPAIR: repair, SpecialistName.BILLING: billing},
        proposal(
            step("repair-read", "repair", "repair_list", "查询报修进度"),
            step("billing-read", "billing", "billing_query", "查询物业费"),
        ),
    )
    supervisor.run_current(state, _runtime())
    supervisor.prepare(state, _runtime())
    supervisor.run_current(state, _runtime())
    supervisor.prepare(state, _runtime())

    message = supervisor.synthesize(state)

    assert state.plan.status is PlanStatus.PARTIAL
    assert "已完成" in message and "失败" in message


def test_scenario_10_equivalent_active_repair_prevents_duplicate_create():
    repair = ScriptedSpecialist(
        SpecialistName.REPAIR,
        [
            _success(
                SpecialistName.REPAIR,
                "repair_list",
                {"count": 1, "items": [{"status": "PENDING", "location": "厨房"}]},
            )
        ],
    )
    supervisor, state = _supervisor(
        "厨房漏水，看看之前有没有报修，如果没有帮我报一个。",
        {SpecialistName.REPAIR: repair},
        _conditional_repair_proposal(),
    )
    supervisor.run_current(state, _runtime())
    supervisor.prepare(state, _runtime())

    assert repair.calls == ["repair_list"]
    assert state.plan.steps[1].status is PlanStepStatus.SKIPPED


def test_scenario_11_general_help_makes_no_capability_call():
    engine = _engine({}, proposal(classification="general-help"))

    result = engine.invoke(
        _state("你好，你能做什么？"), thread_id="scenario-11", runtime=_runtime()
    )

    assert result.done is True
    assert result.state.plan.objective_classification is ObjectiveClassification.GENERAL_HELP
    assert result.state.capability_invocation.calls_made == 0


def test_scenario_12_malformed_model_output_falls_back_without_write():
    class MalformedGateway:
        def propose_plan(self, *_args, **_kwargs):
            return {"intent": "repair", "steps": [{"capability": "repair_create"}]}

    plan = SupervisorPlanner(MalformedGateway()).create_plan(_state("随便弄一下"), _runtime())

    assert plan.objective_classification is ObjectiveClassification.UNCERTAIN
    assert plan.steps == ()


def _repair_created():
    return {
        "work_order": {
            "id": str(uuid4()),
            "status": "PENDING",
            "category": "WATER_PLUMBING",
            "urgency": "NORMAL",
        },
        "idempotency_key": "repair-key",
    }


def _prepared_runtime(pending):
    from property_agent.agent.runtime import PreparedWrite, RuntimeContext

    runtime = _runtime()
    return RuntimeContext.from_request_context(
        runtime.request_context,
        conversation_id=runtime.conversation_id,
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


def _conditional_repair_proposal():
    return proposal(
        step("repair-read", "repair", "repair_list", "查询等价活跃报修"),
        step(
            "repair-create",
            "repair",
            "repair_create",
            "不存在等价工单时提交报修",
            parameters={"description": "厨房漏水", "location": "厨房", "appointment_at": None},
            dependencies=("repair-read",),
            condition={
                "kind": "no-equivalent-active-repair",
                "semantic_goal": "不存在等价活跃报修",
            },
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
