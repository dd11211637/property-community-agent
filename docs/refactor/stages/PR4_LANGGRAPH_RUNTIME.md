# PR4 Stage Contract — LangGraph Runtime Foundation

## 1. Purpose and status

This document defines the **permitted scope and testable exit criteria** for PR4. It is a
**stage contract**, not evidence that PR4 production implementation has started or that the
official LangGraph runtime already exists.

PR4 answers:

> How can a real conversation execute, interrupt, persist, resume, recover, and coexist
> through official LangGraph without changing business authority or weakening the P0
> correctness substrate?

The governing destination is
[`../ARCHITECTURE_NORTH_STAR.md`](../ARCHITECTURE_NORTH_STAR.md), the ordered migration
context is [`../REFACTOR_ROADMAP.md`](../REFACTOR_ROADMAP.md), and the immediately prior
stage is [`PR3_TYPED_STATE.md`](PR3_TYPED_STATE.md). PR4 builds on the merged PR3 typed
`RuntimeContext` / `AgentState` and the PR1/PR3 P0 correctness substrate. It MUST extend
that foundation rather than introduce a second authority or a second correctness owner.

**Status:** Stage Contract established. PR4 production implementation is **NOT** started by
this document. This document itself is a docs-only change; it adds no code, no dependency,
and no behavior.

## 2. Verified repository starting point

The contract is based on current production facts verified against the authoritative
baseline commit `70eca8523d2f2db4f153e731e89ebde08b4eff18` (post-PR3 `main`; merge of PR #17
from `codex/agent-typed-state`, final PR head `f068cc7d3116df2b7cd1238d496d7399f3d18ff6`).
All facts below were read from that tree, not inferred from the task narrative.

### 2.1 PR4 baseline alignment check (16 points)

1. **Current custom graph runtime inventory** — `src/property_agent/agent/graph_core.py`
   defines a self-built `StateGraph`, `CompiledGraph`, `Interrupt`, `interrupt`, `invoke`,
   `resume`, `invoke_stream`, `resume_stream`. This is **not** official LangGraph. Its
   module docstring explicitly states it implements the LangGraph-shaped subset needed for
   MVP and that "if later LangGraph is introduced, replace `StateGraph`/`CompiledGraph`
   with the official implementation; node and state contracts stay unchanged."
2. **Current invoke/resume/stream semantics** — `CompiledGraph.invoke` / `resume` /
   `invoke_stream` / `resume_stream` all drive an internal `_run` generator. `resume`
   sets `state._resume = resume_value` and re-runs from `state._interrupt_node or entry`.
   `expected_version` is threaded into `_emit_finish` → `checkpointer.save`.
3. **Current interrupt semantics** — `interrupt(payload)` raises `Interrupt`; the
   `_run` loop catches it, records `state._interrupt_node = current`, emits an
   `interrupt` event, and persists the checkpoint. On resume the **same node re-executes
   from its top**; the node's own `if state._resume is not None` guard is what currently
   skips the pre-interrupt build.
4. **Current checkpoint ownership** — `SqlAlchemyCheckpointer` owns durable
   orchestration snapshots in `agent_checkpoints` keyed by `thread_id = conversation_id`.
   It is explicitly *not* a business credential (per `models.py` module docstring).
5. **Current expected_version CAS** — `SqlAlchemyCheckpointer._save_cas` performs
   `UPDATE … SET version=version+1 … WHERE version=:expected RETURNING version`; 0 rows →
   `CheckpointVersionConflict`. `expected_version` is read at turn start by
   `AgentSessionRunner._turn_start_version` (never inside `save`), per ADR-0007 P0-3.
6. **Current run lease / heartbeat / fence ownership** — `agent_run_leases`
   (`AgentRunLeaseModel`: `thread_id` PK, `owner_run_id`, `lease_until`, monotonic
   `fence`). Acquire/heartbeat/release live in `run_lease.py` and are invoked only through
   `turn_guard.py` from `AgentSessionRunner._plan_start` / `_plan_resume` (ADR-0007 P0-2,
   P0-4, P0-5). Fencing is enforced at the business write boundary via
   `assert_run_fence`.
7. **Current recovery gate order** — `AgentRecoveryService.restore` runs, in order:
   (1) conversation ownership/lifecycle via `require_owned_by`; (2) house binding;
   (3) confirmation expiry (`pending_action.issued_at` TTL); (4) params-hash binding
   (`expected_action_hash` vs `params_hash`). Identity (`actor_id`/`community_id`) is
   rebound from trusted context, never from the snapshot.
8. **Current confirmation preparation / approval flow** —
   `confirmation_provider.prepare_confirmation` issues a token
   (`ConfirmationService.generate_token`) and promotes the matching approval to
   `APPROVED` (`create_pending` + `approve`), writing `state.approval_ref`. It does
   **not** consume; authoritative consumption stays in the business UoW. `AgentSessionRunner`
   re-issues the token from the *restored* state during `_plan_resume` (HTTP never accepts
   a client token).
9. **Current `runtime_version` persistence seam (not yet an active dispatcher)** —
   `ConversationModel.runtime_version` exists, default `"v1"`, column added by
   `alembic/versions/20260820_0002_add_concurrency_guards.py`. **Current repository fact:**
   the column is a *prepared persistence seam*; `ConversationSnapshot` does **not** yet expose
   `runtime_version`, `ConversationService` does **not** yet dispatch on it, and **no**
   `RuntimeSelectionPolicy` exists yet. **PR4 target:** activate this existing column as the
   **sole authoritative runtime pin owner** (persisted at conversation creation, never derived
   from model/API/checkpoint). The current repository does **not** implement that dispatch; this
   contract MUST NOT describe the not-yet-built dispatch as a present-day fact.
10. **Current API-to-runner dependency** — `adapters/api/router.py` depends on the
    concrete `AgentSessionRunner` via `dependencies.get_agent_runner`, which does
    `isinstance(runner, AgentSessionRunner)` against `request.app.state.agent_runner` and
    raises `503 ADAPTER_NOT_CONFIGURED` otherwise. The public contract is stable
    (`start`/`resume`/`stream_start`/`stream_resume`/`status`/`close`); the concrete-type
    dependency is **not**.
