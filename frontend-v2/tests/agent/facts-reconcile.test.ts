import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { normalizeAgentFacts } from "../../src/agent/factsModel";
import { reconcileTrustedFacts } from "../../src/agent/reconcile";
import { scopeQueryKey } from "../../src/query/keys";

describe("trusted Agent facts", () => {
  it("maps only structured facts, including known list entities", () => {
    expect(normalizeAgentFacts({ work_order: { id: "w-1", status: "PENDING_ASSIGNMENT", location: "厨房" } })[0]).toMatchObject({ type: "work-order", id: "w-1" });
    expect(normalizeAgentFacts({ count: 1, items: [{ entity_type: "BILL", bill_id: "b-1", status: "UNPAID" }] })[0]).toMatchObject({ type: "bill", id: "b-1" });
    expect(normalizeAgentFacts("已成功创建工单")).toEqual([]);
  });

  it("invalidates only related business resources", async () => {
    const client = new QueryClient();
    const work = scopeQueryKey({ actorId: "a", houseId: "h" }, "work-orders");
    const bills = scopeQueryKey({ actorId: "a", houseId: "h" }, "bills");
    client.setQueryData(work, []);
    client.setQueryData(bills, []);
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await reconcileTrustedFacts(client, { work_order: { id: "w" } });
    const predicate = invalidate.mock.calls[0][0]?.predicate;
    expect(predicate?.(client.getQueryCache().find({ queryKey: work })!)).toBe(true);
    expect(predicate?.(client.getQueryCache().find({ queryKey: bills })!)).toBe(false);
  });
});
