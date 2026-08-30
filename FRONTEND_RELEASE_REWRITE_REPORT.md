# Frontend Release Rewrite Report

## Release verdict

```text
BASE_SHA = f7853a1205d5164cb60c7c3435e4a9f26013c9e3
FINAL_SHA = c9a343a842cf5b5b7d383fd9a1f72cd191b75ca2
BRANCH = codex/frontend-release-rewrite

BACKEND_CHANGED = NO
API_CHANGED = NO

PAGES_REWRITTEN = /login, /, /repairs, /billing, /announcements, /inspection, /messages, /admin
SHARED_COMPONENTS = role-aware AppShell, RoleOverview, AsyncState, StatusBadge, AgentContextPanel, display mapper, role workspace mapper
ROLE_AWARE_LAYOUT = PASS

RESIDENT_UX = PASS - 围绕当前房屋、报修、账单、公告与安全上报组织，隐藏运营入口
MAINTENANCE_UX = PASS - 首页首先呈现今日任务、优先处理队列和现场业务入口
ADMIN_UX = PASS - 首页首先呈现待决策、失败消息、高风险事件与服务支撑状态
AGENT_UX = PASS - Agent 融入角色首页，保留真实 SSE、写操作确认、取消、恢复与结果反馈

RAW_UUID_VISIBLE_PRIMARY_UI = NO
RAW_ENUM_VISIBLE_PRIMARY_UI = NO

FRONTEND_UNIT = PASS (33/33)
FRONTEND_LINT = PASS
FRONTEND_BUILD = PASS
PLAYWRIGHT_E2E = PASS (26/26)

BEFORE_SCREENSHOTS = 12 files in docs/frontend/screenshots/before/
AFTER_SCREENSHOTS = 12 files in docs/frontend/screenshots/after/

BLOCKER = 0
HIGH = 0
MEDIUM = 0
LOW = 0

FRONTEND_RELEASE_REWRITE = PASS
NEXT_STEP = FRONTEND_VISUAL_REVIEW
```

`FINAL_SHA` 是完成全部实现、测试与截图复核的被测代码提交；本报告随后作为纯文档提交加入同一分支。

## Design and experience decisions

- 使用共享 design tokens 与克制的中性色系统，但按住户、现场人员、管理员分别提供住户服务、任务执行、运营决策三种工作空间。
- 首页不再是同一 Dashboard 换数字：住户先看“家里发生什么”，维修人员先看“今天处理什么”，管理员先看“哪些事项需要决策”。
- 报修采用任务卡、详情工作区与进度时间线；账单突出本期金额与费用组成；公告区分居民信息流和运营审核队列；巡检/安防按权限拆分视图。
- 用户可见状态、意图、服务集成与业务引用统一经过 presentation mapper；完整 UUID、raw enum、后端字段名不进入主要界面。
- Agent 保留真实后端能力和确认门禁，并以建议、确认、结果、错误、恢复中的业务反馈呈现，不伪造新能力。
- 主要数据页均提供 skeleton loading、业务化 empty state、可重试 error state；写操作保留确认、pending 与可恢复错误反馈。
- 支持 150–250ms 的有限反馈动效、清晰 focus-visible，并在 `prefers-reduced-motion` 下压缩为近零时长。

## Verification evidence

| Gate | Result | Evidence |
|---|---:|---|
| Frontend unit | PASS | 8 files, 33 tests passed |
| ESLint | PASS | `npm run lint` exit 0 |
| TypeScript + Vite build | PASS | 1,759 modules; JS 319.50 kB, CSS 28.73 kB |
| Formal community flows | PASS | Chromium, 26/26, one worker, real Docker stack |
| Release UI checks | PASS | 3/3: 375/768/1024/1440 overflow, mobile menu, role isolation, internal-field hiding, reduced motion |
| Visual evidence capture | PASS | 1/1 capture job; 12 AFTER screenshots |
| Code structure | PASS | `scripts/check_code_structure.py` |
| Backend production diff | PASS | `src/` and `alembic/` diff is empty |
| OpenAPI diff | PASS | unchanged; SHA-256 `9C77E761A8A845AE28CD5D578EEB3707A5EA31E378D239EB98C63FDC8D6D78AC` |

## Final self-check

1. Resident experience resembles an admin backend: **NO**.
2. Maintenance workers immediately see today's work: **YES**.
3. Administrators immediately see actionable items: **YES**.
4. Large tables remain the only primary expression: **NO**.
5. Full UUID remains visible to ordinary users: **NO**.
6. Write actions expose waiting, confirmation, success or failure feedback: **YES**.
7. AI remains an isolated chat box: **NO**.
8. The three roles have distinct information architecture: **YES**.
9. Spacing, hierarchy and scale establish a clear rhythm: **YES**.
10. Decorative motion was added without purpose: **NO**.

## Change classification and delivery boundary

- Production code: `frontend/src/**` only; real runtime presentation, validation feedback, routing shell and API adaptation.
- Test code: `frontend/tests/**`, `frontend/e2e/community-flows.spec.ts`, `frontend/e2e/release-ui.spec.ts`.
- Support/evidence: `design-system/default/MASTER.md`, `docs/frontend/**`, `frontend/e2e/visual-evidence.spec.ts`, this report.
- Demo code: none.
- No backend, database, Alembic, Agent runtime, permission semantics or API contract changes were made.
- No merge, push or deployment was performed. Delivery stops on `codex/frontend-release-rewrite`.

## Commit sequence

1. `97238ab` `feat(frontend): establish release design system`
2. `506ce94` `feat(frontend): introduce role-aware application shell`
3. `9e97b98` `feat(frontend): redesign role-specific business experiences`
4. `8d6e5b5` `test(frontend): certify rewritten release experience`
5. `c9a343a` `fix(frontend): humanize agent history intent labels`
