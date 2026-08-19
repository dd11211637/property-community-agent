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
  RETURNING fence`；0 行 → `AgentSessionError(CONVERSATION_BUSY)`（HTTP 409，
  前端可安全重试）。
* 释放只删自己持有的 lease（`WHERE thread_id=:tid AND owner_run_id=:run_id`），
  避免误杀已抢占的新 run。
* lease 与 checkpoint CAS 分工：lease 防止**同一 conversation 同时跑两个长
  LLM turn**；CAS 防止 lease 过期后旧 worker 用过期版本覆盖新 checkpoint。

### 3. Approval 原子化 — `agent_action_approvals`

* 新增 `agent_action_approvals` 表 + `ApprovalStatus` enum（PENDING/APPROVED/
  REJECTED/CONSUMED/EXPIRED）。
* 部分唯一索引 `(conversation_id, action, params_hash) WHERE status IN ('PENDING', 'APPROVED')`
  保证重复确认不产生第二个业务对象。
* `ApprovalService.create_pending` 在服务端确认时**复用**已存在的开放审批
  （同一会话/动作/参数指纹），返回 `approval.id` 写回 `state.approval_ref`。
* `ApprovalService.consume` 接受调用方的 `Session`，在同一事务内 `FOR UPDATE`
  后校验 actor/action/params_hash，将 status 置为 CONSUMED。
* 业务 Service 在 mutation 之前调用 `uow.confirmations.consume(approval_ref=...,
  token=...)`，端口先消费审批（业务侧唯一信源），再消费 `confirmation_tokens`
  作为传输层防伪造的纵深防御。
* 任一校验失败抛 `ApprovalError`，由调用方回滚同一事务：业务 mutation / 审计 /
  Outbox / 审批消费一起回滚，杜绝中间态。

### 4. 关键改动面

* `agent_run_lease`、`agent_action_approvals` 表 + `runtime_version` 列
  （`alembic/versions/20260820_0002_add_concurrency_guards.py`）。
* `agent_run_lease`、`agent_action_approvals` ORM 模型纳入 `Base.metadata`。
* `runner.py` `_acquire_lease/_release_lease/_turn_start_version` 钩入
  `start` / `resume`，整个 turn 持有 lease。
* `container.py` 的 `confirmation_token_provider` 改为创建 PENDING 审批
  并把 `approval_ref` 写回 state；停用 `AGENT_*` 的"签发即消费"。
* `PlatformConfirmationPort.consume`（repair / announcement / inspection / billing）
  同形扩展：先消费审批，再消费令牌，错误码族 `CONFIRMATION_*`。
* `billing/infrastructure.s` 新增 `PlatformBillingConfirmationPort` + UoW
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