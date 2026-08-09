"""
Demo data seed script for platform module (PRD 8.1).

Generates:
  - Community A (primary demo) and Community B (cross-community isolation test)
  - Single-house residents, multi-house residents, unbound users
  - Customer service, repair workers, finance, security, duty, manager, system admin
  - Active, inactive, and expired house bindings

Usage:
  python -m testing.seeds.seed_platform
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import sessionmaker

# Ensure the project root is on sys.path
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root / "src"))

from property_agent.platform.infrastructure.database import get_engine  # noqa: E402
from property_agent.platform.infrastructure.orm_models import (  # noqa: E402
    CommunityModel,
    HouseModel,
    UserHouseBindingModel,
    UserModel,
    UserRoleModel,
)
from property_agent.platform.services.auth import hash_password  # noqa: E402

# ═══════════════════════════════════════════════════════════════
# Fixed UUIDs for demo accounts (deterministic, easy to reference)
# ═══════════════════════════════════════════════════════════════

COMMUNITY_A = UUID("a0000000-0000-0000-0000-000000000001")
COMMUNITY_B = UUID("b0000000-0000-0000-0000-000000000002")

# Houses in Community A
HOUSE_A_101 = UUID("a1000000-0000-0000-0000-000000000101")
HOUSE_A_102 = UUID("a1000000-0000-0000-0000-000000000102")
HOUSE_A_201 = UUID("a1000000-0000-0000-0000-000000000201")
HOUSE_A_202 = UUID("a1000000-0000-0000-0000-000000000202")

# Houses in Community B
HOUSE_B_101 = UUID("b1000000-0000-0000-0000-000000000101")

# Users in Community A
RESIDENT_A_SINGLE = UUID("a2000000-0000-0000-0000-000000000001")  # 张三 - single house
RESIDENT_A_MULTI = UUID("a2000000-0000-0000-0000-000000000002")  # 李四 - multi house
RESIDENT_A_NOBIND = UUID("a2000000-0000-0000-0000-000000000003")  # 王五 - no binding
CS_A = UUID("a2000000-0000-0000-0000-000000000010")  # 客服小刘
REPAIR_A = UUID("a2000000-0000-0000-0000-000000000020")  # 维修工老张
FINANCE_A = UUID("a2000000-0000-0000-0000-000000000030")  # 财务小陈
SECURITY_A = UUID("a2000000-0000-0000-0000-000000000040")  # 安保老李
DUTY_A = UUID("a2000000-0000-0000-0000-000000000050")  # 值班人员
MANAGER_A = UUID("a2000000-0000-0000-0000-000000000060")  # 王经理
SYSADMIN_A = UUID("a2000000-0000-0000-0000-000000000070")  # 系统管理员

# User in Community B (for cross-community isolation test)
RESIDENT_B = UUID("b2000000-0000-0000-0000-000000000001")  # 钱七 - Community B


def seed(engine) -> bool:
    """Insert deterministic platform data after Alembic migrations.

    Returns ``True`` when rows were inserted and ``False`` when the complete
    demo dataset was already present. Schema ownership belongs to Alembic, so
    this function deliberately never calls ``metadata.create_all``.
    """
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()

    try:
        existing = session.get(CommunityModel, COMMUNITY_A)
        if existing is not None:
            if session.get(CommunityModel, COMMUNITY_B) is None:
                raise RuntimeError("Partial demo seed detected; run the guarded reset command.")
            print("Platform demo data already present; skipping.")
            return False
        _seed_communities(session)
        _seed_houses(session)
        _seed_users(session)
        _seed_roles(session)
        _seed_bindings(session)
        session.commit()
        print("Demo data seeded successfully.")
        return True
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _seed_communities(session) -> None:
    session.add_all(
        [
            CommunityModel(
                id=COMMUNITY_A,
                name="幸福小区",
                status="ACTIVE",
            ),
            CommunityModel(
                id=COMMUNITY_B,
                name="阳光花园",
                status="ACTIVE",
            ),
        ]
    )
    print("  [+] 2 communities seeded")


def _seed_houses(session) -> None:
    session.add_all(
        [
            # Community A houses
            HouseModel(
                id=HOUSE_A_101,
                community_id=COMMUNITY_A,
                building="1栋",
                unit="1单元",
                room_no="101",
                area=100.0,
                status="ACTIVE",
            ),
            HouseModel(
                id=HOUSE_A_102,
                community_id=COMMUNITY_A,
                building="1栋",
                unit="1单元",
                room_no="102",
                area=100.0,
                status="ACTIVE",
            ),
            HouseModel(
                id=HOUSE_A_201,
                community_id=COMMUNITY_A,
                building="2栋",
                unit="1单元",
                room_no="201",
                area=120.0,
                status="ACTIVE",
            ),
            HouseModel(
                id=HOUSE_A_202,
                community_id=COMMUNITY_A,
                building="2栋",
                unit="1单元",
                room_no="202",
                area=120.0,
                status="ACTIVE",
            ),
            # Community B house
            HouseModel(
                id=HOUSE_B_101,
                community_id=COMMUNITY_B,
                building="1栋",
                unit="1单元",
                room_no="101",
                area=90.0,
                status="ACTIVE",
            ),
        ]
    )
    print("  [+] 5 houses seeded")


def _seed_users(session) -> None:
    # Hash demo password once (all demo accounts use the same password)
    DEMO_PASSWORD = "123456"
    password_hash = hash_password(DEMO_PASSWORD)
    session.add_all(
        [
            # Community A — Residents
            UserModel(
                id=RESIDENT_A_SINGLE,
                community_id=COMMUNITY_A,
                username="zhangsan",
                display_name="张三",
                password_hash=password_hash,
                phone="138****6789",
                status="ACTIVE",
            ),
            UserModel(
                id=RESIDENT_A_MULTI,
                community_id=COMMUNITY_A,
                username="lisi",
                display_name="李四",
                password_hash=password_hash,
                phone="138****6790",
                status="ACTIVE",
            ),
            UserModel(
                id=RESIDENT_A_NOBIND,
                community_id=COMMUNITY_A,
                username="wangwu",
                display_name="王五",
                password_hash=password_hash,
                phone="138****6791",
                status="ACTIVE",
            ),
            # Community A — Staff
            UserModel(
                id=CS_A,
                community_id=COMMUNITY_A,
                username="customer_service",
                display_name="客服小刘",
                password_hash=password_hash,
                phone="139****8901",
                status="ACTIVE",
            ),
            UserModel(
                id=REPAIR_A,
                community_id=COMMUNITY_A,
                username="repair_worker",
                display_name="维修工老张",
                password_hash=password_hash,
                phone="139****8902",
                status="ACTIVE",
            ),
            UserModel(
                id=FINANCE_A,
                community_id=COMMUNITY_A,
                username="finance",
                display_name="财务小陈",
                password_hash=password_hash,
                phone="139****8903",
                status="ACTIVE",
            ),
            UserModel(
                id=SECURITY_A,
                community_id=COMMUNITY_A,
                username="security_guard",
                display_name="安保老李",
                password_hash=password_hash,
                phone="139****8904",
                status="ACTIVE",
            ),
            UserModel(
                id=DUTY_A,
                community_id=COMMUNITY_A,
                username="duty_officer",
                display_name="值班人员",
                password_hash=password_hash,
                phone="139****8905",
                status="ACTIVE",
            ),
            UserModel(
                id=MANAGER_A,
                community_id=COMMUNITY_A,
                username="manager",
                display_name="王经理",
                password_hash=password_hash,
                phone="137****0123",
                status="ACTIVE",
            ),
            UserModel(
                id=SYSADMIN_A,
                community_id=COMMUNITY_A,
                username="sysadmin",
                display_name="系统管理员",
                password_hash=password_hash,
                phone="137****0124",
                status="ACTIVE",
            ),
            # Community B — Resident
            UserModel(
                id=RESIDENT_B,
                community_id=COMMUNITY_B,
                username="qianqi",
                display_name="钱七",
                password_hash=password_hash,
                phone="138****6792",
                status="ACTIVE",
            ),
        ]
    )
    print("  [+] 11 users seeded")


def _seed_roles(session) -> None:
    session.add_all(
        [
            # Residents
            UserRoleModel(user_id=RESIDENT_A_SINGLE, role="RESIDENT", scope="*"),
            UserRoleModel(user_id=RESIDENT_A_MULTI, role="RESIDENT", scope="*"),
            UserRoleModel(user_id=RESIDENT_A_NOBIND, role="RESIDENT", scope="*"),
            # Staff
            UserRoleModel(user_id=CS_A, role="CUSTOMER_SERVICE", scope="*"),
            UserRoleModel(user_id=REPAIR_A, role="REPAIR_WORKER", scope="*"),
            UserRoleModel(user_id=FINANCE_A, role="FINANCE", scope="*"),
            UserRoleModel(user_id=SECURITY_A, role="SECURITY_GUARD", scope="*"),
            UserRoleModel(user_id=DUTY_A, role="CUSTOMER_SERVICE", scope="*"),
            UserRoleModel(user_id=MANAGER_A, role="MANAGER", scope="*"),
            UserRoleModel(user_id=SYSADMIN_A, role="SYSTEM_ADMIN", scope="*"),
            # Community B
            UserRoleModel(user_id=RESIDENT_B, role="RESIDENT", scope="*"),
        ]
    )
    print("  [+] 11 roles seeded")


def _seed_bindings(session) -> None:
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            # Single-house resident: 张三 -> 1-1-101
            UserHouseBindingModel(
                user_id=RESIDENT_A_SINGLE,
                house_id=HOUSE_A_101,
                status="ACTIVE",
            ),
            # Multi-house resident: 李四 -> 1-1-102 + 2-1-201
            UserHouseBindingModel(
                user_id=RESIDENT_A_MULTI,
                house_id=HOUSE_A_102,
                status="ACTIVE",
            ),
            UserHouseBindingModel(
                user_id=RESIDENT_A_MULTI,
                house_id=HOUSE_A_201,
                status="ACTIVE",
            ),
            # Expired binding (for testing)
            UserHouseBindingModel(
                user_id=RESIDENT_A_SINGLE,
                house_id=HOUSE_A_202,
                status="EXPIRED",
                valid_until=now - timedelta(days=30),
            ),
            # Community B resident
            UserHouseBindingModel(
                user_id=RESIDENT_B,
                house_id=HOUSE_B_101,
                status="ACTIVE",
            ),
        ]
    )
    print("  [+] 5 bindings seeded")


if __name__ == "__main__":
    engine = get_engine()
    seed(engine)
    print("Done.")
