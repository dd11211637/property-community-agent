# ADR-0007: P0 正确性底座 — Checkpoint CAS + Run Lease + Approval 原子化

## 背景

[deep-research-report.md](../../../../../Downloads/deep-research-report.md) §3
指出了三处会让"已确认"动作在并发/重启下产生不一致的并发缺陷：

1. **同会话并发 lost-update** — 同 conversation 两个 LLM turn 同时跑，
   旧 worker 在 lease 过期后用过期 checkpoint version 覆盖新 checkpoint；
   业务侧的写操作彼此丢失（典型：报修 / 公告 / 安防事件上报）。
2. **审批令牌"签发即消费"反模式** — 既有 `confirmation_tokens` 在
   `confirmation_token_provider` 中创建后立即消费，业务 Service 真正执行
   时再校验"未找到 token"或被新 turn 复用，导致审计与业务落库脱节。
3. **审批与业务 mutation 分事务** — 即使消费顺序正确，因为不在同一事务里，
   "已确认但未落库"或"已落库但未确认"成为可能；事务回滚也只回滚一半。

PRD §6.5.8 / §6.5.10 已经定义了恢复守卫（会话所有权 / 房屋 / 有效期），
本 ADR 在此基础上落地 P0 正确性底座的三道闸门。

## 决策

### 1. Checkpoint CAS — `UPDATE … WHERE version = expected`

* `SqlAlchemyCheckpointer.save` 增加 `expected_version` 参数；提供时走原子
  `UPDATE … SET version=version+1, … WHERE version=:expected RETURNING version`，
  0 行 → `CheckpointVersionConflict`，runner 终止本 stale run。
* `expected_version` 由 **turn 开始** 读取（`AgentSessionRunner._turn_start_version`），
  绝不在 `save()` 内部现读——否则 stale worker 会读到最新版本导致 CAS 失效。

### 2. Run Lease / Fencing — `agent_run_leases`

* 每会话一个 lease 行：`thread_id` PK、`owner_run_id`、`lease_until`、`fence`、`updated_at`。
* 抢占用便携 `INSERT … ON CONFLICT (thread_id) DO UPDATE … WHERE lease_until < now()
  RETURNING fence, lease_until`；0 行 → `AgentSessionError(CONVERSATION_BUSY)`（HTTP 409，
  前端可安全重试）。**acquire 返回 `Lease(thread_id, run_id, fence, lease_until)`**。
* **Fencing（P0-4）**：`run_id + fence` 注入 trusted `RequestContext.agent_lease`，
  业务写 UoW 在 mutation 前调用 `assert_run_fence(session, lease)` 校验当前
  turn 仍拥有 conversation。stale worker 的业务写 100% 被拒绝
  （`StaleAgentRunError`）。
* **Heartbeat / renewal（P0-5）**：`renew(thread_id, run_id, fence)` 校验
  `run_id + fence` 仍属于当前 lease；失败抛 `StaleAgentRunError`，调用方必须
  终止当前 run。runner 在 graph.invoke/resume 前续期一次。
* 释放把 `lease_until` 设为过去时间（**不删除行**），使 fence 持续递增——
  stale worker 的旧 fence 永远不会被新 run 复用。
* lease 与 checkpoint CAS 分工：lease 防止**同一 conversation 同时跑两个长
  LLM turn**；CAS 防止 lease 过期后旧 worker 用过期版本覆盖新 checkpoint；
  fence 防止 lease 过期后旧 worker 执行业务 mutation（CAS 只挡 checkpoint）。

### 3. Approval 原子化 — `agent_action_approvals`

* 新增 `agent_action_approvals` 表 + `ApprovalStatus` enum（PENDING/APPROVED/
  REJECTED/CONSUMED/EXPIRED）。
* 部分唯一索引 `(conversation_id, action, params_hash) WHERE status IN ('PENDING', 'APPROVED')`
  保证重复确认不产生第二个业务对象。
* **生命周期统一（P0-6）**：PENDING → APPROVED → CONSUMED 状态机严格强制。
  `confirmation_token_provider` 在用户确认后 create_pending + 立即 approve
  （PENDING → APPROVED）。`consume` **只接受 APPROVED**，拒绝 PENDING
  （"签发即消费"反模式）。
