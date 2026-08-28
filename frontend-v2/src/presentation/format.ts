const statusLabels: Record<string, string> = {
  OPEN: "待处理", IN_PROGRESS: "处理中", PENDING: "待确认", COMPLETED: "已完成", CLOSED: "已关闭",
  PAID: "已缴清", UNPAID: "待缴费", OVERDUE: "已逾期", PUBLISHED: "已发布", DRAFT: "草稿",
  HIGH_RISK: "高风险", MEDIUM_RISK: "中风险", LOW_RISK: "低风险", ON_DUTY: "值班中",
};

export function labelFor(code: string): string { return statusLabels[code] ?? code.replaceAll("_", " "); }
export function formatCurrency(value: number): string { return new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY" }).format(value); }
export function formatDate(value: string): string { return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
export function statusTone(code: string): "neutral" | "success" | "warning" | "dangerTone" | "info" {
  if (["COMPLETED", "CLOSED", "PAID", "PUBLISHED"].includes(code)) return "success";
  if (["OVERDUE", "HIGH_RISK"].includes(code)) return "dangerTone";
  if (["PENDING", "UNPAID", "MEDIUM_RISK"].includes(code)) return "warning";
  if (["IN_PROGRESS", "ON_DUTY"].includes(code)) return "info";
  return "neutral";
}
