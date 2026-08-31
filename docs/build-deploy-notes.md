# 构建与部署说明（本地 Windows / 中文用户名环境）

> 适用范围：本机开发联调与镜像构建。生产部署到服务器时，第 1 节的 "ASCII 路径" 限制不再适用（服务器路径通常为 ASCII），但第 2、3、4 节仍然有效。

## 1. 关键约束：构建必须用 ASCII 真实路径

**现象**：从 ASCII 软链（指向包含非 ASCII 字符的真实仓库路径）执行 `docker compose build`
会失败，报：

```
header key "x-docker-expose-session-sharedkey" contains value with non-printable ASCII characters
```

**原因**：BuildKit 会解析构建上下文的**真实路径**；软链解析到含中文的路径后，
BuildKit 的 gRPC session header 拒绝非 ASCII 字符，导致构建中断（与 Dockerfile 内容无关）。

**解决**：维护一份**真实 ASCII 路径**的仓库副本，所有 `docker compose build/up`
都从这份副本执行：

```powershell
# 1) 用 robocopy 同步一份 ASCII 路径副本（排除 .git / node_modules / .workbuddy / 编译缓存）
$src = "<仓库真实路径>"
$dst = "D:\pca-build"
robocopy $src $dst /MIR /XD .git node_modules .workbuddy /XF *.pyc

# 2) 所有构建/启动都从 D:\pca-build 跑
cd D:\pca-build
docker compose build
docker compose up -d
```

- `/MIR` 是镜像同步：改完源码后**重新 robocopy 再 build** 即可把改动烤进镜像。
- 不要从指向非 ASCII 真实路径的软链直接 build（会触发上面的错误）。
- 增量同步很快；首次约 127MB（已排除 `.git`/`node_modules`）。

## 2. Pip 镜像源

`infra/backend.Dockerfile` 默认注入清华镜像，规避容器内直连 PyPI 时 TLS 被重置：

```dockerfile
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
```

生产/CI 如需官方源，覆盖即可：

```powershell
docker compose build --build-arg PIP_INDEX_URL=https://pypi.org/simple
```

## 3. 本地全栈启动

```powershell
cd D:\pca-build
docker compose up -d
```

启动顺序（compose 依赖链）：`postgres(:5432)` → `migrate`(alembic) →
`langgraph-setup` → `seed` → `backend(:8000)` → `frontend(:5173)`。

- 后端健康检查：`GET /ready` 返回 200 即就绪。
- 前端：`http://localhost:5173`。

## 4. 测试

- **针对性回归（不依赖数据库）**：
  `tests/agent/test_repair_appointment_required.py` —— 验证报修创建必须收集预约时间。
  也可在已运行的 backend 容器内临时验证：
  ```powershell
  docker cp tests/agent/test_repair_appointment_required.py property-community-agent-backend-1:/app/
  docker exec -u root property-community-agent-backend-1 sh -c "cd /app && PYTHONPATH=/app/src python -m pytest test_repair_appointment_required.py -q"
  ```

- **全量回归（含 Postgres）**：
  ```powershell
  cd D:\pca-build
  docker compose --profile testing up postgres-tests
  ```
  该服务基于 Dockerfile 的 `test` target（已拷贝 `tests/`），连接 `postgres-test`
  容器执行 `pytest -rs`。

## 5. 本会话修复：报修未询问预约时间即建单

- **根因**：活的槽位门控是 `RepairCreateInput` 的 Pydantic 必填校验；原
  `appointment_at` 有默认值 `None` → 校验永远通过 → 编排层认为槽位齐全 → 直接建单。
- **修复**：
  1. `agent/capabilities/adapters/repair.py`：`appointment_at` 改为**必填**（去默认值），
     缺失即 `INVALID_CAPABILITY_INPUT`。
  2. `agent/specialists/repair.py`：`project_parameters` 在用户未回答预约时间时
     **有意不传**该字段以触发追问；答具体时间或"稍后协商"（解析为 `None` 延期）才放行。
  3. `agent/policies.py` / `repair/application/commands.py`：一致性补 `appointment_at`，
     并修正 dataclass 字段顺序（避免 migrate 容器导入崩溃）。
- **状态**：构建镜像已含修复；针对性回归测试 5/5 通过；"稍后协商"延期路径未被破坏。
