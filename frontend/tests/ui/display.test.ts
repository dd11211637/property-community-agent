import { describe, expect, it } from "vitest";
import { businessReference, displayIntegration, displayLabel, isUuid } from "../../src/ui/display";
import { workspaceFor } from "../../src/ui/roles";

describe("release presentation contracts", () => {
  it("maps storage values to business language", () => {
    expect(displayLabel("PENDING_ASSIGNMENT")).toBe("待派单");
    expect(displayLabel("DEGRADED")).toBe("需要关注");
    expect(displayIntegration("model_gateway")).toBe("智能体服务");
  });

  it("never promotes a UUID to a primary business reference", () => {
    const uuid = "a1000000-0000-4000-8000-000000000101";
    expect(isUuid(uuid)).toBe(true);
    expect(businessReference(uuid, "当前工单")).toBe("当前工单");
    expect(businessReference("WX-20260830-001")).toBe("WX-20260830-001");
  });

  it("selects the role-specific workspace fail-closed", () => {
    expect(workspaceFor(["RESIDENT"])).toBe("resident");
    expect(workspaceFor(["REPAIR_WORKER"])).toBe("maintenance");
    expect(workspaceFor(["MANAGER"])).toBe("admin");
    expect(workspaceFor([])).toBe("resident");
  });
});
