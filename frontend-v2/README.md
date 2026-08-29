# Frontend V2 核心业务运行时

Frontend V2 是独立、尚未切换生产的下一代前端。它使用真实认证、浏览器 Session、房屋上下文和真实业务 API，覆盖 Repairs、Billing、Community、Operations、Messages 与只读 Admin。Agent、SSE 和 Memory 仍未迁移。

## 真源与基线

- 真源：`D:\FRONTEND_V2_CORE_BUSINESS_MIGRATION_TRUE_SOURCE.md`
- SHA-256：`9786EE912EE78649A624E1EFBAFD33F4361604AF45CA3BC4336FBC01FE8A26D7`
- 精确父提交：`8e842ec0c12d89a0d69f05d92b23e7c6d5179891`
- 实现分支：`codex/frontend-v2-core-business-migration`

## 运行边界

- `index.html` 是真实运行入口：真实认证、单键版本化 `sessionStorage`、六个业务垂直流、集中 401、确认/幂等/版本冲突和作用域缓存协调。
- `demo.html` 是明确标识的设计预览入口：只使用 `examples/` 中的 in-memory adapter 和 Demo 数据。
- 正式入口不导入 Demo fixture，不在真实查询失败时回退为伪业务记录。
- `src/api/generated/schema.ts` 完全由仓库 OpenAPI 生成，不手工编辑。
- 旧 `frontend/`、后端、数据库、迁移、Compose 和生产路由均不依赖本目录。

浏览器会话只使用键 `property_agent_v2_session`，记录版本为 `1`。仓库没有 `/me`、refresh token 或独立 Session 验证协议，因此这里不实现续期或持久登录。

多数非 Billing 业务响应的 OpenAPI 契约仍为通用 `Envelope.data: unknown`；V2 在传输边界用严格运行时解析器建立展示模型，畸形响应 fail closed。咨询列表只返回当前 Actor 的数据；财务/经理处理其他咨询时只能使用 Messages/Admin 提供的真实资源 ID 深链进入详情。

业务查询按真实边界隔离：居民账单和当前房屋报修使用 Actor + House；运营、公告和 Admin 使用 Actor + Community；Messages/咨询依照 Actor/Community 契约且不随房屋切换误清。确认 Token 不持久化，参数变化即作废；版本冲突会重新获取权威详情并要求重新确认，不使用旧版本静默重试。

## 开发与验证

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

真实入口：`http://127.0.0.1:5174/`

设计预览：`http://127.0.0.1:5174/demo.html`

```powershell
npm run dev
npm run dev:demo
```

下一阶段需另行授权；本目录不会自行开始 Agent、SSE 或 Memory 迁移，也不会切换生产路由。
