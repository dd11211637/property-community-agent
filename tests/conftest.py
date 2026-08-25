import os
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text

from property_agent.repair.application.ports import RequestContext
from property_agent.repair.application.service import WorkOrderService
from property_agent.repair.domain.enums import Role
from tests.support import Harness


@pytest.fixture(scope="session", autouse=True)
def _enable_postgres_test_extensions() -> None:
    """Prepare extensions required by ORM-based PostgreSQL test schemas."""
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        return
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    finally:
        engine.dispose()


@dataclass(frozen=True)
class Ids:
    community: UUID
    house: UUID
    other_house: UUID
    resident: UUID
    customer_service: UUID
    repair_worker: UUID
    other_worker: UUID
    manager: UUID


@pytest.fixture
def ids() -> Ids:
    return Ids(*(uuid4() for _ in range(8)))


@pytest.fixture
def harness(ids: Ids) -> Harness:
    return Harness(
        houses={ids.house},
        repair_workers={ids.repair_worker, ids.other_worker},
    )


@pytest.fixture
def service(harness: Harness) -> WorkOrderService:
    return WorkOrderService(harness.uow)


@pytest.fixture
def resident_context(ids: Ids) -> RequestContext:
    return RequestContext(
        actor_id=ids.resident,
        community_id=ids.community,
        roles=frozenset({Role.RESIDENT}),
        house_ids=frozenset({ids.house}),
        request_id="req_resident",
    )


@pytest.fixture
def customer_service_context(ids: Ids) -> RequestContext:
    return RequestContext(
        actor_id=ids.customer_service,
        community_id=ids.community,
        roles=frozenset({Role.CUSTOMER_SERVICE}),
        request_id="req_customer_service",
    )


@pytest.fixture
def repair_context(ids: Ids) -> RequestContext:
    return RequestContext(
        actor_id=ids.repair_worker,
        community_id=ids.community,
        roles=frozenset({Role.REPAIR_WORKER}),
        request_id="req_repair",
    )


@pytest.fixture
def manager_context(ids: Ids) -> RequestContext:
    return RequestContext(
        actor_id=ids.manager,
        community_id=ids.community,
        roles=frozenset({Role.MANAGER}),
        request_id="req_manager",
    )