* `ApprovalService.consume` 接受调用方的 `Session`，在同一事务内 `FOR UPDATE`
  后校验 actor/action/params_hash + status==APPROVED，将 status 置为 CONSUMED。
* 业务 Service 在 mutation 之前调用 `uow.confirmations.consume(approval_ref=...,
  token=...)`，端口先 fence 校验 → 消费审批（业务侧唯一信源）→ 消费
  `confirmation_tokens` 作为传输层防伪造的纵深防御。
* 任一校验失败抛 `ApprovalError`，由调用方回滚同一事务：业务 mutation / 审计 /
  Outbox / 审批消费一起回滚，杜绝中间态。

### 4. 关键改动面

* `agent_run_lease`、`agent_action_approvals` 表 + `runtime_version` 列
  （`alembic/versions/20260820_0002_add_concurrency_guards.py`）。
* `agent_run_lease`、`agent_action_approvals` ORM 模型纳入 `Base.metadata`。
* **Lease 最外层（P0-2）**：`runner.py` 的 `start` / `resume` / `close` 都在
  最外层 acquire lease，所有 conversation 级 mutation（ConversationService.start、
  recovery.restore、confirmation_token_provider、graph.invoke/resume、
  sync_from_state、close）都处于 lease ownership 下。
* **expected_version 后读（P0-3）**：`_turn_start_version` 在 acquire lease
  之后调用，避免读到被并发新 run 覆盖的版本。
* `container.py` 的 `confirmation_token_provider` 在 lease 内 create_pending
  + approve（PENDING → APPROVED）；停用 `AGENT_*` 的"签发即消费"。
* **Production UoW factory wiring（P0-1）**：`build_work_order_service` /
  `build_announcement_service` / `build_inspection_services` 用闭包绑定
  `approval_service`，避免运行时 `TypeError`（审查报告 composition-root 错误）。
* `PlatformConfirmationPort.consume`（repair / announcement / inspection / billing）
  同形扩展：fence 校验 → 消费审批 → 消费令牌，错误码族 `CONFIRMATION_*`。
* **close race 修复（P0-7）**：`sync_from_state` 检查 `status == CLOSED` 并
  拒绝旧 run 复活；`close` 在 lease 内执行。
* `billing/infrastructure` 新增 `PlatformBillingConfirmationPort` + UoW
  `confirmations` 端口；`create_draft` 调用 `uow.confirmations.consume(...)`
  在 UoW 内提交。

### 5. Feature flag — `AGENT_CONCURRENCY_GUARD`

* 默认开启（生产强制）；关闭后 `Runner._enforce=False` 跳过 lease / CAS，
  仅做 `_save_legacy`（SELECT→+1→COMMIT）保留兼容。
* 通过 `Settings.agent_concurrency_guard`（默认 `True`）控制。
* 关闭场景仅限回滚 / 排错，事后必须重新打开。

### 6. Runtime pinning — `runtime_version` (v1)

* `ConversationModel.runtime_version` 默认 `"v1"`；未来切到 LangGraph 后
  按列钉住运行时分发分钟级回退——纯字段，无功能性约束。

## 可选方案（已否决）

* **乐观锁 (`SELECT … FOR UPDATE` 锁整张 conversations 行)**：拒绝。
  一张 conversations 行要承载长 LLM turn（≥10s），期间整会话被锁死，
  与"长 turn 不持库锁"的设计原则冲突。
* **复用既有 `confirmation_tokens` 表扩展 CAS 列**：拒绝。
  `confirmation_tokens` 的语义是传输层防伪造凭据，与业务审批职责不同；
  把 `params_hash`、`expires_at`、`status` 全部塞进同一表会让 token consume
  与 approval consume 互相干扰（同一行同时被两个用例更新）。
* **把审批消费放在 GraphNode 层**：拒绝。
  节点层只做编排；业务侧已在 UoW 事务边界，原子消费必须落在 UoW 里，
  否则跨节点会重新引入分事务风险。

## 后果

