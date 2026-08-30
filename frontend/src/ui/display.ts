const labels: Record<string, string> = {
  PENDING: "待处理",
  PENDING_ASSIGNMENT: "待派单",
  PENDING_ACCEPTANCE: "待接单",
  PROCESSING: "处理中",
  PENDING_VERIFICATION: "待验收",
  REWORKING: "返工中",
  CLOSED: "已完成",
  UNPAID: "待缴费",
  OVERDUE: "已逾期",
  PAID: "已缴费",
  CANCELLED: "已取消",
  PLANNED: "待安排",
  ASSIGNED: "已分派",
  IN_PROGRESS: "进行中",
  SUBMITTED: "待复核",
  COMPLETED: "已完成",
  REPORTED: "待处理",
  PENDING_REVIEW: "待审核",
  DRAFT: "草稿",
  REJECTED: "已驳回",
  PUBLISHED: "已发布",
  SCHEDULED: "待发布",
  WITHDRAWN: "已撤回",
  SENT: "已送达",
  FAILED: "送达失败",
  READ: "已读",
  UNREAD: "未读",
  LOW: "低风险",
  MEDIUM: "中风险",
  HIGH: "高风险",
  HIGH_RISK: "高风险",
  NORMAL: "普通",
  URGENT: "紧急",
  WATER_PLUMBING: "水暖管道",
  ELECTRICAL: "电气",
  ELEVATOR: "电梯",
  OTHER: "其他",
  GENERAL: "一般通知",
  MAINTENANCE: "维护通知",
  SAFETY: "安全通知",
  EMERGENCY: "紧急通知",
  REPAIR: "报修",
  ANNOUNCEMENT: "公告",
  BILLING: "账单",
  INSPECTION: "巡检安防",
  UP: "运行正常",
  CONFIGURED: "已配置",
  DEGRADED: "需要关注",
  CONFIGURED_NOT_PROBED: "已配置，待检测",
  NOT_CREATED: "尚未发起",
};

const statusTone: Record<string, "neutral" | "success" | "warning" | "danger"> = {
  PAID: "success",
  CLOSED: "success",
  COMPLETED: "success",
  PUBLISHED: "success",
  SENT: "success",
  UP: "success",
  FAILED: "danger",
  REJECTED: "danger",
  OVERDUE: "danger",
  HIGH: "danger",
  HIGH_RISK: "danger",
  URGENT: "danger",
  PENDING: "warning",
  PENDING_ASSIGNMENT: "warning",
  PENDING_ACCEPTANCE: "warning",
  PENDING_REVIEW: "warning",
  UNPAID: "warning",
  DEGRADED: "warning",
};

export function displayLabel(value: unknown, fallback = "待确认"): string {
  const key = String(value ?? "").trim();
  return labels[key] ?? fallback;
}

export function displayTone(value: unknown) {
  return statusTone[String(value ?? "")] ?? "neutral";
}

export function isUuid(value: unknown): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(String(value ?? ""));
}

export function businessReference(value: unknown, fallback = "业务记录"): string {
  const text = String(value ?? "").trim();
  return !text || isUuid(text) ? fallback : text;
}

export function displayDate(value: unknown, fallback = "时间待确认"): string {
  if (!value) return fallback;
  const date = new Date(String(value));
  return Number.isNaN(date.getTime()) ? fallback : date.toLocaleString("zh-CN");
}

export function displayMoney(value: unknown): string {
  const amount = Number(value);
  return Number.isFinite(amount)
    ? amount.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : "—";
}

