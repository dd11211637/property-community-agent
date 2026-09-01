# Observation-driven ReAct Capability Gap Matrix

This matrix is the versioned allowlist and rollout record. Runtime authority remains in
the capability registry, policy engine, trusted `RuntimeContext`, and Application Services.

| Domain | Goal / Action | Capability | Mode | Governance | Gap status |
|---|---|---|---|---|---|
| Repair | Search current-house work orders | `repair_list` | READ | domain + house scope | Closed: location, category, assigned-to-me filters; description and appointment state returned |
| Repair | Inspect one work order | `repair_get` | READ | domain + house scope | Closed |
| Repair | Create after equivalent-order pre-read | `repair_create` | WRITE | HITL, idempotency, same-goal pre-read | Closed |
| Inspection | Search tasks or security events | `inspection_list` | READ | community/role scope | Closed: point/location filters and original status/risk/assignee/version facts |
| Inspection | Read task or event | `inspection_get_task`, `inspection_get_event` | READ | community/role scope | Closed |
| Inspection | Create/start/record/submit | `inspection_create`, `inspection_start_task`, `inspection_add_record`, `inspection_submit_records` | WRITE | HITL + version + idempotency | Closed |
| Security | Report or dispose event | `security_event_create`, `security_event_submit_disposal` | WRITE | HITL + deterministic risk floor | Closed |
| Security | Close high-risk event | `close_high_risk_event` | WRITE | Human-only | Intentionally human-only |
| Billing | List/detail/rule query | `billing_query` | READ | house/role scope | Closed: rule name, parameters, version, validity returned |
| Billing | Open consultation when rule absent | `billing_consult` | WRITE | HITL + idempotency + absence observation | Closed |
| Announcement | Search/get/knowledge | `announcement_list`, `announcement_get`, `community_knowledge_search` | READ | community/role scope | Closed: bounded text query |
| Announcement | Draft/revise | `announcement_draft`, `announcement_revise` | READ/model | bounded inputs, no authority | Closed |
| Announcement | Save/publish/schedule | `announcement_create_draft`, `announce_publish`, `announcement_schedule_publish` | WRITE | HITL + version + approval + idempotency | Closed |

Rollout defaults to `repair,inspection` through `AGENT_REACT_DOMAINS`. Billing and
announcement code paths remain deployable but disabled until their acceptance gates pass.
