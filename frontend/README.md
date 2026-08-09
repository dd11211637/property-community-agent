# Frontend

响应式 Web 前端入口，面向住户端、物业工作台和统一智能体对话入口。

首期页面范围：

- 登录与身份切换
- 报修列表、详情、创建和状态操作
- 公告列表、草稿、审核与发布
- 账单只读查询与财务咨询
- 巡检计划、记录和安防事件
- 智能体对话、确认卡片和人工接管

## 技术栈与边界

- React、TypeScript、Vite、React Router
- 生产页面统一通过 `src/api/client.ts` 访问后端，不包含 mock、fixture 或固定业务数据
- 登录令牌和当前房屋只作为请求凭据传递；权限仍由后端校验
- 写操作先展示确认卡片，并通过 `Idempotency-Key` 调用业务接口
- `VITE_ENV_LABEL` 非 `production` 时，页面固定显示模拟环境标识

## 本地启动

```powershell
cd frontend
npm install
npm run dev
```

Vite 默认将 `/api` 和 `/health` 代理到 `http://127.0.0.1:8000`。其他环境可通过
`VITE_API_BASE_URL` 指定 API 地址。

## 质量检查

```powershell
npm run lint
npm test
npm run build
```

当前后端未提供的登录、公告、财务咨询、消息、Agent 和 `/ready` 接口会显示真实错误状态，
不会回退到浏览器内模拟成功。对应接口完成后可直接按 `src/api/contracts.ts` 联调。
