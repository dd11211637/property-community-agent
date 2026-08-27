# PR7-C canary controls

PR7-C installs the control plane for future v2 assignment. It does not authorize or start
a canary. The committed default remains `0` basis points, so public new conversations are
pinned to v1 unless a later, explicitly approved deployment configuration changes it.

## Assignment boundary

Only a not-yet-persisted conversation is assigned. The application holds the existing turn
lease, evaluates trusted structural eligibility, computes a secret-keyed stable bucket from
the trusted community, actor, and conversation identities, and inserts `runtime_version` with
the conversation. Existing `ACTIVE`, `WAITING_CONFIRM`, `HANDOVER`, and restarted
conversations always use the persisted pin.

Clients, prompts, model output, Memory, request slots, and checkpoints do not enter the
assignment policy. The public request schema has no runtime selector.

## Configuration

The server-owned configuration is:

| Environment name | Contract | Default |
| --- | --- | --- |
| `AGENT_V2_NEW_CONVERSATION_ROLLOUT_BASIS_POINTS` | Integer from 0 through 10000 | `0` |
| `AGENT_V2_ROLLOUT_SALT` | Secret with at least 32 bytes when rollout is nonzero | empty |
| `AGENT_V2_ROLLOUT_SALT_VERSION` | Bounded non-secret identifier | `unconfigured` |
| `AGENT_V2_ROLLOUT_CONFIG_VERSION` | Auditable config version | `pr7c-default-v1` |
| `AGENT_V2_ELIGIBILITY_POLICY_VERSION` | Auditable policy version | `pr7c-eligibility-v1` |
| `AGENT_V2_NEW_CONVERSATION_FALLBACK_RUNTIME` | Safe ineligible-new fallback | `v1` |
| `AGENT_V2_EMERGENCY_STOP` | Stops future v2 assignment | `false` |
| `AGENT_V2_MODEL_CONFIG_APPROVED` | Explicit approved provider/model/prompt fact | `false` |
| `RELEASE_SHA` | Exact 40-hex deployed Git commit SHA verified at startup | empty |
| `ROLLOUT_ACTIVATION_MANIFEST_PATH` | Path to the deployment-provided activation manifest | `config/rollout_activation_manifest.json` |

The salt is never returned, logged, placed in telemetry, or committed. Nonzero configuration
fails closed when the salt is too short, the official v2 engine/saver is unavailable, the
model configuration is not approved, or another structural eligibility gate fails.

## Readiness and telemetry

At zero rollout, `/ready` reports `agent_v2_rollout.state=OPTIONAL_ZERO`; optional v2
unavailability does not falsely imply public v2 traffic. At nonzero rollout, readiness also
requires the configured runtime policy, compatible deployment, v2 engine, official saver,
accepted-head store, database, services, and approved model configuration.

The accepted-head gate is a **freshness-bounded snapshot**, not a sticky flag. The live
`/ready` probe records a timestamped observation; a new v2 assignment is authorized only when
that snapshot is both fresh (within the readiness TTL) and healthy. A missing, expired, or
unhealthy snapshot fails closed to the configured v1 fallback — a single historical probe can
never authorize v2 indefinitely, and `/ready` is never an execution authority. Pinned
existing `v1`/`v2` conversations are unaffected: they always follow the persisted runtime.

`agent_runtime_assignment_total` records only bounded runtime, eligibility reason, config
version, salt-version identifier, eligibility-policy version, and bucket decision class.
It excludes the raw bucket, secret salt, actor/community/conversation IDs, prompts, and
Memory.

## Activation audit (production rollout change control)

A non-zero rollout can become active **only** through the `activate_rollout_control` boundary,
and only when a deployment-provided `RolloutActivationManifest` is `APPROVED` and its complete
identity is verified against the running configuration. A fresh process that starts directly
at a non-zero rollout without a fully verified approved manifest fails closed — the
audit/release identity can never be silently bypassed. A rollout of zero basis points needs no
manifest and is returned as-is.

The model/provider/prompt approval identity is **not** operator-supplied. It is derived from the
single shared production contract `property_agent.agent.model_release.ModelReleaseIdentity`
(`primary_provider` = the CERTIFIED DeepSeek contract — never dynamically swapped to whichever
provider answered a request; `model` from `settings.deepseek_model`; `provider_config_version`;
`provider_config_fingerprint` = SHA-256 of the canonical non-secret certified provider execution
contract: primary_provider / normalized base_url / model / connect|read|total timeouts / bounded
retry `max_attempts` + `retry_policy_version` / `fallback_enabled` / `fallback_policy_version` /
`provider_response_config_version`); `prompt_contract_version`;
and the verified `model_release_evidence_reference`). PR7-B certification metadata consumes the
same source of truth, so there is exactly one model-configuration authority.

`primary_provider_ready` (DeepSeek credential readiness) is an independent runtime eligibility
condition, NOT part of the signed rollout manifest: activation fails closed when it is False, so a
non-zero rollout can never be authorized while the certified DeepSeek primary cannot actually be
constructed (production would otherwise run the deterministic fallback). The certified fallback /
retry contract (`fallback_policy_version = deepseek-to-deterministic-v1`) is part of the signed
identity and the provider-config fingerprint, so the certified fallback behavior is bound, not
merely a runtime detail.

