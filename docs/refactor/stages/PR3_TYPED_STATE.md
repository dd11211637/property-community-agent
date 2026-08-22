# PR3 Stage Contract — Typed State + Runner De-domainization

## 1. Purpose and status

This document defines the permitted scope and testable exit conditions for PR3. It is a
stage contract, not evidence that PR3 implementation has started or that the target
types already exist.

PR3 answers:

> What does the Agent currently know, which facts are trusted, and where does mutable
> orchestration/domain working state belong?

The governing destination is
[`../ARCHITECTURE_NORTH_STAR.md`](../ARCHITECTURE_NORTH_STAR.md), and the ordered
migration context is [`../REFACTOR_ROADMAP.md`](../REFACTOR_ROADMAP.md). PR3 builds on
the merged PR2 Capability Layer; it MUST extend that framework rather than introduce a
second capability mechanism.

## 2. Stage objective

PR3 MUST deliver one compatible vertical migration that:

- establishes an immutable, server-created `RuntimeContext` for trusted invocation
  facts;
- replaces the unstructured `GraphState.slots` boundary with a typed mutable
  `AgentState` and evidence-driven typed domain working states;
- migrates announcement and inspection business actions through the existing
  `CapabilitySpec` -> `CapabilityPolicy` -> `CapabilityExecutor` -> typed adapter path;
- removes domain continuation policy from `AgentSessionRunner` and its start/resume
  planning seam without rewriting the Runner or changing its public API; and
- restores legacy checkpoints, including pending confirmation checkpoints, through an
  explicit compatibility codec until measured removal conditions pass.

PR3 changes representation and ownership, not business authority. Application Services
remain authoritative for live authorization, domain state, approval, idempotency,
business mutation, audit/outbox, and transaction ownership.

## 3. Verified repository starting point

The contract is based on the following current production facts rather than a target
model inferred from directory names:

- platform `RequestContext` is frozen and carries authenticated identity, roles,
  community/house scope, request identity, execution source, and optional Agent
  lease/fence context;
- PR2 `CapabilityRuntimeContext` and `CapabilityWriteContext` already keep trusted facts
  and server-issued write material separate from typed model-controlled input;
- `GraphState` mixes identity copies, mutable semantic fields, a generic `slots` map,
  pending-confirmation material, presentation facts, and private execution flags;
- the SQL checkpointer serializes the complete `GraphState` JSON snapshot and uses
  version CAS; the Conversation table, not the checkpoint, is authoritative for
  ownership and lifecycle;
- recovery reloads a checkpoint, rebinds identity from the current trusted context, and
  revalidates conversation ownership, house binding, confirmation expiry, and parameter
  binding before resume;
- Runner start preparation currently contains repair, announcement, and inspection
  follow-up rules, slot copying/filtering, draft continuation, and correction parsing;
  and
- repair and billing use the PR2 Capability Layer, while announcement and inspection
  business tools still execute through their legacy registries.

These facts are constraints on migration. They do not authorize retaining mixed trust
or untyped state indefinitely.

## 4. State ownership model

### 4.1 Trusted RuntimeContext

`RuntimeContext` MUST be an immutable server-created value assembled for a turn or
resumed invocation. It MUST expose typed, read-only groupings rather than a model-filled
dictionary:

| Group | Required contents | Source and rule |
| --- | --- | --- |
| Identity and scope | `actor_id`, `community_id`, `roles`, `bound_house_ids`, `current_house_id` | Authenticated/reloaded platform context; never copied from model arguments as authority. |
| Request and origin | `request_id`, `execution_source` | Server request/Agent composition only. |
| Conversation and run | `conversation_id`, `run_id`, active lease/fence data | Conversation service and turn guard; fence validity is rechecked at the protected boundary. |
| Observation | trace/span correlation and bounded invocation state | Server instrumentation and executor controls. |
| Prepared write | optional confirmation token, idempotency key, and approval reference | Server-issued `CapabilityWriteContext`; opaque to model input and not proof that a write is authorized. |

`RuntimeContext` MAY wrap the existing platform `RequestContext`, lease context, and PR2
capability contexts during migration. It MUST NOT create a divergent identity or scope
source. Any checkpointed identifiers are correlation data only: on every request and
resume, trusted values MUST be reconstructed or rebound from current server-side facts
and revalidated at their authoritative boundaries.

