# Agent

意图路由、信息补全、确认卡片、业务工具调用、失败降级和人工接管。该模块不保存业务真实
状态，也不能绕过业务服务、权限、状态机、幂等或审计规则。

## 结构

```
state.py          GraphState（§6.5.4）
policies.py       意图枚举、工具等级表、槽位表（§6.5.6 / §6.5.7）
model_gateway.py  DeepSeek JSON 网关、一次重试与关键词降级（§6.5.9 / R-02）
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

## 模型边界与降级（§6.5.9 / R-02）

* 配置 `DEEPSEEK_API_KEY` 后，通过 DeepSeek `POST /chat/completions` 获取严格 JSON
  意图、置信度和槽位；密钥只从环境变量读取。
* 超时、429、5xx、空内容或非法结构最多重试一次，仍失败则切换确定性关键词路由并
  在回复中明确提示降级；未配置密钥时直接使用关键词路由。
* 模型建议不得覆盖 actor、community、house、role、request、confirmation、
  idempotency、version 或 tool。工具选择、必填校验、权限、状态机、确认和审计始终由
  确定性节点与公开 Application Service 负责。

## 上下文理解与受控查询

* 每轮最多向模型提供最近 6 轮用户/助手消息；会话历史只用于理解“那上个月呢”、
  “刚才那个工单”等续问，不作为业务事实。
* 当前日期、社区名称和房屋显示名称由服务端数据库与 Asia/Shanghai 业务时钟注入，
  模型不能从用户文本覆盖这些可信上下文。
* “不是厨房，是卧室”等显式纠正会生成一份新的待确认参数；旧参数指纹不能继续使用，
  且纠正本身不会触发任何业务写入。
* 公告、账单、报修和巡检/安防查询是有界、只读的受控工具链。时间、适用范围、金额、状态和明细均
  来自 Application Service 返回的 `facts`；空结果会说明查询范围和不确定性边界。
* 当前没有开放通用自由 ReAct：模型不能自行选择任意工具、循环写数据库或把经验判断
  当作公告、账单和工单事实。写操作仍执行“补齐信息 → 用户确认 → 固定状态机”。

## 第二阶段：受控只读 ReAct 与 Harness

只读查询采用单一 Community Read Manager。Planner 每一步只能返回严格的
`PlannerDecision`，工具调用必须通过代码实现的输入/输出 Guardrail：

* 白名单仅包含可信上下文、业务日期、公告、账单、工单、巡检任务、安防事件和已发布社区资料的只读工具；
* 每轮最多五次调用且总时长最多 20 秒，相同工具与相同参数不得重复；未知工具、写工具、身份或社区参数
  覆盖、非法日期/月份/数量参数会在执行前拒绝；
* 工具仍只调用公开 Application Service，返回结果会再次检查 community/house 范围；
* 每次决策、参数哈希、工具结果摘要、降级和结束原因进入 `AgentTrace`，不保存完整思维链；
* DeepSeek 规划失败时切换确定性 Planner；模型尝试重复调用时，Guard 先阻止执行，再由
  确定性 Planner 基于已有 Observation 结束或选择下一只读工具。
* 单条文本事实、列表条数、对象字段数和嵌套深度均有硬上限，防止大结果污染规划上下文。
* `search_community_knowledge` 当前只检索住户可见的已发布公告资料，并返回来源、
  发布时间和适用范围；不在代码中内置物业电话或社区规则。独立知识库属于第三阶段。
* API 将脱敏 `agent_trace` 与成功业务 `facts` 分开返回，因此工具失败时仍可按 trace ID
  排障；轨迹不包含原始工具参数、密钥或模型思维链。

`testing/agent_harness.py` 提供离线 Harness，数据集位于
`tests/agent/data/controlled_read_cases.json`。评测同时检查单步 Guardrail、工具轨迹、
最大步数、禁止工具、最终 Facts 路径，以及回复必须出现的真实值和禁止生成的断言；
生产应用不会导入 Harness 或测试数据。

## 巡检与安防 Agent 边界

* 安保查询默认由巡检 Application Service 强制限定到本人任务；管理者按当前社区聚合，
  住户不能读取巡检任务。完成度由 Repository 的未分页聚合计算，不能从前 20 条列表推断。
* 写操作支持创建任务、开始巡检、追加记录、提交最终记录、上报事件和提交处置结果。
  任务/事件 ID 与版本从授权业务对象解析，不要求用户输入技术字段；多个候选必须先选择。
* 每次写入都经过中文确认卡；最终记录和安防事件使用平台签发的一次性确认令牌。
  `ADD_RECORD` 仅允许在巡检中或待复核状态追加且不改变状态，`SUBMIT_RECORDS` 才进入待复核。
* 燃气泄漏、火情和明确人员危险由确定性规则锁定最低高风险，用户或模型不能下调。
  高风险创建后通知值班人员并进入人工接管；Agent 不提供分派、评级确认、复核或关闭工具。

