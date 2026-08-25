# Agent observability, streaming, and SLO operations

PR7-A adds instrumentation and hardens the existing SSE surface. It does not certify a
production SLO, enable public v2 traffic, or change any business-authority decision.

## Runtime architecture

Each FastAPI application owns one OpenTelemetry `TracerProvider` and `MeterProvider`.
Traces use a batch span processor; metrics use a periodic reader; both export through
OTLP/HTTP. Providers are never installed globally and are flushed and shut down with the
application lifespan. Structured access logs include only safe request, trace, and span
correlation fields.

Configuration is server-owned:

| Environment variable | Meaning |
| --- | --- |
| `OTEL_ENABLED` | Enables the production providers and exporters. |
| `OTEL_SERVICE_NAME` | OTel resource service name. |
| `OTEL_EXPORTER_ENDPOINT` | Collector base URL; `/v1/traces` and `/v1/metrics` are appended. |
| `OTEL_EXPORT_INTERVAL_MS` | Periodic metric-export interval. |
| `RELEASE_SHA` | Optional release identifier on the OTel resource and turn spans. |
| `DEPLOYMENT_ENVIRONMENT` | Deployment environment resource value. |
| `AGENT_STREAM_MAX_CONCURRENCY` | Hard application-wide producer admission bound. |
| `AGENT_STREAM_SHUTDOWN_GRACE_SECONDS` | Bounded shutdown drain interval. |

Production validation rejects enabled telemetry without an exporter endpoint. In other
profiles, missing export configuration is explicitly `ENABLED_DEGRADED`; explicit disablement
is `DISABLED`. Export failure never changes a business result, but `/ready` exposes the sticky
bounded failure category. `/ready` separately verifies database connectivity, the required
accepted-head schema, runtime assembly, and reports optional embedding/Memory Writer state.
`/health` remains dependency-free liveness. Telemetry degradation does not by itself fail
application readiness.

## Correlation and privacy

Inbound W3C `traceparent` is extracted as correlation metadata only. Identity, authorization,
community/house scope, runtime pin, approval, versions, leases, and idempotency continue to
come from trusted server state. Actual `Conversation.runtime_version` (`v1` or `v2`) is used
on every turn.

Inbound `X-Request-ID` is untrusted. Only the opaque server format `req_` followed by 32
lowercase hexadecimal characters is echoed and used for telemetry correlation. Any other value
is replaced with a newly minted server ID before it can reach logs or spans. This header and W3C
trace context are correlation-only and never influence business authority.

Metric labels are limited to `runtime`, `operation`, `outcome`, `reason`, `specialist`,
`capability`, and `provider`, with bounded values. Conversation, run, request, actor, house,
Memory, token, and idempotency identifiers are forbidden metric dimensions. Trace attributes
use a separate allowlist. Prompts, messages, Memory content, raw model responses, tokens,
addresses, phone numbers, graph state, and planner reasoning are not emitted.

## Signal inventory

| Boundary | Counters / values | Latency histogram |
| --- | --- | --- |
| Agent/HTTP | `agent_request_total`, `agent_outcome_total`, `agent_http_request_total` | `agent_turn_duration_seconds`, `agent_http_request_duration_seconds` |
| Model logical operation | `agent_model_operation_request_total`, `agent_model_operation_outcome_total`, `agent_model_fallback_total` | `agent_model_operation_duration_seconds` |
| Model provider attempt | `agent_model_provider_request_total`, `agent_model_provider_outcome_total`, `agent_model_retry_total` | `agent_model_provider_duration_seconds` |
| Supervisor/capability | `agent_orchestration_total`, `agent_capability_request_total` | `agent_capability_duration_seconds` |
| Lease/fence | `agent_lease_operation_total`, `agent_conversation_busy_total`, `agent_stale_fence_rejected_total` | `agent_lease_operation_duration_seconds` |
| Checkpoint/accepted head | `agent_checkpoint_persist_total`, `agent_accepted_head_publish_total`, `agent_accepted_head_orphan_total`, `agent_checkpoint_conflict_total`, `agent_exact_cursor_resolution_total` | `agent_checkpoint_persist_duration_seconds`, `agent_accepted_head_publish_duration_seconds` |
| Approval | `agent_approval_operation_total`, `agent_approval_rollback_total` | `agent_approval_operation_duration_seconds` |
| Memory | `agent_memory_retrieve_total`, `agent_memory_result_count`, `agent_memory_writer_total`, `agent_memory_reindex_total`, `agent_memory_reindex_backlog` | `agent_memory_retrieve_duration_seconds`, `agent_memory_writer_duration_seconds`, `agent_memory_reindex_duration_seconds` |
| SSE | `agent_stream_total` (`final`, `failed`, `client_disconnect`, `progress_coalesced`) | `agent_stream_first_event_duration_seconds`, `agent_stream_duration_seconds` |
| Stream execution | `agent_stream_execution_total`, `agent_stream_active_producers` | `agent_stream_execution_drain_duration_seconds` |

