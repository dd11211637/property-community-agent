"""Announcement content normalization shared by create and edit commands."""

from property_agent.announcement.application.commands import (
    CreateAnnouncementCommand,
    EditAnnouncementCommand,
)
from property_agent.announcement.domain.errors import validation_error
from property_agent.announcement.domain.policies import (
    normalize_audience_condition,
    validate_category,
)
from property_agent.platform.validation import required_text


def validated_content(command: CreateAnnouncementCommand | EditAnnouncementCommand):
    title = required_text(command.title, "title is required.")
    body = required_text(command.body, "body is required.")
    if len(title) > 128:
        raise validation_error("title must not exceed 128 characters.")
    category = validate_category(command.category)
    return title, body, category, normalize_audience_condition(command.audience_condition)
