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

export type LoginResponse = {
  access_token: string;
  actor_id: string;
  display_name: string;
  community_id: string;
  community_name: string;
  roles: Role[];
  house_ids: string[];
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
  bill_id: string;
  bill_period: string;
  total_amount: string | number;
  status: string;
  fee_type?: string | null;
  version: number;
  source_time?: string | null;
  rule_version?: string | null;
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
