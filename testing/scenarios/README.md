# PRD 验收场景

本目录属于 demo/support，不会被生产应用导入，也不参与生产镜像运行。

`acceptance_matrix.json` 固化 PRD V1.1 的 F-01—F-06、A-01—A-04、S-01—S-04、R-01—R-04 共 18 个验收场景，并将每一项关联到可执行测试或烟测入口。`tests/test_acceptance_manifest.py` 会检查编号完整性、重复项和证据路径，避免文档与代码静默漂移。

证据按以下层级执行：

1. 本地 SQLite 快速测试。
2. Docker 中真实 PostgreSQL 全套测试，要求无跳过。
3. `testing.e2e_api_smoke` 通过公开 API 验证四类业务、消息和审计。
4. Playwright 在构建后的前端验证浏览器跨角色流程。
5. `testing.agent_restart_smoke` 验证待确认会话跨后端重启恢复。
6. `testing.outbox_failure_smoke` 验证发送失败、重试上限和人工接管。

全新环境验收使用独立 Compose project 和独立数据卷；验收结束后精确删除该 project 及其数据卷，不影响日常演示环境。`execution-2026-08-12.md` 是历史执行记录；当前回归结论和日期以 [`docs/ACCEPTANCE_STATUS.md`](../../docs/ACCEPTANCE_STATUS.md) 为准。
