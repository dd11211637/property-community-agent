# 联调与验收状态

更新时间：2026-08-13。该记录对应未提交工作区，最终提交前必须重新执行质量门禁。

## 已验证

- Docker Compose：PostgreSQL、一次性迁移、种子、后端和构建后前端均可启动并通过健康检查。
- 真实 PostgreSQL：重建当前源码测试镜像后执行 439 项后端测试全部通过，无跳过；本地 SQLite/快速套件 436 项通过，3 项 PostgreSQL 专项按设计跳过。
- 数据库结构：当前演示库位于唯一 Alembic head `20260819_0001`，结构/类型/约束/索引/默认值的 `alembic check` 通过；已实测 `downgrade 20260818_0001 → upgrade head`。
- 浏览器：当前真实 Compose 组合环境完成默认 Playwright 26/26 回归。覆盖账单上下文续问、公告与社区资料可信边界、Agent、工单进度、多房屋、越权、报修返工、公告驳回/发布/撤回、财务咨询、巡检、安防退回/复核、高风险确认、消息和管理工作台；探索性 Agent 脚本使用独立配置手动运行，不计入阻断门禁。
- 前端：Vitest 27 项通过，ESLint 和生产构建通过；其中包含异步筛选请求的竞态回归测试。
- API 业务烟测：报修返工、Billing 咨询、巡检、安防事件、公告审核发布、消息、审计和 Agent 查询均通过公开 API 落入真实数据库。
- Agent：四类业务各 10 条表达通过确定性路由；歧义、高风险、越权、字段缺失、信息冲突、模型和工具失败各 5 条数据已固化。确认前不写、取消、参数指纹和模型降级有自动化覆盖；待确认页面刷新恢复、确认只创建一次以及真实 backend 容器重启后恢复均已实测。
- Agent 模型：浏览器真实组合入口已验证；显式业务关键词会纠正模型返回的 `UNCERTAIN`/冲突意图，模型仍负责非可信槽位提取。外部模型的单次原始分类结果不作为确定性验收证据。
- 消息：生产 Outbox 后台发送器已装配；失败达到上限后生成客服人工接管单的真实数据库烟测通过。
- 故障入口：Demo 模型失败开关已在 Docker 中验证返回 200 并给出明确关键词降级消息，随后已恢复标准生产组合入口。
- 依赖：项目 Python 虚拟环境 `pip check` 通过；前端完整 npm audit 为 0 项已知漏洞。
- 生产镜像：后端以非 root 用户运行，runtime 镜像不包含 `testing/`；生产配置仍为开发默认值时会拒绝启动。
- 登录安全：失败计数与 15 分钟锁定状态持久化到 PostgreSQL，按规范化用户名和可信来源 IP 隔离；失败、阻断、成功审计均已验证落库。
- API 与交付门禁：OpenAPI 已导出并加入漂移检查；GitHub Actions 覆盖 Ruff/compileall/pip check、代码结构与生产依赖边界、SQLite、真实 PostgreSQL 无跳过、前端 lint/Vitest/build 和 Docker Compose Playwright。
- 可观测性：生产访问日志为结构化 JSON，包含 request_id、路由模板、状态码和耗时；不记录请求体、令牌或动态资源 ID，慢请求和 5xx 提升为告警级别。

## 尚未完成的企业级验收

- 消息中心和管理工作台的正常浏览器链路已覆盖；消息发送器竞争、失败重试耗尽和人工接管仍使用独立真实数据库烟测，不在浏览器中直接控制后台发送器。
- Nginx 已具备安全响应头和入口限流，后端具备持久化登录锁定及 OTLP/OpenTelemetry Agent 信号；外部 Collector/跨服务链路和集中告警尚未在生产环境验收，TLS 证书终止与备份恢复演练仍待完成。
- 登录表允许“同一用户名存在于不同社区”，但当前登录请求没有社区字段。本轮已对重名账户 fail-closed，正式多社区产品仍需选择“全局用户名唯一”或“登录时选择社区”的产品契约。
- 当前 JWT 为不可主动吊销的 8 小时访问令牌；角色与房屋授权会实时重载，但生产化仍应增加短期 access token、refresh token 轮换和会话撤销表。
- DeepSeek 密钥如曾通过聊天或其他明文渠道暴露，必须在供应商控制台轮换；仓库验收不能替代密钥轮换。

## F/A/S/R 对照

逐项名称、可执行测试/烟测入口和最近一次执行记录见 [`testing/scenarios/`](../testing/scenarios/README.md)。

| 范围 | 结果 | 证据口径 |
|---|---|---|
| F-01—F-06 | 通过 | 公开 API 全流程烟测 + 业务服务状态机测试 + PostgreSQL 记录 |
| A-01—A-04 | 通过 | Agent HTTP、Graph、工具适配器自动化测试；持久化确认恢复实测 |
| S-01—S-04 | 通过 | 跨房屋/社区、RBAC、受保护高风险动作、参数变化与审计脱敏测试 |
| R-01—R-04 | 通过 | 幂等重试、账单源失败、消息状态分离/人工接管、通知失败与模型降级测试 |

## 范围外部依赖

- 未接入真实短信、语音或外部物业账单源；这些能力不在本阶段范围，演示环境只保留站内消息和明示故障适配器。

## 复现命令

```powershell
.\scripts\compose.ps1 Test
.\.venv\Scripts\python.exe -m pytest --basetemp=.pytest-tmp-full
.\.venv\Scripts\python.exe -m ruff check src tests testing
.\.venv\Scripts\python.exe scripts/check_code_structure.py
.\.venv\Scripts\python.exe -m compileall -q src
.\.venv\Scripts\python.exe -m pip check

docker compose --profile testing run --rm postgres-tests
docker compose run --rm migrate alembic check
.\.venv\Scripts\python.exe -m testing.agent_restart_smoke prepare --state testing\agent-restart-runtime-state.json
# 重启 backend 并等待健康后执行 verify；脚本会删除状态文件
.\.venv\Scripts\python.exe -m testing.agent_restart_smoke verify --state testing\agent-restart-runtime-state.json

cd frontend
npm run lint
npm test
npm run build
npm run test:e2e
# 非阻断的探索性 Agent 浏览器脚本
npm run test:e2e:exploratory
```

全业务 API 烟测会写入演示库，执行前应先运行 `.\scripts\compose.ps1 Reset`。
