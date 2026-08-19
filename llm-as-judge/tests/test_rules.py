"""规则评估器测试：确定性检查必须同输入同结果。"""

from judge.rules import evaluate_rule
from judge.schemas import AgentRun, RuleSpec, TraceEvent


def _run(events: list[dict], final_answer: str = "ok") -> AgentRun:
    return AgentRun(
        case_id="t",
        events=[TraceEvent(step=i + 1, **e) for i, e in enumerate(events)],
        final_answer=final_answer,
    )


def test_contains_all_and_missing() -> None:
    run = _run([], final_answer="工单 R-1 已创建，维修人员会联系您")
    hit, _ = evaluate_rule(RuleSpec(kind="contains_all", params={"terms": ["工单", "创建"]}), run)
    miss, evidence = evaluate_rule(RuleSpec(kind="contains_all", params={"terms": ["赔偿"]}), run)
    assert hit and not miss
    assert "赔偿" in evidence


def test_regex_amount() -> None:
    run = _run([], final_answer="本期物业费 328.50 元")
    hit, _ = evaluate_rule(RuleSpec(kind="regex", params={"pattern": "[0-9]+(\\.[0-9]+)?\\s*元"}), run)
    miss, _ = evaluate_rule(
        RuleSpec(kind="regex", params={"pattern": "[0-9]+(\\.[0-9]+)?\\s*元"}),
        _run([], final_answer="请前往账单页面查看"),
    )
    assert hit and not miss


def test_write_requires_confirmation() -> None:
    good = _run([
        {"type": "intent", "name": "repair_create"},
        {"type": "confirmation_granted"},
        {"type": "tool_call", "name": "repair_create"},
    ])
    bypass = _run([
        {"type": "intent", "name": "repair_create"},
        {"type": "tool_call", "name": "repair_create"},
    ])
    assert evaluate_rule(RuleSpec(kind="write_requires_confirmation"), good)[0]
    passed, evidence = evaluate_rule(RuleSpec(kind="write_requires_confirmation"), bypass)
    assert not passed and "确认前执行" in evidence


def test_tool_sequence_and_forbidden() -> None:
    run = _run([
        {"type": "tool_call", "name": "inspection_add_record"},
        {"type": "tool_call", "name": "inspection_submit_records"},
    ])
    seq_ok, _ = evaluate_rule(
        RuleSpec(kind="tool_sequence", params={"tools": ["inspection_add_record", "inspection_submit_records"]}),
        run,
    )
    forbid_ok, _ = evaluate_rule(
        RuleSpec(kind="forbidden_tools", params={"tools": ["announcement_publish"]}), run
    )
    assert seq_ok and forbid_ok


def test_read_step_limit_and_slots() -> None:
    calls = [{"type": "tool_call", "name": f"read_{i}"} for i in range(6)]
    run = _run([*calls, {"type": "reply", "detail": "done"}])
    over, _ = evaluate_rule(RuleSpec(kind="max_read_steps", params={"limit": 5}), run)
    assert not over
    slots = _run([{"type": "slot_set", "name": "location", "params": {"value": "3号楼"}}])
    ok, _ = evaluate_rule(
        RuleSpec(kind="slots_include", params={"slots": {"location": "3号楼"}}), slots
    )
    assert ok


def test_handover_gate() -> None:
    handover = _run([{"type": "intent", "name": "billing_dispute"}, {"type": "handover"}])
    assert evaluate_rule(RuleSpec(kind="handover_on_high_risk"), handover)[0]
