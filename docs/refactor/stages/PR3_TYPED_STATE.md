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
  business tools still execute through their legacy registries; and
- announcement publish/schedule, inspection task submit-records, and security-event
  create already consume confirmation/approval inside their Application Service UoW,
  but several other announcement/inspection Agent writes currently stop at legacy
  orchestration `require_confirmation()` and have no authoritative consumption in the
  business transaction. Section 6 records those paths as
  `KNOWN_BASELINE_AUTHORITY_GAP`.

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
| Execution policy constraints | maximum step/call ceilings, deadline/budget limits, allowlist and policy configuration | Immutable server-created constraints; they bound execution but do not record mutable progress. |
| Observation | trace/span correlation | Server instrumentation only. |
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

`RuntimeContext` MUST NOT own checkpointable invocation progress. In particular,
`step`, `calls_made`, prior fingerprints, retry progress, selected capability, and resume
cursor data do not belong beside immutable server policy ceilings. A mutable execution
datum MUST have exactly one canonical mutable owner.

### 4.2 Typed mutable AgentState

`AgentState` is a checkpointable mutable orchestration value. Its target contract MUST
make these concepts explicit and typed:

- conversation correlation and schema version;
- typed messages and current user turn input;
- intent/classification and confidence;
- selected capability and a checkpointable `CapabilityInvocationState` containing
  current step, calls made, prior fingerprints, retry/progress state, and related resume
  progress;
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

`AgentState`/`CapabilityInvocationState` own mutable progress; immutable maximums,
deadlines, allowlists, and server policy inputs come from `RuntimeContext`. Implementations
MUST NOT keep `step`, `calls_made`, or prior fingerprints mutable in both places. The
model cannot declare human confirmation. Orchestration MAY checkpoint a server-observed
human-confirmation event for resume routing, but that event is not authoritative approval
truth and cannot replace Application Service/UoW validation and consumption.

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
| PR2 `CapabilityInvocationState` (`allowlist`, `step`/`max_steps`, `calls_made`/`max_calls`, deadline, fingerprints, `human_confirmed`) | Capability caller/policy/executor | Mixed server constraints and observed invocation data in one frozen per-call value | Recreated per call; progress is not checkpointed today | No | Repair/billing wrappers currently create a fresh value; tests also inject bounds/progress directly | Split immutable constraints to `RuntimeContext.execution_policy`; move mutable progress and server-observed confirmation event to checkpointable `AgentState.capability_invocation` | SPLIT |
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

### 6.1 Current authority-path audit

For this contract:

- **A — orchestration confirmation only** means the legacy Agent tool calls
  `require_confirmation()`, but the invoked Application Service/UoW does not validate,
  bind, and consume that confirmation/approval atomically with the mutation.
- **B — authoritative transaction consumption** means the Application Service passes
  the server-issued token/reference into its UoW, which validates/binds/consumes it in
  the same transaction as mutation, idempotency, audit/outbox, and commit.

The current-main audit is:

| Legacy Agent write | Current Application Service path | Current authority class | PR3 migration requirement |
| --- | --- | --- | --- |
| `announcement_create_draft` | `AnnouncementService.create_draft()` | A — tool confirmation only; command/service has no authoritative confirmation/approval consumption | `KNOWN_BASELINE_AUTHORITY_GAP`; add the minimum typed command and Service/UoW contract correction before activating the migrated capability. |
| `announce_publish` | `AnnouncementService.publish()` | B — `ANNOUNCEMENT_PUBLISH` consumption in the mutation UoW | Preserve as the announcement reference implementation. |
| `announcement_schedule_publish` | `AnnouncementService.schedule_publish()` | B — `ANNOUNCEMENT_SCHEDULE` consumption in the mutation UoW | Preserve as the announcement reference implementation. |
| `inspection_create` | `InspectionTaskService.create_task()` | A — tool confirmation only | `KNOWN_BASELINE_AUTHORITY_GAP`; correct narrowly in the typed command and mutation UoW. |
| `inspection_start_task` | `InspectionTaskService.execute_task_action(START)` | A — tool confirmation only | `KNOWN_BASELINE_AUTHORITY_GAP`; correct narrowly for the migrated Agent write. |
| `inspection_add_record` | `InspectionTaskService.execute_task_action(ADD_RECORD)` | A — the tool confirms, then deliberately omits token/reference for the non-final record command | `KNOWN_BASELINE_AUTHORITY_GAP`; correct narrowly for the migrated Agent write. |
| `inspection_submit_records` | `InspectionTaskService.execute_task_action(SUBMIT_RECORDS)` | B — `INSPECTION_TASK_SUBMIT_RECORDS` consumption in `_submit_records()` within the task UoW | Preserve as the inspection-task reference implementation. |
| legacy `inspection_submit_record` compatibility entry | `InspectionTaskService.execute_task_action(ADD_RECORD)` | A — token/reference are passed on the command but `_add_record()` does not consume them | `KNOWN_BASELINE_AUTHORITY_GAP`; map deliberately during compatibility migration and do not claim equivalence with submit-records. |
| `inspection_ai_suggest` | `InspectionTaskService.add_ai_suggestion()` | A — tool confirmation only; service persists the suggestion without authoritative consumption | `KNOWN_BASELINE_AUTHORITY_GAP`; correct narrowly if this Agent mutation remains a migrated capability. |
| `security_event_create` | `SecurityEventService.create_event()` | B — `SECURITY_EVENT_CREATE` consumption in the event-creation UoW | Preserve as the security-event reference implementation. |
| `security_event_submit_disposal` | `SecurityEventService.execute_event_action(SUBMIT_DISPOSAL)` | A — tool confirmation only | `KNOWN_BASELINE_AUTHORITY_GAP`; correct narrowly for the migrated Agent write. |
| `close_high_risk_event` | No Agent mutation; returns handover | Not applicable — existing Agent path is human-only/non-mutating | Preserve `HUMAN_ONLY`; do not add an Agent mutation adapter. |

