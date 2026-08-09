"""
infrastructure/database.py     数据库连接

纯连接管理，不负责建表/建库。
建表请执行 sql/ddl.sql，种子数据请执行 sql/seed.sql。

环境变量 DB_URL 控制连接目标，默认 SQLite。

────────────────────────────────────────────────────────
SQL 连接配置:
────────────────────────────────────────────────────────
  -- SQLite (开发/测试):
  --   DB_URL=sqlite:///backend/data/property_fee.db

  -- PostgreSQL (生产):
  --   DB_URL=postgresql://user:pass@host:5432/property_fee

────────────────────────────────────────────────────────
数据库表 (6 张，启动时自动创建):
────────────────────────────────────────────────────────
  community_buildings   楼栋信息表   (PK: building_id)
  community_rooms       房号信息表   (PK: room_id, FK: building_id)
  sys_users             用户表       (PK: user_id, FK: building_id, room_id)
  fee_bills             账单主表     (PK: bill_id, FK: user_id, room_id)
  fee_payments          缴费记录表   (PK: payment_id, FK: bill_id, user_id)
  fee_receipts          电子票据表   (PK: receipt_no, FK: bill_id, user_id, payment_id)

────────────────────────────────────────────────────────
调用链:
────────────────────────────────────────────────────────
  backend/main.py: startup_event()
    → Base.metadata.create_all(bind=engine)
      → SQL: CREATE TABLE IF NOT EXISTS community_buildings (...);
      → SQL: CREATE TABLE IF NOT EXISTS community_rooms (...);
      → SQL: CREATE TABLE IF NOT EXISTS sys_users (...);
      → SQL: CREATE TABLE IF NOT EXISTS fee_bills (...);
      → SQL: CREATE TABLE IF NOT EXISTS fee_payments (...);
      → SQL: CREATE TABLE IF NOT EXISTS fee_receipts (...);

  adapters/api/routes.py: list_bills(db=Depends(get_db))
    → get_db() → SessionLocal()
      → SQL: CONNECT TO property_fee;
      → yield db
      → finally: db.close()
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .orm_models import Base

# ═══════════════════════════════════════════════════════════════
# 数据库 URL 配置
# ═══════════════════════════════════════════════════════════════

# SQL: 默认使用 SQLite 文件数据库，路径为 backend/data/property_fee.db
DB_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "backend" / "data"
DB_DIR.mkdir(parents=True, exist_ok=True)

# SQL: 通过环境变量 DB_URL 切换数据库
# PostgreSQL 示例: postgresql://property_admin:password@localhost:5432/property_fee
DATABASE_URL = os.environ.get("DB_URL", f"sqlite:///{DB_DIR / 'property_fee.db'}")

# ═══════════════════════════════════════════════════════════════
# 引擎与会话工厂
# ═══════════════════════════════════════════════════════════════

# SQL: SQLite 引擎配置（单线程模式，开发用）
# SQL: PostgreSQL 引擎配置（连接池模式，生产用）
if "sqlite" in DATABASE_URL:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
else:
    # SQL: PostgreSQL 连接池配置: pool_size=10, max_overflow=20
    engine = create_engine(DATABASE_URL, pool_size=10, max_overflow=20, echo=False)

# SQL: 会话工厂，每次请求创建新会话
# 等价于: conn = psycopg2.connect(DATABASE_URL); cursor = conn.cursor()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_billing_db() -> None:
    """Create all billing tables (idempotent) in the billing database.

    PRD 6.3 added ``billing_rules`` and ``billing_consultations`` plus new
    columns on ``fee_bills``; ``create_all`` provisions them without dropping
    existing data. Production deployments should still run the Alembic chain,
    but ``create_all`` keeps local development and test paths self-contained.
    """

    Base.metadata.create_all(bind=engine)


# 轻量接入: 启动时确保账单库表存在（幂等）。
init_billing_db()


# ═══════════════════════════════════════════════════════════════
# FastAPI 依赖注入
# ═══════════════════════════════════════════════════════════════


def get_db() -> Session:
    """
    FastAPI 依赖注入：获取数据库会话。

    使用方式:
        @app.get("/api/bills")
        def get_bills(db: Session = Depends(get_db)):
            ...

    SQL 等价:
        -- 每个请求开始时获取连接
        BEGIN;
        -- 请求处理...
        COMMIT;  -- 或 ROLLBACK;
        -- 请求结束时释放连接
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════════
# 直接获取会话
# ═══════════════════════════════════════════════════════════════


def get_db_session() -> Session:
    """
    直接获取数据库会话（非 FastAPI 上下文使用）。

    使用方式:
        db = get_db_session()
        try:
            repo = SqlAlchemyBillRepository(db)
            bills = repo.find_by_user("user_101")
        finally:
            db.close()

    SQL 等价:
        -- 获取独立数据库连接
        CONNECT TO property_fee;
    """
    return SessionLocal()
