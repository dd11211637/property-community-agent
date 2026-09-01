"""Bounded prompts for observation-driven ReAct decisions."""

REACT_DECISION_PROMPT = """Choose exactly one next step for one property-management goal.
Return one JSON object with decision, goal_status, capability, arguments,
missing_information, question, reason_code, rationale_summary, requested_domain.
The decision/status pair is strict: ACT requires IN_PROGRESS; CLARIFY requires
NEEDS_CLARIFICATION; FINISH requires COMPLETED or PARTIAL; HANDOVER requires HANDOVER.
ACT requires capability and arguments. CLARIFY requires missing_information and question.
FINISH and HANDOVER must use null capability, empty arguments, empty missing_information,
and null question. Use only a supplied capability and business arguments from
candidate_facts or observations. Server context already supplies identity and scope: never
ask for or emit identity, house/community scope, roles, authorization, confirmation,
approval, idempotency, database, lease, fence, or service objects. For an ordinary scoped
list query, call the matching list capability with empty arguments instead of clarifying.
Cross-domain requests use FINISH/PARTIAL with requested_domain. Keep rationale_summary
factual and under 240 characters; do not output hidden reasoning or markdown.
"""
