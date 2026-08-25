# PR6 Stage Contract — Long-term Memory

## 1. Purpose and status

This document freezes the architecture, semantic boundaries, implementation scope, and
testable exit criteria for PR6. It is a **Stage Contract only**. It does not install
pgvector, create embeddings, implement a Memory Writer, change production retrieval, or
claim that PR6 production behavior already exists.

PR6 answers:

> How can durable memory improve Agent reasoning while remaining scoped, revisable,
> privacy-conscious context that never becomes business authority?

The governing destination is
[`../ARCHITECTURE_NORTH_STAR.md`](../ARCHITECTURE_NORTH_STAR.md), the ordered migration
context is [`../REFACTOR_ROADMAP.md`](../REFACTOR_ROADMAP.md), and the current durable
Supervisor baseline is
[`PR5_SUPERVISOR_SPECIALISTS.md`](PR5_SUPERVISOR_SPECIALISTS.md).

**Status:** Stage Contract established; PR6 production implementation has not started in
this document.

## 2. Verified starting point

These facts were verified from `main` at
`635da922094c84cc2c3219d00e1f5c960fb9ef92`, the merge of PR #21. PR5's final reviewed
head was `38f3d1ac68ae5183eca6b990595af1dada32fbb6`.

- `agent_messages` is append-only transcript/history, not episodic memory.
- `agent_memories` is user-controlled and stores actor/community, optional house, public
  type, content, optional source conversation, confirmation, version, timestamps, expiry,
  and soft deletion.
- Public types are `PREFERENCE`, `COMMUNICATION`, `ACCESSIBILITY`, and `SERVICE_NOTE`.
  Authenticated API create/list/CAS-update/CAS-delete exists; creates are user-confirmed.
- CRUD uses trusted actor/community ownership; house-scoped creation checks account binding.
  Update/delete use guarded atomic SQL CAS, including a real PostgreSQL two-writer test.
- API and runtime loading exclude deleted/expired rows. v1 retrieves at most ten records
  for actor/community and global-or-current-house scope into
  `state.trusted_context["user_confirmed_memories"]` for model analysis.
- That v1 name wrongly groups revisable memory with trusted facts. v2 has no memory
  integration: its planner projects only business date/current-house presence, and its
  internal checkpoint removes `trusted_context`.
- Current code has no semantic/episodic/procedural distinction, pgvector, embedding
  provider, hybrid retrieval, automatic Writer, conflict model, or complete provenance.
- Tests cover CRUD, scope, expiry, loading, and CAS, but no paired value or quality suite.

The name `agent_memories` is not evidence that the PR6 target is implemented.

## 3. Product objective and value scenarios

PR6 succeeds only when governed memory measurably improves Agent task quality without
creating authority or leakage. Installing pgvector, storing vectors, returning similar
text, or adding a service class is insufficient.

At minimum, implementation and evaluation MUST prove:

| Task | Permitted improvement | Permanent boundary |
| --- | --- | --- |
| Contact preference: “上门前提前半小时用站内消息联系” | Later repair planning uses the preference without asking again | No booking, house selection, or work-order mutation authority |
| Style preference: “社区通知尽量简洁” | Later announcement drafting becomes concise | No change to facts, audience, version/review binding, or approval |
| Prior outcome: weekday-morning visits repeatedly rejected | Later planning prioritizes asking about evening/weekend availability | No permission to schedule and no deterministic rule |

## 4. Permanent authority boundary

Memory is untrusted, revisable reasoning context. It is never authority.

Memory MUST NOT:

- authenticate a user or establish actor identity;
- grant a role or authorization;
- establish tenant, community, or house scope;
- prove house ownership or binding;
- prove approval or bypass HITL;
- authorize a capability or override `CapabilityPolicy`;
- replace an Application Service query;
- become authoritative billing, repair, announcement, inspection, or security truth;
- define a legal business-state transition; or
- supply lease, fence, idempotency, confirmation, or approval material.

When memory conflicts with live authoritative business state, the live Application
Service/domain/RBAC/approval/PostgreSQL result wins. The Agent SHOULD identify the live
source when explaining the discrepancy and MAY offer to correct or delete the stale
memory.

Business actions remain:

```text
Supervisor / Specialist
  -> Capability Registry / Policy / Executor
  -> typed adapter
  -> Application Service / Domain Rules / RBAC / Approval / UoW
  -> audit / outbox / commit
```

Memory retrieval and writing are orchestration infrastructure through a dedicated Memory
API. They MUST NOT be added to the business Capability Registry merely for uniformity.

## 5. Unified memory taxonomy

PR6 MUST expose one governed retrieval surface with three precise kinds.

