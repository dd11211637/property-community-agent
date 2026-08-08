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

## 对外接口（§6.5.2 / §6.5.11）

`adapters/api` 把智能体暴露为统一后端的两个入口（JSON 与 SSE），仅依赖可信身份上下文：

```
POST   /api/agent/conversations/{conversation_id}/messages          发起一轮（JSON）
POST   /api/agent/conversations/{conversation_id}/messages/stream   同上（SSE 事件流）
POST   /api/agent/conversations/{conversation_id}/confirmations     确认 / 取消（恢复的唯一入口）
GET    /api/agent/conversations/{conversation_id}                   会话状态与待确认卡片
DELETE /api/agent/conversations/{conversation_id}                   关闭会话
```

* 身份**只**从平台认证层注入的 `RequestContext` 取（actor / community / house），
  请求体里的自述身份一律忽略；`house_id` 必须在绑定列表内，否则 `403 HOUSE_NOT_BOUND`。
* 运行时未装配时返回 `503 ADAPTER_NOT_CONFIGURED`，不影响其它结构化业务接口。
* 确认接口必然先过恢复守卫四道闸（§6.5.8）：用户会话 → 房屋绑定 → 确认有效期 →
  参数指纹（`action_hash`）。确认回执必须带回待确认卡片里的 `action_hash`，参数变化后
  旧确认作废（`409 CONFIRMATION_PARAMS_CHANGED`）。
* 响应统一信封 `{success, data, error, request_id}`；`data.facts` 才是业务真实事实，
  `data.reply` / `data.messages` 仅面向用户的建议，不作为"操作成功"的证据。
* SSE 按 `intent → message → confirmation → facts → handover → done` 顺序回放。