11. **Current container composition** — `platform/container.py::build_agent_runner`
    constructs `AgentSessionRunner` with `confirmation_token_provider`,
    `enforce_concurrency=settings.agent_concurrency_guard`, and the shared checkpointer /
    run-lease / approval services; result stored on `app.state.agent_runner`. There is
    **no** `RuntimeSelectionPolicy` and **no** runtime-selection feature flag yet.
12. **Current PR3 RuntimeContext/AgentState boundary** — `runtime.py::RuntimeContext` is a
    **frozen** dataclass wrapping `RequestContext` + `ExecutionPolicy` + `RuntimeObservation`
    + `PreparedWrite` (server-issued `confirmation_token` / `idempotency_key` /
    `approval_ref`, explicitly *not* approval truth). `state.py::AgentState` (a.k.a.
    `GraphState`) is mutable, typed (`schema_version=2`, `DomainWorkingState`,
    `CapabilityInvocationState`, `ProposedAction`, `OrchestrationState`), and MUST NOT hold
    business authority. `ProposedAction` already carries `params_hash` and `issued_at`.
13. **Current capability execution paths** — PR2 `CapabilityRegistry` / `CapabilityPolicy`
    / `CapabilityExecutor` are the stable typed path; legacy tool execution still flows
    through the subgraph tool registry (`execute_tool_node` → `registry.get(tool_name)` →
    `tool(state)`). Both paths converge on existing Application Services.
14. **Current structure-gate debt** — `scripts/check_code_structure.py` exists;
    `config/code_quality_baseline.json` records historical debt. `AgentSessionRunner`
    (`runner.py`, 571 lines) already exceeds the 500-line production-module guideline and
    is registered as historical debt (per ADR-0007 P1 note). PR4 MUST NOT expand it.
15. **PR4 minimum modification surface** — add: `langgraph` +
    `langgraph-checkpoint-postgres` dependencies (implementation phase, not this doc),
    an `AgentRuntimeFacade` / `AgentSessionFacade` + shared `TurnLifecycle` /
    `LifecycleCoordinator` + a `GraphEngine` / `GraphRuntimeEngine` split (a v2
    `LangGraphEngine`), a server-only `RuntimeSelectionPolicy`
    + rollout flag, an accepted-LangGraph-checkpoint pointer, and the Repair vertical slice.
    Everything else (business code, Application Service, Capability Layer, public API shape,
    P0 substrate) is out of scope for PR4 production change.
16. **PR5+ deferred items** — full Supervisor, multi-specialist topology, long-term memory,
    production default 100% v2, legacy runtime retirement/drain, full canary/chaos/load
    programs. See §28.

### 2.2 Conflict determination

**No `ARCHITECTURE_CONFLICT`.** The verified baseline is consistent with the North Star and
Roadmap: business authority, trust boundary, P0 CAS/lease/fence/approval, runtime pinning,
and the "custom graph_core is a LangGraph placeholder" intent all hold. The task's cited
baseline hashes (`70eca85`, `f068cc7`) correspond to real commit objects in this repository
and were adopted as the authoritative starting point. `BASELINE_DEFECT_RETRACTED` is
unchanged; no P0 fencing defect is reopened. No "main missing P0 fencing" blocker is raised
(this is a stale false blocker explicitly excluded).

## 3. PR4 objective

PR4 MUST deliver one compatible vertical foundation that proves a real conversation can
execute, interrupt, persist, resume, and recover through **official LangGraph** while:

- leaving business authority, the trust boundary, and the P0 correctness substrate
  intact and unweakened;
- keeping the legacy v1 custom runtime operational and coexisting with v2;
- pinning the runtime per conversation at creation and never switching it mid-lifecycle;
- providing an API-compatible runtime facade so the public surface does not change; and
- being fully rollback-safe.

PR4 changes the **orchestration engine** for a pilot domain, not business authority, not
the Capability Layer, not Application Services, and not the public API contract.

## 4. Scope and exclusions

### 4.1 In scope (production PR4)

- official LangGraph `StateGraph` runtime as the v2 engine;
- durable PostgreSQL-backed LangGraph checkpointer (`PostgresSaver`);
- an **`AgentRuntimeFacade` / `AgentSessionFacade`** (API-only dependency) layered above a
  shared `TurnLifecycle` / `LifecycleCoordinator` and a `GraphEngine` /
  `GraphRuntimeEngine` split (see §10 / §11);
- persisted conversation runtime pinning (`runtime_version` v1/v2), activating the existing
  column as the sole authoritative pin owner;
- coexistence of legacy v1 and LangGraph v2 for the same deployment;
- **exactly one pilot specialist execution path (Repair, see §21.1)** — stateless,
  capability-only, with no durable / RBAC / business / approval authority;
- a real end-to-end vertical slice implemented as the Repair pilot specialist, **plus**
  explicit v2 compatibility orchestration for the remaining current public intents so a
  v2-pinned conversation never loses existing capability (see §21.2);
- interrupt/resume through official LangGraph `interrupt` / `Command(resume=…)` semantics;
- restart recovery (process restart → accepted checkpoint → safe resume);
- a server-only runtime rollout feature policy (default disabled / 0%);
- rollback / disable-new-v2 behavior; and
- explicit legacy runtime retention until PR7 drain.

### 4.2 Out of scope (explicitly forbidden in PR4)

- adding the `langgraph` / `langgraph-checkpoint-postgres` dependencies in **this docs
  PR** (they are added in the implementation phase only);
- writing a LangGraph production runtime in this docs PR;
- modifying business code, Application Services, the Capability Layer, or Capability
  contracts;
- creating a Supervisor, the complete four-specialist topology, multi-specialist
  collaboration, broad replanning, or long-term memory;
- adding a new database runtime-implementation migration in this docs PR;
- changing production runtime selection, the public API, or `AgentState` business
  authority;
- retiring or draining the legacy v1 runtime (that is PR7); and
- any PR5+ scope (see §28).

## 5. Current custom runtime inventory

