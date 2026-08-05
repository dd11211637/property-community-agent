"""
数据库连接模块 - 物业社区管理AI智能体

纯连接管理，不负责建表/建库/种子数据。
建表语句请参考 sql/ddl.sql，种子数据请参考 sql/seed.sql。

环境变量 DB_URL 控制连接目标，默认 SQLite。
"""
from __future__ import annotations
import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from models_db import Base

# ── 数据库 URL 配置 ───────────────────────────────────

DB_DIR = Path(__file__).resolve().parent / "data"
DB_DIR.mkdir(exist_ok=True)

# 默认 SQLite，可通过环境变量切换 PostgreSQL:
#   set DB_URL=postgresql://user:pass@localhost:5432/property_fee
DATABASE_URL = os.environ.get("DB_URL", f"sqlite:///{DB_DIR / 'property_fee.db'}")

# ── 引擎与会话工厂 ────────────────────────────────────

if "sqlite" in DATABASE_URL:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
else:
    engine = create_engine(
        DATABASE_URL,
        pool_size=10,
        max_overflow=20,
        echo=False,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    """获取数据库会话（FastAPI 依赖注入）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_session() -> Session:
    """直接获取数据库会话（非 FastAPI 上下文）"""
    return SessionLocal()