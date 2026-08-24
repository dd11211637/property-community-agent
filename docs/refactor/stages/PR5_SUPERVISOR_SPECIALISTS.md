# PR5 Stage Contract — Supervisor + Stateless Specialists

## 1. Purpose and status

This document freezes the permitted architecture, implementation scope, and testable exit
criteria for PR5. It is a **Stage Contract only**. It does not implement a Supervisor or
specialist, change runtime behavior, or prove that the PR5 target already exists.

PR5 answers:

> How does the durable v2 runtime become a governed, behavior-complete multi-domain runtime
> without giving the model, Supervisor, or specialists business authority?

The governing destination is
[`../ARCHITECTURE_NORTH_STAR.md`](../ARCHITECTURE_NORTH_STAR.md), the ordered migration
context is [`../REFACTOR_ROADMAP.md`](../REFACTOR_ROADMAP.md), and the durable foundation is
[`PR4_LANGGRAPH_RUNTIME.md`](PR4_LANGGRAPH_RUNTIME.md).

**Status:** Stage Contract established; PR5 production implementation has not started in
this document.

## 2. Verified starting point

These facts were verified from `main` at
`4e169e10475a84c451f8c7a78560628cfe8210a7` (merge of PR #19). PR4's final reviewed head
was `d289b13f5bfb34dc95341342203f596ae5e48449`.

1. v2 is an official LangGraph `StateGraph`, not the custom v1 `graph_core` under another
   name.
2. v2 uses the official PostgreSQL `PostgresSaver` with synchronously durable checkpoints.
3. `agent_checkpoints` separately owns the application accepted head, typed `AgentState`,
   exact LangGraph cursor, and first/subsequent publication CAS.
4. `AgentRuntimeFacadeImpl` is the API-facing facade. `AgentSessionRunner` remains the
   shared lifecycle/correctness owner for both graph engines.
5. `Conversation.runtime_version` is the sole persisted runtime pin. The default production
   selection policy keeps public/general v2 selection at hard zero.
6. A v2-pinned conversation never falls back to the v1 engine. If its v2 engine is
   unavailable, execution fails closed.
7. The only v2 specialist is `RepairPilotSpecialist`. It is stateless and invokes
   `repair_list`, `repair_get`, and `repair_create` through `CapabilityExecutor`.
8. The current v2 graph rejects non-Repair domains with `UNSUPPORTED_PILOT_DOMAIN`.
9. No Supervisor, `BillingSpecialist`, `AnnouncementSpecialist`, or `InspectionSpecialist`
   exists at this baseline.
10. The v1 custom graph and its four domain subgraphs remain available for pinned v1
    conversations. Their compatibility tools already route business calls through the
    Capability Layer, while retaining v1 selection, continuation, confirmation, and
    presentation behavior.
11. The canonical registry contains 24 capability specifications and two compatibility
    aliases. Section 10 records their exact current names and contracts.
12. The v2 Repair restart/interrupt/confirm/cancel vertical has real PostgreSQL test
    evidence, including exact accepted cursor resolution, one atomic approved mutation,
    replay rejection, and cancel with zero mutation.

These facts are the starting point, not evidence that the PR5 topology or behavior is
already implemented.

## 3. PR5 objective

PR5 MUST transform PR4's single-domain pilot into a governed multi-domain Supervisor
runtime while preserving business authority and the complete P0 correctness substrate.

The implementation target is:

```text
official LangGraph Supervisor
  -> bounded typed plan
  -> one selected stateless specialist at a time
  -> domain-scoped canonical capability
  -> CapabilityPolicy
  -> CapabilityExecutor
  -> typed adapter
  -> existing Application Service / Domain Rules / RBAC / Approval / UoW
  -> audit / outbox / commit
```

## 4. Scope

### 4.1 In scope

- one official LangGraph Supervisor;
- one canonical stateless specialist for each current domain: Repair, Billing,
  Announcement, and Inspection;
- convergence of `RepairPilotSpecialist` into the canonical Repair specialist;
- typed, bounded plan construction and deterministic plan validation;
- domain routing, specialist delegation, bounded replanning, and sequential multi-domain
  collaboration;
- clarification, safe general help, result synthesis, handover, and HITL resume;
- execution-policy ceilings and checkpointed progress counters;
- removal of `UNSUPPORTED_PILOT_DOMAIN` for every currently supported public domain;
- v2 functional completeness for current public Agent behavior; and
- an explicit decision that v2 is functionally rollout-eligible when section 27 passes,
  without making v2 the production default.

### 4.2 Out of scope

PR5 MUST NOT introduce:

- semantic, episodic, or procedural long-term memory;
- pgvector, a Memory Writer, autonomous policy learning, or memory-derived authority;
- specialist-owned persistence, repositories, database sessions, Unit of Work, or business
  state;
- Supervisor-owned business rules, RBAC, approval, transaction, or domain transitions;
- forced migration, drain, deletion, or replacement of pinned v1 conversations/runtime;
- 100% default v2 rollout, canary automation, production load/chaos programme, or final
  runtime drain; or
- a public API redesign unrelated to a separately justified compatibility defect.

PR6 owns long-term memory. PR7 owns progressive/default rollout and runtime drain.

## 5. Supervisor responsibility

The Supervisor owns only orchestration:

- intent/domain interpretation and deterministic routing validation;
- bounded plan construction and current-step selection;
- selection and sequencing of specialists;
- evaluation of structured specialist results;
- legal replanning within immutable limits;
- result synthesis, clarification, safe fallback, and escalation;
- orchestration budget accounting; and
- structured decision metadata for observability.

The Supervisor MUST NOT:

- choose or widen actor, role, tenant, community, house, execution source, lease, fence, or
  runtime version;
- grant authorization, declare approval, consume approval, or treat confirmation as a
  business mutation;
- implement a domain state machine or make a live business transition authoritative;
- call an Application Service for a business action except through the Capability Layer;
- own ORM, repository, SQL, UoW, commit, rollback, checkpoint publication, or accepted-head
  publication;
- expose every capability to every specialist; or
- treat model confidence, a plan, `AgentState`, cached observations, or future memory as
  business truth.

## 6. Common specialist contract

PR5 SHOULD define a small shared protocol or composition helper, not four copied execution
engines and not a domain-policy god class.

A specialist invocation conceptually receives:

- the minimum semantic projection of `AgentState` needed for its current step;
- a read-only projection of fresh trusted `RuntimeContext`;
- the current typed `PlanStep`;
- its exact canonical capability allowlist; and
- bounded prior orchestration results needed by that step.

A structured result conceptually contains one of:

- a user-facing response/result;
- a proposed typed capability invocation;
- a clarification request;
- a replan or handover signal; or
- a normalized typed error.

Every specialist MUST be stateless. It MUST NOT own a database session, repository, UoW,
checkpointer, conversation store, graph persistence, cross-request mutable authority,
RuntimeContext, RBAC, approval truth, or business facts. It MUST NOT directly mutate an
authoritative `AgentState` outside the graph's typed update contract. Every business action
MUST go through `CapabilityExecutor`.

Specialists MUST NOT call each other. All delegation returns through the Supervisor.

## 7. Canonical topology and sequential execution

The required ownership topology is:

```text
START
  -> Supervisor validates/creates bounded plan
  -> Supervisor selects next eligible step
  -> exactly one of:
       RepairSpecialist
       BillingSpecialist
       AnnouncementSpecialist
       InspectionSpecialist
  -> Supervisor validates structured result
       -> finish | clarify | HITL | handover | replan | next step
  -> END
```

PR5 does not require parallel specialist execution. Production PR5 MUST execute specialist
steps sequentially. Parallel fan-out/fan-in is forbidden unless a later contract defines
ordering, budget, accepted-head, cancellation, and failure semantics without adding a second
correctness owner.

## 8. Typed bounded planning model

PR5 MUST define a typed schema validated before execution. A suitable conceptual model is:

```text
Plan
  plan_id
  objective
  objective_classification  # single-domain | multi-domain | general-help | uncertain
  steps: tuple[PlanStep, ...]
  status
  current_step_id

PlanStep
  step_id
  domain
  specialist
  goal
  dependencies
  status
  allowed_capability_hint?  # proposal only
  result_reference?
  budget_consumption
```

Plan and step identifiers are orchestration correlation. A plan MUST NOT contain trusted
identity, roles, community/house authority, execution source, runtime selection, lease/fence,
approval truth, confirmation token, or transaction authority.

The plan is mutable orchestration state, not business truth. It MAY refer to bounded prior
results, but any live business fact needed for a later action MUST be re-read through a
capability/Application Service. Live authoritative state wins over prior narrative.

Top-level objective classification and step-local execution routing are distinct. The
top-level objective MAY be single-domain, multi-domain, general help, or uncertain. PR5 does
not require changing the existing `Intent` enum to represent that classification. Global
intent/objective is orchestration context only; it is not per-step execution authority.

Each executable `PlanStep` MUST carry, or allow deterministic derivation of, its `domain`,
`specialist`, and optional capability hint/proposal. The step-local tuple is validated before
delegation as described in section 11. A multi-domain plan therefore does not require every
step to agree with one global `AgentState.intent`.

Malformed, oversized, cyclic, dependency-invalid, unknown-specialist, or policy-invalid
plans MUST fail closed to clarification, a safe public error, or handover. They MUST NOT be
executed by best-effort interpretation.

## 9. Model autonomy versus deterministic authority

Model-assisted typed planning is allowed. A completely free-form plan is not.

| Decision | Model MAY propose | Deterministic/policy/authoritative owner MUST decide |
| --- | --- | --- |
| Intent/domain | yes | schema and supported-domain validation |
| Specialist | yes | existence, domain allowlist, current-step legality |
| Plan and step order | yes | dependency validity and immutable bounds |
| Clarification/replan | yes | legal outcome and remaining budget |
| Summary | yes | presentation/safety filter; never business proof |
| Runtime version | no | persisted `Conversation.runtime_version` |
| Actor/role/community/house | no | fresh trusted server context and protected services |
| Capability existence | no | canonical `CapabilityRegistry` |
| Specialist capability allowlist | no | server execution policy |
| Risk floor and HITL posture | no | `CapabilitySpec` + deterministic `CapabilityPolicy` |
| Step/call/replan/deadline maxima | no | immutable server execution policy |
| Duplicate-call protection | no | deterministic fingerprint/progress guard |
| Business authorization/state transition | no | Application Service/domain rules/RBAC |
| Approval validation/consume | no | Application Service/UoW transaction |
| Commit/rollback | no | UoW |

Model output MUST pass structured schema validation. A model suggestion never makes the
plan, specialist, capability, risk, approval, scope, or outcome authoritative.

## 10. Canonical capability inventory and allowlists

This inventory is verified at the PR5 baseline. Names are canonical registry identities;
aliases do not create another active execution path. Every specialist receives only its
domain allowlist.

### 10.1 RepairSpecialist

| Capability | Kind / posture | Typed input | Typed output | Existing service owner |
| --- | --- | --- | --- | --- |
| `repair_list` | read / none | `statuses`, `limit` | count + work-order briefs | `WorkOrderService` |
| `repair_get` | read / none | `work_order_id` | work order + timeline | `WorkOrderService` |
| `repair_create` | write-low-risk / policy HITL | `description`, `location`, `urgency` | work order + idempotency key | `WorkOrderService` |

The specialist reasons about repair intent, read/create selection, and missing semantic
input. It MAY use the trusted current house supplied by runtime; it MUST NOT accept a model
house as authority. It MUST reuse deterministic repair category/urgency normalization and
must not duplicate `WorkOrderService` rules.

PR5 MUST replace the pilot with this canonical interface, preserving the proven v2 Repair
read and restart/HITL vertical. `RepairPilotSpecialist` and `RepairSpecialist` MUST NOT remain
as divergent business orchestration owners.

### 10.2 BillingSpecialist

| Capability | Kind / posture | Typed input | Typed output | Existing service owner |
| --- | --- | --- | --- | --- |
| `billing_query` | read / none | `query_type`, `period`, `fee_type`, optional `bill_id` | typed list/detail/rule result | `BillingService` |
| `billing_consult` | write-low-risk / policy HITL | `subject`, `description`, optional `bill_id` | consultation + idempotency key | `ConsultationService` |

The specialist handles bill list/detail/rule reasoning and consultation drafting. Trusted
actor/community/house access comes through current server context; optional `bill_id=None`
must remain `None` through binding and confirmation hashing. It MUST NOT calculate an
authoritative amount, change a bill, or duplicate Billing/Consultation Service policy.

The v1 selector, typed billing working state, missing-slot continuation, and legacy response
projection SHOULD be reused or migrated deliberately. They MUST NOT become a second
CapabilityExecutor path.

### 10.3 AnnouncementSpecialist

| Capability | Kind / posture | Typed input | Typed output | Existing service owner |
| --- | --- | --- | --- | --- |
| `announcement_list` | read / none | `statuses`, `limit` | announcement data | `AnnouncementService` |
| `announcement_get` | read / none | `announcement_id` | announcement data | `AnnouncementService` |
| `community_knowledge_search` | read / none | `query`, `limit` | published announcement data | `AnnouncementService` |
| `announcement_draft` | read/advisory / none | `topic`, `audience`, `requirements` | non-authoritative draft | model gateway adapter |
| `announcement_revise` | read/advisory / none | draft fields + `revision_instruction` | non-authoritative revised draft | model gateway adapter |
| `announcement_create_draft` | write-low-risk / policy HITL | `title`, `body`, `audience` | persisted announcement data | `AnnouncementService` |
| `announce_publish` | write-low-risk / policy HITL | `announcement_id`, `expected_version` | published announcement data | `AnnouncementService` |
| `announcement_schedule_publish` | write-low-risk / policy HITL | id, version, `scheduled_at` | scheduled announcement data | `AnnouncementService` |

The specialist owns drafting/revision orchestration, read selection, candidate version
handling, and clarification. Trusted roles MUST come only from `RuntimeContext`. Model/user
text cannot infer or assert `MANAGER`. Publish/schedule MUST preserve current role checks,
reviewed version binding, exact action confirmation, and Application Service authority.

The v1 announcement action normalizer, active-draft/revision continuation, trusted-role
selector guard, time projection, and presentation MAY be reused or migrated. Domain rules
and authorization MUST NOT be copied into the specialist.

### 10.4 InspectionSpecialist

| Capability | Kind / posture | Typed input | Typed output | Existing service owner |
| --- | --- | --- | --- | --- |
| `inspection_list` | read / none | target/status/risk/assignment filters, `limit` | task/event data | Inspection task/event services |
| `inspection_get_task` | read / none | `task_id` | task + actions + timeline | `InspectionTaskService` |
| `inspection_get_event` | read / none | `event_id` | event + actions + timeline | `SecurityEventService` |
| `inspection_create` | write-low-risk / policy HITL | task fields and schedule | task data | `InspectionTaskService` |
| `inspection_start_task` | write-low-risk / policy HITL | `task_id`, `expected_version` | task data | `InspectionTaskService` |
| `inspection_add_record` | write-low-risk / policy HITL | task/version + record fields | task data | `InspectionTaskService` |
| `inspection_submit_records` | write-low-risk / policy HITL | task/version + record fields | task data | `InspectionTaskService` |
| `inspection_ai_suggest` | write-low-risk / policy HITL | task/point/finding/severity/model | persisted suggestion/task data | `InspectionTaskService` |
| `security_event_create` | write-low-risk baseline; policy may raise to high-risk/HITL | event fields | event data | `SecurityEventService` |
| `security_event_submit_disposal` | write-low-risk / policy HITL | event/version/note | event data | `SecurityEventService` |
| `close_high_risk_event` | write-high-risk / human-only | `event_id` | no Agent mutation path | authorized human business flow |

`inspection_create_task -> inspection_create` and
`inspection_submit_record -> inspection_add_record` are compatibility aliases only.
`__prepare_inspection__` is v1 orchestration support, not a business capability.

The specialist may reason about tasks, records, suggestions, and security-event flows. It
MUST preserve the deterministic security risk floor. Model confidence MUST NOT downgrade
high risk. `HUMAN_ONLY` remains non-executable and cannot be replanned around. Trusted
inspection context projection must preserve canonical actor, community, roles, origin, and
lease/fence.

### 10.5 Allowlist invariants

- The Supervisor MUST derive allowed canonical names from the registry and a server-owned
  domain-to-specialist mapping.
- A specialist MUST NOT receive names from another domain.
- Alias resolution occurs at the compatibility boundary; plans SHOULD store canonical names.
- Cross-domain plans delegate each step to the specialist that owns that capability.
- An unknown, missing-adapter, wrong-domain, or removed capability fails closed.

## 11. Routing contract

The model MAY suggest a top-level objective classification, step domain, or specialist.
Deterministic routing operates on the **current PlanStep**, not one global intent. It MUST
verify:

- the specialist exists and is enabled for this v2 runtime;
- `PlanStep.domain` maps to that specialist's server-owned domain;
- any proposed capability exists, its `CapabilitySpec.domain` equals `PlanStep.domain`, and
  it belongs to that specialist's canonical allowlist;
- trusted scope is valid at the protected boundary;
- required dependencies are completed;
- execution budget remains; and
- the transition is permitted by the graph and current orchestration outcome.

The initial/global intent MUST NOT suppress a valid later cross-domain step. For example, a
Repair + Billing objective may legally execute a Repair step followed by a Billing step when
each step independently passes the domain/specialist/capability validation above.

Unknown or ambiguous requests MUST lead to clarification or safe general help. Uncertainty
MUST NOT default to a write specialist. Authorization MUST never be guessed.

## 12. Execution budget

PR5 MUST add explicit server-created ceilings for at least:

- maximum Supervisor steps;
- maximum replans;
- maximum specialist delegations;
- maximum total capability calls;
- maximum clarification loops;
- maximum cross-domain steps;
- deadline/wall-clock budget; and
- duplicate capability fingerprints.

Exact defaults are an implementation decision backed by tests and operational evidence.
The ceilings and deadline belong to immutable, server-controlled execution policy. Mutable
counters and fingerprints MAY be checkpointed in `AgentState`. The model, plan, specialist,
checkpoint, request body, or resume value MUST NOT raise a ceiling or reset a deadline.

The plan/execution deadline MUST use a restart-safe server-created budget epoch, such as
`started_at_utc` plus `deadline_at_utc`, or a verified equivalent durable representation.
The exact storage/schema is an implementation decision for the production PR, but the
original accepted epoch MUST be reconstructable by the shared lifecycle from trusted,
server-controlled semantics. A raw `time.monotonic()` timestamp is process-local: it MUST
NOT be persisted and interpreted as an absolute deadline after process restart.

Within one process, execution MAY derive a local monotonic deadline from the remaining
wall-clock duration. On resume, the effective deadline MUST be no later than:

```text
min(original accepted plan deadline, current stricter server-policy deadline)
```

Clarification, interrupt, process restart, and resume of the same active plan preserve its
budget epoch, counters, and completed dependencies; none resets or extends the deadline.
Current server policy MAY narrow the remaining duration. Only a new independent plan created
after terminal completion may receive a fresh server-created budget epoch.

All counters need one canonical owner. Existing capability `max_steps`, `max_calls`,
deadline, allowlist, and duplicate protection MUST be composed with Supervisor limits, not
silently replaced or maintained as divergent counters.

## 13. Replanning

Replanning is legal when:

- a specialist reports missing information;
- a typed recoverable capability failure invalidates the current approach;
- a completed read invalidates a later dependency;
- the user adds or changes a goal; or
- a bounded read result changes the next reasoning step.

Replanning MUST revalidate the complete typed plan and consume the replan budget. It MUST
NOT:

- bypass `DENY`, `HUMAN_ONLY`, or required HITL;
- avoid confirmation or change the exact pending action;
- widen scope, identity, roles, or capability allowlists;
- change runtime version;
- evade duplicate fingerprints;
- retry indefinitely; or
- reinterpret raw exceptions as permission.

`DENY` remains denied. `HUMAN_ONLY` remains handover. Budget exhaustion terminates safely.

## 14. Multi-domain collaboration

PR5 supports bounded **sequential** collaboration. The Supervisor is the only coordinator:

1. create and validate the plan;
2. delegate one step to the owning specialist;
3. validate and checkpoint the structured result;
4. decide finish, clarification, HITL, handover, legal replan, or next step; and
5. synthesize a final response with fact provenance.

Specialists MUST NOT call each other or share mutable objects. A result passed to another
step is bounded orchestration context, not authority. A later write re-reads/revalidates
live state at the Capability/Application Service boundary.

## 15. General help and uncertain intent

General help may remain a governed Supervisor response/synthesis node; PR5 need not invent a
fifth fake specialist. It MUST describe supported domains without claiming a business
operation succeeded. Where published community facts are needed,
`community_knowledge_search` or the retained controlled-read path is used within its actual
scope.

Ambiguous single- or cross-domain intent leads to clarification or a small validated plan.
It never defaults to a write.

## 16. HITL and approval model

PR5 MUST reuse the PR4 chain without creating Supervisor approval authority:

```text
Supervisor/specialist proposes typed business action
  -> CapabilityPolicy computes orchestration posture
  -> persist exact ProposedAction (params_hash + issued_at)
  -> official LangGraph interrupt
  -> shared lifecycle publishes accepted application head
  -> user returns confirmed + action_hash through public API
  -> shared recovery + fresh trusted RuntimeContext
  -> server prepares confirmation token / approval / idempotency material
  -> Command(resume) at exact accepted cursor
  -> specialist -> CapabilityExecutor
  -> Application Service/UoW validates and consumes authoritatively
  -> mutation + audit + outbox + commit atomically
```

Client/model/checkpoint never supplies trusted confirmation material. Resume is not
authorization.

One confirmation authorizes one exact pending action binding. A generic “approve plan” MUST
NOT authorize unrelated future writes. Every later write requires its own independently
bound confirmation unless an existing Application Service exposes an explicit reviewed bulk
operation contract.

`PreparedWrite` is trusted material for **one exact confirmed business action**. Before it
can make an invocation human-confirmed, the shared lifecycle/execution boundary MUST verify
that its server-owned binding matches the current capability/action and `params_hash`. It
SHOULD also bind the current `plan_id` and `plan_step_id`, or an equivalent server-owned
execution identity. PR5 does not mandate exact field names, but non-null
`RuntimeContext.prepared_write` alone is never sufficient.

After successful execution or cancellation, that material is consumed or invalidated for
orchestration use. If write A is followed by write B, material for A MUST fail closed for B.
The Supervisor must create `ProposedAction` B, interrupt again, receive a new exact
confirmation, and reconstruct a new `PreparedWrite` binding before B can execute. Safe
read-only steps MAY continue after A without another confirmation; every later write passes
its own HITL gate.

Cancel produces zero mutation and invalidates the pending orchestration action.

## 17. Interrupt and plan state boundary

At interrupt, the accepted application state MAY persist:

- typed plan and current step;
- selected specialist;
- exact proposed action and public confirmation projection;
- bounded prior results/provenance; and
- progress counters, fingerprints, and remaining orchestration position.

It MUST NOT persist as authority:

- confirmation token or approval truth;
- trusted identity, roles, community/house authorization, or execution source;
- lease/fence ownership;
- mutable `RuntimeContext`; or
- business transaction state.

Resume reconstructs fresh trusted context, resolves the exact accepted cursor, runs shared
recovery, and revalidates every protected boundary before graph resume.

### 17.1 Plan lifecycle

One conversation has at most one active plan and at most one active pending confirmation
action. Clarification, interrupt, restart, and resume of the same active plan preserve its
plan identity, budget epoch, mutable counters, and completed dependencies. After a plan is
terminal, a later independent user goal MAY create a new plan, new counters, and a fresh
server-created budget epoch.

### 17.2 Normal messages while `WAITING_CONFIRM`

The current baseline permits `ConversationService.start` to encounter an existing
`WAITING_CONFIRM` conversation, so PR5 MUST add deterministic lifecycle semantics above that
fact. A normal `POST /messages` while an exact pending action exists MUST NOT start or replace
another plan. The default behavior is to return/re-present the current pending confirmation
and require its resolution, without changing the accepted pending action.

Only an explicit, supported cancel or modify command MAY change that state. Under the
conversation lease, the shared lifecycle MUST first invalidate the exact old pending action
and publish that invalidation through accepted-head CAS. Only after CAS succeeds may it
replan or create a replacement proposal. Modification creates a new `params_hash`, a new
interrupt, and a new confirmation; the old `action_hash` and old prepared-write material
MUST fail closed. No implicit replacement, second active write plan, binding reuse, or
conversion of the old confirmation into a new action is permitted.

## 18. Accepted-head ownership

PR5 MUST NOT change PR4's two-level persistence ownership:

- official `PostgresSaver` owns internal graph checkpoints/super-steps/interrupt cursors;
- `agent_checkpoints` owns the application accepted head, typed state, exact cursor, and
  publication CAS.

Only the shared lifecycle publishes an accepted head after internal synchronous durability
and heartbeat/fence survival. Supervisor and specialists MUST NOT publish or select an
accepted head. An orphan or merely latest LangGraph checkpoint is non-canonical and cannot
drive normal turns, streaming, confirmation resume, or restart recovery.

## 19. Concurrency and exactly-one execution owner

PR5 MUST preserve:

- conversation run lease and heartbeat;
- monotonic fence and protected-boundary fence validation;
- first-checkpoint and subsequent accepted-head CAS;
- exact accepted runtime cursor;
- runtime pinning;
- idempotency keys and authoritative approval binding; and
- duplicate capability fingerprints.

Multi-domain planning MUST NOT create a second lifecycle, persistence, approval, or business
invocation owner. One turn uses one pinned runtime and one sequential graph execution. Shadow
business writes remain forbidden.

## 20. Runtime pinning and v1 coexistence

`Conversation.runtime_version` remains the sole runtime pin owner. PR5 MUST NOT add
`supervisor_runtime`, `specialist_runtime`, `turn_runtime`, or model-selected runtime.

- v1 conversations remain v1, including `WAITING_CONFIRM` resume.
- v2 conversations remain v2, including `WAITING_CONFIRM` resume.
- feature/eligibility changes affect only selection of new conversations.
- custom `graph_core`, the legacy graph, and pinned-v1 compatibility remain available.

PR5 MAY reduce duplicate domain orchestration only through explicit compatibility adapters
that preserve v1 public behavior and one business invocation owner. v1 drain/deletion is PR7.

## 21. Public v2 behavior completeness

PR4's public selector is hard-zero because v2 is Repair-only and returns
`UNSUPPORTED_PILOT_DOMAIN` elsewhere. PR5 MUST remove that restriction only after v2 has
defined, tested behavior for:

- Repair;
- Billing;
- Announcement;
- Inspection/Security;
- General Help; and
- ambiguous/uncertain input.

“Defined behavior” means native Supervisor routing to the canonical specialist, governed
clarification/general response, or deterministic handover—not a hidden dispatch to the v1
graph and not a generic unsupported error for a currently supported public domain.

PR5 may declare v2 **functionally complete and rollout-eligible** when all section 27 gates
pass. It MUST NOT declare v2 the default or select a production percentage. PR7 owns canary,
default rollout, production load/chaos validation, drain, and v1 retirement.

## 22. Error and outcome model

Supervisor/specialist control flow MUST consume typed outcomes, conceptually including:

- `SUCCESS`;
- `NEEDS_CLARIFICATION`;
- `REPLAN`;
- `HITL_REQUIRED`;
- `HANDOVER`;
- `DENIED`;
- `BUDGET_EXHAUSTED`;
- `CAPABILITY_ERROR`; and
- `UNSUPPORTED`.

Names MAY differ, but meanings and legal transitions must be explicit. Raw exception text or
model prose MUST NOT become control authority. Internal causes, secrets, tokens, stack traces,
and policy internals MUST NOT leak to users. Existing public business error semantics remain
preserved through normalization.

## 23. Observability minimum

PR5 MUST emit structured metadata for at least:

- Supervisor plan created/validated/rejected;
- specialist delegated/completed;
- canonical capability selected;
- policy disposition and public reason code;
- replan count and reason category;
- budget usage/exhaustion;
- clarification, HITL, and handover;
- accepted interrupt/resume correlation; and
- final structured outcome.

Observability MUST NOT log chain-of-thought, private model reasoning, confirmation tokens,
approval secrets, raw prompts, PII, or unrestricted capability payload/results. Existing
trace correlation and safe hashed/bounded metadata rules remain.

## 24. Implementation migration sequence

The production PR SHOULD proceed in reviewable slices:

1. add characterization tests for the PR4 pilot, v1 compatibility, and current public API;
2. define typed plan/outcome/specialist contracts and deterministic validators;
3. add the Supervisor skeleton to official LangGraph with no business execution;
4. converge the Repair pilot into the common specialist contract and preserve its PG HITL
   vertical;
5. migrate Billing, Announcement, and Inspection specialists one at a time using exact
   registry allowlists;
6. add sequential multi-domain planning/replanning and shared budgets;
7. complete general/uncertain behavior, API/SSE compatibility, recovery, and v1 regression;
8. run real PostgreSQL domain and cross-domain acceptance; and
9. evaluate functional rollout eligibility without changing production default rollout.

Mechanical moves and behavior changes MUST be separated. No slice may add a parallel
business mutation path.

## 25. PR5 implementation test matrix

### 25.1 Supervisor and typed plan

- production v2 uses a real official LangGraph Supervisor;
- the custom graph is not renamed or wrapped as the Supervisor;
- valid typed plans execute; malformed/cyclic/oversized plans fail closed;
- unknown specialists and wrong-domain capabilities are rejected;
- model suggestions cannot override deterministic routing or plan validation;
- Supervisor step and replan counts are bounded;
- a Repair + Billing two-step plan is valid even when the initial/global intent names only
  the first domain;
- the wrong specialist for `PlanStep.domain` is rejected;
- a `CapabilitySpec.domain` / specialist / PlanStep mismatch is rejected; and
- global initial intent cannot suppress a valid later cross-domain step.

### 25.2 Specialist statelessness

For each of the four specialists, structural and behavioral tests prove:

- no DB session, repository, UoW, checkpointer, or conversation persistence ownership;
- no cross-request mutable authority;
- no direct Application Service business invocation outside typed adapters;
- only its canonical domain allowlist is visible;
- business actions go through `CapabilityPolicy` and `CapabilityExecutor`; and
- specialists cannot call one another.

### 25.3 Routing and uncertainty

- Repair intent delegates to Repair;
- Billing delegates to Billing;
- Announcement delegates to Announcement;
- Inspection/Security delegates to Inspection;
- uncertain input clarifies or returns safe general help;
- unknown specialist proposals are rejected;
- wrong-domain capability proposals are rejected; and
- uncertainty never defaults to a write.

### 25.4 Capability policy

- no specialist bypasses `CapabilityPolicy`;
- `DENY` stays denied after replan;
- `HUMAN_ONLY` cannot be replanned around;
- a write requiring HITL interrupts before mutation;
- high-risk security classification cannot be downgraded by model confidence; and
- an alias resolves to one canonical invocation, never a duplicate path.

### 25.5 Multi-domain collaboration

- Repair read -> Billing read -> final synthesis;
- Billing result -> Announcement reasoning through Supervisor, without direct specialist
  call;
- Inspection finding -> permitted follow-up domain step through Supervisor;
- at least one conversation delegates to two or more specialists and live capabilities; and
- later writes revalidate live state rather than trusting prior narrative.

### 25.6 Budget and replanning

- maximum Supervisor steps reached -> safe terminal outcome;
- maximum replans reached -> safe failure or handover;
- maximum delegations/calls/cross-domain/clarification loops are enforced;
- duplicate capability fingerprint is blocked;
- model/checkpoint/request cannot raise a budget;
- process restart with a different monotonic origin reconstructs remaining time from the
  restart-safe server budget epoch;
- deadline is not reset or extended on clarification, interrupt, restart, or resume;
- the current stricter server-policy deadline wins on resume;
- checkpoint/model/request cannot extend the original accepted deadline; and
- a policy denial cannot be converted into a retry plan.

### 25.7 HITL

- a multi-domain plan reaching a write interrupts on that exact action only;
- accepted plan/current step/proposal survive restart;
- resume uses fresh `RuntimeContext` and exact accepted cursor;
- confirmed action executes once;
- cancel executes zero mutations;
- replay cannot execute twice; and
- every later write receives its own exact binding/confirmation;
- in a two-write plan, confirming A executes A exactly once while B remains zero-mutation
  and interrupts separately; and
- attempting to use A's `PreparedWrite` for B fails closed on capability/action,
  `params_hash`, or step binding.

### 25.8 Recovery and concurrency

Restart during planning, specialist delegation, and `WAITING_CONFIRM` recovers safely.
Wrong actor, wrong community, revoked house, expired confirmation, wrong action hash, stale
lease, stale fence, stale accepted CAS, runtime mismatch, and orphan/latest internal cursor
all fail closed. First checkpoint CAS, heartbeat, accepted publication ordering, close race,
and next-normal-turn stale-checkpoint isolation remain covered.

Additionally:

- an ordinary message during `WAITING_CONFIRM` cannot overwrite the pending action or start
  a second plan;
- explicit cancel invalidates exactly that action through accepted-head CAS before replanning;
- explicit modification creates a new `params_hash` and requires a new confirmation;
- the old `action_hash` cannot confirm the modified action; and
- budget epoch/counters do not reset through clarification, interrupt, or resume.

### 25.9 Trust boundary

Forged `AgentState`, plan, model output, request slots, or checkpoint fields for roles,
actor, community, house, runtime, approval, token, execution source, lease, or fence cannot
gain authority. Specialist projections cannot reconstruct trusted facts.

### 25.10 Domain PostgreSQL acceptance

Real PostgreSQL tests, with zero critical skips, MUST cover:

- at least one meaningful read in Repair, Billing, Announcement, and Inspection;
- representative write/HITL paths for every domain that exposes writes;
- Repair restart-confirm-cancel preservation;
- Billing consultation exact binding and optional `bill_id=None`;
- Announcement role/version/review binding for create/publish/schedule representatives;
- Inspection task/security-event representative writes and risk floor;
- `close_high_risk_event` remains human-only and non-mutating; and
- approval/fence/idempotency/audit/outbox atomicity and no shadow double-write.

Tests MUST exercise real specialists, CapabilityExecutor, adapters, and Application Services,
not only fake specialist doubles.

### 25.11 Cross-domain PostgreSQL acceptance

At least one real conversation MUST require two or more domains, produce a bounded typed
plan, delegate sequentially to two or more real specialists, invoke live
CapabilityExecutor/Application Services, and synthesize the final result without direct DB
access from orchestration.

### 25.12 API, SSE, v1, and v2 regression

- OpenAPI and current request/response schemas remain compatible unless separately justified;
- sync response and confirmation card remain compatible;
- existing SSE event families and ordering contracts remain compatible;
- pinned v1 start/resume/stream/close and `WAITING_CONFIRM` resume work;
- pinned v2 always remains v2 and never dispatches to the v1 graph;
- all current public domains and general/uncertain behavior are defined in v2; and
- default production rollout remains unchanged until separately authorized.

### 25.13 Full quality gates

The production PR MUST run focused tests first, then Ruff lint/format, compile/import checks,
`scripts/check_code_structure.py`, the complete local suite, Agent evaluation, real
PostgreSQL zero-skip tests, OpenAPI consistency, frontend tests/build, browser E2E, and the
remote backend/postgres/frontend/browser-e2e Quality Gates required by repository policy.
Evidence MUST distinguish local, PostgreSQL integration, remote CI, and human review.

## 26. PR6 and PR7 deferrals

### 26.1 PR6

PR5 MUST NOT add pgvector, memory schema, semantic memory, episodic memory, procedural
memory, Memory Writer, autonomous policy learning, or memory-based authority. A narrow
future Memory interface seam MAY be documented only to avoid an architectural dead end; it
MUST have no production persistence or behavior in PR5.

### 26.2 PR7

PR5 MUST NOT set canary percentages, make v2 the production default, claim 100% rollout,
complete production load/chaos programmes, drain pinned v1 conversations, delete the legacy
runtime, or remove rollback support. Those require PR7 evidence and explicit approval.

## 27. Functional rollout eligibility boundary

PR5 may call v2 **functionally rollout-eligible** only when:

1. all current public domains have native governed Supervisor behavior;
2. general/uncertain behavior is safe and defined;
3. no supported v2 turn returns the PR4 pilot-only unsupported outcome;
4. no v2 turn dispatches to the v1 graph;
5. four-domain and cross-domain real PostgreSQL acceptance passes;
6. HITL/recovery/concurrency/trust tests pass;
7. v1 compatibility passes; and
8. the full required Quality Gates pass.

Functional eligibility permits a later controlled rollout decision. It is not a default
rollout decision and does not authorize runtime drain.

## 28. Exit criteria

PR5 implementation may be considered complete only when:

1. one official LangGraph Supervisor exists;
2. exactly four canonical stateless specialists exist;
3. the Repair pilot has converged into the canonical Repair specialist;
4. all business actions use the canonical Capability Layer and exactly one adapter path;
5. deterministic trust, risk, policy, security, approval, and business authority remain
   outside the model/Supervisor/specialists;
6. typed plans, specialist allowlists, execution budgets, deadlines, duplicate protection,
   and bounded replanning are enforced;
7. sequential multi-domain collaboration and result synthesis work;
8. HITL and recovery survive restart using fresh trusted context and exact accepted cursor;
9. accepted-head, CAS, lease/heartbeat/fence, idempotency, and runtime pin ownership remain
   unchanged;
10. v2 is behavior-complete for current public domains and functionally rollout-eligible;
11. pinned v1 conversations remain compatible and resumable;
12. no PR6 memory implementation or authority leak is introduced;
13. no PR7 default rollout, drain, or legacy deletion is introduced; and
14. real PostgreSQL domain/cross-domain acceptance and all required Quality Gates pass.

PR5 MUST NOT claim completion from type declarations, fake-specialist tests, SQLite-only
evidence, or a clean static scan.

## 29. Self-review checklist

- [ ] Supervisor owns orchestration, not business authority.
- [ ] Four specialists are stateless and never call each other.
- [ ] Exact canonical capability allowlists are domain-scoped.
- [ ] No Supervisor/specialist direct DB, repository, UoW, or business AppService path.
- [ ] Model plans are typed proposals and deterministically validated.
- [ ] Cross-domain routing is step-local, not constrained by one global intent.
- [ ] Budget ceilings/deadline are immutable server facts; counters have one owner.
- [ ] Restart-safe budget epoch survives resume; raw monotonic timestamps do not.
- [ ] Replanning cannot bypass `DENY`, `HUMAN_ONLY`, HITL, scope, or duplicate guards.
- [ ] One confirmation binds one exact current pending action.
- [ ] `PreparedWrite` is exact capability/params/step material and cannot authorize a later write.
- [ ] `WAITING_CONFIRM` admits no implicit pending-action or active-plan replacement.
- [ ] Accepted-head publication and exact-cursor ownership remain in shared lifecycle.
- [ ] Runtime pin remains `Conversation.runtime_version` only.
- [ ] v2 never hides a v1 graph fallback.
- [ ] Public API/SSE compatibility is preserved.
- [ ] No long-term memory implementation or authority is introduced.
- [ ] No v1 drain/default rollout/legacy deletion is introduced.
- [ ] Real PostgreSQL four-domain and cross-domain acceptance is required.
- [ ] Full backend/postgres/frontend/browser-e2e gates are required.

## 30. Document authority and conflict handling

Architecture guidance is interpreted in this order:

1. current repository and production facts;
2. [`../ARCHITECTURE_NORTH_STAR.md`](../ARCHITECTURE_NORTH_STAR.md);
3. [`../REFACTOR_ROADMAP.md`](../REFACTOR_ROADMAP.md);
4. this Stage Contract; and
5. historical reports.

If repository facts reveal an ordinary implementation defect, PR5 MUST classify it and
keep the fix within an explicitly reviewed scope. If this contract conflicts with the North
Star, affected work MUST stop and record `ARCHITECTURE_CONFLICT`.

Changing business authority, Capability Registry ownership, `RuntimeContext` trust,
LangGraph/accepted-head responsibility, runtime pin ownership, or memory authority requires
an explicit ADR. PR5 MUST NOT make such a change implicitly.
