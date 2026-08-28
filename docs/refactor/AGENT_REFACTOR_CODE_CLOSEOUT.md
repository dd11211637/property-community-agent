# Agent refactor code closeout

## Status at 2026-08-28

This report records the reviewable PR7 implementation stack. It does not certify a
production migration or replace exact-head GitHub checks and independent approval.

```text
AGENT_REFACTOR_CODE_COMPLETE = NO
PRODUCTION_MIGRATION_COMPLETE = NO
PUBLIC_V2_ROLLOUT = 0_PERCENT
LEGACY_RUNTIME_RETAINED = YES
```

`AGENT_REFACTOR_CODE_COMPLETE` remains `NO` until PR7-C, PR7-D, PR7-E, and PR7-F are
merged in order and post-merge CI passes on `main`.

## Ordered implementation stack

| Stage | PR | Exact branch head | Current status |
| --- | --- | --- | --- |
| PR7-C | #27 | `696e130378002b9955aed28cfcebfb4199a908ee` | Ready; exact-head CI passed; Code Owner approval pending |
| PR7-D | #28 | `f32f6f66ad91dfe27592ecf3d9db91af1061e642` | Draft stacked on PR7-C; exact-head CI passed |
| PR7-E | #29 | `e48fe41a98d210c3a69c5f517c8fe2f06b091edc` | Draft stacked on PR7-D; exact-head CI passed after schema-drift repair |
| PR7-F | #30 | `0a03d29d8fe630ba9173c7c5f6c361f7570b107f` | Draft stacked on PR7-E; exact-head CI passed |

The stack must merge C to D to E to F. After each parent merge, the child must be based
on the latest `main`, its effective diff and exact head must be reviewed again, and all
required checks and independent approval must pass. History rewrite and protection-rule
bypass are prohibited.

## Implemented boundaries

- PR7-C provides fail-closed, signed approval and zero-percent canary controls.
- PR7-D provides versioned rollout evidence, promotion gates, rollback receipts, and a
  validation CLI; it does not activate rollout.
- PR7-E provides the canonical drain classifier, database inventory, signed policy, and
  dry-run-first bounded executor; it does not drain production conversations or delete
  checkpoints/history.
- PR7-F provides static, dynamic, database, R5, rollback, and human-approval retirement
  interlocks; it does not remove `LegacyGraphEngine` or change the v1 fallback.

Production code lives under `src/` and the PR7-E Alembic migration. Tests live under
`tests/`. Operator CLIs, PENDING examples, and operational documentation are support
artifacts under `testing/`, `config/`, and `docs/`.

## Evidence and external gates

- PR7-C exact-head GitHub Quality Gates run `33139072627` passed backend, PostgreSQL
  zero-skip, frontend, and browser E2E.
- PR7-D exact-head GitHub Quality Gates run `33139354054` passed the same four jobs.
- PR7-E local focused verification passed after declaring the migration index in ORM
  metadata; exact-head GitHub Quality Gates run `33140234496` then passed all four jobs.
- PR7-F local verification passed Ruff, format, structure, compileall, pip check, diff
  check, 942 pytest tests, frontend lint, 30 frontend tests, and frontend build. The 35
  locally skipped PostgreSQL-dependent tests are not counted as PASS; GitHub zero-skip
  PostgreSQL CI and all other Quality Gates passed in run `33140250492`.
- PR7-F's static scanner and PENDING example both return `PENDING`, as required while
  v1/Legacy dependencies and real production evidence remain.

Real R0-R5 observations, representative production traffic, SLO windows, rollback
exercise evidence, production drain, retirement approval, and Code Owner approval are
external gates. None is synthesized or inferred from local/CI success.
