"""Platform-owned fencing contract used at authoritative write boundaries."""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class Lease:
    """Trusted lease snapshot carried from orchestration to a business UoW."""

    thread_id: str
    run_id: UUID
    fence: int
    lease_until: datetime


class StaleAgentRunError(RuntimeError):
    """Raised when a worker no longer owns the conversation lease."""

    status_code = 409

    def __init__(self, thread_id: str, *, reason: str = "lease expired or preempted") -> None:
        self.thread_id = thread_id
        self.reason = reason
        super().__init__(f"stale agent run for conversation {thread_id}: {reason}")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def assert_run_fence(session: Session, lease: Lease) -> None:
    """Lock and validate the current lease inside the business transaction."""
    statement = text(
        """
        UPDATE agent_run_leases
        SET updated_at = updated_at
        WHERE thread_id = :thread_id
          AND owner_run_id = :owner_run_id
          AND fence = :fence
          AND lease_until >= :now
        RETURNING 1
        """
    )
    row = session.execute(
        statement,
        {
            "thread_id": lease.thread_id,
            "owner_run_id": str(lease.run_id),
            "fence": lease.fence,
            "now": _normalize(_utcnow()),
        },
    ).first()
    if row is None:
        raise StaleAgentRunError(
            lease.thread_id, reason="fence check failed (expired, preempted, or mismatched)"
        )


__all__ = ["Lease", "StaleAgentRunError", "assert_run_fence"]
