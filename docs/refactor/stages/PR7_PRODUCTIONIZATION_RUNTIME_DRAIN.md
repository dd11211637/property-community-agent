# PR7 Stage Contract — Productionization + Runtime Drain

## 1. Purpose and status

This document freezes the production migration, evidence, rollback, drain, and legacy
retirement contract for the final Agent refactor stage. It is a **Stage Contract only**.
It does not change production rollout, enable v2 traffic, add telemetry infrastructure,
run a canary, clean production data, or delete legacy code.

PR7 answers:

> Can the already-built v2 Agent safely become the production default, remain observable
> and recoverable, and eventually permit retirement of v1 without losing correctness?

The governing architecture is
[`../ARCHITECTURE_NORTH_STAR.md`](../ARCHITECTURE_NORTH_STAR.md). PR7 preserves the
accepted-head, runtime-pin, capability, approval, and Memory contracts established by
PR1–PR6; it does not introduce another Agent architecture.

**Status:** Stage Contract established; PR7 production implementation and rollout have
not started in this document.

## 2. Authoritative baseline

Repository facts were verified from `origin/main` at
`79abc26b5fd022026acbb99e6e3a31d1fb2578de`, the merge of PR #23. The final reviewed PR6
head was `8f655413a4e0533932acf46d3b71844e372bbdcd`. The post-merge backend, PostgreSQL,
frontend, and browser E2E Quality Gates all passed on that merge commit.

The PR6 implementation is present at this baseline, including the governed Memory API,
typed bounded Memory context, accepted-evidence Writer, provider-neutral embeddings,
pgvector storage/retrieval, conflict/provenance/retention policy, and bounded reindexing.

### 2.1 PR7 baseline alignment check

1. **Runtime versions:** exactly `v1` (custom `graph_core` via `LegacyGraphEngine`) and `v2`
   (official LangGraph Supervisor via `LangGraphEngine`).
2. **Selection:** `AgentRuntimeFacadeImpl` consults `RuntimeSelectionPolicy` only for new
   conversations; production constructs its default `enabled=False` policy.
3. **Persistence owner:** `agent_conversations.runtime_version` is the sole pin;
   `ConversationService.start()` writes it on insert and ignores later runtime arguments.
4. **Live switching:** existing `ACTIVE`, `WAITING_CONFIRM`, and `HANDOVER` use the pin; a
   pinned v2 with no v2 engine fails closed instead of falling back to v1.
5. **`WAITING_CONFIRM`:** confirmation resumes the pinned runtime and exact accepted pending
   cursor under fresh recovery gates; an ordinary v2 message only re-presents it.
6. **v2 eligibility:** ordinary production traffic has none; only explicitly injected
   test/internal `RuntimeSelectionPolicy(enabled=True)` instances select v2.
7. **Default:** public new conversations are pinned to v1, so public v2 is hard-zero.
8. **Controls:** no deployable rollout percentage, tenant eligibility, or rollout flag
   exists; `agent_concurrency_guard` and `otel_enabled` have different purposes.
9. **Stickiness:** current binary selection is deterministic and persistence is sticky, but
   no stable percentage-bucket assignment exists.
10. **New-traffic rollback:** selecting v1 is conceptually safe, but there is no audited
    rollout control to operate because production is already hard-zero v2.
11. **Pin safety:** policy is not re-evaluated for existing conversations; future rollout
    changes must preserve existing v1/v2 pins, as characterization tests already require.
12. **Legacy inventory:** one v1 runtime exposes six branches—controlled read, Repair,
    Billing, Announcement, Inspection, and general help—through `LegacyGraphEngine`, custom
    `graph_core`, the legacy root, seven node modules, and four shared-template subgraphs.
    Compatibility helpers are not extra runtime paths.
13. **v1 data identity:** `runtime_version='v1'` is canonical; lifecycle evidence also uses
    status, timestamps, community, and accepted-checkpoint pending state.
14. **Drain today:** grouped schema queries and pending-thread listing exist, but no
    production inventory, protected report, classifier, expiry workflow, or interlock does.
15. **Observability:** JSON access logs carry request/route/status/duration; Agent exposes one
    `agent.turn` span and four correctness counters; `/health` is liveness and `/ready`
    checks PostgreSQL plus assembly.
16. **Correlation:** request and Agent metadata exist, but no trace joins runtime, plan,
    step, capability, approval, accepted head, Memory, provider, and business mutation;
    production does not populate `RuntimeObservation`.
17. **Provider metrics:** absent despite bounded DeepSeek timeouts and one retry.
18. **Memory metrics:** absent despite typed degradation, logs, embedding state, and reindex
    backlog results.
19. **Checkpoint metrics:** conflict, stale-fence, and contention counters exist; accepted
    publish/latency, persistence latency, orphan, and cursor-anomaly metrics do not.
20. **Resilience evidence:** real PostgreSQL concurrency, restart/HITL, Agent, PR6 Memory,
    and browser tests exist; sustained load, capacity, bounded chaos, and a complete
    adversarial release suite do not. Test/support scripts are not production telemetry.