* **正面**：同一 conversation 并发 lost-update 不再发生；审批与业务
  mutation 真正同事务；AGENT_* 工具的"签发即消费"反模式被移除；运行时
  可以平滑切到 LangGraph（runtime_version 字段）。
* **代价**：每个受控写多一次 `agent_action_approvals` 写入与一次
  `FOR UPDATE` 读；lease 抢占 / 释放各一次轻事务。
* **风险**：
  * 部分 unique 索引 (`status IN ('PENDING','APPROVED')`) 跨方言：SQLite 用
    `sqlite_where`，PostgreSQL 用 `postgresql_where`，已在 ORM Index 上声明。
  * 部分既有测试只校验 token，需要同时携带 `approval_ref`；新增 helper
    `_make_consultation_approval(sessions, ctx, …)` 配套。
  * 关闭 `AGENT_CONCURRENCY_GUARD` 退回旧行为，**生产必须保持开启**。

## 审查报告 P0 整改记录（2026-08-19）

基于《审查报告.md》对 HEAD `4673caa` 的 P0 问题清单，本分支完成了以下整改：

| P0 项 | 状态 | 代码位置 |
| --- | --- | --- |
| 1. 3 领域 UoW factory wiring | 已修复 | `container.py` build_work_order/announcement/inspection_service 闭包绑定 |
| 2. lease 提升到 turn 最外层 | 已修复 | `runner.py` start/resume/close 最外层 acquire lease |
| 3. expected_version 在 acquire 后读 | 已修复 | `runner.py` _turn_start_version 在 _acquire_lease 之后 |
| 4. 真正 fencing | 已修复 | `run_lease.py` Lease dataclass + `assert_run_fence`；`RequestContext.agent_lease`；`PlatformConfirmationPort.consume` 内 fence 校验 |
| 5. lease heartbeat / renewal | 已修复 | `run_lease.py` renew()；`turn_guard.py` heartbeat_turn_lease；runner 在 graph.invoke/resume 前 renew |
| 6. approval 生命周期统一 | 已修复 | `approval_service.py` consume 只接受 APPROVED；`container.py` confirmation_token_provider 在 lease 内 create_pending + approve |
| 7. close + active run race | 已修复 | `conversation_service.py` sync_from_state 拒绝 CLOSED；`runner.py` close 在 lease 内 |
| 8. PostgreSQL 并发测试 | 已新增 | `tests/test_p0_postgres_concurrency.py`（11 场景，@pytest.mark.postgres） |

验证结果：489 测试通过（SQLite）；11 PG 测试在 CI 运行（@pytest.mark.postgres）；
ruff / structure / alembic 全通过。

## 关联代码

| 组件 | 路径 |
| --- | --- |
| Migration | `alembic/versions/20260820_0002_add_concurrency_guards.py` |
| ORM | `agent/infrastructure/models.py`（`AgentRunLeaseModel`、`AgentActionApprovalModel`、`runtime_version`） |
| Run lease | `agent/infrastructure/run_lease.py` |
| Approval service | `platform/application/approval_service.py` |
| Checkpoint CAS | `agent/infrastructure/checkpointer.py`（`SqlAlchemyCheckpointer.save` + `CheckpointVersionConflict`） |
| Runner wiring | `agent/application/runner.py`（`_acquire_lease`、`_release_lease`、`_turn_start_version`） |
| Provider | `platform/container.py`（`confirmation_token_provider`、`build_agent_runner`） |
| Ports | `repair/infrastructure/shared_ports.py`、`announcement/infrastructure/shared_ports.py`、`inspection/infrastructure/shared_ports.py`、`billing/infrastructure/shared_ports.py` |
| Config | `config.py`（`agent_concurrency_guard`、`agent_approval_ttl_minutes`、`agent_run_lease_seconds`） |
| 测试 | `tests/test_p0_concurrency_atomicity.py` |

## 验证

* `pytest tests/test_p0_concurrency_atomicity.py`：15/15 通过（含 checkpoint CAS
  冲突、lease 并发 409、approval 同事务消费 + 过期 / 错 actor / 错 params_hash
  拒绝、重复消费幂等）。
