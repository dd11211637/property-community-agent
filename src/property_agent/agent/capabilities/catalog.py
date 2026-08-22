"""Canonical static capability metadata for all PR3 Agent domains."""

from property_agent.agent.capabilities.adapters.announcement import (
    AnnouncementCreateInput,
    AnnouncementDataOutput,
    AnnouncementDraftInput,
    AnnouncementGetInput,
    AnnouncementListInput,
    AnnouncementPublishInput,
    AnnouncementReviseInput,
    AnnouncementScheduleInput,
    CommunityKnowledgeInput,
)
from property_agent.agent.capabilities.adapters.billing import (
    BillingConsultInput,
    BillingConsultOutput,
    BillingQueryInput,
    BillingQueryOutput,
)
from property_agent.agent.capabilities.adapters.inspection import (
    HighRiskCloseInput,
    InspectionAiSuggestInput,
    InspectionCreateInput,
    InspectionDataOutput,
    InspectionEventGetInput,
    InspectionListInput,
    InspectionRecordInput,
    InspectionTaskActionInput,
    InspectionTaskGetInput,
    SecurityDisposalInput,
    SecurityEventCreateInput,
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


def _core_specs() -> tuple[CapabilitySpec, ...]:
    core = (
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
    return core


def _announcement_specs() -> tuple[CapabilitySpec, ...]:
    return (
        _spec(
            "announcement_list",
            "announcement",
            AnnouncementListInput,
            AnnouncementDataOutput,
            "查询公告",
        ),
        _spec(
            "announcement_get",
            "announcement",
            AnnouncementGetInput,
            AnnouncementDataOutput,
            "查看公告",
        ),
        _spec(
            "community_knowledge_search",
            "announcement",
            CommunityKnowledgeInput,
            AnnouncementDataOutput,
            "查询社区知识",
        ),
        _spec(
            "announcement_draft",
            "announcement",
            AnnouncementDraftInput,
            AnnouncementDataOutput,
            "起草公告",
        ),
        _spec(
            "announcement_revise",
            "announcement",
            AnnouncementReviseInput,
            AnnouncementDataOutput,
            "修改公告稿",
        ),
        _spec(
            "announcement_create_draft",
            "announcement",
            AnnouncementCreateInput,
            AnnouncementDataOutput,
            "保存公告草稿",
            "确认采用这份 AI 稿件并保存为公告草稿吗？",
        ),
        _spec(
            "announce_publish",
            "announcement",
            AnnouncementPublishInput,
            AnnouncementDataOutput,
            "发布公告",
            "您已审阅最终稿，确认立即发布这份公告吗？",
        ),
        _spec(
            "announcement_schedule_publish",
            "announcement",
            AnnouncementScheduleInput,
            AnnouncementDataOutput,
            "定时发布公告",
            "您已审阅最终稿，确认按指定时间发布吗？",
        ),
    )


def _inspection_read_specs() -> tuple[CapabilitySpec, ...]:
    return (
        _spec(
            "inspection_list", "inspection", InspectionListInput, InspectionDataOutput, "查询巡检"
        ),
        _spec(
            "inspection_get_task",
            "inspection",
            InspectionTaskGetInput,
            InspectionDataOutput,
            "查看巡检任务",
        ),
        _spec(
            "inspection_get_event",
            "inspection",
            InspectionEventGetInput,
            InspectionDataOutput,
            "查看安防事件",
        ),
    )


def _inspection_write_specs() -> tuple[CapabilitySpec, ...]:
    return (
        _spec(
            "inspection_create",
            "inspection",
            InspectionCreateInput,
            InspectionDataOutput,
            "创建巡检任务",
            "确认创建这项巡检任务吗？",
        ),
        _spec(
            "inspection_create_task",
            "inspection",
            InspectionCreateInput,
            InspectionDataOutput,
            "创建巡检任务",
            "确认创建这项巡检任务吗？",
        ),
        _spec(
            "inspection_start_task",
            "inspection",
            InspectionTaskActionInput,
            InspectionDataOutput,
            "开始巡检",
            "确认开始执行这项巡检任务吗？",
        ),
        _spec(
            "inspection_add_record",
            "inspection",
            InspectionRecordInput,
            InspectionDataOutput,
            "追加巡检记录",
            "确认追加这条巡检记录吗？",
        ),
        _spec(
            "inspection_submit_record",
            "inspection",
            InspectionRecordInput,
            InspectionDataOutput,
            "追加巡检记录",
            "确认追加这条巡检记录吗？",
        ),
        _spec(
            "inspection_submit_records",
            "inspection",
            InspectionRecordInput,
            InspectionDataOutput,
            "提交巡检记录",
            "确认提交最终巡检记录吗？",
        ),
        _spec(
            "inspection_ai_suggest",
            "inspection",
            InspectionAiSuggestInput,
            InspectionDataOutput,
            "保存异常建议",
            "确认保存这条异常建议吗？",
        ),
    )


def _inspection_event_specs() -> tuple[CapabilitySpec, ...]:
    return (
        _spec(
            "security_event_create",
            "inspection",
            SecurityEventCreateInput,
            InspectionDataOutput,
            "上报安防事件",
            "确认上报这项安防事件吗？",
        ),
        _spec(
            "security_event_submit_disposal",
            "inspection",
            SecurityDisposalInput,
            InspectionDataOutput,
            "提交事件处置",
            "确认提交这项事件处置结果吗？",
        ),
        _spec(
            "close_high_risk_event",
            "inspection",
            HighRiskCloseInput,
            InspectionDataOutput,
            "关闭高风险事件",
            "需授权人工处理",
            human_only=True,
        ),
    )


def capability_specs() -> tuple[CapabilitySpec, ...]:
    return (
        _core_specs()
        + _announcement_specs()
        + _inspection_read_specs()
        + _inspection_write_specs()
        + _inspection_event_specs()
    )


def _spec(
    name,
    domain,
    input_type,
    output_type,
    title,
    confirmation_title=None,
    *,
    human_only=False,
):
    risk = (
        CapabilityRisk.WRITE_HIGH_RISK
        if human_only
        else (CapabilityRisk.WRITE_LOW_RISK if confirmation_title else CapabilityRisk.READ)
    )
    posture = (
        ApprovalPosture.HUMAN_ONLY
        if human_only
        else (ApprovalPosture.POLICY if confirmation_title else ApprovalPosture.NONE)
    )
    return CapabilitySpec(
        name,
        domain,
        f"Execute {name} through the authoritative Application Service boundary.",
        input_type,
        output_type,
        risk,
        posture,
        CapabilityPresentation(title, confirmation_title),
        frozenset({"write" if confirmation_title else "read"}),
    )


def default_capability_registry() -> CapabilityRegistry:
    return CapabilityRegistry(capability_specs())
