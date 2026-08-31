"""Typed announcement capability adapters to ``AnnouncementService``."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from property_agent.agent.capabilities.contracts import (
    CapabilityDomainError,
    CapabilityInput,
    CapabilityOutput,
    CapabilityRuntimeContext,
)
from property_agent.announcement.application.commands import (
    AnnouncementSearch,
    CreateAnnouncementCommand,
    ReviewActionCommand,
    ScheduleAnnouncementCommand,
)
from property_agent.announcement.domain.classification import classify_announcement_category
from property_agent.announcement.domain.enums import AnnouncementAction, VersionSource
from property_agent.announcement.domain.errors import BusinessError


@contextmanager
def _translate_errors():
    try:
        yield
    except BusinessError as exc:
        raise CapabilityDomainError(exc.code, exc.message, details=dict(exc.details or {})) from exc


class AnnouncementListInput(CapabilityInput):
    statuses: tuple[str, ...] = ()
    limit: int = Field(default=20, ge=1, le=100)
    topic: str | None = None
    target_date: str | None = None


class AnnouncementGetInput(CapabilityInput):
    announcement_id: UUID


class AnnouncementDraftInput(CapabilityInput):
    topic: str = Field(min_length=1, max_length=200)
    audience: dict[str, Any]
    requirements: str = Field(default="", max_length=4000)


class AnnouncementReviseInput(CapabilityInput):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10000)
    audience: dict[str, Any]
    category: str | None = None
    revision_instruction: str = Field(min_length=1, max_length=4000)


class AnnouncementCreateInput(CapabilityInput):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10000)
    audience: dict[str, Any]


class AnnouncementPublishInput(CapabilityInput):
    announcement_id: UUID
    expected_version: int = Field(ge=1)


class AnnouncementScheduleInput(AnnouncementPublishInput):
    scheduled_at: datetime


class CommunityKnowledgeInput(CapabilityInput):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=10, ge=1, le=20)


class AnnouncementDataOutput(CapabilityOutput):
    data: dict[str, Any]


def _brief(item: Any) -> dict[str, Any]:
    return {
        "entity_type": "ANNOUNCEMENT",
        "id": str(item.id),
        "business_no": getattr(item, "business_no", None),
        "title": item.title,
        "body": item.body,
        "category": str(item.category),
        "status": str(item.status),
        "audience": getattr(item, "audience_condition", {}) or {},
        "scheduled_at": item.scheduled_at.isoformat() if item.scheduled_at else None,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "version": item.version,
    }


class AnnouncementListAdapter:
    def __init__(self, service: Any) -> None:
        self._service = service

    def __call__(self, request: AnnouncementListInput, runtime: CapabilityRuntimeContext):
        with _translate_errors():
            items = self._service.search(
                AnnouncementSearch(statuses=request.statuses, limit=request.limit),
                runtime.request_context,
            )
        if request.topic:
            terms = {
                "WATER_OUTAGE": ("停水", "供水"),
                "POWER_OUTAGE": ("停电", "供电"),
            }.get(request.topic, ())
            if terms:
                items = [
                    item
                    for item in items
                    if any(term in f"{item.title} {item.body}" for term in terms)
                ]
        return AnnouncementDataOutput(
            data={
                "count": len(items),
                "items": [_brief(x) for x in items],
                "topic": request.topic,
                "target_date": request.target_date,
            }
        )


class AnnouncementGetAdapter:
    def __init__(self, service: Any) -> None:
        self._service = service

    def __call__(self, request: AnnouncementGetInput, runtime: CapabilityRuntimeContext):
        with _translate_errors():
            item = self._service.get(request.announcement_id, runtime.request_context)
        return AnnouncementDataOutput(data={"announcement": _brief(item)})


class CommunityKnowledgeAdapter:
    def __init__(self, service: Any) -> None:
        self._service = service

    def __call__(self, request: CommunityKnowledgeInput, runtime: CapabilityRuntimeContext):
        with _translate_errors():
            candidates = self._service.search(
                AnnouncementSearch(statuses=("PUBLISHED",), limit=20), runtime.request_context
            )
        domain_terms = ("物业电话", "联系方式", "联系电话", "停车", "装修", "门禁", "垃圾")
        terms = [term for term in domain_terms if term in request.query]
        if any(term in request.query for term in ("物业电话", "联系方式", "联系电话")):
            terms = ["物业电话", "联系方式", "联系电话"]
        matches = [
            item
            for item in candidates
            if not terms or any(term in f"{item.title} {item.body}" for term in terms)
        ][: request.limit]
        return AnnouncementDataOutput(
            data={
                "count": len(matches),
                "items": [_brief(x) for x in matches],
                "query": request.query,
            }
        )


class AnnouncementDraftAdapter:
    def __init__(self, model_gateway: Any) -> None:
        self._gateway = model_gateway

    def __call__(self, request: AnnouncementDraftInput, runtime: CapabilityRuntimeContext):
        draft = self._gateway.draft_announcement(
            topic=request.topic, audience=request.audience, requirements=request.requirements
        )
        draft["category"] = classify_announcement_category(
            str(draft.get("title") or ""), str(draft.get("body") or "")
        ).value
        return AnnouncementDataOutput(data={"draft": {**draft, "audience": request.audience}})


class AnnouncementReviseAdapter:
    def __init__(self, model_gateway: Any) -> None:
        self._gateway = model_gateway

    def __call__(self, request: AnnouncementReviseInput, runtime: CapabilityRuntimeContext):
        revised = self._gateway.revise_announcement(
            draft={
                "title": request.title,
                "body": request.body,
                "category": request.category
                or classify_announcement_category(request.title, request.body).value,
            },
            audience=request.audience,
            instruction=request.revision_instruction,
        )
        revised["category"] = classify_announcement_category(
            str(revised.get("title") or ""), str(revised.get("body") or "")
        ).value
        return AnnouncementDataOutput(data={"draft": {**revised, "audience": request.audience}})


class AnnouncementCreateAdapter:
    def __init__(self, service: Any) -> None:
        self._service = service

    def __call__(self, request: AnnouncementCreateInput, runtime: CapabilityRuntimeContext):
        if runtime.write is None:
            raise RuntimeError("announcement_create_draft requires server write context")
        command = CreateAnnouncementCommand(
            request.title,
            request.body,
            classify_announcement_category(request.title, request.body),
            request.audience,
            source=VersionSource.AI_SUGGESTION_ADOPTED,
            confirmation_token=runtime.write.confirmation_token,
            approval_ref=runtime.write.approval_ref,
        )
        with _translate_errors():
            item = self._service.create_draft(
                command, runtime.request_context, idempotency_key=runtime.write.idempotency_key
            )
        return AnnouncementDataOutput(data={"announcement": _brief(item)})


class AnnouncementPublishAdapter:
    def __init__(self, service: Any) -> None:
        self._service = service

    def __call__(self, request: AnnouncementPublishInput, runtime: CapabilityRuntimeContext):
        if runtime.write is None:
            raise RuntimeError("announce_publish requires server write context")
        command = ReviewActionCommand(
            AnnouncementAction.PUBLISH,
            request.expected_version,
            confirmation_token=runtime.write.confirmation_token,
            approval_ref=runtime.write.approval_ref,
        )
        with _translate_errors():
            item = self._service.publish(
                request.announcement_id,
                command,
                runtime.request_context,
                idempotency_key=runtime.write.idempotency_key,
            )
        return AnnouncementDataOutput(data={"announcement": _brief(item)})


class AnnouncementScheduleAdapter:
    def __init__(self, service: Any) -> None:
        self._service = service

    def __call__(self, request: AnnouncementScheduleInput, runtime: CapabilityRuntimeContext):
        if runtime.write is None:
            raise RuntimeError("announcement_schedule_publish requires server write context")
        command = ScheduleAnnouncementCommand(
            request.expected_version,
            request.scheduled_at,
            runtime.write.confirmation_token,
            approval_ref=runtime.write.approval_ref,
        )
        with _translate_errors():
            item = self._service.schedule_publish(
                request.announcement_id,
                command,
                runtime.request_context,
                idempotency_key=runtime.write.idempotency_key,
            )
        return AnnouncementDataOutput(data={"announcement": _brief(item)})
