from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from property_agent.repair.application.ports import (
    AttachmentPort,
    AuditPort,
    ConfirmationPort,
    HandoverPort,
    HouseAccessPort,
    IdempotencyPort,
    MessagePort,
    StaffDirectoryPort,
)
from property_agent.repair.infrastructure.repository import SqlAlchemyWorkOrderRepository


@dataclass(frozen=True, slots=True)
class SharedPorts:
    idempotency: IdempotencyPort
    confirmations: ConfirmationPort
    house_access: HouseAccessPort
    staff_directory: StaffDirectoryPort
    attachments: AttachmentPort
    audit: AuditPort
    messages: MessagePort
    handover: HandoverPort


SharedPortFactory = Callable[[Session], SharedPorts]


class SqlAlchemyRepairUnitOfWork:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        shared_port_factory: SharedPortFactory,
    ) -> None:
        self._session_factory = session_factory
        self._shared_port_factory = shared_port_factory

    def __enter__(self) -> "SqlAlchemyRepairUnitOfWork":
        self.session = self._session_factory()
        self.work_orders = SqlAlchemyWorkOrderRepository(self.session)
        ports = self._shared_port_factory(self.session)
        self.idempotency = ports.idempotency
        self.confirmations = ports.confirmations
        self.house_access = ports.house_access
        self.staff_directory = ports.staff_directory
        self.attachments = ports.attachments
        self.audit = ports.audit
        self.messages = ports.messages
        self.handover = ports.handover
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None:
                self.rollback()
        finally:
            self.session.close()

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()