21. **Missing before v2 default:** deployable deterministic rollout/eligibility; unified
    telemetry/release metadata/SLOs/alerts; protected real-model and Memory gates; load,
    crash-window, chaos, adversarial, rollback, canary, drain, and retirement evidence.

### 2.2 Conflict determination

There is **no `ARCHITECTURE_CONFLICT`** and no `BLOCKING_BASELINE_DEFECT`. The missing
production controls are the intended PR7 implementation scope. Existing runtime pinning,
accepted-head ownership, Application Service authority, approval atomicity, fencing,
idempotency, and Memory authority boundaries remain suitable foundations.

## 3. Goal, sequence, and exclusions

The required sequence is:

```text
observe
  -> validate
  -> internal-only evidence
  -> canary eligible new conversations
  -> progressively increase eligible new traffic
  -> v2 default for eligible new conversations
  -> observe at 100%
  -> drain pinned v1 conversations
  -> verify zero live v1 dependency
  -> explicitly approve and retire v1
```

PR7 implementation MUST be split into bounded reviews. This contract does not authorize:

- a rollout percentage or default change;
- migration of a live or `WAITING_CONFIRM` conversation merely because a flag changed;
- shadow business mutation;
- deletion of v1 while it is live or still the approved rollback target;
- weakening approval, fencing, accepted-head, idempotency, or Memory safety;
- production cleanup bundled with executable-code retirement; or
- interpreting CI success as rollout or retirement approval.

## 4. Permanent runtime-pin invariants

`Conversation.runtime_version` remains the one authoritative pin.

- A v1 conversation stays v1 until terminal completion, approved expiry, or a separately
  proven explicit migration procedure.
- A v2 conversation stays v2.
- `ACTIVE`, `WAITING_CONFIRM`, and `HANDOVER` never switch due to percentage, eligibility,
  deployment, rollback, model output, request content, client input, checkpoint state, or
  Memory.
- Restart resolves the persisted pin and accepted application head before graph execution.
- A missing pinned engine fails closed or hands over according to reviewed policy; it never
  dispatches the other graph as a compatibility shortcut.
- One request has one authoritative runtime and at most one business mutation path.

An explicit migration protocol, if ever justified, is a separate architecture-sensitive
change. It must prove exact accepted-state/cursor conversion, pending-action binding,
approval/idempotency continuity, rollback, and no double execution. PR7 defaults to no live
migration.

## 5. Eligible-new-conversation assignment

Rollout applies only before the first runtime-dependent execution of an eligible new
conversation. The selected version is inserted with the conversation under the existing
lease/creation boundary.

### 5.1 Eligibility

A new conversation is eligible for v2 only when all server-owned conditions hold:

- the API surface is supported by v2;
- the deployment advertises a compatible schema and accepted-head implementation;
- the official LangGraph saver and authoritative PostgreSQL dependencies are ready;
- the approved model/provider/prompt configuration is available;
- the community/tenant is included by an auditable operational policy, if rollout is
  intentionally scoped; and
- no active emergency stop forbids new v2 assignment.

Eligibility MUST NOT use natural-language keywords, inferred intent, model output, client
roles/IDs, client-provided `runtime_version`, or Memory. Failure of eligibility selects the
configured safe new-conversation runtime and records a bounded reason code.

### 5.2 Deterministic percentage assignment

The implementation MUST use a stable, server-controlled bucket, conceptually:

```text
bucket = HMAC-SHA256(
    secret rollout salt version,
    trusted community_id + trusted actor_id + stable conversation_id
) mod 10_000

select v2 when eligible and bucket < rollout_basis_points
```

Equivalent deterministic hashing is allowed if the salt/config is server-controlled and
versioned. A client-visible conversation ID alone is insufficient because clients could
probe assignments. Random choice per request is forbidden.

The selection decision, rollout config version, and bounded eligibility reason are
observable, but the secret salt is not. Persisted `runtime_version` wins forever after
creation; changing salt or percentage affects only conversations that do not yet exist.

## 6. Rollout ladder and promotion gates

The default ladder is:

| Stage | Eligible new v2 traffic | Minimum evidence before promotion |
| --- | ---: | --- |
| R0 | 0% external; protected internal only | all offline/release gates; rollback drill |
| R1 | 5% | 24 hours and 200 eligible v2 turns |
| R2 | 25% | 48 additional hours and 1,000 cumulative v2 turns |
| R3 | 50% | 72 additional hours and 5,000 cumulative v2 turns |
| R4 | 100% | explicit approval after all prior gates |
| R5 | 100% observation | at least 14 days and 10,000 cumulative v2 turns before drain/rollback-target transition |

If real traffic cannot meet a sample floor, the stage remains pending unless an explicit
human decision accepts a documented alternative supported by production-shaped load and
holdout evidence. Time alone never promotes a stage.

Every promotion requires:

- the exact release/config/model/embedding/prompt versions recorded;
- availability and latency gates healthy for the whole observation window;
- quality non-regression against the frozen v1/baseline cohort;
- all correctness and safety hard gates at zero;
- no unresolved severity-1/2 incident or unexplained contention spike;
- protected real-model and Memory release gates passing; and
- explicit named approval.

Promotion is a deployment operation, not a code-path timer. The system MUST NOT
automatically increase rollout after a duration elapses.

