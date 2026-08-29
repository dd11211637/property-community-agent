import { describe, expect, it, vi } from "vitest";
import { ApiClient } from "../../src/api/client";
import { BusinessClient } from "../../src/business/client";

function response(data: unknown, status = 200) {
  return new Response(
    JSON.stringify(
      status < 400
        ? { success: true, data, error: null, request_id: "req-business" }
        : {
            success: false,
            data: null,
            error: data,
            request_id: "req-business",
          },
    ),
    { status, headers: { "Content-Type": "application/json" } },
  );
}

describe("generated-contract business client", () => {
  it("applies house scope only to bill endpoints", async () => {
    const fetcher = vi.fn(async () => response([])) as unknown as typeof fetch;
    const client = new BusinessClient(
      new ApiClient(
        "",
        () => ({ accessToken: "token", currentHouseId: "house-a" }),
        fetcher,
      ),
    );
    await client.listBills();
    await client.listMessages({ limit: 20 });
    expect(
      new Headers(
        (
          fetcher as unknown as ReturnType<typeof vi.fn>
        ).mock.calls[0][1].headers,
      ).get("X-Current-House-ID"),
    ).toBe("house-a");
    expect(
      new Headers(
        (
          fetcher as unknown as ReturnType<typeof vi.fn>
        ).mock.calls[1][1].headers,
      ).has("X-Current-House-ID"),
    ).toBe(false);
  });

  it("uses direct confirmation transport and exact confirmation parameters", async () => {
    const fetcher = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ token: "confirm-1", expires_in_seconds: 300 }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
    ) as unknown as typeof fetch;
    const client = new BusinessClient(
      new ApiClient("", () => ({ accessToken: "token" }), fetcher),
    );
    await expect(
      client.confirm({
        action: "CREATE_CONSULTATION",
        parameters: { subject: "费用", description: "请解释", bill_id: null },
      }),
    ).resolves.toMatchObject({ token: "confirm-1" });
    expect(
      JSON.parse(
        String(
          (fetcher as unknown as ReturnType<typeof vi.fn>).mock.calls[0][1]
            .body,
        ),
      ),
    ).toEqual({
      action: "CREATE_CONSULTATION",
      parameters: { subject: "费用", description: "请解释", bill_id: null },
    });
  });

  it("fails closed when the direct confirmation response is malformed", async () => {
    const fetcher = vi.fn(
      async () =>
        new Response(JSON.stringify({ token: "" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    ) as unknown as typeof fetch;
    const client = new BusinessClient(
      new ApiClient("", () => ({ accessToken: "token" }), fetcher),
    );
    await expect(
      client.confirm({
        action: "CREATE_WORK_ORDER",
        parameters: { house_id: "house-a" },
      }),
    ).rejects.toMatchObject({
      kind: "invalid-response",
      code: "INVALID_CONFIRMATION_RESPONSE",
    });
  });

  it("sends idempotency and expected_version on a versioned repair action", async () => {
    const payload = {
      id: "wo-1",
      business_no: "BX-1",
      house_id: "house-a",
      category: "OTHER",
      location: "门厅",
      description: "损坏",
      urgency: "NORMAL",
      status: "IN_PROGRESS",
      version: 4,
      assignee_id: "worker",
      has_review: false,
      available_actions: [],
      created_at: "",
      updated_at: "",
    };
    const fetcher = vi.fn(async () =>
      response(payload),
    ) as unknown as typeof fetch;
    const client = new BusinessClient(
      new ApiClient("", () => ({ accessToken: "token" }), fetcher),
    );
    await client.workOrderAction(
      "wo-1",
      "accept",
      { expected_version: 3 },
      "intent-1",
    );
    const init = (fetcher as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0][1];
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe("intent-1");
    expect(JSON.parse(String(init.body))).toEqual({ expected_version: 3 });
  });

  it("reuses the original idempotency key for a network retry of the same confirmed intent", async () => {
    const payload = {
      id: "wo-1",
      business_no: "BX-1",
      house_id: "house-a",
      category: "OTHER",
      location: "门厅",
      description: "损坏",
      urgency: "NORMAL",
      status: "PENDING_ASSIGNMENT",
      version: 1,
      assignee_id: null,
      has_review: false,
      available_actions: [],
      created_at: "",
      updated_at: "",
    };
    let attempt = 0;
    const fetcher = vi.fn(async () => {
      attempt += 1;
      if (attempt === 1) throw new TypeError("offline");
      return response(payload);
    }) as unknown as typeof fetch;
    const client = new BusinessClient(
      new ApiClient("", () => ({ accessToken: "token" }), fetcher),
    );
    const base = {
      house_id: "house-a",
      category: "OTHER" as const,
      location: "门厅",
      description: "损坏",
      urgency: "NORMAL" as const,
      attachment_ids: [],
    };
    await expect(
      client.createWorkOrder(
        { ...base, confirmation_token: "token-one" },
        "intent-original",
      ),
    ).rejects.toMatchObject({ kind: "network" });
    await client.createWorkOrder(
      { ...base, confirmation_token: "token-two" },
      "intent-accidentally-new",
    );
    const calls = (fetcher as unknown as ReturnType<typeof vi.fn>).mock.calls;
    expect(new Headers(calls[0][1].headers).get("Idempotency-Key")).toBe(
      "intent-original",
    );
    expect(new Headers(calls[1][1].headers).get("Idempotency-Key")).toBe(
      "intent-original",
    );
  });

  it("converts malformed generic Envelope data into invalid-response", async () => {
    const fetcher = vi.fn(async () =>
      response({ items: [{ id: "broken" }] }),
    ) as unknown as typeof fetch;
    const client = new BusinessClient(
      new ApiClient("", () => ({ accessToken: "token" }), fetcher),
    );
    await expect(client.listWorkOrders({ limit: 50 })).rejects.toMatchObject({
      kind: "invalid-response",
      code: "INVALID_BUSINESS_RESPONSE",
    });
  });

  it("preserves 409 code, details and request id", async () => {
    const fetcher = vi.fn(async () =>
      response(
        {
          code: "VERSION_CONFLICT",
          message: "stale",
          details: { current_version: 8 },
        },
        409,
      ),
    ) as unknown as typeof fetch;
    const client = new BusinessClient(
      new ApiClient("", () => ({ accessToken: "token" }), fetcher),
    );
    await expect(
      client.consultationAction("c-1", "submit", { expected_version: 7 }),
    ).rejects.toMatchObject({
      kind: "conflict",
      code: "VERSION_CONFLICT",
      details: { current_version: 8 },
      requestId: "req-business",
    });
  });
});
