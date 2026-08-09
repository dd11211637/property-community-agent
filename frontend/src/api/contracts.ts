export type Role =
  | "RESIDENT"
  | "CUSTOMER_SERVICE"
  | "REPAIR_WORKER"
  | "FINANCE"
  | "SECURITY"
  | "DUTY_STAFF"
  | "MANAGER"
  | "SYSTEM_ADMIN";

export type House = { id: string; label: string; address?: string };
export type Session = {
  access_token: string;
  actor: { id: string; display_name: string; roles: Role[]; community_name: string };
  houses: House[];
  current_house_id?: string | null;
};

export type ListResult<T> = { items: T[]; total?: number; limit: number; offset: number };
export type WorkOrder = {
  id: string;
  category: string;
  location: string;
  description: string;
  urgency: string;
  status: string;
  version: number;
  created_at?: string;
  available_actions?: string[];
};
export type Bill = {
  id: string;
  external_bill_no: string;
  fee_type: string;
  period_start: string;
  period_end: string;
  amount: string;
  payment_status: string;
  source_system: string;
  source_updated_at: string;
  version?: number;
};
export type InspectionTask = {
  id: string;
  title: string;
  description: string;
  status: string;
  version: number;
  planned_at?: string | null;
  available_actions?: string[];
};
export type SecurityEvent = {
  id: string;
  event_type: string;
  risk_level: string;
  location: string;
  description: string;
  status: string;
  version: number;
  available_actions?: string[];
};
export type Announcement = { id: string; title: string; status: string; published_at?: string };
export type MessageRecord = { id: string; business_type: string; title: string; status: string; created_at: string };
