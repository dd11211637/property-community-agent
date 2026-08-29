import { describe, expect, it, vi } from "vitest";
import { ApiClient } from "../../src/api/client";
import { AgentService } from "../../src/agent/service";

function envelope(data: unknown, status = 200) {
  return new Response(JSON.stringify({ success: status < 400, data: status < 400 ? data : null, error: status < 400 ? null : { code: "ERROR", message: "failed" }, request_id: "agent-req" }), { status, headers: { "Content-Type": "application/json" } });
}

describe("AgentService", () => {
  it("uses generated Agent paths and exact request bodies without invented idempotency", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const fetcher = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      if (String(url).includes("confirmations")) return envelope({ conversation_id: "c-1", status: "CLOSED", done: true });
      if (String(url).includes("memories/m-1")) return envelope({ id: "m-1", memory_type: "PREFERENCE", content: "安静联系", version: 2 });
      return envelope([]);
    });
    const api = new ApiClient("", () => ({ accessToken: "token", currentHouseId: "house-a" }), fetcher as typeof fetch);
    const service = new AgentService(api);
    await service.listConversations(25);
    await service.confirm("c-1", { confirmed: true, action_hash: "server-owned-hash" });
    await service.updateMemory("m-1", { content: "安静联系", expected_version: 1 });
    expect(calls.map((call) => call.url)).toEqual([
      "/api/agent/conversations?limit=25",
      "/api/agent/conversations/c-1/confirmations",
      "/api/agent/memories/m-1",
    ]);
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ confirmed: true, action_hash: "server-owned-hash" });
    expect(new Headers(calls[1].init?.headers).has("Idempotency-Key")).toBe(false);
    expect(JSON.parse(String(calls[2].init?.body))).toEqual({ content: "安静联系", expected_version: 1 });
  });

  it("parses a real SSE response through the same service", async () => {
    const fetcher = vi.fn(async () => new Response('event: message\ndata: {"content":"收到"}\n\nevent: done\ndata: {"done":true}\n\n'));
    const service = new AgentService(new ApiClient("", () => ({ accessToken: "token" }), fetcher as typeof fetch));
    const events = [];
    for await (const event of service.streamMessage("stable-id", { text: "你好", house_id: null })) events.push(event);
    expect(events.map((event) => event.event)).toEqual(["message", "done"]);
  });
});
