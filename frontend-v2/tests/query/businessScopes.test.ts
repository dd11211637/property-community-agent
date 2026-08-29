import { describe, expect, it } from "vitest";
import { isScopeQuery, scopeQueryKey } from "../../src/query/keys";

describe("business query scope identity", () => {
  it("separates actor, house, filters and pagination", () => {
    const a = scopeQueryKey(
      {
        actorId: "actor-a",
        communityId: "c",
        houseId: "house-a",
        mode: "house",
      },
      "bills",
      { filters: { limit: 20, offset: 0, status: "UNPAID" } },
    );
    const b = scopeQueryKey(
      {
        actorId: "actor-a",
        communityId: "c",
        houseId: "house-b",
        mode: "house",
      },
      "bills",
      { filters: { limit: 20, offset: 0, status: "UNPAID" } },
    );
    const page = scopeQueryKey(
      {
        actorId: "actor-a",
        communityId: "c",
        houseId: "house-a",
        mode: "house",
      },
      "bills",
      { filters: { limit: 20, offset: 20, status: "UNPAID" } },
    );
    expect(a).not.toEqual(b);
    expect(a).not.toEqual(page);
    expect(isScopeQuery(a, "actor-a", "house-a")).toBe(true);
  });

  it("does not force actor/community resources into house scope", () => {
    const actorA = scopeQueryKey(
      {
        actorId: "actor-a",
        communityId: "c",
        houseId: "house-a",
        mode: "actor",
      },
      "messages",
    );
    const actorB = scopeQueryKey(
      {
        actorId: "actor-a",
        communityId: "c",
        houseId: "house-b",
        mode: "actor",
      },
      "messages",
    );
    const other = scopeQueryKey(
      {
        actorId: "actor-b",
        communityId: "c",
        houseId: "house-a",
        mode: "actor",
      },
      "messages",
    );
    expect(actorA).toEqual(actorB);
    expect(actorA).not.toEqual(other);
  });
});