| Component | Location | Role today | PR4 disposition |
| --- | --- | --- | --- |
| `StateGraph` / `CompiledGraph` | `agent/graph_core.py` | custom orchestration engine | **v1 only**; v2 uses official LangGraph; must NOT be renamed/wrapped and called "v2" |
| `Interrupt` / `interrupt` | `agent/graph_core.py` | custom pause/resume | v1 only; v2 uses official `interrupt()` |
| `Checkpointer` protocol + `SqlAlchemyCheckpointer` | `agent/infrastructure/checkpointer.py` | app accepted orchestration head + CAS | retained for v1; v2 uses `PostgresSaver` for internal graph checkpoint (§17) plus an accepted-head pointer (§18) |
| `MemoryCheckpointer` | `agent/graph_core.py` | unit-test/demo only | unchanged |
| `confirm_action_node` | `agent/nodes/confirm_action.py` | build pending + `interrupt` | v1 only; v2 needs replay-safe phases (§14) |
| subgraph topology | `agent/subgraphs/base.py` | `select_tool → collect_slots → confirm → (execute\|handover\|finish) → explain` | v1 only; v2 uses a LangGraph graph with equivalent gating |
| `AgentSessionRunner` | `agent/application/runner.py` | canonical turn lifecycle + P0 ownership | becomes the generic lifecycle coordinator (`TurnLifecycle` / `LifecycleCoordinator`) behind the `AgentRuntimeFacade` (§10 / §11) |
| `AgentRecoveryService` | `agent/application/recovery.py` | 4-gate resume safety | reused by both runtimes (§20) |

## 6. Official LangGraph semantic differences

Production v2 MUST use **official LangGraph**; the custom `graph_core` is not a substitute.

Critical semantic difference the contract MUST enforce:

- Official `interrupt(payload)` suspends the node. On `Command(resume=value)` with the
  **same persistent thread config**, the node containing the `interrupt` **re-executes from
  its first line**; code before `interrupt()` runs again on resume.
- The current `confirm_action_node` constructs the pending action, derives `params_hash`,
  and stamps `issued_at` (a wall-clock timestamp) **before** calling `interrupt`. Under
  official LangGraph, a naive lift would re-run that construction on resume, re-deriving
  `issued_at` (drift) and re-mutating pending state. `params_hash` is deterministic
  (`canonical_hash`) so it will not drift if params are identical, but `issued_at` will.

Therefore PR4 v2 MUST redesign confirmation into **replay-safe phases** (§14): the typed
`ProposedAction` (including `issued_at` and `params_hash`) MUST be persisted *before* the
interrupt, and the code that runs on resume MUST NOT perform any of: creating a business
object, consuming approval, consuming a token, executing a mutation, or producing any
non-idempotent side effect.

## 7. Runtime ownership model

LangGraph owns only: graph transitions, durable orchestration, interrupt/resume, failure
recovery, and orchestration-state progression. It MUST NOT own: ORM mutation, direct SQL
business mutation, RBAC authority, approval authority, business domain transition authority.

The business path remains unchanged and single-owned:

```text
LangGraph (v2)
  -> Capability Registry
  -> Capability Policy
  -> Capability Executor
  -> Typed Adapter
  -> Existing Application Service
  -> Unit of Work
       -> authoritative approval validation / binding / consumption
       -> business mutation
       -> audit / outbox
  -> Commit
```

`CapabilityExecutor` MUST NOT consume authoritative approval in either runtime.

## 8. Runtime version pinning

- The `ConversationModel.runtime_version` column **already exists** (default `"v1"`) as the
  prepared persistence seam; **current `main` does not yet dispatch on it** (`ConversationSnapshot`
  does not expose it, `ConversationService` does not select on it, and no `RuntimeSelectionPolicy`
  exists). PR4 is the stage that **activates** this existing column as the sole authoritative
  runtime pin owner. PR4 MUST NOT create a second runtime-ownership source.
- Canonical versions: `v1` = legacy custom runtime; `v2` = LangGraph runtime.
- **New conversation:** server-side `RuntimeSelectionPolicy` selects the runtime, persists
  `runtime_version` at creation, before the first runtime-dependent execution.
- **Existing conversation:** always uses the persisted `runtime_version`.
- `ACTIVE`, `WAITING_CONFIRM`, and `HANDOVER` MUST NOT switch runtimes mid-lifecycle due
  to feature flag, deployment, request body, model output, `AgentState`, checkpoint,
  slots, or any other signal.
- The model / API client MUST NOT specify the runtime.

## 9. Runtime selection / rollout

- Add a **server-only** rollout configuration (e.g. `settings.agent_langgraph_runtime_enabled`
  or an explicit percentage policy). The default MUST be **disabled / 0%** unless a test
  explicitly enables it.
- Flag semantics = **"disable new v2 selection"**, NOT "force existing v2 conversations
  back to v1".
- A conversation already pinned `v2` MUST continue to be executed and resumed by v2.
- Distinguish:
  - **(A)** stop creating new v2 conversations → safe, flip the flag off;
  - **(B)** retire/disable the v2 runtime entirely → **unsafe while live v2-pinned
    conversations exist**; PR4 MUST NOT allow (B).

## 10. API compatibility facade (two layers)

The current public API is unchanged by PR4:

- `POST /api/agent/conversations/{id}/messages`
- `POST /api/agent/conversations/{id}/messages/stream`
- `POST /api/agent/conversations/{id}/confirmations`
- `GET /api/agent/conversations/{id}`
- `DELETE /api/agent/conversations/{id}`

PR4 MUST separate the **API-facing facade** from the **graph-engine contract**. Mixing
conversation lifecycle with the graph-engine contract (a single `GraphRuntime` exposing
`start`/`stream_start`/`resume`/`stream_resume`/`status`/`close`) is no longer acceptable.

**Layer 1 — `AgentRuntimeFacade` / `AgentSessionFacade` (API-only dependency):**

```text
AgentRuntimeFacade:
  start / stream_start / resume / stream_resume / status / close
```

The API layer MUST depend *only* on this facade. It MUST NOT depend on a concrete
`AgentSessionRunner` `isinstance` check (`dependencies.get_agent_runner`), and it MUST NOT
depend on any graph-engine type. v1 and v2 are interchangeable behind this single surface.

**Layer 2 — `TurnLifecycle` / `LifecycleCoordinator` (shared, sole lifecycle owner):**

Exactly one component owns conversation lifecycle and all P0 correctness for *both* runtimes:

```text
TurnLifecycle / LifecycleCoordinator sole owner:
  - run lease acquisition / heartbeat
  - checkpoint expected_version CAS
  - recovery gates (§20)
  - runtime selection / pin activation
  - confirmation preparation
  - conversation sync
  - close-race handling
  - transcript
  - observability
```

