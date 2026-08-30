import { describe, expect, it } from "vitest";
import { businessReference, displayHouseAddress, displayIntegration, displayLabel, isUuid } from "../../src/ui/display";
import { workspaceFor } from "../../src/ui/roles";

describe("release presentation contracts", () => {
  it("maps storage values to business language", () => {
    expect(displayLabel("PENDING_ASSIGNMENT")).toBe("待派单");
    expect(displayLabel("DEGRADED")).toBe("需要关注");
    expect(displayLabel("REPAIR")).toBe("报修");
    expect(displayLabel("BILLING")).toBe("账单");
    expect(displayLabel("INSPECTION")).toBe("巡检安防");
    expect(displayIntegration("model_gateway")).toBe("智能体服务");
  });

  it("never promotes a UUID to a primary business reference", () => {
    const uuid = "a1000000-0000-4000-8000-000000000101";
    expect(isUuid(uuid)).toBe(true);
    expect(businessReference(uuid, "当前工单")).toBe("当前工单");
    expect(businessReference("WX-20260830-001")).toBe("WX-20260830-001");
  });

  it("formats server-owned house fields without duplicating the unit suffix", () => {
    expect(displayHouseAddress({ building: "1栋", unit: "1单元", room_no: "101" })).toBe("1栋 1单元 101");
    expect(displayHouseAddress({ building: "2栋", unit: "2", room_no: "201" })).toBe("2栋 2单元 201");
  });

  it("selects the role-specific workspace fail-closed", () => {
    expect(workspaceFor(["RESIDENT"])).toBe("resident");
    expect(workspaceFor(["REPAIR_WORKER"])).toBe("maintenance");
    expect(workspaceFor(["MANAGER"])).toBe("admin");
    expect(workspaceFor([])).toBe("resident");
  });
});
