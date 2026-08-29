import type {
  AgentStreamEvent,
  AgentTurn,
  PendingConfirmation,
  TurnState,
} from "./models";
import { parseAgentTurn, parsePendingConfirmation, parseSlotPrompt } from "./parsers";

export const initialTurnState: TurnState = {
  phase: "idle",
  progress: null,
  reply: null,
  intent: null,
  facts: null,
  requestedSlot: null,
  missingSlots: [],
  slotPrompt: null,
  confirmation: null,
  handoverTicketId: null,
  error: null,
  uncertain: false,
};

export type TurnAction =
  | { type: "submit" }
  | { type: "event"; event: AgentStreamEvent }
  | { type: "turn"; turn: AgentTurn }
  | { type: "fail"; message: string; uncertain?: boolean }
  | { type: "cancel" }
  | { type: "restore-confirmation"; confirmation: PendingConfirmation | null }
  | { type: "reset" };

function fromTurn(state: TurnState, turn: AgentTurn): TurnState {
  const phase = turn.handoverRequired
    ? "handed-over"
    : turn.pendingConfirmation
      ? "awaiting-confirmation"
      : turn.requestedSlot || turn.missingSlots.length
        ? "clarifying"
        : turn.done
          ? "completed"
          : "running";
  return {
    ...state,
    phase,
    progress: null,
    reply: turn.reply ?? state.reply,
    intent: turn.intent ?? state.intent,
    facts: turn.facts ?? state.facts,
    requestedSlot: turn.requestedSlot,
    missingSlots: turn.missingSlots,
    slotPrompt: turn.slotPrompt,
    confirmation: turn.pendingConfirmation,
    error: null,
    uncertain: false,
  };
}

export function turnReducer(state: TurnState, action: TurnAction): TurnState {
  if (action.type === "reset") return initialTurnState;
  if (action.type === "submit")
    return { ...initialTurnState, phase: "submitting" };
  if (action.type === "turn") return fromTurn(state, action.turn);
  if (action.type === "cancel")
    return { ...state, phase: "cancelled", progress: null, confirmation: null, uncertain: true };
  if (action.type === "fail")
    return {
      ...state,
      phase: "failed",
      progress: null,
      confirmation: null,
      error: action.message,
      uncertain: action.uncertain ?? false,
    };
  if (action.type === "restore-confirmation")
    return action.confirmation
      ? { ...state, phase: "awaiting-confirmation", confirmation: action.confirmation }
      : { ...state, confirmation: null, phase: state.phase === "awaiting-confirmation" ? "idle" : state.phase };

  const { event, data } = action.event;
  if (event === "unknown") return state;
  if (event === "run_started") return { ...state, phase: "running", progress: "正在启动" };
  if (event === "tool_started" || event === "tool_finished")
    return { ...state, phase: "running", progress: typeof data.stage === "string" ? data.stage : "正在处理" };
  if (event === "intent")
    return { ...state, intent: typeof data.intent === "string" ? data.intent : state.intent };
  if (event === "message")
    return { ...state, reply: typeof data.content === "string" ? data.content : typeof data.message === "string" ? data.message : typeof data.reply === "string" ? data.reply : state.reply };
  if (event === "facts") return { ...state, facts: data.facts ?? null };
  if (event === "clarification")
    return {
      ...state,
      phase: "clarifying",
      progress: null,
      requestedSlot: typeof data.requested_slot === "string" ? data.requested_slot : parseSlotPrompt(data.slot_prompt)?.field ?? null,
      missingSlots: Array.isArray(data.missing_slots) ? data.missing_slots.filter((x): x is string => typeof x === "string") : [],
      slotPrompt: parseSlotPrompt(data.slot_prompt),
    };
  if (event === "confirmation")
    return { ...state, phase: "awaiting-confirmation", progress: null, confirmation: parsePendingConfirmation(data.pending_confirmation ?? data) };
  if (event === "handover")
    return {
      ...state,
      phase: "handed-over",
      progress: null,
      handoverTicketId: typeof data.handover_ticket_id === "string" ? data.handover_ticket_id : null,
    };
  if (event === "turn") return fromTurn(state, parseAgentTurn(data));
  if (event === "failed")
    return { ...state, phase: "failed", progress: null, confirmation: null, error: "Agent 本轮执行失败，请恢复会话状态后重试。", uncertain: true };
  if (event === "done" && data.done === true && state.phase !== "awaiting-confirmation" && state.phase !== "handed-over")
    return { ...state, phase: "completed", progress: null, uncertain: false };
  return state;
}