**Layer 3 — `GraphEngine` / `GraphRuntimeEngine` (graph only):**

```text
GraphEngine:
  - graph invoke
  - graph resume (Command(resume=…))
  - graph streaming
  - graph state / result / interrupt translation
```

Implementations: `LegacyGraphEngine` (v1 custom `graph_core`) and `LangGraphEngine` (v2
official LangGraph). A `GraphEngine` MUST NOT own `status`, `close`, conversation creation,
lease, recovery, confirmation preparation, or any business/approval authority.

The facade delegates lifecycle to `TurnLifecycle` and delegates graph work to a selected
`GraphEngine`. This preserves **one canonical P0 lifecycle owner** (see §11).

## 11. Shared turn lifecycle ownership

PR4 MUST NOT duplicate `AgentSessionRunner` + `LangGraphSessionRunner` such that each owns
its own lease, heartbeat, checkpoint CAS, recovery, confirmation preparation, conversation
sync, close-race handling, transcript, and observability. P0 correctness logic MUST NOT
form two driftable implementations.

The three-layer split in §10 fixes the ownership boundary: the **`TurnLifecycle` /
`LifecycleCoordinator`** is the single canonical owner of lease/heartbeat/fence, checkpoint
CAS, recovery gates, runtime selection, confirmation preparation, conversation sync,
close-race, transcript, and observability. The `AgentRuntimeFacade` (§10) only exposes those
to the API; the `GraphEngine` implementations only run graph steps.

Allowed implementation shapes (Codex MAY choose in the implementation plan based on real
dependencies):

- **(A)** existing `AgentSessionRunner` becomes the `TurnLifecycle` / `LifecycleCoordinator`
  (generic lifecycle owner) behind the `AgentRuntimeFacade`, with `LegacyGraphEngine` and
  `LangGraphEngine` as pluggable `GraphEngine` implementations; or
- **(B)** a thin `AgentRuntimeFacade` + explicit `TurnLifecycle` / `LifecycleCoordinator` +
  v1/v2 `GraphEngine` implementations.

In either case there is **one canonical turn-lifecycle / correctness owner**. The
lease/heartbeat/fence CAS/recovery/confirmation/conversation-sync/close-race/transcript/
observability responsibilities live in exactly one place and are reused by both engines.

## 12. LangGraph AgentState boundary

LangGraph persisted state MUST NOT become a trusted `RuntimeContext`.

- A typed / JSON-safe orchestration envelope is allowed, e.g.
  `LangGraphState → CheckpointStateCodec → AgentState`.
- `RuntimeContext` MUST be reconstructed by the server on every fresh turn / resume.
- The following MUST NOT be restored as trusted from a LangGraph checkpoint, `AgentState`,
  model args, or slots: actor, roles, community, house scope, execution_source, lease/fence,
  run identity, approval authority.

## 13. Trusted RuntimeContext boundary

- `RuntimeContext` (frozen) is reconstructed per request/resume from current server facts.
- v2 MUST re-bind identity/scope/lease/fence from the canonical platform context before
  any `Command(resume=…)`, exactly as v1 does in `AgentRecoveryService.restore` and
  `AgentSessionRunner._activate_lease_context`.
- A LangGraph checkpoint is correlation data only; it is never an authority source.

## 14. Interrupt/HITL state machine

The v2 confirmation flow MUST be replay-safe:

```text
prepare_action
  -> persist typed ProposedAction (params_hash, issued_at)        # before interrupt
  -> await_confirmation
       -> interrupt(payload)                                        # no non-replayable side effect before this
  -> Command(resume=value) + same persistent thread config
  -> confirmed / cancel routing
  -> capability execution (only on confirm)
```

Rules for code **before** `interrupt()`:

- MUST NOT create a business object;
- MUST NOT consume approval;
- MUST NOT consume a token;
- MUST NOT execute a mutation;
- MUST NOT produce any non-idempotent side effect.

`issued_at` and `params_hash` MUST be persisted in the typed `ProposedAction` before the
interrupt and MUST NOT be re-derived on resume (prevents `issued_at` drift and avoids
regenerating a different action).

Cancel → no business mutation. Confirm → exactly one business mutation through the existing
Application Service/UoW.

## 15. Prepared-write material

PR4 MUST reuse the PR3 trusted prepared-write seam.

- v2 MUST NOT treat the LangGraph checkpoint as the trusted source for
  `confirmation_token` / `approval_ref`.
- Server confirmation preparation MUST produce typed trusted material
  (`CapabilityWriteContext` / `PreparedWrite`: `confirmation_token`, `idempotency_key`,
  `approval_ref`), re-bound at resume runtime.
- v1 MAY continue its current state mirror; v2 checkpoint MUST NOT be promoted to approval
  authority.

## 16. Persistence ownership matrix

| Concern | v1 owner | v2 owner | Shared / correlation |
| --- | --- | --- | --- |
| Application accepted orchestration head | `agent_checkpoints` (`SqlAlchemyCheckpointer`, CAS) | accepted app checkpoint pointer (§18) | conversation lifecycle table owns business truth |
| Internal graph checkpoint / super-step / interrupt cursor | n/a (custom graph) | official `PostgresSaver` | thread_id = conversation_id; correlation only |
| Lease / fence | `agent_run_leases` | `agent_run_leases` (same) | single owner, both runtimes |
| Approval atomicity | `agent_action_approvals` | `agent_action_approvals` (same) | single owner, both runtimes |
| Runtime pin | `ConversationModel.runtime_version` | same | single authoritative owner |
| Business mutation / audit / outbox | Application Service UoW | same | unchanged |

## 17. LangGraph PostgresSaver contract

- Production v2 MUST use the official durable PostgreSQL checkpointer (`PostgresSaver`),
  currently scoped to `langgraph >=1.2,<1.3` and
  `langgraph-checkpoint-postgres >=3.1,<4` (exact versions re-verified at implementation).
- Explicitly handle setup/schema; no hidden runtime DDL assumption.
- Secure serializer/deserializer; no unsafe `pickle` / `cloudpickle`; JSON/primitive state
  preferred; strict `msgpack` / explicit allowed modules per the installed supported
  version.
