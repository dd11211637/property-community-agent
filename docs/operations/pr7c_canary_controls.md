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

The salt is never returned, logged, placed in telemetry, or committed. Nonzero configuration
fails closed when the salt is too short, the official v2 engine/saver is unavailable, the
model configuration is not approved, or another structural eligibility gate fails.

## Readiness and telemetry

At zero rollout, `/ready` reports `agent_v2_rollout.state=OPTIONAL_ZERO`; optional v2
unavailability does not falsely imply public v2 traffic. At nonzero rollout, readiness also
requires the configured runtime policy, compatible deployment, v2 engine, official saver,
accepted-head store, database, services, and approved model configuration. The assignment
policy starts with accepted-head unavailable and is refreshed only by the live `/ready`
probe, so a request arriving before a successful probe fails closed to the configured v1
fallback.

`agent_runtime_assignment_total` records only bounded runtime, eligibility reason, config
version, salt-version identifier, eligibility-policy version, and bucket decision class.
It excludes the raw bucket, secret salt, actor/community/conversation IDs, prompts, and
Memory.

## Explicit change and rollback

`RolloutControl.apply` has no scheduler or time-based promotion path. An increase requires an
explicit approval flag and a bounded operator/change reference. The audit event records old
and new basis points, old and new config versions, reason, operator reference, and UTC time.

Rollback calls `rollback_to_zero`, which changes only the future assignment configuration.
It does not rewrite persisted runtime pins, checkpoints, pending confirmations, or accepted
heads. R1 remains prohibited until protected certification passes on the exact candidate
release and a human explicitly approves rollout.
