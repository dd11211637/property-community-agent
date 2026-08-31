# PR1 / P0 Final Remediation Report

**Repository:** `dd11211637/property-community-agent`  
**PR:** [#13](https://github.com/dd11211637/property-community-agent/pull/13)  
**Branch:** `feat/p0-concurrency-and-approval-atomicity`  
**Generated:** 2026-08-22 (Asia/Shanghai)  
**PR state:** Open, Ready for review, not merged

## A. Baseline

| Item | SHA / state |
|---|---|
| `origin/main` | `8727c6979eafc26da91a2b8f39f3f86b6ab413cf` |
| Initial PR #13 HEAD | `099ae4f8bf0e5659b9552e95a4388de8e58327b5` |
| Final PR #13 HEAD | `7a3d4bf39caacbd740fd3f946436de9a65236934` |
| Merge base | `8727c6979eafc26da91a2b8f39f3f86b6ab413cf` |
| Final mergeability | `MERGEABLE`; `REVIEW_REQUIRED` |

Commits added in this remediation:

1. `173547c` — `refactor(agent): restore P0 structure limits`
2. `a0b1a52` — `fix(test): remove postgres concurrency self-deadlock`
3. `e19c31b` — `fix(agent): scope fencing to agent execution`
4. `7a3d4bf` — `fix(billing): wire consultation confirmation token`

The original dirty `main` worktree was not modified, reset, restored, stashed, cleaned, or staged. All work was performed in the isolated `.p0work` worktree. No rebase, amend, force-push, new PR, or merge was performed.

## B. Structure gate

The four violations were growth from already-existing orchestration/helper logic, not a need for a new agent architecture. The fix extracted existing behavior without changing public imports, tool names, response shapes, fallback order, confirmation gates, or idempotency semantics.

| Violation | Initial | Root cause and extraction | Final |
|---|---:|---|---:|
| `container.py` | 713 lines, baseline 687 | Moved confirmation-token/approval preparation into focused `confirmation_provider.py` | 663 lines |
| `runner.py` | 801 lines, baseline 754 | Moved first-turn/continuation state preparation into focused `start_state.py` | 571 lines |
| `AgentSessionRunner._plan_start` | 81 lines, limit 80 | Delegated behavior-preserving state preparation | 45 lines |
| `AgentSessionRunner` | 663 lines, baseline 617 | Removed continuation and announcement/inspection helper responsibilities | 486 lines |

`python scripts/check_code_structure.py`: **PASS**. The baseline was not increased, ignored, or disabled. `runner.py` retains the existing compatibility import used by contract tests.

## C. PostgreSQL hang

### Proven blocking point

The first real block was:

`tests/test_p0_postgres_concurrency.py::test_assert_run_fence_holds_row_lock`

While the test was hung, PostgreSQL activity showed:

- session A: `idle in transaction` after `UPDATE agent_run_leases ... RETURNING 1`, holding the fence row lock;
- session B: active, waiting on `Lock / transactionid`, blocked by session A during the competing lease `INSERT ... ON CONFLICT`.

The test synchronously invoked the competing `run_lease.acquire()` on the same test thread before reaching `session_a.rollback()`. The test therefore waited for a lock that only later code in that same thread could release: a deterministic self-deadlock.

### Why PostgreSQL exposed it

This is a real PostgreSQL row/transaction lock wait in a PostgreSQL-marked test. The issue was not reproduced by non-PostgreSQL test paths because they do not execute this row-lock scenario with PostgreSQL's transaction locking behavior.

### Fix

The competing acquire now runs in a separate thread. The test proves that it blocks while session A owns the row lock, rolls session A back, joins the contender, and verifies the contender receives `CONVERSATION_BUSY`. A second faulty assertion in `test_memory_double_writer_cas` was corrected to inspect the per-writer result suffix (`:ok` / `:VERSION_CONFLICT`) instead of comparing whole strings to `ok`.

The investigation ruled out fixture schema reset, pool exhaustion, leaked sessions, heartbeat lifecycle, migration/metadata interaction, and fixture teardown as the hang cause. No production lock, pool, fixture, or heartbeat behavior was weakened.

### Actual PostgreSQL execution

Local dedicated PostgreSQL 16 run:

```text
collected: 535 total / 19 selected
passed:    19
failed:    0
skipped:   0
duration:  46.48s
no-skip:   tests=19 skipped=0 errors=0
```

GitHub Actions PostgreSQL job:

```text
collected: 535 total / 19 selected
passed:    19
failed:    0
skipped:   0
duration:  21.49s
no-skip:   tests=19 skipped=0 errors=0
```

The executed suite covers double-writer exclusion, stale fence rejection, lease renewal/preemption, checkpoint CAS, approval/token atomicity and rollback, memory CAS, CLOSED terminal behavior, close/run races, close/mark-handover races, and rejection of `mark_handover` after CLOSED.

## D. Browser E2E fencing regression

### Root cause

`agent_concurrency_guard=True` was passed as `enforce_fence` to production confirmation ports. Those ports treated every missing lease as a stale agent run. Direct authenticated human HTTP writes do not have an agent run lease, so valid human operations were incorrectly rejected with `StaleAgentRunError`.

The repair does not use the unsafe shortcut `lease is None => human`. It introduces a trusted `ExecutionSource` on `RequestContext`:

- HTTP-created contexts default to `HUMAN`;
- `AgentSessionRunner` explicitly switches a real trusted context to `AGENT` when activating turn context;
- `AGENT` without a lease fails closed;
- `AGENT` with an expired, replaced, or wrong fence fails in `assert_run_fence`;
- `HUMAN` bypasses only agent fencing and still passes normal authorization, approval, confirmation-token, idempotency, audit, outbox, and UoW rules.

Announcement and inspection ports now use the same fencing helper. Billing uses the shared confirmation implementation through a domain-error adapter. A separate billing regression was also found: consultation creation required a confirmation token in the service, but the HTTP schema and frontend did not carry one. The router, frontend request, contract test, and committed OpenAPI document now agree.

### Real call paths

Human direct write:

```text
FastAPI authentication dependency
→ trusted RequestContext(execution_source=HUMAN)
→ direct HTTP router
→ Application Service
→ Unit of Work / confirmation port
→ agent fencing not applicable
→ normal authorization + token/approval
→ mutation + audit + outbox + commit
```

Valid agent write:

```text
AgentSessionRunner
→ acquire conversation lease
→ activate trusted RequestContext(execution_source=AGENT, agent_lease=...)
→ graph/tool
→ Application Service
→ Unit of Work / confirmation port
→ assert_run_fence
→ token/approval
→ mutation + audit + outbox + commit
```

Stale or malformed agent write:

```text
Agent execution context
→ confirmation port before approval consumption or mutation
→ missing lease: reject StaleAgentRunError
→ stale/expired/wrong fence: assert_run_fence updates 0 rows and rejects
→ Unit of Work rollback; no business mutation
```

Proven semantics:

| Path | Result |
|---|---|
| Human direct write without agent lease | **PASS** |
| Active agent with valid lease/fence | **PASS** |
| Stale agent fence | **REJECT** |
| Explicit agent execution without lease | **REJECT** |

## E. Tests and file classification

### Tests added or changed

- Added explicit human, valid-agent, stale-agent, and agent-without-lease fencing tests in `tests/test_p0_concurrency_atomicity.py`.
- Repaired the row-lock concurrency test and memory CAS assertion in `tests/test_p0_postgres_concurrency.py`.
- Added the billing OpenAPI requirement assertion in `tests/test_billing_route_contract.py`.
- No test was skipped, xfailed, deleted, or weakened to repair browser behavior.

### Tests actually run

| Validation | Result |
|---|---|
| Runner-focused tests | 52 passed |
| P0 concurrency/atomicity tests | 32 passed |
| Full Python suite without PG env | 516 passed, 19 PostgreSQL tests skipped as expected |
| Dedicated real PostgreSQL suite | 19 passed, 0 failed, 0 skipped |
| Ruff check (CI scope) | PASS |
| Ruff format check (CI scope) | PASS |
| Structure gate | PASS |
| `compileall` / `pip check` | PASS |
| Alembic upgrade/check | PASS / no new operations |
| Deterministic agent evaluation | 7/7 passed |
| Frontend lint | PASS |
| Frontend Vitest | 7 files, 27 tests passed |
| Frontend build | PASS |
| Full browser E2E on real compose stack | 26 passed |

The full GitHub backend run independently confirmed `516 passed, 19 skipped`; the dedicated PostgreSQL job independently executed all 19 PostgreSQL tests with zero skips.

### File classes

- **Production:** extracted agent state/confirmation collaborators, trusted execution-source context, fencing helper and port adapters, billing API/frontend confirmation-token wiring.
- **Tests:** the three test files listed above.
- **Contract/support:** regenerated `docs/api/openapi.json`.
- **Demo:** none.

## F. Browser E2E scenarios

All seven previously failing PR13 scenarios now pass without skip/xfail or assertion reduction:

1. Resident reports a high-risk event through Agent and enters human handover — **PASS**
2. Finance consultation remains visible after refresh and can be submitted — **PASS**
3. Manually reported security event retains state after refresh — **PASS**
4. Cross-role announcement create/review/reconfirm/publish — **PASS**
5. Cross-role inspection create/assign/record/review — **PASS**
6. Returned security event can be re-disposed and reviewed — **PASS**
7. High-risk security event requires rating confirmation before review closure — **PASS**

Final local browser run: `26 passed` in approximately 1.1 minutes.  
Final GitHub browser job: `26 passed` in 50.0 seconds; job completed in 2m27s including stack and browser setup.

## G. GitHub Actions

Final workflow: [Quality gates run 32537232520](https://github.com/dd11211637/property-community-agent/actions/runs/32537232520)

| Required job | Result | Evidence |
|---|---|---|
| backend | **PASS** | Ruff, format, structure, compile, dependency, OpenAPI, 516-test suite, deterministic eval |
| postgres | **PASS** | Alembic current; 19 passed; 0 failed; 0 skipped; no-skip guard passed |
| frontend | **PASS** | lint, 27 unit tests, production build |
| browser-e2e | **PASS** | real PostgreSQL application stack; 26 passed |

PR #13 is no longer Draft. GitHub reports `MERGEABLE`, while `mergeStateStatus=BLOCKED` only because repository ruleset `Protect main` requires one approving review, Code Owner review, resolved review threads, and related human governance. `reviewDecision=REVIEW_REQUIRED`. This is not a failing code, test, or P0 correctness gate, and it was not bypassed.

## H. Remaining blockers

Known remaining **P0 patch blockers:** none.

Remaining action before merge:

- A qualified human/Code Owner must review and approve under the active repository ruleset.
- The user must decide whether to merge PR #13.

The Node.js action runtime deprecation annotation is a non-blocking maintenance warning; it does not affect this P0 conclusion.

## Final conclusion

`READY_TO_MERGE_P0`

All required correctness and CI conditions are satisfied. PR #13 has not been merged.
