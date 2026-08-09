from collections.abc import Callable
from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from property_agent.inspection.application.ports import (
    AttachmentPort,
    AuditPort,
    ConfirmationPort,
    EscalationPort,
    IdempotencyPort,
    MessagePort,
    SharedPorts,
    StaffDirectoryPort,
)
from property_agent.inspection.infrastructure.repository import SqlAlchemyInspectionRepository


class SqlAlchemyInspectionUnitOfWork:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        shared_port_factory: Callable[..., SharedPorts],
    ) -> None:
        self._session_factory = session_factory
        self._shared_port_factory = shared_port_factory

    def __enter__(self) -> "SqlAlchemyInspectionUnitOfWork":
        self.session = self._session_factory()
        self.repository = SqlAlchemyInspectionRepository(self.session)
        ports: SharedPorts = self._shared_port_factory(self.session)
        self.idempotency: IdempotencyPort = ports.idempotency
        self.confirmations: ConfirmationPort = ports.confirmations
        self.staff_directory: StaffDirectoryPort = ports.staff_directory
        self.attachments: AttachmentPort = ports.attachments
        self.audit: AuditPort = ports.audit
        self.messages: MessagePort = ports.messages
        self.escalation: EscalationPort = ports.escalation
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