The model, `AgentState`, legacy checkpoint JSON, and capability input MUST NOT override
trusted context. In particular, legacy `slots.roles`, copied `actor_id`, copied
`community_id`, or a model-supplied execution source MUST NOT grant authority.

Authoritative approval validation, actor/action/parameter binding, and consumption
remain inside the existing Application Service/UoW transaction. Putting server-issued
write material in `RuntimeContext` does not move approval consumption into the Runner,
policy, executor, or adapter.

### 4.2 Typed mutable AgentState

`AgentState` is a checkpointable mutable orchestration value. Its target contract MUST
make these concepts explicit and typed:

- conversation correlation and schema version;
- typed messages and current user turn input;
- intent/classification and confidence;
- selected capability and bounded orchestration progress;
- a discriminated domain working-state union;
- missing-input and clarification state;
- a proposed pending action and interrupt/resume position;
- typed capability result or public orchestration error;
- read facts with provenance, trace summaries, retry, and handover state; and
- typed continuation metadata needed for deterministic recovery.

Mutable `AgentState` MAY contain a proposed action, a candidate entity/version, or a
cached read result. None is authoritative business state. `AgentState` MUST NOT be the
source of truth for identity, RBAC, house binding, approval status, lease ownership,
fence validity, transaction state, or a live domain transition.

Private transport flags such as `_resume`, `_interrupt_node`, `_continuation`, and
`_contextual_followup` MUST become named typed orchestration fields or codec-owned
compatibility data. They MUST NOT remain an open-ended escape hatch.

### 4.3 Domain working states derived from the current slot scan

PR3 MUST introduce types because the current lifecycles require them, not merely because
four domain folders exist. The current scan supports this target union:

```text
DomainWorkingState =
    RepairWorkingState
  | BillingWorkingState
  | AnnouncementWorkingState
  | InspectionWorkingState
  | EmptyWorkingState
```

The types MUST be shaped by the following distinct lifecycle evidence:

- `RepairWorkingState` holds create/query continuation such as action,
  `work_order_id`, description, location, category, urgency, statuses, and limit.
- `BillingWorkingState` holds query/consultation continuation such as action,
  `query_type`, optional `bill_id`, fee type, period, subject, and description. Missing
  optional `bill_id` MUST retain the Application Service representation `None`, including
  confirmation parameter derivation.
- `AnnouncementWorkingState` MUST distinguish query, drafting/revision, and
  publish/schedule preparation rather than form one nullable mega-record. It owns such
  working values as announcement id, title/body/audience/category, timing, status query,
  revision instruction, and candidate expected version. The existing active-draft and
  correction marker dictionaries become typed draft/continuation structures. Roles move
  to `RuntimeContext`.
- `InspectionWorkingState` MUST distinguish task and security-event workflows. Typed
  variants own selection candidates, task/event ids, candidate expected versions,
  query filters, route/record fields, risk/event fields, disposal data, and supplement
  data. Candidate ids/versions are mutable working facts and MUST be revalidated by the
  Application Service.

Common transport values (`user_text`, selected tool/capability, roles, correction
markers, and selection metadata) MUST live in typed shared orchestration structures, not
be repeated as magic keys in every domain state. A proposed implementation MAY further
split the types above when a real lifecycle or invariant justifies it; it MUST NOT add
speculative state for PR4+ planning, specialists, or memory.

## 5. PR3 STATE INVENTORY

Migration status uses `MIGRATE`, `SPLIT`, `RETAIN`, `COMPAT`, or `DEFER`. `COMPAT` means
readable only through the legacy checkpoint adapter, not a second source of truth.