- LangGraph DB tables are **orchestration infrastructure**, not Application Service tables.
- Any LangGraph checkpoint that may later become an application accepted-head pointer MUST be
  persisted with verified **synchronous durability** (official `durability="sync"`, or an
  explicitly verified equivalent). PR4 MUST NOT rely on async persistence for a checkpoint
  that an accepted-head CAS will reference (see §18.1).

## 18. Application accepted-head / CAS contract

- Official `PostgresSaver` and the existing `agent_checkpoints` have **different**
  responsibilities and MUST NOT be conflated.
- `PostgresSaver` = internal graph checkpoint / super-step / interrupt cursor.
- `agent_checkpoints` = application accepted orchestration head / typed `AgentState`
  compatibility / `expected_version` CAS / recovery boundary.
- PR4 MUST NOT claim "PostgresSaver already persists, so the existing checkpoint CAS can be
  deleted." The application CAS and accepted head remain authoritative for resume
  correlation.

Implementation MUST define an explicit **accepted LangGraph checkpoint pointer** (the complete
LangGraph config/cursor needed to locate the exact checkpoint — at minimum `thread_id` +
checkpoint namespace + `checkpoint_id`). This pointer is orchestration correlation, **not**
business/trust authority, and is stored only *after* the internal checkpoint is durably
persisted (see the publication protocol below).

### 18.1 Accepted-head publication protocol (hard invariant)

A LangGraph checkpoint MUST be durably persisted **before** the application accepted-head CAS
may reference it.

Legal order:

1. execute a LangGraph super-step / reach an interrupt boundary;
2. the internal checkpoint is synchronously durable (verified `durability="sync"` or
   equivalent);
3. obtain the exact persisted checkpoint config — internal `thread_id`, checkpoint
   namespace (if applicable), and `checkpoint_id`;
4. the application checkpoint CAS publishes atomically: the accepted `AgentState` **and** the
   accepted LangGraph pointer;
5. only after CAS success is that pointer canonical.

Failure semantics:

- internal checkpoint persists **+** application CAS fails → the internal checkpoint is an
  **ORPHAN**; it MUST NOT become a legal resume source.
- internal checkpoint persistence fails → the application accepted head MUST NOT advance.

PR4 MUST NOT publish an accepted pointer before internal durability is guaranteed. For the
installed LangGraph version, use `durability="sync"` (or an explicitly verified equivalent)
for checkpoints that may become application accepted heads; async persistence MUST NOT back an
accepted pointer.

### 18.2 Accepted head governs ALL v2 executions (not only `Command(resume)`)

For **any** existing v2-pinned conversation — a next normal message, `stream_start`, a
confirmation resume, or a restart recovery — canonical continuity MUST NOT be decided by
`thread_id → "latest internal LangGraph checkpoint"`. The source of continuity is always the
**application accepted head**:

- accepted app checkpoint → exact accepted internal cursor (the published pointer); **or**
- accepted `AgentState` → seed a fresh, isolated internal execution.

A stale/orphan LangGraph checkpoint MUST NOT poison:

- confirmation resume;
- the next normal user turn;
- a streaming turn (`stream_start`);
- restart recovery.

Legal resolution order (all v2 executions):

```text
Conversation / runtime pin
  -> common v2 continuity gates (§20A)        # next message, stream_start, resume, restart
  -> accepted app checkpoint                  (authoritative source of continuity)
  -> accepted LangGraph checkpoint pointer    (exact cursor, not "latest")
  -> exact LangGraph execution / resume
```

A `Command(resume=…)` additionally applies the **confirmation-resume gates (§20B)** on top of
the common gates (pending action exists, confirmation not expired, action/parameter hash
matches, proposal binding, server confirmation preparation, trusted prepared-write material).
A normal `ACTIVE` v2 conversation with **no pending action** MUST still pass the common gates
and accept a next message / `stream_start`; it MUST NOT be required to own a pending action.

Resume MUST NOT be `conversation_id → "find latest internal LangGraph checkpoint" → resume`,
because orphan/stale checkpoints MUST NOT poison the canonical resume.

## 19. Internal execution identity / stale-run isolation

- Implementation MUST define stale-run isolation using a **supported internal execution
  identity / cursor** so a stale turn's internal LangGraph checkpoints cannot overwrite or
  impersonate the accepted turn. This is **not** yet a confirmed mechanism; it MUST be
  verified against the installed official LangGraph version before being treated as settled.
- Allowed candidates (after version verification):
  - exact `checkpoint_id` anchoring;
  - an isolated internal LangGraph `thread_id`;
  - supported `checkpoint_namespace` usage — **only if** the installed version documents it
    as a valid isolation mechanism (it MUST NOT be assumed to be an arbitrary free-form
    fencing key);
  - an equivalent tested mechanism.
- `application conversation_id` remains the business conversation identity. LangGraph internal
  `thread_id` / `checkpoint_ns` are correlation only, **not** authority.
- If `checkpoint_ns` is used for per-run isolation, PR4 MUST prove with the installed official
  version that this usage is supported; it cannot be assumed.
- The accepted pointer MUST store the actual complete LangGraph config/cursor needed to locate
  the exact checkpoint (not a partial or inferred reference).
- A new normal turn MAY use a fresh isolated execution identity.
- A `WAITING_CONFIRM` resume MUST restore the exact accepted cursor of the originally
  interrupted execution.
- The new lease `run_id` still comes from the trusted current turn; the internal execution
  identity MUST NOT inherit old lease authority.

## 20. Recovery and resume ordering

Current recovery safety MUST NOT be deleted. The re-validation gates are split into two
tiers: **common v2 continuity gates** apply to every v2 execution, while **confirmation-resume
gates** apply only to `Command(resume=…)`.

### 20A. Common v2 continuity gates

These apply to **all** v2 executions — a next normal message, `stream_start`, a confirmation
resume, and a restart recovery. Before any of these, implementation MUST re-validate at least:

- conversation ownership;
- conversation lifecycle;
- `runtime_version == v2`;
- actor;
- community;
- house binding;
- fresh `RuntimeContext`;
- fresh AGENT execution_source;
- fresh lease / fence ownership (new run, server-issued);
- application accepted-head resolution (§18); and
- exact accepted internal cursor **or** accepted-`AgentState`-seeded isolated execution.

A normal `ACTIVE` v2 conversation with **no pending action** still satisfies the common gates
and MUST be allowed to accept a next normal message and `stream_start`. The common gates do
not require a pending action.

