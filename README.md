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
| 统一身份、JWT、房屋选择和 RBAC | 已实现，待真实浏览器验收 |
| 报修、公告、Billing、巡检安防后端 | 已合并，待全流程联调 |
| 统一 FastAPI 组合根与 Alembic 迁移 | 已实现，Billing 正在纳入统一 PostgreSQL |
| Web 前端基础页面与 API 客户端 | 已实现，四类业务闭环待补齐 |
| Agent 编排、持久化确认与关键词路由 | 已实现，DeepSeek 真实网关待接入 |
| 消息中心与管理工作台 API | 待实现（M2） |
| Docker Compose 真实 PostgreSQL 环境 | 正在实现与验收（M1） |

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