| Current field or state | Current owner | Trust | Mutability | Business-authoritative? | Persistence / resume behavior | PR3 target owner | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `RequestContext.actor_id`, `community_id`, `roles`, `bound_house_ids`, `current_house_id` | Platform auth/request composition | Trusted server facts | Frozen per context; live authorization can change between requests | Identity/scope inputs are authoritative only when revalidated by protected services | Recreated on request; recovery overwrites checkpoint identity copies and checks house binding | `RuntimeContext.identity_scope`, wrapping the canonical platform context | MIGRATE |
| `request_id`, `execution_source` | Platform request composition | Trusted | Frozen | Origin and trace input, not domain truth | Recreated per request/turn | `RuntimeContext.request_origin` | MIGRATE |
| `AgentLeaseContext` (`thread_id`, `run_id`, `fence`, `lease_until`) | Turn guard/platform context | Trusted but time-sensitive | New value per acquired turn | Lease store/UoW fence check is authoritative | Never trusted merely because checkpointed; reacquired and checked on resume | `RuntimeContext.run`; existing turn guard remains owner | RETAIN |
| PR2 `CapabilityRuntimeContext` / `CapabilityWriteContext` | Capability composition | Trusted server seam | Frozen per invocation | Write material is not approval truth | Rebuilt for each invocation; secrets are not model fields | Typed projection from `RuntimeContext` | RETAIN |
| `GraphState.actor_id`, `community_id`, `current_house_id` | Legacy graph state | Mixed: copied trusted values become stale snapshot data | Mutable | No | Serialized in checkpoint; actor/community rebound on restore, house checked | Correlation projection only; authority remains in `RuntimeContext` | COMPAT |
| `GraphState.conversation_id` | Runner/graph state | Server-correlated identifier | Stable for lifecycle | Conversation table owns lifecycle and ownership | Checkpoint key and payload; validated against Conversation service | `AgentState.conversation_ref` plus `RuntimeContext.conversation_id` | MIGRATE |
| `messages` | Graph state | User/model content, untrusted | Append/migrate | No | Last messages copied for same-house continuation; full snapshot persisted | Typed `AgentState.messages` with bounded retention | MIGRATE |
| `intent`, `confidence` | Classifier/graph state | Model/deterministic inference | Mutable each turn | No | Persisted and reused only for eligible continuation | Typed classification state | MIGRATE |
| `slots.user_text`, `slots.tool`, correction markers | Generic slot map/nodes | User/model/orchestration data | Mutable | No | Persisted and copied/filtered by start preparation | Typed turn input, selected capability, and correction metadata | SPLIT |
| Repair slot family | Repair subgraph/tools and Runner follow-up helpers | User/model plus normalized candidates | Mutable | No | Persisted; selectively continued for same house | `RepairWorkingState` | MIGRATE |
| Billing slot family, including optional `bill_id` | Billing subgraph/tools | User/model plus read candidates | Mutable | No | Persisted; confirmation hash must preserve `None` for absence | `BillingWorkingState` | MIGRATE |
| Announcement query/draft/revision/publish slots, `_active_announcement_draft` | Announcement subgraph/tools and start preparation | User/model plus service read candidates | Mutable | No; expected version is a candidate | Persisted; draft and revision rules copy values across turns | Typed announcement query/draft/publish variants | SPLIT |
| `slots.roles` | Runner start-state builder | Copy of trusted roles placed in untrusted bag | Mutable by bag operations | No | Persisted and currently read by announcement selector | Remove; selectors/policy read `RuntimeContext.roles` | MIGRATE |
| Inspection task slots, selected task, and selection options | Inspection preparation/tools | User/model plus service read candidates | Mutable | No; service revalidates entity/version | Persisted and filtered by task continuation groups | Typed inspection task/query/selection variants | SPLIT |
| Inspection event/risk/disposal slots, selected event | Inspection preparation/tools | User/model plus service read candidates | Mutable | No | Persisted and filtered by event continuation groups | Typed inspection event/query/selection variants | SPLIT |
| `missing_slots`, `requested_slot` | Collect-slots node | Deterministic orchestration result | Mutable | No | Persisted to support single-slot reply continuation | Typed clarification state derived from capability/domain input contract | MIGRATE |
| `operation_level` and selected legacy tool | Legacy policies/subgraph | Orchestration classification | Mutable | No | Persisted around confirmation | Capability selection plus `CapabilityPolicyDecision`; compatibility projection for legacy names | MIGRATE |
| `pending_action` | Confirm node/checkpoint | Proposed orchestration action | Mutable | No | `issued_at`, params hash, tool/parameters persisted with interrupt for resume | Typed `PendingActionProposal` in `AgentState` | MIGRATE |
| `confirmation_token`, `approval_ref` | Confirmation provider/legacy state | Server-issued sensitive material | Mutable/short-lived | No; Application Service/UoW validates and consumes authoritatively | Persisted today for legacy resume; cleared on cancel/expiry | `RuntimeContext.prepared_write`; legacy codec preserves old pending snapshots | COMPAT |
| `_resume`, `_interrupt_node` | Graph core/checkpointer | Orchestration control data | Mutable | No | Persisted with pending checkpoint and used to resume exact node | Typed resume command/cursor owned by graph compatibility interface | MIGRATE |
| `_continuation`, `_contextual_followup` | Runner start preparation/classifier | Deterministic orchestration flags | Mutable | No | Recomputed/copied between turns | Typed continuation decision owned outside Runner | MIGRATE |
| `tool_result`, `read_facts`, `read_trace` | Tools/read orchestration | Typed/legacy result and observed data | Mutable | No; cached facts cannot replace live services | Persisted for response/recovery | Typed capability result and provenance-bearing read observations | SPLIT |
| `retry_count`, `error`, `handover_required` | Graph orchestration | Orchestration state | Mutable | Conversation service owns durable handover/lifecycle fact | Persisted; selected values synchronized to Conversation table | Typed execution/handover state; Conversation service remains authoritative | MIGRATE |
| Conversation row ownership, status, current house, handover, last intent | `ConversationService`/business table | Trusted persisted business lifecycle | Transactionally mutable | Yes for conversation ownership/lifecycle | Checked before resume; `CLOSED` remains terminal | Existing Conversation service/table | RETAIN |
| Checkpoint JSON, version, interrupt node, pending flag | SQL checkpointer | Durable orchestration snapshot | CAS mutable | No | Latest snapshot restored by conversation id; independent of business mutation transaction | Versioned `AgentStateCodec` over existing checkpointer contract | MIGRATE |
| Display context and confirmed-memory snippets in `trusted_context` | Context loader/graph state | Server-loaded, but cached and potentially stale | Replaced per turn | No | Snapshot currently persists it and announcement date logic may reuse it | Typed `ObservedContext`/memory references in `AgentState`; fresh server facts enter via dedicated loaders | SPLIT |

