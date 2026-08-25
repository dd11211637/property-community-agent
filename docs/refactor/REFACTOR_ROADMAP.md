# Agent Refactor Roadmap

## 1. Purpose

This roadmap defines the ordered migration from the current Agent runtime to the
architecture in [`ARCHITECTURE_NORTH_STAR.md`](ARCHITECTURE_NORTH_STAR.md). Each stage
must deliver a reviewable vertical slice while preserving compatibility and the business
authority boundary. Stage detail MAY evolve from verified results, but the North Star
MUST NOT change silently.

Stages MUST proceed in order. A later stage MAY be researched, but its production scope
MUST NOT be pulled into an earlier stage merely because it is convenient.

## 2. Stage summary

| Stage | Focus | Status |
| --- | --- | --- |
| PR1 | Correctness substrate | **DONE / MERGED / VERIFIED** |
| PR2 | Capability Layer | **DONE / MERGED / VERIFIED** |
| PR3 | Typed State and Runner de-domainization | **DONE / MERGED / VERIFIED** |
| PR4 | LangGraph runtime foundation | **DONE / MERGED / VERIFIED** |
| PR5 | Supervisor and stateless specialists | **DONE / MERGED / VERIFIED** |
| PR6 | Long-term memory | **DONE / MERGED / VERIFIED** |
| PR7 | Productionization and runtime drain | Planned (Stage Contract established) |

## 3. PR1 — Correctness substrate

**Status:** **DONE / MERGED / VERIFIED**

**Baseline:** `25d3b628f54b698a21a3a1e1246fdf27b314cc78`

### Goal

Establish the concurrency, approval, and lifecycle correctness required before structural
Agent refactoring.

### Main deliverables

- single-writer conversation execution;
- checkpoint and memory compare-and-swap behavior;
- run lease, heartbeat, fencing, and stale-worker rejection;
- approval atomicity and `CLOSED` terminal lifecycle;
- corrected production composition;
- PostgreSQL concurrency coverage and zero-skip PostgreSQL CI; and
- browser regression remediation.

### Explicit exclusions

- Capability Layer;
- typed RuntimeContext/AgentState migration;
- LangGraph replacement;
- Supervisor/specialist architecture; and
- long-term vector memory.

### Exit criteria

- merged to `main` at the baseline above;
- post-merge backend, PostgreSQL, frontend, and browser E2E quality gates passed; and
- P0 remains closed unless a real correctness regression is discovered.

## 4. PR2 — Capability Layer

**Status:** **DONE / MERGED / VERIFIED**

**Final PR head:** `3d11a163c557aaa4768b6eeb41fd3b4fee0bb9f3`

**Merged to `main`:** `6b567f5a223ec4e296c1b97d4a73f9cb23f56dc5`