### 5.1 Semantic memory

Semantic memory is stable user-level knowledge or preference with future reasoning value,
such as a preferred communication channel, response style, accessibility need, or stable
service preference.

The current `PREFERENCE`, `COMMUNICATION`, `ACCESSIBILITY`, and `SERVICE_NOTE` records
become a user-confirmed subset of semantic memory. Their public types and CRUD behavior
remain compatibility contracts. Implementation MAY retain the physical values and map
them at the Memory API boundary; it MUST NOT create an unrelated “legacy memory” retrieval
stack alongside a new semantic stack.

### 5.2 Episodic memory

Episodic memory is a selected, compressed, provenance-linked record of a past interaction
with future reasoning value. It may capture an objective, meaningful outcome, user
feedback, correction, or resolved preference conflict.

An episode is not a copy of the full transcript. `agent_messages` remains conversation
history and human-handover evidence. PR6 MUST NOT embed or permanently label every raw
message as episodic memory by default.

### 5.3 Procedural candidate

A procedural candidate is a bounded hypothesis about how the Agent may better help, for
example that evening appointments have worked better for this user.

A procedural candidate is not policy, a deterministic rule, capability authorization,
or a self-modifying system instruction. It MUST NOT modify `CapabilityPolicy`, RBAC,
risk floors, HITL policy, business rules, budgets, system security instructions, or
capability arguments without a future human-reviewed governance decision.

## 6. Memory record contract

The canonical Memory API MUST return typed records. Exact table normalization is an
implementation decision, but the logical contract MUST represent:

- `memory_id` and `memory_kind`;
- canonical content or a typed canonical representation;
- trusted storage scope: `actor_id`, `community_id`, and optional `house_id`;
- source type;
- source conversation and bounded message/turn references when applicable;
- provenance sufficient to locate or explain the source;
- confirmation status and `confirmed_by_user` compatibility;
- confidence where it is meaningful, with its producer/method;
- lifecycle/conflict status;
- `created_at`, `updated_at`, optional `expires_at`, and `deleted_at`;
- optimistic-lock `version`; and
- embedding model/version/index status for vector-capable records, either on the record
  or in an owned index representation.

Fields MAY be kind-specific. A user API record need not pretend to have model confidence,
and a non-vectorized tombstone need not contain a vector. Missing fields MUST have explicit
semantics rather than ambiguous empty strings.

## 7. Provenance contract

Every automatically created memory MUST be traceable to actual evidence. Supported source
classes MUST include at least:

- explicit user Memory API;
- explicit conversation statement;
- user correction;
- completed plan outcome; and
- human-entered service note.

Automatic provenance MUST include enough bounded identifiers to answer “where did this
come from?” without storing chain-of-thought. Anonymous “AI remembered this” records are
forbidden.

Model-generated summaries MUST reference the source evidence they summarize. The Writer
MUST NOT create a user fact from an unsupported model inference.

## 8. Memory Writer ownership and timing

PR6 introduces a governed Memory Writer as orchestration infrastructure, not a business
Capability. It MAY propose candidates from explicit user statements, user corrections,
stable preferences, successful interactions, and meaningful completed outcomes.

The Writer SHOULD run after a turn or plan reaches a meaningful known state. It MUST
distinguish `completed`, `cancelled`, `failed`, `pending`, and `partial`. A failed write or
business action MUST NOT be summarized as a successful episode.

The Writer MUST NOT implement:

```text
every message -> embedding -> permanent memory
```

Candidate extraction failure MUST NOT change the business outcome. Persistence failure
MUST remain observable and MUST NOT be reported as a stored memory.

## 9. Write and confirmation policy

### 9.1 Eligible writes

- An unambiguous, future-oriented, explicit user preference has high eligibility.
- An explicit user correction is eligible and triggers conflict/supersession handling.
- A meaningful completed interaction may yield a bounded episode.
- Repeated outcomes may yield a low-authority procedural candidate with provenance.

### 9.2 Normally ineligible writes

- transient requests such as “今天下午三点联系我” unless explicitly framed as a future
  preference;
- greetings, filler, duplicate transcript content, and low-value events;
- business claims such as “我物业费已经交了” as authoritative semantic facts; and
- model guesses such as “the user probably prefers weekends.”

### 9.3 Confirmation meanings

Existing API-created records remain `confirmed_by_user=true`.

An unambiguous explicit user statement MAY be marked user-confirmed when the stored
canonical meaning is directly entailed by that statement and provenance identifies the
turn. Ambiguous or model-inferred semantic candidates require user confirmation or remain
clearly unconfirmed candidates. Procedural candidates are never automatically promoted to
user-confirmed facts.