## 6. Announcement and inspection capability migration

PR3 MUST register and invoke announcement and inspection operations through the existing
PR2 registry, policy, executor, result/error contract, and observation hook. It MUST NOT
create a parallel executor or allow a migrated legacy tool to bypass policy.

The migration inventory is:

| Current operation family | PR3 capability treatment |
| --- | --- |
| Announcement list/get and community knowledge search | Typed read/advisory capabilities with scope from `RuntimeContext`; service/provider observations remain non-authoritative. |
| Announcement draft/revise | Typed proposal capabilities. They may produce mutable draft state but MUST NOT publish or establish business truth. |
| Announcement create draft | Typed write capability calling the existing Announcement Application Service and preserving confirmation/idempotency behavior. |
| Announcement publish/schedule | Separate typed write capabilities calling the existing Application Service with candidate id/version/time; policy gate and authoritative approval transaction remain distinct. |
| Inspection list/get task/get event | Typed read capabilities scoped by trusted context. |
| Inspection create/start task, add/submit records | Separate typed write capabilities calling existing Inspection Application Services. |
| Security event create/submit disposal | Separate typed write capabilities; live risk/domain rules remain in the Application Service. |
| Inspection AI suggestion | Typed advisory capability whose result cannot authorize or mutate a task/event. |
| High-risk event close | Registered static `HUMAN_ONLY` posture and deterministic policy outcome; the Agent path MUST NOT gain a mutation adapter. |
| `__prepare_inspection__` | Not a business capability. Replace it with typed state projection/selection preparation before capability invocation. |

Stable public tool names and response/error/confirmation behavior MUST remain available
through explicit compatibility projections where they are contracts. New canonical
capability names MUST be unique registry identities; aliases MUST NOT create a second
active invocation path. Each migrated write has exactly one route:

```text
typed AgentState + trusted RuntimeContext
  -> Capability Registry
  -> Capability Policy
  -> Capability Executor
  -> typed Announcement/Inspection adapter
  -> existing Application Service
  -> authoritative rules / approval / UoW / audit / outbox / commit
```

`CapabilityPolicy` MAY compute effective risk, approval/HITL requirement, and
allow/deny/human-only classification from typed input and trusted context. It MUST NOT
consume approval or replace current business authorization. No shadow double-write is
permitted.

## 7. Runner de-domainization without a rewrite

`AgentSessionRunner` remains the outer turn coordinator in PR3. It MUST retain:

- lease acquire/heartbeat/stale detection/release;
- trusted context activation and conversation ownership/lifecycle calls;
- checkpoint start-version/CAS coordination and recovery invocation;
- graph invoke/resume and streaming event adaptation;
- turn finalization, transcript persistence, and observability; and
- its existing public start, resume, stream, status, and close contracts.

The Runner and its start-planning seam MUST cease owning:

- repair correction/follow-up rules;
- announcement active-draft, revision, and time-slot continuation rules;
- inspection task/event action grouping and slot filtering;
- generic copying of domain `slots`; and
- domain decisions based on intent-specific magic keys.

Those rules MUST move to typed domain state projectors/continuation handlers behind one
small orchestration-facing preparation interface. The Runner MAY call that neutral
interface, but MUST NOT inspect domain state variants or import domain action helpers.
Factories only assemble the handlers. This is extraction and typing of existing
behavior, not a new planning framework and not a wholesale Runner rewrite.

Confirmation preparation MAY remain an injected server-side provider during PR3, but
its parameters MUST derive from the typed pending action/domain state and its returned
write material MUST enter the trusted invocation seam. Authoritative consumption stays
inside the business transaction.

## 8. Legacy checkpoint compatibility and removal conditions

PR3 MUST introduce an explicit versioned state codec/adapter at the checkpointer
boundary. The required migration behavior is:

1. read both the current unversioned `GraphState` snapshot and the new versioned typed
   snapshot;
2. map every legacy field listed in the inventory deterministically, rejecting malformed
   security-sensitive state rather than guessing;
3. write only the new schema after successful conversion, while preserving checkpointer
   CAS and stable conversation/thread identity;
4. keep one active business invocation path; compatibility converts representation and
   MUST NOT replay or double-execute a write; and
5. re-run all existing recovery gates after conversion and before resume.

Legacy `pending`/`WAITING_CONFIRM` snapshots MUST retain the exact action, canonical
parameter hash, issued time, interrupt cursor, current house correlation, and opaque
server-issued confirmation/approval references needed by the existing safe resume path.
Conversion MUST NOT mark an action approved, refresh its expiry, change `None`/empty
parameter representation, change idempotency identity, or consume approval.

The old snapshot reader and compatibility projections MAY be removed only when all of
the following are evidenced:

- production inventory shows no resumable old-schema checkpoints, including pending and
  `WAITING_CONFIRM` conversations, for the approved retention/drain window;
- all such conversations completed, expired through the existing rules, or were safely
  drained without bypassing confirmation;
- rollback no longer requires the old representation;
- restart/resume, expiry, binding, cancellation, duplicate, stale-fence, and `CLOSED`
  terminal regression suites pass on converted snapshots; and
- removal is separately reviewed. PR3 itself MUST NOT retire runtime-pinned legacy
  conversations.

## 9. Migration sequence

Implementation MUST proceed as reviewable slices:

1. freeze this inventory with focused characterization tests for current state,
   continuation, checkpoint, and resume behavior;
2. add immutable `RuntimeContext` composition and trust-boundary tests without changing
   business execution;
3. add typed `AgentState`, domain working states, and the versioned compatibility codec;
4. convert one domain continuation path at a time, preserving external behavior and
   removing the corresponding magic-key ownership from Runner preparation;
5. add announcement typed contracts/adapters/registry entries, then migrate one read and
   one write before expanding to the remaining listed operations;
6. repeat the same controlled migration for inspection, including the human-only close
   path;
7. remove Runner domain imports and verify that only the neutral preparation interface
   remains; and
8. collect full local, PostgreSQL, remote CI, and compatibility/drain evidence.

Mechanical state conversion and behavior changes MUST be separated. A discovered
business defect requires a focused fix and evidence; it does not authorize PR4+ scope.

## 10. Test and verification requirements

PR3 MUST add or update focused tests under `tests/` for:

- immutable `RuntimeContext` construction and rejection of model attempts to inject
  identity, roles, scope, execution source, lease/fence, or prepared-write material;
- typed `AgentState` and every admitted domain-state variant, including invalid
  cross-domain combinations;
- optional billing `bill_id=None` preservation through state conversion and
  confirmation/application parameter hashing;
- legacy active, missing-slot, contextual-follow-up, failed-retry, pending-confirm,
  cancelled, expired, wrong-house, wrong-actor, wrong-parameter, and closed snapshots;
