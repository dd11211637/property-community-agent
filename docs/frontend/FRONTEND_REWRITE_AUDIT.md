# Frontend Release Rewrite Audit

## Evidence boundary

- Canonical repository: `C:/Users/戴嘉兴/Desktop/华工/具身智能`
- Audited baseline: `f7853a1205d5164cb60c7c3435e4a9f26013c9e3`
- Runtime inspected: healthy Docker Compose frontend/backend/PostgreSQL stack.
- Contracts inspected: acceptance reports, `docs/api/openapi.json`, frontend API
  client/types, production E2E, unit tests, API adapters, and seed identities.
- Accounts inspected: `zhangsan`, `repair_worker`, `manager` with seed password.
- BEFORE evidence: `docs/frontend/screenshots/before/*.png` at 1440×1000.
- Historical acceptance claims remain historical; the rewrite gates will be run
  again and reported independently.

## Cross-cutting findings

1. One sidebar exposes the same destinations to every role, including resident
   billing and admin workspace links for maintenance staff. Authorization still
   protects data, but the information architecture does not express role intent.
2. The shared `/` route is an Agent workbench for all roles. It does not answer
   the resident's current service state, the worker's next job, or the manager's
   exception queue within three seconds.
3. Entity cards expose raw enums, versions, shortened internal IDs, assignee IDs,
   handover codes, integration keys, and English intent labels.
4. Entity list/card patterns repeat across most pages with weak primary/secondary
   hierarchy. The layout is technically clean but still reads as one admin tool.
5. The Agent has real confirmation and tool behavior, but the surrounding shell
   makes it feel like an isolated chat product rather than contextual service help.
6. Loading/error/empty components exist, but loading is primarily spinner/text and
   write-operation success feedback is not consistently persistent.

## Page and role audit

| Route / role | Current purpose and hierarchy | Problems / internal exposure | Keep | Rewrite | API dependency | Risk |
| --- | --- | --- | --- | --- | --- | --- |
| `/login` / all | Brand story left, login form right | Copy says demo account and demo placeholder despite release scope; CTA implies house selection even for staff | Labeled fields, password manager support, backend auth | Formal service-entry copy, concise trust statement, stable pending/error state | `POST /api/auth/login` | Low |
| `/` / resident | Conversation history → chat → context panel | Agent occupies whole product; house shown as short UUID before server label resolution; intent enums visible | Real conversation, memory, confirmation gate, facts | Resident service overview with contextual Agent entry and dedicated Agent workspace region | Agent conversations/messages/turns/memories plus list APIs | High |
| `/` / maintenance | Same chat workspace as resident | No immediate assigned-task priority; irrelevant resident shortcuts; no role identity | Real Agent endpoint where permitted | Today's task queue, urgency, next action, route to repair details | `GET /api/work-orders`, Agent endpoints | High |
| `/` / admin | Same chat workspace as resident | No exception summary or management decision priority | Agent handover capability | Exception-first overview with queues and admin workspace deep link | `GET /api/admin/dashboard`, Agent endpoints | High |
| `/repairs` / resident | Create form → work-order list → detail | Raw status/urgency/version; fallback to full ID; timeline actions raw; assignee UUID | Confirmation token, idempotency, timeline, review | Current repair spotlight, human status, progress timeline, secondary history | Work-order list/detail/timeline/actions/reviews | High |
| `/repairs` / maintenance | Same list/detail as resident | Task execution is not prioritized; same narrative and hierarchy | Available actions and optimistic version checks | Assigned-task workbench with urgent/current grouping and action-forward detail | Same work-order endpoints, staff list only for assign | High |
| `/repairs` / admin | Same list/detail plus assign action | Overall allocation context weak; assignee IDs exposed | Real assign/rework/verify actions | Operations queue presentation, staff names, decision-oriented detail | Work orders, staff, timeline, actions | High |
| `/billing` / resident | Bill cards then consultations | Bill status enum and rule metadata lead too early; current amount lacks dominant hierarchy | Real bill detail and consultation creation | Current amount/status hero, charge breakdown, history below, contextual Agent prompt only when supported | Bills/detail, billing consultations | Medium |
| `/announcements` / resident | Shared announcement list | Workflow states compete with content; raw categories/statuses | Published content and detail | Community information feed with importance/date/summary | Announcements list/detail | Medium |
| `/announcements` / admin | Same feed plus workflow actions | Draft/review/publish decision chain not visually clear | Real audience preview, version history, action gates | Editorial queue lanes and explicit next actions | Announcements, preview, versions, actions | High |
| `/inspection` / resident | Task and event columns | Resident sees internal inspection tasks and management-shaped lists | Manual security reporting with confirmation | Safety reporting entry and only resident-appropriate event feedback | Security events, confirmations | High |
| `/inspection` / maintenance/security | Shared dual-column operations view | Task priority and anomalies have equal weight; raw event/risk/action enums | Timeline and available actions | Role-relevant assigned tasks, risk emphasis, executable next step | Inspection tasks/events and action endpoints | High |
| `/inspection` / admin | Shared dual-column view | No exception/assignment hierarchy | Creation, grading, review controls | Oversight view with risk queue and inspection coverage | Same inspection/security APIs | High |
| `/messages` / resident | Filter row then database-like message list | Delivery internals, retry counters, handover codes, business enums exposed | Read/read-all behavior | Inbox hierarchy: unread first, context, summary, time, related destination | Message list/read/read-all | Medium |
| `/messages` / admin | Same message center | Operational delivery failures mixed with normal inbox | Failure evidence and handover data | Human-facing inbox here; delivery operations remain in admin workspace | Messages and admin dashboard | Medium |
| `/admin` / admin | Metrics → pending/high risk → failed → integrations | Raw status/source/queue/integration keys and backend health enums; all sections visually similar | Real aggregate and exception counts | Decision queue as primary, health as compact secondary evidence, human labels | `GET /api/admin/dashboard` | Medium |

## Contract constraints

- No backend, schema, runtime, capability, authorization, authentication, or API
  changes are authorized.
- Server session, role, house scope, confirmation, version, and idempotency remain
  authoritative.
- Existing production E2E behavior is preserved; locator changes may only follow
  semantic DOM changes, never reduced coverage.
- Demo-specific entry points, quick login, tours, promotional pages, or fabricated
  data are out of scope.