## 7. Rollback and emergency behavior

### 7.1 Ordinary rollback

Rollback means:

- stop assigning v2 to new eligible conversations by setting the audited rollout to 0%;
- existing v1 remains v1;
- existing v2 remains v2 and uses the exact accepted v2 continuity path; and
- the rollback event records reason, config version, release SHA, approver, and time.

Rollback MUST NOT rewrite `runtime_version`, copy checkpoints between runtimes, or route
existing v2 conversations through `LegacyGraphEngine`.

### 7.2 Severe v2 incident

For provider outage, checkpoint corruption, accepted-head anomaly, Memory safety failure,
capability regression, approval regression, duplicate-write signal, or latency explosion:

1. freeze new v2 assignment;
2. preserve existing pins and accepted heads;
3. fail closed on authority/canonicality/scope uncertainty;
4. disable only reviewed non-authoritative optional features when their no-feature fallback
   is safe, such as retrieval or automatic Writer;
5. permit clarification/handover or a user-visible retryable failure for pinned v2 when
   correctness cannot be preserved; and
6. do not bypass policy, approval, fence, CAS, or idempotency to improve availability.

If v1 remains the approved new-traffic rollback target, its executable code cannot be
retired. Changing that recovery strategy requires separate evidence and approval.

## 8. Unified production observability

PR7 MUST use one OpenTelemetry-compatible trace/metric/log correlation model. JSON logs may
remain the log transport, but instrumentation must not create three unrelated identifier or
export stacks.

Current `agent.turn`/four-counter instrumentation is a seed, not completion. Production
composition MUST configure actual providers/exporters and report exporter health/degraded
state. In-memory counters and `NullTracer` are test/degraded behavior, not production
evidence.

### 8.1 Correlation model

One request SHOULD be traceable through bounded metadata:

- `request_id`, trace ID, and span ID;
- `conversation_id`, current `run_id`, and actual pinned `runtime_version`;
- release SHA and runtime/config/model/prompt versions;
- `plan_id`, `step_id`, specialist, and canonical capability when applicable;
- policy disposition and public reason code;
- safe approval reference correlation, never token/material;
- expected and published accepted-head versions plus exact-cursor status;
- Memory operation ID/kind/count/degradation reason, never raw content; and
- provider request class/model/result category.

High-cardinality IDs belong in traces/logs, not metric labels. Metric dimensions are bounded
to runtime, operation class, outcome/reason category, domain/capability from a finite
registry, provider class, and release/config versions.

### 8.2 Required spans/events

Instrumentation SHOULD provide these bounded spans or semantically equivalent events:

```text
agent.request
runtime.selection
runtime.start | runtime.resume
langgraph.invoke | langgraph.resume
supervisor.plan
supervisor.delegate
specialist.execute
capability.execute
approval.request | approval.consume
checkpoint.persist
accepted_head.publish
memory.retrieve | memory.write | memory.reindex
model.request
business.application_service
```

Instrumentation is placed at ownership boundaries, not every helper. Span status must
distinguish expected orchestration outcomes from infrastructure failure.

### 8.3 Data minimization

Telemetry MUST NOT emit raw prompts, chain-of-thought, private system instructions, full
messages, raw Memory content, capability payload/results, addresses, phone numbers,
credentials, confirmation tokens, approval material, idempotency keys, leases, or secret
rollout salt. Safe identifiers should be opaque or hashed where operationally sufficient.

## 9. Production Agent streaming contract

Streaming is presentation and delivery infrastructure. It MUST NOT become execution,
lifecycle, checkpoint, accepted-head, business-transaction, approval, or runtime-selection
authority. One request still has one pinned runtime, one authoritative execution, and one
business mutation path. HTTP, SSE, or WebSocket connection state is not execution truth,
and PR7 MUST NOT create a separate `StreamingRunner` or durable stream lifecycle.

### 9.1 Typed events and canonical lifecycle

The implementation defines one bounded typed stream-event union. Its exact wire names are
implementation scope, but it SHOULD represent equivalents of `TURN_STARTED`, `PROGRESS`,
`CLARIFICATION_REQUIRED`, `CONFIRMATION_REQUIRED`, `HANDOVER`, `COMPLETED`, and `FAILED`.
Lifecycle events derive only from canonical typed Agent/lifecycle state. Free-form model
text cannot establish `WAITING_CONFIRM`, completion, failure, approval, or handover.

Progress is explicitly provisional. For an operation that may mutate business state, an
authoritative final success/`COMPLETED` event MUST occur only after both:

1. the Application Service business outcome is durably committed; and
2. the application accepted turn/head is successfully published under PR1–PR6 contracts.

Internal LangGraph persistence is insufficient. If accepted-head publication fails, the
orphan state cannot emit success. A client must never be told a mutation succeeded and then
discover that either authoritative boundary failed.

### 9.2 Model text and privacy

Model token/text streaming, if supported, is untrusted provisional presentation. It MUST
NOT expose chain-of-thought, hidden reasoning, system/developer prompts, unintended raw
Memory, approval/confirmation material, credentials/secrets, or capability payloads/results
by default. High-risk/write text cannot claim authoritative completion before the durable
boundary. Event-level streaming is valid and may replace raw-token streaming when safer;
raw tokens are not a PR7 requirement.

