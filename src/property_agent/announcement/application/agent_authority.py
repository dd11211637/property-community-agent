"""Announcement create-draft Agent authority projection."""

from __future__ import annotations

from typing import Any

from property_agent.announcement.domain.errors import confirmation_required
from property_agent.platform.application.agent_write_authority import consume_agent_write


def create_draft_parameters(title, body, category, audience) -> dict[str, Any]:
    return {"title": title, "body": body, "category": category, "audience": audience}


def consume_create_draft(
    uow: Any,
    context: Any,
    command: Any,
    operation: str,
    parameters: dict[str, Any],
) -> None:
    consume_agent_write(uow, context, command, operation, parameters, confirmation_required)
