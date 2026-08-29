import { describe, expect, it, vi } from "vitest";
import { ApiClient } from "../../src/api/client";
import { AuthenticationService } from "../../src/auth/AuthenticationService";

const loginResponse = {
  access_token: "server-token", token_type: "bearer", actor_id: "actor-a", display_name: "真实用户",
  community_id: "community-a", community_name: "真实社区", roles: ["RESIDENT", "CUSTOMER_SERVICE"],
  house_ids: ["house-a", "house-b"], current_house_id: null,
};

describe("AuthenticationService", () => {
  it("maps only the generated OpenAPI login response into session identity", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify(loginResponse), { status: 200 }));
    const service = new AuthenticationService(new ApiClient("", () => ({}), fetcher as typeof fetch));
    await expect(service.signIn({ username: "input-name", password: "secret" })).resolves.toEqual({
      status: "authenticated", accessToken: "server-token",
      actor: { id: "actor-a", displayName: "真实用户", communityId: "community-a", communityName: "真实社区", roles: ["RESIDENT", "CUSTOMER_SERVICE"] },
      houses: [
        { id: "house-a", label: "房屋 · house-a", resolved: false },
        { id: "house-b", label: "房屋 · house-b", resolved: false },
      ],
      currentHouseId: null,
    });
    const loginCall = fetcher.mock.calls[0] as unknown as [RequestInfo | URL, RequestInit];
    const body = JSON.parse(loginCall[1].body as string);
    expect(body).toEqual({ username: "input-name", password: "secret" });
  });

  it("uses the real house endpoint and returns only server display metadata", async () => {
    let token = "server-token";
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ house_id: "house-a", building: "1 栋", unit: "2 单元", room_no: "1203" }), { status: 200 }));
    const service = new AuthenticationService(new ApiClient("", () => ({ accessToken: token }), fetcher as typeof fetch));
    await expect(service.selectHouse("house-a")).resolves.toEqual({ houseId: "house-a", building: "1 栋", unit: "2 单元", roomNo: "1203" });
    const houseCall = fetcher.mock.calls[0] as unknown as [RequestInfo | URL, RequestInit];
    const headers = new Headers(houseCall[1].headers);
    expect(headers.get("Authorization")).toBe("Bearer server-token");
    token = "";
    await expect(service.selectHouse("house-a")).rejects.toMatchObject({ kind: "missing-context" });
  });

  it("rejects a current house that is not in server bindings", async () => {
    const invalid = { ...loginResponse, current_house_id: "foreign-house" };
    const service = new AuthenticationService(new ApiClient("", () => ({}), vi.fn(async () => new Response(JSON.stringify(invalid), { status: 200 })) as unknown as typeof fetch));
    await expect(service.signIn({ username: "u", password: "p" })).rejects.toMatchObject({ kind: "invalid-response" });
  });
});
