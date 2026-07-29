# 报修模块集成契约

## 模块边界

报修模块接收已经结构化的业务参数，不识别用户意图，不负责对话追问和 Agent 路由。工单状态只能由 `WorkOrderService` 驱动，调用方不得直接写 `status`。

## 状态与动作

| 当前状态 | 动作 | 目标状态 | 操作者 |
|---|---|---|---|
| `PENDING_ASSIGNMENT` | `ASSIGN` | `PENDING_ACCEPTANCE` | 客服、管理者 |
| `PENDING_ACCEPTANCE` | `ACCEPT` | `PROCESSING` | 被分派维修人员 |
| `PENDING_ACCEPTANCE` | `REJECT` | `PENDING_ASSIGNMENT` | 被分派维修人员；原因必填 |
| `PROCESSING` | `SUBMIT_COMPLETION` | `PENDING_VERIFICATION` | 被分派维修人员；完工说明必填 |
| `PENDING_VERIFICATION` | `VERIFY_PASS` | `CLOSED` | 对应房屋住户、管理者 |
| `PENDING_VERIFICATION` | `REQUEST_REWORK` | `REWORKING` | 对应房屋住户、管理者；原因必填 |
| `REWORKING` | `SUBMIT_REWORK_COMPLETION` | `PENDING_VERIFICATION` | 被分派维修人员 |

`RECORD_PROGRESS` 只能在 `PROCESSING` 或 `REWORKING` 使用，会追加过程记录并递增版本，但不改变状态。

## HTTP API

所有写请求必须包含 `Idempotency-Key`。状态动作请求必须包含 `expected_version`。

| Method | Path | 用途 |
|---|---|---|
| POST | `/api/work-orders` | 使用确认凭证创建工单 |
| GET | `/api/work-orders` | 按授权范围查询列表 |
| GET | `/api/work-orders/{id}` | 查询详情 |
| GET | `/api/work-orders/{id}/timeline` | 查询状态与处理时间线 |
| POST | `/api/work-orders/{id}/actions/assign` | 分派 |
| POST | `/api/work-orders/{id}/actions/accept` | 接单 |
| POST | `/api/work-orders/{id}/actions/reject` | 拒单 |
| POST | `/api/work-orders/{id}/actions/record-progress` | 追加处理记录 |
| POST | `/api/work-orders/{id}/actions/submit-completion` | 首次或返工后提交完工 |
| POST | `/api/work-orders/{id}/actions/verify-pass` | 验收通过 |
| POST | `/api/work-orders/{id}/actions/request-rework` | 验收不通过 |
| POST | `/api/work-orders/{id}/reviews` | 关闭后评价 |

## M1/M2 Port 要求

`SqlAlchemyRepairUnitOfWork` 的 `shared_port_factory` 必须为当前 SQLAlchemy `Session` 创建以下适配器：

- `IdempotencyPort`：以 `(actor_id, operation, key)` 唯一；同键异参返回冲突；成功结果保存 `response_snapshot`。
- `ConfirmationPort`：原子校验并消费用户、动作、参数哈希、有效期和重放状态。
- `HouseAccessPort`：服务端验证房屋归属，不能信任请求体中的范围信息。
- `StaffDirectoryPort`：验证被分派人是本小区维修人员。
- `AttachmentPort`：验证附件归属、上传状态、文件类型和访问权限。
- `AuditPort`、`MessagePort`：在同一事务中追加审计与站内消息任务。

这些 Port 不得使用内存 fake 作为生产默认值。测试实现位于 `tests/support.py`。

生产集成必须显式调用 `create_app(service)` 注入已装配的 `WorkOrderService`。报修模块不提供
使用 fake backend 的默认生产应用。`RequestContext.request_id` 必须是 1–64 个非空白字符，
HTTP 入口会拒绝沿用超长或空白的外部 `X-Request-ID`。

## Agent 工具

`RepairToolAdapter` 暴露 `search_work_orders`、`create_work_order` 和 `execute_work_order_action`。对应 JSON Schema 位于 `TOOL_SCHEMAS`，M2 可按所选 Agent 框架进行注册。

`HIGH_RISK` 创建请求返回 `HANDOVER_REQUIRED`，不会产生普通工单。调用方应转人工或安防事件流程。
