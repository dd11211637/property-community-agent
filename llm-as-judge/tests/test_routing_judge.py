"""路由决策与 LLM Judge 行为测试。"""

import pytest

from judge.llmjudge import JudgeUnavailable, LLMJudge
from judge.routing import RoutingError, route
from judge.schemas import AgentRun, Check, EvaluationCase, LLMSpec, RuleSpec


def _check(**kwargs) -> Check:
    kwargs.setdefault("metric", "answer_quality")
    return Check(id=kwargs.pop("id", "c1"), **kwargs)


def test_auto_prefers_rule_when_available() -> None:
    check = _check(evaluator="auto", rule=RuleSpec(kind="exact_match", params={"expected": "x"}))
    assert route(check) == "rule"


def test_auto_falls_back_to_llm() -> None:
    check = _check(evaluator="auto", llm=LLMSpec(rubric="语义正确"))
    assert route(check) == "llm"


def test_explicit_mode_requires_matching_spec() -> None:
    assert route(_check(evaluator="llm", llm=LLMSpec(rubric="r"))) == "llm"
    with pytest.raises(RoutingError):
        route(_check(evaluator="rule"))
    with pytest.raises(RoutingError):
        route(_check(evaluator="auto"))


class _FakeClient:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def complete_json(self, system: str, user: str) -> dict:
        if isinstance(self._payload, Exception):
            raise self._payload
        return {"score": self._payload, "evidence": ["轨迹第 2 步"], "failure_category": "answer_incomplete", "reasoning": "缺金额"}


def _case() -> EvaluationCase:
    return EvaluationCase.model_validate(
        {
            "id": "t1",
            "name": "t",
            "module": "billing",
            "input": {"turns": ["hi"]},
            "checks": [
                {"id": "sem", "metric": "answer_quality", "evaluator": "llm", "llm": {"rubric": "回复完整"}}
            ],
        }
    )


def test_llm_judge_scores_and_maps_category() -> None:
    judge = LLMJudge(_FakeClient(5))
    result = judge.evaluate(_case(), _case().checks[0], AgentRun(case_id="t1", final_answer="好"))
    assert result.passed and result.score == 1.0 and result.failure_category is None

    failing = LLMJudge(_FakeClient(2)).evaluate(_case(), _case().checks[0], AgentRun(case_id="t1"))
    assert failing.passed is False
    assert failing.failure_category is not None
    assert failing.failure_category.value == "answer_incomplete"


def test_llm_judge_pending_on_unavailable() -> None:
    judge = LLMJudge(_FakeClient(JudgeUnavailable("API key missing")))
    result = judge.evaluate(_case(), _case().checks[0], AgentRun(case_id="t1"))
    assert result.evaluator == "pending_human" and result.passed is None


def test_llm_judge_pending_on_bad_payload() -> None:
    class _Bad:
        def complete_json(self, system: str, user: str) -> dict:
            return {"score": "nine"}

    result = LLMJudge(_Bad()).evaluate(_case(), _case().checks[0], AgentRun(case_id="t1"))
    assert result.evaluator == "pending_human"