`KNOWN_BASELINE_AUTHORITY_GAP` is not `BLOCKING_BASELINE_DEFECT`, does not reopen P0,
and does not imply that the Application Service has ceased to be the sole business
authority. It identifies a legacy Agent write selected for PR3 migration whose current
confirmation stops at orchestration and therefore does not yet meet the already-governing
North Star transaction boundary.

Before each migrated write is activated, PR3 MUST audit its current path and, for every
class-A entry, make the minimum targeted contract correction so the existing Application
Service/UoW validates and binds authoritative confirmation/approval and atomically
commits or rolls it back with the mutation. `CapabilityExecutor` and adapters MUST NOT
consume approval. This narrowly scoped compatibility/correctness work is permitted PR3
scope; it is not an approval subsystem redesign, Application Service rewrite, or P0
redesign. A change to the North Star authority model, rather than conformance to it,
requires `ARCHITECTURE_CONFLICT` and an ADR.

### 6.2 Target capability migration

The migration inventory is:

| Current operation family | PR3 capability treatment |
| --- | --- |
| Announcement list/get and community knowledge search | Typed read/advisory capabilities with scope from `RuntimeContext`; service/provider observations remain non-authoritative. |
| Announcement draft/revise | Typed proposal capabilities. They may produce mutable draft state but MUST NOT publish or establish business truth. |
| Announcement create draft | Typed write capability calling the existing Announcement Application Service; close its recorded authority gap so authoritative consumption and mutation share the UoW. |
| Announcement publish/schedule | Separate typed write capabilities calling the existing Application Service with candidate id/version/time; policy gate and authoritative approval transaction remain distinct. |
| Inspection list/get task/get event | Typed read capabilities scoped by trusted context. |
| Inspection create/start task, add/submit records | Separate typed write capabilities calling existing Inspection Application Services; preserve the authoritative submit-records reference and close each recorded class-A gap before activation. |
| Security event create/submit disposal | Separate typed write capabilities; live risk/domain rules remain in the Application Service. |
| Inspection AI suggestion | The current operation persists a pending suggestion and is therefore a write, not a read-only advisory call. If retained for Agent invocation, migrate it as a typed write and close its recorded authority gap; its result still cannot authorize another task/event transition. |
| High-risk event close | Registered static `HUMAN_ONLY` posture and deterministic policy outcome; the Agent path MUST NOT gain a mutation adapter. |
| `__prepare_inspection__` | Not a business capability. Replace it with typed state projection/selection preparation before capability invocation. |

Stable public tool names and response/error behavior MUST remain available through
explicit compatibility projections where they are contracts. Confirmation UX/gates MUST
remain compatible, but compatibility MUST NOT preserve tool-only confirmation as the
final authority boundary for a migrated write. New canonical
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
boundary. Legacy decode/conversion and durable checkpoint migration are separate
actions.

The ordinary read path is pure:

```text
legacy snapshot -> decode -> in-memory typed conversion -> caller
```

`peek`, status, and other read-only inspection MUST NOT write an upgraded checkpoint
merely because they encountered the old schema. Conversion failure or malformed
security-sensitive legacy state MUST fail closed and MUST leave the original checkpoint
unchanged.

For pending/resume migration the required order is:

