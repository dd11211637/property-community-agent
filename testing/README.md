# Testing Support

本目录保存独立压测和本地验证支持，不得被生产模块导入，也不作为生产启动依赖。

- `seeds/`：只在 Alembic 迁移后写入可重复的演示数据。
- `reset/`：只允许重置本地 `property_agent_demo` 数据库的受控工具。
- `demo_app.py`、`compose.demo.yaml`：独立故障注入入口。
- `DEMO_ACCOUNTS.md`：账号、重置和故障开关说明。
- `e2e_api_smoke.py`：仅通过公开 HTTP API 执行四条业务闭环，并核对消息与 Agent。
- `outbox_failure_smoke.py`：在演示库验证消息重试耗尽和人工接管；运行时应先停止后端，避免发送器竞争。
- `agent_restart_smoke.py`：通过重启前后两个阶段验证待确认状态持久化，取消后不得创建工单。
- `scenarios/`：PRD F/A/S/R 的完整验收矩阵、证据入口和逐次执行记录。

## Controlled-read Agent Harness

```powershell
.\.venv\Scripts\python.exe -m testing.agent_harness
```

该离线 Harness 使用生产 Planner、Guardrail 和 Trace 契约以及测试目录下的确定性工具
输出，检查公告、账单、工单、巡检完成度、安防事件、已发布社区资料、工具失败、禁止写工具、事实路径和最终回复约束。它不会连接或修改生产数据库，
也不会被生产组合根导入。

## 可重复验收

无需预先配置 JWT、账号、house、数据库、release SHA 或本地 URL 的自包含功能闭环：

```powershell
.\.venv\Scripts\python.exe -m testing.local_functional_closure --output <report.json>
```

该命令在临时目录创建 SQLite 数据库和随机 JWT secret，装载可重复 demo 数据，并通过
进程内真实 ASGI 应用执行完整业务 API smoke 和 bounded load-harness smoke。临时数据库在
结束时销毁，令牌和 secret 不进入报告。缺少 `DEEPSEEK_API_KEY` 或
`MEMORY_EMBEDDING_API_KEY` 只会把对应真实外部 provider gate 标成 `NOT_RUN`。

```powershell
.\scripts\compose.ps1 Reset
.\scripts\compose.ps1 Up
.\.venv\Scripts\python.exe -m testing.e2e_api_smoke

cd frontend
npm run test:e2e
```

`e2e_api_smoke.py` 会创建真实演示业务记录；需要重复获得相同初始状态时，先执行受控重置。
故障脚本只允许连接本地 `property_agent_demo`，不得在生产数据库执行。

## 离线与故障演示

- 不配置 `DEEPSEEK_API_KEY` 时，Agent 使用确定性关键词路由；结构化页面完全不依赖模型。
- `DEMO_FAIL_MODEL=true` 会让模型主路径失败并进入关键词降级，不会把 Agent HTTP 接口整体改成 503。
- `DEMO_FAIL_BILLING_SOURCE=true` 只中断账单/规则读取，财务咨询仍可创建。
- `DEMO_FAIL_MESSAGE_TRANSPORT=true` 替换 Demo Outbox 发送适配器，消息中心和管理接口仍可查看失败状态。

离线演示前先拉取镜像并安装 Playwright 浏览器；断网后使用已构建的本地镜像启动。若模型不可用，按
“结构化四业务页面 → 消息中心 → 管理工作台 → Agent 关键词查询”的顺序演示，禁止把降级结果描述为
DeepSeek 正常响应。
