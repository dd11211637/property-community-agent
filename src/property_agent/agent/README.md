# Agent

意图路由、信息补全、确认卡片、业务工具调用、失败降级和人工接管。该模块不保存业务真实
状态，也不能绕过业务服务、权限、状态机、幂等或审计规则。

## 结构

```
state.py          GraphState（§6.5.4）
policies.py       意图枚举、工具等级表、槽位表（§6.5.6 / §6.5.7）
model_gateway.py  模型接缝；不可用时降级为 UNCERTAIN（§6.5.9 / R-02）
graph_core.py     轻量 StateGraph / interrupt / Checkpointer 协议
nodes/            分类 → 选工具 → 补槽位 → 确认 → 执行 → 解释 / 接管
subgraphs/        报修 / 公告 / 账单 / 巡检四个业务子图
tools/            只调用各模块公开 Application Service（§6.5.2）
routing.py        意图 → 子图入口
graph.py          主路由图装配
application/      Conversation 业务表服务、恢复守卫、会话运行时（§6.5.8）
infrastructure/   agent_conversations / agent_checkpoints 与持久化 Checkpointer
```

## 持久化与恢复（§6.5.8）

* `thread_id` 恒等于稳定的 `conversation_id`。
* `agent_conversations`（业务表）保存会话所有权、当前房屋、接管状态与生命周期；
  `agent_checkpoints`（Checkpointer）只保存图执行状态，**不能**替代 Conversation、
  AuditLog 或任何业务实体。
* 单元测试可用 `MemoryCheckpointer`；演示与生产必须用 `SqlAlchemyCheckpointer`。
* `AgentRecoveryService.restore` 是 resume 的唯一入口，先过三道闸：
  用户会话（所有权 + 生命周期）→ 房屋绑定 → 确认有效期（默认 5 分钟，与平台
  `ConfirmationService` 对齐）。任一不过即作废该待确认，绝不"接着执行"。
* interrupt 之前不发生任何业务副作用；幂等键由会话 + 工具 + 参数确定性推导，
  跨进程重启保持不变，重复确认只会命中重放。
