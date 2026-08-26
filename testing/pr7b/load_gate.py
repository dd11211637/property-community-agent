"""Bounded production-shaped HTTP/SSE load certification runner."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx

from testing.pr7b.capacity import (
    DEFAULT_R0_CONCURRENCY,
    CapacityBounds,
    r0_metadata,
)
from testing.pr7b.evidence import (
    GateEvidence,
    GateStatus,
    repository_state,
    utc_now,
    write_evidence,
)

ROOT = Path(__file__).resolve().parents[2]
_WRITE_ENVIRONMENTS = frozenset({"isolated-test", "preproduction"})
_EXPECTED_OUTCOMES = frozenset({200, 409, 422, 429, 503})


@dataclass(slots=True)
class LoadStats:
    totals: Counter[str] = field(default_factory=Counter)
    latency: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    first_event: list[float] = field(default_factory=list)
    phase_elapsed: dict[str, float] = field(default_factory=dict)
    infrastructure_failures: int = 0
    write_operations: int = 0
    abort_reason: str = ""


@dataclass(frozen=True, slots=True)
class LoadProfile:
    base_url: str
    token: str
    house_id: str
    environment: str
    expected_concurrency: int
    sustained_seconds: int
    spike_seconds: int
    allow_writes: bool
    smoke: bool


async def execute(
    profile: LoadProfile,
    bounds: CapacityBounds,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    requested_sha: str | None = None,
    server_observability_summary: Path | None = None,
    server_observability_url: str | None = None,
) -> GateEvidence:
    started = utc_now()
    release_sha, dirty = repository_state(ROOT, requested_sha)
    _validate_target(profile, bounds)
    stats = LoadStats()
    headers = {"Authorization": f"Bearer {profile.token}"}
    timeout = httpx.Timeout(bounds.request_timeout_seconds)
    limits = httpx.Limits(
        max_connections=bounds.max_concurrency,
        max_keepalive_connections=bounds.max_concurrency,
    )
    async with httpx.AsyncClient(
        base_url=profile.base_url,
        headers=headers,
        timeout=timeout,
        limits=limits,
        transport=transport,
    ) as client:
        pool_before = await _pool_snapshot(client)
        run_id = uuid4().hex
        shared_conversation = f"pr7b-{run_id}-shared"
        await _request(
            client,
            "POST",
            f"/api/agent/conversations/{shared_conversation}/messages",
            "shared_conversation_start",
            "setup",
            stats,
            _read_payload(profile),
        )
        await _phase(
            client,
            profile,
            bounds,
            stats,
            duration=profile.sustained_seconds,
            concurrency=profile.expected_concurrency,
            phase="sustained",
            run_id=run_id,
            shared_conversation=shared_conversation,
        )
        if not stats.abort_reason:
            await _phase(
                client,
                profile,
                bounds,
                stats,
                duration=profile.spike_seconds,
                concurrency=profile.expected_concurrency * 2,
                phase="spike",
                run_id=run_id,
                shared_conversation=shared_conversation,
            )
        pool_after = await _pool_snapshot(client)
    total = sum(value for key, value in stats.totals.items() if key.endswith(":request"))
    failure_rate = stats.infrastructure_failures / max(1, total)
    full_duration = profile.sustained_seconds >= 1_800 and profile.spike_seconds >= 600
    hard_gates = {
        "bounded_without_global_abort": not stats.abort_reason,
        "infrastructure_success_at_least_0_995": failure_rate <= 0.005,
        "full_sustained_duration": stats.phase_elapsed.get("sustained", 0.0) >= 1_799,
        "full_spike_duration": stats.phase_elapsed.get("spike", 0.0) >= 599,
        "hard_correctness_violations_zero": profile.smoke,
        "database_pool_evidence_available": profile.smoke or bool(pool_after),
    }
    latencies = [sample for values in stats.latency.values() for sample in values]
    pool_metrics = _pool_metrics(pool_before, pool_after)
    server_document = await _load_server_observability(
        server_observability_summary,
        server_observability_url,
        release_sha=release_sha,
        started_at=started,
    )
    server_metrics, server_evidence_ok = _server_observability_metrics(
        server_document, release_sha, total
    )
    hard_gates["server_otel_evidence_available"] = profile.smoke or server_evidence_ok
    if server_evidence_ok:
        hard_gates.update(_server_slo_gates(server_metrics))
    if profile.smoke:
        gate = "HARNESS_SMOKE"
        status = GateStatus.PASS if not stats.abort_reason else GateStatus.FAIL
    else:
        gate = "LOAD_GATE"
        if dirty or not server_evidence_ok:
            status = GateStatus.NOT_RUN
        else:
            status = (
                GateStatus.PASS if full_duration and all(hard_gates.values()) else GateStatus.FAIL
            )
    limitations = (
        "client metrics must be reconciled with the uploaded application OTel artifact",
        "R0 is a preproduction target, not a measured production traffic peak",
    )
    if dirty and not profile.smoke:
        limitations += ("tracked checkout is dirty",)
    return GateEvidence(
        schema_version="pr7b-evidence-v1",
        gate=gate,
        status=status,
        release_sha=release_sha,
        git_dirty=dirty,
        environment=profile.environment,
        started_at=started,
        ended_at=utc_now(),
        configuration={**r0_metadata(profile.expected_concurrency), "smoke": profile.smoke},
        sample_counts={
            "requests": total,
            "infrastructure_failures": stats.infrastructure_failures,
            "write_operations": stats.write_operations,
            **{key: value for key, value in sorted(stats.totals.items())},
        },
        metrics={
            "infrastructure_failure_rate": failure_rate,
            "latency_p50_seconds": _percentile(latencies, 0.50),
            "latency_p95_seconds": _percentile(latencies, 0.95),
            "latency_p99_seconds": _percentile(latencies, 0.99),
            "stream_first_event_p95_seconds": _percentile(stats.first_event, 0.95),
            "configured_sustained_seconds": profile.sustained_seconds,
            "configured_spike_seconds": profile.spike_seconds,
            "observed_sustained_seconds": stats.phase_elapsed.get("sustained", 0.0),
            "observed_spike_seconds": stats.phase_elapsed.get("spike", 0.0),
            "abort_reason": stats.abort_reason,
            **pool_metrics,
            **server_metrics,
        },
        hard_gates=hard_gates,
        limitations=limitations,
    )


async def _phase(
    client: httpx.AsyncClient,
    profile: LoadProfile,
    bounds: CapacityBounds,
    stats: LoadStats,
    *,
    duration: int,
    concurrency: int,
    phase: str,
    run_id: str,
    shared_conversation: str,
) -> None:
    phase_started = asyncio.get_running_loop().time()
    deadline = phase_started + duration
    tasks = [
        asyncio.create_task(
            _worker(
                client,
                profile,
                bounds,
                stats,
                deadline,
                phase,
                worker,
                run_id,
                shared_conversation,
            )
        )
        for worker in range(concurrency)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    stats.phase_elapsed[phase] = asyncio.get_running_loop().time() - phase_started
    worker_errors = sum(isinstance(result, BaseException) for result in results)
    if worker_errors:
        stats.infrastructure_failures += worker_errors
        stats.abort_reason = "one or more bounded load workers failed"


async def _worker(
    client: httpx.AsyncClient,
    profile: LoadProfile,
    bounds: CapacityBounds,
    stats: LoadStats,
    deadline: float,
    phase: str,
    worker: int,
    run_id: str,
    shared_conversation: str,
) -> None:
    conversation = f"pr7b-{run_id}-{worker}"
    preparation = (
        "active_message"
        if phase == "spike" and worker < profile.expected_concurrency
        else "conversation_start"
    )
    await _request(
        client,
        "POST",
        f"/api/agent/conversations/{conversation}/messages",
        preparation,
        phase,
        stats,
        _read_payload(profile),
    )
    iteration = 0
    while asyncio.get_running_loop().time() < deadline and not stats.abort_reason:
        total = sum(value for key, value in stats.totals.items() if key.endswith(":request"))
        if total >= bounds.max_requests:
            if not profile.smoke:
                stats.abort_reason = "maximum request budget exhausted before phase duration"
            return
        operation = (
            "active_message",
            "multi",
            "status",
            "sse",
            "memory",
            "same_conversation",
            "sse_disconnect",
        )[iteration % 7]
        if operation == "same_conversation":
            conversation = shared_conversation
        if profile.allow_writes and iteration % 13 == 12:
            operation = "write_cancel" if iteration % 26 == 12 else "write_confirm"
        if profile.allow_writes and iteration % 41 == 40:
            operation = "sse_waiting_confirm"
        await _operation(client, profile, bounds, stats, conversation, operation, phase, worker)
        iteration += 1
        total = sum(value for key, value in stats.totals.items() if key.endswith(":request"))
        if (
            total >= 20
            and stats.infrastructure_failures / total > bounds.infrastructure_failure_abort_rate
        ):
            stats.abort_reason = "infrastructure failure abort threshold exceeded"


async def _operation(
    client: httpx.AsyncClient,
    profile: LoadProfile,
    bounds: CapacityBounds,
    stats: LoadStats,
    conversation: str,
    operation: str,
    phase: str,
    worker: int,
) -> None:
    del worker
    path = f"/api/agent/conversations/{conversation}"
    if operation == "status":
        await _request(client, "GET", path, operation, phase, stats)
        return
    if operation == "memory":
        await _request(client, "GET", "/api/agent/memories", operation, phase, stats)
        return
    payload = _read_payload(profile, multi=operation == "multi")
    if operation == "sse":
        await _sse(client, f"{path}/messages/stream", payload, phase, stats)
        return
    if operation == "sse_disconnect":
        await _sse(
            client,
            f"{path}/messages/stream",
            payload,
            phase,
            stats,
            disconnect_after_first_event=True,
        )
        await _request(client, "GET", path, "sse_disconnect_recovery", phase, stats)
        return
    if operation == "sse_waiting_confirm":
        card = await _sse(
            client,
            f"{path}/messages/stream",
            _write_payload(profile),
            phase,
            stats,
        )
        if card:
            await _request(
                client,
                "POST",
                f"{path}/confirmations",
                "sse_confirmation_cancel",
                phase,
                stats,
                {"confirmed": False, "action_hash": card["action_hash"]},
            )
        return
    if operation.startswith("write_"):
        if stats.write_operations >= bounds.max_write_operations:
            return
        payload = _write_payload(profile)
        response = await _request(
            client, "POST", f"{path}/messages", operation, phase, stats, payload
        )
        card = _pending_card(response)
        if card:
            confirmed = operation == "write_confirm"
            await _request(
                client,
                "POST",
                f"{path}/confirmations",
                f"confirmation_{str(confirmed).lower()}",
                phase,
                stats,
                {"confirmed": confirmed, "action_hash": card["action_hash"]},
            )
            stats.write_operations += int(confirmed)
            if confirmed:
                await _request(
                    client,
                    "POST",
                    f"{path}/confirmations",
                    "confirmation_replay",
                    phase,
                    stats,
                    {"confirmed": True, "action_hash": card["action_hash"]},
                )
        return
    await _request(client, "POST", f"{path}/messages", operation, phase, stats, payload)


async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    operation: str,
    phase: str,
    stats: LoadStats,
    payload: dict[str, Any] | None = None,
) -> httpx.Response | None:
    started = perf_counter()
    stats.totals[f"{phase}:{operation}:request"] += 1
    try:
        response = await client.request(method, path, json=payload)
        stats.latency[operation].append(perf_counter() - started)
        stats.totals[f"{phase}:{operation}:status_{response.status_code}"] += 1
        if response.status_code >= 500 or response.status_code not in _EXPECTED_OUTCOMES:
            stats.infrastructure_failures += 1
        return response
    except (httpx.HTTPError, TimeoutError):
        stats.latency[operation].append(perf_counter() - started)
        stats.infrastructure_failures += 1
        stats.totals[f"{phase}:{operation}:transport_failure"] += 1
        return None


async def _sse(
    client: httpx.AsyncClient,
    path: str,
    payload: dict[str, Any],
    phase: str,
    stats: LoadStats,
    *,
    disconnect_after_first_event: bool = False,
) -> dict[str, Any] | None:
    started = perf_counter()
    stats.totals[f"{phase}:sse:request"] += 1
    try:
        async with client.stream("POST", path, json=payload) as response:
            first = True
            event_name = ""
            pending_card = None
            async for line in response.aiter_lines():
                if first and line.startswith("event:"):
                    stats.first_event.append(perf_counter() - started)
                    first = False
                    if disconnect_after_first_event:
                        break
                if line.startswith("event:"):
                    event_name = line.partition(":")[2].strip()
                elif event_name == "confirmation" and line.startswith("data:"):
                    try:
                        value = json.loads(line.partition(":")[2].strip())
                    except ValueError:
                        stats.infrastructure_failures += 1
                        continue
                    if isinstance(value, dict) and value.get("action_hash"):
                        pending_card = value
                if line == "event: done":
                    break
            stats.latency["sse"].append(perf_counter() - started)
            stats.totals[f"{phase}:sse:status_{response.status_code}"] += 1
            if response.status_code >= 500 or response.status_code not in _EXPECTED_OUTCOMES:
                stats.infrastructure_failures += 1
            return pending_card
    except httpx.HTTPError:
        stats.infrastructure_failures += 1
        stats.totals[f"{phase}:sse:transport_failure"] += 1
        return None


def _read_payload(profile: LoadProfile, *, multi: bool = False) -> dict[str, Any]:
    return {
        "text": "查询账单并查看已有报修记录" if multi else "查看当前账单",
        "house_id": profile.house_id,
        "slots": {},
    }


def _write_payload(profile: LoadProfile) -> dict[str, Any]:
    return {
        "text": "我要报修",
        "house_id": profile.house_id,
        "slots": {
            "action": "create",
            "location": "厨房",
            "description": "测试环境水管漏水",
            "category": "WATER_PLUMBING",
        },
    }


def _pending_card(response: httpx.Response | None) -> dict[str, Any] | None:
    if response is None or response.status_code != 200:
        return None
    try:
        return response.json()["data"].get("pending_confirmation")
    except (KeyError, TypeError, ValueError):
        return None


async def _pool_snapshot(client: httpx.AsyncClient) -> dict[str, Any]:
    try:
        response = await client.get("/ready")
        payload = response.json()
        detail = payload.get("detail")
        components = payload.get("components") or (
            detail.get("components") if isinstance(detail, dict) else {}
        )
        snapshot = components.get("database_pool") or {}
        return snapshot if snapshot.get("state") == "OBSERVED" else {}
    except (httpx.HTTPError, TypeError, ValueError):
        return {}


def _pool_metrics(before: dict[str, Any], after: dict[str, Any]) -> dict[str, int]:
    return {
        "database_pool_checkout_delta": max(
            0, int(after.get("checkout_total", 0)) - int(before.get("checkout_total", 0))
        ),
        "database_pool_timeout_delta": max(
            0, int(after.get("timeout_total", 0)) - int(before.get("timeout_total", 0))
        ),
        "database_pool_failure_delta": max(
            0, int(after.get("failure_total", 0)) - int(before.get("failure_total", 0))
        ),
        "database_pool_peak_in_use": int(after.get("peak_in_use", 0)),
        "database_pool_peak_overflow": int(after.get("peak_overflow", 0)),
        "database_pool_base_capacity": int(after.get("base_capacity", 0)),
    }


async def _load_server_observability(
    path: Path | None,
    url: str | None,
    *,
    release_sha: str,
    started_at: str,
) -> dict[str, Any]:
    if url:
        headers = {"Authorization": f"Bearer {os.getenv('PR7B_OTEL_SUMMARY_TOKEN', '')}"}
        try:
            async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
                response = await client.get(
                    url,
                    params={"release_sha": release_sha, "started_at": started_at},
                )
                response.raise_for_status()
                value = response.json()
                return value if isinstance(value, dict) else {}
        except (httpx.HTTPError, ValueError):
            return {}
    if path is not None and path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _server_observability_metrics(
    document: dict[str, Any], release_sha: str, client_total: int
) -> tuple[dict[str, float | int], bool]:
    if not document:
        return {}, False
    required_signals = {
        "model",
        "checkpoint",
        "accepted_head",
        "lease_fence",
        "approval",
        "memory",
        "stream",
        "request_runtime",
    }
    required_metrics = {
        "agent_infrastructure_success_rate",
        "runtime_success_rate",
        "accepted_head_success_rate",
        "capability_success_rate",
        "hard_correctness_violation_total",
        "model_latency_p95_seconds",
        "capability_latency_p95_seconds",
        "checkpoint_latency_p95_seconds",
        "accepted_head_latency_p95_seconds",
        "memory_latency_p95_seconds",
        "lease_acquisition_total",
        "lease_contention_total",
        "fence_rejection_total",
        "accepted_head_cas_conflict_total",
        "memory_advisory_lock_contention_total",
        "approval_consume_contention_total",
        "business_idempotency_conflict_total",
        "stream_peak_active",
        "stream_capacity",
        "queue_backlog_peak",
    }
    signals = document.get("signal_totals", {})
    aggregate_metrics = document.get("metrics", {})
    if (
        document.get("release_sha") != release_sha
        or not required_signals.issubset(signals)
        or not required_metrics.issubset(aggregate_metrics)
    ):
        return {}, False
    server_total = int(document.get("request_total", -1))
    if server_total < 0:
        return {}, False
    discrepancy = abs(server_total - client_total) / max(1, client_total)
    return {
        "server_request_total": server_total,
        "client_server_request_discrepancy_rate": discrepancy,
        **{f"server_signal_{name}_total": int(signals[name]) for name in sorted(signals)},
        **{f"server_{name}": float(aggregate_metrics[name]) for name in sorted(required_metrics)},
    }, True


def _server_slo_gates(metrics: dict[str, float | int]) -> dict[str, bool]:
    return {
        "hard_correctness_violations_zero": (
            metrics["server_hard_correctness_violation_total"] == 0
        ),
        "agent_infrastructure_success_at_least_0_995": (
            metrics["server_agent_infrastructure_success_rate"] >= 0.995
        ),
        "runtime_success_at_least_0_995": metrics["server_runtime_success_rate"] >= 0.995,
        "accepted_head_success_at_least_0_9999": (
            metrics["server_accepted_head_success_rate"] >= 0.9999
        ),
        "capability_success_at_least_0_995": (metrics["server_capability_success_rate"] >= 0.995),
    }


def _validate_target(profile: LoadProfile, bounds: CapacityBounds) -> None:
    bounds.validate()
    if profile.expected_concurrency * 2 > bounds.max_concurrency:
        raise ValueError("2x spike exceeds max concurrency")
    if not profile.base_url.startswith(("http://", "https://")):
        raise ValueError("explicit HTTP(S) target is required")
    if not profile.token or not profile.house_id:
        raise ValueError("trusted bearer token and bound house ID are required")
    if profile.allow_writes and profile.environment not in _WRITE_ENVIRONMENTS:
        raise ValueError("write-capable load requires an isolated-test or preproduction marker")


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * percentile + 0.999999))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--expected-concurrency", type=int, default=DEFAULT_R0_CONCURRENCY)
    parser.add_argument("--max-concurrency", type=int, default=32)
    parser.add_argument("--sustained-seconds", type=int, default=1_800)
    parser.add_argument("--spike-seconds", type=int, default=600)
    parser.add_argument("--max-conversations", type=int, default=256)
    parser.add_argument("--max-requests", type=int, default=100_000)
    parser.add_argument("--max-write-operations", type=int, default=128)
    parser.add_argument("--request-timeout", type=float, default=20.0)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--allow-writes", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--sha")
    parser.add_argument("--server-observability-summary", type=Path)
    parser.add_argument("--server-observability-url")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.smoke:
        args.sustained_seconds = min(args.sustained_seconds, 3)
        args.spike_seconds = min(args.spike_seconds, 2)
    profile = LoadProfile(
        base_url=args.base_url.rstrip("/"),
        token=os.getenv("PR7B_BEARER_TOKEN", ""),
        house_id=os.getenv("PR7B_HOUSE_ID", ""),
        environment=args.environment,
        expected_concurrency=args.expected_concurrency,
        sustained_seconds=args.sustained_seconds,
        spike_seconds=args.spike_seconds,
        allow_writes=args.allow_writes,
        smoke=args.smoke,
    )
    bounds = CapacityBounds(
        expected_concurrency=args.expected_concurrency,
        max_concurrency=args.max_concurrency,
        max_conversations=args.max_conversations,
        max_requests=args.max_requests,
        max_write_operations=args.max_write_operations,
        max_run_seconds=args.sustained_seconds + args.spike_seconds,
        request_timeout_seconds=args.request_timeout,
    )
    evidence = asyncio.run(
        execute(
            profile,
            bounds,
            requested_sha=args.sha,
            server_observability_summary=args.server_observability_summary,
            server_observability_url=args.server_observability_url,
        )
    )
    write_evidence(args.output, evidence)
    print(f"{evidence.gate}={evidence.status.value}")
    return 1 if evidence.status is GateStatus.FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
