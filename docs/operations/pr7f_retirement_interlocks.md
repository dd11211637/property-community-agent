# PR7-F retirement interlocks

PR7-F adds proof tooling only. It does not remove `LegacyGraphEngine`, change the v1 fallback, or
claim production migration is complete.

```powershell
python -m testing.pr7f.retirement_gate static
python -m testing.pr7f.retirement_gate evaluate retirement-evidence.json
```

The static command intentionally returns `PENDING` while any production selector, fallback, or
dispatch path can yield v1. The combined gate additionally requires signed R5 evidence, a signed
representative observation with `new_v1_assignment_count == 0`, a complete drain inventory with
zero resumable/unknown/abandoned v1 pins, zero runtime-switch violations and blockers, a completed
rollback exercise, an approved non-v1 recovery strategy, approved retention handling, and an
independently signed human retirement approval bound to the exact release.

`agent_new_v1_assignment_total` is emitted for every new v1 assignment using bounded labels. A
single database snapshot is never accepted as dynamic-zero evidence. Until every interlock passes,
legacy remains executable and configured as the rollback target.
