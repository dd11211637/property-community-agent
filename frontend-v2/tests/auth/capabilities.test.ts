import { describe, expect, it } from "vitest";
import { hasCapability } from "../../src/auth/capabilities";

describe("explicit capability mapping", () => {
  it("keeps resident, operations and admin capabilities separate", () => {
    expect(hasCapability(["RESIDENT"], "resident-experience")).toBe(true);
    expect(hasCapability(["RESIDENT"], "operations")).toBe(false);
    expect(hasCapability(["SECURITY_GUARD"], "operations")).toBe(true);
    expect(hasCapability(["SECURITY_GUARD"], "admin")).toBe(false);
    expect(hasCapability(["MANAGER"], "admin")).toBe(true);
    expect(hasCapability(["SYSTEM_ADMIN"], "admin")).toBe(true);
  });

  it("does not grant unknown roles implicit staff access", () => {
    expect(hasCapability(["FUTURE_UNKNOWN_ROLE"], "operations")).toBe(false);
    expect(hasCapability(["FUTURE_UNKNOWN_ROLE"], "admin")).toBe(false);
  });
});
