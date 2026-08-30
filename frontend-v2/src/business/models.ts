export type BusinessRecord = Readonly<Record<string, unknown>>;

export type WorkOrder = {
  id: string;
  number: string;
  houseId: string;
  houseDisplay: string | null;
  reporterId: string;
  reporterName: string | null;
  category: string;
  location: string;
  description: string;
  urgency: string;
  status: string;
  version: number;
  assigneeId: string | null;
  assigneeName: string | null;
  hasReview: boolean;
  availableActions: string[];
  createdAt: string;
  updatedAt: string;
};

export type TimelineEntry = {
  id: string;
  action: string;
  fromStatus: string | null;
  toStatus: string | null;
  operatorId: string | null;
  note: string | null;
  reason: string | null;
  createdAt: string;
};

export type Announcement = {
  id: string;
  title: string;
  body: string;
  category: string;
  status: string;
  version: number;
  audience: BusinessRecord;
  availableActions: string[];
  scheduledAt: string | null;
  publishedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type InspectionTask = {
  id: string;
  title: string;
  description: string;
  status: string;
  version: number;
  assigneeId: string | null;
  routePoints: string[];
  availableActions: string[];
  plannedAt: string | null;
  dueAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type SecurityEvent = {
  id: string;
  number: string;
  eventType: string;
  riskLevel: string;
  location: string;
  description: string;
  status: string;
  version: number;
  assigneeId: string | null;
  availableActions: string[];
  createdAt: string;
  updatedAt: string;
};

export type PlatformMessage = {
  id: string;
  title: string;
  content: string;
  status: string;
  businessType: string | null;
  resourceId: string | null;
  isRead: boolean;
  failureReason: string | null;
  retryCount: number;
  handoverRequired: boolean;
  createdAt: string;
};

export type AdminDashboard = {
  pending: BusinessRecord[];
  failedMessages: BusinessRecord[];
  highRiskEvents: BusinessRecord[];
  integrationHealth: BusinessRecord[];
  rawCounts: BusinessRecord;
};

function record(value: unknown, label: string): BusinessRecord {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new Error(`${label} 必须是对象。`);
  return value as BusinessRecord;
}
function text(value: unknown, label: string): string {
  if (typeof value !== "string") throw new Error(`${label} 必须是字符串。`);
  return value;
}
function optionalText(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}
function integer(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isInteger(value))
    throw new Error(`${label} 必须是整数。`);
  return value;
}
function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}
function list(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`${label} 必须是数组。`);
  return value;
}
function dateText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function parseCollection<T>(
  value: unknown,
  parser: (item: unknown) => T,
  label: string,
): { items: T[]; total?: number; limit?: number; offset?: number } {
  if (Array.isArray(value)) return { items: value.map(parser) };
  const source = record(value, label);
  return {
    items: list(source.items, `${label}.items`).map(parser),
    total: typeof source.total === "number" ? source.total : undefined,
    limit: typeof source.limit === "number" ? source.limit : undefined,
    offset: typeof source.offset === "number" ? source.offset : undefined,
  };
}

export function parseWorkOrder(value: unknown): WorkOrder {
  const item = record(value, "workOrder");
  return {
    id: text(item.id, "workOrder.id"),
    number: text(item.business_no, "workOrder.business_no"),
    houseId: text(item.house_id, "workOrder.house_id"),
    houseDisplay: optionalText(item.house_display),
    reporterId: text(item.reporter_id, "workOrder.reporter_id"),
    reporterName: optionalText(item.reporter_name),
    category: text(item.category, "workOrder.category"),
    location: text(item.location, "workOrder.location"),
    description: text(item.description, "workOrder.description"),
    urgency: text(item.urgency, "workOrder.urgency"),
    status: text(item.status, "workOrder.status"),
    version: integer(item.version, "workOrder.version"),
    assigneeId: optionalText(item.assignee_id),
    assigneeName: optionalText(item.assignee_name),
    hasReview: item.has_review === true,
    availableActions: strings(item.available_actions),
    createdAt: dateText(item.created_at),
    updatedAt: dateText(item.updated_at),
  };
}

export function parseTimeline(value: unknown): TimelineEntry[] {
  return list(value, "timeline").map((raw, index) => {
    const item = record(raw, `timeline[${index}]`);
    return {
      id: optionalText(item.id) ?? `${index}`,
      action: text(
        item.action ?? item.event_type ?? item.type,
        `timeline[${index}].action`,
      ),
      fromStatus: optionalText(item.from_status),
      toStatus: optionalText(item.to_status),
      operatorId: optionalText(item.operator_id),
      note: optionalText(item.note),
      reason: optionalText(item.reason),
      createdAt: dateText(item.created_at),
    };
  });
}

