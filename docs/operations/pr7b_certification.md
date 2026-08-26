# PR7-B production evaluation and resilience gates

PR7-B certifies an exact release SHA. It does not select, roll out, shadow-write, or make
v2 publicly eligible. Public new-conversation selection remains hard-zero v2.

## Evidence and status

Every gate writes `pr7b-evidence-v1` JSON containing the exact SHA, tracked dirty state,
environment, dataset/config versions, timestamps, sample counts, aggregate metrics, hard
gates, and limitations. Raw prompts, raw Memory, credentials, confirmation material,
idempotency keys, and PII are rejected from evidence fields. `PASS`, `FAIL`, and `NOT_RUN`
are distinct. Evidence from an older SHA is stale as soon as branch HEAD moves.

The protected `PR7-B protected certification` workflow checks out the requested SHA,
rejects a moved or dirty checkout, runs against the `pr7b-certification` GitHub Environment,
and uploads immutable artifacts. Missing credentials produce `NOT_RUN`; they never select
the deterministic provider as real-model evidence.

Operator entry points:

```text
python -m testing.pr7b.real_model_gate --sha <full-sha> --approved-baseline <approved-aggregate.json> --output artifacts/real-model.json
python -m testing.pr7b.memory_gate --sha <full-sha> --server-observability-url <collector-summary-url> --output artifacts/memory.json
python -m testing.pr7b.load_gate --base-url <url> --environment preproduction --expected-concurrency 8 --sustained-seconds 1800 --spike-seconds 600 --allow-writes --sha <full-sha> --server-observability-url <collector-summary-url> --output artifacts/load.json
python -m testing.pr7b.chaos_gate --campaign full --sha <full-sha> --server-observability-url <collector-summary-url> --output artifacts/chaos.json
python -m testing.pr7b.adversarial_gate --sha <full-sha> --output artifacts/adversarial.json
```

## R0 preproduction capacity target

`pr7b-r0-v1` defines `R0_PREPRODUCTION_CAPACITY_TARGET=8`. This is not a claim about a
measured production traffic peak. The repository has no authoritative production peak.

The bounded target is derived from current Compose and runtime facts:

- one backend Uvicorn process/worker per container;
- stream-producer admission cap 16 and shutdown drain grace 15 seconds;
- Agent lease 30 seconds;
- model total deadline 6 seconds with at most one retry;
- PostgreSQL uses SQLAlchemy `QueuePool` behavior with `pool_pre_ping=True`;
- the currently resolved SQLAlchemy 2.0 defaults are base size 5, overflow allowance 10,
  and pool timeout 30 seconds, but these are framework defaults—not a frozen production
  capacity policy;
- R0 uses half the stream admission ceiling, leaving headroom for non-stream HTTP and
  lifecycle work. Its 2x spike intentionally reaches the stream ceiling and may exercise
  pool wait/overflow behavior.

The target is an explicit CLI input. The harness bounds concurrency, conversations,
requests, writes, duration, per-request timeout, and global infrastructure-failure abort.
Write-capable traffic requires the explicit `isolated-test` or `preproduction` marker.
The full gate is at least 30 minutes at R0 followed by at least 10 minutes at 2x R0. A
short test is only `HARNESS_SMOKE=PASS` and can never yield `LOAD_GATE=PASS`.
The full gate also requires an exact-SHA server observability summary. The summary endpoint
is queried after the load window with `release_sha` and `started_at` parameters and returns
`request_total` plus bounded aggregate `signal_totals` for model, checkpoint, accepted head,
lease/fence, approval, Memory, stream, and request/runtime signals. Missing or mismatched
server evidence yields `LOAD_GATE=NOT_RUN`; it is never inferred from client measurements.
Its aggregate metrics include the Stage Contract success rates, hard-correctness count,
component latency summaries, lease/fence/CAS/approval/idempotency contention, queue backlog,
and stream active/capacity values. These values come from PR7-A production telemetry.

## Database pool signals

The application observes the existing SQLAlchemy pool without changing its policy or
creating another pool:

- `database_pool_checkout_total` by bounded outcome/reason;
- `database_pool_checkin_total`;
- `database_pool_connections_in_use`;
- `database_pool_connections_idle`;
- `database_pool_base_capacity`;
- `database_pool_current_overflow`;
- `database_pool_overflow_allowance` where the active SQLAlchemy pool exposes it;
- `database_pool_connection_use_duration_seconds`;
- `database_pool_connection_failure_total` for invalidation;
- checkout timeout as `database_pool_checkout_total{outcome=failure,reason=timeout}`.

SQLAlchemy does not expose a portable checkout-start event, so the repository does not
mislabel connection-hold duration as checkout wait. Pool timeout is measured at the FastAPI
session boundary; client latency and OTel totals must be reconciled in load evidence.

## Gate boundaries

- The real-model holdout expands to exactly 100 versioned cases and goes through the
  production DeepSeek/Fallback/Observed gateway plus `SupervisorPlanner`. Aggregates retain
  primary-provider attempts separately from logical fallback outcomes. PASS also requires a
  frozen, human-approved aggregate baseline, and reports absolute deltas for task completion,
  clarification, handover, and unsafe selection without storing prompts or case text.
- The Memory gate extends the PR6 paired evaluator, raises precision to 0.80, measures
  retrieval p50/p95/p99, probes the configured external embedding in a dedicated `*_test`
  PostgreSQL database, and measures configured model/version coverage and backlog age. Its
  exact-window server summary records Writer extraction/persistence, embedding, index, reindex,
  degradation-reason, and fallback-mode signals; missing server evidence is `NOT_RUN`.
- The chaos campaign has C1-C12 evidence. C7 and C8 cross a real subprocess boundary;
  exception-only tests are not presented as process-death evidence. Full PASS also requires
  an exact-window server telemetry signal for every injected case.
- The versioned adversarial manifest reuses production validators, authority seams, approval/fence,
  accepted-head, Memory, privacy, and idempotency tests. A confirmed hard-zero violation
  fails the gate rather than being averaged into a score.

Long or credentialed gates are manual. Normal pull-request CI verifies schemas/CLIs, a
bounded load smoke, safe fault injection, deterministic adversarial cases, and the existing
zero-skip PostgreSQL suite.
