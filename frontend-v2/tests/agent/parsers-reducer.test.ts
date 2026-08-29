import { describe, expect, it } from "vitest";
import { parseAgentTurn, parseConversationStatus, parseMemories } from "../../src/agent/parsers";
import { initialTurnState, turnReducer } from "../../src/agent/reducer";

describe("Agent parsers and reducer", () => {
  it("strictly maps status pending confirmation", () => {
    const status = parseConversationStatus({
      conversation_id: "c-1",
      status: "WAITING_CONFIRM",
      current_house_id: "h-1",
      pending_confirmation: {
        summary: "创建报修",
        tool: "repair_create",
        params: { location: "厨房" },
        action_hash: "server-hash",
        issued_at: "2026-08-29T00:00:00Z",
      },
    });
    expect(status.pendingConfirmation?.actionHash).toBe("server-hash");
  });

  it("fails closed on malformed memory types", () => {
    expect(() => parseMemories([{ id: "m", memory_type: "SECRET", content: "x", version: 1 }])).toThrow();
  });

  it("normalizes JSON and SSE terminal state without contradictory confirmation", () => {
    const waiting = turnReducer(initialTurnState, {
      type: "turn",
      turn: parseAgentTurn({
        conversation_id: "c",
        status: "WAITING_CONFIRM",
        done: false,
        pending_confirmation: {
          summary: "确认",
          tool: "repair_create",
          params: {},
          action_hash: "hash",
        },
      }),
    });
    expect(waiting.phase).toBe("awaiting-confirmation");
    const failed = turnReducer(waiting, {
      type: "event",
      event: { event: "failed", originalEvent: "failed", data: {} },
    });
    expect(failed).toMatchObject({ phase: "failed", confirmation: null, uncertain: true });
  });

  it("preserves structured clarification options from the backend", () => {
    const state = turnReducer(initialTurnState, {
      type: "event",
      event: {
        event: "clarification",
        originalEvent: "clarification",
        data: { slot_prompt: { field: "location", label: "位置", prompt: "请选择位置", allow_custom: true, options: [{ label: "地下车库", value: "地下车库" }] } },
      },
    });
    expect(state).toMatchObject({ phase: "clarifying", requestedSlot: "location" });
    expect(state.slotPrompt?.options[0].value).toBe("地下车库");
    const notTerminal = turnReducer(state, {
      type: "event",
      event: { event: "done", originalEvent: "done", data: { done: false } },
    });
    expect(notTerminal.phase).toBe("clarifying");
  });
});
