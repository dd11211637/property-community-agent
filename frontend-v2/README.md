# Frontend V2 authenticated foundation

Frontend V2 是独立、尚未切换生产的下一代前端。本阶段接入真实认证、浏览器会话和房屋上下文，但不迁移任何业务垂直流或 Agent/SSE。

## 真源与基线

- 真源：`D:\FRONTEND_V2_AUTH_SESSION_HOUSE_TRUE_SOURCE.md`
- SHA-256：`D542785E2D76A9BA19FE7B4244790A7A7C74BE4DB760D9F3CA8D41C906B5DFB4`
- 精确父提交：`5cfa7ad72374603ecbe1ab1e3cd0ec262b4fe7e6`
- 实现分支：`codex/frontend-v2-auth-session-house`

## 运行边界

- `index.html` 是真实运行入口：真实 `/api/auth/login`、`/api/auth/house`、单键版本化 `sessionStorage`、集中 401 和作用域缓存清理。
- `demo.html` 是明确标识的设计预览入口：只使用 `examples/` 中的 in-memory adapter 和 Demo 数据。
- 正式入口中的报修、账单、社区、运营、消息和管理页面只显示“尚未迁移”占位，不展示 Demo 业务记录。
- `src/api/generated/schema.ts` 完全由仓库 OpenAPI 生成，不手工编辑。
- 旧 `frontend/`、后端、数据库、迁移、Compose 和生产路由均不依赖本目录。

浏览器会话只使用键 `property_agent_v2_session`，记录版本为 `1`。仓库没有 `/me`、refresh token 或独立 Session 验证协议，因此这里不实现续期或持久登录。

登录只返回房屋 ID。未通过真实房屋选择响应解析的房屋使用 `房屋 · <ID 前缀>` 中性标签；building、unit 和 room 仅来自 `/api/auth/house`。

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

下一阶段需另行选择一个中等风险的真实业务垂直流；不直接开始 Agent、SSE、confirmation 或写流程迁移。
