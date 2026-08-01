from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from property_agent.inspection.adapters.api.app import create_app
from property_agent.inspection.adapters.api.dependencies import (
    get_event_service,
    get_request_context,
    get_task_service,
)
from property_agent.inspection.application.ports import RequestContext
from property_agent.inspection.application.service import (
    InspectionTaskService,
    SecurityEventService,
)
from property_agent.inspection.domain.enums import Role
from tests.inspection.support import Harness


@dataclass(frozen=True)
class Ids:
    community: UUID
    house: UUID
    other_house: UUID
    resident: UUID
    customer_service: UUID
    security_worker: UUID
    other_security: UUID
    manager: UUID
    duty_user: UUID


@pytest.fixture
def ids() -> Ids:
    return Ids(*(uuid4() for _ in range(9)))


@pytest.fixture
def harness(ids: Ids) -> Harness:
    return Harness(
        security_workers={ids.security_worker, ids.other_security},
        duty_users=[ids.duty_user],
    )


@pytest.fixture
def task_service(harness: Harness) -> InspectionTaskService:
    return InspectionTaskService(harness.uow)


@pytest.fixture
def event_service(harness: Harness) -> SecurityEventService:
    return SecurityEventService(harness.uow)


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
def cs_context(ids: Ids) -> RequestContext:
    return RequestContext(
        actor_id=ids.customer_service,
        community_id=ids.community,
        roles=frozenset({Role.CUSTOMER_SERVICE}),
        request_id="req_cs",
    )


@pytest.fixture
def security_context(ids: Ids) -> RequestContext:
    return RequestContext(
        actor_id=ids.security_worker,
        community_id=ids.community,
        roles=frozenset({Role.SECURITY_STAFF}),
        request_id="req_security",
    )


@pytest.fixture
def manager_context(ids: Ids) -> RequestContext:
    return RequestContext(
        actor_id=ids.manager,
        community_id=ids.community,
        roles=frozenset({Role.MANAGER}),
        request_id="req_manager",
    )


@pytest.fixture
def client_maker():
    def _make(task_service, event_service, context):
        app = create_app(task_service, event_service)
        app.dependency_overrides[get_task_service] = lambda: task_service
        app.dependency_overrides[get_event_service] = lambda: event_service
        app.dependency_overrides[get_request_context] = lambda: context
        return TestClient(app)

    return _make
