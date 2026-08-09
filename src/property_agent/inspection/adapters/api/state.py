from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from property_agent.inspection.application.service import (
        InspectionTaskService,
        SecurityEventService,
    )


@runtime_checkable
class InspectionAppState(Protocol):
    """Structural type for the attributes ``create_app`` attaches to ``app.state``."""

    task_service: InspectionTaskService | None
    event_service: SecurityEventService | None
