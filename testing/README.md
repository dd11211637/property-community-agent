# Testing Support

本目录保存独立压测和本地验证支持，不得被生产模块导入，也不作为生产启动依赖。

- `seeds/`：只在 Alembic 迁移后写入可重复的演示数据。
- `reset/`：只允许重置本地 `property_agent_demo` 数据库的受控工具。
- `demo_app.py`、`compose.demo.yaml`：独立故障注入入口。
- `DEMO_ACCOUNTS.md`：账号、重置和故障开关说明。
