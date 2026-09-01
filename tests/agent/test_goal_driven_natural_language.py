from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from property_agent.agent.application.domain_continuation import prepare_start_state
from property_agent.agent.application.langgraph_runtime import LangGraphEngine, build_saver_resource
from property_agent.agent.capabilities.catalog import default_capability_registry
from property_agent.agent.capabilities.executor import CapabilityExecutor
from property_agent.agent.capabilities.policy import default_capability_policy
from property_agent.agent.goal_contracts import GoalResolution, GoalResolutionType
from property_agent.agent.planning import SupervisorPlanner
from property_agent.agent.react_contracts import GoalStatus, ReactDecision, ReactDecisionType
from property_agent.agent.runtime import ExecutionPolicy, PreparedWrite, RuntimeContext
from property_agent.agent.specialists import (
    AnnouncementSpecialist,
    BillingSpecialist,
    InspectionSpecialist,
    RepairSpecialist,
)
from property_agent.agent.specialists.supervisor import Supervisor
from property_agent.platform.adapters.api.dependencies import RequestContext
from property_agent.platform.context import ExecutionSource


class SemanticScenarioGateway:
    def __init__(self, resolutions, decide):
        self.resolutions = resolutions
        self.decide = decide
        self.goal_contexts = []
        self.react_contexts = []
        self.plan_calls = 0

    def resolve_goal(self, context):
        self.goal_contexts.append(context)
        return self.resolutions[context["user_text"]]

    def react_decide(self, context):
        self.react_contexts.append(context)
        return self.decide(context)

    def propose_plan(self, *_args, **_kwargs):
        self.plan_calls += 1
        raise AssertionError("normal Goal-driven path must not call the Planner")


def _resolution(kind, domain=None, goal=None, facts=None, question=None):
    return GoalResolution(
        kind,
        domain=domain,
        goal=goal,
        candidate_facts=facts or {},
        authorized_domains=(domain,) if domain else (),
        question=question,
    )


def _engine(gateway, adapters):
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
        SupervisorPlanner(gateway),
        {item.name: item for item in specialists},
        react_gateway=gateway,
    )
    return LangGraphEngine(build_saver_resource(in_memory=True).saver, supervisor)


def _context(*, security=False):
    actor_id, community_id = uuid4(), uuid4()
    roles = frozenset({"SECURITY_GUARD" if security else "RESIDENT"})
    request = RequestContext(
        actor_id=actor_id,
        community_id=community_id,
        roles=roles,
        bound_house_ids=frozenset(),
        current_house_id=None,
        request_id="natural-language-e2e",
        execution_source=ExecutionSource.AGENT,
    )
    runtime = RuntimeContext.from_request_context(
        request,
        conversation_id="natural-language-conversation",
        execution_policy=ExecutionPolicy(
            react_domains=frozenset({"repair", "billing", "announcement", "inspection"})
        ),
    )
    agent_context = SimpleNamespace(
        actor_id=actor_id,
        community_id=community_id,
        house_ids=(),
        roles=tuple(roles),
    )
    return agent_context, runtime


def _turn(engine, gateway, text, *, previous=None, thread="natural", security=False):
    context, runtime = _context(security=security)
    if previous is not None:
        context = SimpleNamespace(
            actor_id=previous.actor_id,
            community_id=previous.community_id,
            house_ids=(),
            roles=tuple(runtime.roles),
        )
        runtime = RuntimeContext.from_request_context(
            runtime.request_context.__class__(
                actor_id=previous.actor_id,
                community_id=previous.community_id,
                roles=runtime.roles,
                bound_house_ids=frozenset(),
                current_house_id=None,
                request_id="natural-language-followup",
                execution_source=ExecutionSource.AGENT,
            ),
            conversation_id="natural-language-conversation",
            execution_policy=runtime.execution_policy,
        )
    prepared = prepare_start_state(
        conversation_id="natural-language-conversation",
        context=context,
        current_house_id=None,
        previous=previous,
        user_text=text,
        slots=None,
    )
    return engine.invoke(prepared.state, thread_id=thread, runtime=runtime)