### 20B. Confirmation-resume gates

These apply **only** to `Command(resume=…)` (the `WAITING_CONFIRM` transition), in addition to
the common gates (§20A). Before resuming, implementation MUST re-validate at least:

- a pending action exists;
- confirmation is not expired (the `issued_at` TTL check);
- the action / parameter hash matches the accepted proposal;
- the proposal binding is intact;
- server confirmation preparation has produced trusted prepared-write material (§15); and
- the trusted `PreparedWrite` (`confirmation_token` / `idempotency_key` / `approval_ref`) is
  re-bound at resume runtime.

A confirmation resume that reaches this stage **without** a pending action MUST fail closed.

Authoritative approval is still validated/consumed by AppService/UoW inside the mutation
transaction for the confirm path. **Resume is not authorization.**

## 21. Repair pilot specialist and v2 cross-domain continuity

### 21.1 One pilot specialist (Repair)

The parent Roadmap PR4 deliverable requires **one specialist execution path**. PR4 MUST
provide **exactly one** pilot specialist, recommended **Repair** (unless the repository scan
surfaces a stronger reason). This pilot specialist is a PR4 deliverable; the complete
four-specialist topology, multi-specialist collaboration, broad replanning, and the Supervisor
are **deferred to PR5** (§28).

The PR4 pilot specialist MUST be:

- **stateless** (no owned repository / session / conversation state beyond the passed
  projection);
- free of **durable authority**;
- free of **RBAC / business authority**;
- free of **approval authority**;
- invoking all business actions **only through the Capability Layer**
  (`CapabilityRegistry` → `CapabilityPolicy` → `CapabilityExecutor` → typed adapter →
  Application Service → UoW);
- receiving a **minimum typed projection** of `AgentState` (mutable orchestration state) +
  `RuntimeContext` (server-trusted execution facts) as read-only inputs; and
- performing **no cross-domain planning / replanning**.

Clarification on authority vs. trust — a specialist being **authority-free** is **not** the
same as `RuntimeContext` being untrusted:

- `AgentState` remains mutable orchestration state;
- `RuntimeContext` remains server-trusted execution facts (rebound per turn/resume, §12/§13);
- the specialist **MAY consume** the trusted `RuntimeContext` facts it is given;
- the specialist **MUST NOT mutate** `RuntimeContext`;
- the specialist **MUST NOT reconstruct** trusted facts (actor, roles, community, house scope,
  lease/fence, run identity, approval authority) from `AgentState` or model input;
- the specialist **MUST NOT persist** checkpoint data as a replacement authority;
- the specialist **MUST NOT become** an RBAC / approval / business authority;
- authoritative business decisions remain with the Application Service / UoW.

It MUST NOT pre-build the full Supervisor / Repair / Billing / Announcement / Inspection
specialists (those are PR5).

At minimum prove:

**READ:**

```text
LangGraph v2 -> Repair pilot specialist (stateless)
  -> repair capability read -> CapabilityExecutor
  -> AppService -> response
```

**WRITE / HITL:**

```text
LangGraph v2 -> Repair pilot specialist (stateless)
  -> repair create proposal
  -> HITL interrupt (replay-safe, §14)
  -> durable PostgreSQL checkpoint (§17 / §18.1)
  -> process restart
  -> recovery gates (§20)
  -> server confirmation preparation
  -> Command(resume)
  -> CapabilityExecutor
  -> Repair AppService / UoW
  -> approval consume
  -> fence validation
  -> mutation
  -> audit / outbox
  -> commit
```

**cancel:** no business mutation.

If Codex believes another domain is clearly smaller and equivalent, it MAY only **propose** it
in the Stage Contract report — it MUST NOT alter the North Star / Roadmap unilaterally.

### 21.2 Non-pilot domain behavior for v2-pinned conversations

The contract requires both (a) a v2-pinned conversation's runtime is immutable for its
lifecycle, and (b) a Repair-only native vertical slice. It therefore MUST define what happens
when a v2-pinned conversation later receives a **non-Repair** intent (Billing / Announcement /
Inspection). This is a mandatory architecture hole to close.

**Hard invariant:** a v2-pinned conversation MUST NOT switch to the v1 graph runtime for a
later non-Repair turn. The following are forbidden:

```text
if intent != REPAIR:
    legacy_compiled_graph.invoke(...)        # forbidden: turn-level runtime switch
```

Turn-level runtime switching is forbidden. A v2-pinned conversation is executed by the v2
runtime for its entire lifecycle.

**Preferred implementation policy (record and follow in implementation):** the official
LangGraph v2 root retains a **behavior-compatible orchestration path** for all current public
intents:

- **Repair** → native pilot specialist execution path (§21.1);
- **Billing / Announcement / Inspection** → explicit **compatibility orchestration inside
  v2** that reuses the existing typed capability paths → Application Services.

These compatibility paths MUST NOT invoke the entire v1 `CompiledGraph` runtime, and MUST NOT
form a second runtime owner. They are v2-internal orchestration that delegates business work
to the Capability Layer, exactly like the pilot specialist.

**Fallback (if PR4 cannot deliver all public-intent compatibility):** if implementation cannot
provide every current public intent's compatibility path in PR4, then `RuntimeSelectionPolicy`
MUST keep the **general / public v2 rollout at a hard 0%**, and v2 MAY be exercised only by
explicit server-owned test / internal eligibility until PR5. An ordinary user MUST NOT be
pinned to v2 and then lose an already-shipped business capability.

## 22. No-shadow-write invariant

Every turn MUST be executed by exactly one pinned runtime. **Absolute prohibition** on:

- v1 executing a real write AND v2 executing a real write for "shadow compare".

Allowed shadow observations (no business effect):

- selection metadata;
- state conversion;
- routing decision;
- timing;
- read-only non-business observations.

Forbidden outputs of any shadow path:

- business mutation;
- approval consume;
- audit/outbox duplicate;
- idempotency consumption.

## 23. Rollback matrix

| Scenario | Behavior |
| --- | --- |
| new v2 rollout OFF | new conversations pin v1 |
| existing v1 | remain v1 |
| existing v2 `ACTIVE` | remain v2 |
| existing v2 `WAITING_CONFIRM` | resume with v2 |
| deployment rollback that fully removes v2 implementation while live v2-pinned conversations remain in DB | **NOT SAFE** |

