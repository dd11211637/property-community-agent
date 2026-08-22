"""Factories that assemble capability dependencies without business logic."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from property_agent.agent.capabilities.adapters.billing import (
    BillingConsultAdapter,
    BillingQueryAdapter,
)
from property_agent.agent.capabilities.adapters.repair import (
    RepairCreateAdapter,
    RepairGetAdapter,
    RepairListAdapter,
)
from property_agent.agent.capabilities.catalog import default_capability_registry
from property_agent.agent.capabilities.contracts import CapabilityRuntimeContext
from property_agent.agent.capabilities.executor import CapabilityExecutor, ObservationHook
from property_agent.agent.capabilities.policy import CapabilityPolicy

SessionProvider = Callable[[CapabilityRuntimeContext], Any]


def build_capability_executor(
    *,
    work_order_service: Any,
    billing_service: Any,
    consultation_service: Any,
    billing_session_provider: SessionProvider,
    observe: ObservationHook | None = None,
) -> CapabilityExecutor:
    adapters = {
        "repair_list": RepairListAdapter(work_order_service),
        "repair_get": RepairGetAdapter(work_order_service),
        "repair_create": RepairCreateAdapter(work_order_service),
        "billing_query": BillingQueryAdapter(billing_service, billing_session_provider),
        "billing_consult": BillingConsultAdapter(consultation_service, billing_session_provider),
    }
    return CapabilityExecutor(
        default_capability_registry(), CapabilityPolicy(), adapters, observe=observe
    )
