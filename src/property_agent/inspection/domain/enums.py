from enum import StrEnum


class Role(StrEnum):
    RESIDENT = "RESIDENT"
    CUSTOMER_SERVICE = "CUSTOMER_SERVICE"
    SECURITY_STAFF = "SECURITY_STAFF"
    MANAGER = "MANAGER"


# ---------------------------------------------------------------------------
# 巡检任务 (InspectionTask) 状态与动作
# ---------------------------------------------------------------------------
class TaskStatus(StrEnum):
    PLANNED = "PLANNED"  # 计划草稿，尚未分派
    ASSIGNED = "ASSIGNED"  # 已分派安保人员，已发布
    IN_PROGRESS = "IN_PROGRESS"  # 安保已到点打卡/开始执行
    SUBMITTED = "SUBMITTED"  # 记录已提交，待人工确认/复核
    COMPLETED = "COMPLETED"  # 已确认完成并关闭


class TaskAction(StrEnum):
    CREATE = "CREATE"
    ASSIGN = "ASSIGN"
    START = "START"
    SUBMIT_RECORDS = "SUBMIT_RECORDS"
    COMPLETE = "COMPLETE"
    ADD_RECORD = "ADD_RECORD"  # 不改变状态，仅追加记录并递增版本


class TaskRecordType(StrEnum):
    CHECKIN = "CHECKIN"  # 到点打卡
    POINT_RECORD = "POINT_RECORD"  # 点位记录
    PROGRESS = "PROGRESS"  # 过程记录
    COMPLETION = "COMPLETION"  # 完工/完成记录
    SUPPLEMENT = "SUPPLEMENT"  # 补交记录（标注实际时间与原因）


# ---------------------------------------------------------------------------
# 安防事件 (SecurityEvent) 状态、风险等级与动作
# ---------------------------------------------------------------------------
class EventStatus(StrEnum):
    REPORTED = "REPORTED"  # 已上报，待分派
    ASSIGNED = "ASSIGNED"  # 已分派处置负责人
    PENDING_REVIEW = "PENDING_REVIEW"  # 处置已提交，待授权人员复核
    CLOSED = "CLOSED"  # 复核通过并关闭


class EventRiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH_RISK = "HIGH_RISK"


class EventType(StrEnum):
    GAS_LEAK = "GAS_LEAK"  # 燃气泄漏
    FIRE = "FIRE"  # 火情
    PERSONAL_SAFETY = "PERSONAL_SAFETY"  # 人员安全
    EQUIPMENT_FAULT = "EQUIPMENT_FAULT"  # 设施设备隐患
    OTHER = "OTHER"


class EventAction(StrEnum):
    CREATE = "CREATE"
    ASSIGN = "ASSIGN"
    SUBMIT_DISPOSAL = "SUBMIT_DISPOSAL"
    REVIEW_PASS = "REVIEW_PASS"  # 复核通过并关闭
    RETURN = "RETURN"  # 复核不通过，退回处置人
