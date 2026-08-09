# Infrastructure

本目录保存 Compose 所需的生产形态容器构建文件：

- `backend.Dockerfile`：后端、迁移和种子任务的共用镜像。
- `frontend.Dockerfile`：前端构建与 Nginx 运行镜像。
- `nginx.conf`：SPA 回退及 `/api`、`/health`、`/ready` 反向代理。

根目录 `compose.yaml` 强制按 `postgres -> migrate -> seed -> backend -> frontend`
顺序启动，并使用持久化 PostgreSQL 卷。

密钥、令牌和真实用户数据不得提交到仓库。
