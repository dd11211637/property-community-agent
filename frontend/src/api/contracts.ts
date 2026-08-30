export type Role =
  | "RESIDENT"
  | "CUSTOMER_SERVICE"
  | "REPAIR_WORKER"
  | "FINANCE"
  | "FINANCE_STAFF"
  | "SECURITY_STAFF"
  | "SECURITY_GUARD"
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

export type HouseSelectionResponse = {
  house_id: string;
  building: string;
  unit: string;
  room_no: string;
};

export type ListResult<T> = { items: T[]; total?: number; limit: number; offset: number };
export type StaffOption = { id: string; display_name: string; role: string };
export type WorkOrder = {
  id: string;
  business_no?: string;
  house_id?: string;
  category: string;
  location: string;
  description: string;
  urgency: string;
  status: string;
  version: number;
  created_at?: string;
  updated_at?: string;
  assignee_id?: string | null;
  has_review?: boolean;
  available_actions?: string[];
};
export type TimelineEntry = { entry_type: string; action: string; operator_id: string; created_at: string; from_status?: string | null; to_status?: string | null; reason?: string | null; note?: string | null; attachment_ids?: string[] };
export type Bill = {
  bill_id: string;
  bill_period: string;
  total_amount: string;
  status: string;
  fee_type?: string | null;
  version: number;
  source_time?: string | null;
  rule_version?: string | null;
  property_fee?: string;
  utility_fee?: string;
  parking_fee?: string;
  late_fee?: string;
  due_date?: string | null;
  rule_name?: string | null;
};
export type BillingRule = { id: string; fee_type: string; version: string; name: string; parameters: Record<string, unknown>; valid_from?: string | null; valid_until?: string | null };
export type BillDetail = { bill: Bill; rule: BillingRule | null; unknown_rule: boolean; consultation_entry?: string | null };
export type Consultation = { id: string; subject: string; description: string; bill_id?: string | null; status: string; answer?: string | null; version: number; created_at: string; updated_at: string };
export type InspectionTask = {
  id: string;
  business_no?: string;
  title: string;
  description: string;
  status: string;
  version: number;
  planned_at?: string | null;
  due_at?: string | null;
  route_points?: string[];
  assignee_id?: string | null;
  available_actions?: string[];
};
export type SecurityEvent = {
  id: string;
  business_no?: string;
  event_type: string;
  risk_level: string;
  location: string;
  description: string;
  status: string;
  version: number;
  assignee_id?: string | null;
  grade_confirmed_by?: string | null;
  available_actions?: string[];
};
export type Announcement = { id: string; business_no?: string; title: string; body: string; category: string; audience_condition: Record<string, string[]>; status: string; version: number; manager_recheck_required?: boolean; scheduled_at?: string | null; published_at?: string | null; created_at?: string; updated_at?: string; available_actions?: string[] };
export type AnnouncementVersion = {
  version_no: number;
  title: string;
  body: string;
  category: string;
  audience_condition: Record<string, string[]>;
  operator_id: string;
  source: string;
  created_at: string;
};
export type AudiencePreview = { condition: Record<string, string[]>; count: number; samples: Array<Record<string, string>>; generated_at: string };
export type MessageRecord = {
  id: string;
  business_type: string;
  resource_id: string;
  title: string;
  body: string;
  status: string;
  is_read: boolean;
  read_at?: string | null;
  retry_count: number;
  max_retry_count: number;
  retry_exhausted: boolean;
  last_error?: string | null;
  handover_status?: string | null;
  fallback_contact?: string | null;
  created_at: string;
  updated_at: string;
};
export type AdminDashboard = {
  pending_count: number;
  failed_message_count: number;
  high_risk_event_count: number;
  pending_items: Array<{ id: string; source: string; queue: string; summary: string; status: string; created_at: string }>;
  failed_messages: MessageRecord[];
  high_risk_events: Array<{ id: string; business_no: string; location: string; risk_level: string; status: string; updated_at: string }>;
  integration_health: Record<string, string>;
};
export type AgentConversation = {
  conversation_id: string;
  title: string;
  status: string;
  current_house_id?: string | null;
  last_intent?: string | null;
  last_message_at?: string | null;
};
export type AgentMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  intent?: string | null;
  house_id?: string | null;
  created_at: string;
};
export type AgentMemory = {
  id: string;
  memory_type: "PREFERENCE" | "COMMUNICATION" | "ACCESSIBILITY" | "SERVICE_NOTE";
  content: string;
  house_id?: string | null;
  source_conversation_id?: string | null;
  confirmed_by_user: boolean;
  version: number;
  created_at: string;
  updated_at: string;
  expires_at?: string | null;
};
