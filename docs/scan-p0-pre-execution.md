# 预执行扫描报告（PR1 收尾 + 分支可合并性复核）

> 角色：Staff+ Agent Infrastructure Engineer
> 模式：**仅扫描，不修改代码**
> 依据：用户提供的《审查报告》+《自主物业 Agent 系统重构实施计划》+ 当前仓库代码

---

## 0. 执行摘要 / 关键结论

审查报告基于 commit `4673caa`（2026-08-19）。**当前分支 HEAD = `b61d8b1`**，已在其上叠加 3 个修复提交，直接回应了审查结论：

| 提交 | 内容 |
|---|---|
| `1741972` fix(agent): P0 remediation per review report | 直接修复审查报告列出的 7 个阻断项 |
| `f60fc1f` fix(agent): P1 safety hardening | memory 真 CAS + runtime guard 校验 |
| `b61d8b1` P1 观测与流式 | OTel 4 关键指标 + SSE 真流式 + 确定性 eval gate |

**结论：审查报告中"当前分支不应合并"的判断已过时。** 7 个 P0 阻断项中 **5 项 CLOSED，2 项 PARTIAL**（均为加固项，不造成数据损坏）。剩余工作已收敛为"P0 收尾加固 + 真实 PostgreSQL CI 实跑"，而非重新设计。

### 阻断项复核（审查报告 vs 当前代码）

| # | 阻断项 | 状态 | 证据 |
|---|---|---|---|
| 1 | 生产容器装配 TypeError（缺 approval_service） | **CLOSED** | `container.py:319-326 / 343 / 396` 用 lambda 闭包绑定 `approval_service`；回归测试 `test_production_container_wiring_no_typeerror` |
| 2 | 真实 fencing（acquire 丢弃 fence） | **CLOSED** | `run_lease.py:45` `Lease(fence)`；`:293` `assert_run_fence`；`platform_confirmation_port.py:66-68` 在业务 UoW 同 session 内校验 |
| 3 | lease 获取过晚（start/restore 在 lease 之前） | **CLOSED** | `runner.py:294` `_plan_start`、`:689` `_plan_resume` 均先 `acquire` |
| 4 | lease heartbeat / 续期 | **PARTIAL** | `run_lease.py:176` `renew()` 已实现；`runner.py:354/704` 仅单次续期，**无后台 10s 周期循环** |
| 5 | 审批生命周期 PENDING→CONSUMED（缺 APPROVED） | **CLOSED** | `approval_service.py:157` `approve()` PENDING→APPROVED；`:223` `consume()` 仅接受 APPROVED |
| 6 | PostgreSQL 并发测试缺失（仅 SQLite） | **CLOSED（需实跑）** | `tests/test_p0_postgres_concurrency.py` 13 用例；但无 `TEST_POSTGRES_URL` 时 skip |
| 7 | close() 可复活 CLOSED 会话 | **PARTIAL** | `conversation_service.py:143` `sync_from_state` 为读后判断，**非原子 `UPDATE ... WHERE status <> 'CLOSED'`**，缺 `FOR UPDATE` |

**审查报告 P1 关注点，经核查也已闭合：**
- 配置安全校验：`config.py:115-120` 现已校验 `agent_concurrency_guard`（生产必须 True）、`agent_run_lease_seconds>0`、`agent_approval_ttl_minutes>0`。
- 4 个关键指标：`observability.py` 已实现 `agent_conversation_busy_total` / `agent_checkpoint_conflict_total` / `agent_stale_fence_rejected_total` / `agent_approval_rollback_total`。
- Memory 真 CAS：`f60fc1f` 已修（2 并发 update 严格 1 成功 + 1 conflict）。
- SSE 真流式：`b61d8b1` 已接入 `astream` 式事件（run_started / tool_started / approval_required / message_delta / done）。

---

## 1. 当前架构图

见下方可视化图表（当前 P0 运行时数据流 + 阻断项复核看板）。

---

## 2. 修改文件列表（PR1 收尾，使分支可合并）

### 必须完成（合并前）

| 文件 | 修改 | 对应阻断 |
|---|---|---|
| `src/property_agent/agent/application/runner.py` | 增加后台 heartbeat 守护（10s 周期 `renew`；失败 `StaleAgentRunError` → 取消当前 graph 执行并释放）。将单次续期替换为周期循环 | 4 |
| `src/property_agent/agent/application/conversation_service.py` | `sync_from_state` / `close` 改为原子 `UPDATE ... WHERE status <> 'CLOSED' RETURNING`，并加 `FOR UPDATE`；修正 docstring 与实现不一致 | 7 |
| `src/property_agent/platform/application/platform_confirmation_port.py` | fence 校验 fail-closed：production（enforce_concurrency）路径 lease 必存在，缺失即抛错（当前 `lease is None` 静默跳过） | 2 残余 |
| `.github/workflows/quality.yml` | PostgreSQL 16 job 注入 `TEST_POSTGRES_URL`，确保 `test_p0_postgres_concurrency.py` 实跑（非 skip） | 6 |
| `tests/test_p0_postgres_concurrency.py` | 补充：后台 heartbeat 下 90s turn 不丢租；close 并发 100 次无 CLOSED→ACTIVE 复活 | 4 / 7 |