1. acquire required turn ownership and lease;
2. read the legacy checkpoint;
3. perform pure in-memory decode/conversion;
4. reconstruct/rebind trusted `RuntimeContext` from current server facts;
5. execute all existing recovery gates before resume: conversation ownership/lifecycle,
   actor/community rebinding, house binding, confirmation expiry, parameter binding,
   and relevant lease/fence checks at their protected boundaries; and
6. only after safe acceptance, persist the new schema using existing checkpoint CAS, or
   persist it as part of the next normal checkpoint save.

`load old -> auto-write upgraded schema -> run recovery checks` is forbidden. A rejected
wrong-house, expired, wrong-actor, wrong-parameter, stale-run, or otherwise unsafe
snapshot MUST NOT be silently upgraded first. A conversion CAS conflict MUST retain the
existing stale-run termination semantics and MUST NOT overwrite newer state.

The codec MUST map every legacy inventory field deterministically. Compatibility keeps
one active business invocation path: conversion MUST NOT invoke an adapter, replay a
business write, or double-execute a mutation.

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
   one write before expanding to the remaining listed operations; audit each write's
   authority class and close any recorded gap before activating it;
6. repeat the same controlled migration for inspection, including the human-only close
   path and the targeted UoW corrections required by its authority matrix;
7. remove Runner domain imports and verify that only the neutral preparation interface
   remains; and
8. collect full local, PostgreSQL, remote CI, and compatibility/drain evidence.

Mechanical state conversion and behavior changes MUST be separated. A discovered
business defect requires a focused fix and evidence; it does not authorize PR4+ scope.

## 10. Test and verification requirements

PR3 MUST add or update focused tests under `tests/` for:

- immutable `RuntimeContext` construction and rejection of model attempts to inject
  identity, roles, scope, execution source, lease/fence, or prepared-write material;
- immutable server execution-policy ceilings/configuration and checkpointed mutable
  invocation progress having no duplicated canonical owner;
- model output being unable to assert human confirmation; only a server-observed event
  may drive orchestration resume, and it never substitutes for authoritative approval;
- typed `AgentState` and every admitted domain-state variant, including invalid
  cross-domain combinations;
- optional billing `bill_id=None` preservation through state conversion and
  confirmation/application parameter hashing;
- legacy active, missing-slot, contextual-follow-up, failed-retry, pending-confirm,
  cancelled, expired, wrong-house, wrong-actor, wrong-parameter, and closed snapshots;
- checkpoint schema conversion, CAS, restart/resume, and no write replay;
- `peek` of a legacy snapshot performing no durable write;
- a rejected wrong-house legacy snapshot remaining unmodified rather than being upgraded
  before recovery checks;
- an expired pending snapshot being rejected before resume;
- conversion CAS conflict terminating the stale run without overwriting newer state;
- successful pending conversion resuming exactly once with no business mutation replay;
- repair, billing, announcement, and inspection continuation behavior equivalence;
- every announcement/inspection capability class in section 6, including trusted scope,
  typed errors, effective policy, idempotency, audit/outbox, and no shadow double-write;
- the section 6 authority matrix: every class-B reference path retains atomic
  consumption/mutation, and each class-A `KNOWN_BASELINE_AUTHORITY_GAP` receives focused
  proof that the Application Service/UoW—not executor or adapter—now consumes the bound
  confirmation/approval atomically with the migrated write;
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

The narrow command/Application Service/UoW contract corrections enumerated as
`KNOWN_BASELINE_AUTHORITY_GAP` in section 6 are allowed conformance work, not a redesign
exception for unrelated services or writes.

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
5. every migrated announcement/inspection write has a recorded authority-path audit;
   class-B references remain intact and no class-A `KNOWN_BASELINE_AUTHORITY_GAP` is
   activated without the minimum authoritative Application Service/UoW correction;
6. all business writes still enter existing Application Services, and authoritative
   approval validation/binding/consumption remains atomic with business mutation in the
   UoW; no executor or adapter consumes approval;
7. the Runner owns only generic turn coordination and no longer imports, filters, or
   branches on domain continuation state;
8. current public API/tool behavior, fallback order, confirmation UX/gates, idempotency,
   audit/outbox, lease/fencing, CAS, and terminal lifecycle semantics are preserved;
9. old checkpoints, especially pending/`WAITING_CONFIRM` snapshots, convert in memory,
   pass recovery gates before any durable upgrade, and resume safely exactly once without
   write-on-read, replay, or authority escalation;
10. compatibility code has measured removal conditions and is not prematurely deleted;
11. no PR4+ runtime, Supervisor, specialist, memory, or productionization scope is
    introduced; and
12. focused, complete local, real PostgreSQL zero-skip, Agent evaluation,
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
