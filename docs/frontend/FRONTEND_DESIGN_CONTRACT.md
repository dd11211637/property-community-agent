# Frontend Design Contract

## Design principles

1. Role before module: each role sees the work and language that matters to it.
2. Attention before inventory: the first viewport answers what needs attention.
3. Business language before storage language: readable names, numbers, and Chinese
   states replace IDs, enums, versions, and backend field names.
4. Action with consequence: writes expose scope, confirmation, progress, and result.
5. AI in the service loop: Agent assistance is contextual and its tool actions are
   rendered as business UI, while the backend remains the result authority.
6. Calm by default: spacing and hierarchy create product quality; motion only
   explains state or spatial continuity.

## Visual foundation

The executable token source is `design-system/default/MASTER.md`. The accepted
direction is warm off-white canvas, ink-green primary text/navigation, restrained
role accents, border-led surfaces, and system typography. No glassmorphism,
marketing hero, purple AI gradient, or external font dependency is permitted.

### Typography

- System UI / Chinese UI sans stack; body 16px with 1.55 line height.
- Page titles 32–40px and section titles 20–24px create clear hierarchy.
- Metadata remains at least 13px and never carries essential information alone.
- Uppercase English eyebrow labels are optional tertiary context, never the title.

### Spacing and surfaces

- 4/8px base grid with section rhythm of 24/32/48px.
- Major surfaces use 16px corners; compact controls 8px; chips are pill-shaped only
  when their semantics are status/filter, not as a universal decoration.
- Borders and alternating quiet surfaces establish groups. Shadows are reserved for
  dialogs, drawers, and an actively elevated detail pane.

### Status system

All status/category/role/action labels resolve through the central display mapper.
States use text + optional icon + semantic color. Unknown values render as a safe
human fallback without exposing raw enum text in primary UI.

## Navigation model

### Resident

`概览` → `我的报修` → `账单` → `社区公告` → `消息` with a clearly available
`社区助手` entry. Inspection management and admin workspace are absent.

### Maintenance

`今日任务` → `维修工单` → `巡检与事件` → `消息` → `社区助手`. Billing and
resident announcement management are absent unless the server role genuinely
authorizes and the view has a task purpose.

### Admin

`运营概览` → `工单调度` → `公告运营` → `巡检安防` → `消息` → `管理工作台`
with Agent as an operational assistant, not the landing destination.

Unknown roles fail closed to the smallest safe navigation set.

## Role-aware home hierarchy

- Resident primary: current service state and one clear next action. Secondary:
  current bill, latest announcement, unread messages. Tertiary: Agent prompt.
- Maintenance primary: assigned work that can be acted on now. Secondary: urgent
  queue and progress counts. Tertiary: messages and Agent assistance.
- Admin primary: pending decisions, failed delivery, and high-risk exceptions.
  Secondary: workload totals and operational entry points. Tertiary: integration
  health and Agent assistance.

## Interaction patterns

- Detail uses a side panel when it preserves list context; long independent tasks may
  use a route. Every panel has a heading, close button, and stable focus behavior.
- Destructive or consequential writes use confirmation. Buttons disable and show
  pending copy while submitted; errors are announced and recoverable.
- Skeletons preserve the expected structure. Empty states explain meaning and offer a
  valid next action. Error states include retry and development-only technical detail.
- Desktop targets 1440/1366/1280; 1024 remains fully usable. Under 760px the sidebar
  becomes a drawer and multi-column layouts stack without horizontal scrolling.

## Component inventory

- Production shared UI: role-aware `AppShell`, `PageHeader`, `StatusBadge`,
  `Skeleton`, `EmptyState`, `ErrorState`, `DetailPanel`, `Timeline`, action and
  confirmation dialogs, contextual Agent entry, and central display mapper.
- Domain components: resident service snapshot, maintenance task queue, admin
  attention queue, bill summary, announcement feed/editorial queue, repair cards,
  operational event cards, Agent message/action/result blocks.
- Test/support: repeatable Playwright visual-evidence capture and semantic locator
  updates. No mock/demo/fixture code enters `frontend/src`.

## Page hierarchy

1. Role context and page purpose.
2. Primary attention/action region.
3. Current business state and next steps.
4. History, supporting detail, or health evidence.
5. Contextual Agent assistance only where existing endpoints support it.

## Accessibility and performance gates

- Semantic controls, labels, keyboard navigation, visible 2px focus, 4.5:1 text
  contrast, non-color status cues, and `aria-live`/`role=alert` for async results.
- Respect reduced motion and do not hide focused controls behind sticky UI.
- No new visual dependency. Lucide remains the single icon family. Route/page code
  stays statically simple; long lists can be paginated/limited by existing APIs.
