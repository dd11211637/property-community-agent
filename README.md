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
| 统一身份、JWT、房屋选择和 RBAC | 已通过真实 API 和 Playwright 浏览器验收 |
| 报修、公告、Billing、巡检安防后端 | 已通过 Docker PostgreSQL 主流程联调 |
| 统一 FastAPI 组合根与 Alembic 迁移 | 已实现并通过真实 PostgreSQL 测试 |
| Web 四类业务页面与 API 客户端 | 已通过组件测试、lint、构建及 Playwright 浏览器 E2E |
| Agent 编排、持久化确认与模型路由 | DeepSeek 网关、严格 JSON、重试、确定性语义保护和关键词降级已通过真实入口验收 |
| 消息中心与管理工作台 API | 已实现并使用真实表聚合 |
| Docker Compose 真实 PostgreSQL 环境 | 已实现一键启动、迁移、种子和重置 |

“已实现”表示已有生产调用链和自动化测试，不等同于已通过真实 PostgreSQL
和浏览器端到端联调。

## Docker Compose 联调

启动 PostgreSQL、迁移、种子、后端和构建后前端：

```powershell
.\scripts\compose.ps1 Up
```

前端访问 `http://localhost:5173`，后端健康检查为 `http://localhost:8000/ready`。
演示账号、重置和故障注入见 [`testing/DEMO_ACCOUNTS.md`](testing/DEMO_ACCOUNTS.md)。
该脚本会使用指向当前仓库的 ASCII 目录联接，规避 Windows BuildKit 对中文检出路径的
gRPC 会话头兼容问题；不会复制或移动仓库。

如需启用 Agent 的 DeepSeek 意图与槽位识别，在仓库根目录的本地 `.env` 中设置
`DEEPSEEK_API_KEY` 后重新构建/启动后端。密钥为空时后端自动使用确定性关键词路由；
模型调用失败最多重试一次，随后同样降级，不影响结构化业务页面。

浏览器 E2E 需要先安装一次 Chromium，并确保 Compose 已启动：

```powershell
cd frontend
npx playwright install chromium
npm run test:e2e
```

当前验收记录见 [`docs/ACCEPTANCE_STATUS.md`](docs/ACCEPTANCE_STATUS.md)。2026-08-13
基于当前未提交工作区的回归结果为：默认 Playwright 26/26、前端 Vitest 27/27、
真实 PostgreSQL 后端测试 439/439；本地快速套件 436 项通过、3 项 PostgreSQL 专项按设计跳过。
这些数字只对应文档日期和当时工作区，代码变化后必须重新执行。

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

统一应用由 `property_agent.main:app` 启动，生产组合根负责装配数据库、身份、权限、
确认、幂等、审计和消息 Port。故障注入只存在于 `testing.demo_app`，不会被生产入口导入。

运行质量检查：

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests alembic
.\.venv\Scripts\python.exe scripts/check_code_structure.py
.\scripts\compose.ps1 Test
```

统一应用 OpenAPI 契约位于 [`docs/api/openapi.json`](docs/api/openapi.json)，需要刷新时执行
`.\.venv\Scripts\python.exe scripts/export_openapi.py`。

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
