import { describe, expect, it } from "vitest";
import { canPresentAction } from "../../src/business/permissions";

describe("role-sensitive business actions", () => {
  it("intersects available actions with explicit roles", () => {
    expect(canPresentAction("repair", "ASSIGN", ["CUSTOMER_SERVICE"])).toBe(
      true,
    );
    expect(canPresentAction("repair", "ASSIGN", ["RESIDENT"])).toBe(false);
    expect(canPresentAction("inspection", "START", ["SECURITY_GUARD"])).toBe(
      true,
    );
    expect(canPresentAction("security", "GRADE_CONFIRM", ["MANAGER"])).toBe(
      true,
    );
  });

  it("never elevates unknown roles or unknown actions", () => {
    expect(canPresentAction("announcement", "PUBLISH", ["UNKNOWN_ROLE"])).toBe(
      false,
    );
    expect(
      canPresentAction("inspection", "CONFIRM_AI", ["MANAGER", "SYSTEM_ADMIN"]),
    ).toBe(false);
    expect(
      canPresentAction("repair", "FUTURE_ADMIN_ACTION", ["SYSTEM_ADMIN"]),
    ).toBe(false);
  });
});
