from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from property_agent.announcement.adapters.presentation import (
    announcement_data,
    audience_snapshot_data,
)
from property_agent.announcement.application.commands import (
    AnnouncementSearch,
    CreateAnnouncementCommand,
    EditAnnouncementCommand,
    ReviewActionCommand,
)
from property_agent.announcement.application.service import AnnouncementService
from property_agent.announcement.domain.enums import AnnouncementAction, VersionSource
from property_agent.platform.context import RequestContext


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchAnnouncementsInput(ToolInput):
    statuses: list[str] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class CreateDraftInput(ToolInput):
    title: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1)
    category: str = Field(min_length=1, max_length=32)
    audience_condition: dict[str, list[str]] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=128)


class EditDraftInput(CreateDraftInput):
    announcement_id: UUID
    expected_version: int = Field(ge=1)


class PreviewAudienceInput(ToolInput):
    announcement_id: UUID


class SubmitReviewInput(ToolInput):
    announcement_id: UUID
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_announcements": {
        "name": "search_announcements",
        "description": "Search authorized announcements.",
        "parameters": SearchAnnouncementsInput.model_json_schema(),
    },
    "create_announcement_draft": {
        "name": "create_announcement_draft",
        "description": "Save an AI-assisted announcement only as a draft; it cannot publish.",
        "parameters": CreateDraftInput.model_json_schema(),
    },
    "edit_announcement_draft": {
        "name": "edit_announcement_draft",
        "description": "Edit a DRAFT or REJECTED announcement only.",
        "parameters": EditDraftInput.model_json_schema(),
    },
    "preview_announcement_audience": {
        "name": "preview_announcement_audience",
        "description": "Preview the server-resolved, masked audience for a draft.",
        "parameters": PreviewAudienceInput.model_json_schema(),
    },
    "submit_announcement_review": {
        "name": "submit_announcement_review",
        "description": "Submit a draft for human review; this never approves or publishes it.",
        "parameters": SubmitReviewInput.model_json_schema(),
    },
}


class AnnouncementToolAdapter:
    """Framework-neutral safe Agent tools. Publish and withdraw are intentionally absent."""

    def __init__(self, service: AnnouncementService) -> None:
        self._service = service

    def search_announcements(
        self, arguments: dict[str, Any], context: RequestContext
    ) -> dict[str, Any]:
        payload = SearchAnnouncementsInput.model_validate(arguments)
        items = self._service.search(
            AnnouncementSearch(tuple(payload.statuses), payload.limit, payload.offset), context
        )
        return {
            "items": [announcement_data(item, self._service, context) for item in items],
            "limit": payload.limit,
            "offset": payload.offset,
        }

    def create_announcement_draft(
        self, arguments: dict[str, Any], context: RequestContext
    ) -> dict[str, Any]:
        payload = CreateDraftInput.model_validate(arguments)
        item = self._service.create_draft(
            CreateAnnouncementCommand(
                payload.title,
                payload.body,
                payload.category,
                payload.audience_condition,
                source=VersionSource.AI_SUGGESTION_ADOPTED,
            ),
            context,
            idempotency_key=payload.idempotency_key,
        )
        return announcement_data(item, self._service, context)

    def edit_announcement_draft(
        self, arguments: dict[str, Any], context: RequestContext
    ) -> dict[str, Any]:
        payload = EditDraftInput.model_validate(arguments)
        item = self._service.edit_draft(
            payload.announcement_id,
            EditAnnouncementCommand(
                payload.title,
                payload.body,
                payload.category,
                payload.audience_condition,
                payload.expected_version,
                VersionSource.AI_SUGGESTION_ADOPTED,
            ),
            context,
            idempotency_key=payload.idempotency_key,
        )
        return announcement_data(item, self._service, context)

    def preview_announcement_audience(
        self, arguments: dict[str, Any], context: RequestContext
    ) -> dict[str, Any]:
        payload = PreviewAudienceInput.model_validate(arguments)
        return audience_snapshot_data(
            self._service.preview_audience(payload.announcement_id, context)
        )

    def submit_announcement_review(
        self, arguments: dict[str, Any], context: RequestContext
    ) -> dict[str, Any]:
        payload = SubmitReviewInput.model_validate(arguments)
        item = self._service.submit_review(
            payload.announcement_id,
            ReviewActionCommand(AnnouncementAction.SUBMIT_REVIEW, payload.expected_version),
            context,
            idempotency_key=payload.idempotency_key,
        )
        return announcement_data(item, self._service, context)