**Post-merge verification:** GitHub Quality Gates run
[`32576252159`](https://github.com/dd11211637/property-community-agent/actions/runs/32576252159)
passed backend, PostgreSQL, frontend, and browser E2E jobs on the merge commit.

### Goal

Answer “What can the Agent do, and through which stable contract may orchestration request
it?”

### Main deliverables

- `CapabilitySpec`, `CapabilityRegistry`, `CapabilityPolicy`, and `CapabilityExecutor`;
- typed capability input/output;
- repair and billing capability adapters;
- compatibility for migrated legacy tool metadata; and
- at least one real read and one real write path from orchestration through the
  Capability Layer to an existing Application Service.

### Explicit exclusions

- LangGraph runtime work;
- Supervisor or specialist-agent architecture;
- full typed AgentState migration;
- long-term memory;
- large Runner rewrite; and
- Application Service, RBAC, approval, or P0 correctness rewrites.

### Exit criteria

- the detailed criteria in
  [`stages/PR2_CAPABILITY_LAYER.md`](stages/PR2_CAPABILITY_LAYER.md) pass;
- migrated static capability metadata has one authoritative registry representation;
- trusted identity/scope cannot be overridden by model arguments;
- business writes still execute only through Application Services; and
- legacy runtime compatibility and all required quality gates remain green.

## 5. PR3 — Typed State and Runner de-domainization

**Status:** **DONE / MERGED / VERIFIED**

**Final PR head:** `f068cc7d3116df2b7cd1238d496d7399f3d18ff6`

**Merged to `main`:** `70eca8523d2f2db4f153e731e89ebde08b4eff18`

**Post-merge baseline for PR4:** `70eca8523d2f2db4f153e731e89ebde08b4eff18`

### Goal

Answer “What does the Agent currently know, which facts are trusted, and where does
mutable orchestration/domain working state belong?”

### Main deliverables

- typed trusted `RuntimeContext`;
- typed mutable `AgentState` and typed domain working state;
- announcement and inspection capability migration;
- domain continuation logic removed from the Runner; and
- explicit adapters for legacy state compatibility.

### Explicit exclusions

- LangGraph root runtime;
- Supervisor and multi-specialist collaboration;
- long-term vector memory; and
- retirement of runtime-pinned legacy conversations.

### Exit criteria

- trust and mutable-state boundaries are type-enforced and contract-tested;
- all target business domains invoke Application Services through capability contracts;
- the Runner no longer owns domain working-state policy or domain continuation logic;
- legacy pending/confirmation conversations remain resumable; and
- correctness and compatibility gates pass.

## 6. PR4 — LangGraph runtime foundation

**Status:** **DONE / MERGED / VERIFIED**

**Final PR head:** `d289b13f5bfb34dc95341342203f596ae5e48449`

**Merged to `main`:** `4e169e10475a84c451f8c7a78560628cfe8210a7`

**Post-merge verification:** backend, PostgreSQL, frontend, and browser E2E Quality Gates
passed on the merge commit.

### Goal

Prove that a real conversation can execute, interrupt, persist, resume, and recover
through LangGraph without changing business authority.

### Main deliverables

- LangGraph root graph;
- durable PostgreSQL-backed checkpoint integration;
- API compatibility adapter (an `AgentRuntimeFacade` above a shared lifecycle owner and a
  `GraphEngine` split, per the PR4 Stage Contract);
- exactly one pilot specialist (Repair); the Supervisor and the complete four-specialist
  topology remain PR5;
- interrupt/resume and checkpoint recovery; and
- runtime feature flag plus conversation-level runtime pinning.

### Explicit exclusions

- complete Supervisor/specialist topology;
- broad multi-domain replanning;
- long-term memory; and
- immediate legacy runtime retirement.

### Exit criteria

- the new-runtime vertical slice survives restart and resume tests;
- resume revalidates identity, scope, lease/fence, approval, expiry, binding, and
  idempotency at authoritative boundaries;
- new and legacy runtimes can coexist without shadow double-write;
- rollback and runtime pinning are demonstrated; and
- all required quality gates pass.

## 7. PR5 — Supervisor and stateless specialists

**Status:** **DONE / MERGED / VERIFIED**

**Final PR head:** `38f3d1ac68ae5183eca6b990595af1dada32fbb6`

**Merged to `main`:** `635da922094c84cc2c3219d00e1f5c960fb9ef92`

**Stage Contract:**
[`stages/PR5_SUPERVISOR_SPECIALISTS.md`](stages/PR5_SUPERVISOR_SPECIALISTS.md)

### Goal

Introduce governed multi-domain routing, replanning, specialist delegation, and HITL on
the durable runtime.

### Main deliverables

- Supervisor;
- Repair, Billing, Announcement, and Inspection specialists;
- deterministic routing constraints and execution budgets;
- replanning and multi-domain collaboration; and
- human-in-the-loop handover and resumption.

### Explicit exclusions

- specialist-owned persistence or business rules;
- autonomous authorization or approval;
- long-term memory authority; and
- forced migration of pinned legacy conversations.

### Exit criteria

- specialists are stateless; their business/domain actions use registered capabilities,
  while governed orchestration infrastructure uses dedicated interfaces;
- Supervisor plans and replans within budgets and policy limits;
- deterministic business and security decisions remain authoritative outside the LLM;
- HITL and recovery behavior are contract-tested; and
- multi-domain acceptance and regression gates pass.

## 8. PR6 — Long-term memory

**Status:** **DONE / MERGED / VERIFIED**

**Final PR head:** `8f655413a4e0533932acf46d3b71844e372bbdcd`

**Merged to `main`:** `79abc26b5fd022026acbb99e6e3a31d1fb2578de`

**Post-merge verification:** backend, PostgreSQL, frontend, and browser E2E Quality Gates
passed on the merge commit.

**Stage Contract:**
[`stages/PR6_LONG_TERM_MEMORY.md`](stages/PR6_LONG_TERM_MEMORY.md)

### Goal

Add durable retrieval context that improves reasoning without creating a parallel source
of business truth.

### Main deliverables

- PostgreSQL memory schema and pgvector integration;
- semantic and episodic memory;
- procedural candidates;
- hybrid retrieval;
- governed Memory Writer; and
- conflict, provenance, retention, and deletion handling.

### Explicit exclusions

- memory-based authorization, approval, or scope;
- replacing Application Service queries with remembered facts;
- silent promotion of procedural candidates into policy; and
- premature legacy runtime retirement.

### Exit criteria

- memory retrieval is tenant/scope-safe and provenance-aware;
- stale or conflicting memory loses to authoritative business state;
- write, retention, deletion, and conflict policies are tested;
- measurable evaluation shows justified reasoning value; and
- security, privacy, and regression gates pass.

## 9. PR7 — Productionization and runtime drain

**Status:** Planned (Stage Contract established)

**Stage Contract:**
[`stages/PR7_PRODUCTIONIZATION_RUNTIME_DRAIN.md`](stages/PR7_PRODUCTIONIZATION_RUNTIME_DRAIN.md)

### Goal

Make the new runtime observable, measurable, resilient, progressively deployable, and
safe to operate as the default.

### Main deliverables

- production streaming and OpenTelemetry;
- deterministic evaluation and justified LLM evaluation;
- load and concurrency validation;
- adversarial, chaos, and failure-drill coverage;
- canary rollout and rollback controls; and
- pinned-conversation drain and legacy retirement procedures.

### Explicit exclusions

- weakening business authority or correctness for rollout speed;
- unpinned mid-conversation runtime switching; and
- retiring legacy runtime while live pinned conversations remain unsafe to drain.

### Exit criteria

- 100% of eligible new conversations can use the LangGraph runtime under controlled
  rollout;
- production SLOs, observability, evaluation, load, adversarial, and chaos gates pass;
- rollback is exercised without correctness loss;
- all legacy-pinned conversations have completed, expired, or been safely drained; and
- legacy runtime retirement is explicitly approved and verified.

## 10. Cross-stage controls

Every stage MUST:

- preserve the North Star business authority, trust, memory, and safety boundaries;
- preserve runtime pinning and compatibility until drain criteria are met;
- separate verified current facts from target-state statements;
- define testable exit evidence before claiming completion; and
- stop and record `ARCHITECTURE_CONFLICT` if its scope contradicts the North Star.

Architecture guidance is interpreted in this order:

1. current repository and production facts;
2. [`ARCHITECTURE_NORTH_STAR.md`](ARCHITECTURE_NORTH_STAR.md);
3. this roadmap;
4. the current stage document; and
5. historical reports.

Changing the business authority boundary, Capability Registry ownership,
`RuntimeContext` trust model, LangGraph responsibility, or memory authority model
requires an explicit ADR. An ordinary feature PR MUST NOT make such a change implicitly.
