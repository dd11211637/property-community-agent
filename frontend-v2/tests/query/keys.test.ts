import { describe, expect, it } from "vitest";
import { isScopeQuery, scopeQueryKey } from "../../src/query/keys";

describe("scope query identity", () => {
  it("separates the same resource across houses", () => {
    const a = scopeQueryKey({ actorId: "actor-1", houseId: "house-a" }, "repairs");
    const b = scopeQueryKey({ actorId: "actor-1", houseId: "house-b" }, "repairs");
    expect(a).not.toEqual(b);
  });

  it("separates actors and includes filters, resource and conversation", () => {
    const identity = { resourceId: "work-1", conversationId: "chat-1", filters: { status: "OPEN", page: 2 } };
    const first = scopeQueryKey({ actorId: "actor-1", houseId: "house-a" }, "repairs", identity);
    const second = scopeQueryKey({ actorId: "actor-2", houseId: "house-a" }, "repairs", identity);
    expect(first).not.toEqual(second);
    expect(JSON.stringify(first)).toContain("work-1");
    expect(JSON.stringify(first)).toContain("chat-1");
    expect(JSON.stringify(first)).toContain("OPEN");
    expect(isScopeQuery(first, "actor-1", "house-a")).toBe(true);
  });

  it("normalizes filter order", () => {
    const scope = { actorId: "actor-1", houseId: "house-a" };
    expect(scopeQueryKey(scope, "bills", { filters: { page: 1, status: "OPEN" } }))
      .toEqual(scopeQueryKey(scope, "bills", { filters: { status: "OPEN", page: 1 } }));
  });
});
