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
from property_agent.billing.infrastructure.shared_ports import build_billing_ports
from property_agent.billing.infrastructure.source_port import LocalBillingSourcePort
from property_agent.platform.application.approval_service import ApprovalService
from property_agent.platform.infrastructure.orm_models import CommunityModel


class SqlAlchemyBillingUnitOfWork:
    """Bind billing repositories, audit, idempotency and confirmation to one Session."""

    def __init__(
        self, session: Session, approval_service: ApprovalService, *, enforce_fence: bool = False
    ) -> None:
        self._session = session
        self.source = LocalBillingSourcePort(SqlAlchemyBillRepository(session))
        self.rules = SqlAlchemyBillingRuleRepository(session)
        self.consultations = SqlAlchemyConsultationRepository(session)
        ports = build_billing_ports(session, approval_service, enforce_fence=enforce_fence)
        self.idempotency = ports["idempotency"]
        self.audit = ports["audit"]
        self.confirmations = ports["confirmations"]

    def community_code(self, community_id: UUID) -> str:
        community = self._session.get(CommunityModel, community_id)
        if community is None:
            raise BillingError("COMMUNITY_NOT_FOUND", "社区不存在", 404)
        return community.name

    def commit(self) -> None:
        self._session.commit()