PR7 is solely responsible for final runtime drain / retirement.

## 24. Legacy runtime coexistence / removal conditions

- v1 (custom `graph_core`) continues to operate for all v1-pinned conversations.
- v2 is additive; both share the same lease/fence/approval/checkpoint-CAS/application
  services.
- The legacy runtime is **explicitly retained** through PR4–PR6.
- Removal / drain of v1-pinned conversations is a PR7 responsibility with its own
  measured, reviewed conditions (inventory of resumable old-runtime conversations,
  completion/expiry/drain evidence, regression suites, separate review).

## 25. Security semantics preservation matrix

Controlled-read safety MUST NOT be removed for the pilot migration:

- untrusted arg guards;
- scope;
- allowlist;
- max steps / max calls;
- deadline;
- duplicate fingerprints;
- record bounds;
- safe errors;
- hashed trace.

If the pilot does not migrate controlled-read, the v1/compat path MUST be explicitly
retained ("preserve v1/compat path"), not deleted with "PR5 will fix it later".

## 26. Test matrix

PR4 implementation MUST provide at least:

**RUNTIME PINNING**

- new conversation persists v1/v2 before execution;
- user cannot choose runtime;
- model cannot choose runtime;
- slots cannot choose runtime;
- existing v1 remains v1 after flag change;
- existing v2 remains v2 after flag change;
- `WAITING_CONFIRM` v2 remains v2;
- concurrent first requests resolve one persisted pin.

**COEXISTENCE**

- v1 conversation works;
- v2 pilot conversation works;
- no turn executes both runtimes;
- no shadow business write.

**LANGGRAPH**

- real official `StateGraph` used;
- production path does not route through custom `graph_core` pretending to be v2;
- durable `PostgresSaver`;
- restart survives.

**READ PILOT**

- real repair read;
- scope preserved;
- output compatibility preserved.

**WRITE / HITL**

- proposal before business mutation;
- interrupt durable;
- cancel = zero mutation;
- confirm = exactly one mutation;
- server-issued confirmation material only;
- AppService/UoW authoritative consume;
- audit/outbox atomicity.

**INTERRUPT REPLAY**

- code before interrupt safe to replay;
- `issued_at` / `params_hash` do not drift on resume;
- proposal not regenerated into a different action;
- no pre-interrupt business side effect.

**RECOVERY**

- wrong actor rejected;
- wrong community rejected;
- revoked house rejected;
- expired pending rejected;
- wrong action hash rejected;
- closed conversation rejected;
- runtime mismatch rejected;
- read-only status does not upgrade/write checkpoint.

**CONCURRENCY**

- conversation busy;
- lease heartbeat;
- stale fence rejected;
- stale application checkpoint CAS rejected;
- stale LangGraph internal checkpoint cannot become accepted head;
- stale runtime cannot poison resume.

**SPECIALIST (pilot)**

- exactly one PR4 pilot specialist path (Repair);
- pilot specialist is stateless;
- pilot specialist has no DB / session / repository ownership;
- pilot specialist has no durable / RBAC / business / approval authority;
- pilot specialist executes business actions only through the Capability Layer;
- pilot specialist receives a minimum typed `AgentState` + `RuntimeContext` projection only;
- pilot specialist MAY consume trusted `RuntimeContext` facts but MUST NOT mutate it;
- pilot specialist MUST NOT reconstruct trusted facts from `AgentState` / model input;
- pilot specialist MUST NOT become an RBAC / approval / business authority.

**COMMON CONTINUITY GATES (§20A)**

- an `ACTIVE` v2 conversation with **no pending action** passes the common gates and accepts a
  next normal message;
- an `ACTIVE` v2 conversation with **no pending action** passes the common gates and a
  `stream_start` works;
- a confirmation resume (`Command(resume=…)`) that reaches §20B **without** a pending action
  fails closed;
- the common gates do **not** require a pending action for a normal message / `stream_start`.

**V2 CROSS-DOMAIN CONTINUITY**

- a v2-pinned conversation cannot switch to the v1 engine for a later non-Repair turn;
- non-pilot intent (Billing / Announcement / Inspection) behavior is explicitly tested;
- no hidden `LegacyGraphEngine` / v1 `CompiledGraph` fallback for a v2-pinned turn;
- Repair first turn → then Billing: runtime remains v2, no v1 engine dispatch, safe
  compatible response;
- Repair first turn → then Announcement: runtime remains v2, safe compatible response;
- Repair first turn → then Inspection: runtime remains v2, safe compatible response;
- if general/public rollout is hard-zeroed, prove the general production selector cannot
  select an incomplete v2 runtime.

**ACCEPTED-HEAD PUBLISH**

- internal LangGraph checkpoint is durably persisted *before* the accepted-head CAS
  references it;
- accepted-head CAS failure leaves the internal checkpoint as an orphan only (never a legal
  resume source);
- internal checkpoint persistence failure leaves the accepted head unchanged (no advance);
- the accepted pointer always resolves to an existing durable checkpoint.

**FRESH TURN STALE SAFETY**

- a stale internal LangGraph checkpoint cannot poison the next normal user message;
- a stale internal LangGraph checkpoint cannot poison `stream_start`;
- a stale internal LangGraph checkpoint cannot poison restart recovery;
- a stale internal LangGraph checkpoint cannot poison confirmation resume.

**LAYERING**

- the API depends only on `AgentRuntimeFacade` / `AgentSessionFacade`;
- `GraphEngine` implementations do not own `status` / `close` / conversation creation /
  lease / recovery / confirmation preparation / business authority;
- the shared `TurnLifecycle` / `LifecycleCoordinator` is the sole lifecycle owner for both
  runtimes.

**DUPLICATE / IDEMPOTENCY**

- duplicate invocation protected;
- retry does not duplicate business mutation;
- restart + resume exactly once.

**ROLLBACK**

- disable new v2 selection;
- existing v2 continues;
- existing v1 continues;
- `WAITING_CONFIRM` resume remains pinned.

**API**

- current OpenAPI unchanged;
- sync response unchanged;
- SSE event compatibility.

**POSTGRES**

- real PostgreSQL;
- zero skipped PR4 concurrency/runtime tests.

**FULL**

