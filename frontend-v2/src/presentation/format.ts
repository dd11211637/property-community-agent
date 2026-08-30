const statusLabels: Record<string, string> = {
  OPEN: "待处理", IN_PROGRESS: "处理中", PENDING: "待确认", COMPLETED: "已完成", CLOSED: "已关闭",
  PAID: "已缴清", UNPAID: "待缴费", OVERDUE: "已逾期", PUBLISHED: "已发布", DRAFT: "草稿",
  HIGH_RISK: "高风险", MEDIUM_RISK: "中风险", LOW_RISK: "低风险", ON_DUTY: "值班中",
  PENDING_ASSIGNMENT: "待分派", PENDING_ACCEPTANCE: "待接单", PROCESSING: "处理中",
  PENDING_VERIFICATION: "待验收", REWORKING: "返工中", ASSIGNED: "已分派", PLANNED: "待分派",
  SUBMITTED: "待复核", PENDING_REVIEW: "待审核", APPROVED: "已批准", REJECTED: "已驳回",
  ARCHIVED: "已归档", WITHDRAWN: "已撤回", RESOLVED: "已解决", ANSWERED: "已答复",
  APPEALED: "申诉中", CANCELLED: "已取消", REPORTED: "待处置", NORMAL: "普通", URGENT: "紧急",
  LOW: "低风险", MEDIUM: "中风险", WATER_PLUMBING: "给排水", ELECTRICAL: "电气",
  ELEVATOR: "电梯", OTHER: "其他", FIRE: "火情", GAS_LEAK: "燃气泄漏",
  PERSONAL_SAFETY: "人员安全", EQUIPMENT_FAULT: "设施设备隐患", GENERAL: "综合",
  MAINTENANCE: "维修维护", SAFETY: "安全", EMERGENCY: "紧急通知",
};

export function labelFor(code: string): string { return statusLabels[code] ?? code.replaceAll("_", " "); }
export function formatCurrency(value: number): string { return new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY" }).format(value); }
export function formatDate(value: string): string {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return "未提供";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
export function statusTone(code: string): "neutral" | "success" | "warning" | "dangerTone" | "info" {
  if (["COMPLETED", "CLOSED", "PAID", "PUBLISHED"].includes(code)) return "success";
  if (["OVERDUE", "HIGH_RISK"].includes(code)) return "dangerTone";
  if (["PENDING", "UNPAID", "MEDIUM_RISK"].includes(code)) return "warning";
  if (["IN_PROGRESS", "ON_DUTY"].includes(code)) return "info";
  return "neutral";
}
