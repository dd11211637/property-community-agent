import { ApiError } from "../api/client";
import type {
  AgentMemory,
  AgentTurn,
  ConversationMessage,
  ConversationStatus,
  ConversationSummary,
  MemoryType,
  PendingConfirmation,
  SlotPrompt,
} from "./models";

type RecordValue = Record<string, unknown>;
const memoryTypes = new Set<MemoryType>([
  "PREFERENCE",
  "COMMUNICATION",
  "ACCESSIBILITY",
  "SERVICE_NOTE",
]);

function invalid(label: string): never {
  throw new ApiError(
    "invalid-response",
    200,
    "INVALID_RESPONSE",
    `Agent ${label} 响应不符合契约。`,
  );
}
function record(value: unknown, label: string): RecordValue {
  if (!value || typeof value !== "object" || Array.isArray(value)) invalid(label);
  return value as RecordValue;
}
function text(value: unknown, label: string): string {
  if (typeof value !== "string" || !value.trim()) invalid(label);
  return value;
}
function optionalText(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}
function boolean(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}
function integer(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isInteger(value)) invalid(label);
  return value;
}
function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}
function items(value: unknown, label: string): unknown[] {
  if (Array.isArray(value)) return value;
  const source = record(value, label);
  if (!Array.isArray(source.items)) invalid(`${label}.items`);
  return source.items;
}

export function parsePendingConfirmation(value: unknown): PendingConfirmation | null {
  if (value === null || value === undefined) return null;
  const source = record(value, "pending_confirmation");
  const params = record(source.params, "pending_confirmation.params");
  return {
    summary: text(source.summary, "pending_confirmation.summary"),
    tool: text(source.tool, "pending_confirmation.tool"),
    params,
    actionHash: text(source.action_hash, "pending_confirmation.action_hash"),
    issuedAt: optionalText(source.issued_at),
  };
}

export function parseSlotPrompt(value: unknown): SlotPrompt | null {
  if (value === null || value === undefined) return null;
  const source = record(value, "slot_prompt");
  const field = optionalText(source.field);
  const prompt = optionalText(source.prompt);
  if (!field || !prompt) invalid("slot_prompt");
  const options = Array.isArray(source.options)
    ? source.options.map((raw) => {
        const option = record(raw, "slot_prompt.option");
        return { label: text(option.label, "slot_prompt.option.label"), value: option.value };
      })
    : [];
  return {
    field,
    label: optionalText(source.label) ?? field,
    prompt,
    allowCustom: source.allow_custom !== false,
    options,
  };
}

export function parseConversationList(value: unknown): ConversationSummary[] {
  return items(value, "conversation list").map((raw) => {
    const source = record(raw, "conversation");
    return {
      conversationId: text(source.conversation_id, "conversation_id"),
      title: optionalText(source.title),
      status: text(source.status, "conversation.status"),
      currentHouseId: optionalText(source.current_house_id),
      lastIntent: optionalText(source.last_intent),
      lastMessageAt: optionalText(source.last_message_at),
      handoverRequired: boolean(source.handover_required),
    };
  });
}

export function parseConversationMessages(value: unknown): ConversationMessage[] {
  return items(value, "message history").map((raw) => {
    const source = record(raw, "message");
    const role = text(source.role, "message.role");
    if (!(["user", "assistant", "system"] as string[]).includes(role))
      invalid("message.role");
    return {
      id: text(source.id, "message.id"),
      role: role as ConversationMessage["role"],
      content: text(source.content, "message.content"),
      intent: optionalText(source.intent),
      houseId: optionalText(source.house_id),
      createdAt: optionalText(source.created_at),
    };
  });
}

export function parseConversationStatus(value: unknown): ConversationStatus {
  const source = record(value, "conversation status");
  return {
    conversationId: text(source.conversation_id, "conversation_id"),
    status: text(source.status, "status"),
    currentHouseId: optionalText(source.current_house_id),
    lastIntent: optionalText(source.last_intent),
    handoverRequired: boolean(source.handover_required),
    handoverTicketId: optionalText(source.handover_ticket_id),
    runtimeVersion: optionalText(source.runtime_version),
    pendingConfirmation: parsePendingConfirmation(source.pending_confirmation),
  };
}

export function parseAgentTurn(value: unknown): AgentTurn {
  const source = record(value, "turn");
  return {
    conversationId: text(source.conversation_id, "turn.conversation_id"),
    status: text(source.status, "turn.status"),
    done: boolean(source.done),
    intent: optionalText(source.intent),
    reply: optionalText(source.reply),
    facts: source.facts ?? null,
    missingSlots: strings(source.missing_slots),
    requestedSlot: optionalText(source.requested_slot),
    slotPrompt: parseSlotPrompt(source.slot_prompt),
    handoverRequired: boolean(source.handover_required),
    pendingConfirmation: parsePendingConfirmation(source.pending_confirmation),
  };
}

export function parseMemories(value: unknown): AgentMemory[] {
  return items(value, "memory list").map(parseMemory);
}

export function parseMemory(value: unknown): AgentMemory {
  const source = record(value, "memory");
  const memoryType = text(source.memory_type, "memory.memory_type") as MemoryType;
  if (!memoryTypes.has(memoryType)) invalid("memory.memory_type");
  return {
    id: text(source.id, "memory.id"),
    memoryType,
    content: text(source.content, "memory.content"),
    houseId: optionalText(source.house_id),
    sourceConversationId: optionalText(source.source_conversation_id),
    version: integer(source.version, "memory.version"),
    expiresAt: optionalText(source.expires_at),
    createdAt: optionalText(source.created_at),
    updatedAt: optionalText(source.updated_at),
  };
}
