import type { AnnouncementCardModel as AnnouncementModel, BillCardModel as BillModel, CardVariant, HouseCardModel as HouseModel, InspectionTaskCardModel as InspectionTaskModel, ResidentCardModel as ResidentModel, SecurityEventCardModel as SecurityEventModel, WorkOrderCardModel as WorkOrderModel } from "../domain/cardModels";
export type { CardVariant, WorkOrderModel, BillModel, AnnouncementModel, ResidentModel, HouseModel, InspectionTaskModel, SecurityEventModel };
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