“User confirmed this memory record” means only that the user confirmed its content. It
does not make the record trusted business authority.

## 10. Conflict, correction, and deduplication

Memory-to-memory conflict is separate from memory-to-business conflict.

### 10.1 Memory-to-memory

The lifecycle MUST distinguish at least effective active records, superseded records,
unresolved conflicts, and deleted records, whether represented by statuses, relations, or
an equivalent model.

For an old “上门前打电话” and a new explicit correction “以后不要打电话，只发站内
消息”, the correction MUST supersede the conflicting old preference for retrieval. Both
provenances remain auditable, but only the effective preference guides new reasoning.

Newer does not universally win. Resolution MUST consider explicit correction,
confirmation status, provenance quality, scope specificity, and time. An unresolved
material conflict SHOULD be withheld or presented for clarification, not returned as two
equally effective instructions forever.

### 10.2 Memory-to-business

No memory conflict algorithm can overrule live business truth. For example, remembered
“bill paid” loses to a current `BillingService` result of `UNPAID`.

### 10.3 Deduplication

Near-identical preferences MUST not multiply indefinitely. Exact normalized matches MAY
be deterministically coalesced. Semantic similarity MAY propose a merge, but merge logic
MUST retain source/provenance history, scope, confirmation, and version ownership. A model
similarity score alone MUST NOT destructively merge conflicting records.

## 11. Trusted scope and isolation

All writes and retrievals MUST derive scope from fresh trusted server context. At minimum,
the authoritative data-layer query filters by `actor_id` and `community_id`; house-scoped
retrieval additionally applies current/bound-house semantics.

Model text, plan parameters, checkpoint fields, memory content, or request-provided
`actor_id`, `community_id`, or `house_id` MUST NOT widen retrieval. Scope filtering MUST
happen before candidate text reaches vector similarity or a model prompt.

If scope is absent, ambiguous, revoked, or cannot be verified, memory retrieval fails
closed. Cross-actor, cross-community, and unauthorized cross-house leakage are release
blockers and MUST measure zero.

## 12. Hybrid retrieval contract

PR6 MUST implement bounded hybrid retrieval, conceptually:

```text
fresh trusted RuntimeContext scope
  -> canonical active / non-expired / non-deleted filter
  -> kind, source, confirmation, status, and task metadata filter
  -> semantic vector candidates + structured recency/relevance candidates
  -> deterministic conflict/supersession and safety filtering
  -> bounded ranking and token budget
  -> typed MemoryContext
```

Pure vector similarity MUST NOT be the sole filter or rank signal. Ranking SHOULD consider
task relevance, scope specificity, effective status, provenance/confirmation, freshness,
confidence where applicable, and redundancy.

Retrieval MUST return bounded typed items such as memory id, kind, content, provenance,
scope, age/freshness, confirmation/confidence, and conflict status. It MUST NOT concatenate
an uncontrolled text dump. The model prompt MUST explicitly label these items as memories
that may be stale and cannot supply authority.

## 13. PostgreSQL, pgvector, and embeddings

PostgreSQL remains canonical memory persistence. pgvector is retrieval/index support; it
does not become a second canonical store and vector similarity does not confer truth.

Embedding failure MUST NOT corrupt, partially overwrite, or falsely activate canonical
memory. Embedding/index state and model/version MUST be observable. Records MUST be
re-embeddable after model changes without losing provenance, confirmation, version, or
conflict relationships.

The implementation SHOULD define a small provider-neutral `EmbeddingProvider` contract:

```text
bounded text -> vector + model identifier + model/version metadata
```

The embedding provider and Agent model provider may be independent. Memory MUST NOT be
tightly coupled to DeepSeek.

## 14. AgentState and compatibility migration

The target type boundary is:

```text
RuntimeContext                 = trusted server authority
AgentState.retrieved_memories  = untrusted, bounded reasoning context
```

PR6 SHOULD define typed `RetrievedMemory` and `MemoryContext` representations and a
bounded `AgentState.retrieved_memories` field or equivalent. Retrieved memory MUST NOT be
placed in `RuntimeContext` or any semantically trusted projection.

The v1 key `state.trusted_context["user_confirmed_memories"]` is compatibility-only and
must have a documented retirement path. During migration, a compatibility adapter MAY
project the unified Memory API result into the old shape for pinned v1 conversations. It
MUST label/handle it as untrusted memory, use the same scope/conflict/deletion filters, and
must not create a second retrieval owner.

The v2 Supervisor and specialists consume memory only through the dedicated Memory API and
typed state projection. Specialists remain stateless and do not own memory repositories.