### 9.3 Disconnect, cancellation, reconnect, and retry

A transport disconnect alone MUST NOT roll back a committed mutation, change a runtime pin,
invalidate accepted business state, or trigger a second execution on reconnect. Typed
cancellation may stop work only while cancellation remains legally and transactionally
possible; after irreversible commit it cannot pretend the mutation did not occur.

Reconnect/retry recovers canonical persisted conversation and accepted state using existing
conversation identity, accepted head, idempotency, approval binding, lease, and fence. It
MUST NOT restart a mutation merely because events were missed or add a streaming-specific
correctness store. Returning a current canonical snapshot/outcome is sufficient; replay of
every token is not required.

### 9.4 Backpressure and observability

Buffers and producer/consumer waits are bounded. A slow client cannot create unbounded
in-memory Agent state or indefinitely block authoritative transaction completion. An
implementation may coalesce/drop provisional progress or close a slow stream, provided the
canonical outcome remains recoverable. Exact limits and transport protocol are slice scope.

At minimum, telemetry records stream start, first event/first visible latency, completion,
client disconnect, reconnect/resume, buffer/backpressure failure, and final outcome. Traces
correlate safe `request_id`, `conversation_id`, `run_id`, pinned `runtime_version`, and
accepted version. High-cardinality IDs remain trace/log attributes, not metric labels.

### 9.5 Streaming implementation evidence

Future implementation tests MUST prove:

1. a read/simple turn streams successfully;
2. `WAITING_CONFIRM` emits a typed confirmation-required event;
3. a business write emits no final success before authoritative durability;
4. accepted-head publication failure emits no success from orphan state;
5. disconnect before completion permits safe canonical recovery;
6. disconnect after commit plus retry/reconnect does not duplicate the mutation;
7. reconnect restores canonical state without a runtime switch;
8. slow-client backpressure remains bounded; and
9. no chain-of-thought, unintended raw Memory, secret, or approval material leaks.

## 10. Metrics and SLO contract

All SLOs are measured separately by pinned runtime and operation class. Expected
`NEEDS_CLARIFICATION`, `PENDING_CONFIRMATION`, policy denial, and `HUMAN_ONLY` handover are
not infrastructure failures.

### 10.1 Availability indicators

At minimum export:

- Agent request and start/resume totals by structured outcome;
- new-conversation runtime assignment totals by v1/v2 and bounded selection reason;
- model request, timeout, retry, transport, schema, and provider failure totals;
- capability infrastructure failure totals separate from domain/policy outcomes;
- lease acquisition/contention/loss and stale-fence rejection totals;
- checkpoint persistence, accepted-head publication, CAS conflict, orphan, and exact-cursor
  resolution totals;
- approval request/consume/contention/rollback totals;
- Memory retrieval/degraded/safety-fail, Writer, embedding/index, and reindex totals; and
- database pool utilization/wait/timeout and authoritative business idempotency conflicts.

Initial promotion objectives, to be frozen with the R0 baseline, are:

- Agent infrastructure success rate at least 99.5% over each 24-hour window;
- runtime start/resume infrastructure success at least 99.5%;
- accepted-head publication and exact-cursor resolution at least 99.99%;
- capability infrastructure success at least 99.5%; and
- configured model structured-response success at least 98%, excluding explicit
  non-retryable policy/config rejection.

Thresholds MAY be tightened after baseline evidence. Weakening them requires an explicit
release decision and MUST NOT affect zero-tolerance correctness gates.

### 10.2 Latency indicators

Report p50, p95, and p99 for:

- simple/read interaction;
- multi-step Agent flow;
- initial turn ending in `WAITING_CONFIRM`;
- confirmation resume;
- model request by request class;
- capability call by bounded canonical capability;
- checkpoint persist/accepted-head publish; and
- Memory retrieval and Writer/index maintenance.

Before R1, freeze absolute ceilings from production-shaped R0 evidence. At every promotion,
v2 p95 MUST be no more than 20% slower than the comparable frozen v1/baseline cohort and
v2 p99 no more than 30% slower, unless an explicitly approved absolute product SLO is both
stricter and met. Timeout failures remain availability failures and cannot be hidden by
discarding slow samples.

### 10.3 Correctness and safety hard gates

The following are exact zero gates, not error-budget SLOs:

- unauthorized business mutation;
- cross-actor/community/house Memory leakage;
- approval/HITL bypass or wrong-action binding;
- duplicate committed business write;
- stale worker accepted after fence loss;
- runtime switch within a conversation;
- deleted Memory leakage or unsafe resurrection;
- accepted-head canonicality violation or orphan used as canonical;
- shadow business mutation; and
- model/client/Memory influence over trusted identity, scope, runtime, policy, or approval.

No availability, latency, task-completion, or rollout target may trade away these gates.
Any confirmed violation freezes promotion and new v2 assignment until incident closure.

## 11. Agent quality and real-model release gate

Graph completion is not quality. Establish a frozen pre-rollout baseline and compare v1/v2
where behavior is meaningfully comparable:

