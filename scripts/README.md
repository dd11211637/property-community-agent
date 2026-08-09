# Scripts

本目录用于可重复执行的项目支持脚本，例如：

- 开发环境初始化
- 数据库迁移
- 受控演示数据种子
- 演示环境启动和健康检查

脚本不应被生产核心模块导入。临时算法验证和一次性实验不要放入本目录。

`compose.ps1` 是 Windows 本地联调入口：

- `Up`：构建并启动完整环境。
- `Test`：在独立临时 PostgreSQL 中运行真实仓储和迁移测试。
- `Reset`：受控重置 `property_agent_demo`。
- `Config`：仅校验 Compose 配置。
- `Down`：停止环境，不删除持久化卷。
