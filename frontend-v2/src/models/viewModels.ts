export type CardVariant = "default" | "compact" | "agent" | "context";

export type WorkOrderModel = { id: string; number: string; title: string; location: string; status: string; priority: string; summary: string; updatedAt: string };
export type BillModel = { id: string; period: string; total: number; status: string; dueDate: string; items: string[] };
export type AnnouncementModel = { id: string; title: string; category: string; audience: string; status: string; summary: string; publishedAt: string };
export type ResidentModel = { id: string; name: string; house: string; contact: string; tags: string[] };
export type HouseModel = { id: string; label: string; address: string; occupancy: string };
export type InspectionTaskModel = { id: string; title: string; assignee: string; status: string; dueAt: string; progress: number };
export type SecurityEventModel = { id: string; title: string; location: string; risk: string; status: string; reportedAt: string };
export type ConversationModel = { id: string; name: string; preview: string; time: string; unread?: number };
export type MessageModel = { id: string; sender: "agent" | "user" | "staff"; body: string; time: string };

export type StructuredAgentResult =
  | { type: "text"; text: string }
  | { type: "work-order"; value: WorkOrderModel }
  | { type: "bill"; value: BillModel }
  | { type: "announcement"; value: AnnouncementModel }
  | { type: "inspection"; value: InspectionTaskModel }
  | { type: "security-event"; value: SecurityEventModel }
  | { type: "suggested-action"; label: string; description: string }
  | { type: "confirmation"; title: string; description: string; confirmLabel: string }
  | { type: "handoff"; title: string; owner: string; status: string };

export type ShowcaseModels = {
  workOrders: WorkOrderModel[];
  bills: BillModel[];
  announcements: AnnouncementModel[];
  residents: ResidentModel[];
  houses: HouseModel[];
  inspections: InspectionTaskModel[];
  securityEvents: SecurityEventModel[];
  conversations: ConversationModel[];
  messages: MessageModel[];
  agentResults: StructuredAgentResult[];
};
