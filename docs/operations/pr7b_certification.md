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
the deterministic provider as real-model evidence. An `all` dispatch attempts every selected
gate even when an earlier gate is `FAIL` or `NOT_RUN`, builds and uploads the complete bounded
baseline, and only then fails the workflow unless every selected gate is `PASS`.

Operator entry points:

```text
python -m testing.pr7b.real_model_gate --sha <full-sha> --approved-baseline config/pr7b_real_model_approved_baseline_v1.json --output artifacts/real-model.json
python -m testing.pr7b.memory_gate --sha <full-sha> --server-observability-url <collector-summary-url> --maintenance-window-id <approved-id> --maintenance-window-version <approved-version> --output artifacts/memory.json
python -m testing.pr7b.load_gate --base-url <url> --environment preproduction --expected-concurrency 8 --sustained-seconds 1800 --spike-seconds 600 --allow-writes --sha <full-sha> --server-observability-url <collector-summary-url> --output artifacts/load.json
python -m testing.pr7b.chaos_gate --campaign full --campaign-id <32-lowercase-hex> --sha <full-sha> --server-observability-url <collector-summary-url> --output artifacts/chaos.json
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
Before its first write-capable request, the runner calls the authenticated server-owned
`GET /api/certification/identity` endpoint. The endpoint must report an
`isolated-test` or `preproduction` deployment, the exact requested release SHA, and
`certification_write_enabled=true`. Missing/unauthorized identity, an unknown or production
environment, a SHA mismatch, or the production-default disabled flag fails closed before
conversation setup or any business write. The CLI environment marker is descriptive only and
is not trusted as proof of the remote target.

For the full campaign the same trusted preflight must advertise
`v2_certification_available=true`. Only an application started with both
`certification_write_enabled=true` and an `isolated-test|preproduction` deployment mounts
`POST /api/certification/v2-conversations`. That endpoint generates the conversation ID on
the server and persists runtime `v2`; it accepts no runtime selector. Normal production does
not mount the endpoint, and ordinary public new conversations still select v1. Load workers
read the persisted runtime from the certification response and public status response. PASS
also requires collector totals for actual v2 multi-step, multi-domain,
WAITING_CONFIRM/resume, official checkpoint persistence, and accepted-head publication;
payload wording alone is not evidence.
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
sync-session dependency boundary; client latency and OTel totals must be reconciled in load
evidence. Production Agent/Application Service UoWs can acquire sessions directly from the
shared factory, and readiness uses a separate async engine, so their checkout timeouts are not
included in `timeout_total`. `/ready` exposes this limitation explicitly; no all-path timeout
coverage is claimed and pool sizing is unchanged.

## Gate boundaries

- The real-model holdout expands to exactly 100 versioned cases and goes through the
  production DeepSeek/Fallback/Observed gateway plus `SupervisorPlanner`. Aggregates retain
  primary-provider attempts separately from logical fallback outcomes. Every case declares its
  allowed capabilities, forbidden capabilities, and read/write risk posture; any unexpected
  dangerous write is unsafe. PASS also requires the exact path and SHA-256 in the committed
  baseline approval manifest to be `APPROVED`; arbitrary JSON paths cannot become a comparison
  baseline. The committed manifest remains `PENDING` until human approval. Planner hard gates
  cover only configured-provider/SupervisorPlanner evidence and never claim mutation-level
  idempotency, approval atomicity, fence, or accepted-head evidence.
- The Memory gate extends the PR6 paired evaluator, raises precision to 0.80, measures
  retrieval p50/p95/p99, probes the configured external embedding in a dedicated `*_test`
  PostgreSQL database, and reports that result only as `EMBEDDING_PROVIDER_SMOKE` plus a
  current coverage snapshot. A fresh one-record 100% snapshot cannot pass the gate. PASS
  separately requires an approved maintenance-window ID/version whose exact-SHA summary
  matches window start/end, embedding model/version, eligible and ready counts, end coverage,
  backlog count/age, and reindex failures. Missing or mismatched window evidence is `NOT_RUN`.
- The chaos campaign has an explicit C1-C12 manifest. Each drill independently binds exact
  pytest nodes, execution status, durable DB, accepted-head, checkpoint, Memory assertions,
  and required telemetry. C8 requires both the lower-layer subprocess commit/replay case and
  the Agent confirmed-write delivery-loss recovery case. Full PASS requires every drill and
  its exact-window telemetry signal to pass. Every campaign uses a new bounded opaque
  `chaos_campaign_id`; the same ID is injected into fault-test processes, attached to PR7-A
  spans (never metric labels), stored in GateEvidence, and required by the collector query and
  response. Same-SHA signals carrying another ID cannot satisfy a drill. C12 additionally runs
  the authoritative Runner post-engine guard and proves a stale candidate cannot publish an
  accepted head or become Memory Writer input; the existing PostgreSQL stale-fence test remains
  the business-mutation assertion.
- The versioned adversarial manifest maps every case to exact pytest nodes, hard gates, and an
  expected safe invariant. Each case is executed and classified independently as PASS, FAIL,
  or NOT_RUN; a hard gate passes only when all mapped cases pass, and missing/unmapped required
  threats prevent full PASS. Duplicate HTTP and business-idempotency threats use their actual
  duplicate/replay tests rather than stale-fence coverage.

Long or credentialed gates are manual. Normal pull-request CI verifies schemas/CLIs, a
bounded load smoke, safe fault injection, deterministic adversarial cases, and the existing
zero-skip PostgreSQL suite.
