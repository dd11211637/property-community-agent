from dataclasses import replace
from datetime import datetime, timezone

from property_agent.agent.model_gateway import DeterministicModelGateway
from property_agent.agent.orchestration import (
    GoalOutcome,
    PlanStatus,
    PlanStepStatus,
    SpecialistName,
    SpecialistOutcome,
    SpecialistResult,
)
from property_agent.agent.planning import SupervisorPlanner
from property_agent.agent.specialists.supervisor import Supervisor
from tests.agent.test_pr5_planning_and_specialists import _runtime, _state


class ScriptedSpecialist:
    def __init__(self, name, results):
        self.name = name
        self.results = list(results)
        self.calls = []

    def invoke(self, step, state, runtime, prior_results):
        del state, runtime, prior_results
        self.calls.append(step.capability)
        return replace(self.results.pop(0), step_id=step.step_id, specialist=self.name)


def _success(name, capability, data):
    return SpecialistResult(
        SpecialistOutcome.SUCCESS,
        "placeholder",
        name,
        capability=capability,
        data=data,
        public_message=f"{capability} completed",
    )


def _supervisor(text, scripts):
    state = _state(text)
    supervisor = Supervisor(
        SupervisorPlanner(DeterministicModelGateway()),
        scripts,
        clock=lambda: datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
    )
    supervisor.prepare(state, _runtime())
    return supervisor, state


def test_existing_equivalent_repair_skips_duplicate_create():
    repair = ScriptedSpecialist(
        SpecialistName.REPAIR,
        [
            _success(
                SpecialistName.REPAIR,
                "repair_list",
                {
                    "count": 1,
                    "items": [
                        {
                            "status": "PENDING",
                            "location": "厨房",
                            "business_no": "WX-1",
                        }
                    ],
                },
            )
        ],
    )
    supervisor, state = _supervisor(
        "厨房漏水，看看之前有没有报修，如果没有帮我报一个。",
        {SpecialistName.REPAIR: repair},
    )

    supervisor.run_current(state, _runtime())
    supervisor.prepare(state, _runtime())

    assert repair.calls == ["repair_list"]
    assert state.plan.steps[1].status is PlanStepStatus.SKIPPED
    assert state.goal_outcomes[state.plan.steps[1].step_id] is GoalOutcome.COMPLETED
    assert state.plan.status is PlanStatus.COMPLETED


def test_empty_inspection_result_prevents_unnecessary_announcement_call():
    inspection = ScriptedSpecialist(
        SpecialistName.INSPECTION,
        [_success(SpecialistName.INSPECTION, "inspection_list", {"data": {"items": []}})],
    )
    announcement = ScriptedSpecialist(
        SpecialistName.ANNOUNCEMENT,
        [_success(SpecialistName.ANNOUNCEMENT, "announcement_draft", {"data": {}})],
    )
    supervisor, state = _supervisor(
        "看看电梯故障有没有巡检发现，如果真的有问题，准备一份业主公告。",
        {
            SpecialistName.INSPECTION: inspection,
            SpecialistName.ANNOUNCEMENT: announcement,
        },
    )

    supervisor.run_current(state, _runtime())
    supervisor.prepare(state, _runtime())

    assert inspection.calls == ["inspection_list"]
    assert announcement.calls == []
    assert state.plan.steps[1].status is PlanStepStatus.SKIPPED


def test_typed_not_found_replan_materially_changes_capability_and_consumes_budget():
    replan = SpecialistResult(
        SpecialistOutcome.REPLAN,
        "placeholder",
        SpecialistName.INSPECTION,
        capability="inspection_get_task",
        data={
            "replacement_capability": "inspection_list",
            "replacement_parameters": {"target": "task", "limit": 20},
        },
        reason_code="TASK_NOT_FOUND",
    )
    inspection = ScriptedSpecialist(SpecialistName.INSPECTION, [replan])
    supervisor, state = _supervisor(
        "查询巡检任务",
        {SpecialistName.INSPECTION: inspection},
    )
    original = state.plan.steps[0]
    state.plan = replace(
        state.plan,
        steps=(replace(original, capability="inspection_get_task"),),
    )

    supervisor.run_current(state, _runtime())

    assert state.plan.steps[0].capability == "inspection_list"
    assert state.plan.steps[0].status is PlanStepStatus.PENDING
    assert state.plan.replan_reason == "TASK_NOT_FOUND"
    assert state.orchestration_budget.replans == 1


def test_partial_completion_synthesis_never_claims_everything_completed():
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
    )

    supervisor.run_current(state, _runtime())
    supervisor.prepare(state, _runtime())
    supervisor.run_current(state, _runtime())
    supervisor.prepare(state, _runtime())
    message = supervisor.synthesize(state)

    assert state.plan.status is PlanStatus.PARTIAL
    assert "已完成" in message
    assert "失败" in message
    assert "全部完成" not in message
