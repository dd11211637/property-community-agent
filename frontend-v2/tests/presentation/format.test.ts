import { describe, expect, it } from "vitest";

import { labelFor } from "../../src/presentation/format";

describe("product display labels", () => {
  it("renders repair workflow and category codes as readable Chinese labels", () => {
    expect(labelFor("PENDING_ASSIGNMENT")).toBe("待分派");
    expect(labelFor("PENDING_ACCEPTANCE")).toBe("待接单");
    expect(labelFor("WATER_PLUMBING")).toBe("给排水");
    expect(labelFor("NORMAL")).toBe("普通");
  });

  it("keeps unknown server values visible without fabricating meaning", () => {
    expect(labelFor("FUTURE_STATE")).toBe("FUTURE STATE");
  });
});
