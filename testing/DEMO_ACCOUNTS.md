# 演示账号

所有账号密码均为 `123456`，仅用于 `ENV=demo` 的本地 Compose 环境。

| 账号 | 角色 | 房屋情况 |
| --- | --- | --- |
| `zhangsan` | 住户 | 单房屋，自动选择 |
| `lisi` | 住户 | 多房屋，需选择 |
| `wangwu` | 住户 | 无有效房屋，用于 403 |
| `customer_service` | 客服 | 公告、报修协调 |
| `repair_worker` | 维修工 | 报修处理 |
| `finance` | 财务 | Billing 咨询处理 |
| `security_guard` | 安保 | 巡检和安防事件 |
| `manager` | 管理者 | 公告审核、高风险处理 |
| `sysadmin` | 系统管理员 | 平台管理 |
| `qianqi` | 社区 B 住户 | 跨社区隔离验证 |

`duty_officer` 是值班人员的客服角色账号。

## 启动与重置

```powershell
.\scripts\compose.ps1 Up
.\scripts\compose.ps1 Reset
```

真实 PostgreSQL 仓储与 Alembic 测试使用独立的临时数据库：

```powershell
.\scripts\compose.ps1 Test
```

## 故障注入

故障入口仅在显式叠加 Demo Compose 时生效：

```powershell
$env:DEMO_FAIL_MODEL="true"
docker compose -f compose.yaml -f testing/compose.demo.yaml up --build
```

可用开关：`DEMO_FAIL_BILLING_SOURCE`、`DEMO_FAIL_MESSAGE_TRANSPORT`、`DEMO_FAIL_MODEL`。
