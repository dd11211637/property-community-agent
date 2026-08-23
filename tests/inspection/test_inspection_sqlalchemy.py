import os
import uuid

import pytest
from sqlalchemy import create_engine

from property_agent.inspection.adapters.api.dependencies import get_request_context  # noqa: F401
from property_agent.inspection.application.commands import (
    CreateInspectionTaskCommand,
    CreateSecurityEventCommand,
    ExecuteTaskActionCommand,
)
from property_agent.inspection.application.ports import RequestContext
from property_agent.inspection.application.service import (
    InspectionTaskService,
    SecurityEventService,
)
from property_agent.inspection.domain.enums import (
    EventRiskLevel,
    EventType,
    Role,
    TaskAction,
    TaskStatus,
)
from property_agent.inspection.infrastructure.database import create_session_factory
from property_agent.inspection.infrastructure.models import (
    Base,
    InspectionTaskModel,
    InspectionTaskRecordModel,
    InspectionTaskStatusLogModel,
    SecurityEventDisposalModel,
    SecurityEventModel,
    SecurityEventStatusLogModel,
)
from property_agent.inspection.infrastructure.uow import SqlAlchemyInspectionUnitOfWork
from property_agent.platform.context import ExecutionSource
from tests.inspection_support import (
    FakeAttachmentPort,
    FakeAuditPort,
    FakeConfirmationPort,
    FakeEscalationPort,
    FakeIdempotencyPort,
    FakeMessagePort,
    FakeStaffDirectoryPort,
    FakeState,
    SharedPorts,
)

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_URL, reason="requires TEST_POSTGRES_URL and a dedicated PostgreSQL database"
    ),
]

INSPECTION_TABLES = [
    InspectionTaskModel.__table__,
    InspectionTaskRecordModel.__table__,
    InspectionTaskStatusLogModel.__table__,
    SecurityEventModel.__table__,
    SecurityEventDisposalModel.__table__,
    SecurityEventStatusLogModel.__table__,
]


@pytest.fixture
def env():
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine, tables=INSPECTION_TABLES)
    yield engine
    Base.metadata.drop_all(engine, tables=INSPECTION_TABLES)
    engine.dispose()


def _uow_factory(engine, state, security_workers, duty_users):
    session_factory = create_session_factory(POSTGRES_URL)

    def factory():
        return SqlAlchemyInspectionUnitOfWork(
            session_factory,
            lambda session: SharedPorts(
                idempotency=FakeIdempotencyPort(state),
                confirmations=FakeConfirmationPort(state),
                staff_directory=FakeStaffDirectoryPort(state, security_workers, duty_users),
                attachments=FakeAttachmentPort(),
                audit=FakeAuditPort(state),
                messages=FakeMessagePort(state),
                escalation=FakeEscalationPort(state),
            ),
        )

    return factory


def test_task_persists_through_real_repository(env):
    community = uuid.uuid4()
    manager = uuid.uuid4()
    worker = uuid.uuid4()
    state = FakeState()
    factory = _uow_factory(env, state, {worker}, [uuid.uuid4()])
    task_service = InspectionTaskService(factory)
    event_service = SecurityEventService(factory)
    manager_ctx = RequestContext(
        actor_id=manager,
        community_id=community,
        roles=frozenset({Role.MANAGER}),
        request_id="pg-mgr",
        execution_source=ExecutionSource.HUMAN,
    )

    task = task_service.create_task(
        CreateInspectionTaskCommand(
            title="夜间巡检", description="B1-B3", route_points=("B1", "B2")
        ),
        manager_ctx,
        idempotency_key="pg-1",
    )
    task = task_service.execute_task_action(
        task.id,
        ExecuteTaskActionCommand(
            action=TaskAction.ASSIGN, expected_version=task.version, assignee_id=worker
        ),
        manager_ctx,
        idempotency_key="pg-assign",
    )
    assert task.status == TaskStatus.ASSIGNED

    # 用新 UoW（新会话）读取，验证已落库
    task2 = task_service.get_task(task.id, manager_ctx)
    assert task2 is not None
    assert task2.assignee_id == worker
    assert task2.status == TaskStatus.ASSIGNED

    # 安防事件高风险落库 + 值班通知
    sec_ctx = RequestContext(
        actor_id=worker,
        community_id=community,
        roles=frozenset({Role.SECURITY_STAFF}),
        request_id="pg-sec",
        execution_source=ExecutionSource.HUMAN,
    )
    event = event_service.create_event(
        CreateSecurityEventCommand(
            source_task_id=None,
            event_type=EventType.GAS_LEAK,
            risk_level=EventRiskLevel.HIGH_RISK,
            location="B2 车库",
            description="燃气气味",
            confirmation_token="ct-1",
        ),
        sec_ctx,
        idempotency_key="pg-ev",
    )
    assert event.status.value == "REPORTED"
    assert any(m["event_type"] == "HIGH_RISK_EVENT" for m in state.messages)
