"""智能体策略与门控 — PRD §6.5.4 / §6.5.6 / §6.5.7.

确定性规则层：意图枚举、各意图必填槽位、操作等级判定、以及"高风险转人工"门控。
所有判定都是纯 Python 逻辑（不依赖模型），保证可测与可审计。
"""

from enum import StrEnum


class Intent(StrEnum):
    REPAIR = "REPAIR"
    ANNOUNCEMENT = "ANNOUNCEMENT"
    BILLING = "BILLING"
    INSPECTION = "INSPECTION"
    GENERAL_HELP = "GENERAL_HELP"
    UNCERTAIN = "UNCERTAIN"


class OperationLevel(StrEnum):
    READ = "read"
    WRITE_LOW_RISK = "write-low-risk"
    WRITE_HIGH_RISK = "write-high-risk"


# 各意图的必填槽位（确定性必填校验，PRD §6.5.5 必须用确定性逻辑）。
SLOT_SPECS: dict[str, list[str]] = {
    "REPAIR": ["category", "location", "description"],
    "ANNOUNCEMENT": ["title", "body", "audience"],
    "BILLING": ["query_type"],
    "INSPECTION": ["action"],
    "GENERAL_HELP": [],
    "UNCERTAIN": [],
}

# 低风险写意图（需用户确认 + 幂等 + 审计后才调用业务写 Service）。
WRITE_LOW_RISK_INTENTS = {"REPAIR", "BILLING", "INSPECTION"}

# 高风险工具名：Agent 不执行，只转授权人工接管（PRD §6.5.7）。
HIGH_RISK_TOOLS = {"announce_publish", "close_high_risk_event"}

# 工具名 -> 操作等级（确定性门控，PRD §6.5.7）。优先于意图判定，避免把
# "查询类"工具误判为低风险写。
TOOL_LEVELS: dict[str, str] = {
    # 报修
    "repair_list": OperationLevel.READ.value,
    "repair_get": OperationLevel.READ.value,
    "repair_create": OperationLevel.WRITE_LOW_RISK.value,
    # 公告
    "announcement_list": OperationLevel.READ.value,
    "announcement_get": OperationLevel.READ.value,
    "announce_publish": OperationLevel.WRITE_HIGH_RISK.value,
    # 账单
    "billing_query": OperationLevel.READ.value,
    "billing_consult": OperationLevel.WRITE_LOW_RISK.value,
    # 巡检
    "inspection_list": OperationLevel.READ.value,
    "inspection_create": OperationLevel.WRITE_LOW_RISK.value,
    "inspection_submit_record": OperationLevel.WRITE_LOW_RISK.value,
    "inspection_ai_suggest": OperationLevel.WRITE_LOW_RISK.value,
    "close_high_risk_event": OperationLevel.WRITE_HIGH_RISK.value,
}


# 工具级必填槽位。比意图级更精确：同一意图下"查询"不需要写操作的参数，
# 因此槽位补全在选定工具之后按工具校验（PRD §6.5.5 确定性必填校验）。
TOOL_SLOTS: dict[str, list[str]] = {
    "repair_list": [],
    "repair_get": ["work_order_id"],
    "repair_create": ["category", "location", "description"],
    "announcement_list": [],
    "announcement_get": ["announcement_id"],
    "announce_publish": ["announcement_id"],
    "billing_query": [],
    "billing_consult": ["subject", "description"],
    "inspection_list": [],
    "inspection_create": ["title", "description"],
    "inspection_submit_record": ["task_id", "expected_version", "point"],
    "inspection_ai_suggest": ["task_id", "point", "finding"],
    "close_high_risk_event": ["event_id"],
}


def required_slots(intent: str) -> list[str]:
    return SLOT_SPECS.get(intent, [])


def missing_slots_for(intent: str, slots: dict) -> list[str]:
    return [name for name in required_slots(intent) if not slots.get(name)]


def required_slots_for_tool(tool_name: str) -> list[str]:
    return TOOL_SLOTS.get(tool_name, [])


def missing_slots_for_tool(tool_name: str, slots: dict) -> list[str]:
    missing = []
    for name in required_slots_for_tool(tool_name):
        value = slots.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(name)
    return missing


def classify_operation_level(intent: str, tool_name: str | None = None) -> str:
    """依据具体工具名（优先）与意图判定操作等级（PRD §6.5.7）。"""
    if tool_name in TOOL_LEVELS:
        return TOOL_LEVELS[tool_name]
    if tool_name in HIGH_RISK_TOOLS:
        return OperationLevel.WRITE_HIGH_RISK.value
    if intent in WRITE_LOW_RISK_INTENTS:
        return OperationLevel.WRITE_LOW_RISK.value
    return OperationLevel.READ.value


def is_high_risk(tool_name: str | None) -> bool:
    return tool_name in HIGH_RISK_TOOLS or (
        tool_name in TOOL_LEVELS
        and TOOL_LEVELS[tool_name] == OperationLevel.WRITE_HIGH_RISK.value
    )
