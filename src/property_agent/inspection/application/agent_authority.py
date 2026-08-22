"""Inspection projection for the shared Agent write authority boundary."""

from __future__ import annotations

from typing import Any

from property_agent.inspection.domain.enums import Role
from property_agent.inspection.domain.errors import (
    confirmation_required,
    forbidden,
    validation_error,
)
from property_agent.platform.application.agent_write_authority import (
    business_command,
    consume_agent_write,
)


def consume_inspection_agent_write(
    uow: Any,
    context: Any,
    command: Any,
    operation: str,
    **entity_refs: Any,
) -> None:
    parameters = {**entity_refs, **business_command(command)}
    consume_agent_write(uow, context, command, operation, parameters, confirmation_required)


def require_inspection_idempotency_key(key: str) -> None:
    if not key or not key.strip() or len(key) > 128:
        raise validation_error("Idempotency-Key is required and must not exceed 128 characters.")


def inspection_operator_role(context: Any) -> Role:
    for role in (Role.MANAGER, Role.SECURITY_STAFF, Role.CUSTOMER_SERVICE, Role.RESIDENT):
        if role in context.roles:
            return role
    raise forbidden()
