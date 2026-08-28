# PR7-E v1 drain tooling

The inventory reads trusted database state and emits grouped lifecycle counts without actor or
house identifiers. `UNKNOWN` and `ABANDONED_CANDIDATE` remain resumable and block retirement.
`WAITING_CONFIRM` is always live and inactivity never expires an open approval or pending
checkpoint.

```powershell
python -m testing.pr7e.v1_drain inventory --inactive-days 30
python -m testing.pr7e.v1_drain expire config/pr7e_v1_drain_policy.example.json
```

The committed policy is non-operational. Expiry requires a valid Ed25519 signature from the same
independent approval authority as PR7-C/D. The command defaults to dry-run; a write additionally
requires `--execute --confirm-policy-version <exact-version>`. Each transaction is capped at 100
records, locks candidates with `SKIP LOCKED`, stops on inconsistent state, preserves checkpoints
and history, and records an idempotency key plus policy version on the conversation. This PR does
not run the command against production.
