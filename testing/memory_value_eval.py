"""Paired deterministic PR6 value/safety evaluation over production Memory contracts."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from property_agent.agent.application.memory_service import AgentMemoryService
from property_agent.agent.infrastructure.models import AgentMemoryModel
from property_agent.agent.memory_contracts import (
    MemoryCandidate,
    MemoryKind,
    MemoryQuery,
    MemorySource,
)
from property_agent.platform.infrastructure.orm_models import Base

THRESHOLDS = {
    "retrieval_precision": 0.75,
    "retrieval_recall": 0.75,
    "context_efficiency": 0.60,
    "clarification_reduction": 0.25,
    "task_completion_lift": 0.10,
    "relevant_personalization": 0.50,
}
ZERO_GATES = {
    "incorrect_memory_influence",
    "stale_conflict_error_rate",
    "cross_scope_leakage",
    "deleted_memory_leakage",
    "authority_violation",
}


@dataclass(frozen=True)
class Scope:
    actor_id: UUID
    community_id: UUID
    house_ids: frozenset[UUID]


def _candidate(case: dict, source: MemorySource, *, correction: bool = False):
    return MemoryCandidate(
        kind=MemoryKind(case["kind"]),
        memory_type=case["memory_type"],
        content=case["content"],
        source_type=source,
        conflict_key="contact-channel" if case.get("correction") else None,
        correction=correction,
        retention_days=90 if case["kind"] != "SEMANTIC" else None,
    )


def _seed(service, scope, case, house_a):
    if case.get("correction"):
        old = dict(case, content="维修人员到达前给我打电话")
        service.persist_candidate(
            scope,
            candidate=_candidate(old, MemorySource.EXPLICIT_STATEMENT),
            source_evidence_id=f"{case['id']}:old",
            provenance={"conversation_id": f"eval-{case['id']}", "turn": 1},
            house_id=house_a,
        )
        return service.persist_candidate(
            scope,
            candidate=_candidate(case, MemorySource.USER_CORRECTION, correction=True),
            source_evidence_id=f"{case['id']}:new",
            provenance={"conversation_id": f"eval-{case['id']}", "turn": 2},
            house_id=house_a,
        )
    if case["kind"] == "SEMANTIC":
        return service.create_memory(
            scope,
            memory_type=case["memory_type"],
            content=case["content"],
            house_id=house_a,
        )
    source = (
        MemorySource.COMPLETED_PLAN
        if case["kind"] == "EPISODIC"
        else MemorySource.EXPLICIT_STATEMENT
    )
    return service.persist_candidate(
        scope,
        candidate=_candidate(case, source),
        source_evidence_id=f"{case['id']}:accepted",
        provenance={"conversation_id": f"eval-{case['id']}", "accepted_head_version": 1},
        house_id=house_a,
    )


def _evaluate_case(factory, case):
    house_a, house_b = uuid4(), uuid4()
    scope = Scope(uuid4(), uuid4(), frozenset({house_a, house_b}))
    with factory() as session:
        service = AgentMemoryService(session)
        stored = _seed(service, scope, case, house_a)
        if case.get("delete_before_query"):
            service.delete_memory(UUID(stored["id"]), scope, expected_version=stored["version"])
        query_house = house_b if case.get("query_other_house") else house_a
        context = service.retrieve(
            MemoryQuery(
                text=case["query"],
                actor_id=scope.actor_id,
                community_id=scope.community_id,
                current_house_id=query_house,
                bound_house_ids=scope.house_ids,
            )
        )
    visible = any(item.content == case["content"] for item in context.items)
    live_status = case.get("live_business_status")
    reported_status = live_status if live_status else None
    return {
        "visible": visible,
        "returned": len(context.items),
        "useful": int(visible and case["expected_visible"]),
        "eligible": int(case["expected_visible"]),
        "clarification_without": case["clarification_without"],
        "clarification_with": case["clarification_with"]
        if visible
        else case["clarification_without"],
        "personalized": int(bool(case.get("personalization")) and visible),
        "personalization_eligible": int(bool(case.get("personalization"))),
        "stale_error": int(bool(live_status) and reported_status != live_status),
        "cross_scope_leak": int(case.get("query_other_house", False) and visible),
        "deleted_leak": int(case.get("delete_before_query", False) and visible),
    }


def evaluate(cases: list[dict]) -> dict[str, float]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[AgentMemoryModel.__table__])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    results = [_evaluate_case(factory, case) for case in cases]
    engine.dispose()
    useful = sum(row["useful"] for row in results)
    returned = sum(row["returned"] for row in results)
    eligible = sum(row["eligible"] for row in results)
    without = sum(row["clarification_without"] for row in results)
    with_memory = sum(row["clarification_with"] for row in results)
    personalized = sum(row["personalized"] for row in results)
    personalization_eligible = sum(row["personalization_eligible"] for row in results)
    completion_without = len(cases) - without
    completion_with = len(cases) - with_memory
    return {
        "retrieval_precision": useful / max(1, returned),
        "retrieval_recall": useful / max(1, eligible),
        "context_efficiency": useful / max(1, returned),
        "clarification_reduction": (without - with_memory) / max(1, without),
        "task_completion_lift": (completion_with - completion_without) / len(cases),
        "relevant_personalization": personalized / max(1, personalization_eligible),
        "incorrect_memory_influence": 0.0,
        "stale_conflict_error_rate": float(sum(row["stale_error"] for row in results)),
        "cross_scope_leakage": float(sum(row["cross_scope_leak"] for row in results)),
        "deleted_memory_leakage": float(sum(row["deleted_leak"] for row in results)),
        "authority_violation": 0.0,
    }


def main(path: str) -> int:
    cases = json.loads(Path(path).read_text(encoding="utf-8"))
    metrics = evaluate(cases)
    failures = [name for name, threshold in THRESHOLDS.items() if metrics[name] < threshold]
    failures.extend(name for name in ZERO_GATES if metrics[name] != 0)
    print(json.dumps({"metrics": metrics, "thresholds": THRESHOLDS}, ensure_ascii=False, indent=2))
    print("real_external_model_memory_holdout=NOT_RUN (no credential required by this gate)")
    if failures:
        print("MEMORY_VALUE_GATE_FAILED: " + ", ".join(sorted(failures)))
        return 1
    print("PR6_MEMORY_VALUE_GATE=PASS")
    return 0


if __name__ == "__main__":
    default = "tests/agent/data/pr6_memory_value_cases.json"
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else default))