def _clarify(*missing, question):
    return ReactDecision(
        ReactDecisionType.CLARIFY,
        GoalStatus.NEEDS_CLARIFICATION,
        missing_information=missing,
        question=question,
    )


def test_case_1_known_repair_goal_clarifies_business_facts_without_plan():
    gateway = SemanticScenarioGateway(
        {"我要报修": _resolution(GoalResolutionType.NEW, "repair", "发起报修")},
        lambda _context: _clarify(
            "description", "location", question="请告诉我故障内容和具体地点。"
        ),
    )
    result = _turn(_engine(gateway, {}), gateway, "我要报修")

    assert result.state.plan is None
    assert result.state.active_goal.domain == "repair"
    assert result.state.active_goal.status is GoalStatus.NEEDS_CLARIFICATION
    assert result.state.messages[-1]["content"] == "请告诉我故障内容和具体地点。"
    assert gateway.plan_calls == 0


def test_case_2_repair_observation_changes_list_to_get_and_prevents_create():
    work_order_id = str(uuid4())
    calls = []

    def decide(context):
        observations = context["observations"]
        if not observations:
            return ReactDecision(
                ReactDecisionType.ACT,
                GoalStatus.IN_PROGRESS,
                capability="repair_list",
                arguments={"location": "3栋电梯"},
            )
        if observations[-1]["capability"] == "repair_list":
            return ReactDecision(
                ReactDecisionType.ACT,
                GoalStatus.IN_PROGRESS,
                capability="repair_get",
                arguments={"work_order_id": work_order_id},
            )
        return ReactDecision(ReactDecisionType.FINISH, GoalStatus.COMPLETED)

    gateway = SemanticScenarioGateway(
        {
            "3栋电梯一直报警，帮我处理": _resolution(
                GoalResolutionType.NEW,
                "repair",
                "处理3栋电梯持续报警",
                {"location": "3栋电梯", "description": "电梯一直报警"},
            )
        },
        decide,
    )
    brief = {
        "id": work_order_id,
        "business_no": "WX-EXISTING",
        "status": "PROCESSING",
        "category": "ELEVATOR",
        "location": "3栋电梯",
        "urgency": "HIGH",
        "appointment_status": "NOT_SCHEDULED",
    }
    engine = _engine(
        gateway,
        {
            "repair_list": lambda _request, _runtime: (
                calls.append("repair_list")
                or {
                    "count": 1,
                    "items": (brief,),
                    "query_location": "3栋电梯",
                    "query_category": None,
                }
            ),
            "repair_get": lambda _request, _runtime: (
                calls.append("repair_get") or {"work_order": brief, "timeline": ()}
            ),
        },
    )
    result = _turn(engine, gateway, "3栋电梯一直报警，帮我处理")

    assert result.state.plan is None
    assert calls == ["repair_list", "repair_get"]
    assert result.state.active_goal.status is GoalStatus.COMPLETED
    assert all(
        item["capability"] != "repair_create" for item in gateway.react_contexts[-1]["observations"]
    )


def test_case_3_security_report_enters_inspection_without_internal_enums():
    gateway = SemanticScenarioGateway(
        {
            "安全出口有人堆放杂物": _resolution(
                GoalResolutionType.NEW,
                "inspection",
                "上报公共区域安全隐患",
                {"location": "安全出口", "description": "有人堆放杂物"},
            )
        },
        lambda context: ReactDecision(
            ReactDecisionType.ACT,
            GoalStatus.IN_PROGRESS,
            capability="security_event_create",
            arguments=context["candidate_facts"],
        ),
    )
    result = _turn(_engine(gateway, {}), gateway, "安全出口有人堆放杂物", security=True)

    assert not result.done
    assert result.state.plan is None
    assert result.state.pending_action["goal_id"] == result.state.active_goal.goal_id
    assert "plan_id" not in result.state.pending_action
    assert "event_type" not in result.state.pending_action["params"]
    assert "risk_level" not in result.state.pending_action["params"]
    inventory = {item["name"]: item for item in gateway.react_contexts[0]["capability_inventory"]}
    assert inventory["security_event_create"]["purpose"] == "上报安防事件"
    assert inventory["security_event_create"]["required_inputs"] == [
        "description",
        "location",
    ]


