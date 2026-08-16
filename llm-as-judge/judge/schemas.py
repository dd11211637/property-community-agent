"""评测数据模型 — 用例 / 运行 / 结果三段契约。

对齐 agent 实际运行形态：GraphState（intent、slots、tool_result、
read_trace、handover_required）+ 最后一条 assistant 消息。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Metric(StrEnum):
    """Agent 质量六维指标。"""

    TASK_SUCCESS = "task_success"
    TOOL_CORRECTNESS = "tool_correctness"
    WORKFLOW_CORRECTNESS = "workflow_correctness"
    ANSWER_QUALITY = "answer_quality"
    INSTRUCTION_FOLLOWING = "instruction_following"
    SAFETY = "safety"


class FailureCategory(StrEnum):
    """失败归因分类，规则与 LLM Judge 共用同一词表。"""

    INTENT_MISROUTE = "intent_misroute"
    SLOT_MISSING = "slot_missing"
    TOOL_MISSING = "tool_missing"
    TOOL_WRONG = "tool_wrong"
    TOOL_ORDER = "tool_order"
    PARAM_WRONG = "param_wrong"
    CONFIRMATION_BYPASS = "confirmation_bypass"
    HANDOVER_MISSING = "handover_missing"
    ANSWER_INCOMPLETE = "answer_incomplete"
    ANSWER_WRONG = "answer_wrong"
    ANSWER_HALLUCINATED = "answer_hallucinated"
    INSTRUCTION_VIOLATION = "instruction_violation"
    SAFETY_VIOLATION = "safety_violation"
    DEGRADATION = "degradation"
    UNEVALUATED = "unevaluated"


class CaseInput(BaseModel):
    """评测输入：模拟用户侧可见的全部信息。"""

    turns: list[str] = Field(min_length=1, description="按顺序发送的用户消息")
    role: str = Field(default="resident", description="resident / staff / admin")
    house_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class RuleSpec(BaseModel):
    """确定性规则检查，kind 决定参数含义。"""

    kind: Literal[
        "exact_match",
        "contains_all",
        "contains_any",
        "not_contains",
        "regex",
        "not_regex",
        "intent_is",
        "tool_sequence",
        "tool_calls_include",
        "forbidden_tools",
        "max_steps",
        "max_read_steps",
        "write_requires_confirmation",
        "handover_on_high_risk",
        "slot_requested",
        "slots_include",
    ]
    params: dict[str, Any] = Field(default_factory=dict)


class LLMSpec(BaseModel):
    """LLM Judge 评审规格。"""

    rubric: str = Field(description="评分标准（判定什么算合格）")
    criteria: list[str] = Field(default_factory=list, description="分项考察点")


class Check(BaseModel):
    """单条检查：规则与 LLM 至少配一个；auto 路由优先规则。"""

    id: str
    metric: Metric
    target: Literal["final_answer", "trace", "both"] = "final_answer"
    evaluator: Literal["auto", "rule", "llm"] = "auto"
    rule: RuleSpec | None = None
    llm: LLMSpec | None = None
    failure_category: FailureCategory | None = None
    weight: float = Field(default=1.0, gt=0)
    high_risk: bool = Field(default=False, description="命中即强制人工抽检")


class EvaluationCase(BaseModel):
    """完整评测用例。"""

    id: str
    name: str
    module: Literal["repair", "announcement", "billing", "inspection", "platform", "agent"]
    description: str = ""
    high_risk: bool = False
    input: CaseInput
    ground_truth: str = Field(default="", description="给 LLM Judge 的参考事实")
    expected_behavior: str = ""
    constraints: list[str] = Field(default_factory=list)
    checks: list[Check] = Field(min_length=1)


class TraceEvent(BaseModel):
    """规范化运行事件，录制器与规则评估器共用。"""

    step: int = Field(ge=1)
    type: Literal[
        "intent",
        "slot_request",
        "slot_set",
        "tool_call",
        "tool_result",
        "confirmation_request",
        "confirmation_granted",
        "handover",
        "degrade",
        "reply",
    ]
    name: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    ok: bool | None = None
    detail: str = ""


class AgentRun(BaseModel):
    """一次 Agent 运行的完整记录。"""

    case_id: str
    agent_mode: Literal["deepseek", "keyword"] = "keyword"
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    events: list[TraceEvent] = Field(default_factory=list)
    final_answer: str = ""
    handover_required: bool = False
    degraded: bool = False


class CheckResult(BaseModel):
    """单条 check 的评估结果。"""

    check_id: str
    metric: Metric
    evaluator: Literal["rule", "llm", "pending_human"]
    passed: bool | None = Field(default=None, description="None = 无法判定")
    score: float = Field(default=0.0, ge=0, le=1)
    evidence: str = ""
    failure_category: FailureCategory | None = None
    error: str = ""


class CaseResult(BaseModel):
    """一个用例的聚合结果。"""

    case_id: str
    checks: list[CheckResult]
    metric_scores: dict[str, float]
    overall: float
    safety_gate_failed: bool = False
    needs_human_review: bool = False
    failure_categories: list[str] = Field(default_factory=list)


class RunReport(BaseModel):
    """一次评测批次的报告。"""

    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    total_cases: int = 0
    results: list[CaseResult] = Field(default_factory=list)
    average_overall: float = 0.0
    failed_cases: list[str] = Field(default_factory=list)
    human_review_cases: list[str] = Field(default_factory=list)
    high_risk_cases: list[str] = Field(default_factory=list)
