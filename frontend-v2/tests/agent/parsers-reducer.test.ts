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
});

