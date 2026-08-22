# PR1.1 Runtime Foundation 收尾 — 完成报告

> 分支：`feat/p0-concurrency-and-approval-atomicity`（HEAD `b61d8b1` 之上增量修改）
> 范围：仅收尾，未做任何架构重构；`graph_core.py` 保持不变，未引入 LangGraph / Capability / Memory。

## 变更摘要（4 项任务）

### 任务 1 — Lease Heartbeat 后台周期续租 ✅
- `src/property_agent/agent/infrastructure/run_lease.py`：新增 `LeaseHeartbeat`
  （`start` / `stop` / `_renew_loop`）。每 10s 调 `RunLeaseService.renew`；续租失败
  （lease 过期/被抢占）标记 `stale` 并自停，持有 worker 必须中止本 run。
- `src/property_agent/agent/application/runner.py`：四个人口（`start` / `stream_start` /
  `resume` / `stream_resume`）在 `_plan_start` / `_plan_resume` 中**先单次续租**确认
  lease 有效、**再启动后台循环**（避免续租线程泄漏）；执行后 `_assert_heartbeat_alive`，
  `finally` 中 `_stop_heartbeat` 先于 `_release_lease`。

### 任务 2 — Close / Sync 原子性 ✅
- `src/property_agent/agent/application/conversation_service.py`：`sync_from_state` 与
  `close` 改用**原子条件 UPDATE** `… WHERE status <> 'CLOSED' RETURNING`。0 行即
  `CONVERSATION_CLOSED` —— 消除「读后判断再更新」的 TOCTOU，杜绝已关闭会话被旧 run 复活。
- 修正点：原始 SQL 不更新内存 ORM 对象，且 session 用 `expire_on_commit=False`，
  必须在 `commit()` 后 `session.refresh(row)` 才能拿到原子更新后的状态。

### 任务 3 — Fence Fail-Closed ✅
- 平台类 `PlatformConfirmationPort` 及 inspection / announcement / billing 的**本地**
  `PlatformConfirmationPort` / `PlatformBillingConfirmationPort` 均新增 `enforce_fence`
  形参（默认 `False` = 测试 mock 放行）。
- `consume` 顶门：`if enforce_fence and lease is None: raise StaleAgentRunError(...)`。
- 容器 `platform/container.py` 用 `settings.agent_concurrency_guard`（默认 `True`）注入到
  repair / announcement / inspection / billing / consultation 五个领域。`repair` 复用平台类。

### 任务 4 — PostgreSQL 并发 CI 强制 ✅
- 新增 `scripts/check_postgres_no_skip.py`：解析 pytest junitxml，强制
  `skipped == 0 and tests > 0`（否则 CI 失败）。
- `.github/workflows/quality.yml` 的 `postgres` job 改为
  `pytest -m postgres --junitxml=pytest-postgres.xml` + 该检查（替代原脆弱的 `grep SKIPPED`）。

## 测试结果
- 新增（SQLite，本地已实跑全绿）：
  - `tests/test_p0_concurrency_atomicity.py`：heartbeat 续租保活、续租失败标记 stale、
    fence fail-closed 拒绝、fence 关闭放行、已关闭会话不被 sync 复活、close 幂等。
  - `tests/test_p0_postgres_concurrency.py`：`test_close_and_run_race_keeps_conversation_closed`
    （100 线程并发 sync，复活次数 = 0）— `@pytest.mark.postgres`，CI 真实 PG 上实跑。
- 回归：`tests/ -m "not postgres"` 全量 **exit 0**（无失败）；repair / announcement /
  inspection / billing 生产端口套件全绿。

## 风险（残余）
1. **PostgreSQL 并发套件本地无法实跑**：需真实 `TEST_POSTGRES_URL`，本地仅 skip；
   逻辑已在 SQLite 验证，CI 经 `check_postgres_no_skip.py` 强制无 skip。
2. **生产 fail-closed 强依赖 lease 注入**：`enforce_fence=True` 时，任何未经 runner
   注入 lease 的业务写都会失败关闭。生产路径均经 runner，预期无碍；若未来出现合法的
   非 runner 写路径，需要显式注入 lease。
3. 未提交：变更留在工作区，待建 `feat/p0-runtime-hardening` 分支、合并 `main` 后打 tag
   `v0.1-agent-runtime-foundation`。

## 下一步建议
1. 提交为 PR1.1（独立 PR，保持 API 兼容、不删旧 runtime）。
2. 合并 `main` 并打 tag `v0.1-agent-runtime-foundation`。
3. 启动 PR2 **Capability Registry**（冻结当前 runtime，不引入 LangGraph）。