def test_case_4_billing_followup_reuses_goal_and_observation():
    calls = []

    def decide(context):
        if context["observations"]:
            return ReactDecision(ReactDecisionType.FINISH, GoalStatus.COMPLETED)
        return ReactDecision(
            ReactDecisionType.ACT,
            GoalStatus.IN_PROGRESS,
            capability="billing_query",
            arguments={"query_type": "list", "period": "2026-09"},
        )

    gateway = SemanticScenarioGateway(
        {
            "查询一下本月的账单": _resolution(
                GoalResolutionType.NEW,
                "billing",
                "查询本月账单",
                {"period": "2026-09"},
            ),
            "本月账单包含哪几种费用": _resolution(
                GoalResolutionType.CONTINUE,
                "billing",
                "了解本月账单费用构成",
            ),
        },
        decide,
    )
    bill = {
        "bill_id": "bill-1",
        "period": "2026-09",
        "total_amount": "130.00",
        "property_fee": "100.00",
        "utility_fee": "30.00",
        "parking_fee": "0.00",
        "late_fee": "0.00",
        "status": "UNPAID",
    }
    engine = _engine(
        gateway,
        {
            "billing_query": lambda _request, _runtime: (
                calls.append("billing_query")
                or {"query_type": "list", "period": "2026-09", "count": 1, "items": (bill,)}
            )
        },
    )
    first = _turn(engine, gateway, "查询一下本月的账单", thread="billing-followup")
    goal_id = first.state.active_goal.goal_id
    second = _turn(
        engine,
        gateway,
        "本月账单包含哪几种费用",
        previous=first.state,
        thread="billing-followup",
    )

    assert second.state.active_goal.goal_id == goal_id
    assert second.state.active_goal.domain == "billing"
    assert calls == ["billing_query"]
    assert gateway.goal_contexts[-1]["active_goal"]["domain"] == "billing"
    assert gateway.goal_contexts[-1]["business_date"] == str(date.today())
    assert gateway.react_contexts[-1]["business_date"] == str(date.today())


def test_case_5_announcement_goal_clarifies_then_drafts_from_observation():
    calls = []

    def decide(context):
        facts = context["candidate_facts"]
        if "audience" not in facts or not facts.get("requirements"):
            return _clarify("audience", "requirements", question="请补充通知范围和停水时间安排。")
        if not context["observations"]:
            return ReactDecision(
                ReactDecisionType.ACT,
                GoalStatus.IN_PROGRESS,
                capability="announcement_draft",
                arguments=facts,
            )
        return ReactDecision(ReactDecisionType.FINISH, GoalStatus.COMPLETED)

    gateway = SemanticScenarioGateway(
        {
            "我要发个停水通知": _resolution(
                GoalResolutionType.NEW,
                "announcement",
                "起草停水通知",
                {"topic": "停水通知"},
            ),
            "通知全体住户，明天上午9点到11点停水": _resolution(
                GoalResolutionType.CONTINUE,
                "announcement",
                "起草停水通知",
                {
                    "topic": "停水通知",
                    "audience": {},
                    "requirements": "明天上午9点到11点停水",
                },
            ),
        },
        decide,
    )
    engine = _engine(
        gateway,
        {
            "announcement_draft": lambda _request, _runtime: (
                calls.append("announcement_draft")
                or {
                    "data": {
                        "draft": {
                            "title": "停水通知",
                            "body": "明天上午9点到11点停水。",
                            "category": "MAINTENANCE",
                            "audience": {},
                        }
                    }
                }
            )
        },
    )
    first = _turn(engine, gateway, "我要发个停水通知", thread="announcement")

    assert first.state.plan is None
    assert first.state.active_goal.domain == "announcement"
    assert first.state.active_goal.status is GoalStatus.NEEDS_CLARIFICATION
    second = _turn(
        engine,
        gateway,
        "通知全体住户，明天上午9点到11点停水",
        previous=first.state,
        thread="announcement",
    )
    assert second.state.active_goal.status is GoalStatus.COMPLETED
    assert calls == ["announcement_draft"]