- task completion;
- clarification, handover, replan, and invalid-plan rates;
- specialist and user-visible failure rates;
- provider schema failure;
- capability call efficiency and unsupported behavior;
- Memory usefulness and incorrect influence; and
- approval outcomes and business mutation correctness.

Before R1, run the configured production model against a blind/holdout suite of at least
100 cases, with adequate representation of paraphrase, multi-domain tasks, negation,
distractors, conditions, context references, missing information, Memory relevance, stale
Memory, and adversarial authority claims. The suite must not expose expected labels to the
provider path.

The protected gate records model/provider identifier, prompt-contract version, provider
configuration version, release SHA, dataset version/hash, run time, and result. It requires:

- all authority/scope/approval/duplicate zero gates;
- at least 98% valid structured provider responses;
- no statistically or practically material regression in task completion, clarification,
  handover, or unsafe capability selection versus the frozen approved baseline; and
- human review of every failure category before promotion.

If CI cannot access credentials, this runs as a protected manual release gate. It MUST NOT
be replaced by deterministic fakes or claimed as executed without credentials.

A model, provider, prompt contract, or material provider configuration change is a
production change. It does not automatically require runtime `v3`, but it requires new
traceable release metadata and re-execution of the protected gate.

## 12. Memory production gate

Before R1 and at every broad promotion, verify:

- retrieval precision at least 0.80 and recall at least 0.75 on the approved suite;
- context efficiency at least 0.60;
- paired relevant personalization and clarification/task value do not regress from the
  approved PR6 baseline;
- incorrect influence, stale-business override, cross-scope leakage, deleted leakage,
  authority violation, and procedural-policy mutation remain zero;
- retrieval latency meets its frozen p95/p99 ceiling;
- Writer extraction/persistence error rate and embedding/index failure rate are measured
  and remain within the approved R0 budget;
- reindex backlog count/age are visible, and 99% of eligible active records reach the
  configured model/version within the approved maintenance window; and
- degradation reason and fallback behavior are observable.

Ordinary embedding/vector/Writer failure falls back to bounded structured or no-Memory
reasoning when scope-safe. A scope, canonical-integrity, privacy, deletion, or leakage
failure fails Memory closed and alerts. Memory never replaces a live Application Service.

## 13. Production-shaped load and contention validation

PR7-B MUST provide a repeatable preproduction load profile using production-equivalent
PostgreSQL/pgvector, LangGraph saver, application configuration, and representative model
latency or an explicitly bounded provider test contract.

It must exercise a weighted mix of:

- new conversation start;
- existing active conversation message and resume;
- `WAITING_CONFIRM` confirmation/cancel;
- multi-step/multi-domain v2 plans;
- concurrent independent and same-conversation requests;
- Memory structured and pgvector retrieval;
- checkpoint persistence and accepted-head publication;
- capability reads; and
- representative approved business writes with idempotent replay.

The sustained test runs at least 30 minutes at the documented expected peak concurrency;
the spike test runs at least 10 minutes at 2x expected peak. Promotion requires no hard-gate
violation, no unbounded queue/backlog, healthy pool headroom, and all frozen availability/
latency SLOs. Benchmarking `/health` alone is invalid.

Contention evidence must explain rates for lease acquisition/contention, fence rejection,
accepted-head CAS conflict, checkpoint/database-pool wait, Memory advisory lock contention,
approval consume contention, and business idempotency conflict. Correctness retries remain
visible; they are not relabeled as success-only traffic.

## 14. Chaos and crash-window drills

Each drill records injection point, expected user behavior, durable state, recovery path,
forbidden behavior, and observed evidence.

| Drill | Required outcome | Forbidden outcome |
| --- | --- | --- |
| Model timeout/sustained outage | bounded retry then safe failure/clarify/handover; freeze rollout if sustained | invented result or bypassed model-dependent validation |
| Malformed model response | schema failure metric; safe clarify/handover | best-effort execution of malformed plan |
| Embedding/vector outage | scoped structured/no-Memory fallback | unscoped retrieval or false READY status |
| PostgreSQL transient interruption | readiness false when authoritative DB unusable; safe retry/recovery | accepting writes without durable authority |
| Checkpoint persistence failure | accepted head unchanged | advancing accepted head |
| Accepted-head publish failure after internal checkpoint | internal checkpoint orphan only; zero Writer side effect | orphan used by normal/resume/restart path |
| Process death after internal checkpoint | recover from accepted app head/exact cursor | selecting latest orphan checkpoint |
| Business commit then process death before response | replay returns same committed resource | duplicate committed write |
| Confirmation around approval consume/business commit | same-UoW atomicity and exact binding | consumed approval without mutation or double execution |
| Memory indexing/Writer failure | business result remains committed; degradation visible | rollback business success or claim stored/indexed success |
| Approval consume race | one legal consume/mutation | two committed mutations |
| Lease loss/stale worker | stale worker aborts and fence rejects write | stale accepted checkpoint or business mutation |

These drills are bounded and run in isolated non-production environments unless a separately
approved production game day defines blast radius and recovery.

## 15. Adversarial release suite

