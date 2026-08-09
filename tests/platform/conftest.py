"""
Platform module conftest — shared fixtures for platform tests.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import create_engine

from property_agent.platform.infrastructure.orm_models import (
    Base,
    CommunityModel,
    HouseModel,
    UserHouseBindingModel,
    UserModel,
    UserRoleModel,
)
from property_agent.platform.services.auth import hash_password

# Pre-compute a bcrypt hash for the demo password "123456"
DEMO_PASSWORD = "123456"
DEMO_HASH = hash_password(DEMO_PASSWORD)


@pytest.fixture
def engine():
    """In-memory SQLite engine for testing."""
    eng = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    """A fresh session for each test."""
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=engine)
    sess = SessionLocal()
    yield sess
    sess.rollback()
    sess.close()


@pytest.fixture
def community_a_id() -> UUID:
    return UUID("a0000000-0000-0000-0000-000000000001")


@pytest.fixture
def community_b_id() -> UUID:
    return UUID("b0000000-0000-0000-0000-000000000002")


@pytest.fixture
def seed_data(session, community_a_id, community_b_id):
    """Seed minimal test data with bcrypt-hashed passwords."""
    # Communities
    session.add(CommunityModel(id=community_a_id, name="Test Community A", status="ACTIVE"))
    session.add(CommunityModel(id=community_b_id, name="Test Community B", status="ACTIVE"))

    # Houses
    house_a1 = UUID("a1000000-0000-0000-0000-000000000101")
    house_a2 = UUID("a1000000-0000-0000-0000-000000000102")
    session.add(
        HouseModel(
            id=house_a1,
            community_id=community_a_id,
            building="1",
            unit="1",
            room_no="101",
            status="ACTIVE",
        )
    )
    session.add(
        HouseModel(
            id=house_a2,
            community_id=community_a_id,
            building="1",
            unit="1",
            room_no="102",
            status="ACTIVE",
        )
    )

    # Users (bcrypt-hashed password)
    user_a = UUID("a2000000-0000-0000-0000-000000000001")
    user_b = UUID("a2000000-0000-0000-0000-000000000002")
    session.add(
        UserModel(
            id=user_a,
            community_id=community_a_id,
            username="resident1",
            display_name="Resident One",
            password_hash=DEMO_HASH,
            status="ACTIVE",
        )
    )
    session.add(
        UserModel(
            id=user_b,
            community_id=community_a_id,
            username="manager1",
            display_name="Manager One",
            password_hash=DEMO_HASH,
            status="ACTIVE",
        )
    )

    # Roles
    session.add(UserRoleModel(user_id=user_a, role="RESIDENT", scope="*"))
    session.add(UserRoleModel(user_id=user_b, role="MANAGER", scope="*"))

    # Bindings (user_a bound to house_a1)
    session.add(UserHouseBindingModel(user_id=user_a, house_id=house_a1, status="ACTIVE"))

    session.commit()

    return {
        "community_a": community_a_id,
        "community_b": community_b_id,
        "house_a1": house_a1,
        "house_a2": house_a2,
        "user_a": user_a,
        "user_b": user_b,
    }
