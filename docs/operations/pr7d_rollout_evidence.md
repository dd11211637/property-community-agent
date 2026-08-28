# PR7-D rollout evidence

PR7-D adds a read-only promotion gate. It does not change deployment configuration or increase
rollout. The command below validates one immutable evidence artifact against the same certified
model identity and trusted Ed25519 approval authority used by PR7-B/PR7-C:

```powershell
python -m testing.pr7d.rollout_gate config/pr7d_rollout_evidence.example.json
```

The committed example is deliberately `PENDING`. A `PASS` requires an exact release SHA,
stage-correct basis points, complete real observation duration and turn counts, zero incidents,
all mandatory release gates at `PASS`, durable evidence references, certified provider/model/
prompt/fingerprint identity, provider readiness, verified baseline evidence, and a signature from
the independently controlled approval authority. Missing real evidence returns `PENDING`; the tool
never fabricates it and never activates rollout.

R1 through R5 remain separate operational decisions. Their mandatory minimums are encoded from
the PR7 Stage Contract: R1 24h/200 turns, R2 48 additional hours/1,000 cumulative turns, R3 72
additional hours/5,000 cumulative turns, R4 explicit 100% approval, and R5 14 days/10,000 turns.