`agent_boundary_duration_seconds` is an additional low-level boundary histogram. Existing
database-pool instrumentation is reused when supplied by the deployment; PR7-A adds no second
pool or metrics exporter.

DeepSeek physical attempts, retryable outcomes, deterministic fallback use, and the overall
logical model operation are separate facts. A successful fallback therefore records the primary
failure, fallback success, and logical `degraded_success`; it is never counted as DeepSeek
success. For v2, `checkpoint.persist` is emitted only around the official LangGraph saver `put`
boundary, while `accepted_head.publish` measures the later application CAS. For v1 the custom
accepted snapshot is one physical operation and uses the explicit operation label
`v1_accepted_snapshot`.

## SLO derivation

Use the Stage Contract window and thresholds; do not infer production compliance from local
tests. Suggested downstream calculations are:

- Agent infrastructure success: `1 - FAILED_INFRASTRUCTURE agent_outcome_total / all
  agent_outcome_total`. Clarification, waiting confirmation, handover, policy denial, and
  business rejection are structured outcomes, not infrastructure downtime.
- Start/resume success: group `agent_outcome_total` by `operation` and `runtime`.
- Accepted-head success: completed publications divided by all publish outcomes; any orphan
  signal is a correctness anomaly.
- Model structured success: primary successes divided by requests; track timeout, transport,
  schema, provider failure, retry, and fallback separately.
- Latency: compute p50/p95/p99 from the turn, model, capability, persistence, Memory, and SSE
  histograms. Turn `reason` distinguishes `simple` from `multi_step`; `operation=resume`
  isolates confirmation resumes, and outcome isolates `WAITING_CONFIRM` initial turns.
- Stream anomaly rate: failed plus disconnect terminals divided by all terminal/disconnect
  stream outcomes. Track `progress_coalesced` separately as backpressure pressure.
- Telemetry health: alert on sustained `/ready.components.telemetry.state ==
  ENABLED_DEGRADED` when telemetry is required for promotion.

## Actionable alerts

Page or block promotion for sustained provider outage, database saturation, accepted-head
failure/orphan signals, checkpoint CAS conflict bursts, lease loss or stale-fence rejection,
approval binding rejection/rollback anomalies, cross-scope or deleted-Memory safety audit
events, duplicate committed-write/idempotency audit violations, runtime-pin invariant
violations in correlated traces, and sustained SSE failure/disconnect/backpressure rates.
Do not page on individual clarification, `WAITING_CONFIRM`, expected policy denial, or a
single model retry.

## SSE contract

The existing `POST /api/agent/conversations/{conversation_id}/messages/stream` endpoint is
the only streaming surface. The lifecycle Runner still owns lease, heartbeat, engine,
checkpoint, accepted-head publication, conversation synchronization, Memory write, and
cleanup. A bounded delivery bridge only projects events.

Public progress is one of `planning`, `delegating`, `executing_capability`,
`waiting_confirmation`, or `finalizing`; internal graph node names are not API contracts.
Only provisional progress may be coalesced. A `final` projection is emitted only after the
canonical lifecycle has durably published and synchronized its accepted result. Execution
failure produces one bounded `failed` terminal and no success terminal. Client disconnect stops
delivery but does not cancel an in-flight canonical lifecycle or create a second retry store.
Producers are admitted through an application-owned fixed executor with no waiting queue and
remain operationally discoverable after delivery disconnect. Shutdown first stops admission and
waits the configured bounded grace interval before the LangGraph saver, telemetry providers, or
database engines are closed. The frontend stores only the final safe turn snapshot, never the
event replay.
