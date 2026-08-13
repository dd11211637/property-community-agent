"""SQLAlchemy adapter for the billing application transaction boundary."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from property_agent.billing.errors import BillingError
from property_agent.billing.infrastructure.repositories import (
    SqlAlchemyBillingRuleRepository,
    SqlAlchemyBillRepository,
    SqlAlchemyConsultationRepository,
)
from property_agent.billing.infrastructure.shared_ports import (
    PlatformBillingAuditPort,
    SqlAlchemyBillingIdempotencyPort,
)
from property_agent.billing.infrastructure.source_port import LocalBillingSourcePort
from property_agent.platform.infrastructure.orm_models import CommunityModel


class SqlAlchemyBillingUnitOfWork:
    """Bind billing repositories, audit and idempotency to one request Session."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self.source = LocalBillingSourcePort(SqlAlchemyBillRepository(session))
        self.rules = SqlAlchemyBillingRuleRepository(session)
        self.consultations = SqlAlchemyConsultationRepository(session)
        self.idempotency = SqlAlchemyBillingIdempotencyPort(session)
        self.audit = PlatformBillingAuditPort(session)

    def community_code(self, community_id: UUID) -> str:
        community = self._session.get(CommunityModel, community_id)
        if community is None:
            raise BillingError("COMMUNITY_NOT_FOUND", "社区不存在", 404)
        return community.name

    def commit(self) -> None:
        self._session.commit()