The evidence reference is derived by the shared production validator
`property_agent.agent.model_release_approval.verify_committed_baseline_approval` from the
protected real-model baseline approval artifact (`config/pr7b_real_model_baseline_approval.json`):
only an `APPROVED` manifest whose artifact exists, is within the repo `config/` boundary, and
whose exact SHA-256 matches the recorded `artifact_sha256` produces the deterministic evidence
reference `pr7b-real-model:<artifact_sha256>`. While that baseline is `PENDING` the derived
reference is empty, so no non-zero rollout can pass — `"PENDING"` equality can never
self-authorize, and no source constant or operator string can claim approval.

The boundary enforces five independent controls on every non-zero activation:

1. **Exact release SHA (both sides).** The deployed `RELEASE_SHA` AND the manifest
   `identity.release_sha` must each be an exact 40-hex lowercase Git commit identity, and they
   must match exactly. Missing, abbreviated, or mismatched SHAs fail closed.
2. **Complete identity match + actual model binding.** Every field of `RolloutReleaseIdentity`
   must match the active `RolloutConfig` exactly: `rollout_config_version`, `rollout_basis_points`,
   `salt_version`, `eligibility_policy_version`, and `approved_fallback_runtime`. The
   `activation_manifest_version` must be the supplied `pr7c-activation-v2` schema version (v1 used
   `provider_class` and is rejected after this correction; a missing version is not silently
   defaulted); `approver_reference` must be a bounded opaque identifier and must not be a
   placeholder/`unconfigured` value; `approved_at` must be a valid UTC ISO-8601 timestamp. The
   manifest's `primary_provider`, `model`, `provider_config_version`, `provider_config_fingerprint`,
   `fallback_policy_version`, `prompt_contract_version`, `model_release_evidence_reference`, and
   `model_approval_id` are verified against the **actual** `ModelReleaseIdentity` — not against
   operator env and not against each other — so a deployment cannot self-approve a rollout by
   supplying matching-looking operator strings while the real model, certified provider, effective
   provider configuration (including the fallback/retry contract), or approval evidence differs.
   `primary_provider_ready` (DeepSeek credential readiness) must be True at activation — a
   non-zero rollout is never authorized while the certified DeepSeek primary cannot be constructed
   (no static `"deepseek"` constant can masquerade). `provider_config_fingerprint` must be an exact
   64-hex SHA-256 and equal the actual fingerprint, so a certification against one
   base_url/timeout/fallback-policy set can never run against a different one.
   `model_release_evidence_reference` and `model_approval_id` must both equal the SAME verified
   evidence reference (a single approval authority; the manifest's evidence field is hashed into the
   digest AND validated against the actual release). A non-zero rollout
   therefore remains fail-closed while `REAL_MODEL_BASELINE_APPROVAL=PENDING`.
3. **SHA-256 integrity.** `manifest_sha256` must be the lowercase 64-hex SHA-256 of the
   canonical approval payload (see digest generation below). The payload now also binds the
   actual model/provider/prompt facts and the explicit approved transition identity
   (`previous_rollout_basis_points`, `previous_rollout_config_version`), so the digest changes
   whenever those facts change. Any empty, malformed, or mismatched digest fails closed.
4. **No in-process promotion.** Runtime `RolloutControl.apply` may only *decrease* basis points
   (rollback). An increase is rejected with `ValueError`; there is no `promotion_approved`
   bypass. A higher rollout becomes active only through a brand-new `APPROVED` activation
   manifest crossing the real deployment boundary.
5. **Attributable, truthful audit.** Activation and rollback both emit a `RolloutAuditEvent`
   bound to the exact `release_sha`, a bounded `approver_reference`, and a bounded `change_reference`
   (no PII). `record_activation` emits the approved transition's **actual** previous/target
   basis points and config version (`previous_rollout_basis_points` / `previous_rollout_config_version`
   → target); it never synthesizes a previous state of zero except for a manifest that explicitly
   represents the initial zero → first-canary transition. The secret salt is never part of the
   manifest identity or the audit evidence.

### Operator digest generation

The deployment operator computes `manifest_sha256` from the manifest's immutable payload
(`identity` + `status`), **excluding** `manifest_sha256` itself. The canonical serialization is
deterministic JSON with sorted keys and compact separators
(`json.dumps(payload, sort_keys=True, separators=(",", ":"))`), hashed with SHA-256. In code:

```python
from property_agent.agent.runtime_rollout import (
    compute_manifest_sha256,
    parse_rollout_activation_manifest,
)
manifest = parse_rollout_activation_manifest(json.load(open(path)))
manifest.manifest_sha256 = compute_manifest_sha256(manifest)  # operator-side signing
```

The secret rollout salt is deliberately absent from the payload, so digest generation never
requires the salt. The example manifest (`config/rollout_activation_manifest.example.json`) is
committed as `PENDING` with placeholder strings and an empty digest; it is never operational.

## Explicit change and rollback

`RolloutControl.apply` has no scheduler, timer, or time-based promotion path. It can only
*decrease* `basis_points` (rollback); an increase raises `ValueError` and is never reachable
through an in-process authority. A higher rollout is reached only by deploying a new `APPROVED`
activation manifest and restarting through `activate_rollout_control`. The audit event records
old and new basis points, old and new config versions, reason, bounded `approver_reference`,
bounded `change_reference`, UTC time, and the established `release_sha`.

Rollback calls `rollback_to_zero`, which changes only the future assignment configuration.
It does not rewrite persisted runtime pins, checkpoints, pending confirmations, or accepted
heads. R1 remains prohibited until protected certification passes on the exact candidate
release and a human explicitly approves rollout.