* `pytest tests/test_billing_production_ports.py tests/test_repair_production_ports.py
  tests/test_announcement_production_ports.py`：所有既有生产端口测试通过。
* `ruff check` 已改文件 / 测试：全部通过。

## P1 安全加固（后续迭代）

P0 合并后，针对审查报告 P1 中两项低风险、验收明确的安全加固，在
`feat/p0-concurrency-and-approval-atomicity` 分支继续提交：

1. **Memory 真 CAS**（审查报告 P1「Memory 真 CAS」）。
   `agent/application/memory_service.py` 的 `update_memory` / `delete_memory`
   原先是「先 SELECT 读 `row.version`，再用 Python 比较 `!= expected_version`，
   然后 ORM 赋值 `version += 1`」——属于读-改-写三段，两个并发事务可能都读到
   version=N 并各自写 version=N+1，产生 lost update。
   改为单条原子语句：

   ```sql
   UPDATE agent_memories
   SET content = :content, version = version + 1, updated_at = now()
   WHERE id = :id AND actor_id = :actor AND community_id = :community
     AND deleted_at IS NULL AND version = :expected
   RETURNING version;
   ```

   命中返回新 version；未命中（被别的请求先改）返回 0 行 → 抛
   `VERSION_CONFLICT`。ownership + 未删除过滤一并保留。

2. **配置安全验证**（审查报告 P1「配置安全验证」）。
   `config.py` 的 `validate_runtime_security()` 生产分支新增三条硬性约束：
   `agent_concurrency_guard` 必须开启、`agent_run_lease_seconds > 0`、
   `agent_approval_ttl_minutes > 0`。任何一条不满足，生产启动直接抛
   `RuntimeError("Unsafe production configuration: ...")`，杜绝有人把 P0
   正确性底座（concurrency guard / lease / approval 窗口）悄悄关掉或置为非法值。

### 关联代码（P1）

| 组件 | 路径 |
| --- | --- |
| Memory CAS | `agent/application/memory_service.py`（`_apply_versioned_update`、`update_memory`、`delete_memory`） |
| 配置校验 | `config.py`（`validate_runtime_security`） |
| 测试 | `tests/agent/test_agent_memory.py`（update/delete stale version 冲突）、`tests/platform/test_runtime_config.py`（生产禁用 guard / 非正 lease·ttl 拒绝）、`tests/test_p0_postgres_concurrency.py::test_memory_double_writer_cas`（PG 双写严格 1 ok + 1 conflict） |

### 验证（P1）

* `pytest tests/agent/test_agent_memory.py tests/platform/test_runtime_config.py`：全绿。

## P1 观测与流式（后续迭代）

P0 合并后，在 `feat/p0-concurrency-and-approval-atomicity` 分支继续提交审查报告
P1 中的「观测与流式」三项（OTel tracing + 4 关键指标、SSE 真流式、Agent eval
PR gate）。三项均低风险、验收明确，可随 P0 分支一同合并（L 级架构改造按报告
顺序延后到 P0 合并后）。

### 1. OTel tracing + 4 关键指标

新增 `agent/observability.py`，在 runner 的 `start` / `resume` / `stream_start` /
`stream_resume` 四个入口各包一层 `agent.turn` root span，并精确递增 4 个验证指标：

| 指标 | 触发路径 |
| --- | --- |
| `agent_conversation_busy_total` | 同会话并发被拒（`AgentSessionError(CONVERSATION_BUSY)` / 409） |
| `agent_checkpoint_conflict_total` | checkpoint CAS 命中 stale 版本（`CheckpointVersionConflict`） |
| `agent_stale_fence_rejected_total` | 旧 worker 凭旧 fence 的业务写被 fencing 拒绝（`StaleAgentRunError` / 409） |
| `agent_approval_rollback_total` | 已确认（审批已被业务 UoW 消费）却因后续业务失败整体回滚 |

**不记录 PII**：span 只带 `conversation_id` / `run_id` / `lease.fence` /
`checkpoint.expected_version` / `confirmed` / `intent` / `degraded` / `runtime.version`
等元数据，绝不携带 prompt / 住户聊天 / 地址 / 电话 / tool 参数 / 业务结果。