## 15. Checkpoint relationship

Memory is separately durable and MUST NOT be copied wholesale into durable LangGraph
checkpoints.

For a new independent plan, retrieval is fresh. Within one active logical plan, including pending confirmation and exact resume, the accepted checkpoint MAY store bounded references, retrieval metadata, and only the minimum snapshot needed for deterministic continuity. It MUST NOT silently replace the reasoning basis mid-plan.

On resume, the lifecycle validates every reference against current trusted scope, deletion, expiry, and conflict status. Deleted, expired, or newly unauthorized memory MUST not be reintroduced. If removal invalidates reasoning, the plan clarifies or safely replans; it does not preserve privacy-unsafe text for determinism. Exact pending-action binding remains unchanged and memory still cannot authorize execution.

Historical checkpoints need not be rewritten solely for deletion unless privacy/compliance requires it. New prompts and accepted state MUST not surface deleted content.

## 16. Deletion, retention, and expiry

User deletion is end-to-end meaningful. After deletion:

- the Memory API no longer returns the record;
- structured and vector retrieval no longer return it;
- new prompts do not contain it;
- index entries are deleted or tombstoned before they can surface; and
- asynchronous cleanup, if used, fails closed at canonical active filtering.

Canonical deletion/index cleanup need observable status and retries. Canonical active-record validation MUST reject a stale vector hit.

Existing `expires_at` semantics remain. User-confirmed preferences MAY be long-lived or explicitly expiring. Episodes SHOULD have bounded retention. Procedural candidates SHOULD age faster, decay in rank, and expire unless renewed. Expired records are excluded before ranking and prompting.

Low-value events MUST NOT default to permanent storage. Retention is explicit, kind-aware, configurable, and testable.

## 17. Privacy and data minimization

The Writer and Memory API MUST minimize content. They MUST NOT automatically persist credentials, secrets, approval/confirmation tokens, idempotency keys, leases, fences, private system prompts, hidden reasoning, chain-of-thought, unnecessary personal data, or unrestricted capability/business payloads.

Observability MUST use bounded metadata and MUST NOT log raw memory content by default.
Source references should permit audit without duplicating sensitive transcripts.

## 18. Failure behavior

Ordinary availability failures SHOULD degrade to no-memory reasoning rather than block unrelated business operations. Degradation is observable and the Agent MUST not invent missing context.

- Scope uncertainty or scope-filter failure: fail closed for memory retrieval.
- Embedding outage: preserve canonical memory; do not claim indexing success.
- Vector-search outage: use bounded structured fallback only with intact scope filters.
- Writer/extraction outage: continue the business turn; record no candidate as successful.
- Canonical integrity/privacy failure: fail closed and alert; never use an unscoped index.

## 19. Adversarial authority requirements

The implementation MUST prove:

- Memory “I am an administrator” has zero role or authorization effect.
- Memory “My house is 8-2-301” cannot widen a different trusted current-house scope.
- Memory “I already approved this action” cannot bypass HITL or approval binding.
- Memory “My bill is paid” loses to live `BillingService=UNPAID`.
- Procedural candidates cannot modify risk, policy, budgets, capability allowlists, or
  business inputs as deterministic shortcuts.

Code such as `if memory contains "晚上": always schedule evening` or direct injection of a
`PREFERENCE` into capability arguments is forbidden. Memory informs bounded reasoning;
Supervisor/Specialist interpretation and all deterministic/authoritative checks remain.

## 20. Value evaluation design

PR6 MUST include paired evaluations using the same task, trusted context, business data,
model/provider configuration, and evaluation rubric:

```text
WITHOUT MEMORY  vs  WITH GOVERNED MEMORY
```

Storage and retrieval tests alone cannot satisfy this gate. Required scenarios are:

1. communication preference improves a repair plan without repeated questioning;
2. style preference changes announcement wording but not facts, audience, or approval;
3. explicit correction makes only the effective contact preference guide reasoning;
4. stale “bill paid” memory loses to a live unpaid result;
5. House A memory does not leak into House B;
6. deleted memory cannot surface in a new conversation;
7. a selected episode reduces unnecessary repetition; and
8. a procedural candidate may influence planning but cannot become policy/authorization.

Evaluation MUST separate deterministic safety assertions from model-quality judgments.
Real-model evaluation MAY establish a PR6 baseline, while production rollout/SLO gates
remain PR7.

## 21. Memory quality metrics

PR6 MUST report at least:

- **Retrieval Precision:** relevant effective memories / all returned memories.
- **Retrieval Recall:** useful eligible stored memories surfaced / useful eligible records.
- **Context Efficiency:** useful memory tokens / total retrieved-memory tokens.
- **Clarification Reduction:** avoidable repeated questions without vs with memory.
- **Task Completion Lift:** paired successful-task difference attributable to memory.
- **Relevant Personalization:** tasks where a supported preference is correctly applied.
- **Incorrect-memory Influence:** tasks harmed by irrelevant or unsupported memory.
- **Stale Conflict Error Rate:** tasks where memory overrides current authoritative truth.
- **Cross-scope Leakage:** unauthorized memory surfaced; MUST be zero.
- **Deleted Memory Leakage:** deleted memory surfaced; MUST be zero.
- **Authority Violation:** memory changes identity/scope/approval/policy/business truth; MUST be zero.

The implementation plan sets dataset sizes, scoring rubrics, non-zero quality thresholds, repeated-run handling, and regression comparison. Zero-tolerance safety metrics are hard gates, not averages.

## 22. PR6 implementation test matrix

The production implementation MUST include:

- **Memory CRUD:** existing user-controlled create/list/update/delete behavior remains.
- **CAS:** stale update/delete are rejected; PostgreSQL double writers produce one winner.
- **Scope:** actor, community, global-vs-house, current-house, and revoked-house isolation.
- **Expiry:** expired records are excluded before ranking and prompting.
- **Delete:** API, structured/vector retrieval, and new prompts never surface deletion.
- **Provenance:** every automatic record is traceable to bounded real evidence.
- **Semantic retrieval:** relevant paraphrases retrieve useful active memory.
- **Hybrid retrieval:** metadata, recency, semantic relevance, dedupe, and bounds compose.
- **Conflict:** an explicit correction supersedes the old effective preference.
- **Business conflict:** live Application Service state wins.
- **Authority/Writer:** authority claims have zero effect; transient text, secrets, raw transcripts, and model inventions are not blindly stored.
- **Deduplication/Episodic:** duplicates stay bounded with provenance; episodes are compressed and transcripts are not copied wholesale.
- **Procedural:** candidates cannot mutate policy, risk, budgets, or authorization.
- **Restart:** filtering, active-plan semantics, and safe degradation survive restart.
- **V1:** pinned conversations and public Memory API use one compatible retrieval owner.
- **V2:** Supervisor/Specialists consume bounded typed memory through the dedicated API.
- **PostgreSQL:** real PostgreSQL + pgvector tests run with zero critical skips.
- **Value evaluation:** paired without/with-memory evidence covers section 20.
- **Quality:** all repository local, PostgreSQL, frontend, browser, and remote gates pass.

## 23. PR7 deferrals

PR6 MUST NOT pull in full production canary management, a complete model SLO program,
load/chaos production certification, 100% v2 rollout, pinned-v1 drain, runtime deletion, or
rollback retirement. These remain PR7.

## 24. Exit criteria

PR6 implementation may be considered complete only when:

1. one governed Memory API owns retrieval and writing;
2. existing user-controlled memory is integrated, not duplicated;
3. semantic and episodic memory work, while procedural information remains candidate-only;
4. PostgreSQL canonical persistence and pgvector retrieval/index support work;
5. scope-first hybrid retrieval is bounded and provenance-aware;
6. conflict/supersession, dedupe, retention, expiry, and deletion work end-to-end;
7. v1 compatibility and v2 typed consumption use one retrieval owner;
8. stale memory loses to live business truth;
9. memory has zero identity, scope, authorization, approval, policy, or business-state
   authority;
10. cross-scope, deleted-memory, and authority leakage are zero;
11. paired evaluation demonstrates justified reasoning value; and
12. real PostgreSQL/pgvector tests and all required Quality Gates pass.

PR6 MUST NOT claim completion from schema existence, pgvector installation, embedding
success, similarity search, fake-only tests, or storage/retrieval unit tests alone.

## 25. Document authority and conflict handling

Architecture guidance is interpreted in this order:

1. current repository and production facts;
2. [`../ARCHITECTURE_NORTH_STAR.md`](../ARCHITECTURE_NORTH_STAR.md);
3. [`../REFACTOR_ROADMAP.md`](../REFACTOR_ROADMAP.md);
4. this Stage Contract; and
5. historical reports.

If repository facts reveal an ordinary implementation defect, PR6 MUST classify it and
keep any later fix within an explicitly reviewed production scope. If this contract
conflicts with the North Star, affected work MUST stop and record
`ARCHITECTURE_CONFLICT`.

Changing business authority, Capability Registry ownership, `RuntimeContext` trust,
LangGraph/accepted-head responsibility, runtime pin ownership, or memory authority requires
an explicit ADR. PR6 MUST NOT make such a change implicitly.
