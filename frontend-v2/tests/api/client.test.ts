import { describe, expect, it, vi } from "vitest";
import { ApiClient } from "../../src/api/client";

function envelope(status: number, success: boolean, data: unknown = null) {
  return new Response(JSON.stringify({ success, data, error: success ? null : { code: `E_${status}`, message: "failed" }, request_id: "request-42" }), { status, headers: { "Content-Type": "application/json" } });
}

describe("ApiClient", () => {
  it("adds the formal auth, house, request and idempotency headers", async () => {
    const fetcher = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      expect(headers.get("Authorization")).toBe("Bearer jwt-token");
      expect(headers.get("X-Current-House-ID")).toBe("house-a");
      expect(headers.get("X-Request-ID")).toMatch(/^web_v2_/);
      expect(headers.get("Idempotency-Key")).toBe("idem-1");
      return envelope(200, true, { ok: true });
    });
    const client = new ApiClient("https://example.test", () => ({ accessToken: "jwt-token", currentHouseId: "house-a" }), fetcher as typeof fetch);
    await expect(client.request<{ ok: boolean }>("/api/example", { method: "POST", body: { expected_version: 3, confirmation_token: "token" }, idempotencyKey: "idem-1" })).resolves.toEqual({ ok: true });
  });

  it.each([
    [401, "unauthenticated"], [403, "forbidden"], [409, "conflict"], [422, "validation"], [429, "rate-limited"], [503, "unavailable"], [500, "unknown"],
  ] as const)("classifies HTTP %s as %s", async (status, kind) => {
    const client = new ApiClient("", () => ({}), vi.fn(async () => envelope(status, false)) as unknown as typeof fetch);
    await expect(client.request("/api/fail")).rejects.toMatchObject({ kind, status, requestId: "request-42" });
  });

  it("distinguishes invalid responses", async () => {
    const client = new ApiClient("", () => ({}), vi.fn(async () => new Response("not-json", { status: 200 })) as unknown as typeof fetch);
    await expect(client.request("/api/fail")).rejects.toMatchObject({ kind: "invalid-response" });
  });

  it("distinguishes caller cancellation and timeout", async () => {
    const fetcher = vi.fn((_url: string | URL | Request, init?: RequestInit) => new Promise<Response>((_resolve, reject) => init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")))));
    const client = new ApiClient("", () => ({}), fetcher as typeof fetch);
    const caller = new AbortController();
    const cancelled = client.request("/api/wait", { signal: caller.signal, timeoutMs: 200 });
    caller.abort();
    await expect(cancelled).rejects.toMatchObject({ kind: "cancelled" });
    await expect(client.request("/api/wait", { timeoutMs: 1 })).rejects.toMatchObject({ kind: "timeout" });
  });
});