### 建议完成（可选加固）
- `run_lease.py`：`release` 当前为 `UPDATE lease_until=过去`（保留 fence 递增），设计正确，**无需改**。
- `graph_core.py`：冻结 feature，不再扩展（已满足）。
- `config.py`：production 默认 `agent_concurrency_guard=True`，`validate_runtime_security` 已在 lifespan 调用，无需额外改。

---

## 3. 风险列表

| 风险 | 概率/影响 | 当前缓解 | 建议 |
|---|---|---|---|
| 长 turn（>30s）中途失租 | 中 / 中 | 单次续期 + `assert_run_fence` 拒绝写入（降级 409，无数据损坏） | 后台周期 heartbeat |
| close 竞态窗口 | 低 / 中 | 单写者 lease 在实践中排除同会话并发 | 原子条件 UPDATE + FOR UPDATE |
| fence 校验 fail-open（lease is None 跳过） | 低 / 高 | 生产路径均注入 lease | fail-closed 断言 |
| PG 并发用例被 skip 而无人发现 | 中 / 高 | 用例存在，标记 `postgres` | CI 注入 `TEST_POSTGRES_URL` 并 fail-on-skip |
| AUTO 级别写绕过 confirmation port 不校验 fence | 低 / 中 | 现有写多经确认门 | 确认所有写路径经 confirmation port 或显式 fence 检查 |
| 旧 runtime 与 v2 并存（未来 Phase 2） | 低 / 高 | `runtime_version` 字段已存在 | 旧 pending 会话 pin v1（后续 PR） |
| 不修改测试以"通过" | — | — | 禁止（工作模式要求） |

---

## 4. 实施步骤（PR1 收尾，独立提交）

**步骤 1 — heartbeat 后台循环（`runner.py`）**
- 在 `_plan_start` / `_plan_resume` acquire lease 后启动 `asyncio.create_task(_heartbeat_loop(lease))`；
- 循环每 10s 调 `run_lease.renew(...)`；renew 抛 `StaleAgentRunError` → 取消当前 graph 执行并 release；
- `finally` 中 cancel 任务并 `release`。
- 影响文件：仅 `runner.py`（不动业务 / API）。
- 验收：`test_renew_extends_lease_on_postgres` 扩展为"90s turn 不丢租"；heartbeat 停止 → 旧 worker 写被 fence 拒绝。

**步骤 2 — 原子 close / sync_from_state（`conversation_service.py`）**
- `sync_from_state`：`UPDATE agent_conversations SET status=:new WHERE id=:id AND status <> 'CLOSED' RETURNING id`，0 行 → `CONVERSATION_CLOSED`；
- `close`：已 `require_owned_by`，补 `WHERE status <> 'CLOSED'` 或在 close 前确认无 live lease（`is_held`）；
- 修正 docstring 与实现一致。
- 验收：现有 `test_closed_conversation_not_resurrected_by_old_run` + 新增 100 次并发 close/run 无复活。

**步骤 3 — fence fail-closed（`platform_confirmation_port.py`）**
- `assert_run_fence` 调用处：若 production（`enforce_concurrency`）且 `lease is None` → 抛 `StaleAgentRunError`（或明确 configuration error）；测试 mock 路径保留 allow-none 开关。

**步骤 4 — CI 实跑 PG suite（`quality.yml`）**
- PostgreSQL 16 service 已配置；在其 env 增加 `TEST_POSTGRES_URL`；
- pytest postgres step 后加 `pytest tests/test_p0_postgres_concurrency.py -m postgres`（确保无 skip）。

**步骤 5 — 回归 + 提交**
- 跑 `pytest tests/test_p0_postgres_concurrency.py -m postgres`（需 PG）、`pytest tests/`（SQLite 部分）、Ruff、`scripts/check_code_structure.py`；
- 每步独立 PR 描述，关联审查报告条目；**不修改测试以通过**。

---

## 5. 后续 PR 路线图（与用户提供的 Phase 0-4 一致，标注当前位置）

- **当前分支 = PR1（P0 正确性）收尾。** 不 Big Bang。
- PR2 Capability Registry / PR3 Typed State / PR4 LangGraph Runtime / PR5 Long-term Memory / PR6 Observability：均尚未开始，按用户提供的顺序逐步迁移。
- 关键纪律（来自工作模式要求）：
  1. 不破坏 Application Service 作为业务唯一事实源；
  2. Agent 不允许直接修改业务数据库；
  3. 所有 write path 经 Policy → Approval → Application Service → UoW → Audit → Outbox → Commit；
  4. 数据库 migration additive，API backward-compatible；
  5. 旧 `WAITING_CONFIRM` 会话 pin v1 runtime 直到完成/过期。

---

## 6. 最终建议

**当前分支已达到"可合并"的实质门槛**（P0 正确性阻断项基本闭合，仅余 2 项加固）。建议：

1. 先合 4 个收尾 PR（heartbeat / atomic close / fence fail-closed / CI PG 实跑）；
2. 合并后启动 PR2（Capability Registry）；
3. **不要在当前分支同时引入 LangGraph**（冻结 `graph_core.py`）；
4. **禁止为通过测试修改测试**。

> 注：本文件为扫描/复核产物，未对仓库代码做任何修改。