The release suite MUST cover prompt injection, role/identity claims, community/house scope
override, approval claims, Memory-based authority claims, model-proposed trusted arguments,
capability-name manipulation, oversized/cyclic plans, budget exhaustion, repeated
confirmation, replay/duplicate requests, cross-tenant identifiers, stale/deleted Memory
resurrection, and attempts to select runtime.

Correct behavior may be clarification, safe read, handover, policy denial, or a bounded
public failure. It need not reject every request, but it must preserve trusted scope,
authority, accepted-head, approval, and exactly-once mutation boundaries.

## 16. Production configuration and feature controls

Configuration is server-owned, observable, and versioned/auditable. At minimum record:

- v2 new-conversation rollout basis points and assignment salt version;
- eligibility policy/version and approved fallback runtime;
- model/provider/model identifier, prompt contract, and provider config version;
- embedding provider/model/version;
- provider timeouts and retry ceilings;
- immutable plan budgets;
- telemetry exporter endpoint/config version;
- release SHA; and
- optional-feature degradation switches.

Clients, model output, Memory, request slots, and checkpoints cannot modify these values.

Avoid flag explosion. The initial operational control set SHOULD be limited to:

- `agent_v2_new_conversation_rollout_basis_points`;
- one auditable eligibility policy/config version;
- `memory_retrieval_enabled` only if a proven no-Memory fallback exists; and
- `automatic_memory_writer_enabled` as a non-authoritative kill switch.

Do not create per-specialist rollout flags. Correctness guards such as fencing,
accepted-head CAS, approval atomicity, and idempotency are not optional feature flags.
The validated retirement configuration MUST be rejected if any default, eligibility,
fallback, degraded, emergency, tenant/community, or API-surface path can still select v1.

## 17. Liveness, readiness, and alerting

Liveness means only that the process can respond. Readiness means the deployment can serve
the traffic its current rollout/config advertises.

Readiness MUST fail when an authoritative dependency such as PostgreSQL, required schema,
accepted-head store, or configured pinned-runtime engine is unusable. An optional embedding
or Writer outage MAY leave readiness green only when safe no-Memory/no-write degradation is
verified and exposed as a degraded component.

Actionable alerts include:

- availability or latency SLO burn;
- sustained model/provider or Memory degradation;
- database pool saturation;
- runtime-switch invariant violation;
- accepted-head/orphan/exact-cursor anomaly;
- authorization, approval, duplicate-write, fence, or Memory safety hard-gate violation;
- unexplained lease/CAS/advisory-lock contention; and
- v1 drain stalled beyond the approved schedule.

Individual expected retries do not page. Alerts aggregate by bounded reason and link to a
runbook with rollback/fail-closed actions.

## 18. Canary comparison and no-shadow-write rule

At R1–R3 compare v1 and v2 cohorts separately for success/task completion, clarification,
handover, replan, latency, infrastructure error, approval outcome, user-visible failure,
and business mutation correctness. Cohorts must be identified by persisted runtime, not by
request-time percentage.

The comparison is observational. It MUST NOT execute both runtimes for a live write.

```text
one user request -> one pinned runtime -> one capability/Application Service mutation path
```

Offline/replay comparison may use immutable snapshots or non-mutating read/evaluation
adapters that cannot consume approvals, idempotency, audit/outbox, or business writes.

## 19. v1 drain inventory and classification

PR7-E MUST add a protected production inventory using trusted database state. At minimum it
reports, grouped without routine user-identifying data:

- total conversations pinned v1;
- `ACTIVE`, `WAITING_CONFIRM`, `HANDOVER`, and `CLOSED` counts;
- derived terminal-completed, terminal-failed, expired, and abandoned-policy-candidate
  counts where the current schema lacks a direct status;
- oldest live v1 creation/last-activity age;
- counts by community/tenant identifier using protected operational access;
- pending accepted checkpoints and confirmation expiry status; and
- records whose classification is unknown/inconsistent.

The current schema alone does not distinguish every required terminal class. Implementation
MUST define one canonical classifier from conversation lifecycle, accepted checkpoint,
pending action, accepted outcome, and activity timestamps. Unknown is live, not zero.

Inventory queries are read-only until a separately reviewed expiry/cleanup action is
approved. The count must be reproducible at an exact release/database snapshot and expose
query/config version.

## 20. Drain state machine

Pinned v1 conversations are classified as:

```text
LIVE_ACTIVE
LIVE_WAITING_CONFIRM
LIVE_HANDOVER
TERMINAL_COMPLETED
TERMINAL_FAILED
EXPIRED
ABANDONED_CANDIDATE
UNKNOWN
```

Rules:

- `LIVE_*` and `UNKNOWN` retain executable v1.
- `WAITING_CONFIRM` continues on v1 until confirm/cancel, authoritative confirmation expiry,
  or a separately approved exact migration protocol. Rollout changes never migrate it.
- Inactivity alone cannot expire a pending approval/write.
- `ABANDONED_CANDIDATE` becomes `EXPIRED` only after the approved maximum inactive age,
  confirmation/pending checks, business-safe policy, audit evidence, and reversible or
  approved cleanup action.
- Terminal/expired classification does not immediately delete checkpoints or history.
- Drain actions are idempotent, bounded, observable, and stop on inconsistent state.

