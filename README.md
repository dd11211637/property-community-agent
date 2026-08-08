# 物业社区管理智能体

面向住宅小区的 Web AI 协同平台，通过结构化业务页面和统一对话入口，连接住户、客服、
维修、财务、安保和物业管理者。

MVP 覆盖四条最小业务闭环：

- 报修处理
- 公告通知
- 费用查询与财务咨询
- 安防巡检

AI 负责意图识别、信息补全、只读查询、内容生成和操作建议。业务写入仍由后端执行身份、
权限、状态机、确认、幂等和审计校验。

## 当前状态

| 范围 | 状态 |
|---|---|
| 项目级目录与架构文档 | 已建立 |
| 报修 P0 后端闭环 | 已合并，等待生产 Port 装配 |
| 巡检与安防后端 | 已合并，等待生产 Port 装配 |
| 公共请求上下文、角色和 HTTP 错误协议 | 已建立 |
| 公共身份认证、基础数据和生产服务装配 | 待实现 |
| 费用只读查询与规则解释 | 已重构并接入统一应用 |
| 财务咨询状态机 | 待实现 |
| 公告后端 | P0 已实现，等待生产 Port 装配 |
| Web 前端 | 已建立目录，待初始化 |
| Agent 编排 | 待开发 |
| 演示部署环境 | 待开发 |

“待开发”仅表示已纳入 MVP，不代表当前代码已经实现。

## 仓库结构

```text
frontend/                    响应式 Web 前端
src/property_agent/
├── platform/                公共身份、权限和审计能力
├── repair/                  报修业务
├── announcement/            公告业务
├── billing/                 费用业务
├── inspection/              巡检与安防业务
├── agent/                   智能体编排
└── integrations/            外部系统适配
alembic/                     PostgreSQL 数据库迁移
tests/                       自动化测试
testing/                     压测和本地验证支持脚本
infra/                       本地及演示部署配置
scripts/                     初始化、迁移、种子和运维脚本
docs/
├── architecture/            项目架构与边界
├── api/                     前后端 API 契约
└── decisions/               重要技术决策
```

详细说明见 [`docs/architecture/README.md`](docs/architecture/README.md)。

## 后端开发环境

- Python 3.11
- PostgreSQL
- FastAPI
- Pydantic
- SQLAlchemy 2
- Alembic

安装开发依赖：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

执行数据库迁移：

```powershell
$env:DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost/property_agent"
.\.venv\Scripts\alembic.exe upgrade head
```

项目级入口为 `property_agent.main:create_app`，当前统一注册报修、公告、巡检和安防 Router。各业务
Service 仍须由生产组合根装配数据库、身份、权限、确认、幂等、审计和消息 Port；未装配时
业务接口会明确返回 `503 ADAPTER_NOT_CONFIGURED`，不会回退到 fake backend。费用模块只公开
查询、详情和规则解释，不提供支付、退款、减免或账单状态修改入口。

公告 P0 使用 `/api/announcements`：客服或管理员创建/编辑草稿并提交审核，管理员批准后必须
携带绑定操作人、参数哈希和有效期的确认令牌才能发布。受众由服务端在当前小区内解析并在提交
审核及发布时冻结快照；发布写入共享站内消息 Outbox，投递状态与公告状态分离。生产组合根须
装配公告的 UoW、受众、确认、幂等、审计和消息 Port；Agent 只暴露草稿、查询、预览和提交审核
工具。详细契约及人工验收见 [`docs/announcement_module.md`](docs/announcement_module.md)。

验证统一应用入口：

```powershell
.\.venv\Scripts\uvicorn.exe property_agent.main:create_app --factory
```

启动后可访问 `/health` 和 `/docs`；业务接口需要完成生产 Service 装配后才能使用。

运行质量检查：

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests alembic
.\.venv\Scripts\python.exe -m compileall -q src tests
```

## 协作约定

- 使用 Issue 描述任务和验收条件。
- 每项工作使用独立分支，通过 Pull Request 合并到 `main`。
- 业务模块按领域边界划分，不按成员划分所有权。
- 一个功能的代码、迁移、测试和接口文档尽量在同一 Pull Request 中交付。
- 不提交 `.env`、密钥、真实住户数据、缓存和本地构建产物。
- demo、mock 和临时验证代码只能进入 `tests/`、`testing/` 或后续 `examples/`。
- 未通过后端权限和状态机校验的前端或 Agent 操作不视为功能完成。

## 业务分支

`main` 只保存经过团队确认的项目骨架和稳定集成结果。业务模块在独立分支开发并通过 Pull
Request 合并，例如报修模块使用 `repair` 分支。
