# Inspection（巡检与安防事件）

巡检与安防事件业务模块，负责计划/路线、到点打卡、记录提交、AI 异常建议、事件分级、处置、复核与关闭。

本模块包含两个聚合：

- `InspectionTask`（巡检任务）：创建计划 → 分派 → 开始 → 提交记录 → 完成。
- `SecurityEvent`（安防事件）：上报 → 分派处置人 → 提交处置 → 复核 → 关闭。

## 状态与动作

### 巡检任务

| 当前状态 | 动作 | 目标状态 | 操作者 |
|---|---|---|---|
| `PLANNED` | `ASSIGN` | `ASSIGNED` | 管理者 |
| `ASSIGNED` | `START` | `IN_PROGRESS` | 被分派安保人员 |
| `IN_PROGRESS` | `SUBMIT_RECORDS` | `SUBMITTED` | 被分派安保人员（需确认令牌） |
| `SUBMITTED` | `COMPLETE` | `COMPLETED` | 管理者 |
| `IN_PROGRESS`/`SUBMITTED` | `ADD_RECORD` | 不变（仅追加记录，递增版本） | 被分派安保人员 |

### 安防事件

| 当前状态 | 动作 | 目标状态 | 操作者 |
|---|---|---|---|
| `REPORTED` | `ASSIGN` | `ASSIGNED` | 管理者 |
| `ASSIGNED` | `SUBMIT_DISPOSAL` | `PENDING_REVIEW` | 被分派处置人 |
| `PENDING_REVIEW` | `REVIEW_PASS` | `CLOSED` | 管理者（高风险需人工确认等级） |
| `PENDING_REVIEW` | `RETURN` | `ASSIGNED` | 管理者（需原因） |

高风险事件（`HIGH_RISK`）创建时向值班人员发送通知，但**不能由 AI 关闭**；关闭必须经管理者复核，且高风险事件的等级与处置方案在复核通过时由管理者确认。

## 与平台的关系

模块只依赖稳定 Port，不直接依赖具体供应商。共享端口集合为：幂等、确认、安保人员目录、附件、审计、消息（巡检实体为小区/路线级，不依赖房屋访问端口）。这些端口的生产实现由 `platform` 模块提供；测试实现位于 `tests/inspection_support.py`。

创建安防事件与提交巡检记录属于"执行前必须确认"操作，需携带确认令牌。所有写操作均需 `Idempotency-Key`，状态动作需 `expected_version`，审计与消息在同一事务中追加。
