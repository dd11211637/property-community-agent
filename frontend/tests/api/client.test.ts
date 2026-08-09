import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiRequest, queryString } from "../../src/api/client";

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
});

it("omits empty query values", () => {
  expect(queryString({ house_id: "h1", offset: 0, status: undefined })).toBe("?house_id=h1&offset=0");
});