**可选依赖 + 优雅降级**：`opentelemetry-api` / `opentelemetry-sdk` 为
`[otel]` 可选依赖组。`AgentObservability.build(settings)` 在 `otel_enabled=False`
或 SDK 未安装时自动降级为进程内计数器（`InMemoryCounter`）+ `NullTracer`
（`degraded=True`），正确性底座完全不受影响，本地 / CI（SQLite）亦可断言指标递增。

### 2. SSE 真流式

原 SSE 接口是「graph 跑完 → 回放 `__turn__` 事件」，首事件延迟等于整轮耗时。
改为**真流式**：

* `graph_core._run` 改造为生成器，逐节点 yield
  `("node_enter"|"node_exit"|"interrupt"|"__final__", payload)`；非流式
  `invoke` / `resume` 仍消费该生成器取 `__final__`，语义不变（且修复了中断路径
  原本误把 turn 标记为 `done`、丢失 `confirmation` 的 bug）。
* runner 新增 `stream_start` / `stream_resume`：先 yield `("run_started", ...)`
  （graph 完成前即下发，降低 time-to-first-event），再按节点生命周期 yield
  `("tool_started"|"tool_finished", ...)`，最后在 `__final__` 处 `_finalize` 并
  yield `("__turn__", turn)` 由 API 层展开为 intent / message / confirmation /
  facts / done。
* API 层 `send_message_stream` 直接透传 `run_started` / `tool_started` /
  `tool_finished`，并把 `__turn__` 展开为既有 SSE 子事件序列。

### 3. Agent eval PR gate

`testing/agent_harness.py` 是基于确定性场景工具 + 生产 planner/guard/runtime
契约的离线评测台，覆盖 controlled-read 单步 / 轨迹 / 安全用例。任一用例失败即
返回 exit 1。在 `quality.yml` 的 `backend` job 末尾新增步骤，PR / push 时运行
`tests/agent/data/controlled_read_cases.json`，失败则阻断合并。

### 关联代码（P1 观测与流式）

| 组件 | 路径 |
| --- | --- |
| 观测模块 | `agent/observability.py`（`AgentObservability`、`Metrics`、4 指标、root span、降级） |
| 配置 | `config.py`（`otel_enabled`、`otel_service_name`） |
| 可选依赖 | `pyproject.toml`（`[otel]` 组） |
| 图内核流式 | `agent/graph_core.py`（`_run` 生成器、`_emit_finish` 透传 interrupt） |
| Runner 接入 | `agent/application/runner.py`（`stream_start` / `stream_resume` / `_plan_start` / `_plan_resume` / `_TurnPlan` / observability 注入） |
| API 透传 | `agent/adapters/api/router.py`（`send_message_stream`） |
| 装配 | `platform/container.py`（`AgentObservability.build(settings)` 注入 runner） |
| CI gate | `.github/workflows/quality.yml`（`backend` job 末尾 eval 步骤） |
| 测试 | `tests/agent/test_p1_observability.py`（4 指标递增 + span 无 PII + 图流式）、`tests/agent/test_agent_api.py::test_sse_stream_emits_run_started_first_then_node_lifecycle` |

### 验证（P1 观测与流式）

* `pytest tests/agent/test_p1_observability.py tests/agent/test_agent_api.py`：全绿
  （4 指标在正确错误路径精确递增、root span 无 PII、`invoke_stream` 逐节点 yield
  生命周期、`run_started` 为 SSE 首事件且早于 intent/done）。
* `pytest tests/agent/` + 全量（非 postgres/performance）：全绿。
* `ruff check` / `scripts/check_code_structure.py`：通过（runner 因 P1 流式新增
  方法，已在 `config/code_quality_baseline.json` 登记历史债务）。
* `python testing/agent_harness.py tests/agent/data/controlled_read_cases.json`：
  exit 0（确定性 eval gate 本地通过）。
* 全量 `pytest`：通过（PG 用例本地 skip，CI 在 PostgreSQL 16 上运行）。
* `ruff check` / `ruff format --check` / `scripts/check_code_structure.py`：全部通过。