def test_case_6_other_event_continues_active_security_goal():
    resolutions = {
        "我要上报一个安防问题": _resolution(GoalResolutionType.NEW, "inspection", "上报安防事件"),
        "其他事件": _resolution(
            GoalResolutionType.CONTINUE,
            "inspection",
            "继续上报安防事件",
            {"description": "其他事件"},
        ),
    }
    gateway = SemanticScenarioGateway(
        resolutions,
        lambda _context: _clarify("location", question="请补充事件发生地点。"),
    )
    engine = _engine(gateway, {})
    first = _turn(engine, gateway, "我要上报一个安防问题", thread="security-followup")
    goal_id = first.state.active_goal.goal_id
    second = _turn(
        engine,
        gateway,
        "其他事件",
        previous=first.state,
        thread="security-followup",
        security=True,
    )

    assert second.state.active_goal.goal_id == goal_id
    assert second.state.active_goal.candidate_facts["description"] == "其他事件"
    assert all(context.get("domain") == "inspection" for context in gateway.react_contexts)


def test_case_7_explicit_switch_replaces_repair_goal_with_inspection_query():
    gateway = SemanticScenarioGateway(
        {
            "我要报修": _resolution(GoalResolutionType.NEW, "repair", "发起报修"),
            "先不报了，查一下今天的巡检任务": _resolution(
                GoalResolutionType.SWITCH,
                "inspection",
                "查询今天的巡检任务",
                {"target": "task"},
            ),
        },
        lambda context: (
            _clarify("description", "location", question="请补充故障内容和地点。")
            if context["domain"] == "repair"
            else (
                ReactDecision(ReactDecisionType.FINISH, GoalStatus.COMPLETED)
                if context["observations"]
                else ReactDecision(
                    ReactDecisionType.ACT,
                    GoalStatus.IN_PROGRESS,
                    capability="inspection_list",
                    arguments={"target": "task"},
                )
            )
        ),
    )
    engine = _engine(
        gateway,
        {
            "inspection_list": lambda _request, _runtime: {
                "data": {
                    "target": "task",
                    "count": 0,
                    "items": (),
                    "total": 0,
                    "completed": 0,
                    "incomplete": 0,
                }
            }
        },
    )
    first = _turn(engine, gateway, "我要报修", thread="task-switch")
    repair_goal = first.state.active_goal.goal_id
    second = _turn(
        engine,
        gateway,
        "先不报了，查一下今天的巡检任务",
        previous=first.state,
        thread="task-switch",
        security=True,
    )

    assert second.state.active_goal.goal_id != repair_goal
    assert second.state.active_goal.domain == "inspection"
    assert second.state.active_goal.status is GoalStatus.COMPLETED


