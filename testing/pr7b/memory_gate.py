"""PR7-B Memory value, latency, provider, and reindex-coverage gate."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from property_agent.agent.application.embedding import OpenAICompatibleEmbeddingProvider
from property_agent.agent.application.memory_service import AgentMemoryService
from property_agent.agent.infrastructure.models import AgentMemoryModel
from property_agent.config import settings
from testing.memory_value_eval import ZERO_GATES
from testing.memory_value_eval import evaluate as evaluate_pr6
from testing.pr7b.evidence import (
    GateEvidence,
    GateStatus,
    dataset_sha256,
    repository_state,
    utc_now,
    write_evidence,
)
from testing.pr7b.pytest_gate import run_pytest_manifest

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = ROOT / "tests/agent/data/pr6_memory_value_cases.json"
THRESHOLDS = {
    "retrieval_precision": 0.80,
    "retrieval_recall": 0.75,
    "context_efficiency": 0.60,
    "clarification_reduction": 0.25,
    "task_completion_lift": 0.10,
    "relevant_personalization": 0.50,
}


@dataclass(frozen=True, slots=True)
class Scope:
    actor_id: UUID
    community_id: UUID
    house_ids: frozenset[UUID]


def evaluate(
    dataset: Path,
    *,
    requested_sha: str | None = None,
    server_observability_url: str | None = None,
    maintenance_window_id: str | None = None,
    maintenance_window_version: str | None = None,
) -> GateEvidence:
    import json

    started = utc_now()
    release_sha, dirty = repository_state(ROOT, requested_sha)
    cases = json.loads(dataset.read_text(encoding="utf-8"))
    timings: list[float] = []
    original = AgentMemoryService.retrieve

    def timed_retrieve(service: AgentMemoryService, query: Any):
        tick = perf_counter()
        try:
            return original(service, query)
        finally:
            timings.append(perf_counter() - tick)

    AgentMemoryService.retrieve = timed_retrieve
    try:
        report = evaluate_pr6(cases)
    finally:
        AgentMemoryService.retrieve = original

    metrics = dict(report["metrics"])
    metrics.update(
        {
            "retrieval_p50_seconds": _percentile(timings, 0.50),
            "retrieval_p95_seconds": _percentile(timings, 0.95),
            "retrieval_p99_seconds": _percentile(timings, 0.99),
        }
    )
    hard_gates = {
        **{
            f"{name}_threshold": metrics[name] >= threshold
            for name, threshold in THRESHOLDS.items()
        },
        **{f"{name}_zero": metrics[name] == 0 for name in ZERO_GATES},
    }
    failure_counts, failure_limitations, failure_contracts_passed = run_pytest_manifest(
        ROOT,
        [
            "tests/agent/test_pr7b_memory_failures.py",
            "tests/agent/test_pr7b_chaos.py::test_c10_memory_writer_persistence_failure_does_not_rollback_accepted_turn",
        ],
    )
    failure_contracts_passed = failure_contracts_passed and failure_counts["skipped"] == 0
    hard_gates["failure_semantics_contracts"] = failure_contracts_passed
    coverage, limitation = _external_reindex_probe()
    metrics.update(coverage)
    external_ready = not limitation
    hard_gates["embedding_provider_smoke"] = (
        external_ready and float(coverage["configured_model_version_coverage"]) >= 0.99
    )
    observed_metrics, observation_limitation = _memory_observability_summary(
        server_observability_url,
        release_sha=release_sha,
        started_at=started,
    )
    metrics.update(observed_metrics)
    observation_ready = not observation_limitation
    hard_gates["production_memory_observability_available"] = observation_ready
    window_metrics, window_limitation = _maintenance_window_certification(
        server_observability_url,
        release_sha=release_sha,
        window_id=maintenance_window_id,
        window_version=maintenance_window_version,
    )
    metrics.update(window_metrics)
    window_ready = not window_limitation
    hard_gates["maintenance_window_certification_available"] = window_ready
    hard_gates["maintenance_window_coverage_at_least_0_99"] = (
        window_ready and float(window_metrics.get("window_coverage", 0.0)) >= 0.99
    )
    hard_gates["maintenance_window_backlog_zero"] = (
        window_ready and int(window_metrics.get("window_pending_or_failed_backlog", -1)) == 0
    )
    hard_gates["maintenance_window_reindex_failures_zero"] = (
        window_ready and int(window_metrics.get("window_reindex_failure_total", -1)) == 0
    )
    if not failure_contracts_passed:
        status = GateStatus.FAIL
        limitations = failure_limitations
    elif dirty:
        status = GateStatus.NOT_RUN
        limitations = (*failure_limitations, "tracked checkout is dirty")
    elif not external_ready:
        status = GateStatus.NOT_RUN
        limitations = (*failure_limitations, limitation)
    elif not observation_ready:
        status = GateStatus.NOT_RUN
        limitations = (*failure_limitations, observation_limitation)
    elif not window_ready:
        status = GateStatus.NOT_RUN
        limitations = (*failure_limitations, window_limitation)
    else:
        status = GateStatus.PASS if all(hard_gates.values()) else GateStatus.FAIL
        limitations = (
            "local dedicated-database coverage is not a production maintenance-window claim",
        )
    return GateEvidence(
        schema_version="pr7b-evidence-v1",
        gate="MEMORY_GATE",
        status=status,
        release_sha=release_sha,
        git_dirty=dirty,
        environment=os.getenv("PR7B_ENVIRONMENT", "local"),
        started_at=started,
        ended_at=utc_now(),
        dataset_version="pr6-memory-value-v1",
        dataset_sha256=dataset_sha256(dataset),
        configuration={
            "embedding_model": settings.memory_embedding_model,
            "embedding_version": settings.memory_embedding_version,
            "fallback_mode": "structured-no-memory",
            "maintenance_window_id": maintenance_window_id or "",
            "maintenance_window_version": maintenance_window_version or "",
        },
        sample_counts={
            "paired_cases": len(cases),
            "retrieval_samples": len(timings),
            "failure_contract_tests": failure_counts["tests"],
            "failure_contract_test_failures": failure_counts["failures"] + failure_counts["errors"],
        },
        metrics=metrics,
        hard_gates=hard_gates,
        limitations=limitations,
    )


def _external_reindex_probe() -> tuple[dict[str, float | int | str], str]:
    database_url = os.getenv("TEST_POSTGRES_URL", "")
    if not settings.memory_embedding_api_key.strip():
        return _empty_coverage("EMBEDDING_CREDENTIAL_UNAVAILABLE"), "credential unavailable"
    if not _dedicated_test_database(database_url):
        return _empty_coverage("DEDICATED_DATABASE_REQUIRED"), "dedicated *_test database required"
    provider = OpenAICompatibleEmbeddingProvider(
        api_key=settings.memory_embedding_api_key,
        base_url=settings.memory_embedding_base_url,
        model=settings.memory_embedding_model,
        version=settings.memory_embedding_version,
        dimensions=settings.memory_embedding_dimensions,
        timeout_seconds=settings.memory_embedding_timeout_seconds,
    )
    engine = create_engine(database_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    actor = UUID("00000000-0000-0000-0000-000000000761")
    community = UUID("00000000-0000-0000-0000-000000000762")
    house = UUID("00000000-0000-0000-0000-000000000763")
    scope = Scope(actor, community, frozenset({house}))
    try:
        with factory() as session:
            AgentMemoryService(session, embedding_provider=provider).create_memory(
                scope,
                memory_type="COMMUNICATION",
                content="请使用简洁的服务说明。",
                house_id=house,
            )
        with factory() as session:
            active = _count(session, AgentMemoryModel.lifecycle_status == "ACTIVE")
            ready = _count(
                session,
                AgentMemoryModel.lifecycle_status == "ACTIVE",
                AgentMemoryModel.embedding_status == "READY",
                AgentMemoryModel.embedding_model == settings.memory_embedding_model,
                AgentMemoryModel.embedding_version == settings.memory_embedding_version,
            )
            pending = _count(
                session,
                AgentMemoryModel.lifecycle_status == "ACTIVE",
                AgentMemoryModel.embedding_status.in_(("PENDING", "FAILED")),
            )
            oldest = session.execute(
                select(func.min(AgentMemoryModel.updated_at)).where(
                    AgentMemoryModel.lifecycle_status == "ACTIVE",
                    AgentMemoryModel.embedding_status.in_(("PENDING", "FAILED")),
                )
            ).scalar_one()
        return {
            "eligible_active_records": active,
            "configured_model_version_ready_records": ready,
            "pending_or_failed_records": pending,
            "configured_model_version_coverage": ready / max(1, active),
            "reindex_backlog_age_seconds": (
                max(0.0, (datetime_now() - oldest).total_seconds()) if oldest else 0.0
            ),
            "degradation_reason": "",
        }, ""
    except Exception as exc:
        return _empty_coverage(type(exc).__name__.upper()), "external embedding/index probe failed"
    finally:
        engine.dispose()


def _memory_observability_summary(
    url: str | None, *, release_sha: str, started_at: str
) -> tuple[dict[str, float | int | str], str]:
    required = {
        "writer_extraction_failure_total",
        "writer_persistence_failure_total",
        "embedding_failure_total",
        "index_failure_total",
        "reindex_failure_total",
        "degradation_reason",
        "fallback_mode",
    }
    if not url:
        return {}, "server Memory observability summary unavailable"
    headers = {"Authorization": f"Bearer {os.getenv('PR7B_OTEL_SUMMARY_TOKEN', '')}"}
    try:
        response = httpx.get(
            url,
            headers=headers,
            params={"release_sha": release_sha, "started_at": started_at, "gate": "memory"},
            timeout=20.0,
        )
        response.raise_for_status()
        document = response.json()
    except (httpx.HTTPError, ValueError):
        return {}, "server Memory observability summary unavailable"
    if not isinstance(document, dict):
        return {}, "server Memory observability summary mismatched or incomplete"
    metrics = document.get("metrics", {})
    if document.get("release_sha") != release_sha or not required.issubset(metrics):
        return {}, "server Memory observability summary mismatched or incomplete"
    return {f"server_{name}": metrics[name] for name in sorted(required)}, ""


def _maintenance_window_certification(
    url: str | None,
    *,
    release_sha: str,
    window_id: str | None,
    window_version: str | None,
) -> tuple[dict[str, float | int | str], str]:
    if not url or not window_id or not window_version:
        return {}, "approved maintenance-window evidence unavailable"
    headers = {"Authorization": f"Bearer {os.getenv('PR7B_OTEL_SUMMARY_TOKEN', '')}"}
    try:
        response = httpx.get(
            url,
            headers=headers,
            params={
                "release_sha": release_sha,
                "gate": "memory-maintenance",
                "window_id": window_id,
                "window_version": window_version,
            },
            timeout=20.0,
        )
        response.raise_for_status()
        document = response.json()
    except (httpx.HTTPError, ValueError):
        return {}, "approved maintenance-window evidence unavailable"
    required = {
        "release_sha",
        "maintenance_window_id",
        "maintenance_window_version",
        "window_started_at",
        "window_ended_at",
        "embedding_model",
        "embedding_version",
        "eligible_active_records",
        "ready_at_configured_version",
        "coverage_at_window_end",
        "pending_or_failed_backlog_count",
        "oldest_backlog_age_seconds",
        "reindex_failure_count",
    }
    if not isinstance(document, dict) or not required.issubset(document):
        return {}, "maintenance-window evidence mismatched or incomplete"
    try:
        started = datetime.fromisoformat(str(document["window_started_at"]))
        ended = datetime.fromisoformat(str(document["window_ended_at"]))
        eligible = int(document["eligible_active_records"])
        ready = int(document["ready_at_configured_version"])
        coverage = float(document["coverage_at_window_end"])
        backlog = int(document["pending_or_failed_backlog_count"])
        oldest = float(document["oldest_backlog_age_seconds"])
        failures = int(document["reindex_failure_count"])
    except (TypeError, ValueError):
        return {}, "maintenance-window evidence mismatched or incomplete"
    exact = (
        document["release_sha"] == release_sha
        and document["maintenance_window_id"] == window_id
        and document["maintenance_window_version"] == window_version
        and document["embedding_model"] == settings.memory_embedding_model
        and document["embedding_version"] == settings.memory_embedding_version
        and ended >= started
        and started.tzinfo is not None
        and ended.tzinfo is not None
        and eligible > 0
        and 0 <= ready <= eligible
        and abs(coverage - ready / eligible) <= 1e-9
        and backlog >= 0
        and oldest >= 0
        and failures >= 0
    )
    if not exact:
        return {}, "maintenance-window evidence mismatched or incomplete"
    return {
        "window_eligible_active_records": eligible,
        "window_ready_at_configured_version": ready,
        "window_coverage": coverage,
        "window_pending_or_failed_backlog": backlog,
        "window_oldest_backlog_age_seconds": oldest,
        "window_reindex_failure_total": failures,
        "window_started_at": str(document["window_started_at"]),
        "window_ended_at": str(document["window_ended_at"]),
    }, ""


def datetime_now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _count(session: Any, *filters: Any) -> int:
    return int(
        session.execute(
            select(func.count()).select_from(AgentMemoryModel).where(*filters)
        ).scalar_one()
    )


def _empty_coverage(reason: str) -> dict[str, float | int | str]:
    return {
        "eligible_active_records": 0,
        "configured_model_version_ready_records": 0,
        "pending_or_failed_records": 0,
        "configured_model_version_coverage": 0.0,
        "reindex_backlog_age_seconds": 0.0,
        "degradation_reason": reason,
    }


def _dedicated_test_database(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url.replace("postgresql+psycopg", "postgresql"))
    return bool(parsed.path.removeprefix("/").endswith("_test"))


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * percentile + 0.999999)))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--sha")
    parser.add_argument("--server-observability-url")
    parser.add_argument("--maintenance-window-id")
    parser.add_argument("--maintenance-window-version")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = evaluate(
        args.dataset,
        requested_sha=args.sha,
        server_observability_url=args.server_observability_url,
        maintenance_window_id=args.maintenance_window_id,
        maintenance_window_version=args.maintenance_window_version,
    )
    write_evidence(args.output, evidence)
    print(f"MEMORY_GATE={evidence.status.value}")
    return 1 if evidence.status is GateStatus.FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
