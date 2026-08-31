# V2-only 本地交付报告

日期：2026-08-31

分支：`codex/v2-only-product-delivery`

交付目录：`C:\Users\戴嘉兴\Desktop\华工\具身智能-v2-delivery`

## 交付结论

- 生产 Agent 仅装配 LangGraph V2 Supervisor、四个 Specialist 与 Capability Executor。
- V1 graph、node、subgraph、tool bridge、selector、rollout、drain、retirement 与对应旧测试已从活动树移除。
- 新会话和数据库约束仅允许 `runtime_version=v2`；迁移发现 V1 会话时会明确失败，不会自动删除。
- DeepSeek Key 在非测试环境必填；生产 provider 不可用时 fail-closed，不降级到 V1 或确定性 gateway。
- 最新产品前端已保留，房屋切换可用；旧会话引用会清除并提示用户开始新的 V2 会话。

## 验证结果

| Gate | 结果 | 证据 |
|---|---|---|
| Ruff lint | PASS | `ruff check src tests testing scripts alembic` |
| Ruff format | PASS | 所有本次修改的 Python 文件已格式化 |
| 结构检查 | PASS | `scripts/check_code_structure.py` |
| 后端完整本地套件 | PASS | 651 tests collected；完整套件通过，外部/数据库分组按配置跳过 |
| 真实 PostgreSQL | PASS | 34/34，零跳过；独立测试库升级到 `20260830_0001` 后执行 |
| 前端单测 | PASS | 9 files，38/38 |
| 前端 lint/build | PASS | ESLint 通过；Vite 1760 modules 构建通过 |
| Playwright 主业务流 | PASS | Chromium 26/26 |
| V2 静态契约 | PASS | 活动生产源码无 `LegacyGraphEngine`、V1 selector 或 V1 runtime fallback |
| Windows 本机启动 | PASS | 显式 SelectorEventLoop 入口；`/ready=READY`，前端 HTTP 200 |
| Docker 全量重建 | NOT_RUN | Docker daemon 健康；BuildKit gRPC 失败，传统 builder 被 PyPI TLS EOF 阻断 |
| 真实 DeepSeek 调用 | PASS | `provider=deepseek`、`intent=BILLING`、`degraded=false`；Key 未输出且 `.env` 未被 Git 跟踪 |

当前运行状态：PostgreSQL 使用 Docker；V2 后端运行于 `127.0.0.1:8000`，最新前端运行于 `127.0.0.1:5173`。`/ready` 显示 database/services/accepted-head store 均为 `UP`，runtime 为 `V2_ONLY`、100% V2、fallback runtime 为 V2。

## 本地 V1 数据处置

- 已归档并验证恢复的 V1 会话：134。
- 关联归档行：checkpoints 134、messages 384、approvals 19、leases 134。
- 当前 `property_agent_demo`：V1 conversations/messages/checkpoints/approvals/leases 均为 0，Alembic head 为 `20260830_0001`。
- 业务事实未减少；清理后抽查：work orders 40、announcements 36、fee bills 4、inspection tasks 13、security events 48、audit logs 1819、handover tickets 2。

归档目录：`C:\Users\戴嘉兴\Desktop\华工\具身智能-archive\2026-08-30\v1-runtime-archive`

| 文件 | SHA-256 |
|---|---|
| `property_agent_demo.dump` | `ACC676F501ED50D197EC52478786514536D8D8CC67031B47E74B28D96D7945E2` |
| `v1-runtime-records.json` | `FFA59267E5F9A5ACB72FB6C06217346140A6EDEA4D00E09ECE3F77ADED977F6C` |
| `business-counts-before.json` | `4596B6B7A0257C8178ED4726AF04A36F02B2CABC4D5FFD9CEFB777B65779F071` |

## 旧前端工作树归档

四个旧工作树已移动到：

`C:\Users\戴嘉兴\Desktop\华工\具身智能-archive\2026-08-30\frontend-worktrees`

归档清单记录了分支、HEAD 和状态；skeleton 未提交修改保存在
`frontend-v2-skeleton-working-tree.patch`，SHA-256 为
`1EC2C11C17B5DAB53CD403165F3884D8DB31DEBB5B1E66E60B8A6DF467098B30`。

## 文件分类

- 生产代码：`src/property_agent/**`、`frontend/src/**`、Alembic V2-only migration、运行配置。
- 测试代码：`tests/**`、`frontend/tests/**`、`frontend/e2e/**`。
- Demo/support：`testing/**`、`scripts/archive_v1_runtime.py`、本报告及项目外归档材料。

## 待外部条件

1. 确认聊天中暴露的旧 DeepSeek Key 已撤销。
2. Docker 到 PyPI 的 TLS 网络恢复后重新执行全量镜像构建；当前本机混合栈可用，但不能将 Docker 重建门标记为 PASS。
