# API Contracts

本目录维护前端、后端和 Agent 共同依赖的 API 契约。

接口变更应记录请求、响应、错误码、权限、幂等要求和版本兼容影响，并与实现和测试一起评审。

## 统一约定

- 除登录接口外，业务接口使用 Bearer JWT；当前房屋由 `X-Current-House-ID` 或已验证请求体字段指定。
- 写接口要求 `Idempotency-Key`；状态变更同时提交 `expected_version`，冲突返回 409。
- 二次确认由 `/api/confirmations` 生成短期令牌，令牌绑定操作者、房屋、动作和参数摘要。
- 业务响应使用 `success/data/error/request_id` Envelope；401、403、404、409、422、503 保留不同语义。
- 消息查询按接收者隔离；管理聚合仅允许管理角色访问。

[`openapi.json`](openapi.json) 由统一生产组合根导出，不包含 `testing/` 故障入口：

```powershell
.\.venv\Scripts\python.exe scripts/export_openapi.py
```
