export type CardVariant = "default" | "compact" | "agent" | "context";
export type WorkOrderCardModel = { id: string; number: string; title: string; location: string; status: string; priority: string; summary: string; updatedAt: string };
export type BillCardModel = { id: string; period: string; total: number; status: string; dueDate: string; items: string[] };
export type AnnouncementCardModel = { id: string; title: string; category: string; audience: string; status: string; summary: string; publishedAt: string };
export type ResidentCardModel = { id: string; name: string; house: string; contact: string; tags: string[] };
export type HouseCardModel = { id: string; label: string; address: string; occupancy: string };
export type InspectionTaskCardModel = { id: string; title: string; assignee: string; status: string; dueAt: string; progress: number };
export type SecurityEventCardModel = { id: string; title: string; location: string; risk: string; status: string; reportedAt: string };