- existing full `pytest`;
- Agent eval;
- frontend;
- browser E2E;
- `scripts/check_code_structure.py`;
- `ruff`;
- `ruff format --check`;
- `compileall`;
- `pip check`;
- OpenAPI consistency.

## 27. Production validation gates

PR4 production PR MUST pass, before merge:

- focused PR4 tests (§26) green on SQLite and real PostgreSQL (zero-skip postgres marker);
- the complete local suite;
- Agent evaluation harness;
- frontend tests/build;
- browser E2E;
- `scripts/check_code_structure.py` (no new historical debt; `runner.py` not expanded
  beyond its registered baseline);
- `ruff check` / `ruff format --check`;
- `compileall`;
- `pip check` (new `langgraph` deps resolve cleanly);
- OpenAPI consistency;
- post-merge backend / postgres / frontend / browser-e2e quality gates green.

P0 remains closed unless a real correctness regression is discovered and separately
justified.

## 28. PR5+ deferrals

PR4 delivers **exactly one** pilot specialist (Repair, §21.1). It forbids:

- the **Supervisor**;
- the **complete four-specialist topology** (Repair / Billing / Announcement / Inspection
  working together);
- **multi-specialist collaboration**;
- **broad / multi-domain replanning**;
- treating non-Repair public intents as native specialists (they use v2 compatibility
  orchestration, §21.2, not separate specialists);
- long-term memory;
- pgvector;
- memory writer;
- procedural learning;
- production default 100% v2 (general / public rollout stays 0% unless all public-intent
  compatibility ships, §21.2);
- legacy drain;
- legacy runtime removal;
- full production canary framework;
- chaos/load programme.

**PR5** expands to the Repair / Billing / Announcement / Inspection specialists **plus** the
Supervisor, governed multi-domain routing, and HITL on the durable runtime. These belong to
PR5 / PR6 / PR7 respectively.

## 29. Exit criteria

PR4 is complete only when:

1. official LangGraph `StateGraph` runtime executes the v2 pilot (not a renamed/wrapped
   custom `graph_core`);
2. durable PostgreSQL `PostgresSaver` persists internal graph checkpoints, with an explicit
   accepted-LangGraph-checkpoint pointer and an accepted app-checkpoint head;
3. the API depends only on the `AgentRuntimeFacade` / `AgentSessionFacade`; the shared
   `TurnLifecycle` / `LifecycleCoordinator` is the sole lifecycle owner; `GraphEngine`
   implementations do not own status/close/lease/recovery (three-layer split, §10/§11);
4. P0 lease/heartbeat/fence, application checkpoint CAS, and approval atomicity are
   unchanged and regression-covered for v2;
5. runtime pinning is persisted at creation and immutable for the lifecycle;
6. v1 and v2 coexist with no shadow double-write;
7. the Repair vertical slice survives restart and resume;
8. resume revalidates identity, scope, lease/fence, approval, expiry, binding, and
   idempotency at authoritative boundaries;
9. interrupt-replay safety is proven (`issued_at` / `params_hash` stable; no pre-interrupt
   business side effect);
10. rollback disables new v2 selection while live v2 conversations continue;
11. controlled-read safety semantics are preserved (v1/compat path retained if not
    migrated); and
12. focused, full local, real PostgreSQL zero-skip, Agent evaluation, frontend/browser,
    OpenAPI, and required remote quality gates are green;
13. exactly one pilot specialist (Repair) is stateless, capability-only, holds no authority,
    and proves the READ + WRITE/HITL slice (§21.1);
14. a v2-pinned conversation keeps v2 for non-Repair turns with explicit compatibility
    orchestration and no v1-engine fallback, **or** — if PR4 cannot ship all public-intent
    compatibility — general/public v2 rollout is hard 0% and v2 is server-owned test/internal
    only (§21.2);
15. the accepted-head publication protocol (§18.1) is enforced: internal checkpoint durable
    before accepted CAS; CAS failure → orphan only; accepted pointer always resolvable; and
16. the accepted head governs every v2 execution (next message, stream_start, confirmation
    resume, restart recovery) — a stale/orphan internal checkpoint cannot poison any of them
    (§18.2).

PR4 MUST NOT claim completion based only on dependency addition, a passing import, or a
clean static scan.

## 30. Self-review checklist

- [ ] Contract reflects actual `70eca85` main facts (not chat-derived architecture).
- [ ] Official LangGraph interrupt/resume replay semantics explicitly required.
- [ ] Runtime pin model explicit (`runtime_version` single owner, no mid-lifecycle switch).
- [ ] v1/v2 coexistence explicit (no shadow double-write).
- [ ] P0 CAS / fence / lease preservation explicit.
- [ ] `PostgresSaver` ownership explicit (internal graph checkpoint, not app head).
- [ ] Accepted-head / CAS semantics explicit (no "PostgresSaver replaces app CAS").
- [ ] Recovery order explicit (≥12 gates before `Command(resume)`).
- [ ] No shadow double-write explicit.
- [ ] PR5+ exclusions explicit.
- [ ] Exactly one PR4 pilot specialist (Repair), stateless, capability-only, no authority (§21.1).
- [ ] Non-pilot v2-pinned behavior defined: no v1 switch, no hidden v1 fallback; hard-zero
  public rollout fallback recorded if compatibility is incomplete (§21.2).
- [ ] Accepted-head publication protocol explicit: internal durable before accepted CAS;
  CAS failure → orphan only; pointer always resolvable (§18.1).
- [ ] Accepted head governs all v2 executions, not only `Command(resume)` (§18.2).
- [ ] `checkpoint_ns` not overloaded as a free-form fence; isolation via verified supported
  mechanism; accepted pointer stores full cursor (§19).
- [ ] `runtime_version` baseline fact corrected: column exists as seam, PR4 activates it; no
  not-yet-built dispatch described as current fact (§2.1 / §8).
- [ ] Two-layer facade: `AgentRuntimeFacade` (API-only) above `TurnLifecycle` (sole lifecycle
  owner) above `GraphEngine` (graph only) (§10 / §11).
- [ ] Testable implementation exit criteria explicit (§26 / §29).
- [ ] Docs-only: no code, dependency, or behavior change in this PR.
- [ ] No `ARCHITECTURE_CONFLICT`; no reopened P0 fencing defect.
