import type {
  AnnouncementCardModel,
  BillCardModel,
  InspectionTaskCardModel,
  SecurityEventCardModel,
  WorkOrderCardModel,
} from "../domain/cardModels";

export type FactCard =
  | { type: "work-order"; id: string; value: WorkOrderCardModel }
  | { type: "bill"; id: string; value: BillCardModel }
  | { type: "announcement"; id: string; value: AnnouncementCardModel }
  | { type: "inspection"; id: string; value: InspectionTaskCardModel }
  | { type: "security"; id: string; value: SecurityEventCardModel }
  | { type: "consultation"; id: string; subject: string; status: string };

function object(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>) : null;
}
function text(value: unknown, fallback = ""): string { return typeof value === "string" ? value : fallback; }
function number(value: unknown): number {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}
function unwrap(value: unknown): Record<string, unknown> | null {
  const source = object(value);
  return source ? object(source.data) ?? source : null;
}

export function normalizeAgentFacts(value: unknown): FactCard[] {
  const facts = unwrap(value);
  if (!facts) return [];
  const cards: FactCard[] = [];
  const work = object(facts.work_order);
  if (work) {
    const id = text(work.id);
    cards.push({ type: "work-order", id, value: { id, number: text(work.business_no, id), title: "报修工单", location: text(work.location, "位置未提供"), status: text(work.status, "UNKNOWN"), priority: text(work.urgency, "UNKNOWN"), summary: text(work.description, "查看工单详情获取完整信息"), updatedAt: text(work.updated_at) } });
  }
  const bill = object(facts.bill);
  if (bill) {
    const id = text(bill.bill_id ?? bill.id);
    cards.push({ type: "bill", id, value: { id, period: text(bill.period, "账期未提供"), total: number(bill.total_amount ?? bill.amount), status: text(bill.status, "UNKNOWN"), dueDate: text(bill.due_date, "未提供"), items: ["物业费", "水电费", "停车费"].filter((_, index) => number([bill.property_fee, bill.utility_fee, bill.parking_fee][index]) > 0) } });
  }
  const announcement = object(facts.announcement);
  if (announcement) {
    const id = text(announcement.id);
    cards.push({ type: "announcement", id, value: { id, title: text(announcement.title, "公告"), category: text(announcement.category, "UNKNOWN"), audience: "以公告受众条件为准", status: text(announcement.status, "UNKNOWN"), summary: text(announcement.body), publishedAt: text(announcement.published_at ?? announcement.scheduled_at) } });
  }
  const task = object(facts.task);
  if (task) {
    const id = text(task.id);
    cards.push({ type: "inspection", id, value: { id, title: text(task.title, "巡检任务"), assignee: text(task.assignee_id, "未分派"), status: text(task.status, "UNKNOWN"), dueAt: text(task.due_at, "未提供"), progress: text(task.status) === "COMPLETED" ? 100 : 0 } });
  }
  const event = object(facts.event);
  if (event) {
    const id = text(event.id);
    cards.push({ type: "security", id, value: { id, title: text(event.event_type, "安防事件"), location: text(event.location, "位置未提供"), risk: text(event.risk_level, "UNKNOWN"), status: text(event.status, "UNKNOWN"), reportedAt: text(event.created_at) } });
  }
  const consultation = object(facts.consultation);
  if (consultation) {
    const id = text(consultation.id);
    cards.push({ type: "consultation", id, subject: text(consultation.subject, "账单咨询"), status: text(consultation.status, "UNKNOWN") });
  }
  if (Array.isArray(facts.items)) {
    for (const item of facts.items) {
      const entity = object(item);
      if (!entity) continue;
      const entityType = text(entity.entity_type);
      const target = text(facts.target);
      const wrapped = entityType === "WORK_ORDER" ? { work_order: entity }
        : entityType === "BILL" ? { bill: entity }
          : entityType === "ANNOUNCEMENT" ? { announcement: entity }
            : target === "task" ? { task: entity }
              : target === "event" ? { event: entity }
                : null;
      if (wrapped) cards.push(...normalizeAgentFacts(wrapped));
    }
  }
  return cards;
}
