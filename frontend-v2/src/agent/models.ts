import type { components } from "../api/generated/schema";

export type SendAgentMessage = components["schemas"]["SendMessageRequest"];
export type ConfirmAgentAction = components["schemas"]["ConfirmRequest"];
export type CreateAgentMemory = components["schemas"]["CreateMemoryRequest"];
export type UpdateAgentMemory = components["schemas"]["UpdateMemoryRequest"];
export type DeleteAgentMemory = components["schemas"]["DeleteMemoryRequest"];
export type MemoryType = CreateAgentMemory["memory_type"];

export type ConversationSummary = {
  conversationId: string;
  title: string | null;
  status: string;
  currentHouseId: string | null;
  lastIntent: string | null;
  lastMessageAt: string | null;
  handoverRequired: boolean;
};

export type ConversationMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  intent: string | null;
  houseId: string | null;
  createdAt: string | null;
};

export type PendingConfirmation = {
  summary: string;
  tool: string;
  params: Readonly<Record<string, unknown>>;
  actionHash: string;
  issuedAt: string | null;
};

export type ConversationStatus = {
  conversationId: string;
  status: string;
  currentHouseId: string | null;
  lastIntent: string | null;
  handoverRequired: boolean;
  handoverTicketId: string | null;
  runtimeVersion: string | null;
  pendingConfirmation: PendingConfirmation | null;
};

export type AgentTurn = {
  conversationId: string;
  status: string;
  done: boolean;
  intent: string | null;
  reply: string | null;
  facts: unknown | null;
  missingSlots: string[];
  requestedSlot: string | null;
  slotPrompt: string | null;
  handoverRequired: boolean;
  pendingConfirmation: PendingConfirmation | null;
};

export type AgentMemory = {
  id: string;
  memoryType: MemoryType;
  content: string;
  houseId: string | null;
  sourceConversationId: string | null;
  version: number;
  expiresAt: string | null;
  createdAt: string | null;
  updatedAt: string | null;
};

export const publicAgentEvents = [
  "run_started",
  "tool_started",
  "tool_finished",
  "intent",
  "message",
  "clarification",
  "confirmation",
  "facts",
  "handover",
  "turn",
  "failed",
  "done",
] as const;

export type PublicAgentEventName = (typeof publicAgentEvents)[number];
export type AgentStreamEvent = {
  event: PublicAgentEventName | "unknown";
  originalEvent: string;
  data: Readonly<Record<string, unknown>>;
};

export type TurnPhase =
  | "idle"
  | "submitting"
  | "running"
  | "clarifying"
  | "awaiting-confirmation"
  | "completed"
  | "handed-over"
  | "failed"
  | "cancelled";

export type TurnState = {
  phase: TurnPhase;
  progress: string | null;
  reply: string | null;
  intent: string | null;
  facts: unknown | null;
  requestedSlot: string | null;
  missingSlots: string[];
  slotPrompt: string | null;
  confirmation: PendingConfirmation | null;
  handoverTicketId: string | null;
  error: string | null;
  uncertain: boolean;
};

