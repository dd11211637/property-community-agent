"""Canonical static repair and billing capability catalog."""

from property_agent.agent.capabilities.adapters.billing import (
    BillingConsultInput,
    BillingConsultOutput,
    BillingQueryInput,
    BillingQueryOutput,
)
from property_agent.agent.capabilities.adapters.repair import (
    RepairCreateInput,
    RepairCreateOutput,
    RepairGetInput,
    RepairGetOutput,
    RepairListInput,
    RepairListOutput,
)
from property_agent.agent.capabilities.contracts import (
    ApprovalPosture,
    CapabilityPresentation,
    CapabilityRisk,
    CapabilitySpec,
)
from property_agent.agent.capabilities.registry import CapabilityRegistry


def capability_specs() -> tuple[CapabilitySpec, ...]:
    return (
        CapabilitySpec(
            "repair_list",
            "repair",
            "List work orders visible in the trusted resident scope.",
            RepairListInput,
            RepairListOutput,
            CapabilityRisk.READ,
            ApprovalPosture.NONE,
            CapabilityPresentation("查询报修记录"),
            frozenset({"read", "controlled-read"}),
        ),
        CapabilitySpec(
            "repair_get",
            "repair",
            "Get one visible work order and its timeline.",
            RepairGetInput,
            RepairGetOutput,
            CapabilityRisk.READ,
            ApprovalPosture.NONE,
            CapabilityPresentation("查看报修详情"),
            frozenset({"read", "controlled-read"}),
        ),
        CapabilitySpec(
            "repair_create",
            "repair",
            "Create a work order through WorkOrderService.",
            RepairCreateInput,
            RepairCreateOutput,
            CapabilityRisk.WRITE_LOW_RISK,
            ApprovalPosture.POLICY,
            CapabilityPresentation("提交报修", "确认提交这条报修吗？"),
            frozenset({"write"}),
        ),
        CapabilitySpec(
            "billing_query",
            "billing",
            "Query bills or billing rules in trusted resident scope.",
            BillingQueryInput,
            BillingQueryOutput,
            CapabilityRisk.READ,
            ApprovalPosture.NONE,
            CapabilityPresentation("查询账单"),
            frozenset({"read", "controlled-read"}),
        ),
        CapabilitySpec(
            "billing_consult",
            "billing",
            "Create a billing consultation draft through ConsultationService.",
            BillingConsultInput,
            BillingConsultOutput,
            CapabilityRisk.WRITE_LOW_RISK,
            ApprovalPosture.POLICY,
            CapabilityPresentation("提交账单咨询", "确认提交这条费用咨询吗？"),
            frozenset({"write"}),
        ),
    )


def default_capability_registry() -> CapabilityRegistry:
    return CapabilityRegistry(capability_specs())
