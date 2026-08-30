"""Validation and normalization for optional repair service details."""

from typing import Any

from property_agent.repair.application.commands import CreateWorkOrderCommand
from property_agent.repair.domain.errors import validation_error


def normalized_service_details(command: CreateWorkOrderCommand) -> dict[str, Any]:
    return {
        "contact_name": _optional_text(command.contact_name),
        "contact_phone": _optional_text(command.contact_phone),
        "access_instructions": _optional_text(command.access_instructions),
        "preferred_time_windows": tuple(
            _required_text(value) for value in command.preferred_time_windows
        ),
        "request_attachment_ids": command.attachment_ids,
    }


def validate_service_details(command: CreateWorkOrderCommand) -> None:
    for value, maximum, name in (
        (command.contact_name, 128, "contact_name"),
        (command.contact_phone, 32, "contact_phone"),
        (command.access_instructions, 1000, "access_instructions"),
    ):
        if value and len(value.strip()) > maximum:
            raise validation_error(f"{name} must not exceed {maximum} characters.")
    if len(command.preferred_time_windows) > 5:
        raise validation_error("At most 5 preferred service times are allowed.")
    if any(len(value.strip()) > 128 for value in command.preferred_time_windows):
        raise validation_error("A preferred service time must not exceed 128 characters.")


def _required_text(value: str) -> str:
    if not value.strip():
        raise validation_error("Preferred service time cannot be blank.")
    return value.strip()


def _optional_text(value: str | None) -> str | None:
    return value.strip() if value and value.strip() else None