Prefer allowing pinned v1 to reach terminal state. A live migration is not required to
finish PR7 unless a real business deadline makes natural drain impossible and an explicit
protocol is separately approved.

## 21. Rollback-target transition and legacy retirement gate

The project must explicitly distinguish:

```text
phase A: v1 remains executable and is the new-traffic rollback target
phase B: v1 no longer receives new traffic but remains for live pins
phase C: an approved non-v1 recovery strategy replaces v1 rollback
phase D: static assignment interlock and stable dynamic zero are proven
phase E: zero resumable database dependency permits retirement review
```

Legacy executable code may be retired only when all are true:

1. 100% of eligible new conversations have used v2 through the approved R5 or explicit
   retirement observation window while representative new traffic remains admitted;
2. **static interlock:** production selectors, defaults, eligibility failures,
   tenant/community policy, unsupported API surfaces, dependency-degraded fallbacks, and
   emergency configuration are structurally/configurationally incapable of returning v1;
3. **dynamic interlock:** `new_v1_assignment_count == 0` throughout that observation window,
   not merely at one database snapshot;
4. **database interlock:** zero `LIVE_ACTIVE`, `LIVE_WAITING_CONFIRM`, `LIVE_HANDOVER`,
   `UNKNOWN`, or otherwise resumable v1 pin remains;
5. runtime-switch hard gate remains zero;
6. the approved rollback strategy no longer requires or configures v1 for new traffic;
7. old checkpoint/history retention or archival policy is approved;
8. production v2 SLOs, real-model, Memory, load, adversarial, and chaos gates pass;
9. rollback has been exercised successfully;
10. no unresolved severity blocker exists; and
11. explicit human approval authorizes retirement.

Static, dynamic, and database evidence are all mandatory. Snapshot
`live_v1_count == 0` alone is never sufficient because an assignment path could create a
new v1 pin on the next request. At retirement, v1 MUST NOT remain any configured fallback.
If a new request cannot safely use v2, it follows the separately reviewed non-v1 strategy:
explicit safe failure, handover, temporary admission stop, or another approved v2 recovery
path. It MUST NOT silently dispatch retired v1.

After retirement, no request may recreate `runtime_version='v1'`, import/dispatch
`LegacyGraphEngine`, revive legacy code, or switch an existing runtime. Archived historical
code/data is not an executable production runtime and cannot be on any import/dispatch path.

CI green is necessary but never sufficient approval.

## 22. Code retirement and data cleanup

Legacy code deletion is a separate bounded review after the gate above. “Removed or formally
retired” means zero executable production import/dispatch dependency and zero possibility
of assigning a new conversation to v1. Static selector/fallback scans, stable dynamic-zero
evidence, and database inventory must all pass before custom runtime and v1-only paths go.

Do not combine executable retirement with telemetry, canary control, load harness, or
large data cleanup. Do not drop `runtime_version`, legacy columns, checkpoints, or history
merely because code became unused. Data retention/archival/deletion is a later separately
approved, auditable operation that preserves the rollback and compliance window.

## 23. Implementation slices

PR7 SHOULD proceed as multiple bounded PRs or equivalently reviewable commits:

- **PR7-A — Observability, streaming, and SLO instrumentation:** exporters, correlation,
  spans, typed presentation events, durability-gated final outcomes, bounded backpressure,
  reconnect/disconnect semantics, metrics, readiness, dashboards/alerts, baseline evidence.
- **PR7-B — Production evaluation and resilience gates:** protected real-model holdout,
  Memory gate, production-shaped load, chaos/crash-window drills, adversarial suite.
- **PR7-C — Canary controls:** server-owned eligibility, deterministic sticky percentage,
  config audit, promotion/rollback controls; default remains 0% until approval.
- **PR7-D — Progressive/default rollout:** approved R1–R5 operations and durable evidence;
  no code deletion.
- **PR7-E — v1 drain tooling/reporting:** read-only inventory first, then separately
  approved bounded expiry/drain actions.
- **PR7-F — Legacy retirement:** rollback-target transition approval; static no-v1
  assignment/fallback proof; stable `new_v1_assignment_count == 0`; zero resumable database
  pins; bounded code removal; non-v1 degraded/admission behavior; post-retirement regression.
  Data cleanup remains separate.

No slice may create a second lifecycle, business mutation, telemetry identity, or runtime
pin owner.

## 24. Durable release evidence

Every promotion, rollback, drain action, and retirement decision records:

- release commit/image digest and deployment environment;
- runtime rollout/eligibility config version and percentage;
- model/provider/prompt and embedding configuration;
- evaluation dataset/result identifiers;
- SLO observation window, sample size, dashboards, and incidents;
- hard-gate results;
- exact v1 drain inventory plus static no-v1 assignment proof and dynamic
  `new_v1_assignment_count` observation where relevant;
- decision, reason, time, and approver; and
- rollback/recovery result.

CI artifacts and deployment metadata may satisfy this without a new release database if
they are immutable, discoverable, and tied to the exact release.

## 25. PR7 implementation test/evidence matrix

