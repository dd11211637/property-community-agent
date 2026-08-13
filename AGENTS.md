# Repository engineering rules

These rules apply to every change in this repository.

## Production boundaries

1. Production modules contain only real runtime contracts, validation, orchestration,
   error handling, and backend adapters. Demo, mock, fixture, and temporary validation
   code belongs in `examples/`, `testing/`, or `tests/`.
2. Code under `src/` must not import `tests` or `testing`, and must import successfully
   without local JSON/JSONL data, demo seeds, or test fixtures.
3. Identity, authorization, house/community scope, confirmation tokens, versions, and
   idempotency keys come from trusted server context. Model output never overrides them.
4. Public import paths and API/tool names are compatibility contracts. Internal moves
   require a facade or adapter plus contract tests.

## Readability and responsibility limits

1. A new production Python module is limited to 500 physical lines.
2. A new function or method is limited to 80 physical lines, including its signature
   and docstring. A coordinator should normally stay below 40 lines.
3. A new class is limited to 400 physical lines and must have one stated responsibility.
4. Factories only assemble dependencies and return configured objects. Business logic
   belongs in named services, policies, parsers, or handlers.
5. Provider I/O, deterministic business rules, fallback policy, presentation, and
   persistence must be separate responsibilities.
6. Do not reduce line count through dense expressions, semicolon chaining, hidden side
   effects, or meaningless helper extraction. Names must describe business intent.
7. Historical exceptions are recorded in `config/code_quality_baseline.json`. They may
   shrink or be removed, but may not grow. Adding an exception requires an architecture
   decision explaining why composition cannot be used.

## Change and verification rules

1. Refactors preserve public signatures, tool names, response shapes, error behavior,
   confirmation gates, idempotency semantics, and fallback order.
2. Separate behavior changes from mechanical refactors. Add or update focused tests
   before relying on the full suite.
3. New production behavior requires tests under `tests/`; browser contracts belong in
   `frontend/tests/` or blocking Playwright suites. Exploratory flows stay non-blocking.
4. Run Ruff lint and format checks, `scripts/check_code_structure.py`, focused tests,
   the complete local suite, and real PostgreSQL tests before merging to `main`.
5. Stage from an explicit reviewed file list. Never use `git add .`; never commit secrets,
   local environments, caches, generated browser output, or historical local run logs.
6. Every handoff identifies production, test, and demo/support files separately.
