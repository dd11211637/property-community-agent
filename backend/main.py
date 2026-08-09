"""
物业社区管理AI智能体 - 统一后端入口 (FastAPI)

DDD 分层架构 · 费用查询与智能缴费模块

────────────────────────────────────────────────────────
项目结构:
────────────────────────────────────────────────────────
  src/property_agent/
  ├── billing/                    费用查询与智能缴费模块
  │   ├── domain/                 领域层 (实体、值对象、枚举、状态机、业务规则)
  │   ├── application/            应用层 (用例、命令、查询、端口、DTO)
  │   ├── adapters/               适配器层 (HTTP API、Schema、Agent 工具)
  │   └── infrastructure/         基础设施层 (数据库、ORM、仓储、LLM、支付)
  ├── platform/                   公共平台能力
  ├── repair/                     报修
  ├── announcement/               公告
  ├── inspection/                 巡检与安防
  ├── agent/                      智能体编排
  └── integrations/               外部系统适配

────────────────────────────────────────────────────────
API 端点:
────────────────────────────────────────────────────────
  GET    /                             前端页面
  GET    /api/users/{user_id}          用户信息
  GET    /api/bills                    账单列表（分页）
  GET    /api/bills/detail/{bill_id}   账单详情
  GET    /api/bills/export             账单导出
  POST   /api/bills/interpret          账单解读
  POST   /api/bills/pay                缴费
  POST   /api/bills/pay/batch          批量缴费
  POST   /api/bills/refund             退款
  POST   /api/bills/cancel             取消账单
  POST   /api/bills/generate           生成账单
  POST   /api/bills/schedule-overdue   逾期检查
  GET    /api/bills/payments/history   支付历史
  GET    /api/bills/receipt/{no}       电子票据
  GET    /docs                         Swagger 文档
  GET    /redoc                        ReDoc 文档

────────────────────────────────────────────────────────
启动方式:
────────────────────────────────────────────────────────
  # 方式一: 一键启动 (start.bat)
  start.bat

  # 方式二: 手动启动
  cd backend
  pip install -r requirements.txt
  python -m uvicorn main:app --host 0.0.0.0 --port 8080 --reload

  # 方式三: 指定数据库
  set DB_URL=postgresql://user:pass@localhost:5432/property_fee
  python -m uvicorn main:app --host 0.0.0.0 --port 8080 --reload

────────────────────────────────────────────────────────
数据库:
────────────────────────────────────────────────────────
  默认: SQLite (backend/data/property_fee.db)
  切换: set DB_URL=postgresql://user:pass@host:5432/property_fee
  建表: 启动时自动创建 (Base.metadata.create_all)
  种子: 执行 backend/sql/seed.sql
"""
from __future__ import annotations
import sys
from pathlib import Path

# 确保 src 目录在 Python 路径中
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

# ── 导入业务模块路由 ──────────────────────────────────

from property_agent.billing.adapters.api.routes import router as billing_router

# ── 导入基础设施依赖 ──────────────────────────────────

from property_agent.billing.infrastructure.database import get_db, engine
from property_agent.billing.infrastructure.orm_models import Base
from property_agent.billing.infrastructure.repositories import (
    SqlAlchemyUserRepository,
)
from property_agent.billing.application.use_cases import BillQueryUseCase

# ═══════════════════════════════════════════════════════════════
# FastAPI 应用初始化
# ═══════════════════════════════════════════════════════════════

app = FastAPI(
    title="物业社区管理AI智能体",
    version="2.0.0",
    description="DDD 分层架构 · 费用查询与智能缴费模块 · 12 个 API 端点",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS 中间件 ───────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 挂载业务模块路由 ──────────────────────────────────

app.include_router(billing_router)

# ═══════════════════════════════════════════════════════════════
# 启动事件: 自动建表
# ═══════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup_event():
    """
    应用启动时自动创建数据库表（如果不存在）。

    SQL:
        CREATE TABLE IF NOT EXISTS community_buildings (...);
        CREATE TABLE IF NOT EXISTS community_rooms (...);
        CREATE TABLE IF NOT EXISTS sys_users (...);
        CREATE TABLE IF NOT EXISTS fee_bills (...);
        CREATE TABLE IF NOT EXISTS fee_payments (...);
        CREATE TABLE IF NOT EXISTS fee_receipts (...);
    """
    Base.metadata.create_all(bind=engine)
    print("[OK] 数据库表已就绪")

# ═══════════════════════════════════════════════════════════════
# 用户接口（跨模块公共）
# ═══════════════════════════════════════════════════════════════

@app.get("/api/users/{user_id}")
async def get_user_info(
    user_id: str,
    db: Session = Depends(get_db),
):
    """
    获取用户信息。

    调用链:
        HTTP GET /api/users/user_101
          → get_user_info(user_id)
            → BillQueryUseCase.get_user(user_id)
              → SqlAlchemyUserRepository.find_by_id(user_id)
                → SQL: SELECT u.*, b.building_name, r.room_number
                       FROM sys_users u
                       LEFT JOIN community_buildings b ON u.building_id = b.building_id
                       LEFT JOIN community_rooms r ON u.room_id = r.room_id
                       WHERE u.user_id = :user_id;
              → UserDTO → dict

    SQL:
        SELECT u.user_id, u.user_name, u.role, u.phone,
               b.building_name, r.room_number
          FROM sys_users u
          LEFT JOIN community_buildings b ON u.building_id = b.building_id
          LEFT JOIN community_rooms r ON u.room_id = r.room_id
         WHERE u.user_id = :user_id;
    """
    user_repo = SqlAlchemyUserRepository(db)
    user = user_repo.find_by_id(user_id)
    if not user:
        raise HTTPException(404, f"用户 {user_id} 不存在")

    from property_agent.billing.application.dtos import user_to_dto
    from dataclasses import asdict
    return asdict(user_to_dto(user))

# ═══════════════════════════════════════════════════════════════
# 前端静态文件
# ═══════════════════════════════════════════════════════════════

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.get("/")
async def serve_frontend():
    """
    服务前端 SPA 页面。

    返回: frontend/index.html
    """
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {
        "message": "前端页面未找到，请确保 frontend/index.html 存在",
        "api_docs": "/docs",
        "api_endpoints": {
            "bills": "/api/bills?user_id=user_101&role=owner",
            "interpret": "/api/bills/interpret",
            "pay": "/api/bills/pay",
            "receipt": "/api/bills/receipt/REC_20260510_101",
            "history": "/api/bills/payments/history",
        }
    }

# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)