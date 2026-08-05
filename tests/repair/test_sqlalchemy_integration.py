from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from property_agent.repair.application.commands import CreateWorkOrderCommand
from property_agent.repair.application.service import WorkOrderService
from property_agent.repair.domain.entities import WorkOrder
from property_agent.repair.domain.enums import (
    ActionCode,
    RepairCategory,
    Urgency,
)
from property_agent.repair.domain.errors import BusinessError
from property_agent.repair.infrastructure.database import session_factory_from_engine
from property_agent.repair.infrastructure.models import (
    Base,
    WorkOrderModel,
    WorkOrderStatusLogModel,
)
from property_agent.repair.infrastructure.repository import SqlAlchemyWorkOrderRepository
from property_agent.repair.infrastructure.uow import (
    SharedPorts,
    SqlAlchemyRepairUnitOfWork,
)
from tests.conftest import Ids
from tests.repair.support import (
    FakeAttachments,
    FakeAudit,
    FakeConfirmation,
    FakeHouseAccess,
    FakeIdempotency,
    FakeMessages,
    FakeStaffDirectory,
    FakeState,
)


def make_shared_ports(state: FakeState, ids: Ids, *, fail_audit: bool = False):
    confirmation = FakeConfirmation()

    def factory(session: Session) -> SharedPorts:
        return SharedPorts(
            idempotency=FakeIdempotency(state),
            confirmations=confirmation,
            house_access=FakeHouseAccess({ids.house}),
            staff_directory=FakeStaffDirectory({ids.repair_worker}),
            attachments=FakeAttachments(),
            audit=FakeAudit(state, fail=fail_audit),
            messages=FakeMessages(state),
        )

    return factory


def test_repair_tables_and_transaction_rollback(ids, resident_context) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = session_factory_from_engine(engine)
    state = FakeState()
    service = WorkOrderService(
        lambda: SqlAlchemyRepairUnitOfWork(
            sessions, make_shared_ports(state, ids, fail_audit=True)
        )
    )

    with pytest.raises(RuntimeError, match="audit failure"):
        service.create(
            CreateWorkOrderCommand(
                house_id=ids.house,
                category=RepairCategory.WATER_PLUMBING,
                location="Kitchen",
                description="Leak",
                urgency=Urgency.NORMAL,
                confirmation_token="confirmed",
            ),
            resident_context,
            idempotency_key="rollback-create",
        )

    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(WorkOrderModel)) == 0
        assert (
            session.scalar(select(func.count()).select_from(WorkOrderStatusLogModel))
            == 0
        )


def test_repository_uses_atomic_optimistic_lock() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = session_factory_from_engine(engine)
    ids = [uuid4() for _ in range(5)]
    order = WorkOrder(
        id=ids[0],
        community_id=ids[1],
        business_no="WX-LOCK-1",
        house_id=ids[2],
        reporter_id=ids[3],
        category=RepairCategory.ELECTRICAL,
        location="Living room",
        description="No power",
        urgency=Urgency.NORMAL,
        create_idempotency_key="lock-create",
    )
    with sessions() as session:
        repository = SqlAlchemyWorkOrderRepository(session)
        repository.add(order)
        session.commit()

    first_session = sessions()
    second_session = sessions()
    try:
        first_repository = SqlAlchemyWorkOrderRepository(first_session)
        second_repository = SqlAlchemyWorkOrderRepository(second_session)
        first = first_repository.get(order.id, order.community_id)
        second = second_repository.get(order.id, order.community_id)
        assert first is not None and second is not None

        first.assignee_id = ids[4]
        first.transition(ActionCode.ASSIGN)
        first_repository.save(first)
        first_session.commit()

        second.assignee_id = uuid4()
        second.transition(ActionCode.ASSIGN)
        with pytest.raises(BusinessError) as error:
            second_repository.save(second)
        assert error.value.code == "VERSION_CONFLICT"
    finally:
        first_session.close()
        second_session.close()


def test_create_idempotency_key_is_scoped_to_reporter() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = session_factory_from_engine(engine)
    community_id, first_reporter, second_reporter = (uuid4() for _ in range(3))

    def new_order(reporter_id, business_no):
        return WorkOrder(
            id=uuid4(),
            community_id=community_id,
            business_no=business_no,
            house_id=uuid4(),
            reporter_id=reporter_id,
            category=RepairCategory.ELECTRICAL,
            location="Living room",
            description="No power",
            urgency=Urgency.NORMAL,
            create_idempotency_key="shared-client-key",
        )

    with sessions() as session:
        repository = SqlAlchemyWorkOrderRepository(session)
        repository.add(new_order(first_reporter, "WX-SCOPE-1"))
        repository.add(new_order(second_reporter, "WX-SCOPE-2"))
        session.commit()

    with sessions() as session:
        repository = SqlAlchemyWorkOrderRepository(session)
        repository.add(new_order(first_reporter, "WX-SCOPE-3"))
        with pytest.raises(IntegrityError):
            session.commit()
