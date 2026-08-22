"""Factories that assemble capability dependencies without business logic."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from property_agent.agent.capabilities.adapters.announcement import (
    AnnouncementCreateAdapter,
    AnnouncementDraftAdapter,
    AnnouncementGetAdapter,
    AnnouncementListAdapter,
    AnnouncementPublishAdapter,
    AnnouncementReviseAdapter,
    AnnouncementScheduleAdapter,
    CommunityKnowledgeAdapter,
)
from property_agent.agent.capabilities.adapters.billing import (
    BillingConsultAdapter,
    BillingQueryAdapter,
)
from property_agent.agent.capabilities.adapters.inspection import InspectionAdapter
from property_agent.agent.capabilities.adapters.repair import (
    RepairCreateAdapter,
    RepairGetAdapter,
    RepairListAdapter,
)
from property_agent.agent.capabilities.catalog import default_capability_registry
from property_agent.agent.capabilities.contracts import CapabilityRuntimeContext
from property_agent.agent.capabilities.executor import CapabilityExecutor, ObservationHook
from property_agent.agent.capabilities.policy import default_capability_policy

SessionProvider = Callable[[CapabilityRuntimeContext], Any]


def build_capability_executor(
    *,
    work_order_service: Any,
    billing_service: Any,
    consultation_service: Any,
    billing_session_provider: SessionProvider,
    observe: ObservationHook | None = None,
    announcement_service: Any | None = None,
    announcement_model_gateway: Any | None = None,
    inspection_task_service: Any | None = None,
    inspection_event_service: Any | None = None,
) -> CapabilityExecutor:
    adapters = {
        "repair_list": RepairListAdapter(work_order_service),
        "repair_get": RepairGetAdapter(work_order_service),
        "repair_create": RepairCreateAdapter(work_order_service),
        "billing_query": BillingQueryAdapter(billing_service, billing_session_provider),
        "billing_consult": BillingConsultAdapter(consultation_service, billing_session_provider),
    }
    if announcement_service is not None and announcement_model_gateway is not None:
        adapters.update(
            {
                "announcement_list": AnnouncementListAdapter(announcement_service),
                "announcement_get": AnnouncementGetAdapter(announcement_service),
                "community_knowledge_search": CommunityKnowledgeAdapter(announcement_service),
                "announcement_draft": AnnouncementDraftAdapter(announcement_model_gateway),
                "announcement_revise": AnnouncementReviseAdapter(announcement_model_gateway),
                "announcement_create_draft": AnnouncementCreateAdapter(announcement_service),
                "announce_publish": AnnouncementPublishAdapter(announcement_service),
                "announcement_schedule_publish": AnnouncementScheduleAdapter(announcement_service),
            }
        )
    if inspection_task_service is not None and inspection_event_service is not None:
        for name in (
            "inspection_list",
            "inspection_get_task",
            "inspection_get_event",
            "inspection_create",
            "inspection_create_task",
            "inspection_start_task",
            "inspection_add_record",
            "inspection_submit_record",
            "inspection_submit_records",
            "inspection_ai_suggest",
            "security_event_create",
            "security_event_submit_disposal",
        ):
            adapters[name] = InspectionAdapter(
                inspection_task_service, inspection_event_service, name
            )
    return CapabilityExecutor(
        default_capability_registry(), default_capability_policy(), adapters, observe=observe
    )
