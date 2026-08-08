# 公告模块（P0）

## 边界和状态机

公告是按小区隔离的聚合；`RequestContext.actor_id`、`community_id` 和 `roles` 是唯一可信的身份与租户来源。请求体不得提供可被信任的小区、角色或收件人 ID。

```text
DRAFT -> PENDING_REVIEW -> APPROVED -> PUBLISHED -> ARCHIVED
              |                              |
              +-> REJECTED -> DRAFT           +-> WITHDRAWN
```

客服和管理员可创建、编辑、预览受众、提交审核；仅管理员可批准、拒绝、发布和撤回。拒绝必须带原因。已发布公告不能编辑正文，需撤回后新建草稿。

AI 只能经草稿建议契约提供可追溯的草稿内容；没有直接发布路径，Agent 工具也不会暴露发布或撤回操作。

## 稳定契约

公告应用层定义以下依赖，生产组合根必须提供真实实现；未配置服务的 HTTP 入口返回 `503 ADAPTER_NOT_CONFIGURED`，绝不回退到 Fake。

| Port | 责任 |
|---|---|
| `IdempotencyPort` | 按 `(actor_id, operation, key)` 去重；同键不同参数返回 `409 IDEMPOTENCY_CONFLICT`。 |
| `ConfirmationPort` | 原子校验并消费绑定操作人、动作、参数哈希和有效期的发布确认令牌。 |
| `AudienceResolverPort` | 只在当前小区内解析受限的楼栋、单元、户型条件，返回脱敏样例和成员快照。 |
| `AuditPort` | 在同一工作单元追加参数摘要、结果和 request ID；不记录完整正文或敏感联系方式。 |
| `MessagePort` | 发布事务中按受众快照写入站内消息 Outbox；投递状态独立于公告状态。 |
| `AnnouncementUnitOfWork` | 在同一事务协调公告、版本、审核、受众快照、撤回、审计和 Outbox。 |

受众条件是受限的结构化字段：`building_ids`、`unit_ids`、`house_types`。不接受任意表达式、SQL 或跨小区成员 ID。提交审核和发布时都会保存不可变受众快照；空受众返回 `422 EMPTY_AUDIENCE`。

高风险分类仅添加 `manager_recheck_required` 标记，不能绕过审核或发布权限。允许的初始分类由部署配置传入；默认值为 `GENERAL`、`MAINTENANCE`、`SAFETY`、`EMERGENCY`。

## HTTP 契约

所有响应采用平台 `Envelope`：`success`、`data`、`error`、`request_id`。所有写操作需要 `Idempotency-Key`；审核、发布、撤回还需要 `expected_version`，发布还需要 `confirmation_token`。

| 方法 | 路径 | 操作 |
|---|---|---|
| POST | `/api/announcements` | 创建草稿 |
| GET | `/api/announcements` | 授权范围内列表 |
| GET | `/api/announcements/{id}` | 详情 |
| PATCH | `/api/announcements/{id}` | 编辑草稿 |
| GET | `/api/announcements/{id}/audience-preview` | 受众预览 |
| POST | `/api/announcements/{id}/submit-review` | 提交审核 |
| POST | `/api/announcements/{id}/actions/approve` | 批准 |
| POST | `/api/announcements/{id}/actions/reject` | 拒绝 |
| POST | `/api/announcements/{id}/actions/publish` | 确认发布 |
| POST | `/api/announcements/{id}/actions/withdraw` | 撤回 |
| GET | `/api/announcements/{id}/versions` | 不可变版本历史 |

主要错误码：`AUTH_REQUIRED` (401)、`FORBIDDEN` (403)、`RESOURCE_NOT_FOUND` (404)、`VERSION_CONFLICT` / `INVALID_TRANSITION` / `IDEMPOTENCY_CONFLICT` (409)、`VALIDATION_ERROR` / `EMPTY_AUDIENCE` / `CONFIRMATION_REQUIRED` (422)、`ADAPTER_NOT_CONFIGURED` (503)。

## 人工验收

1. 客服创建定向两栋的草稿并提交审核；管理员批准，再使用确认摘要返回的令牌发布。
2. 确认 Outbox 只含冻结受众、公告含版本/审核/受众快照/审计记录；投递失败显示为 `FAILED` 而公告仍为 `PUBLISHED`。
3. 用客服身份审批或发布、跨小区筛选、使用失效确认令牌、空受众和重复幂等键均须被拒绝并留下审计。
4. 在专用 PostgreSQL 测试数据库执行 `alembic upgrade head` 与 `pytest -m postgres` 验证迁移和租户隔离。

## 启动与验证

运行 `property_agent.main:create_app` 后，公告 Router 已存在，但未装配生产服务时会明确返回
`503 ADAPTER_NOT_CONFIGURED`。生产组合根应使用 `SqlAlchemyAnnouncementUnitOfWork` 配置真实的
身份、确认、幂等、受众、审计及共享消息 Outbox Port；测试 Fake 仅位于 `tests/announcement`。

执行 `python -m pytest`、`python -m ruff check src tests alembic`、`python -m compileall -q src tests`。
需要迁移验证时设置独立的 `TEST_POSTGRES_URL`，执行 `alembic upgrade head` 和
`pytest -m postgres`；不得针对开发数据库执行重置操作。