def test_case_8_one_followup_supplies_multiple_repair_facts_and_reaches_hitl():
    def decide(context):
        facts = context["candidate_facts"]
        if not facts.get("description") or not facts.get("location"):
            return _clarify("description", "location", question="请补充故障内容和地点。")
        if not context["observations"]:
            return ReactDecision(
                ReactDecisionType.ACT,
                GoalStatus.IN_PROGRESS,
                capability="repair_list",
                arguments={"location": facts["location"]},
            )
        return ReactDecision(
            ReactDecisionType.ACT,
            GoalStatus.IN_PROGRESS,
            capability="repair_create",
            arguments={
                "description": facts["description"],
                "location": facts["location"],
                "urgency": "NORMAL",
                "appointment_at": facts["appointment_at"],
            },
        )

    gateway = SemanticScenarioGateway(
        {
            "我要报修": _resolution(GoalResolutionType.NEW, "repair", "发起报修"),
            "3栋电梯一直报警，地点就在一楼，时间稍后协商": _resolution(
                GoalResolutionType.CONTINUE,
                "repair",
                "发起3栋电梯报警报修",
                {
                    "description": "3栋电梯一直报警",
                    "location": "3栋一楼电梯",
                    "appointment_at": None,
                },
            ),
        },
        decide,
    )
    engine = _engine(
        gateway,
        {
            "repair_list": lambda _request, _runtime: {
                "count": 0,
                "items": (),
                "query_location": "3栋一楼电梯",
                "query_category": None,
            }
        },
    )
    first = _turn(engine, gateway, "我要报修", thread="multi-fact")
    second = _turn(
        engine,
        gateway,
        "3栋电梯一直报警，地点就在一楼，时间稍后协商",
        previous=first.state,
        thread="multi-fact",
    )

    assert not second.done
    assert second.state.plan is None
    assert second.state.pending_action["tool"] == "repair_create"
    assert second.state.pending_action["params"]["description"] == "3栋电梯一直报警"
    assert second.state.pending_action["params"]["location"] == "3栋一楼电梯"


def test_goal_driven_hitl_resume_executes_once_without_plan_identifiers():
    calls = []

    def decide(context):
        observations = context["observations"]
        if not observations:
            return ReactDecision(
                ReactDecisionType.ACT,
                GoalStatus.IN_PROGRESS,
                capability="repair_list",
                arguments={"location": "厨房"},
            )
        if observations[-1]["capability"] == "repair_list":
            return ReactDecision(
                ReactDecisionType.ACT,
                GoalStatus.IN_PROGRESS,
                capability="repair_create",
                arguments={
                    "description": "厨房漏水",
                    "location": "厨房",
                    "urgency": "NORMAL",
                    "appointment_at": None,
                },
            )
        return ReactDecision(ReactDecisionType.FINISH, GoalStatus.COMPLETED)

    gateway = SemanticScenarioGateway(
        {
            "厨房漏水，稍后协商上门时间，帮我报修": _resolution(
                GoalResolutionType.NEW,
                "repair",
                "发起厨房漏水报修",
                {
                    "description": "厨房漏水",
                    "location": "厨房",
                    "appointment_at": None,
                },
            )
        },
        decide,
    )
    work_order_id = str(uuid4())
    engine = _engine(
        gateway,
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
                        "id": work_order_id,
                        "business_no": "WX-NEW",
                        "status": "PENDING",
                        "category": "WATER_PLUMBING",
                        "location": "厨房",
                        "urgency": "NORMAL",
                        "appointment_status": "NOT_SCHEDULED",
                    },
                    "idempotency_key": "server-owned-key",
                }
            ),
        },
    )
    agent_context, runtime = _context()
    prepared = prepare_start_state(
        conversation_id="natural-language-conversation",
        context=agent_context,
        current_house_id=None,
        previous=None,
        user_text="厨房漏水，稍后协商上门时间，帮我报修",
        slots=None,
    )
    first = engine.invoke(prepared.state, thread_id="direct-hitl", runtime=runtime)
    pending = first.state.pending_action
    assert "plan_id" not in pending and "plan_step_id" not in pending
    resumed_runtime = RuntimeContext.from_request_context(
        runtime.request_context,
        conversation_id=runtime.conversation_id,
        execution_policy=runtime.execution_policy,
        prepared_write=PreparedWrite(
            "server-token",
            "server-key",
            capability=pending["tool"],
            params_hash=pending["params_hash"],
            goal_id=pending["goal_id"],
        ),
    )
    second = engine.resume(
        "direct-hitl",
        {"confirmed": True},
        state=first.state,
        runtime=resumed_runtime,
        runtime_cursor=first.runtime_cursor,
    )

    assert second.done and second.state.plan is None
    assert calls == ["repair_create"]
    assert second.state.active_goal.status is GoalStatus.COMPLETED
    assert "idempotency_key" not in second.state.active_goal.observations[-1].data