export function parseAnnouncement(value: unknown): Announcement {
  const item = record(value, "announcement");
  return {
    id: text(item.id, "announcement.id"),
    title: text(item.title, "announcement.title"),
    body: text(item.body, "announcement.body"),
    category: text(item.category, "announcement.category"),
    status: text(item.status, "announcement.status"),
    version: integer(item.version, "announcement.version"),
    audience:
      typeof item.audience_condition === "object" &&
      item.audience_condition !== null &&
      !Array.isArray(item.audience_condition)
        ? (item.audience_condition as BusinessRecord)
        : {},
    availableActions: strings(item.available_actions),
    scheduledAt: optionalText(item.scheduled_at),
    publishedAt: optionalText(item.published_at),
    createdAt: dateText(item.created_at),
    updatedAt: dateText(item.updated_at),
  };
}

export function parseInspectionTask(value: unknown): InspectionTask {
  const item = record(value, "inspectionTask");
  return {
    id: text(item.id, "inspectionTask.id"),
    title: text(item.title, "inspectionTask.title"),
    description: optionalText(item.description) ?? "",
    status: text(item.status, "inspectionTask.status"),
    version: integer(item.version, "inspectionTask.version"),
    assigneeId: optionalText(item.assignee_id),
    routePoints: strings(item.route_points),
    availableActions: strings(item.available_actions).filter(
      (action) => action !== "CONFIRM_AI",
    ),
    plannedAt: optionalText(item.planned_at),
    dueAt: optionalText(item.due_at),
    createdAt: dateText(item.created_at),
    updatedAt: dateText(item.updated_at),
  };
}

export function parseSecurityEvent(value: unknown): SecurityEvent {
  const item = record(value, "securityEvent");
  return {
    id: text(item.id, "securityEvent.id"),
    number: optionalText(item.business_no) ?? text(item.id, "securityEvent.id"),
    eventType: text(item.event_type, "securityEvent.event_type"),
    riskLevel: text(item.risk_level, "securityEvent.risk_level"),
    location: text(item.location, "securityEvent.location"),
    description: optionalText(item.description) ?? "",
    status: text(item.status, "securityEvent.status"),
    version: integer(item.version, "securityEvent.version"),
    assigneeId: optionalText(item.assignee_id),
    availableActions: strings(item.available_actions),
    createdAt: dateText(item.created_at),
    updatedAt: dateText(item.updated_at),
  };
}

export function parseMessage(value: unknown): PlatformMessage {
  const item = record(value, "message");
  const status =
    optionalText(item.status) ?? (item.read_at ? "READ" : "UNREAD");
  return {
    id: text(item.id, "message.id"),
    title:
      optionalText(item.title) ?? optionalText(item.event_type) ?? "业务消息",
    content: optionalText(item.content) ?? optionalText(item.body) ?? "",
    status,
    businessType: optionalText(item.business_type),
    resourceId: optionalText(item.resource_id),
    isRead: item.is_read === true || Boolean(item.read_at) || status === "READ",
    failureReason:
      optionalText(item.failure_reason) ?? optionalText(item.last_error),
    retryCount: typeof item.retry_count === "number" ? item.retry_count : 0,
    handoverRequired:
      item.handover_required === true ||
      item.handover_status === "PENDING" ||
      item.status === "HANDOVER_REQUIRED",
    createdAt: dateText(item.created_at),
  };
}

export function parseAdminDashboard(value: unknown): AdminDashboard {
  const item = record(value, "adminDashboard");
  const records = (candidate: unknown) =>
    Array.isArray(candidate)
      ? candidate.map((entry) => record(entry, "dashboard item"))
      : [];
  const health =
    typeof item.integration_health === "object" &&
    item.integration_health !== null &&
    !Array.isArray(item.integration_health)
      ? Object.entries(item.integration_health as BusinessRecord).map(
          ([name, status]) => ({ name, status }),
        )
      : records(item.integration_health ?? item.service_health);
  return {
    pending: records(item.pending_items ?? item.pending_work ?? item.pending),
    failedMessages: records(item.failed_messages),
    highRiskEvents: records(item.high_risk_events),
    integrationHealth: health,
    rawCounts: {
      pending_count: item.pending_count,
      failed_message_count: item.failed_message_count,
      high_risk_event_count: item.high_risk_event_count,
    },
  };
}

export function describeAudience(audience: BusinessRecord): string {
  const buildings = strings(audience.buildings ?? audience.building_ids);
  const roles = strings(audience.roles);
  if (buildings.length) return `楼栋：${buildings.join("、")}`;
  if (roles.length) return `角色：${roles.join("、")}`;
  return "全社区可见范围";
}
