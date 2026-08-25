import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiRequest, queryString, streamAgentTurn } from "../../src/api/client";

afterEach(() => { vi.restoreAllMocks(); sessionStorage.clear(); });

describe("apiRequest", () => {
  it("unwraps the shared success envelope and forwards trusted session headers", async () => {
    sessionStorage.setItem("property_agent_token", "token");
    sessionStorage.setItem("property_agent_house_id", "house-1");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ success: true, data: { id: "1" }, error: null, request_id: "req-1" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    await expect(apiRequest<{ id: string }>("/api/example")).resolves.toEqual({ id: "1" });
    const headers = new Headers(vi.mocked(fetch).mock.calls[0][1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer token");
    expect(headers.get("X-Current-House-ID")).toBe("house-1");
  });

  it("preserves business error status, code and request id", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ success: false, data: null, error: { code: "VERSION_CONFLICT", message: "stale" }, request_id: "req-409" }), { status: 409, headers: { "Content-Type": "application/json" } }));
    await expect(apiRequest("/api/example")).rejects.toMatchObject({ status: 409, code: "VERSION_CONFLICT", requestId: "req-409" } satisfies Partial<ApiError>);
  });

  it("accepts successful platform responses that do not use an envelope", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ status: "READY", components: { database: "UP" } }), { status: 200, headers: { "Content-Type": "application/json" } }));
    await expect(apiRequest("/ready")).resolves.toEqual({ status: "READY", components: { database: "UP" } });
  });

  it("maps connection failures without fabricating data", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("offline"));
    await expect(apiRequest("/api/example")).rejects.toMatchObject({ code: "NETWORK_ERROR", status: 0 });
  });

  it("maps proxy throttling to a truthful user-facing message", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(
      JSON.stringify({ status: 429 }),
      { status: 429, headers: { "Content-Type": "application/json" } },
    ));

    await expect(apiRequest("/api/auth/login")).rejects.toMatchObject({
      status: 429,
      code: "HTTP_429",
      message: "操作过于频繁，请稍后再试。",
    });
  });

  it("aborts stalled requests and returns a user-facing timeout error", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((_input, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    }));

    await expect(apiRequest("/api/slow", { timeoutMs: 5 })).rejects.toMatchObject({
      code: "REQUEST_TIMEOUT",
      status: 0,
    });
  });
});

it("omits empty query values", () => {
  expect(queryString({ house_id: "h1", offset: 0, status: undefined })).toBe("?house_id=h1&offset=0");
});

describe("streamAgentTurn", () => {
  it("returns only the authoritative turn snapshot", async () => {
    const body = [
      "event: run_started\ndata: {\"conversation_id\":\"c1\"}\n\n",
      "event: tool_started\ndata: {\"stage\":\"planning\"}\n\n",
      "event: turn\ndata: {\"conversation_id\":\"c1\",\"done\":true}\n\n",
      "event: done\ndata: {\"done\":true}\n\n",
    ].join("");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    }));

    await expect(streamAgentTurn<{ conversation_id: string; done: boolean }>(
      "/api/agent/conversations/c1/messages/stream",
      { text: "hello" },
    )).resolves.toEqual({ conversation_id: "c1", done: true });
  });

  it("rejects a failed terminal even when progress was delivered", async () => {
    const body = [
      "event: tool_started\ndata: {\"stage\":\"planning\"}\n\n",
      "event: failed\ndata: {\"category\":\"infrastructure_failure\"}\n\n",
    ].join("");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(body, { status: 200 }));

    await expect(streamAgentTurn("/stream", { text: "hello" })).rejects.toMatchObject({
      code: "AGENT_STREAM_FAILED",
      status: 503,
    });
  });

  it("does not fabricate success when the final snapshot is missing", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(
      "event: done\ndata: {\"done\":true}\n\n",
      { status: 200 },
    ));

    await expect(streamAgentTurn("/stream", { text: "hello" })).rejects.toMatchObject({
      code: "STREAM_FINAL_MISSING",
      status: 502,
    });
  });
});
