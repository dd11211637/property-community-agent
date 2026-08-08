from collections.abc import Callable
from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from property_agent.announcement.application.ports import AnnouncementUnitOfWork
from property_agent.announcement.infrastructure.repository import SqlAlchemyAnnouncementRepository


class SqlAlchemyAnnouncementUnitOfWork:
    def __init__(
        self, session_factory: sessionmaker[Session], shared_port_factory: Callable
    ) -> None:
        self._session_factory = session_factory
        self._shared_port_factory = shared_port_factory

    def __enter__(self) -> AnnouncementUnitOfWork:
        self.session = self._session_factory()
        self.announcements = SqlAlchemyAnnouncementRepository(self.session)
        ports = self._shared_port_factory(self.session)
        self.idempotency = ports.idempotency
        self.confirmations = ports.confirmations
        self.audiences = ports.audiences
        self.audit = ports.audit
        self.messages = ports.messages
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
