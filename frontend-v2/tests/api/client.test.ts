import { describe, expect, it, vi } from "vitest";
import { ApiClient, type RequestDescriptor } from "../../src/api/client";

const business: RequestDescriptor = { authentication: "required", house: "required", decoder: "envelope", invalidateSessionOn401: true };
const login: RequestDescriptor = { authentication: "none", house: "none", decoder: "direct", invalidateSessionOn401: false };
function envelope(status: number, success: boolean, data: unknown = null) {
  return new Response(JSON.stringify({ success, data, error: success ? null : { code: `E_${status}`, message: "failed" }, request_id: "request-42" }), { status, headers: { "Content-Type": "application/json" } });
}

describe("ApiClient", () => {
  it("keeps direct auth decoding and business-envelope decoding explicit", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: "token" }), { status: 200 }))
      .mockResolvedValueOnce(envelope(200, true, { ok: true }));
    const client = new ApiClient("", () => ({ accessToken: "token", currentHouseId: "house-a" }), fetcher);
    await expect(client.request<{ access_token: string }>(login, "/api/auth/login")).resolves.toEqual({ access_token: "token" });
    await expect(client.request<{ ok: boolean }>(business, "/api/business")).resolves.toEqual({ ok: true });
  });

  it("adds formal headers and preserves a caller request ID", async () => {
    const fetcher = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      expect(headers.get("Authorization")).toBe("Bearer jwt-token");
      expect(headers.get("X-Current-House-ID")).toBe("house-a");
      expect(headers.get("X-Request-ID")).toBe("request-owned");
      expect(headers.get("Idempotency-Key")).toBe("idem-1");
      return envelope(200, true, { ok: true });
    });
    const client = new ApiClient("https://example.test", () => ({ accessToken: "jwt-token", currentHouseId: "house-a" }), fetcher as typeof fetch);
    await client.request(business, "/api/example", { method: "POST", body: {}, requestId: "request-owned", idempotencyKey: "idem-1" });
  });

  it("fails before transport when required auth or house context is absent", async () => {
    const fetcher = vi.fn();
    const noAuth = new ApiClient("", () => ({}), fetcher);
    await expect(noAuth.request(business, "/api/data")).rejects.toMatchObject({ kind: "missing-context", code: "AUTH_CONTEXT_REQUIRED" });
    const noHouse = new ApiClient("", () => ({ accessToken: "token" }), fetcher);
    await expect(noHouse.request(business, "/api/data")).rejects.toMatchObject({ kind: "missing-context", code: "HOUSE_CONTEXT_REQUIRED" });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("invalidates only authenticated 401 and retains session on 403", async () => {
    const invalidate = vi.fn();
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "bad login" }), { status: 401 }))
      .mockResolvedValueOnce(envelope(401, false))
      .mockResolvedValueOnce(envelope(403, false));
    const client = new ApiClient("", () => ({ accessToken: "token", currentHouseId: "house-a" }), fetcher, invalidate);
    await expect(client.request(login, "/api/auth/login")).rejects.toMatchObject({ kind: "unauthenticated" });
    expect(invalidate).not.toHaveBeenCalled();
    await expect(client.request(business, "/api/data")).rejects.toMatchObject({ kind: "unauthenticated" });
    expect(invalidate).toHaveBeenCalledOnce();
    await expect(client.request(business, "/api/data")).rejects.toMatchObject({ kind: "forbidden" });
    expect(invalidate).toHaveBeenCalledOnce();
  });

  it.each([[429, "rate-limited"], [503, "unavailable"], [500, "unknown"]] as const)("classifies HTTP %s as %s", async (status, kind) => {
    const client = new ApiClient("", () => ({}), vi.fn(async () => new Response(JSON.stringify({ detail: "error" }), { status })) as unknown as typeof fetch);
    await expect(client.request(login, "/api/fail")).rejects.toMatchObject({ kind, status });
  });

  it("distinguishes invalid responses, caller cancellation and timeout", async () => {
    const invalid = new ApiClient("", () => ({}), vi.fn(async () => new Response("not-json", { status: 200 })) as unknown as typeof fetch);
    await expect(invalid.request(login, "/api/fail")).rejects.toMatchObject({ kind: "invalid-response" });
    const fetcher = vi.fn((_url: string | URL | Request, init?: RequestInit) => new Promise<Response>((_resolve, reject) => init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")))));
    const client = new ApiClient("", () => ({}), fetcher as typeof fetch);
    const caller = new AbortController();
    const cancelled = client.request(login, "/api/wait", { signal: caller.signal, timeoutMs: 200 });
    caller.abort();
    await expect(cancelled).rejects.toMatchObject({ kind: "cancelled" });
    await expect(client.request(login, "/api/wait", { timeoutMs: 1 })).rejects.toMatchObject({ kind: "timeout" });
  });

  it("classifies a transport failure as a network error", async () => {
    const client = new ApiClient("", () => ({}), vi.fn(async () => { throw new TypeError("fetch failed"); }) as unknown as typeof fetch);
    await expect(client.request(login, "/api/auth/login")).rejects.toMatchObject({ kind: "network", code: "NETWORK_ERROR" });
  });
});