| Area | Required implementation evidence |
| --- | --- |
| Runtime pin | Existing v1/v2 and `WAITING_CONFIRM` retain their pin through rollout, rollback, restart, confirmation, and engine degradation; concurrent creation converges on one server-owned pin; untrusted inputs cannot select it. |
| Rollout | Stable cross-process buckets pass 0/5/25/50/100 boundary tests; salt/config and ineligibility behavior affect only new conversations; promotion requires evidence and approval. |
| Rollback | Zero rollout changes only new eligible traffic; pinned v1/v2 continue from the exact accepted cursor; no pin/checkpoint rewrite or correctness bypass occurs. |
| Observability/privacy | Correlation spans the required execution chain with bounded labels; exporter degradation is visible; expected outcomes are classified correctly; prohibited raw or secret data is absent. |
| Streaming | Typed canonical events cover simple read and `WAITING_CONFIRM`; writes cannot complete before durable business plus accepted-head publication; orphan publication failure cannot emit success; disconnect/reconnect preserves canonical state and idempotency; slow-client resources are bounded; privacy gates pass. |
| SLO/quality/model | Availability and p50/p95/p99 calculations, hard-gate freezes, persisted-runtime cohort comparison, protected configured-model holdout, and explicit `NOT RUN` behavior are tested. |
| Memory | Degradation and fail-closed behavior, zero leakage gates, Writer/index/reindex metrics, and authority-neutral kill switches are verified. |
| Load/contention | The production-shaped mix meets frozen sustained/spike targets; same-conversation correctness holds; pool, lease, CAS, lock, approval, and idempotency contention is explained. |
| Chaos/adversarial | Every drill and critical crash window proves durable/recovery/forbidden outcomes; attacks fail safely; duplicate writes, orphan canonicality, and shadow mutation remain zero. |
| Drain/retirement | Inventory classifications and counts are reproducible; pending v1 is never inactivity-expired; static selectors/fallbacks cannot yield v1, dynamic new-v1 assignment remains zero under representative traffic, and database resumable pins are zero; post-retirement behavior cannot assign, import, or dispatch v1. |
| Full regression | Each slice runs proportional focused tests plus Ruff lint/format, structure, compile/import, `pip check`, OpenAPI drift, full backend, Agent/value gates, real PostgreSQL/pgvector zero-skip, frontend lint/test/build, Compose Playwright, and remote Quality Gates. Retirement reruns the full matrix after deletion. |

## 26. Final exit criteria

PR7 and the Agent refactor are complete only when:

1. v2 is the persisted default for 100% of eligible new conversations;
2. no conversation switched runtime mid-lifecycle;
3. production telemetry covers critical execution paths with safe correlation;
4. typed production streaming obeys durability, recovery, backpressure, and privacy gates;
5. production availability, latency, quality, and safety evidence passes;
6. the configured real-model holdout and Memory production gates pass;
7. production-shaped load, contention, adversarial, chaos, and crash-window gates pass;
8. rollback has been exercised without correctness loss;
9. no duplicate business write or authority regression is observed;
10. static assignment/fallback paths cannot return v1 and dynamic new-v1 assignment stays
    zero through the approved representative-traffic observation window;
11. zero live, waiting, handover, unknown, or otherwise resumable v1 pin remains;
12. v1 is neither required nor configured by the approved rollback strategy;
13. legacy retirement has explicit human approval;
14. legacy executable code has zero production dispatch/import and zero new-assignment
    possibility after bounded removal or formal retirement;
15. post-retirement requests that cannot use v2 follow the approved non-v1 strategy;
16. post-retirement local, PostgreSQL, frontend, browser, and remote gates pass; and
17. the Roadmap records PR1–PR7 as **DONE / MERGED / VERIFIED**.

Only then may project status become `REFACTOR_DONE_MERGED_VERIFIED`.

## 27. Stage Contract self-review

Before accepting this contract, reviewers confirm: rollout targets only eligible new
conversations; all existing and `WAITING_CONFIRM` pins remain immutable; assignment is
server-owned, deterministic, sticky, and persisted before execution; no untrusted runtime
authority, duplicate streaming lifecycle, or shadow mutation exists; promotion requires
evidence plus approval; safety cannot be traded for availability; telemetry excludes
prohibited data; real-model evidence cannot be faked; final streamed success follows durable
business and accepted publication;
unknown and pending v1 remain live; static, dynamic, and database interlocks all pass before
v1 retirement; code retirement is separate from data cleanup; CI is not retirement
approval; and this Stage Contract changes documentation only.

## 28. Document authority and conflict handling

Architecture guidance is interpreted in this order:

1. current repository and production facts;
2. [`../ARCHITECTURE_NORTH_STAR.md`](../ARCHITECTURE_NORTH_STAR.md);
3. [`../REFACTOR_ROADMAP.md`](../REFACTOR_ROADMAP.md);
4. this Stage Contract; and
5. historical reports.

A production fact that changes before implementation must be re-baselined and recorded.
Changing business authority, Capability Registry ownership, `RuntimeContext` trust,
LangGraph/accepted-head responsibility, runtime-pin ownership, or Memory authority requires
an explicit ADR. PR7 MUST NOT make such a change implicitly.