- checkpoint schema conversion, CAS, restart/resume, and no write replay;
- repair, billing, announcement, and inspection continuation behavior equivalence;
- every announcement/inspection capability class in section 6, including trusted scope,
  typed errors, effective policy, idempotency, audit/outbox, and no shadow double-write;
- high-risk inspection close remaining human-only and non-mutating from the Agent path;
- Runner contract equivalence plus a structural check that it no longer imports or
  branches on domain continuation policy; and
- preservation of P0 lease/fencing, approval atomicity, `CLOSED`, and existing public API
  behavior.

The stage MUST run focused tests first, then Ruff lint and format checks,
`scripts/check_code_structure.py`, the complete local suite, real PostgreSQL zero-skip
tests, Agent evaluation, frontend tests/build, browser E2E, and OpenAPI consistency as
required by repository policy. Evidence MUST distinguish static, local, PostgreSQL,
remote CI, and human review results.

## 11. Explicit exclusions

PR3 MUST NOT introduce or undertake:

- the PR4 LangGraph root runtime, runtime feature flag, runtime selection/pinning
  implementation, new durable graph backend, or legacy-runtime drain;
- the PR5 Supervisor, specialist agents, multi-agent delegation, replanning, or new
  execution-budget architecture;
- the PR6 long-term vector-memory schema, pgvector, Memory Writer, or memory-policy work;
- the PR7 streaming/telemetry redesign, canary rollout, load/chaos program, or production
  runtime retirement;
- a broad Runner rewrite, public API redesign, new orchestration framework, or topology
  rewrite;
- Application Service, domain model, RBAC, approval transaction, P0 lease/fencing,
  idempotency, audit, outbox, or transaction-ownership redesign;
- direct ORM/repository/SQL business mutation from state handlers, capabilities,
  adapters, Runner, or graph nodes;
- business facts, approval truth, or live authorization stored authoritatively in
  `AgentState`; or
- forced migration, runtime switching, or retirement of pending/runtime-pinned legacy
  conversations.

PR4 through PR7 direction remains governed by the Roadmap and North Star. PR3 extension
points MUST NOT become speculative implementation of those stages.

## 12. Exit criteria

PR3 is complete only when:

1. one immutable server-created `RuntimeContext` supplies trusted identity, scope,
   origin, conversation/run, lease/fence, trace, and prepared-write facts without model
   override;
2. typed `AgentState` and evidence-driven domain working-state variants replace generic
   slot access on all migrated execution paths;
3. the state inventory has no unexplained field, trust source, persistence behavior, or
   target owner;
4. announcement and inspection business actions use the PR2 Registry, Policy, Executor,
   and typed adapters with exactly one active mutation path;
5. all business writes still enter existing Application Services, and authoritative
   approval consumption remains atomic with business mutation in the UoW;
6. the Runner owns only generic turn coordination and no longer imports, filters, or
   branches on domain continuation state;
7. current public API/tool behavior, fallback order, confirmation gates, idempotency,
   audit/outbox, lease/fencing, CAS, and terminal lifecycle semantics are preserved;
8. old checkpoints, especially pending/`WAITING_CONFIRM` snapshots, convert and resume
   safely through explicit compatibility code without replay or authority escalation;
9. compatibility code has measured removal conditions and is not prematurely deleted;
10. no PR4+ runtime, Supervisor, specialist, memory, or productionization scope is
    introduced; and
11. focused, complete local, real PostgreSQL zero-skip, Agent evaluation,
    frontend/browser, OpenAPI, and required remote quality gates are green.

PR3 MUST NOT claim completion based only on type declarations, unit tests, or a clean
static scan.

## 13. Document authority and conflict handling

Architecture guidance is interpreted in this order:

1. current repository and production facts;
2. [`../ARCHITECTURE_NORTH_STAR.md`](../ARCHITECTURE_NORTH_STAR.md);
3. [`../REFACTOR_ROADMAP.md`](../REFACTOR_ROADMAP.md);
4. this stage contract; and
5. historical reports.

If repository facts reveal an implementation defect, PR3 MUST classify it explicitly;
ordinary defects do not authorize expanding the stage. If this contract conflicts with
the North Star, affected implementation MUST stop and record `ARCHITECTURE_CONFLICT`.

Changing the business authority boundary, Capability Registry ownership,
`RuntimeContext` trust model, LangGraph responsibility, or memory authority model
requires an explicit ADR. PR3 MUST NOT make such a change implicitly.
