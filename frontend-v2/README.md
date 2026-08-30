# Frontend V2 Release Candidate

Frontend V2 是完整产品 Release Candidate 的真实前端入口。它直接连接本仓库后端，覆盖真实认证、浏览器 Session、房屋上下文、核心业务、Agent SSE、结构化 facts、clarification、confirmation、handover 与长期 Memory。

## 运行边界

- `index.html` 是真实入口，不导入 Demo fixture，也不在 API 或 SSE 失败时返回伪成功。
- `demo.html` 只用于显式设计预览；其 in-memory adapter 和示例数据全部位于 `examples/`。
- 认证接口使用 direct response；业务与 Agent 接口使用 Envelope，两者分别解码。
- 浏览器会话仅使用版本化 `sessionStorage` 键 `property_agent_v2_session`；不实现 localStorage、refresh token、`/me` 或自动续期。
- actor、house、community、conversation、confirmation、idempotency 和 version 均以服务端为权威；模型文本不驱动业务成功 UI。
- `src/api/generated/schema.ts` 由 `docs/api/openapi.json` 生成，不手工编辑。
- 旧 `frontend/` 仍保留并独立回归；Frontend V2 不要求用户跳回旧前端完成正常流程。

## 本地开发验证

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

开发服务器真实入口为 `http://127.0.0.1:5174/`，设计预览为 `http://127.0.0.1:5174/demo.html`。

完整生产构建、API edge 和真实后端验收使用仓库根目录的 `compose.rc.yaml` 与 `docs/operations/full_product_rc.md`。
