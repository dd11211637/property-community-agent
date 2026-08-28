# Frontend V2 Skeleton

Frontend V2 是独立、未切换生产的下一代前端骨架。它建立契约、会话、作用域查询、角色能力、设计系统、领域组件和 Agent-native UI 的长期边界，不迁移真实业务流程。

## 唯一真源

- 输入文档：`D:\FRONTEND_V2_SKELETON_TRUE_SOURCE.md`
- SHA-256：`73BE6CB448865D8A31268C8332F1B96BF4858CEE7EFAA2C0C54553CADD36037C`
- 基线：执行时最新 `origin/main`

如输入文档变化，必须显式版本化并重新评估本骨架；实现代码和本文档不能反向修改产品契约。

## 边界

- `src/`：生产级基础模块、接口、布局、共享 UI、领域 UI 和页面组合，不保存浏览器 Session，不包含真实业务成功 mock。
- `examples/`：可视化展示数据、演示认证适配器和应用入口。
- `tests/`：Vitest/Testing Library 的架构与行为测试。
- `e2e/`：基于构建产物和 Chromium 的独立视觉/响应式 Smoke。
- `src/api/generated/`：完全由 `docs/api/openapi.json` 生成，禁止手工编辑。

旧 `frontend/`、后端、数据库、Compose 和生产路由均不依赖本目录。

## 架构要点

- `SessionState` 只通过可替换的 `SessionStore` 暴露。Skeleton 使用内存实现，不决定真实令牌的持久化策略。
- 角色通过显式 capability mapping 进入 Resident、Operations 和 Admin 体验；未知角色 fail closed。
- TanStack Query 键强制包含 Actor、House，并可包含过滤器、会话和资源 ID。切房不会把旧房屋数据作为新房屋内容展示。
- HTTP Client 使用正式 `X-Current-House-ID`、Bearer JWT、request ID 和 idempotency header，保留 Envelope 与语义错误。
- OpenAPI 生成类型是后端 DTO 的唯一 TypeScript 来源；本地 ViewModel 只服务展示。
- 页面只组合共享 UI、领域组件和 Agent 结构化结果，不持有 HTTP 或存储细节。

## 命令

```powershell
cd frontend-v2
npm ci
npm run api:check
npm run lint
npm run typecheck
npm test
npm run build
npx playwright install chromium
npm run test:smoke
```

开发预览：

```powershell
npm run dev
```

打开 `http://127.0.0.1:5174`，使用 `resident / preview` 查看居民端，使用 `manager / preview` 查看运营端。所有会话均为内存状态，刷新后消失。

## 当前限制和下一边界

- 无真实登录、业务请求、Agent SSE、confirmation 执行或生产切换。
- 下一迁移边界是认证、房屋选择和 API 请求上下文；届时单独评审 access token、刷新和持久化安全策略。
